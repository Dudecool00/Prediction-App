"""Offline development cutoff audit; observed table counts are not modeling features."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_injury_archive import _gunzip
from nfl_prop_model.data.team_injury_development import (
    POLICY,
    development_cutoffs,
    development_path,
    load_development,
    load_development_schedule,
)
from nfl_prop_model.data.team_injury_history import _digest, capture_time
from nfl_prop_model.data.team_injury_tables import GROUPS, parse_tables
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

COUNTS = (
    "player_rows",
    "blank_game_status_rows",
    "observed_out_count",
    "observed_questionable_count",
    "observed_doubtful_count",
    "unknown_out_positions",
    *[g + "_observed_out_count" for g in GROUPS],
)


def development_source_hash() -> str:
    base = Path(__file__).parent
    paths = [
        Path(__file__),
        base / "team_injury_development.py",
        base / "team_injury_tables.py",
        base / "team_injury_history.py",
        base / "team_injury_archive.py",
        base / "team_games.py",
        base / "team_depth.py",
        base.parent / "features" / "team_epa.py",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def development_records(
    games: pl.DataFrame,
    weeks: dict[tuple[int, int], dict[str, Any]],
    directory: Path,
) -> pl.DataFrame:
    cutoffs = development_cutoffs(games)
    if set(weeks) != set(cutoffs):
        raise DataQualityError("Development injury source week cohort mismatch")
    parsed: dict[tuple[int, int, str], dict[str, Any]] = {}
    for (season, week), source in weeks.items():
        for receipt in source["captures"]:
            key = (season, week, receipt["timestamp"])
            if receipt["status"] != "verified":
                parsed[key] = {"reason": "unverified_edition"}
                continue
            body = _gunzip(
                (directory / str(season) / f"week-{week:02}" / receipt["file"]).read_bytes()
            )
            if _digest(body) != receipt["digest"]:
                raise DataQualityError("Development entity differs from selected edition")
            try:
                parsed[key] = {"teams": parse_tables(body, season, week)}
            except (OSError, ValueError) as error:
                parsed[key] = {"reason": "unsupported_edition", "detail": str(error)}
    early = weekly_cutoffs(games)
    records = []
    for game in games.to_dicts():
        season, week = game["season"], game["week"]
        source = weeks[(season, week)]
        for horizon in HORIZONS:
            cutoff = (
                early[(season, week)]
                if horizon == "early_week"
                else game["kickoff_utc"] - timedelta(hours=1)
            )
            eligible = [r for r in source["captures"] if capture_time(r) < cutoff]
            receipt = max(eligible, key=lambda r: r["timestamp"]) if eligible else None
            for side, other in (("home", "away"), ("away", "home")):
                fields: dict[str, Any] = {}
                reason = "no_earlier_edition" if source["article"] else "week_url_not_in_catalog"
                detail = None
                if receipt:
                    decoded = parsed[(season, week, receipt["timestamp"])]
                    if cutoff - capture_time(receipt) > timedelta(hours=72):
                        reason = "stale_edition"
                    elif "reason" in decoded:
                        reason, detail = decoded["reason"], decoded.get("detail")
                    else:
                        fields = decoded["teams"].get(game[f"{side}_team"], {})
                        if fields and fields["opponent"] != game[f"{other}_team"]:
                            fields, reason = {}, "schedule_matchup_mismatch"
                        else:
                            reason = "" if fields else "team_section_missing"
                records.append(
                    {
                        "game_id": game["game_id"],
                        "season": season,
                        "week": week,
                        "team": game[f"{side}_team"],
                        "side": side,
                        "horizon": horizon,
                        "cutoff_utc": cutoff,
                        "status": fields.get("status", "unavailable"),
                        "reason": reason or None,
                        "detail": detail,
                        "archive_captured_at_utc": capture_time(receipt) if receipt else None,
                        "archive_url": receipt["archive_url"] if receipt else None,
                        **{name: fields.get(name) for name in COUNTS},
                        "unsupported_rows": fields.get("unsupported_rows"),
                        "final_report_verified": False,
                        "model_eligible": False,
                    }
                )
    frame = (
        pl.DataFrame(records, infer_schema_length=None)
        .with_columns(
            pl.col("reason", "detail", "archive_url").cast(pl.String),
            pl.col("archive_captured_at_utc").cast(pl.Datetime("us", "UTC")),
            pl.col(*COUNTS).cast(pl.Int64),
            pl.col("unsupported_rows").cast(pl.Boolean),
        )
        .sort("season", "week", "game_id", "side", "horizon")
    )
    require_keys(frame, ["game_id", "side", "horizon"], "development injury records")
    if (
        frame.height != games.height * 4
        or frame.filter(pl.col("archive_captured_at_utc") >= pl.col("cutoff_utc")).height
    ):
        raise DataQualityError("Development injury cutoff/cohort mismatch")
    return frame


def _summarize(frame: pl.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    rows = []
    for key, group in frame.partition_by(keys, as_dict=True).items():
        rows.append(
            {
                **dict(zip(keys, key, strict=True)),
                "team_slots": group.height,
                **{
                    s: group.filter(pl.col("status") == s).height
                    for s in (
                        "designations_present",
                        "practice_only_or_pending",
                        "unavailable",
                    )
                },
                "final_reports_verified": 0,
                "model_eligible": 0,
            }
        )
    return sorted(rows, key=lambda r: tuple(r[k] for k in keys))


def _diagnostics(frame: pl.DataFrame) -> dict[str, Any]:
    missing = (
        frame.filter(pl.col("reason").is_not_null())
        .group_by("season", "horizon", "reason")
        .len()
        .sort("season", "horizon", "reason")
    )
    unsupported = (
        frame.filter(pl.col("reason") == "unsupported_edition")
        .select(
            "season",
            "week",
            "archive_url",
            "detail",
        )
        .unique()
        .sort("season", "week", "archive_url")
    )
    return {"missing_reasons": missing.to_dicts(), "unsupported_editions": unsupported.to_dicts()}


def load_development_audit(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or report["source_sha256"] != development_source_hash()
        or report["policy"] != POLICY
    ):
        raise DataQualityError("Development injury audit code/policy changed")
    games, schedule = load_development_schedule(data_dir)
    raw = development_path(data_dir, games)
    if report["schedule"] != schedule or report["raw_manifest_sha256"] != sha256_file(raw):
        raise DataQualityError("Development injury audit source inputs changed")
    weeks = load_development(raw, games)
    frame = load_frame(path.parent, report["artifact"])
    expected = development_records(games, weeks, raw.parent)
    if (
        not frame.equals(expected, null_equal=True)
        or report["coverage"] != _summarize(frame, ["season", "horizon"])
        or report["weekly_coverage"] != _summarize(frame, ["season", "week", "horizon"])
        or any(report[k] != v for k, v in _diagnostics(frame).items())
        or report["games"] != games.height
        or report["records"] != frame.height
        or report["model_fits_performed"] != 0
        or report["enriched_replay_ready"] is not False
        or report["matched_context_comparison_ready"] is not False
    ):
        raise DataQualityError("Development injury audit differs from preserved source cutoffs")
    return report


def save_development_audit(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    games, schedule = load_development_schedule(data_dir)
    raw = development_path(data_dir, games)
    source_hash, raw_hash = development_source_hash(), sha256_file(raw)
    input_hash = hashlib.sha256((raw_hash + schedule["manifest_sha256"]).encode()).hexdigest()
    folder = (
        data_dir / "processed" / "team_injury_development" / f"{source_hash[:12]}-{input_hash[:12]}"
    )
    path = folder / "manifest.json"
    if path.exists():
        report = load_development_audit(path, data_dir)
    else:
        write_json(
            folder / "protocol.json",
            {
                "source_sha256": source_hash,
                "raw_manifest_sha256": raw_hash,
                "policy": POLICY,
                "recorded_at_utc": datetime.now(UTC).isoformat(),
            },
        )
        weeks = load_development(raw, games)
        frame = development_records(games, weeks, raw.parent)
        if development_source_hash() != source_hash or sha256_file(raw) != raw_hash:
            raise DataQualityError("Development injury source changed during audit")
        report = {
            "format_version": 1,
            "source_sha256": source_hash,
            "schedule": schedule,
            "raw_manifest_sha256": raw_hash,
            "policy": POLICY,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "games": games.height,
            "records": frame.height,
            "coverage": _summarize(frame, ["season", "horizon"]),
            "weekly_coverage": _summarize(frame, ["season", "week", "horizon"]),
            **_diagnostics(frame),
            "model_fits_performed": 0,
            "enriched_replay_ready": False,
            "matched_context_comparison_ready": False,
            "artifact": store_frame(folder, "cutoffs", frame),
        }
        write_json(path, report)
    load_development_audit(path, data_dir)
    write_json(report_dir / "results.json", report)
    pl.DataFrame(report["coverage"]).write_csv(report_dir / "coverage.csv")
    return report
