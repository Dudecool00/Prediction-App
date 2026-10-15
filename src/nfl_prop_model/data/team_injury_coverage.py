"""Outcome-free coverage of preserved weekly injury editions at forecast cutoffs."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_depth import SCHEDULE_FIELDS, load_schedule_only
from nfl_prop_model.data.team_injury_archive import _gunzip, load_injury_pilot
from nfl_prop_model.data.team_injury_history import (
    POLICY,
    capture_time,
    history_path,
    load_history,
    parse_weekly_article,
    week_cutoffs,
)
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

GROUPS = ("QB", "OL", "skill", "defense", "special")


def _prior_entities(data_dir: Path) -> tuple[dict[tuple[str, str, str], bytes], str | None]:
    path = data_dir / "raw" / "team_availability" / "injury_week1_2025" / "manifest.json"
    if not path.exists():
        return {}, None
    load_injury_pilot(path)
    entities = {}
    for record in read_json(path)["captures"]:
        stamp = (
            datetime.fromisoformat(record["captured_at_utc"])
            .astimezone(UTC)
            .strftime("%Y%m%d%H%M%S")
        )
        entities[(stamp, record["cdx_sha1_base32"], record["original_url"])] = _gunzip(
            (path.parent / record["file"]).read_bytes()
        )
    return entities, sha256_file(path)


def coverage_source_hash() -> str:
    base = Path(__file__).parent
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + (base / "team_injury_history.py").read_bytes()
        + (base / "team_injury_archive.py").read_bytes()
        + (base / "team_depth.py").read_bytes()
        + (base.parent / "features" / "team_epa.py").read_bytes()
    ).hexdigest()


def coverage_records(
    games: pl.DataFrame,
    weeks: dict[int, dict[str, Any]],
    directory: Path,
    prior_entities: dict[tuple[str, str, str], bytes] | None = None,
) -> pl.DataFrame:
    if set(games.columns) != set(SCHEDULE_FIELDS) or set(games["season"]) != {2025}:
        raise DataQualityError("Injury coverage accepts only 2025 schedule metadata, no outcomes")
    require_keys(games, ["game_id"], "injury coverage games")
    cutoffs = week_cutoffs(games)
    parsed: dict[tuple[int, str], dict[str, Any]] = {}
    for week, source in weeks.items():
        for capture in source["captures"]:
            key = (week, capture["timestamp"])
            prior = (prior_entities or {}).get(
                (capture["timestamp"], capture["digest"], capture["original"])
            )
            if capture["status"] != "verified" and prior is None:
                parsed[key] = {"reason": "unverified_edition"}
                continue
            origin = "collection" if capture["status"] == "verified" else "prior_pilot"
            body = (
                _gunzip((directory / f"week-{week:02}" / capture["file"]).read_bytes())
                if origin == "collection"
                else prior
            )
            assert body is not None
            if hashlib.sha1(body).digest() != base64.b32decode(capture["digest"]):
                raise DataQualityError("Reused entity does not match selected archive edition")
            try:
                teams = parse_weekly_article(body, week, capture_time(capture), cutoffs[week][0])
                expected = set(
                    games.filter(pl.col("week") == week).select("home_team").to_series()
                ) | set(games.filter(pl.col("week") == week).select("away_team").to_series())
                if not set(teams) <= expected:
                    raise DataQualityError("Edition team is outside this week's schedule")
                parsed[key] = {"teams": teams, "body_origin": origin}
            except (OSError, ValueError, KeyError) as error:
                parsed[key] = {"reason": "unsupported_edition", "detail": str(error)}
    early = weekly_cutoffs(games)
    rows = []
    for game in games.to_dicts():
        week = game["week"]
        for horizon in HORIZONS:
            cutoff = (
                early[(2025, week)]
                if horizon == "early_week"
                else game["kickoff_utc"] - timedelta(hours=1)
            )
            if cutoff >= game["kickoff_utc"]:
                raise DataQualityError("Injury coverage cutoff must precede kickoff")
            eligible = [r for r in weeks[week]["captures"] if capture_time(r) < cutoff]
            receipt = max(eligible, key=lambda r: r["timestamp"]) if eligible else None
            for side in ("home", "away"):
                team = game[f"{side}_team"]
                reason = "no_earlier_edition"
                fields: dict[str, Any] = {}
                detail = None
                record_origin = None
                if receipt:
                    decoded = parsed[(week, receipt["timestamp"])]
                    record_origin = decoded.get("body_origin")
                    if (cutoff - capture_time(receipt)) > timedelta(hours=72):
                        reason = "stale_edition"
                    elif "reason" in decoded:
                        reason, detail = decoded["reason"], decoded.get("detail")
                    elif team not in decoded["teams"]:
                        reason = "team_section_missing"
                    else:
                        fields = decoded["teams"][team]
                        reason = ""
                rows.append(
                    {
                        "game_id": game["game_id"],
                        "week": week,
                        "team": team,
                        "side": side,
                        "horizon": horizon,
                        "cutoff_utc": cutoff,
                        "status": fields.get("status", "unavailable"),
                        "reason": reason or None,
                        "detail": detail,
                        "body_origin": record_origin,
                        "archive_captured_at_utc": capture_time(receipt) if receipt else None,
                        "archive_url": receipt["archive_url"] if receipt else None,
                        "out_count": fields.get("out_count"),
                        "unknown_position_out_players": fields.get("unknown_position_out_players"),
                        **{name + "_out_count": fields.get(name + "_out_count") for name in GROUPS},
                    }
                )
    frame = pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.col("reason", "detail", "archive_url", "body_origin").cast(pl.String),
        pl.col("archive_captured_at_utc").cast(pl.Datetime("us", "UTC")),
        pl.col(
            "out_count", "unknown_position_out_players", *[g + "_out_count" for g in GROUPS]
        ).cast(pl.Int64),
    )
    require_keys(frame, ["game_id", "side", "horizon"], "weekly injury cutoff records")
    if (
        frame.height != games.height * 4
        or frame.filter(pl.col("archive_captured_at_utc") >= pl.col("cutoff_utc")).height
    ):
        raise DataQualityError("Weekly injury cohort or source cutoff mismatch")
    return frame


def summarize(frame: pl.DataFrame) -> list[dict[str, Any]]:
    result = []
    for (week, horizon), group in frame.partition_by("week", "horizon", as_dict=True).items():
        result.append(
            {
                "week": week,
                "horizon": horizon,
                "team_slots": group.height,
                **{
                    status: group.filter(pl.col("status") == status).height
                    for status in (
                        "designations",
                        "no_designations",
                        "partial_or_pending",
                        "unavailable",
                    )
                },
                "unknown_position_out_slots": group.filter(
                    pl.col("unknown_position_out_players") > 0
                ).height,
            }
        )
    return sorted(result, key=lambda row: (row["week"], row["horizon"]))


def _diagnostics(frame: pl.DataFrame) -> dict[str, Any]:
    return {
        "missing_reasons": frame.filter(pl.col("status") == "unavailable")
        .group_by("horizon", "reason")
        .len()
        .sort("horizon", "reason")
        .to_dicts(),
        "unsupported_editions": frame.filter(pl.col("detail").is_not_null())
        .select("week", "archive_url", "detail")
        .unique()
        .sort("week", "archive_url")
        .to_dicts(),
    }


def load_coverage(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if report["source_sha256"] != coverage_source_hash() or report["policy"] != POLICY:
        raise DataQualityError("Weekly injury coverage code/policy changed")
    games, schedule = load_schedule_only(data_dir)
    raw_path = history_path(data_dir, games)
    prior_entities, pilot_hash = _prior_entities(data_dir)
    if schedule != report["schedule"] or sha256_file(raw_path) != report["raw_manifest_sha256"]:
        raise DataQualityError("Weekly injury coverage source inputs changed")
    if pilot_hash != report["prior_pilot_manifest_sha256"]:
        raise DataQualityError("Prior verified injury pilot changed")
    weeks = load_history(raw_path, games)
    frame = load_frame(path.parent, report["artifact"])
    expected = coverage_records(games, weeks, raw_path.parent, prior_entities)
    if (
        not frame.equals(expected, null_equal=True)
        or summarize(frame) != report["coverage"]
        or any(report[key] != value for key, value in _diagnostics(frame).items())
        or report["games"] != games.height
        or report["records"] != frame.height
        or report["2025_fits_performed"] != 0
        or report["enriched_replay_ready"] is not False
    ):
        raise DataQualityError("Weekly injury coverage differs from source cutoffs")
    return report


def save_coverage(data_dir: Path, report_dir: Path) -> dict[str, Any]:
    games, schedule = load_schedule_only(data_dir)
    raw_path = history_path(data_dir, games)
    source_hash, raw_hash = coverage_source_hash(), sha256_file(raw_path)
    prior_entities, pilot_hash = _prior_entities(data_dir)
    input_hash = hashlib.sha256(
        (raw_hash + str(pilot_hash) + schedule["manifest_sha256"]).encode()
    ).hexdigest()
    directory = (
        data_dir / "processed" / "team_injury_coverage" / f"{source_hash[:12]}-{input_hash[:12]}"
    )
    path = directory / "manifest.json"
    if path.exists():
        report = load_coverage(path, data_dir)
    else:
        write_json(
            directory / "protocol.json",
            {
                "recorded_at_utc": datetime.now(UTC).isoformat(),
                "source_sha256": source_hash,
                "raw_manifest_sha256": raw_hash,
                "policy": POLICY,
            },
        )
        weeks = load_history(raw_path, games)
        frame = coverage_records(games, weeks, raw_path.parent, prior_entities)
        if coverage_source_hash() != source_hash or sha256_file(raw_path) != raw_hash:
            raise DataQualityError("Weekly injury source changed during audit")
        report = {
            "format_version": 1,
            "source_sha256": source_hash,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "policy": POLICY,
            "schedule": schedule,
            "raw_manifest_sha256": raw_hash,
            "prior_pilot_manifest_sha256": pilot_hash,
            "games": games.height,
            "records": frame.height,
            "coverage": summarize(frame),
            **_diagnostics(frame),
            "2025_fits_performed": 0,
            "enriched_replay_ready": False,
            "artifact": store_frame(directory, "cutoffs", frame),
        }
        write_json(path, report)
    load_coverage(path, data_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "results.json", report)
    pl.DataFrame(report["coverage"]).write_csv(report_dir / "coverage.csv")
    return report
