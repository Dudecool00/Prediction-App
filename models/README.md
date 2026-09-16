# Model artifacts

Milestone 2 fits Ridge independently before each evaluation week, alongside prior-five-game
and season-to-date rolling forecasts. The fitted estimators are transient; no production model
file is published yet. Run `nfl-prop evaluate` from the repository root to reproduce them.

`reports/milestone_2/evaluation.json` records training cutoffs, feature names, fold medians,
missing indicators, scaler parameters, standardized coefficients, package versions, and source
hashes. Forecasts are stored in `data/processed/baselines_2022_2024/`, outside Git. The 2025
season remains reserved. Future serialized models must retain the same provenance and be
loaded only from trusted project artifacts.
