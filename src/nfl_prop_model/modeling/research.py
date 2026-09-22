"""Compare a fixed tree model and feature groups using a separate calibration window."""

from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray
from xgboost import XGBRegressor

from nfl_prop_model.data.schemas import DataQualityError, require_keys
from nfl_prop_model.features.context import OPPONENT_FEATURES, SCHEDULE_FEATURES
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS
from nfl_prop_model.modeling.baselines import feature_matrix, ridge_pipeline, rolling_predictions
from nfl_prop_model.modeling.evaluate import point_metrics, walk_forward_folds
from nfl_prop_model.modeling.uncertainty import (
    ResidualCalibration,
    chronological_calibration_split,
)

TREE_PARAMETERS = {
    "n_estimators": 150,
    "max_depth": 2,
    "learning_rate": 0.05,
    "min_child_weight": 10,
    "reg_lambda": 10.0,
    "subsample": 1.0,
    "colsample_bytree": 1.0,
    "random_state": 42,
    "n_jobs": 1,
    "tree_method": "hist",
    "max_bin": 256,
    "objective": "reg:squarederror",
    "device": "cpu",
}
TREE_FEATURES = {
    "xgb_qb": FEATURE_COLUMNS,
    "xgb_schedule": FEATURE_COLUMNS + SCHEDULE_FEATURES,
    "xgb_context": FEATURE_COLUMNS + SCHEDULE_FEATURES + OPPONENT_FEATURES,
}
COVERAGES = (0.5, 0.8, 0.9)
DIAGNOSTIC_THRESHOLDS = (150.5, 200.5, 250.5, 300.5)


@dataclass(frozen=True)
class ResearchResult:
    predictions: pl.DataFrame
    intervals: pl.DataFrame
    probabilities: pl.DataFrame
    calibration_residuals: pl.DataFrame
    point_metrics: pl.DataFrame
    interval_metrics: pl.DataFrame
    probability_metrics: pl.DataFrame
    reliability: pl.DataFrame
    folds: list[dict[str, Any]]


def matrix(table: pl.DataFrame, features: tuple[str, ...]) -> NDArray[np.float64]:
    return np.asarray(table.select(features).to_numpy(), dtype=np.float64)


def interval_metrics(intervals: pl.DataFrame) -> pl.DataFrame:
    return (
        intervals.group_by("model", "coverage", "season")
        .agg(
            pl.len().alias("n"),
            ((pl.col("actual") >= pl.col("lower")) & (pl.col("actual") <= pl.col("upper")))
            .mean()
            .alias("observed_coverage"),
            (pl.col("upper") - pl.col("lower")).mean().alias("mean_width"),
        )
        .sort("model", "coverage", "season")
    )


def probability_diagnostics(probabilities: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    scored = probabilities.with_columns(
        ((pl.col("probability_over") - pl.col("outcome_over")) ** 2).alias("brier"),
        (
            -(
                pl.col("outcome_over") * pl.col("probability_over").log()
                + (1 - pl.col("outcome_over")) * (1 - pl.col("probability_over")).log()
            )
        ).alias("log_loss"),
        ((pl.col("probability_over") * 10).floor().clip(0, 9).cast(pl.Int64)).alias("bin"),
    )
    scores = (
        scored.group_by("model", "line", "season")
        .agg(
            pl.len().alias("n"),
            pl.col("brier", "log_loss").mean(),
        )
        .sort("model", "line", "season")
    )
    reliability = (
        scored.group_by("model", "line", "bin")
        .agg(
            pl.len().alias("n"),
            pl.col("probability_over").mean().alias("mean_predicted_probability"),
            pl.col("outcome_over").mean().alias("observed_over_rate"),
        )
        .sort("model", "line", "bin")
    )
    return scores, reliability


def evaluate_research(
    table: pl.DataFrame, *, calibration_weeks: int = 6, minimum_calibration_rows: int = 100
) -> ResearchResult:
    points: list[pl.DataFrame] = []
    intervals: list[pl.DataFrame] = []
    probabilities: list[pl.DataFrame] = []
    calibration_records: list[pl.DataFrame] = []
    audit: list[dict[str, Any]] = []
    for fold in walk_forward_folds(table):
        split = chronological_calibration_split(
            fold.train, weeks=calibration_weeks, minimum_rows=minimum_calibration_rows
        )
        train, calibration, test = split.train, split.calibration, fold.test
        target = np.asarray(train["target_passing_yards"].to_numpy(), dtype=np.float64)
        cal_target = np.asarray(calibration["target_passing_yards"].to_numpy(), dtype=np.float64)
        # Baselines and trees share identical proper-training/calibration/test cohorts.
        cal_rolling = rolling_predictions(calibration, float(target.mean()))
        test_rolling = rolling_predictions(test, float(target.mean()))
        forecasts: dict[str, tuple[NDArray[np.float64], NDArray[np.float64]]] = {
            name: (
                np.asarray(cal_rolling[name].to_numpy(), dtype=np.float64),
                np.asarray(test_rolling[name].to_numpy(), dtype=np.float64),
            )
            for name in ("prior_five_mean", "season_to_date_mean")
        }
        ridge = ridge_pipeline()
        ridge.fit(feature_matrix(train), target)
        forecasts["ridge"] = (
            ridge.predict(feature_matrix(calibration)),
            ridge.predict(feature_matrix(test)),
        )
        importance = {}
        for name, features in TREE_FEATURES.items():
            tree = XGBRegressor(**TREE_PARAMETERS)
            tree.fit(matrix(train, features), target)
            forecasts[name] = (
                np.asarray(tree.predict(matrix(calibration, features)), dtype=np.float64),
                np.asarray(tree.predict(matrix(test, features)), dtype=np.float64),
            )
            importance[name] = dict(zip(features, tree.feature_importances_.tolist(), strict=True))
        keys = test.select(
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
            pl.lit(fold.cutoff_utc).alias("evaluation_cutoff_utc"),
            pl.lit(split.cutoff_utc).alias("training_cutoff_utc"),
        )
        calibration_summary = {}
        for name, (cal_prediction, prediction) in forecasts.items():
            if not np.isfinite(cal_prediction).all() or not np.isfinite(prediction).all():
                raise DataQualityError(f"Non-finite {name} prediction")
            residuals = ResidualCalibration(cal_target - cal_prediction)
            calibration_records.append(
                calibration.select(
                    "player_id", "game_id", "kickoff_utc", "prediction_time_utc"
                ).with_columns(
                    pl.lit(fold.name).alias("fold"),
                    pl.lit(name).alias("model"),
                    pl.lit(fold.cutoff_utc).alias("evaluation_cutoff_utc"),
                    pl.lit(split.cutoff_utc).alias("training_cutoff_utc"),
                    pl.Series("residual", residuals.residuals),
                )
            )
            point = keys.with_columns(
                pl.lit(name).alias("model"), pl.Series("prediction", prediction)
            )
            points.append(point)
            radii = {}
            for coverage in COVERAGES:
                radius = residuals.radius(coverage)
                radii[str(coverage)] = radius
                intervals.append(
                    point.with_columns(
                        pl.lit(coverage).alias("coverage"),
                        (pl.col("prediction") - radius).alias("lower"),
                        (pl.col("prediction") + radius).alias("upper"),
                    )
                )
            for line in DIAGNOSTIC_THRESHOLDS:
                probabilities.append(
                    point.with_columns(
                        pl.lit(line).alias("line"),
                        pl.Series(
                            "probability_over",
                            [residuals.probability_over(float(p), line) for p in prediction],
                        ),
                        (pl.col("actual") > line).cast(pl.Int64).alias("outcome_over"),
                    )
                )
            calibration_summary[name] = {
                "residual_count": len(residuals.residuals),
                "mean_signed_residual": float(residuals.residuals.mean()),
                "radii": radii,
            }
        audit.append(
            {
                "fold": fold.name,
                "evaluation_cutoff_utc": fold.cutoff_utc.isoformat(),
                "training_cutoff_utc": split.cutoff_utc.isoformat(),
                "training_rows": train.height,
                "calibration_rows": calibration.height,
                "test_rows": test.height,
                "latest_training_result_available_utc": train.select(
                    (pl.col("kickoff_utc") + pl.duration(hours=24)).max()
                )
                .item()
                .isoformat(),
                "latest_calibration_result_available_utc": calibration.select(
                    (pl.col("kickoff_utc") + pl.duration(hours=24)).max()
                )
                .item()
                .isoformat(),
                "calibration": calibration_summary,
                "tree_gain_importance": importance,
                "ridge_imputation_values": ridge.named_steps["imputer"].statistics_.tolist(),
                "ridge_scaler_mean": ridge.named_steps["scaler"].mean_.tolist(),
            }
        )
    sort = ["kickoff_utc", "game_id", "player_id", "model"]
    all_points = pl.concat(points).sort(sort)
    all_intervals = pl.concat(intervals).sort(*sort, "coverage")
    all_probabilities = pl.concat(probabilities).sort(*sort, "line")
    all_calibration = pl.concat(calibration_records).sort(
        "fold", "model", "kickoff_utc", "game_id", "player_id"
    )
    require_keys(all_points, ["player_id", "game_id", "model"], "research forecasts")
    scores, reliability = probability_diagnostics(all_probabilities)
    return ResearchResult(
        all_points,
        all_intervals,
        all_probabilities,
        all_calibration,
        point_metrics(all_points),
        interval_metrics(all_intervals),
        scores,
        reliability,
        audit,
    )
