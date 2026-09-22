"""A discrete-yard settlement approximation from chronological signed residuals."""

import numpy as np

from nfl_prop_model.markets.odds import OutcomeProbabilities, finite_number
from nfl_prop_model.modeling.uncertainty import ResidualCalibration

DISTRIBUTION_VERSION = "rounded_signed_residuals_v1"


def passing_yard_probabilities(
    prediction: float, calibration: ResidualCalibration, line: float
) -> OutcomeProbabilities:
    """Round pseudo-outcomes to whole yards; half-count smoothing at each infinite tail.

    The two tail pseudocounts preserve normalization across every line and leave
    zero push mass on half-yard lines. Integer push mass is empirical and may be zero.
    This is an explicit settlement approximation, not validated conditional calibration.
    """
    prediction = finite_number(prediction, "Prediction")
    line = finite_number(line, "Passing-yards line")
    if line < 0 or not (line * 2).is_integer():
        raise ValueError("Passing-yards lines must be nonnegative whole or half yards")
    with np.errstate(over="ignore", invalid="ignore"):
        outcomes = np.floor(prediction + calibration.residuals + 0.5)
    if not np.isfinite(outcomes).all():
        raise ValueError("Non-finite predictive outcomes")
    denominator = len(outcomes) + 1
    return OutcomeProbabilities(
        over=float((np.count_nonzero(outcomes > line) + 0.5) / denominator),
        under=float((np.count_nonzero(outcomes < line) + 0.5) / denominator),
        push=float(np.count_nonzero(outcomes == line) / denominator),
    )
