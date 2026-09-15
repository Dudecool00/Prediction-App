"""One row per recorded regular-season QB-game; no opportunity threshold."""

from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys, validate_sources


def build_target_table(
    stats: pl.DataFrame, schedules: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    validate_sources(stats, schedules)
    qbs = stats.filter(pl.col("position") == "QB")
    regular = qbs.filter(pl.col("season_type") == "REG")
    if regular.is_empty():
        raise DataQualityError("No regular-season quarterback rows in the requested data.")
    selected = regular.select(
        "player_id",
        "player_display_name",
        "game_id",
        "season",
        "week",
        "team",
        "opponent_team",
        "passing_yards",
        "attempts",
        "completions",
    )
    # Explicit selection prevents scores, market lines, and weather observations
    # from accidentally becoming predictors. Scores are used only for completion.
    schedule = schedules.select(
        "game_id",
        pl.col("season").alias("schedule_season"),
        pl.col("week").alias("schedule_week"),
        "game_type",
        "gameday",
        "gametime",
        "home_team",
        "away_team",
        "home_qb_id",
        "away_qb_id",
        (pl.col("home_score").is_not_null() & pl.col("away_score").is_not_null()).alias(
            "game_completed"
        ),
    )
    if selected.join(schedule, on="game_id", how="anti").height:
        raise DataQualityError("Quarterback rows have game IDs missing from the schedules.")
    joined = selected.join(schedule, on="game_id", how="left", validate="m:1")
    inconsistent = (
        (pl.col("season") != pl.col("schedule_season"))
        | (pl.col("week") != pl.col("schedule_week"))
        | (pl.col("game_type") != "REG")
        | ~(
            (
                (pl.col("team") == pl.col("home_team"))
                & (pl.col("opponent_team") == pl.col("away_team"))
            )
            | (
                (pl.col("team") == pl.col("away_team"))
                & (pl.col("opponent_team") == pl.col("home_team"))
            )
        )
    )
    if joined.filter(inconsistent.fill_null(True)).height:
        raise DataQualityError("Player/schedule season, week, type, or matchup mismatch.")
    completed = joined.filter(pl.col("game_completed"))
    usable = completed.filter(pl.col("passing_yards").is_not_null())
    if usable.is_empty():
        raise DataQualityError("No completed regular-season QB games with a known target.")
    if usable.filter(
        (pl.col("attempts") < 0)
        | (pl.col("completions") < 0)
        | (pl.col("completions") > pl.col("attempts"))
    ).height:
        raise DataQualityError("Invalid attempts/completions in quarterback statistics.")

    # nflverse times are Eastern, including international/neutral games.
    # strict=False lets us report missing/malformed kickoff times explicitly.
    usable = usable.with_columns(
        pl.concat_str(["gameday", "gametime"], separator=" ")
        .str.to_datetime("%Y-%m-%d %H:%M", strict=False)
        .dt.replace_time_zone("America/New_York")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc")
    )
    if usable["kickoff_utc"].null_count():
        raise DataQualityError("Missing or malformed kickoff timestamps; no dates were guessed.")
    table = usable.select(
        "player_id",
        "player_display_name",
        "game_id",
        "season",
        "week",
        "team",
        "opponent_team",
        "kickoff_utc",
        (pl.col("kickoff_utc") - pl.duration(hours=1)).alias("prediction_time_utc"),
        pl.col("passing_yards").cast(pl.Float64).alias("target_passing_yards"),
        pl.col("attempts").cast(pl.Float64).alias("observed_attempts"),
        pl.col("completions").cast(pl.Float64).alias("observed_completions"),
        (pl.col("team") == pl.col("home_team")).alias("is_designated_home"),
        (
            pl.col("player_id")
            == pl.when(pl.col("team") == pl.col("home_team"))
            .then(pl.col("home_qb_id"))
            .otherwise(pl.col("away_qb_id"))
        ).alias("schedule_reported_starter"),
    ).sort("kickoff_utc", "game_id", "player_id")
    require_keys(table, ["player_id", "game_id"], "quarterback_games")
    require_keys(table, ["player_id", "kickoff_utc"], "quarterback_kickoffs")

    completed_schedule = schedule.filter((pl.col("game_type") == "REG") & pl.col("game_completed"))
    starters = pl.concat(
        [
            completed_schedule.select(
                "game_id",
                pl.col(f"{side}_qb_id").alias("player_id"),
                pl.col(f"{side}_team").alias("team"),
            )
            for side in ("home", "away")
        ]
    )
    missing_starters = starters.filter(pl.col("player_id").is_not_null()).join(
        table.select("game_id", "player_id"), on=["game_id", "player_id"], how="anti"
    )
    counts: dict[str, Any] = {
        "source_player_rows": stats.height,
        "source_schedule_rows": schedules.height,
        "excluded_non_qb_or_unknown_position": stats.height - qbs.height,
        "excluded_qb_non_regular_season": qbs.height - regular.height,
        "excluded_qb_uncompleted_games": joined.height - completed.height,
        "excluded_qb_missing_target": completed.height - usable.height,
        "included_qb_games": table.height,
        "included_unique_qbs": table["player_id"].n_unique(),
        "included_zero_attempt_rows": table.filter(pl.col("observed_attempts") == 0).height,
        "included_recorded_nonstarters": table.filter(~pl.col("schedule_reported_starter")).height,
        "included_unknown_starter_status": table["schedule_reported_starter"].null_count(),
        "source_player_duplicate_key_rows": stats.select("player_id", "game_id")
        .is_duplicated()
        .sum(),
        "source_player_null_key_rows": stats.filter(
            pl.any_horizontal(pl.col("player_id", "game_id").is_null())
        ).height,
        "source_unknown_position_rows": stats["position"].null_count(),
        "source_qb_duplicate_keys": 0,
        "source_schedule_duplicate_keys": 0,
        "unmatched_qb_game_ids": 0,
        "completed_regular_schedule_games": completed_schedule.height,
        "unknown_scheduled_starter_slots": starters["player_id"].null_count(),
        "scheduled_starters_without_target_rows": missing_starters.to_dicts(),
    }
    return table, counts
