from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from test_upcoming import AS_OF, save_sources

from nfl_prop_model.cli import main
from nfl_prop_model.data.status_reviews import (
    checksum,
    evidence,
    read_status_reviews,
    review_context,
    save_status_review,
)
from nfl_prop_model.data.storage import read_json, store_frame, write_json
from nfl_prop_model.data.upcoming import load_upcoming_report

GAME = "2026_03_ARI_BUF"
URL = "https://example.org/team/status"


def save(data, *, now=AS_OF, espn="espn-BUF", starter="confirmed", active="active", source=None):
    source = source or (now - timedelta(minutes=30)).isoformat()
    return save_status_review(
        data,
        GAME,
        espn,
        now=now,
        starter=evidence(
            starter, URL if starter != "unknown" else "", source if starter != "unknown" else ""
        ),
        availability=evidence(
            active, URL if active != "unknown" else "", source if active != "unknown" else ""
        ),
    )


def row(data, as_of=AS_OF, espn="espn-BUF"):
    report = load_upcoming_report(data, as_of=as_of)
    return next(r for r in report["candidates"] if r["espn_id"] == espn)


def test_review_is_dated_append_only_overlay_without_forecasts(tmp_path):
    directory, _ = save_sources(tmp_path, AS_OF)
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    first = save(tmp_path)
    second = save(tmp_path, now=AS_OF + timedelta(minutes=1), starter="unknown", active="unknown")
    assert first != second and len(read_status_reviews(tmp_path / "status_reviews")) == 2
    before = row(tmp_path)
    assert before["starter_confirmed"] and before["active_status_reviewed"]
    assert not before["forecast_available"]
    assert "production_model_unavailable" in before["review_reasons"]
    assert not row(tmp_path, AS_OF + timedelta(minutes=1))["starter_confirmed"]
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original


def test_cutoff_does_not_use_later_review(tmp_path):
    save_sources(tmp_path, AS_OF)
    save(tmp_path, now=AS_OF + timedelta(minutes=1))
    candidate = row(tmp_path)
    assert candidate["status_review"]["state"] == "unreviewed"
    assert not candidate["starter_confirmed"] and not candidate["active_status_reviewed"]


def test_source_age_and_review_age_expire_independently(tmp_path):
    save_sources(tmp_path, AS_OF)
    save(tmp_path)
    seven_hours = row(tmp_path, AS_OF + timedelta(hours=7))
    assert seven_hours["starter_confirmed"]
    assert not seven_hours["active_status_reviewed"]
    assert seven_hours["status_review"]["availability"] == "stale"
    later = row(tmp_path, AS_OF + timedelta(hours=25))
    assert not later["starter_confirmed"] and later["status_review"]["starter"] == "stale"
    save(
        tmp_path, now=AS_OF + timedelta(minutes=1), source=(AS_OF - timedelta(hours=25)).isoformat()
    )
    rechecked = row(tmp_path, AS_OF + timedelta(minutes=1))
    assert not rechecked["starter_confirmed"] and not rechecked["active_status_reviewed"]


@pytest.mark.parametrize("change", ["kickoff", "chart", "identity", "opponent"])
def test_changed_candidate_requires_recheck(tmp_path, change):
    directory, manifest = save_sources(tmp_path, AS_OF)
    save(tmp_path)
    key = "schedules" if change in {"kickoff", "opponent"} else "depth_charts"
    frame = pl.read_parquet(directory / manifest["datasets"][key]["file"])
    if change == "kickoff":
        frame = frame.with_columns(pl.lit("19:15").alias("gametime"))
    elif change == "opponent":
        frame = frame.with_columns(pl.lit("ATL").alias("away_team"))
    elif change == "chart":
        frame = frame.with_columns(pl.lit((AS_OF - timedelta(hours=2)).isoformat()).alias("dt"))
    else:
        frame = frame.with_columns(
            pl.when(pl.col("espn_id") == "espn-BUF")
            .then(pl.lit("replacement-id"))
            .otherwise(pl.col("gsis_id"))
            .alias("gsis_id")
        )
    manifest["datasets"][key] = store_frame(directory, key, frame)
    write_json(directory / "manifest.json", manifest)
    candidate = row(tmp_path)
    assert candidate["status_review"]["state"] == "candidate_changed"
    assert not candidate["starter_confirmed"] and not candidate["active_status_reviewed"]


def test_two_starters_require_explicit_resolution_and_keep_unmapped_qb_visible(tmp_path):
    save_sources(tmp_path, AS_OF)
    save(tmp_path)
    save(tmp_path, espn="rookie")
    for espn in ("espn-BUF", "rookie"):
        candidate = row(tmp_path, espn=espn)
        assert not candidate["starter_confirmed"]
        assert candidate["status_review"]["starter"] == "conflict"
    save(tmp_path, starter="not_starter", now=AS_OF + timedelta(minutes=1))
    newcomer = row(tmp_path, AS_OF + timedelta(minutes=1), espn="rookie")
    assert newcomer["starter_confirmed"] and newcomer["gsis_id"] is None
    assert "missing_gsis_mapping" in newcomer["review_reasons"]
    assert not newcomer["forecast_available"]


def test_inactive_starter_claim_and_simultaneous_reviews_stay_blocked(tmp_path):
    save_sources(tmp_path, AS_OF)
    save(tmp_path, active="inactive")
    candidate = row(tmp_path)
    assert not candidate["starter_confirmed"] and not candidate["active_status_reviewed"]
    assert "starter_and_inactive_evidence_conflict" in candidate["review_reasons"]
    save(tmp_path)
    candidate = row(tmp_path)
    assert candidate["status_review"]["state"] == "simultaneous_reviews"
    assert not candidate["starter_confirmed"]


@pytest.mark.parametrize(
    "bad",
    [
        "naive",
        "future",
        "missing_url",
        "credentials",
        "bad_status",
        "unknown_evidence",
        "long_notes",
    ],
)
def test_invalid_claim_never_creates_record(tmp_path, bad):
    save_sources(tmp_path, AS_OF)
    claim = evidence("confirmed", URL, AS_OF.isoformat())
    notes = ""
    if bad == "naive":
        claim["source_at_utc"] = "2026-09-22T12:00:00"
    elif bad == "future":
        claim["source_at_utc"] = (AS_OF + timedelta(seconds=1)).isoformat()
    elif bad == "missing_url":
        claim["source_url"] = ""
    elif bad == "credentials":
        claim["source_url"] = "https://user:secret@example.org/status"
    elif bad == "bad_status":
        claim["status"] = "probable"
    elif bad == "unknown_evidence":
        claim["status"] = "unknown"
    else:
        notes = "x" * 2001
    with pytest.raises(ValueError):
        save_status_review(
            tmp_path,
            GAME,
            "espn-BUF",
            now=AS_OF,
            starter=claim,
            availability=evidence("unknown"),
            notes=notes,
        )
    assert not list((tmp_path / "status_reviews").glob("*.json"))


def test_saving_rechecks_clock_sources_and_form_context(tmp_path):
    save_sources(tmp_path, AS_OF)
    expected = review_context(row(tmp_path))
    expected["gsis_id"] = "wrong-id"
    with pytest.raises(ValueError, match="changed while reviewing"):
        save_status_review(
            tmp_path,
            GAME,
            "espn-BUF",
            now=AS_OF,
            starter=evidence("unknown"),
            availability=evidence("unknown"),
            expected_context=expected,
        )
    with pytest.raises(ValueError, match="stale"):
        save(tmp_path, now=AS_OF + timedelta(hours=25))
    with pytest.raises(ValueError, match="unique upcoming QB"):
        save(tmp_path, now=datetime(2026, 9, 25, 0, 15, tzinfo=UTC))
    assert not list((tmp_path / "status_reviews").glob("*.json"))


def test_corrupt_or_renamed_reviews_fail_visibly(tmp_path):
    save_sources(tmp_path, AS_OF)
    path = save(tmp_path)
    original = path.read_text()
    envelope = read_json(path)
    envelope["payload"]["starter"]["status"] = "not_starter"
    write_json(path, envelope)
    with pytest.raises(ValueError, match="checksum"):
        load_upcoming_report(tmp_path, as_of=AS_OF)
    path.write_text(original)
    path.rename(path.with_name(f"status-{'f' * 32}.json"))
    with pytest.raises(ValueError, match="filename"):
        load_upcoming_report(tmp_path, as_of=AS_OF)


@pytest.mark.parametrize("boundary", ["kickoff", "source_age"])
def test_clock_is_rechecked_after_sources_load(tmp_path, monkeypatch, boundary):
    import nfl_prop_model.data.status_reviews as module

    save_sources(tmp_path, AS_OF)
    if boundary == "kickoff":
        times = iter([AS_OF, datetime(2026, 9, 25, 0, 15, tzinfo=UTC)])
        message = "precede kickoff"
    else:
        times = iter([AS_OF + timedelta(hours=23), AS_OF + timedelta(hours=23, seconds=1)])
        message = "stale"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(times)

    monkeypatch.setattr(module, "datetime", Clock)
    with pytest.raises(ValueError, match=message):
        save_status_review(
            tmp_path,
            GAME,
            "espn-BUF",
            starter=evidence("unknown"),
            availability=evidence("unknown"),
        )
    assert not list((tmp_path / "status_reviews").glob("*.json"))


def test_collision_preserves_saved_record(tmp_path, monkeypatch):
    import nfl_prop_model.data.status_reviews as module

    save_sources(tmp_path, AS_OF)
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    first = save(tmp_path)
    original = first.read_bytes()
    with pytest.raises(FileExistsError):
        save(tmp_path, starter="not_starter", now=AS_OF + timedelta(minutes=1))
    assert first.read_bytes() == original


def test_offline_cli_includes_reviews(tmp_path, monkeypatch):
    import nflreadpy as nfl

    now = datetime.now(UTC)
    save_sources(tmp_path, now)
    save(tmp_path, now=now)
    monkeypatch.setattr(nfl, "load_schedules", lambda *a, **k: pytest.fail("Network not allowed"))
    destination = tmp_path / "report"
    assert main(["upcoming", "--data-dir", str(tmp_path), "--report-dir", str(destination)]) == 0
    report = read_json(destination / "upcoming.json")
    assert report["counts"]["manually_reviewed_starter_rows"] == 1
    assert not any(r["forecast_available"] for r in report["candidates"])


def test_ui_validates_saves_and_resets_form_for_another_qb(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    now = datetime.now(UTC)
    save_sources(tmp_path, now)
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=30).run()
    app.radio(key="page").set_value("Upcoming QBs").run()
    app.selectbox(key="upcoming_qb").set_value("espn-BUF").run()
    key = checksum(review_context(row(tmp_path, now)))[:16]
    app.selectbox(key=f"starter_{key}").set_value("confirmed")
    app.button[-1].click().run()
    assert app.error and not list((tmp_path / "status_reviews").glob("*.json"))
    app.text_input(key=f"starter_url_{key}").set_value(URL)
    app.text_input(key=f"starter_time_{key}").set_value(now.isoformat())
    app.selectbox(key=f"availability_{key}").set_value("active")
    app.text_input(key=f"active_url_{key}").set_value(URL)
    app.text_input(key=f"active_time_{key}").set_value(now.isoformat())
    app.button[-1].click().run()
    assert not app.exception and not app.error and app.success
    assert len(read_status_reviews(tmp_path / "status_reviews")) == 1
    saved = app.dataframe[0].value
    assert saved.loc[saved["Quarterback"] == "Current BUF", "Starter review"].item() == "confirmed"
    app.selectbox(key="upcoming_qb").set_value("rookie").run()
    newcomer_key = checksum(review_context(row(tmp_path, now, espn="rookie")))[:16]
    assert app.selectbox(key=f"starter_{newcomer_key}").value == "unknown"
    assert app.text_input(key=f"starter_url_{newcomer_key}").value == ""
