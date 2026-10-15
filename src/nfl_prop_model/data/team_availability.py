"""Outcome-free schedule audit of dated depth and a preserved injury-report pilot."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_depth import (
    DEPTH_POLICY,
    SCHEDULE_FIELDS,
    DepthSnapshots,
    load_depth_source,
    load_schedule_only,
)
from nfl_prop_model.data.team_games import team_source_hash
from nfl_prop_model.data.team_injury_archive import INJURY_POLICY, injury_as_of, load_injury_pilot
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs
from nfl_prop_model.modeling.reporting import markdown_table

POLICY = {
    "depth": DEPTH_POLICY,
    "injuries": INJURY_POLICY,
    "outcomes": "project schedule metadata columns only; no scores, actual starter or fits",
    "readiness": "listed QB leaders and Week 1 pilot do not clear the enriched replay gate",
    "2022-2024": "untimed depth/roster labels remain excluded; no invented publication dates",
}


def availability_source_hash() -> str:
    base = Path(__file__).parent
    return hashlib.sha256(
        team_source_hash().encode()
        + Path(__file__).read_bytes()
        + (base / "team_depth.py").read_bytes()
        + (base / "team_injury_archive.py").read_bytes()
    ).hexdigest()


def audit_cutoffs(
    games: pl.DataFrame,
    qb: pl.DataFrame,
    inventory: pl.DataFrame,
    captures: list[tuple[datetime, dict[str, dict[str, Any]], str]],
) -> pl.DataFrame:
    if set(games.columns) != set(SCHEDULE_FIELDS) or set(games["season"]) != {2025}:
        raise DataQualityError(
            "Availability audit accepts only 2025 schedule metadata, no outcomes"
        )
    require_keys(games, ["game_id"], "availability schedule")
    cutoffs = weekly_cutoffs(games)
    conservative = DepthSnapshots(qb, inventory)
    immediate = DepthSnapshots(qb, inventory, delay_hours=0)
    rows = []
    for game in games.to_dicts():
        for horizon in HORIZONS:
            cutoff = (
                cutoffs[(game["season"], game["week"])]
                if horizon == "early_week"
                else game["kickoff_utc"] - timedelta(hours=1)
            )
            if cutoff >= game["kickoff_utc"]:
                raise DataQualityError("Availability cutoff must precede kickoff")
            for side in ("home", "away"):
                team = game[f"{side}_team"]
                depth = conservative.as_of(team, cutoff)
                sensitivity = immediate.as_of(team, cutoff)
                injury = injury_as_of(captures, team, game["season"], game["week"], cutoff)
                rows.append(
                    {
                        "game_id": game["game_id"],
                        "season": 2025,
                        "week": game["week"],
                        "side": side,
                        "team": team,
                        "horizon": horizon,
                        "cutoff_utc": cutoff,
                        "kickoff_utc": game["kickoff_utc"],
                        "depth_status": depth["status"],
                        "listed_qb_espn_id": depth["listed_qb_espn_id"],
                        "depth_loaded_at_utc": depth["loaded_at_utc"],
                        "depth_assumed_available_at_utc": depth["assumed_available_at_utc"],
                        "depth_age_hours": depth["age_hours"],
                        "depth_reason": depth["reason"],
                        "zero_delay_qb_espn_id": sensitivity["listed_qb_espn_id"],
                        "depth_delay_sensitive": depth["listed_qb_espn_id"]
                        != sensitivity["listed_qb_espn_id"],
                        "injury_status": injury["status"],
                        "injury_captured_at_utc": injury["captured_at_utc"],
                        "injury_archive_url": injury["archive_url"],
                        "injury_reason": injury["reason"],
                        "injury_out_count": injury["out_count"],
                        "injury_unknown_position_out_players": injury.get(
                            "unknown_position_out_players"
                        ),
                        **{
                            f"injury_{name}_out_count": injury.get(f"{name}_out_count")
                            for name in ("QB", "OL", "skill", "defense", "special")
                        },
                    }
                )
    frame = pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.col(
            "listed_qb_espn_id",
            "zero_delay_qb_espn_id",
            "depth_reason",
            "injury_reason",
            "injury_archive_url",
        ).cast(pl.String),
        pl.col(
            "depth_loaded_at_utc", "depth_assumed_available_at_utc", "injury_captured_at_utc"
        ).cast(pl.Datetime("us", "UTC")),
        pl.col("depth_age_hours").cast(pl.Float64),
        pl.col(
            [
                name
                for name in rows[0]
                if "_out_count" in name or name == "injury_unknown_position_out_players"
            ]
        ).cast(pl.Int64),
    )
    require_keys(frame, ["game_id", "side", "horizon"], "availability cutoff records")
    if (
        frame.height != games.height * 4
        or frame.filter(
            (pl.col("depth_assumed_available_at_utc") >= pl.col("cutoff_utc"))
            | (pl.col("injury_captured_at_utc") >= pl.col("cutoff_utc"))
        ).height
    ):
        raise DataQualityError("Availability source timing or cohort mismatch")
    return frame


def summarize(frame: pl.DataFrame) -> dict[str, Any]:
    coverage = []
    for horizon in HORIZONS:
        current = frame.filter(pl.col("horizon") == horizon)
        week1 = current.filter(pl.col("week") == 1)
        coverage.append(
            {
                "horizon": horizon,
                "team_slots": current.height,
                "listed_qb_leader_slots": current.filter(
                    pl.col("depth_status") == "listed_leader"
                ).height,
                "depth_unavailable_slots": current.filter(
                    pl.col("depth_status") == "unavailable"
                ).height,
                "depth_delay_sensitive_slots": current.filter(
                    pl.col("depth_delay_sensitive")
                ).height,
                "week1_slots": week1.height,
                "week1_designation_slots": week1.filter(
                    pl.col("injury_status") == "designations"
                ).height,
                "week1_explicit_no_designation_slots": week1.filter(
                    pl.col("injury_status") == "no_designations"
                ).height,
                "week1_partial_or_pending_slots": week1.filter(
                    pl.col("injury_status") == "partial_or_pending"
                ).height,
                "week1_unavailable_slots": week1.filter(
                    pl.col("injury_status") == "unavailable"
                ).height,
                "week1_unknown_position_out_slots": week1.filter(
                    pl.col("injury_unknown_position_out_players") > 0
                ).height,
            }
        )
    paired = (
        frame.filter(pl.col("horizon") == "early_week")
        .select("game_id", "team", pl.col("listed_qb_espn_id").alias("early_qb"))
        .join(
            frame.filter(pl.col("horizon") == "pregame_refresh").select(
                "game_id", "team", pl.col("listed_qb_espn_id").alias("refresh_qb")
            ),
            on=["game_id", "team"],
            validate="1:1",
        )
    )
    return {
        "coverage": coverage,
        "known_listed_qb_changes_between_horizons": paired.filter(
            pl.col("early_qb").is_not_null()
            & pl.col("refresh_qb").is_not_null()
            & (pl.col("early_qb") != pl.col("refresh_qb"))
        ).height,
        "depth_missing_reasons": frame.filter(pl.col("depth_status") == "unavailable")
        .group_by("horizon", "depth_reason")
        .len()
        .sort("horizon", "depth_reason")
        .to_dicts(),
        "2025_fits_performed": 0,
        "confirmed_game_starters_established": False,
        "full_enriched_replay_ready": False,
        "remaining": [
            "2022-2024 dated development starter/roster versions unresolved",
            "full active/IR roster and starter confirmation not supplied by listed depth leaders",
            "injury archive pilot is Week 1 only; full season coverage/identity adapter pending",
            "complete historical forecast weather adapter and coverage pending",
        ],
    }


def load_availability_audit(path: Path) -> dict[str, Any]:
    report = read_json(path)
    if report["source_sha256"] != availability_source_hash() or report["policy"] != POLICY:
        raise DataQualityError("Availability audit code/policy changed")
    for source in report["inputs"].values():
        if sha256_file(Path(source["manifest"])) != source["manifest_sha256"]:
            raise DataQualityError("Availability source manifest changed")
    qb, inventory = load_depth_source(Path(report["inputs"]["depth"]["manifest"]))
    captures = load_injury_pilot(Path(report["inputs"]["injury"]["manifest"]))
    games, _ = load_schedule_only(Path(report["inputs"]["schedule"]["manifest"]).parents[2])
    expected = audit_cutoffs(games, qb, inventory, captures)
    frame = load_frame(path.parent, report["artifact"])
    if not frame.equals(expected, null_equal=True) or summarize(frame) != report["summary"]:
        raise DataQualityError("Availability artifact/summary disagrees with source cutoffs")
    return report


def save_availability_audit(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    source_hash = availability_source_hash()
    depth_path = data_dir / "raw" / "team_availability" / "2025" / "manifest.json"
    injury_path = data_dir / "raw" / "team_availability" / "injury_week1_2025" / "manifest.json"
    games, schedule = load_schedule_only(data_dir)
    inputs = {
        "depth": {
            "manifest": str(depth_path.resolve()),
            "manifest_sha256": sha256_file(depth_path),
        },
        "injury": {
            "manifest": str(injury_path.resolve()),
            "manifest_sha256": sha256_file(injury_path),
        },
        "schedule": schedule,
    }
    input_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    directory = (
        data_dir / "processed" / "team_availability" / f"{source_hash[:12]}-{input_hash[:12]}"
    )
    path = directory / "manifest.json"
    if path.exists():
        report = load_availability_audit(path)
    else:
        write_json(
            directory / "protocol.json",
            {
                "policy": POLICY,
                "source_sha256": source_hash,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        qb, inventory = load_depth_source(depth_path)
        captures = load_injury_pilot(injury_path)
        frame = audit_cutoffs(games, qb, inventory, captures)
        if availability_source_hash() != source_hash or any(
            sha256_file(Path(value["manifest"])) != value["manifest_sha256"]
            for value in inputs.values()
        ):
            raise DataQualityError("Availability inputs/code changed during audit")
        report = {
            "format_version": 1,
            "source_sha256": source_hash,
            "policy": POLICY,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "inputs": inputs,
            "games": games.height,
            "records": frame.height,
            "summary": summarize(frame),
            "depth_source": read_json(depth_path),
            "injury_source": read_json(injury_path),
            "artifact": store_frame(directory, "availability", frame),
        }
        write_json(path, report)
    load_availability_audit(path)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    coverage = pl.DataFrame(report["summary"]["coverage"])
    coverage.write_csv(report_dir / "coverage.csv")
    (report_dir / "findings.md").write_text(
        "# Dated availability source audit\n\nOutcome-free metadata audit: "
        "272 games, 1,088 team/horizon slots. Zero fits.\n\n"
        + "\n".join(markdown_table(coverage))
        + "\n\n"
        "Depth is a listed QB leader with a 24-hour assumed release delay, "
        "not a confirmed starter or complete active/IR roster. "
        "ESPN IDs avoid the producer's retrospective GSIS mapping. "
        "Removed, stale, ambiguous and missing snapshots stay unknown.\n\n"
        "Injuries use a bounded Week 1 pilot: original archived bodies verified "
        "against index digests and captured before the cutoff. "
        "No current page or early publication date substitutes for a preserved edition. "
        "Missing reports and unclassified positions remain unknown.\n\n"
        "Full enriched 2025 replay remains gated.\n",
        encoding="utf-8",
    )
    return report
