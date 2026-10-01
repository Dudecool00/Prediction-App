"""Current-season QB candidates with source freshness; never a starter confirmation."""

from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import polars as pl

from nfl_prop_model.data.current_qbs import NFL_TEAMS, SOURCE, UPDATER, latest_quarterbacks
from nfl_prop_model.data.schemas import DataQualityError, require_columns, require_keys
from nfl_prop_model.data.storage import load_frame, read_json, store_frame, write_json

SEASON = 2026
SCHEDULE_SOURCE = "https://github.com/nflverse/nfldata/blob/master/data/games.csv"
CACHE_MAX_AGE = timedelta(hours=24)
CHART_MAX_AGE = timedelta(hours=48)


def utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a time zone")
    return value.astimezone(UTC)


def schedule_games(schedules: pl.DataFrame) -> pl.DataFrame:
    """Discard outcome/starter/market fields after deriving a conservative score-presence flag."""
    require_columns(
        schedules,
        "upcoming schedules",
        ("game_id", "game_type", "gameday", "gametime", "home_team", "away_team"),
        ("season", "week", "home_score", "away_score"),
    )
    require_keys(schedules, ["game_id"], "upcoming schedules")
    if schedules.is_empty() or schedules["season"].null_count():
        raise DataQualityError("Upcoming schedules require a nonempty 2026 season")
    if set(schedules["season"].unique().to_list()) != {SEASON}:
        raise DataQualityError(
            "Upcoming schedules must contain only 2026; do not load holdout data"
        )
    regular = schedules.filter(pl.col("game_type") == "REG")
    if regular.filter(
        pl.col("home_team").is_null()
        | pl.col("away_team").is_null()
        | (pl.col("home_team") == pl.col("away_team"))
        | pl.col("week").is_null()
    ).height:
        raise DataQualityError("Upcoming schedules contain an invalid matchup or week")
    return regular.select(
        "game_id",
        "season",
        "week",
        "home_team",
        "away_team",
        pl.concat_str("gameday", "gametime", separator=" ")
        .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
        .dt.replace_time_zone("America/New_York", ambiguous="null", non_existent="null")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc"),
        (pl.col("home_score").is_not_null() | pl.col("away_score").is_not_null()).alias(
            "has_score"
        ),
    )


def fetch_upcoming(data_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    directory = data_dir / "raw" / "upcoming_2026"
    if (directory / "manifest.json").exists() and not refresh:
        return read_json(directory / "manifest.json")
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    update_config(cache_mode="memory")
    nfl.clear_cache()
    schedules = nfl.load_schedules([SEASON])
    depth = nfl.load_depth_charts([SEASON])
    retrieved = datetime.now(UTC)
    schedule_games(schedules)
    latest_quarterbacks(depth, retrieved)
    manifest = {
        "format_version": 1,
        "season": SEASON,
        "retrieved_at_utc": retrieved.isoformat(),
        "nflreadpy": version("nflreadpy"),
        "datasets": {
            "schedules": {
                **store_frame(directory, "schedules", schedules),
                "source": SCHEDULE_SOURCE,
                "loader": "nflreadpy.load_schedules([2026])",
            },
            "depth_charts": {
                **store_frame(directory, "depth_charts", depth),
                "source": SOURCE,
                "upstream_loader": UPDATER,
                "loader": "nflreadpy.load_depth_charts([2026])",
            },
        },
        "license": "CC-BY-4.0 (nflverse distribution)",
    }
    write_json(directory / f"manifest-{retrieved.strftime('%Y%m%dT%H%M%S%fZ')}.json", manifest)
    write_json(directory / "manifest.json", manifest)
    return manifest


def build_upcoming_report(
    schedules: pl.DataFrame,
    depth: pl.DataFrame,
    manifest: dict[str, Any],
    *,
    as_of: datetime,
    days: int = 14,
    expected_teams: set[str] = NFL_TEAMS,
    history: pl.DataFrame | None = None,
) -> dict[str, Any]:
    as_of = utc_time(as_of)
    retrieved = utc_time(datetime.fromisoformat(manifest["retrieved_at_utc"]))
    if retrieved > as_of:
        raise ValueError(
            "A cache retrieved after the requested time cannot establish past knowledge"
        )
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 28:
        raise ValueError("Upcoming horizon must be 1–28 days")
    if manifest.get("format_version") != 1 or manifest.get("season") != SEASON:
        raise ValueError("Unsupported upcoming cache; run nfl-prop upcoming --refresh")
    games = schedule_games(schedules)
    teams = set(games["home_team"].to_list() + games["away_team"].to_list())
    if not teams <= expected_teams:
        raise DataQualityError("Unknown schedule team; resolve its identity before matching charts")
    current = latest_quarterbacks(depth, retrieved, expected_teams=expected_teams)
    if current.filter(pl.col("pos_rank") != pl.col("pos_rank").floor()).height:
        raise DataQualityError("Depth-chart ranks must be whole numbers")
    if current["player_name"].null_count():
        raise DataQualityError("Current QB chart has missing player names")
    upcoming = games.filter(
        ~pl.col("has_score")
        & (pl.col("kickoff_utc") > as_of)
        & (pl.col("kickoff_utc") <= as_of + timedelta(days=days))
    ).sort("kickoff_utc", "game_id")
    history_by_id: dict[str, dict[str, Any]] = {}
    if history is not None:
        if set(history["season"].unique().to_list()) - {2022, 2023, 2024}:
            raise DataQualityError(
                "History reference must use only the 2022–2024 development sample"
            )
        history_by_id = {
            row["player_id"]: row
            for row in history.group_by("player_id")
            .agg(pl.len().alias("games"), pl.col("kickoff_utc").max().alias("last_kickoff"))
            .iter_rows(named=True)
        }
    cache_stale = as_of - retrieved > CACHE_MAX_AGE
    candidates = []
    for game in upcoming.iter_rows(named=True):
        for side, other in (("home", "away"), ("away", "home")):
            quarterbacks = current.filter(pl.col("team") == game[f"{side}_team"])
            top_rank_count = quarterbacks.filter(pl.col("pos_rank") == 1).height
            for qb in quarterbacks.iter_rows(named=True):
                chart_time = utc_time(datetime.fromisoformat(qb["source_snapshot_utc"]))
                chart_stale = as_of - chart_time > CHART_MAX_AGE
                prior = history_by_id.get(qb["gsis_id"])
                if qb["gsis_id"] is None:
                    history_status = "missing_gsis_mapping"
                elif history is None:
                    history_status = "history_cache_unavailable"
                elif prior is None:
                    history_status = "no_development_history"
                else:
                    history_status = "matched_development_history"
                issues = ["starter_and_injury_status_unverified", "production_model_unavailable"]
                if cache_stale:
                    issues.append("source_cache_older_than_24h")
                if chart_stale:
                    issues.append("team_chart_older_than_48h")
                if top_rank_count != 1:
                    issues.append("ambiguous_top_depth_rank")
                if history_status != "matched_development_history":
                    issues.append(history_status)
                candidates.append(
                    {
                        "game_id": game["game_id"],
                        "season": SEASON,
                        "week": game["week"],
                        "kickoff_utc": game["kickoff_utc"].isoformat(),
                        "team": qb["team"],
                        "opponent_team": game[f"{other}_team"],
                        "designated_home": side == "home",
                        "player_name": qb["player_name"],
                        "espn_id": qb["espn_id"],
                        "gsis_id": qb["gsis_id"],
                        "depth_rank": qb["pos_rank"],
                        "source_snapshot_utc": qb["source_snapshot_utc"],
                        "chart_age_hours": (as_of - chart_time).total_seconds() / 3600,
                        "sources_fresh": not cache_stale and not chart_stale,
                        "starter_confirmed": False,
                        "forecast_available": False,
                        "history_status": history_status,
                        "development_games": prior["games"] if prior else None,
                        "last_development_game_utc": (
                            prior["last_kickoff"].isoformat() if prior else None
                        ),
                        "review_reasons": issues,
                    }
                )
    return {
        "format_version": 1,
        "scope": "upcoming_qb_readiness_only",
        "as_of_utc": as_of.isoformat(),
        "horizon_days": days,
        "sources": manifest,
        "cache_age_hours": (as_of - retrieved).total_seconds() / 3600,
        "freshness_policy": {"cache_max_hours": 24, "team_chart_max_hours": 48},
        "counts": {
            "regular_schedule_games": games.height,
            "score_present_games": games.filter(pl.col("has_score")).height,
            "unknown_kickoff_games": games["kickoff_utc"].null_count(),
            "past_or_started_without_score": games.filter(
                ~pl.col("has_score") & (pl.col("kickoff_utc") <= as_of)
            ).height,
            "upcoming_games": upcoming.height,
            "current_qbs": current.height,
            "candidate_rows": len(candidates),
            "fresh_candidate_rows": sum(row["sources_fresh"] for row in candidates),
        },
        "unknown_kickoff_game_ids": games.filter(pl.col("kickoff_utc").is_null())[
            "game_id"
        ].to_list(),
        "candidates": candidates,
        "limitations": [
            "Depth rank is not confirmation of starting, active status, or participation.",
            "Freshness thresholds are application policies, not guarantees of source accuracy.",
            "Future scheduled time and absence of scores do not confirm a game will proceed; "
            "postponements and last-minute changes require a separate status check.",
            "2022–2024 history is a reference count, not current form. "
            "No 2025 targets or current-season player outcomes are loaded by this command.",
            "Missing current chart membership does not establish retirement. "
            "Historical rows remain unchanged; newcomers and unmapped QBs stay visible.",
            "No upcoming prediction, probability, or EV is generated. "
            "The frozen model and current feature pipeline remain unfinished.",
        ],
    }


def load_upcoming_report(
    data_dir: Path, *, as_of: datetime | None = None, days: int = 14
) -> dict[str, Any]:
    """Read verified local files only; calculate freshness against the current clock."""
    directory = data_dir / "raw" / "upcoming_2026"
    manifest = read_json(directory / "manifest.json")
    frames = {
        key: load_frame(directory, manifest["datasets"][key])
        for key in ("schedules", "depth_charts")
    }
    historical_dir = data_dir / "processed" / "2022_2023_2024"
    history = None
    history_source = None
    if (historical_dir / "manifest.json").exists():
        historical_manifest = read_json(historical_dir / "manifest.json")
        history_source = historical_manifest["table"]
        history = load_frame(historical_dir, history_source)
    report = build_upcoming_report(
        frames["schedules"],
        frames["depth_charts"],
        manifest,
        as_of=as_of or datetime.now(UTC),
        days=days,
        history=history,
    )
    report["development_history_source"] = history_source
    return report


def write_upcoming_report(report_dir: Path, report: dict[str, Any]) -> None:
    write_json(report_dir / "upcoming.json", report)
    counts = report["counts"]
    lines = [
        "# Upcoming QB readiness · 2026",
        "",
        f"As of {report['as_of_utc']}; next {report['horizon_days']} days.",
        f"Cache retrieved {report['sources']['retrieved_at_utc']}.",
        "",
        f"{counts['upcoming_games']} games, {counts['candidate_rows']} QB/game candidates; "
        f"{counts['fresh_candidate_rows']} have fresh sources under the "
        "24h cache / 48h chart policy.",
        f"{counts['unknown_kickoff_games']} regular-season games have an unknown kickoff and "
        "are excluded from time-based selection; their IDs are retained in JSON.",
        "",
        "Depth-chart candidates only. No confirmed starters or upcoming forecasts.",
        "",
        "| Game | Team | Player | Depth rank | Sources fresh | Development history |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report["candidates"]:
        lines.append(
            f"| {row['game_id']} | {row['team']} | {row['player_name']} | {row['depth_rank']} | "
            f"{'Yes' if row['sources_fresh'] else 'Refresh needed'} | {row['history_status']} |"
        )
    lines += ["", "## Limits", "", *[f"- {item}" for item in report["limitations"]], ""]
    lines += [
        "## Sources",
        "",
        f"[nflverse schedules]({SCHEDULE_SOURCE}); [depth charts]({SOURCE}); "
        f"[ESPN updater]({UPDATER}). ESPN and nflverse contributors; "
        "nflverse distribution CC-BY-4.0. Times converted from Eastern to UTC; "
        "team snapshots filtered and joined to upcoming games. See JSON for source hashes.",
        "",
    ]
    (report_dir / "upcoming.md").write_text("\n".join(lines), encoding="utf-8")
