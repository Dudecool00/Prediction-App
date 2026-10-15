"""Offline coverage of dated news reports; shared formal definition, no model fitting."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.team_injury_archive import _gunzip
from nfl_prop_model.data.team_injury_development import development_cutoffs
from nfl_prop_model.data.team_injury_history import _digest, capture_time, parse_weekly_article
from nfl_prop_model.data.team_injury_news import (
    DEFAULT_NEWS_SEASONS,
    POLICY,
    load_news,
    news_schedule,
)
from nfl_prop_model.data.team_injury_news_variants import (
    load_qualified,
    qualified_path,
    require_canonical,
)
from nfl_prop_model.features.team_epa import HORIZONS, weekly_cutoffs

GROUPS = ("QB", "OL", "skill", "defense", "special")
COUNTS = ("out_count", "unknown_position_out_players", *[g + "_out_count" for g in GROUPS])
FORMAL = ("designations", "no_designations")


def news_source_hash() -> str:
    base = Path(__file__).parent
    paths = [
        Path(__file__),
        base / "team_injury_news.py",
        base / "team_injury_news_variants.py",
        base / "team_injury_development.py",
        base / "team_injury_history.py",
        base / "team_injury_archive.py",
        base / "team_depth.py",
        base / "team_games.py",
        base.parent / "features" / "team_epa.py",
    ]
    return hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()


def news_records(
    games: pl.DataFrame,
    weeks: dict[tuple[int, int], dict[str, Any]],
    directory: Path,
) -> pl.DataFrame:
    cutoffs = development_cutoffs(games)
    if set(weeks) != set(cutoffs):
        raise DataQualityError("News source week cohort mismatch")
    parsed: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    for (season, week), source in weeks.items():
        scheduled = games.filter((pl.col("season") == season) & (pl.col("week") == week))
        expected_teams = set(scheduled["home_team"]) | set(scheduled["away_team"])
        # The canonical initialization cohort excludes the canceled BUF-CIN game.
        if (season, week) == (2022, 17):
            expected_teams |= {"BUF", "CIN"}
        for receipt in source["captures"]:
            key = (season, week, receipt["timestamp"], receipt["original"])
            if receipt["status"] != "verified":
                parsed[key] = {"reason": "unverified_edition"}
                continue
            body = _gunzip(
                (directory / str(season) / f"week-{week:02}" / receipt["file"]).read_bytes()
            )
            if _digest(body) != receipt["digest"]:
                raise DataQualityError("News entity differs from selected archive edition")
            try:
                if receipt.get("article_kind") == "tracking_variant":
                    require_canonical(body, receipt["expected_canonical"])
                teams = parse_weekly_article(
                    body, week, capture_time(receipt), cutoffs[(season, week)][0]
                )
                if not set(teams) <= expected_teams:
                    raise DataQualityError("News team lies outside scheduled NFL week")
                parsed[key] = {"teams": teams}
            except (OSError, ValueError, KeyError) as error:
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
            receipt = (
                max(eligible, key=lambda r: (r["timestamp"], r["original"])) if eligible else None
            )
            latest_stamp = receipt["timestamp"] if receipt else None
            tied = (
                latest_stamp is not None
                and len(
                    {
                        (r["original"], r["digest"])
                        for r in eligible
                        if r["timestamp"] == latest_stamp
                    }
                )
                > 1
            )
            for side in ("home", "away"):
                fields: dict[str, Any] = {}
                reason = "no_earlier_edition" if source["article"] else "week_url_not_in_catalog"
                detail = None
                if receipt:
                    decoded = parsed[(season, week, receipt["timestamp"], receipt["original"])]
                    if tied:
                        reason = "ambiguous_latest_editions"
                    elif cutoff - capture_time(receipt) > timedelta(hours=72):
                        reason = "stale_edition"
                    elif "reason" in decoded:
                        reason, detail = decoded["reason"], decoded.get("detail")
                    else:
                        fields = decoded["teams"].get(game[f"{side}_team"], {})
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
                        "article_kind": receipt.get("article_kind", "canonical")
                        if receipt
                        else None,
                        **{k: fields.get(k) for k in COUNTS},
                    }
                )
    frame = (
        pl.DataFrame(records, infer_schema_length=None)
        .with_columns(
            pl.col("reason", "detail", "archive_url", "article_kind").cast(pl.String),
            pl.col("archive_captured_at_utc").cast(pl.Datetime("us", "UTC")),
            pl.col(*COUNTS).cast(pl.Int64),
        )
        .sort("season", "week", "game_id", "side", "horizon")
    )
    require_keys(frame, ["game_id", "side", "horizon"], "news cutoff records")
    if (
        frame.height != games.height * 4
        or frame.filter(pl.col("archive_captured_at_utc") >= pl.col("cutoff_utc")).height
    ):
        raise DataQualityError("News cohort/source cutoff mismatch")
    return frame


def news_summary(frame: pl.DataFrame, keys: list[str]) -> list[dict[str, Any]]:
    result = []
    for key, group in frame.partition_by(keys, as_dict=True).items():
        formal = pl.col("status").is_in(FORMAL)
        paired = group.group_by("game_id").agg(
            formal.all().alias("both_formal"),
            (formal & pl.col("QB_out_count").is_not_null()).all().alias("both_position_counts"),
        )
        result.append(
            {
                **dict(zip(keys, key, strict=True)),
                "team_slots": group.height,
                **{
                    s: group.filter(pl.col("status") == s).height
                    for s in (*FORMAL, "partial_or_pending", "unavailable")
                },
                "formal_team_reports": group.filter(formal).height,
                "tracking_variant_slots": group.filter(
                    pl.col("article_kind") == "tracking_variant"
                ).height,
                "unknown_out_position_slots": group.filter(
                    pl.col("unknown_position_out_players") > 0
                ).height,
                "games_both_teams_formal": paired["both_formal"].sum(),
                "games_both_teams_position_counts": paired["both_position_counts"].sum(),
            }
        )
    return sorted(result, key=lambda r: tuple(r[k] for k in keys))


def _diagnostics(frame: pl.DataFrame) -> dict[str, Any]:
    return {
        "missing_reasons": frame.filter(pl.col("reason").is_not_null())
        .group_by("season", "horizon", "reason")
        .len()
        .sort("season", "horizon", "reason")
        .to_dicts(),
        "unsupported_editions": frame.filter(pl.col("detail").is_not_null())
        .select("season", "week", "archive_url", "detail")
        .unique()
        .sort("season", "week", "archive_url")
        .to_dicts(),
    }


def load_news_audit(path: Path, data_dir: Path) -> dict[str, Any]:
    report = read_json(path)
    if (
        report["format_version"] != 1
        or report["source_sha256"] != news_source_hash()
        or report["policy"] != POLICY
    ):
        raise DataQualityError("News audit code/policy changed")
    games, schedule = news_schedule(data_dir, tuple(report["seasons"]))
    raw = qualified_path(data_dir, games)
    if report["schedule"] != schedule or report["raw_manifest_sha256"] != sha256_file(raw):
        raise DataQualityError("News audit source inputs changed")
    weeks = load_qualified(raw, games)
    baseline = news_records(games, load_news(raw.with_name("manifest.json"), games), raw.parent)
    expected = news_records(games, weeks, raw.parent)
    frame = load_frame(path.parent, report["artifact"])
    if (
        not frame.equals(expected, null_equal=True)
        or report["coverage"] != news_summary(frame, ["season", "horizon"])
        or report["canonical_coverage"] != news_summary(baseline, ["season", "horizon"])
        or report["weekly_coverage"] != news_summary(frame, ["season", "week", "horizon"])
        or any(report[k] != v for k, v in _diagnostics(frame).items())
        or report["games"] != games.height
        or report["records"] != frame.height
        or report["model_fits_performed"] != 0
        or report["enriched_replay_ready"] is not False
        or report["matched_context_comparison_ready"] is not False
    ):
        raise DataQualityError("News audit differs from preserved source cutoffs")
    return report


def save_news_audit(
    data_dir: Path,
    report_dir: Path,
    seasons: tuple[int, ...] = DEFAULT_NEWS_SEASONS,
) -> dict[str, Any]:
    games, schedule = news_schedule(data_dir, seasons)
    raw = qualified_path(data_dir, games)
    source_hash, raw_hash = news_source_hash(), sha256_file(raw)
    input_hash = hashlib.sha256((raw_hash + schedule["manifest_sha256"]).encode()).hexdigest()
    folder = data_dir / "processed" / "team_injury_news" / f"{source_hash[:12]}-{input_hash[:12]}"
    path = folder / "manifest.json"
    if path.exists():
        report = load_news_audit(path, data_dir)
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
        weeks = load_qualified(raw, games)
        frame = news_records(games, weeks, raw.parent)
        baseline = news_records(games, load_news(raw.with_name("manifest.json"), games), raw.parent)
        if news_source_hash() != source_hash or sha256_file(raw) != raw_hash:
            raise DataQualityError("News source changed during audit")
        report = {
            "format_version": 1,
            "source_sha256": source_hash,
            "seasons": sorted(seasons),
            "schedule": schedule,
            "raw_manifest_sha256": raw_hash,
            "policy": POLICY,
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "games": games.height,
            "records": frame.height,
            "coverage": news_summary(frame, ["season", "horizon"]),
            "canonical_coverage": news_summary(baseline, ["season", "horizon"]),
            "weekly_coverage": news_summary(frame, ["season", "week", "horizon"]),
            **_diagnostics(frame),
            "model_fits_performed": 0,
            "enriched_replay_ready": False,
            "matched_context_comparison_ready": False,
            "artifact": store_frame(folder, "cutoffs", frame),
        }
        write_json(path, report)
    load_news_audit(path, data_dir)
    write_json(report_dir / "results.json", report)
    pl.DataFrame(report["coverage"]).write_csv(report_dir / "coverage.csv")
    return report
