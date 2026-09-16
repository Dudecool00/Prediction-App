"""Explicit feature selection and preprocessing learned only from a training fold."""

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from nfl_prop_model.features.quarterback import FEATURE_COLUMNS


def feature_matrix(frame: pl.DataFrame) -> NDArray[np.float64]:
    return np.asarray(frame.select(FEATURE_COLUMNS).to_numpy(), dtype=np.float64)


def ridge_pipeline() -> Pipeline:
    """Fixed alpha=1; no tuning against either development evaluation or holdout."""
    return Pipeline(
        [
            (
                "imputer",
                SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True),
            ),
            ("scaler", StandardScaler()),
            ("regressor", Ridge(alpha=1.0, solver="svd")),
        ]
    )


def rolling_predictions(test: pl.DataFrame, training_target_mean: float) -> pl.DataFrame:
    """Cold starts use this fold's training mean, never a full-sample mean."""
    return test.select(
        pl.col("passing_yards_mean5").fill_null(training_target_mean).alias("prior_five_mean"),
        pl.coalesce(
            "passing_yards_season_mean", "passing_yards_mean5", pl.lit(training_target_mean)
        ).alias("season_to_date_mean"),
    )
