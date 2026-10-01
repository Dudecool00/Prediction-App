"""Fixed development model configuration; importing it does not load estimators."""

from nfl_prop_model.features.context import OPPONENT_FEATURES, SCHEDULE_FEATURES
from nfl_prop_model.features.quarterback import FEATURE_COLUMNS

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
