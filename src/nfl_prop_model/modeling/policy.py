"""Versioned development candidate; a history gate is not production approval."""

import hashlib
import json
from typing import Any

from nfl_prop_model.modeling.config import TREE_FEATURES, TREE_PARAMETERS

MINIMUM_HISTORY_GAMES = 5
MINIMUM_GROUP_RESIDUALS = 30


def candidate_policy() -> dict[str, Any]:
    return {
        "version": "qb-passing-development-candidate-v1",
        "status": "candidate_pending_freeze_and_holdout",
        "model": "xgb_schedule",
        "selection_basis": "Lowest pooled development MAE with improvements in both evaluation "
        "seasons; opponent features have mixed incremental results",
        "features": list(TREE_FEATURES["xgb_schedule"]),
        "parameters": dict(TREE_PARAMETERS),
        "weather": "excluded; historical pregame coverage is unverified",
        "calibration": {
            "weeks": 6,
            "minimum_rows": 100,
            "method": "pooled signed residuals",
            "interval": "finite-sample absolute residual order statistic",
            "coverages": [0.5, 0.8, 0.9],
            "group_matched_minimum_rows": MINIMUM_GROUP_RESIDUALS,
            "group_matched_status": "diagnostic only; not selected for forecasts",
        },
        "history": {
            "minimum_prior_games_in_sample": MINIMUM_HISTORY_GAMES,
            "below_minimum": "research_only; abstain from prospective probability and EV",
            "meaning": "Pregame available model-sample appearances, not career experience",
        },
        "reserved_holdout": {"season": 2025, "access": "closed_until_explicit_policy_freeze"},
        "production_enabled": False,
        "remaining_gates": [
            "freeze source/code/estimator/calibration artifacts before holdout access",
            "evaluate the reserved holdout once without using it to revise this candidate",
            "validate the participation-conditioned cohort for prospective starter use",
            "verify current feature availability, identity, starter, and active-status evidence",
        ],
    }


def policy_hash(policy: dict[str, Any]) -> str:
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def history_policy_status(prior_games: int) -> str:
    if isinstance(prior_games, bool) or not isinstance(prior_games, int) or prior_games < 0:
        raise ValueError("Prior game count must be a nonnegative integer")
    return (
        "research_only_sparse_history"
        if prior_games < MINIMUM_HISTORY_GAMES
        else "history_minimum_met_production_pending"
    )
