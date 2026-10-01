"""Weekly expanding-window evaluation; the 2025 season is rejected at the boundary."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.baselines import feature_matrix, ridge_pipeline, rolling_predictions
from nfl_prop_model.modeling.contracts import validate_model_table as validate_model_table

MODEL_NAMES = ("prior_five_mean", "season_to_date_mean", "ridge")
EVALUATION_SEASONS = (2023, 2024)


@dataclass(frozen=True)
class Fold:
    name: str
    cutoff_utc: datetime
    train: pl.DataFrame
    test: pl.DataFrame


def walk_forward_folds(table: pl.DataFrame, *, minimum_training_rows: int = 2) -> Iterator[Fold]:
    validate_model_table(table)
    if minimum_training_rows < 2:
        raise ValueError("At least two training rows are required")
    ordered = table.sort("kickoff_utc", "game_id", "player_id")
    evaluation = ordered.filter(pl.col("season").is_in(EVALUATION_SEASONS))
    if evaluation.is_empty():
        raise DataQualityError("No 2023-2024 evaluation rows")
    weeks = (
        evaluation.group_by("season", "week")
        .agg(pl.col("prediction_time_utc").min().alias("cutoff"))
        .sort("cutoff", "season", "week")
    )
    for row in weeks.to_dicts():
        test = evaluation.filter(
            (pl.col("season") == row["season"]) & (pl.col("week") == row["week"])
        )
        # Entire prior games must have cleared the same 24-hour availability assumption
        # used by feature construction. No same-week outcomes fit this week's pipeline.
        train = ordered.filter(pl.col("kickoff_utc") + pl.duration(hours=24) < row["cutoff"])
        if train.height < minimum_training_rows:
            raise DataQualityError(
                f"Insufficient earlier training rows for {row['season']}-{row['week']}"
            )
        yield Fold(f"{row['season']}-W{row['week']:02d}", row["cutoff"], train, test)


def point_metrics(predictions: pl.DataFrame) -> pl.DataFrame:
    """Positive bias means overprediction; all metrics use the same scored rows."""
    errors = predictions.with_columns((pl.col("prediction") - pl.col("actual")).alias("error"))
    metrics = []
    for scope, columns in (
        ("overall", []),
        ("season", ["season"]),
        ("history", ["history_bucket"]),
        ("season_history", ["season", "history_bucket"]),
    ):
        summary = (
            errors.group_by("model", *columns)
            .agg(
                pl.len().alias("n"),
                pl.col("error").abs().mean().alias("mae"),
                (pl.col("error") ** 2).mean().sqrt().alias("rmse"),
                pl.col("error").mean().alias("bias"),
            )
            .with_columns(pl.lit(scope).alias("scope"))
        )
        group = (
            pl.concat_str([pl.col(column).cast(pl.String) for column in columns], separator=" / ")
            if columns
            else pl.lit("all")
        )
        metrics.append(
            summary.with_columns(group.alias("group")).select(
                "scope", "group", "model", "n", "mae", "rmse", "bias"
            )
        )
    return pl.concat(metrics).sort("scope", "group", "model")


def evaluate(table: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, list[dict[str, Any]]]:
    outputs: list[pl.DataFrame] = []
    audit: list[dict[str, Any]] = []
    for fold in walk_forward_folds(table):
        x_train = feature_matrix(fold.train)
        y_train = np.asarray(fold.train["target_passing_yards"].to_numpy(), dtype=np.float64)
        pipeline = ridge_pipeline()
        pipeline.fit(x_train, y_train)
        rolling = rolling_predictions(fold.test, float(y_train.mean()))
        forecasts = {
            "prior_five_mean": rolling["prior_five_mean"].to_numpy(),
            "season_to_date_mean": rolling["season_to_date_mean"].to_numpy(),
            "ridge": np.asarray(pipeline.predict(feature_matrix(fold.test)), dtype=np.float64),
        }
        keys = fold.test.select(
            "player_id",
            "game_id",
            "season",
            "week",
            "kickoff_utc",
            "prediction_time_utc",
            "prior_games_in_sample",
            pl.col("target_passing_yards").alias("actual"),
            pl.when(pl.col("prior_games_in_sample") == 0)
            .then(pl.lit("0: no sample history"))
            .when(pl.col("prior_games_in_sample") < 5)
            .then(pl.lit("1-4 prior games"))
            .otherwise(pl.lit("5+ prior games"))
            .alias("history_bucket"),
            pl.lit(fold.name).alias("fold"),
            pl.lit(fold.cutoff_utc).alias("training_cutoff_utc"),
        )
        for name, forecast in forecasts.items():
            if not np.isfinite(forecast).all():
                raise DataQualityError(f"{name} generated non-finite predictions")
            outputs.append(
                keys.with_columns(pl.lit(name).alias("model"), pl.Series("prediction", forecast))
            )
        imputer = pipeline.named_steps["imputer"]
        scaler = pipeline.named_steps["scaler"]
        model = pipeline.named_steps["regressor"]
        latest_available = fold.train.select(
            (pl.col("kickoff_utc") + pl.duration(hours=24)).max()
        ).item()
        audit.append(
            {
                "fold": fold.name,
                "training_cutoff_utc": fold.cutoff_utc.isoformat(),
                "training_rows": fold.train.height,
                "test_rows": fold.test.height,
                "latest_training_target_available_utc": latest_available.isoformat(),
                "training_target_mean": float(y_train.mean()),
                "imputation_values": dict(
                    zip(FEATURE_COLUMNS, imputer.statistics_.tolist(), strict=True)
                ),
                "transformed_feature_names": imputer.get_feature_names_out(
                    FEATURE_COLUMNS
                ).tolist(),
                "scaler_mean": scaler.mean_.tolist(),
                "scaler_scale": scaler.scale_.tolist(),
                "ridge_standardized_coefficients": model.coef_.tolist(),
                "ridge_intercept": float(model.intercept_),
            }
        )
    predictions = pl.concat(outputs).sort("kickoff_utc", "game_id", "player_id", "model")
    require_keys(predictions, ["player_id", "game_id", "model"], "evaluation predictions")
    return predictions, point_metrics(predictions), audit
