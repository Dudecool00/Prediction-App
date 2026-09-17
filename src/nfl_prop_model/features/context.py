"""Schedule context and earlier opponent passing totals from verified cached sources."""

from typing import Any

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys

SCHEDULE_FEATURES = (
    "is_designated_home",
    "is_neutral_site",
    "team_rest_days",
    "opponent_rest_days",
)
OPPONENT_FEATURES = ("opp_gross_pass_yards_allowed_mean5", "opp_pass_attempts_faced_mean5")


def add_context_features(
    table: pl.DataFrame, stats: pl.DataFrame, schedules: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Opponent totals include all recorded passers, not just QBs in the target table."""
    if set(stats["season"].unique().to_list()) - {2022, 2023, 2024}:
        raise DataQualityError("Context sources must exclude reserved seasons")
    require_keys(table, ["player_id", "game_id"], "context QB input")
    game_times = table.select("game_id", "season", "kickoff_utc", "prediction_time_utc").unique()
    require_keys(game_times, ["game_id"], "context game times")
    selected = schedules.select("game_id", "home_team", "away_team", "location")
    require_keys(selected, ["game_id"], "context schedules")
    if game_times.join(selected, on="game_id", how="anti").height:
        raise DataQualityError("Context schedule coverage is incomplete")
    games = game_times.join(selected, on="game_id", validate="1:1")
    if games.filter(
        ~pl.col("location").is_in(["Home", "Neutral"]) | pl.col("location").is_null()
    ).height:
        raise DataQualityError("Unknown schedule location")
    sides = pl.concat(
        [
            games.select(
                "game_id",
                "season",
                "kickoff_utc",
                "prediction_time_utc",
                pl.col(f"{side}_team").alias("team"),
                pl.col(f"{other}_team").alias("opponent_team"),
                (pl.col("location") == "Neutral").alias("is_neutral_site"),
            )
            for side, other in (("home", "away"), ("away", "home"))
        ]
    ).sort("team", "kickoff_utc")
    require_keys(sides, ["game_id", "team"], "team games")
    totals = (
        stats.filter(pl.col("season_type") == "REG")
        .group_by("game_id", "team")
        .agg(
            pl.col("passing_yards").sum().alias("gross_pass_yards"),
            pl.col("attempts").sum().alias("pass_attempts"),
            pl.col("passing_yards").count().alias("yard_records"),
            pl.col("attempts").count().alias("attempt_records"),
        )
    )
    if sides.join(totals, on=["game_id", "team"], how="anti").height:
        raise DataQualityError("Missing team passing totals; do not fill with zero")
    totals = sides.join(totals, on=["game_id", "team"], validate="1:1")
    if totals.filter((pl.col("yard_records") == 0) | (pl.col("attempt_records") == 0)).height:
        raise DataQualityError("All passing totals missing for a team-game")
    defense = totals.select(
        "game_id",
        pl.col("opponent_team").alias("team"),
        pl.col("gross_pass_yards").alias("allowed_yards"),
        pl.col("pass_attempts").alias("faced_attempts"),
    )
    team_history = (
        totals.join(defense, on=["game_id", "team"], validate="1:1")
        .sort("team", "kickoff_utc")
        .with_columns(
            (
                (
                    pl.col("kickoff_utc") - pl.col("kickoff_utc").shift(1).over("team", "season")
                ).dt.total_seconds()
                / 86400
            ).alias("team_rest_days"),
            pl.col("allowed_yards")
            .shift(1)
            .rolling_mean(5, min_samples=1)
            .over("team")
            .alias(OPPONENT_FEATURES[0]),
            pl.col("faced_attempts")
            .shift(1)
            .rolling_mean(5, min_samples=1)
            .over("team")
            .alias(OPPONENT_FEATURES[1]),
            (pl.col("kickoff_utc").shift(1).over("team") + pl.duration(hours=24)).alias(
                "_opponent_history_available"
            ),
        )
    )
    if team_history.filter(
        pl.col("_opponent_history_available") >= pl.col("prediction_time_utc")
    ).height:
        raise DataQualityError("Opponent history was not available before prediction")
    own = team_history.select("game_id", "team", "team_rest_days", "is_neutral_site")
    opponent = team_history.select(
        "game_id",
        pl.col("team").alias("opponent_team"),
        pl.col("team_rest_days").alias("opponent_rest_days"),
        *OPPONENT_FEATURES,
    )
    if table.join(opponent, on=["game_id", "opponent_team"], how="anti").height:
        raise DataQualityError("Opponent context coverage is incomplete")
    result = (
        table.join(own, on=["game_id", "team"], how="left", validate="m:1")
        .join(opponent, on=["game_id", "opponent_team"], how="left", validate="m:1")
        .sort("kickoff_utc", "game_id", "player_id")
    )
    if result.height != table.height or result["is_neutral_site"].null_count():
        raise DataQualityError("Context join changed the cohort or missed a matchup")
    return result, {
        "rows": result.height,
        "team_games": team_history.height,
        "features": list(SCHEDULE_FEATURES + OPPONENT_FEATURES),
        "nulls": {
            name: result[name].null_count() for name in SCHEDULE_FEATURES + OPPONENT_FEATURES
        },
        "opponent_definition": (
            "Prior five regular-season team games; all passers; gross yards before sack subtraction"
        ),
        "rest_definition": "Elapsed days since team's previous regular-season game in this season",
    }
