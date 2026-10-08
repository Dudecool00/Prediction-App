from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_prospective import AS_OF
from test_status_evidence import capture

from nfl_prop_model.cli import main
from nfl_prop_model.data.claim_review import (
    claim_review_queue,
    read_claim_reviews,
    review_result,
    save_claim_review,
)
from nfl_prop_model.data.status_evidence import read_status_evidence
from nfl_prop_model.data.status_reviews import checksum
from nfl_prop_model.data.storage import read_json, write_json


def review(data, **kwargs):
    evidence = read_status_evidence(data)[0]
    return save_claim_review(
        data,
        evidence["record_id"],
        reviewer=kwargs.pop("reviewer", "Alex"),
        verdict=kwargs.pop("verdict", "supported"),
        notes=kwargs.pop("notes", "Explicit starter sentence."),
        now=kwargs.pop("now", AS_OF + timedelta(minutes=1)),
        **kwargs,
    )


def fixed_clock(monkeypatch):
    import nfl_prop_model.data.claim_review as module

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return AS_OF + timedelta(minutes=1)

    monkeypatch.setattr(module, "datetime", FixedDateTime)


def test_append_only_reviews_bind_exact_archive_without_enabling_forecasts(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    first = review(tmp_path)
    second = review(tmp_path, now=AS_OF + timedelta(minutes=2), notes="Second reading.")
    assert first != second
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    evidence = read_status_evidence(tmp_path)
    records = read_claim_reviews(tmp_path, evidence)
    assert len(records) == 2
    assert all(record["evidence_sha256"] == checksum(evidence[0]) for record in records)
    queue = claim_review_queue(tmp_path, as_of=AS_OF + timedelta(minutes=2))
    assert queue["review_counts"] == {"supported": 1}
    assert queue["production_enabled"] is False
    from test_prospective import buf

    from nfl_prop_model.data.prospective import load_feature_report

    row = buf(load_feature_report(tmp_path, as_of=AS_OF + timedelta(minutes=2)))
    assert not row["forecast_available"]
    assert "primary_status_claims_not_independently_adjudicated" in row["forecast_blockers"]


@pytest.mark.parametrize("verdict", ["supported", "contradicted", "unclear"])
def test_verdicts_remain_explicit_and_never_change_annotation(tmp_path, monkeypatch, verdict):
    capture(tmp_path, monkeypatch)
    review(tmp_path, verdict=verdict)
    row = claim_review_queue(tmp_path, as_of=AS_OF + timedelta(minutes=2))["evidence"][0]
    assert row["review_state"] == verdict
    assert row["evidence"]["claim"] == "confirmed"


def test_later_review_same_name_retains_history_and_cross_reviewer_conflict(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    review(tmp_path, verdict="unclear")
    review(tmp_path, reviewer=" ALEX ", now=AS_OF + timedelta(minutes=2))
    queue = claim_review_queue(tmp_path, as_of=AS_OF + timedelta(minutes=2))
    assert queue["review_counts"] == {"supported": 1}
    assert len(queue["evidence"][0]["review_history"]) == 2
    review(tmp_path, reviewer="Sam", verdict="contradicted", now=AS_OF + timedelta(minutes=3))
    assert claim_review_queue(tmp_path, as_of=AS_OF + timedelta(minutes=3))["review_counts"] == {
        "conflict": 1
    }


def test_equal_time_disagreement_never_chooses_filename_order():
    records = [
        dict(reviewer="Alex", verdict=verdict, logged_at_utc=AS_OF.isoformat())
        for verdict in ("supported", "contradicted")
    ]
    assert review_result(records) == review_result(list(reversed(records))) == "conflict"


def test_future_reviews_are_excluded_from_earlier_queue(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    review(tmp_path)
    earlier = claim_review_queue(tmp_path, as_of=AS_OF)["evidence"][0]
    assert earlier["review_state"] == "unreviewed" and not earlier["review_history"]
    assert claim_review_queue(tmp_path, as_of=AS_OF - timedelta(seconds=1))["evidence"] == []


def test_review_does_not_restart_six_hour_availability_expiry(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch, kind="availability", claim="ruled_out")
    review(tmp_path)
    queue = claim_review_queue(tmp_path, as_of=AS_OF + timedelta(hours=5, seconds=1))
    row = queue["evidence"][0]
    assert row["fresh_until_utc"] == (AS_OF + timedelta(hours=5)).isoformat()
    assert row["readiness"] == "evidence_expired" and row["review_state"] == "supported"
    assert not row["review_allowed"]
    with pytest.raises(ValueError, match="fresh archived"):
        review(tmp_path, now=AS_OF + timedelta(hours=5, seconds=1))


@pytest.mark.parametrize("change", ["changed_context", "stale_cache", "missing_candidate"])
def test_save_rechecks_context_and_source_freshness(tmp_path, monkeypatch, change):
    capture(tmp_path, monkeypatch)
    import nfl_prop_model.data.upcoming as upcoming

    original = upcoming.load_upcoming_report

    def changed(*args, **kwargs):
        report = original(*args, **kwargs)
        for row in report["candidates"]:
            if row["team"] == "BUF":
                if change == "changed_context":
                    row["depth_rank"] += 1
                elif change == "stale_cache":
                    row["sources_fresh"] = False
        if change == "missing_candidate":
            report["candidates"] = []
        return report

    monkeypatch.setattr(upcoming, "load_upcoming_report", changed)
    with pytest.raises(ValueError, match="fresh archived"):
        review(tmp_path)
    assert not list((tmp_path / "claim_reviews").glob("*.json"))


@pytest.mark.parametrize(
    "fields",
    [
        dict(reviewer=""),
        dict(notes=" "),
        dict(verdict="approved"),
        dict(reviewer="x" * 101),
        dict(notes="x" * 2001),
    ],
)
def test_invalid_reviewer_notes_or_verdict_does_not_write(tmp_path, monkeypatch, fields):
    capture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Invalid claim review"):
        review(tmp_path, **fields)
    assert not list((tmp_path / "claim_reviews").glob("*.json"))


def test_form_archive_fingerprint_must_match_at_save(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="changed since"):
        review(tmp_path, expected_evidence_sha256="a" * 64)


@pytest.mark.parametrize("change", ["bytes", "binding", "expired_time", "future_sources"])
def test_corrupt_or_rehashed_invalid_reviews_fail_closed(tmp_path, monkeypatch, change):
    capture(tmp_path, monkeypatch)
    path = review(tmp_path)
    envelope = read_json(path)
    record = envelope["payload"]
    if change == "bytes":
        record["verdict"] = "unclear"
    else:
        if change == "binding":
            record["evidence_sha256"] = "b" * 64
        elif change == "expired_time":
            record["logged_at_utc"] = (AS_OF + timedelta(hours=24)).isoformat()
        else:
            record["sources"]["retrieved_at_utc"] = (AS_OF + timedelta(hours=1)).isoformat()
        envelope["sha256"] = checksum(record)
    write_json(path, envelope)
    with pytest.raises(ValueError):
        claim_review_queue(tmp_path, as_of=AS_OF + timedelta(minutes=2))


def test_game_cutoff_remains_closed_even_if_publication_is_recent(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    evidence = read_status_evidence(tmp_path)[0]
    cutoff = datetime.fromisoformat(evidence["context"]["kickoff_utc"]) - timedelta(hours=1)
    row = claim_review_queue(tmp_path, as_of=cutoff)["evidence"][0]
    assert row["readiness"] == "past_game_cutoff" and not row["review_allowed"]
    with pytest.raises(ValueError, match="fresh archived"):
        review(tmp_path, now=cutoff)


def test_review_commands_are_offline_and_preserve_sources(tmp_path, monkeypatch):
    capture(tmp_path, monkeypatch)
    fixed_clock(monkeypatch)
    import nflreadpy as nfl

    def no_network(*args, **kwargs):
        raise AssertionError("Review commands must never download or fit a model")

    for name in ("load_schedules", "load_depth_charts", "load_player_stats", "load_players"):
        monkeypatch.setattr(nfl, name, no_network)
    originals = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    evidence = read_status_evidence(tmp_path)[0]
    assert (
        main(
            [
                "review-claim",
                "--data-dir",
                str(tmp_path),
                "--evidence-id",
                evidence["record_id"],
                "--reviewer",
                "Alex",
                "--verdict",
                "unclear",
                "--notes",
                "No active-list statement.",
            ]
        )
        == 0
    )
    report_dir = tmp_path / "report"
    assert (
        main(["claim-review-queue", "--data-dir", str(tmp_path), "--report-dir", str(report_dir)])
        == 0
    )
    assert read_json(report_dir / "claim_review.json")["review_counts"] == {"unclear": 1}
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_ui_requires_context_acknowledgement_and_retains_saved_review(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    capture(tmp_path, monkeypatch)
    fixed_clock(monkeypatch)
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20).run()
    app.radio(key="page").set_value("Pregame evidence review").run()
    assert not app.exception and not app.error
    assert "Current BUF will start." in app.text_area[0].value
    app.text_input[0].set_value("Alex")
    app.text_area[1].set_value("The sentence states the intended starter.")
    app.button[0].click().run()
    assert app.error and not list((tmp_path / "claim_reviews").glob("*.json"))
    app.checkbox[0].check()
    app.text_area[1].set_value("")
    app.button[0].click().run()
    assert app.error and not app.exception and not app.code
    assert app.text_input[0].value == "Alex"
    assert not list((tmp_path / "claim_reviews").glob("*.json"))
    app.text_area[1].set_value("The sentence states the intended starter.")
    app.button[0].click().run()
    assert not app.exception and not app.error and app.success
    assert app.dataframe[0].value.iloc[0]["Review"] == "unclear"
    assert len(list((tmp_path / "claim_reviews").glob("*.json"))) == 1
    assert app.selectbox[1].value == "unclear"
