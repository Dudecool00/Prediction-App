# Model artifacts

Milestone 2 fits Ridge independently before each evaluation week, alongside prior-five-game
and season-to-date rolling forecasts. The fitted estimators are transient; no production model
file is published yet. Run `nfl-prop evaluate` from the repository root to reproduce them.

`reports/milestone_2/evaluation.json` records training cutoffs, feature names, fold medians,
missing indicators, scaler parameters, standardized coefficients, package versions, and source
hashes. Forecasts are stored in `data/processed/baselines_2022_2024/`, outside Git. Development
evaluation excludes 2025. Serialized models must retain the same provenance and be
loaded only from trusted project artifacts.

Milestone 3's `nfl-prop research` also fits transient XGBoost estimators with QB-only,
schedule, and opponent feature groups. All six forecasts share earlier training rows and
a separate six-week calibration window. Models never fit calibration or evaluation targets.
`reports/milestone_3/research.json` stores fixed parameters, feature lists, normalized tree
gain importance (not causal effects), Ridge preprocessing, residual summaries, and data/code
hashes. Prediction intervals and threshold probabilities are saved separately in
`data/processed/research_2022_2024/`. No production model has been selected or serialized.

Milestone 4's format-2 research manifest also saves chronological signed calibration errors.
The manual-market engine reuses these verified errors and a saved forecast; it does not refit
a model when a line or price changes. See `reports/milestone_4/settlement_audit.md` for the
discrete-yard approximation and integer-push limitations.

`nfl-prop prepare-candidate` now saves a development `xgb_schedule` estimator plus its
separate six-week calibration pool in a new ignored `models/candidates/` directory.
It reads verified 2022–2024 caches only and preserves all sparse-history training rows.
The artifact includes the input/split tables, signed residuals, source code archive,
dependency constraints, source manifests, file hashes and a draft 2025 holdout protocol.

Use `nfl-prop verify-candidate --bundle <directory>` to verify the content-addressed
manifest, every artifact, the chronological split and exact saved calibration predictions.
The model uses [XGBoost's JSON save/load format](https://xgboost.readthedocs.io/en/release_3.2.0/tutorials/saving_model.html).
Model parameters and feature order are recorded separately in the manifest as well.
Checksums establish integrity, not author identity; use trusted project artifacts.

These bundles are **prepared candidates, not frozen or production models**. They do not
enable upcoming forecasts or access 2025 outcomes. See `MODEL_POLICY.md` for the draft
fixed-estimator holdout protocol and remaining source/cohort requirements.

`nfl-prop freeze-candidate` copies a verified candidate to ignored `models/frozen/`, adding
a content-addressed freeze record, evaluator source archive and runtime versions. The guarded
`evaluate-holdout` command checks these before 2025 access, records access/attempt history,
and reuses the same estimator and residuals for the whole season. A completed invocation
verifies saved artifacts and reuses its report. Failed attempts require explicit same-freeze
resume; another checkpoint cannot retest an accessed season in that data directory.

The October 6 frozen diagnostic is complete. Its report/checkpoint are committed under
`reports/frozen_holdout/`; the exact model/calibration files remain local ignored artifacts.
The 2025 holdout is now accessed, and prospective forecasting/source/cohort gates remain open.

`nfl-prop current-features` now audits prospective inputs without loading any estimator.
It reads the completed 2025 features as prior appearances, combines observed 2026 history,
and saves feature/status/rest evidence separately under ignored `data/prospective/`.
Neither model parameters nor calibration change. No production model is enabled.
