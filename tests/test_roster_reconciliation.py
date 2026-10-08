from copy import deepcopy
from datetime import timedelta

import polars as pl
import pytest
from test_participation import outcomes
from test_prospective import AS_OF, buf, save_inputs

from nfl_prop_model.cli import main
from nfl_prop_model.data import roster_reconciliation as module
from nfl_prop_model.data.participation import PROTOCOL, read_registry, register_candidates
from nfl_prop_model.data.prospective import load_feature_report, save_feature_snapshot
from nfl_prop_model.data.schemas import DataQualityError
from nfl_prop_model.data.status_reviews import checksum, timestamp
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    save_inputs(tmp_path)
    root = tmp_path / "raw" / "upcoming_2026"
    manifest = read_json(root / "manifest.json")
    schedules = load_frame(root, manifest["datasets"]["schedules"]).with_columns(
        pl.lit("123").alias("espn")
    )
    depth = load_frame(root, manifest["datasets"]["depth_charts"]).with_columns(
        pl.col("espn_id")
        .replace({"espn-BUF": "11", "espn-ARI": "22", "rookie": "33"})
        .alias("espn_id")
    )
    manifest["datasets"]["schedules"] = store_frame(root, "schedules", schedules)
    manifest["datasets"]["depth_charts"] = store_frame(root, "depth_charts", depth)
    write_json(root / "manifest.json", manifest)
    report = load_feature_report(tmp_path, as_of=AS_OF)
    snapshot = save_feature_snapshot(tmp_path, report)
    registry = register_candidates(tmp_path, snapshot, now=AS_OF)
    kickoff = buf(report)["kickoff_utc"]
    header = {
        "id": "123",
        "week": 3,
        "season": {"year": 2026, "type": 2},
        "competitions": [
            {
                "id": "123",
                "date": kickoff,
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"homeAway": side, "team": {"id": identity, "abbreviation": team}}
                    for side, identity, team in (("home", "1", "BUF"), ("away", "2", "ARI"))
                ],
            }
        ],
    }
    rosters = {
        team_id: {
            "$ref": "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
            f"events/123/competitions/123/competitors/{team_id}/roster?lang=en",
            "entries": [
                {
                    "playerId": player_id,
                    "starter": True,
                    "didNotPlay": False,
                    "valid": True,
                    "period": 0,
                    "active": False,
                    "position": {"$ref": module.POSITION_SOURCE},
                }
            ],
        }
        for team_id, player_id in (("1", 11), ("2", 22))
    }
    rosters["1"]["entries"].append(
        {**rosters["1"]["entries"][0], "playerId": 33, "starter": False, "valid": False}
    )
    identities = pl.DataFrame(
        {
            "espn_id": ["11", "22", "33"],
            "gsis_id": ["gsis-BUF", "gsis-ARI", None],
            "display_name": ["Current BUF", "Current ARI", "Unmapped newcomer"],
            "position": ["QB"] * 3,
        }
    )
    calls = []

    def request(url):
        calls.append(url)
        if url == module.POSITION_SOURCE:
            return {"id": "8", "abbreviation": "QB"}
        if "summary?" in url:
            return {"header": deepcopy(header), "odds": {"unused": True}}
        return deepcopy(rosters["1" if "/competitors/1/" in url else "2"])

    import nflreadpy

    monkeypatch.setattr(nflreadpy, "load_players", lambda: identities)
    monkeypatch.setattr(module, "request_json", request)
    return {
        "data": tmp_path,
        "registry": registry,
        "report": report,
        "header": header,
        "rosters": rosters,
        "identities": identities,
        "calls": calls,
    }


def postgame(case, **kwargs):
    return outcomes(case["data"], case["report"], **kwargs)


def refresh(case, as_of):
    return module.fetch_roster_evidence(case["data"], case["registry"], now=as_of)


def reconciliation(case, as_of):
    return module.reconcile_rosters(case["data"], case["registry"], as_of=as_of)


def qb(report):
    return next(row for row in report["candidates"] if row["context"]["gsis_id"] == "gsis-BUF")


def test_future_pool_does_not_request_rosters_positions_or_players(enrolled, monkeypatch):
    import nflreadpy

    monkeypatch.setattr(
        nflreadpy, "load_players", lambda: pytest.fail("No future identity download")
    )
    refresh(enrolled, AS_OF)
    report = reconciliation(enrolled, AS_OF)
    assert enrolled["calls"] == []
    assert report["roster_counts"] == {"pending_game": 3}
    assert report["corroborated_rows"] == 0
    assert not report["participation_validation_complete"] and not report["production_enabled"]


def test_zero_targets_and_false_active_field_are_preserved(enrolled):
    before = sha256_file(enrolled["registry"] / "registry.json")
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    report = reconciliation(enrolled, as_of)
    assert report["roster_counts"] == {"corroborated_participation": 2, "identity_unmapped": 1}
    assert qb(report)["observed_passing_yards"] == 0
    assert qb(report)["observed_attempts"] == 0
    assert qb(report)["roster_reconciliation"]["flags"]["starter"]
    assert "active" not in qb(report)["roster_reconciliation"]["flags"]
    assert sha256_file(enrolled["registry"] / "registry.json") == before
    assert read_registry(enrolled["registry"])["protocol_sha256"] == checksum(PROTOCOL)


@pytest.mark.parametrize(
    "absent,dnp,valid,state",
    [
        (True, True, False, "corroborated_dnp"),
        (False, True, False, "roster_stats_conflict"),
        (False, False, False, "roster_flags_ambiguous"),
        (True, False, True, "roster_participant_target_unresolved"),
    ],
)
def test_dnp_ambiguity_and_conflicts_never_invent_targets(enrolled, absent, dnp, valid, state):
    entry = enrolled["rosters"]["1"]["entries"][0]
    entry.update(starter=False, didNotPlay=dnp, valid=valid)
    as_of = postgame(enrolled, absent=absent)
    refresh(enrolled, as_of)
    row = qb(reconciliation(enrolled, as_of))
    assert row["roster_reconciliation"]["state"] == state
    assert row["observed_passing_yards"] is None if absent else row["observed_passing_yards"] == 0
    assert "inactive" not in row["roster_reconciliation"]["state"]


def test_missing_roster_candidate_is_not_dnp(enrolled):
    enrolled["rosters"]["1"]["entries"].pop(0)
    as_of = postgame(enrolled, absent=True)
    refresh(enrolled, as_of)
    assert (
        qb(reconciliation(enrolled, as_of))["roster_reconciliation"]["state"]
        == "candidate_missing_from_qb_roster"
    )


def test_independent_mapping_must_match_original_ids(enrolled, monkeypatch):
    import nflreadpy

    identities = enrolled["identities"].with_columns(
        pl.when(pl.col("espn_id") == "11")
        .then(pl.lit("another-GSIS"))
        .otherwise(pl.col("gsis_id"))
        .alias("gsis_id")
    )
    monkeypatch.setattr(nflreadpy, "load_players", lambda: identities)
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    assert (
        qb(reconciliation(enrolled, as_of))["roster_reconciliation"]["state"]
        == "identity_mapping_conflict"
    )


@pytest.mark.parametrize(
    "field,value",
    [("starter", 1), ("didNotPlay", "false"), ("valid", None), ("period", True), ("period", 1)],
)
def test_roster_flags_require_exact_types_and_period_zero(enrolled, field, value):
    enrolled["rosters"]["1"]["entries"][0][field] = value
    with pytest.raises(DataQualityError, match="boolean flags"):
        refresh(enrolled, postgame(enrolled))


@pytest.mark.parametrize(
    "mutation", ["incomplete", "wrong_year", "wrong_week", "wrong_date", "wrong_side"]
)
def test_event_identity_failures_are_closed(enrolled, mutation):
    header = enrolled["header"]
    if mutation == "incomplete":
        header["competitions"][0]["status"]["type"]["completed"] = False
    elif mutation == "wrong_year":
        header["season"]["year"] = 2025
    elif mutation == "wrong_week":
        header["week"] = 2
    elif mutation == "wrong_date":
        header["competitions"][0]["date"] = (
            timestamp(buf(enrolled["report"])["kickoff_utc"]) + timedelta(minutes=1)
        ).isoformat()
    else:
        header["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "ARI"
    with pytest.raises(DataQualityError):
        refresh(enrolled, postgame(enrolled))


def test_offline_source_hash_and_future_timestamp_checks(enrolled):
    as_of = postgame(enrolled)
    manifest = refresh(enrolled, as_of)
    directory = module.cache_directory(
        enrolled["data"], read_registry(enrolled["registry"])["registry_id"]
    )
    path = directory / manifest["evidence"][0]["roster"]["file"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        reconciliation(enrolled, as_of)


def test_rehashed_future_evidence_still_fails(enrolled):
    as_of = postgame(enrolled)
    manifest = refresh(enrolled, as_of)
    manifest["evidence"][0]["roster"]["retrieved_at_utc"] = (
        as_of + timedelta(seconds=1)
    ).isoformat()
    directory = module.cache_directory(
        enrolled["data"], read_registry(enrolled["registry"])["registry_id"]
    )
    write_json(directory / "manifest.json", {"payload": manifest, "sha256": checksum(manifest)})
    with pytest.raises(DataQualityError, match="before audit"):
        reconciliation(enrolled, as_of)


def test_failed_refresh_preserves_active_manifest(enrolled):
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    directory = module.cache_directory(
        enrolled["data"], read_registry(enrolled["registry"])["registry_id"]
    )
    before = (directory / "manifest.json").read_bytes()
    enrolled["rosters"]["1"]["entries"][0]["starter"] = "true"
    with pytest.raises(DataQualityError):
        refresh(enrolled, as_of)
    assert (directory / "manifest.json").read_bytes() == before


def test_missing_stats_stays_unresolved_with_valid_roster(enrolled):
    as_of = postgame(enrolled, missing=True)
    refresh(enrolled, as_of)
    row = qb(reconciliation(enrolled, as_of))
    assert row["roster_reconciliation"]["state"] == "roster_participant_target_unresolved"
    assert row["observed_passing_yards"] is None


def test_offline_cli_preserves_pool(enrolled, monkeypatch, tmp_path):
    original = module.reconcile_rosters
    monkeypatch.setattr(
        module, "reconcile_rosters", lambda data, registry: original(data, registry, as_of=AS_OF)
    )
    assert (
        main(
            [
                "participation-reconcile",
                "--data-dir",
                str(enrolled["data"]),
                "--registry",
                str(enrolled["registry"]),
                "--report-dir",
                str(tmp_path / "report"),
            ]
        )
        == 0
    )
    report = read_json(tmp_path / "report" / "reconciliation.json")
    assert report["candidate_count"] == 3
    assert report["roster_counts"] == {"pending_game": 3}
    assert enrolled["calls"] == []


@pytest.mark.parametrize(
    "starter,availability,starter_result,availability_result",
    [
        ("confirmed", "active", "agrees_with_roster", "activation_not_established_by_roster_flags"),
        (
            "not_starter",
            "inactive",
            "contradicts_roster",
            "contradicted_by_corroborated_participation",
        ),
        (
            "confirmed",
            "ruled_out",
            "agrees_with_roster",
            "contradicted_by_corroborated_participation",
        ),
    ],
)
def test_claim_comparison_does_not_verify_activation_or_article_meaning(
    enrolled, monkeypatch, starter, availability, starter_result, availability_result
):
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    original = module.audit_participation

    def audit(data, registry, **kwargs):
        report = original(data, registry, **kwargs)
        qb(report)["primary_status_at_enrollment"] = {
            "starter": starter,
            "availability": availability,
        }
        return report

    monkeypatch.setattr(module, "audit_participation", audit)
    check = qb(reconciliation(enrolled, as_of))["roster_reconciliation"]
    assert check["starter_claim_result"] == starter_result
    assert check["availability_claim_result"] == availability_result


def test_duplicate_qb_roster_ids_are_rejected(enrolled):
    enrolled["rosters"]["1"]["entries"].append(deepcopy(enrolled["rosters"]["1"]["entries"][0]))
    with pytest.raises(DataQualityError, match="duplicate"):
        refresh(enrolled, postgame(enrolled))


def test_no_roster_read_downloads_or_rechecks_2025(enrolled, monkeypatch):
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    import nflreadpy

    def fail(*args, **kwargs):
        pytest.fail("Offline reconciliation must not download or evaluate any season")

    monkeypatch.setattr(module, "request_json", fail)
    monkeypatch.setattr(nflreadpy, "load_players", fail)
    monkeypatch.setattr(nflreadpy, "load_player_stats", fail)
    monkeypatch.setattr(nflreadpy, "load_schedules", fail)
    assert reconciliation(enrolled, as_of)["candidate_count"] == 3


def test_unknown_original_id_is_never_filled_from_later_mapping(enrolled, monkeypatch):
    import nflreadpy

    identities = enrolled["identities"].with_columns(
        pl.col("gsis_id").fill_null("newly-mapped-GSIS")
    )
    monkeypatch.setattr(nflreadpy, "load_players", lambda: identities)
    as_of = postgame(enrolled)
    refresh(enrolled, as_of)
    report = reconciliation(enrolled, as_of)
    row = next(row for row in report["candidates"] if row["context"]["gsis_id"] is None)
    assert row["roster_reconciliation"]["state"] == "identity_unmapped"
    assert row["context"]["gsis_id"] is None and row["observed_passing_yards"] is None
