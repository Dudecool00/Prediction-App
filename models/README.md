# Model artifacts

Milestone 2 fits Ridge independently before each evaluation week, alongside prior-five-game
and season-to-date rolling forecasts. The fitted estimators are transient; no production model
file is published yet. Run `nfl-prop evaluate` from the repository root to reproduce them.

`reports/milestone_2/evaluation.json` records training cutoffs, feature names, fold medians,
missing indicators, scaler parameters, standardized coefficients, package versions, and source
hashes. Forecasts are stored in `data/processed/baselines_2022_2024/`, outside Git. The 2025
season remains reserved. Future serialized models must retain the same provenance and be
loaded only from trusted project artifacts.

Milestone 3's `nfl-prop research` also fits transient XGBoost estimators with QB-only,
schedule, and opponent feature groups. All six forecasts share earlier training rows and
a separate six-week calibration window. Models never fit calibration or evaluation targets.
`reports/milestone_3/research.json` stores fixed parameters, feature lists, normalized tree
gain importance (not causal effects), Ridge preprocessing, residual summaries, and data/code
hashes. Prediction intervals and threshold probabilities are saved separately in
`data/processed/research_2022_2024/`. No production model has been selected or serialized.
