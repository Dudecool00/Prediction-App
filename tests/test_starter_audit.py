from copy import deepcopy
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nfl_prop_model.cli import main
from nfl_prop_model.data import starter_audit as audit
from nfl_prop_model.data.ingest_nfl import Snapshot
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.storage import read_json, store_frame, write_json


@pytest.fixture
def evidence_cache(tmp_path, sources, monkeypatch):
    stats, schedules = sources
    first = schedules["game_id"][0]
    schedules = schedules.with_columns(
        pl.lit("123").alias("espn"),
        pl.lit("Ghost").alias("home_qb_name"),
        pl.lit("Player B").alias("away_qb_name"),
        pl.when(pl.col("game_id") == first)
        .then(pl.lit("GHOST"))
        .otherwise(pl.col("home_qb_id"))
        .alias("home_qb_id"),
    )
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
            "gsis_id": ["A", "B"],
            "espn_id": ["11", "22"],
            "display_name": ["Player A", "Player B"],
            "position": ["QB", "QB"],
        }
    )
    header = {
        "id": "123",
        "week": 1,
        "season": {"year": 2023, "type": 2},
        "competitions": [
            {
                "id": "123",
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"homeAway": side, "team": {"id": team_id, "abbreviation": team}}
                    for side, team_id, team in (("home", "1", "HOME"), ("away", "2", "AWAY"))
                ],
            }
        ],
    }
    roster = {
        "$ref": "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
        "events/123/competitions/123/competitors/1/roster?lang=en",
        "entries": [
            {
                "playerId": 11,
                "starter": True,
                "didNotPlay": False,
                "valid": True,
                "period": 0,
                "position": {"$ref": audit.POSITION_SOURCE},
            }
        ],
    }

    def fake_request(url):
        if url == audit.POSITION_SOURCE:
            return {"id": "8", "abbreviation": "QB"}
        if "summary?" in url:
            return {"header": deepcopy(header), "odds": {"ignored": True}}
        return deepcopy(roster)

    import nflreadpy as nfl

    monkeypatch.setattr(nfl, "load_players", lambda: identities)
    monkeypatch.setattr(audit, "request_json", fake_request)
    audit.fetch_starter_evidence(tmp_path)
    return tmp_path, Snapshot(stats, schedules, manifest), identities, header, roster


def replace_roster(data, roster):
    directory = data / "raw" / audit.CACHE
    manifest = read_json(directory / "manifest.json")
    entry = manifest["evidence"][0]
    entry["roster"] = audit.store_source(
        directory, "roster-test", roster, entry["roster"]["source"]
    )
    write_json(directory / "manifest.json", manifest)


def replace_identities(data, identities):
    directory = data / "raw" / audit.CACHE
    manifest = read_json(directory / "manifest.json")
    manifest["identities"] = store_frame(directory, "identities", identities)
    write_json(directory / "manifest.json", manifest)


def test_unique_starter_corrects_only_diagnostic_overlay(evidence_cache):
    data, snapshot, _, _, _ = evidence_cache
    before = snapshot.player_stats.clone(), snapshot.schedules.clone()
    report = audit.load_starter_audit(data)
    assert report["counts"]["flagged_starter_labels"] == 1
    assert report["counts"]["corrected_schedule_labels"] == 1
    assert report["counts"]["needs_review"] == 0
    assert report["counts"]["unflagged_slots_not_independently_verified"] == 13
    case = report["cases"][0]
    assert case["scheduled_player_id"] == "GHOST"
    assert case["reconciled_player_id"] == "A"
    assert case["pregame_verified"] is False
    assert_frame_equal(before[0], snapshot.player_stats)
    assert_frame_equal(before[1], snapshot.schedules)
    cached = audit.load_snapshot(data, [2022, 2023, 2024])
    assert_frame_equal(before[0], cached.player_stats)
    assert_frame_equal(before[1], cached.schedules)
    source = report["cases"][0]["sources"]["header"]
    assert "odds" not in audit.load_source(data / "raw" / audit.CACHE, source)


@pytest.mark.parametrize("kind", ["no_starter", "two_starters", "unmapped", "no_target"])
def test_missing_or_ambiguous_evidence_stays_unresolved(evidence_cache, kind):
    data, _, identities, _, roster = evidence_cache
    if kind == "no_starter":
        roster["entries"][0]["starter"] = False
        replace_roster(data, roster)
    elif kind == "two_starters":
        roster["entries"].append({**roster["entries"][0], "playerId": 22})
        replace_roster(data, roster)
    elif kind == "unmapped":
        replace_identities(data, identities.filter(pl.col("espn_id") != "11"))
    else:
        replace_identities(
            data,
            identities.with_columns(
                pl.when(pl.col("espn_id") == "11")
                .then(pl.lit("OTHER"))
                .otherwise(pl.col("gsis_id"))
                .alias("gsis_id")
            ),
        )
    report = audit.load_starter_audit(data)
    assert report["counts"]["needs_review"] == 1
    assert report["cases"][0]["status"].startswith("unresolved_")


@pytest.mark.parametrize(
    "field,value", [("starter", "true"), ("didNotPlay", True), ("valid", False), ("period", 1)]
)
def test_contradictory_roster_flags_fail(evidence_cache, field, value):
    data, _, _, _, roster = evidence_cache
    roster["entries"][0][field] = value
    replace_roster(data, roster)
    with pytest.raises(DataQualityError, match="flags"):
        audit.load_starter_audit(data)


@pytest.mark.parametrize(
    "kind", ["duplicate_espn", "duplicate_gsis", "wrong_roster", "duplicate_qb"]
)
def test_ambiguous_ids_or_event_mismatch_fail(evidence_cache, kind):
    data, _, identities, _, roster = evidence_cache
    if kind == "duplicate_espn":
        replace_identities(
            data,
            pl.concat(
                [identities, identities.head(1).with_columns(pl.lit("OTHER").alias("gsis_id"))]
            ),
        )
    elif kind == "duplicate_gsis":
        replace_identities(
            data,
            pl.concat(
                [identities, identities.head(1).with_columns(pl.lit("999").alias("espn_id"))]
            ),
        )
    elif kind == "wrong_roster":
        roster["$ref"] = roster["$ref"].replace("competitors/1", "competitors/2")
        replace_roster(data, roster)
    else:
        roster["entries"].append(deepcopy(roster["entries"][0]))
        replace_roster(data, roster)
    with pytest.raises(DataQualityError, match="[Aa]mbiguous|mismatch|duplicate"):
        audit.load_starter_audit(data)


def test_wrong_season_team_and_uncompleted_event_fail(evidence_cache):
    _, snapshot, _, header, _ = evidence_cache
    _, slots, _ = audit.flagged_slots(snapshot)
    for kind in ("season", "week", "team", "unfinished"):
        changed = deepcopy(header)
        if kind == "season":
            changed["season"]["year"] = 2025
        elif kind == "week":
            changed["week"] = 2
        elif kind == "team":
            changed["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "WRONG"
        else:
            changed["competitions"][0]["status"]["type"]["completed"] = False
        with pytest.raises(DataQualityError, match="mismatch"):
            audit.event_team(changed, slots[0])
    with pytest.raises(DataQualityError, match="2022–2024"):
        audit.flagged_slots(
            Snapshot(
                snapshot.player_stats.with_columns(pl.lit(2025).alias("season")),
                snapshot.schedules,
                snapshot.manifest,
            )
        )


def test_offline_cli_and_cache_corruption(evidence_cache, monkeypatch, capsys):
    data, _, _, _, _ = evidence_cache

    def no_network(*args, **kwargs):
        raise AssertionError("Offline reads must not fetch evidence")

    monkeypatch.setattr(audit, "request_json", no_network)
    report_dir = data / "report"
    assert main(["starter-audit", "--data-dir", str(data), "--report-dir", str(report_dir)]) == 0
    report = read_json(report_dir / "starter_audit.json")
    assert report["counts"]["corrected_schedule_labels"] == 1
    assert "Historical data unchanged" in capsys.readouterr().out
    entry = report["cases"][0]["sources"]["roster"]
    path = data / "raw" / audit.CACHE / entry["file"]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        audit.load_starter_audit(data)


def test_stale_manifest_and_incomplete_coverage_fail(evidence_cache):
    data, _, _, _, _ = evidence_cache
    path = data / "raw" / audit.CACHE / "manifest.json"
    manifest = read_json(path)
    changed = deepcopy(manifest)
    changed["raw_manifest"]["retrieved_at_utc"] = "2026-10-01T00:00:00Z"
    write_json(path, changed)
    with pytest.raises(ValueError, match="match raw data"):
        audit.load_starter_audit(data)
    manifest["evidence"] = []
    write_json(path, manifest)
    with pytest.raises(DataQualityError, match="coverage"):
        audit.load_starter_audit(data)


def test_starter_is_not_selected_by_passing_volume(evidence_cache):
    data, snapshot, _, _, _ = evidence_cache
    changed_stats = snapshot.player_stats.with_columns(
        pl.when(pl.col("player_id") == "A").then(-10).otherwise(1000).alias("passing_yards")
    )
    directory = data / "raw" / "2022_2023_2024"
    raw_manifest = deepcopy(snapshot.manifest)
    raw_manifest["datasets"]["player_stats"] = store_frame(directory, "mutated", changed_stats)
    write_json(directory / "manifest.json", raw_manifest)
    path = data / "raw" / audit.CACHE / "manifest.json"
    manifest = read_json(path)
    manifest["raw_manifest"] = raw_manifest
    write_json(path, manifest)
    assert audit.load_starter_audit(data)["cases"][0]["reconciled_player_id"] == "A"


def test_starter_audit_ui_without_research_cache(evidence_cache, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    data, _, _, _, _ = evidence_cache
    monkeypatch.setenv("NFL_PROP_DATA_DIR", str(data))
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=20).run()
    app.radio(key="page").set_value("Starter audit").run()
    assert not app.exception and not app.error
    assert [m.value for m in app.metric] == ["1", "1", "0"]
    assert app.dataframe[0].value.iloc[0]["ESPN-listed starter"] == "Player A"


def test_refresh_retains_prior_evidence_snapshots(evidence_cache):
    data, _, _, _, _ = evidence_cache
    directory = data / "raw" / audit.CACHE
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != "manifest.json"}
    audit.fetch_starter_evidence(data, refresh=True)
    assert all((directory / name).read_bytes() == content for name, content in before.items())
    assert len(list(directory.glob("manifest-*.json"))) == 2
    assert audit.load_starter_audit(data)["counts"]["corrected_schedule_labels"] == 1


def test_failed_refresh_preserves_active_manifest(evidence_cache, monkeypatch):
    data, _, _, _, roster = evidence_cache
    path = data / "raw" / audit.CACHE / "manifest.json"
    before = path.read_bytes()
    original_request = audit.request_json

    def invalid_roster(url):
        if "/roster?" in url:
            changed = deepcopy(roster)
            changed["$ref"] = changed["$ref"].replace("competitors/1", "competitors/999")
            return changed
        return original_request(url)

    monkeypatch.setattr(audit, "request_json", invalid_roster)
    with pytest.raises(DataQualityError, match="identity mismatch"):
        audit.fetch_starter_evidence(data, refresh=True)
    assert path.read_bytes() == before
    assert audit.load_starter_audit(data)["counts"]["corrected_schedule_labels"] == 1
