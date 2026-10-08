"""Pregame candidate enrollment and offline, denominator-preserving outcome audits."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import polars as pl

from nfl_prop_model.data.prospective import _frame, load_feature_report, read_feature_snapshot
from nfl_prop_model.data.schemas import validate_sources
from nfl_prop_model.data.status_reviews import checksum, review_context, timestamp
from nfl_prop_model.data.storage import read_json, sha256_file, write_json
from nfl_prop_model.data.upcoming import CACHE_MAX_AGE, schedule_games

PROTOCOL = {
    "version": 1,
    "scope": "prospective_candidate_participation_audit",
    "enrollment": "Every chart candidate in the chosen feature snapshot, "
    "including abstentions and unmapped IDs",
    "deadline": "Register before every enrolled game's scheduled kickoff minus one hour",
    "overlap": "Each game can belong to only one local registry; no replacement after outcomes",
    "outcomes": "Wait for both scores, kickoff plus 24 hours, "
    "and a stats snapshot retrieved after that time",
    "appearance": "An explicit regular-season QB stats row with a finite passing-yards value; "
    "zero attempts/yards are retained",
    "absence": "No recorded QB appearance is not proof of inactive/DNP; "
    "never manufacture a zero target",
    "missing": "Unmapped identities, schedule changes and missing source coverage "
    "retain their denominator rows",
    "model": "No predictions, probabilities, calibration tests, model selection "
    "or production enablement",
    "next_validation": "Independent gameday roster/gamebook adjudication and a later "
    "prespecified forecast evaluation are still required",
}


def read_registry(directory: Path) -> dict[str, Any]:
    envelope = read_json(directory / "registry.json")
    registry: dict[str, Any] = envelope["payload"]
    snapshot = directory / "features.json"
    if (
        directory.is_symlink()
        or snapshot.is_symlink()
        or envelope["sha256"] != checksum(registry)
        or registry["format_version"] != 1
        or registry["scope"] != PROTOCOL["scope"]
        or registry["protocol"] != PROTOCOL
        or registry["protocol_sha256"] != checksum(PROTOCOL)
        or registry["snapshot_sha256"] != sha256_file(snapshot)
        or registry["production_enabled"] is not False
        or directory.name != f"cohort-{registry['registry_id']}"
    ):
        raise ValueError("Participation registry checksum/scope mismatch")
    report = read_json(snapshot)
    if (
        report["scope"] != "prospective_feature_audit_only"
        or report["production_enabled"] is not False
        or any(row["forecast_available"] for row in report["candidates"])
    ):
        raise ValueError("Participation enrollment must retain disabled forecasts")
    if registry["snapshot_as_of_utc"] != report["as_of_utc"]:
        raise ValueError("Registry and snapshot times differ")
    registered = timestamp(registry["registered_at_utc"])
    rows = report["candidates"]
    if not rows or timestamp(report["as_of_utc"]) > registered:
        raise ValueError("Registry must contain a pre-enrollment candidate snapshot")
    if any(registered >= timestamp(row["forecast_cutoff_utc"]) for row in rows):
        raise ValueError("Registry was created after an enrolled forecast cutoff")
    if registry["candidate_count"] != len(rows) or registry["game_ids"] != sorted(
        {row["game_id"] for row in rows}
    ):
        raise ValueError("Registry denominator differs from its snapshot")
    return registry


def register_candidates(data_dir: Path, snapshot: Path, *, now: datetime | None = None) -> Path:
    """Freeze all candidates locally before outcomes; never drop blocked/unknown rows."""
    as_of = timestamp((now or datetime.now(UTC)).isoformat())
    report = read_feature_snapshot(snapshot)
    live = load_feature_report(data_dir, as_of=as_of, days=report["horizon_days"])
    rows = report["candidates"]
    if not rows or timestamp(report["as_of_utc"]) > as_of:
        raise ValueError("Choose a nonempty snapshot created before registration")
    if any(as_of >= timestamp(row["forecast_cutoff_utc"]) for row in rows):
        raise ValueError("Register before every enrolled forecast cutoff")
    if len(rows) != len(live["candidates"]) or {checksum(review_context(row)) for row in rows} != {
        checksum(review_context(row)) for row in live["candidates"]
    }:
        raise ValueError("Candidate pool changed; save a new feature snapshot before enrollment")
    if (
        checksum(report["sources"]) != checksum(live["sources"])
        or checksum(report["current_stats_source"]) != checksum(live["current_stats_source"])
        or report["pipeline_source_hashes"] != live["pipeline_source_hashes"]
        or any(not row["sources_fresh"] for row in live["candidates"])
        or live["current_stats_age_hours"] > 24
    ):
        raise ValueError("Enrollment requires fresh, unchanged source and code snapshots")
    root = data_dir / "participation"
    root.mkdir(parents=True, exist_ok=True)
    # Serialize overlap checks and publication across CLI/UI processes. A failed process
    # leaves its lock visible rather than silently allowing a second enrollment.
    lock = root / "enrollment.lock"
    with lock.open("x", encoding="utf-8"):
        pass
    try:
        games = sorted({row["game_id"] for row in rows})
        for prior in root.glob("cohort-*"):
            if set(read_registry(prior)["game_ids"]) & set(games):
                raise ValueError("A selected game is already enrolled; audit its existing registry")
        registered = timestamp((now or datetime.now(UTC)).isoformat())
        if registered < as_of or any(
            registered >= timestamp(row["forecast_cutoff_utc"]) for row in rows
        ):
            raise ValueError("Registration clock crossed a forecast cutoff or moved backwards")
        identifier = uuid4().hex
        directory = root / f"cohort-{identifier}"
        directory.mkdir(exist_ok=False)
        (directory / "features.json").write_bytes((snapshot / "features.json").read_bytes())
        registry = {
            "format_version": 1,
            "scope": PROTOCOL["scope"],
            "registry_id": identifier,
            "registered_at_utc": registered.isoformat(),
            "snapshot_as_of_utc": report["as_of_utc"],
            "snapshot_sha256": sha256_file(directory / "features.json"),
            "protocol": PROTOCOL,
            "protocol_sha256": checksum(PROTOCOL),
            "game_ids": games,
            "candidate_count": len(rows),
            "prior_diagnostic_reference": report["history_sources"]["accessed_2025"],
            "production_enabled": False,
        }
        write_json(directory / "registry.json", {"payload": registry, "sha256": checksum(registry)})
        read_registry(directory)
        return directory
    finally:
        lock.unlink(missing_ok=True)


def audit_participation(
    data_dir: Path, directory: Path, *, as_of: datetime | None = None
) -> dict[str, Any]:
    as_of = timestamp((as_of or datetime.now(UTC)).isoformat())
    registry = read_registry(directory)
    if as_of < timestamp(registry["registered_at_utc"]):
        raise ValueError("Audit cannot precede enrollment")
    frozen = read_json(directory / "features.json")
    schedules_dir = data_dir / "raw" / "upcoming_2026"
    schedule_source = read_json(schedules_dir / "manifest.json")
    stats_dir = data_dir / "raw" / "current_stats_2026"
    stats_source = read_json(stats_dir / "manifest.json")
    schedule_time = timestamp(schedule_source["retrieved_at_utc"])
    stats_time = timestamp(stats_source["retrieved_at_utc"])
    if max(schedule_time, stats_time) > as_of:
        raise ValueError("Audit sources were retrieved after the audit time")
    if stats_source["season"] != 2026 or schedule_source["season"] != 2026:
        raise ValueError("Participation audit requires 2026 sources")
    schedules = _frame(schedules_dir, schedule_source["datasets"]["schedules"])
    stats = _frame(stats_dir, stats_source["player_stats"])
    validate_sources(stats, schedules)
    if set(stats["season"].unique()) != {2026}:
        raise ValueError("Participation stats must contain exactly 2026")
    games = {
        row["game_id"]: row
        for row in schedule_games(schedules)
        .join(schedules.select("game_id", "home_score", "away_score"), on="game_id", validate="1:1")
        .iter_rows(named=True)
    }
    qb = stats.filter((pl.col("position") == "QB") & (pl.col("season_type") == "REG"))
    rows = []
    for candidate in frozen["candidates"]:
        row = {
            "context": review_context(candidate),
            "player_name": candidate["player_name"],
            "features_ready_at_enrollment": candidate["features_ready"],
            "forecast_blockers_at_enrollment": candidate["forecast_blockers"],
            "primary_status_at_enrollment": candidate.get("primary_status", {}),
            "state": "pending_game",
            "observed_passing_yards": None,
            "observed_attempts": None,
        }
        game = games.get(candidate["game_id"])
        cutoff = timestamp(candidate["kickoff_utc"]) + timedelta(hours=24)
        home = candidate["team"] if candidate["designated_home"] else candidate["opponent_team"]
        away = candidate["opponent_team"] if candidate["designated_home"] else candidate["team"]
        if game is None:
            row["state"] = "schedule_missing"
        elif (
            game["season"] != candidate["season"]
            or game["week"] != candidate["week"]
            or game["home_team"] != home
            or game["away_team"] != away
            or game["kickoff_utc"] != timestamp(candidate["kickoff_utc"])
        ):
            row["state"] = "schedule_changed"
        elif as_of >= cutoff and game["home_score"] is not None and game["away_score"] is not None:
            if (
                min(stats_time, schedule_time) < cutoff
                or as_of - min(stats_time, schedule_time) > CACHE_MAX_AGE
            ):
                row["state"] = "pending_fresh_postgame_sources"
            elif candidate["gsis_id"] is None:
                row["state"] = "identity_unmapped"
            else:
                slot = qb.filter(
                    (pl.col("game_id") == candidate["game_id"])
                    & (pl.col("team") == candidate["team"])
                )
                match = slot.filter(pl.col("player_id") == candidate["gsis_id"])
                valid = slot.filter(
                    pl.col("passing_yards").is_not_null() & pl.col("passing_yards").is_finite()
                )
                wrong_slot = slot.filter(
                    (pl.col("season") != 2026)
                    | (pl.col("week") != candidate["week"])
                    | (pl.col("opponent_team") != candidate["opponent_team"])
                    | pl.col("week").is_null()
                    | pl.col("opponent_team").is_null()
                )
                if wrong_slot.height:
                    row["state"] = "qb_stats_identity_conflict"
                elif slot.is_empty() or valid.height != slot.height:
                    row["state"] = "qb_stats_coverage_missing"
                elif match.is_empty():
                    row["state"] = "no_recorded_qb_appearance"
                elif (
                    match.height != 1
                    or match["season"][0] != 2026
                    or match["week"][0] != candidate["week"]
                    or match["opponent_team"][0] != candidate["opponent_team"]
                ):
                    row["state"] = "qb_stats_identity_conflict"
                else:
                    row["state"] = "recorded_qb_appearance"
                    row["observed_passing_yards"] = match["passing_yards"][0]
                    row["observed_attempts"] = match["attempts"][0]
        rows.append(row)
    return {
        "format_version": 1,
        "scope": PROTOCOL["scope"],
        "as_of_utc": as_of.isoformat(),
        "registry": registry,
        "registry_sha256": checksum(registry),
        "protocol_sha256": checksum(PROTOCOL),
        "sources": {"schedules": schedule_source, "player_stats": stats_source},
        "candidate_count": len(rows),
        "counts": dict(sorted(Counter(row["state"] for row in rows).items())),
        "candidates": rows,
        "production_enabled": False,
        "participation_validation_complete": False,
    }


def export_enrollment(directory: Path, destination: Path) -> None:
    """Publish the fixed denominator and fingerprints without redistributing source HTML."""
    registry = read_registry(directory)
    frozen = read_json(directory / "features.json")
    write_json(
        destination,
        {
            "scope": "public_participation_enrollment",
            "registry": registry,
            "registry_sha256": checksum(registry),
            "pipeline_source_hashes": frozen["pipeline_source_hashes"],
            "source_hashes": {
                "schedules": frozen["sources"]["datasets"]["schedules"]["sha256"],
                "depth_charts": frozen["sources"]["datasets"]["depth_charts"]["sha256"],
                "player_stats": frozen["current_stats_source"]["player_stats"]["sha256"],
            },
            "candidates": [
                {
                    "context": review_context(row),
                    "player_name": row["player_name"],
                    "features_sha256": checksum(row["features"]),
                    "features_ready": row["features_ready"],
                    "forecast_blockers": row["forecast_blockers"],
                    "primary_annotations": [
                        {
                            key: record[key]
                            for key in (
                                "kind",
                                "claim",
                                "source_url",
                                "published_at_utc",
                                "retrieved_at_utc",
                                "source_sha256",
                            )
                        }
                        for record in row.get("primary_status", {}).get("records", [])
                    ],
                }
                for row in frozen["candidates"]
            ],
            "attribution": "nflverse CC-BY-4.0 distribution; ESPN-derived chart candidates; "
            "primary article annotations are research claims",
            "production_enabled": False,
        },
    )


def write_participation_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "participation.json", report)
    registry = report["registry"]
    lines = [
        "# Prospective candidate participation registry",
        "",
        f"Audit as of {report['as_of_utc']}.",
        "",
        f"Enrolled {registry['registered_at_utc']}: {report['candidate_count']} candidates "
        f"across {len(registry['game_ids'])} games.",
        "",
        "All enrolled rows remain in the denominator. No forecasts enabled; "
        "participation validation remains incomplete.",
        "",
        *[f"- {state}: {count}" for state, count in report["counts"].items()],
        "",
        f"Registry SHA-256: `{report['registry_sha256']}`",
        "",
        f"Snapshot SHA-256: `{registry['snapshot_sha256']}`",
        "",
        f"Protocol SHA-256: `{registry['protocol_sha256']}`",
        "",
        "## Prespecified rules",
        "",
        *[f"- {key}: {value}" for key, value in PROTOCOL.items()],
        "",
    ]
    (report_dir / "participation.md").write_text("\n".join(lines), encoding="utf-8")
