"""Verified current sources, offline feature audits and immutable research snapshots."""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.quarterback_games import build_target_table
from nfl_prop_model.data.schemas import DataQualityError, validate_sources
from nfl_prop_model.data.status_reviews import read_status_reviews
from nfl_prop_model.data.storage import load_frame, read_json, sha256_file, store_frame, write_json
from nfl_prop_model.data.upcoming import (
    CACHE_MAX_AGE,
    build_upcoming_report,
    schedule_games,
    utc_time,
)
from nfl_prop_model.features.prospective import (
    FEATURES,
    HISTORY_COLUMNS,
    candidate_features,
    observed_history,
    validate_history,
)
from nfl_prop_model.modeling.policy import MINIMUM_HISTORY_GAMES


def _time(value: str) -> datetime:
    return utc_time(datetime.fromisoformat(value))


def _frame(directory: Path, metadata: dict[str, Any]) -> pl.DataFrame:
    name = metadata.get("file", "")
    if not name or Path(name).name != name or (directory / name).is_symlink():
        raise DataQualityError("Feature source must be a local nonsymlink file")
    return load_frame(directory, metadata)


def fetch_current_stats(data_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    """Only 2026 is fetched; 2025 is read solely from the completed diagnostic."""
    directory = data_dir / "raw" / "current_stats_2026"
    path = directory / "manifest.json"
    if path.exists() and not refresh:
        return read_json(path)
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="memory")
    nfl.clear_cache()
    stats = nfl.load_player_stats([2026], summary_level="week")
    if stats.is_empty() or set(stats["season"].unique()) != {2026}:
        raise DataQualityError("Current stats must contain exactly season 2026")
    now = datetime.now(UTC)
    manifest = {
        "format_version": 1,
        "season": 2026,
        "retrieved_at_utc": now.isoformat(),
        "nflreadpy": version("nflreadpy"),
        "player_stats": {
            **store_frame(directory, "player_stats", stats),
            "loader": "nflreadpy.load_player_stats([2026], summary_level='week')",
            "source": "https://github.com/nflverse/nflverse-data/releases/tag/stats_player",
            "license": "CC-BY-4.0",
        },
    }
    write_json(directory / f"manifest-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json", manifest)
    write_json(path, manifest)
    return manifest


def load_prior_history(data_dir: Path, *, as_of: datetime) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Require a completed 2025 audit; never download, rescore or import an estimator."""
    as_of = utc_time(as_of)
    development_dir = data_dir / "processed" / "2022_2023_2024"
    development = read_json(development_dir / "manifest.json")
    dev_time = _time(development["raw_manifest"]["retrieved_at_utc"])
    root = data_dir / "holdout" / "2025"
    registry = read_json(root / "access.json")
    digest = registry.get("freeze_sha256", "")
    if registry.get("status") != "complete" or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise DataQualityError("Current features require the completed 2025 diagnostic")
    run = root / f"run-{digest}"
    if sha256_file(run / "manifest.json") != registry["report_sha256"]:
        raise DataQualityError("Completed 2025 report checksum mismatch")
    holdout = read_json(run / "manifest.json")
    if (
        holdout.get("freeze_sha256") != digest
        or holdout.get("status") != "completed_reserved_diagnostic"
    ):
        raise DataQualityError("Completed 2025 report differs from its access record")
    holdout_time = max(
        _time(holdout["inputs"]["retrieved_at_utc"]), _time(holdout["generated_at_utc"])
    )
    if max(dev_time, holdout_time) > as_of:
        raise ValueError("Historical snapshots were retrieved after the requested time")
    dev = _frame(development_dir, development["table"])
    old = _frame(run, holdout["artifacts"]["features"])
    if set(dev["season"].unique()) - {2022, 2023, 2024} or set(old["season"].unique()) != {2025}:
        raise DataQualityError("Historical feature source seasons differ")
    history = pl.concat(
        [observed_history(dev, dev_time), observed_history(old, holdout_time)],
        how="vertical_relaxed",
    )
    validate_history(history)
    return history, {
        "development": {"table": development["table"], "observed_at_utc": dev_time.isoformat()},
        "accessed_2025": {
            "freeze_sha256": digest,
            "report_sha256": registry["report_sha256"],
            "table": holdout["artifacts"]["features"],
            "observed_at_utc": holdout_time.isoformat(),
            "use": "Earlier recorded appearances for prospective lags only; no reevaluation",
        },
    }


def build_feature_report(
    schedules: pl.DataFrame,
    depth: pl.DataFrame,
    stats: pl.DataFrame,
    upcoming_manifest: dict[str, Any],
    stats_manifest: dict[str, Any],
    prior_history: pl.DataFrame,
    *,
    as_of: datetime,
    days: int = 14,
    status_reviews: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    as_of = utc_time(as_of)
    validate_history(prior_history)
    if set(prior_history["season"].unique()) - {2022, 2023, 2024, 2025}:
        raise DataQualityError("Prior source must exclude 2026; use the current stats snapshot")
    if (
        stats_manifest.get("format_version") != 1
        or stats_manifest.get("season") != 2026
        or set(stats["season"].unique()) != {2026}
    ):
        raise DataQualityError("Current feature stats must contain exactly season 2026")
    stats_time = _time(stats_manifest["retrieved_at_utc"])
    schedule_time = _time(upcoming_manifest["retrieved_at_utc"])
    observed_at = max(stats_time, schedule_time)
    if observed_at > as_of:
        raise ValueError("Feature/history sources were retrieved after the requested time")
    validate_sources(stats, schedules)
    report = build_upcoming_report(
        schedules,
        depth,
        upcoming_manifest,
        as_of=as_of,
        days=days,
        history=prior_history.filter(pl.col("season") <= 2024),
        status_reviews=status_reviews,
    )
    games = schedule_games(schedules).join(
        schedules.select("game_id", "home_score", "away_score"), on="game_id", validate="1:1"
    )
    eligible = games.filter(
        pl.col("home_score").is_not_null()
        & pl.col("away_score").is_not_null()
        & (pl.col("kickoff_utc") + pl.duration(hours=24) <= as_of)
    )
    selected_stats = stats.filter(pl.col("game_id").is_in(eligible["game_id"].implode()))
    if selected_stats.filter(
        (pl.col("position") == "QB")
        & (pl.col("season_type") == "REG")
        & pl.col("passing_yards").is_not_null()
    ).height:
        current, exclusions = build_target_table(selected_stats, schedules)
        current = observed_history(current, observed_at)
    else:
        current = prior_history.select(HISTORY_COLUMNS).head(0)
        exclusions = {"included_qb_games": 0}
    history = pl.concat([prior_history.select(HISTORY_COLUMNS), current], how="vertical_relaxed")
    validate_history(history)
    history = history.filter(pl.col("available_at_utc") <= as_of)
    slots = {
        (game["game_id"], game[f"{side}_team"])
        for game in eligible.iter_rows(named=True)
        for side in ("home", "away")
    }
    qb_slots = set(
        selected_stats.filter(
            (pl.col("position") == "QB")
            & (pl.col("season_type") == "REG")
            & pl.col("passing_yards").is_not_null()
        )
        .select("game_id", "team")
        .iter_rows()
    )
    missing_slots = sorted(slots - qb_slots)
    stats_fresh = as_of - stats_time <= CACHE_MAX_AGE
    for row in report["candidates"]:
        row.update(
            candidate_features(row, history, schedules, as_of=as_of, observed_at=observed_at)
        )
        issues = list(row.pop("feature_issues"))
        if row["gsis_id"] is None:
            issues.append("missing_gsis_mapping")
        if not row["sources_fresh"]:
            issues.append("schedule_or_chart_stale")
        if not stats_fresh:
            issues.append("current_stats_cache_older_than_24h")
        if missing_slots:
            issues.append("current_stats_coverage_incomplete")
        if as_of >= _time(row["forecast_cutoff_utc"]):
            issues.append("snapshot_after_forecast_cutoff")
        row["feature_blockers"] = issues
        row["features_ready"] = not issues
        row["history_minimum_met"] = (
            row["features"]["prior_games_in_sample"] >= MINIMUM_HISTORY_GAMES
        )
        evidence_issues = [
            reason
            for reason in row["review_reasons"]
            if reason.startswith(("starter_", "active_"))
            or reason in {"candidate_changed", "simultaneous_reviews", "ambiguous_top_depth_rank"}
        ]
        row["forecast_blockers"] = list(
            dict.fromkeys(
                [
                    *issues,
                    *(
                        []
                        if row["history_minimum_met"]
                        else ["fewer_than_five_available_sample_games"]
                    ),
                    *evidence_issues,
                    "status_source_contents_not_independently_verified",
                    "prospective_participation_cohort_not_validated",
                    "production_model_not_enabled",
                ]
            )
        )
        row["forecast_available"] = False
        row["review_reasons"] = row["forecast_blockers"]
    report.update(
        {
            "format_version": 1,
            "scope": "prospective_feature_audit_only",
            "feature_columns": list(FEATURES),
            "production_enabled": False,
            "current_stats_source": stats_manifest,
            "current_stats_age_hours": (as_of - stats_time).total_seconds() / 3600,
            "history_counts": {
                str(year): history.filter(pl.col("season") == year).height
                for year in range(2022, 2027)
            },
            "current_stats_exclusions": exclusions,
            "missing_current_qb_team_game_slots": [
                {"game_id": game, "team": team} for game, team in missing_slots
            ],
            "availability_policy": {
                "result": "max(kickoff plus 24 hours, source snapshot retrieval)",
                "selection": "Available at snapshot time; no invented future results",
                "forecast_cutoff": "Scheduled kickoff minus one hour; later snapshots are blocked",
                "sample": "2022–2025 saved appearances plus observed 2026 regular QB-games",
            },
            "limitations": [
                "Features only; no model prediction, probability or EV is generated.",
                "Snapshots establish knowledge at/after retrieval, not historical publication.",
                "Kickoff plus 24 hours is an availability assumption; source revisions may occur.",
                "Counts describe sample appearances, not career games, starts or playing time.",
                "Uncompleted/unknown games block rest features; first-season rest is nullable.",
                "Manual status claims retain expiry/conflict checks; "
                "contents are not independently verified.",
                "The appearance cohort does not validate prospective starters. "
                "Forecasts stay disabled.",
            ],
        }
    )
    report["counts"].update(
        {
            "features_ready_rows": sum(row["features_ready"] for row in report["candidates"]),
            "history_minimum_rows": sum(row["history_minimum_met"] for row in report["candidates"]),
            "forecast_available_rows": 0,
        }
    )
    report["blocker_counts"] = dict(
        sorted(
            Counter(
                reason for row in report["candidates"] for reason in row["forecast_blockers"]
            ).items()
        )
    )
    return report


def load_feature_report(
    data_dir: Path, *, as_of: datetime | None = None, days: int = 14
) -> dict[str, Any]:
    as_of = utc_time(as_of or datetime.now(UTC))
    directory = data_dir / "raw" / "upcoming_2026"
    upcoming = read_json(directory / "manifest.json")
    stats_dir = data_dir / "raw" / "current_stats_2026"
    stats = read_json(stats_dir / "manifest.json")
    history, history_sources = load_prior_history(data_dir, as_of=as_of)
    report = build_feature_report(
        _frame(directory, upcoming["datasets"]["schedules"]),
        _frame(directory, upcoming["datasets"]["depth_charts"]),
        _frame(stats_dir, stats["player_stats"]),
        upcoming,
        stats,
        history,
        as_of=as_of,
        days=days,
        status_reviews=read_status_reviews(data_dir / "status_reviews"),
    )
    report["history_sources"] = history_sources
    package = Path(__file__).parents[1]
    report["pipeline_source_hashes"] = {
        path.relative_to(package).as_posix(): sha256_file(path)
        for path in sorted(package.rglob("*.py"))
    }
    return report


def save_feature_snapshot(data_dir: Path, report: dict[str, Any]) -> Path:
    directory = data_dir / "prospective" / f"snapshot-{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "features.json", report)
    write_json(
        directory / "manifest.json",
        {
            "format_version": 1,
            "scope": "prospective_feature_snapshot",
            "as_of_utc": report["as_of_utc"],
            "file": "features.json",
            "sha256": sha256_file(directory / "features.json"),
            "production_enabled": False,
        },
    )
    return directory


def read_feature_snapshot(directory: Path) -> dict[str, Any]:
    manifest = read_json(directory / "manifest.json")
    if (
        manifest.get("format_version") != 1
        or manifest.get("scope") != "prospective_feature_snapshot"
        or manifest.get("file") != "features.json"
    ):
        raise DataQualityError("Unsupported prospective feature snapshot")
    if (directory / "features.json").is_symlink() or sha256_file(
        directory / "features.json"
    ) != manifest["sha256"]:
        raise DataQualityError("Feature snapshot checksum mismatch")
    report = read_json(directory / "features.json")
    if (
        report.get("as_of_utc") != manifest["as_of_utc"]
        or report.get("scope") != "prospective_feature_audit_only"
        or report.get("production_enabled") is not False
    ):
        raise DataQualityError("Feature snapshot scope/time differs from its manifest")
    return report


def write_feature_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "current_features.json", report)
    counts = report["counts"]
    lines = [
        "# Prospective QB feature audit · 2026",
        "",
        f"As of {report['as_of_utc']}; next {report['horizon_days']} days.",
        "",
        f"{counts['upcoming_games']} games / {counts['candidate_rows']} candidates. "
        f"{counts['features_ready_rows']} pass feature checks; "
        f"{counts['history_minimum_rows']} have five available sample games. "
        "Zero forecasts are enabled.",
        "",
        "## Available history",
        "",
        *[
            f"- {season}: {count} recorded QB-games"
            for season, count in report["history_counts"].items()
        ],
        "",
        "## Forecast blockers (candidate rows)",
        "",
        *[f"- {reason}: {count}" for reason, count in report["blocker_counts"].items()],
        "",
        "## Candidates",
        "",
        "| Game | Team | QB | Prior games | 2026 games | Features ready |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    lines += [
        f"| {row['game_id']} | {row['team']} | {row['player_name']} | "
        f"{row['features']['prior_games_in_sample']} | "
        f"{row['features']['prior_season_games_in_sample']} | "
        f"{'Yes' if row['features_ready'] else 'No'} |"
        for row in report["candidates"]
    ]
    lines += [
        "",
        "## Source snapshot fingerprints",
        "",
        f"Schedules/charts retrieved: {report['sources']['retrieved_at_utc']}",
        "",
        f"2026 stats retrieved: {report['current_stats_source']['retrieved_at_utc']}",
        "",
        *[
            f"- {name} SHA-256: `{report['sources']['datasets'][name]['sha256']}`"
            for name in ("schedules", "depth_charts")
        ],
        f"- 2026 stats SHA-256: `{report['current_stats_source']['player_stats']['sha256']}`",
        "",
        "## Limits",
        "",
        *[f"- {item}" for item in report["limitations"]],
        "",
        "Full JSON preserves source hashes/timestamps, feature values/availability, last-five "
        "inputs, current-season IDs, rest gaps, status review context and code hashes.",
        "",
        "[nflreadpy loaders](https://nflreadpy.nflverse.com/api/load_functions/) · "
        "[player stats](https://github.com/nflverse/nflverse-data/releases/tag/stats_player) · "
        "[schedules](https://github.com/nflverse/nfldata/blob/master/data/games.csv). "
        "nflverse distribution CC-BY-4.0; ESPN-derived chart candidates.",
        "",
    ]
    (report_dir / "current_features.md").write_text("\n".join(lines), encoding="utf-8")
