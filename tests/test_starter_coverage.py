import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import polars as pl
import pytest

from nfl_prop_model.cli import main
from nfl_prop_model.data import starter_audit as flagged
from nfl_prop_model.data import starter_coverage as coverage
from nfl_prop_model.data.ingest_nfl import Snapshot, load_snapshot
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.status_reviews import checksum
from nfl_prop_model.data.storage import read_json, sha256_file, store_frame, write_json


@pytest.fixture
def full_cache(tmp_path, sources, monkeypatch):
    stats, schedules = sources
    schedules = (
        schedules.with_row_index("index")
        .with_columns(
            (pl.col("index") + 100).cast(pl.String).alias("espn"),
            pl.col("home_qb_id").alias("home_qb_name"),
            pl.col("away_qb_id").alias("away_qb_name"),
            pl.when(pl.col("index") == 0)
            .then(pl.lit("WRONG"))
            .otherwise(pl.col("home_qb_id"))
            .alias("home_qb_id"),
        )
        .drop("index")
    )
    backup = stats.head(1).with_columns(
        pl.lit("C").alias("player_id"),
        pl.lit("Backup C").alias("player_display_name"),
        pl.lit(0).alias("passing_yards"),
        pl.lit(0).alias("attempts"),
        pl.lit(0).alias("completions"),
    )
    stats = pl.concat([stats, backup], how="vertical_relaxed")
    raw = tmp_path / "raw" / "2022_2023_2024"
    manifest = {
        "seasons": [2022, 2023, 2024],
        "retrieved_at_utc": "2026-09-15T00:00:00+00:00",
        "datasets": {
            "player_stats": store_frame(raw, "player_stats", stats),
            "schedules": store_frame(raw, "schedules", schedules),
        },
    }
    write_json(raw / "manifest.json", manifest)
    identities = pl.DataFrame(
        {
            "gsis_id": ["A", "B", "C"],
            "espn_id": ["11", "22", "33"],
            "display_name": ["Player A", "Player B", "Backup C"],
            "position": ["QB"] * 3,
        }
    )
    _, slots = coverage.all_slots(Snapshot(stats, schedules, manifest))
    slot_index = {(row["event_id"], "1" if row["side"] == "home" else "2"): row for row in slots}
    events = {row["event_id"]: row for row in slots}
    calls = []

    def request(url):
        calls.append(url)
        if url == flagged.POSITION_SOURCE:
            return {"id": "8", "abbreviation": "QB"}
        if "summary?" in url:
            event_id = parse_qs(urlsplit(url).query)["event"][0]
            slot = events[event_id]
            return {
                "odds": {"ignored": True},
                "header": {
                    "id": event_id,
                    "week": slot["week"],
                    "season": {"year": slot["season"], "type": 2},
                    "competitions": [
                        {
                            "id": event_id,
                            "date": slot["kickoff_utc"],
                            "status": {"type": {"completed": True}},
                            "competitors": [
                                {
                                    "homeAway": side,
                                    "team": {"id": team_id, "abbreviation": slot[f"{side}_team"]},
                                }
                                for side, team_id in (("home", "1"), ("away", "2"))
                            ],
                        }
                    ],
                },
            }
        parts = urlsplit(url).path.split("/")
        slot = slot_index[parts[7], parts[11]]
        starter = "A" if (slot["season"] == 2023) == (slot["side"] == "home") else "B"
        return {
            "$ref": url,
            "entries": [
                {
                    "playerId": "11" if starter == "A" else "22",
                    "starter": True,
                    "didNotPlay": False,
                    "valid": True,
                    "period": 0,
                    "position": {"$ref": flagged.POSITION_SOURCE},
                },
                {
                    "playerId": "33",
                    "starter": False,
                    "didNotPlay": False,
                    "valid": False,
                    "period": 0,
                    "position": {"$ref": flagged.POSITION_SOURCE},
                },
            ],
        }

    import nflreadpy as nfl

    monkeypatch.setattr(nfl, "load_players", lambda: identities)
    monkeypatch.setattr(flagged, "request_json", request)
    coverage.fetch_starter_coverage(tmp_path)
    return tmp_path, calls, request


def update_manifest(data, mutate):
    path = data / "raw" / coverage.CACHE / "manifest.json"
    envelope = read_json(path)
    mutate(envelope["payload"])
    envelope["sha256"] = checksum(envelope["payload"])
    write_json(path, envelope)


def change_roster(data, mutate):
    directory = data / "raw" / coverage.CACHE

    def change(manifest):
        entry = manifest["evidence"][0]
        roster = flagged.load_source(directory, entry["roster"])
        mutate(roster)
        entry["roster"] = flagged.store_source(
            directory, "changed-roster", roster, entry["roster"]["source"]
        )

    update_manifest(data, change)


def test_full_denominator_target_overlay_and_original_bytes(full_cache):
    data, _, _ = full_cache
    originals = {path: path.read_bytes() for path in (data / "raw" / "2022_2023_2024").iterdir()}
    report = coverage.load_starter_coverage(data)
    assert report["counts"] == {
        "historical_qb_rows": 15,
        "completed_team_game_slots": 14,
        "verified_schedule_labels": 13,
        "corrected_schedule_labels": 1,
        "resolved_slots": 14,
        "needs_review": 0,
    }
    assert report["appearance_counts"] == {"other_recorded_qb": 1, "roster_starter": 14}
    assert len(report["appearance_overlay"]) == 15
    assert all(not row["pregame_verified"] for row in report["cases"])
    assert report["production_enabled"] is False
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    source = report["cases"][0]["sources"]["header"]
    assert "odds" not in flagged.load_source(data / "raw" / coverage.CACHE, source)


def test_resume_reuses_verified_event_checkpoints(full_cache, monkeypatch):
    data, calls, _ = full_cache
    before = len(calls)
    progress = []
    coverage.fetch_starter_coverage(
        data, progress=lambda done, total: progress.append((done, total))
    )
    assert len(calls) == before + 1  # QB position; all event/roster requests skipped.
    assert progress[-1] == (7, 7)
    assert coverage.load_starter_coverage(data)["counts"]["resolved_slots"] == 14


def test_failed_refresh_preserves_active_manifest_and_resume_retries_failed_events(
    full_cache, monkeypatch
):
    data, _, request = full_cache
    path = data / "raw" / coverage.CACHE / "manifest.json"
    before = path.read_bytes()

    def fail(url):
        if "summary?" in url:
            raise ConnectionError("Unavailable")
        return request(url)

    monkeypatch.setattr(flagged, "request_json", fail)
    with pytest.raises(ConnectionError, match="prior active"):
        coverage.fetch_starter_coverage(data, refresh=True)
    assert path.read_bytes() == before
    assert coverage.load_starter_coverage(data)["counts"]["resolved_slots"] == 14
    monkeypatch.setattr(flagged, "request_json", request)
    coverage.fetch_starter_coverage(data)
    assert coverage.load_starter_coverage(data)["counts"]["resolved_slots"] == 14


@pytest.mark.parametrize(
    "change", ["no_starter", "two_starters", "unmapped", "missing_target", "flags", "period_bool"]
)
def test_unresolved_cases_keep_slots_and_never_create_zero_targets(full_cache, change):
    data, _, _ = full_cache

    def mutate(roster):
        entry = roster["entries"][0]
        if change == "no_starter":
            entry["starter"] = False
        elif change == "two_starters":
            roster["entries"][1].update(starter=True, valid=True)
        elif change == "unmapped":
            entry["playerId"] = "99"
        elif change == "missing_target":
            entry["playerId"] = "33"
            roster["entries"].pop()
        elif change == "period_bool":
            entry["period"] = False
        else:
            entry["didNotPlay"] = True

    change_roster(data, mutate)
    report = coverage.load_starter_coverage(data)
    assert report["counts"]["completed_team_game_slots"] == 14
    assert report["counts"]["needs_review"] == 1
    assert report["cases"][0]["target_passing_yards"] is None
    assert len(report["appearance_overlay"]) == 15
    assert report["appearance_counts"]["starter_unresolved"] == 1


@pytest.mark.parametrize(
    "change", ["missing_slot", "duplicate_slot", "wrong_rules", "wrong_source", "future_retrieval"]
)
def test_rehashed_inconsistent_manifest_fails_closed(full_cache, change):
    data, _, _ = full_cache

    def mutate(manifest):
        if change == "missing_slot":
            manifest["evidence"].pop()
        elif change == "duplicate_slot":
            manifest["evidence"].append(deepcopy(manifest["evidence"][0]))
        elif change == "wrong_rules":
            manifest["rules"]["version"] = 9
        elif change == "wrong_source":
            manifest["evidence"][0]["roster"]["source"] = "https://evil.test/roster"
        else:
            manifest["evidence"][0]["header"]["retrieved_at_utc"] = (
                datetime.now(UTC) + timedelta(days=1)
            ).isoformat()

    update_manifest(data, mutate)
    with pytest.raises((ValueError, DataQualityError)):
        coverage.load_starter_coverage(data)


def test_header_kickoff_mismatch_is_unresolved_without_relabeling_target(full_cache):
    data, _, _ = full_cache
    directory = data / "raw" / coverage.CACHE

    def mutate(manifest):
        entry = manifest["evidence"][0]
        header = flagged.load_source(directory, entry["header"])
        header["competitions"][0]["date"] = "2023-11-05T19:00:00Z"
        entry["header"] = flagged.store_source(
            directory, "changed-header", header, entry["header"]["source"]
        )

    update_manifest(data, mutate)
    report = coverage.load_starter_coverage(data)
    assert report["cases"][0]["status"] == "unresolved_event_context"
    assert report["cases"][0]["reconciled_player_id"] is None
    assert report["counts"]["completed_team_game_slots"] == 14


def test_corrupt_source_and_corrupt_checkpoint_are_not_silently_refetched(full_cache):
    data, _, _ = full_cache
    directory = data / "raw" / coverage.CACHE
    manifest = read_json(directory / "manifest.json")["payload"]
    source = directory / manifest["evidence"][0]["roster"]["file"]
    original = source.read_bytes()
    source.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        coverage.load_starter_coverage(data)
    with pytest.raises(ValueError, match="checksum"):
        coverage.fetch_starter_coverage(data)
    source.write_bytes(original)
    path = next(directory.glob("checkpoint-*.json"))
    envelope = read_json(path)
    envelope["sha256"] = "a" * 64
    write_json(path, envelope)
    with pytest.raises(ValueError, match="checksum"):
        coverage.fetch_starter_coverage(data)


def test_reserved_season_is_rejected_before_network(full_cache, monkeypatch):
    data, _, _ = full_cache
    snapshot = load_snapshot(data, [2022, 2023, 2024])
    invalid = Snapshot(
        snapshot.player_stats.with_columns(pl.lit(2025).alias("season")),
        snapshot.schedules,
        snapshot.manifest,
    )
    with pytest.raises(DataQualityError, match="development seasons"):
        coverage.all_slots(invalid)


def test_offline_cli_does_not_download_or_modify_sources(full_cache, monkeypatch):
    data, _, _ = full_cache

    def offline(*args, **kwargs):
        raise AssertionError("Offline audit must not download")

    monkeypatch.setattr(flagged, "request_json", offline)
    before = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
    report_dir = data / "report"
    assert main(["starter-coverage", "--data-dir", str(data), "--report-dir", str(report_dir)]) == 0
    assert read_json(report_dir / "starter_coverage.json")["counts"]["resolved_slots"] == 14
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_rehashed_incomplete_checkpoint_preserves_active_manifest(full_cache):
    data, _, _ = full_cache
    directory = data / "raw" / coverage.CACHE
    active = (directory / "manifest.json").read_bytes()
    path = next(directory.glob("checkpoint-*.json"))
    envelope = read_json(path)
    envelope["payload"]["evidence"].pop()
    envelope["sha256"] = checksum(envelope["payload"])
    write_json(path, envelope)
    with pytest.raises(ValueError, match="checkpoint team-game coverage"):
        coverage.fetch_starter_coverage(data)
    assert (directory / "manifest.json").read_bytes() == active


def test_missing_event_ids_keep_both_team_slots_without_guessing(full_cache):
    data, calls, _ = full_cache
    raw = data / "raw" / "2022_2023_2024"
    manifest = read_json(raw / "manifest.json")
    snapshot = load_snapshot(data, [2022, 2023, 2024])
    first = snapshot.schedules["game_id"][0]
    schedules = snapshot.schedules.with_columns(
        pl.when(pl.col("game_id") == first).then(None).otherwise(pl.col("espn")).alias("espn")
    )
    manifest["datasets"]["schedules"] = store_frame(raw, "schedules", schedules)
    write_json(raw / "manifest.json", manifest)
    before = len(calls)
    coverage.fetch_starter_coverage(data)
    assert not any("event=100" in url or "events/100/" in url for url in calls[before:])
    report = coverage.load_starter_coverage(data)
    assert report["counts"]["completed_team_game_slots"] == 14
    assert report["counts"]["needs_review"] == 2
    assert len(report["appearance_overlay"]) == 15


def test_ui_retains_denominator_when_filtering_unresolved_slots(full_cache, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    data, _, _ = full_cache
    change_roster(data, lambda roster: roster["entries"][0].update(starter=False))
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20).run()
    app.radio(key="page").set_value("Starter coverage").run()
    assert not app.exception and not app.error
    assert [item.value for item in app.metric] == ["14", "13", "1"]
    assert len(app.dataframe[0].value) == 14
    app.checkbox(key="coverage_unresolved").check().run()
    assert not app.exception and not app.error
    assert app.metric[0].value == "14"
    assert len(app.dataframe[0].value) == 1


def test_public_export_preserves_every_slot_without_raw_source_bodies(full_cache):
    data, _, _ = full_cache
    report = coverage.load_starter_coverage(data)
    destination = data / "public"
    coverage.export_starter_labels(destination, report)
    summary = read_json(destination / "summary.json")
    path = destination / summary["labels"]["file"]
    assert summary["labels"]["sha256"] == sha256_file(path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == summary["labels"]["rows"] == 14
    assert all("sources" in row and "header" not in row and "entries" not in row for row in rows)
    assert summary["production_enabled"] is False
    assert "data/starter_audit.py" in summary["audit_source_hashes"]
    assert "data/roster_reconciliation.py" in summary["audit_source_hashes"]
