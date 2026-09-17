"""Chronological calibration helpers with explicit sample-size and time boundaries."""

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import polars as pl
from numpy.typing import NDArray

from nfl_prop_model.data.schemas import DataQualityError


@dataclass(frozen=True)
class CalibrationSplit:
    train: pl.DataFrame
    calibration: pl.DataFrame
    cutoff_utc: datetime


def chronological_calibration_split(
    available: pl.DataFrame, *, weeks: int = 6, minimum_rows: int = 100
) -> CalibrationSplit:
    """Reserve the latest eligible season/weeks; all QBs from a game stay together."""
    if weeks < 1 or minimum_rows < 1:
        raise ValueError("Calibration weeks and minimum rows must be positive")
    periods = (
        available.group_by("season", "week")
        .agg(pl.col("prediction_time_utc").min().alias("cutoff"))
        .sort("cutoff")
    )
    if periods.height <= weeks:
        raise DataQualityError("Not enough weeks for separate training and calibration")
    selected = periods.tail(weeks).select("season", "week")
    calibration = available.join(selected, on=["season", "week"], how="semi").sort(
        "kickoff_utc", "game_id", "player_id"
    )
    cutoff = calibration["prediction_time_utc"].min()
    if not isinstance(cutoff, datetime):
        raise DataQualityError("Calibration cutoff is missing")
    train = available.filter(pl.col("kickoff_utc") + pl.duration(hours=24) < cutoff).sort(
        "kickoff_utc", "game_id", "player_id"
    )
    if train.height < 2 or calibration.height < minimum_rows:
        raise DataQualityError("Insufficient separate training or calibration rows")
    if set(train["game_id"]).intersection(calibration["game_id"]):
        raise DataQualityError("Training and calibration games overlap")
    return CalibrationSplit(train, calibration, cutoff)


@dataclass(frozen=True)
class ResidualCalibration:
    residuals: NDArray[np.float64]

    def __post_init__(self) -> None:
        values = np.asarray(self.residuals, dtype=np.float64)
        if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all():
            raise ValueError("Calibration residuals must be a nonempty finite vector")
        values = values.copy()
        values.flags.writeable = False
        object.__setattr__(self, "residuals", values)

    def radius(self, coverage: float) -> float:
        """Finite-sample corrected absolute residual order statistic, no interpolation."""
        if not 0 < coverage < 1:
            raise ValueError("Coverage must be between zero and one")
        rank = math.ceil((len(self.residuals) + 1) * coverage)
        if rank > len(self.residuals):
            raise ValueError("Too few calibration rows for a finite interval at this coverage")
        return float(np.sort(np.abs(self.residuals))[rank - 1])

    def probability_over(self, prediction: float, line: float) -> float:
        """Smoothed empirical signed-residual tail; not a Gaussian assumption."""
        if not math.isfinite(prediction) or not math.isfinite(line):
            raise ValueError("Prediction and threshold must be finite")
        successes = np.count_nonzero(self.residuals > line - prediction)
        return float((successes + 0.5) / (len(self.residuals) + 1))
