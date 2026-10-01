"""Pregame model-table contracts independent of estimator imports."""

import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS


def validate_model_table(table: pl.DataFrame) -> None:
    required = {
        "player_id",
        "game_id",
        "season",
        "week",
        "kickoff_utc",
        "prediction_time_utc",
        "history_available_at_utc",
        "target_passing_yards",
        *FEATURE_COLUMNS,
    }
    if missing := required - set(table.columns):
        raise DataQualityError(f"Model table missing columns: {sorted(missing)}")
    if table.is_empty():
        raise DataQualityError("Model table is empty")
    if set(table["season"].unique().to_list()) - {2022, 2023, 2024}:
        raise DataQualityError("Only 2022-2024 development data are allowed; 2025+ is reserved")
    require_keys(table, ["player_id", "game_id"], "model table")
    required_values = [
        "season",
        "week",
        "kickoff_utc",
        "prediction_time_utc",
        "target_passing_yards",
        "prior_games_in_sample",
        "prior_season_games_in_sample",
    ]
    if table.select(pl.any_horizontal(pl.col(required_values).is_null()).any()).item():
        raise DataQualityError("Required model values contain nulls")
    for name in ("kickoff_utc", "prediction_time_utc", "history_available_at_utc"):
        dtype = table.schema[name]
        if not isinstance(dtype, pl.Datetime) or dtype.time_zone != "UTC":
            raise DataQualityError(f"{name} must be a UTC timestamp")
    numeric = ["target_passing_yards", *FEATURE_COLUMNS]
    if table.select(pl.any_horizontal(~pl.col(numeric).is_finite()).fill_null(False).any()).item():
        raise DataQualityError("Model values must be finite or null (for missing features)")
    if table.filter(
        (pl.col("prediction_time_utc") != pl.col("kickoff_utc") - pl.duration(hours=1))
        | (pl.col("history_available_at_utc") >= pl.col("prediction_time_utc"))
        | ((pl.col("prior_games_in_sample") > 0) & pl.col("history_available_at_utc").is_null())
    ).height:
        raise DataQualityError("Features or prediction timestamps violate pregame availability")
    game_metadata = table.group_by("game_id").agg(
        pl.col("season", "week", "kickoff_utc", "prediction_time_utc").n_unique()
    )
    if game_metadata.filter(pl.any_horizontal(pl.exclude("game_id") != 1)).height:
        raise DataQualityError("QB rows for the same game disagree on fold or kickoff")
