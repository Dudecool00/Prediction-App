"""Shift before rolling: every feature uses earlier recorded QB games only."""

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys

# Later estimators must select this list, never all numeric columns in the table.
FEATURE_COLUMNS = (
    "passing_yards_lag1",
    "passing_yards_mean3",
    "passing_yards_mean5",
    "attempts_mean5",
    "passing_yards_season_mean",
    "prior_games_in_sample",
    "prior_season_games_in_sample",
)


def add_lagged_features(table: pl.DataFrame) -> pl.DataFrame:
    require_keys(table, ["player_id", "game_id"], "feature_input")
    require_keys(table, ["player_id", "kickoff_utc"], "feature_kickoffs")
    ordered = table.sort("player_id", "kickoff_utc", "game_id")
    if ordered["target_passing_yards"].null_count():
        raise DataQualityError("Lagged features require known historical targets.")
    result = (
        ordered.with_columns(
            pl.col("target_passing_yards").shift(1).over("player_id").alias("passing_yards_lag1"),
            *[
                pl.col("target_passing_yards")
                .shift(1)
                .rolling_mean(window, min_samples=1)
                .over("player_id")
                .alias(f"passing_yards_mean{window}")
                for window in (3, 5)
            ],
            pl.col("observed_attempts")
            .shift(1)
            .rolling_mean(5, min_samples=1)
            .over("player_id")
            .alias("attempts_mean5"),
            (pl.col("player_id").cum_count().over("player_id") - 1).alias("prior_games_in_sample"),
            (pl.col("player_id").cum_count().over("player_id", "season") - 1).alias(
                "prior_season_games_in_sample"
            ),
            pl.col("target_passing_yards")
            .shift(1)
            .cum_sum()
            .over("player_id", "season")
            .alias("_prior_season_yards"),
            (pl.col("kickoff_utc").shift(1).over("player_id") + pl.duration(hours=24)).alias(
                "history_available_at_utc"
            ),
        )
        .with_columns(
            pl.when(pl.col("prior_season_games_in_sample") > 0)
            .then(pl.col("_prior_season_yards") / pl.col("prior_season_games_in_sample"))
            .otherwise(None)
            .alias("passing_yards_season_mean")
        )
        .drop("_prior_season_yards")
    )
    if result.filter(pl.col("history_available_at_utc") >= pl.col("prediction_time_utc")).height:
        raise DataQualityError("Prior game is not available before the prediction timestamp.")
    return result.sort("kickoff_utc", "game_id", "player_id")
