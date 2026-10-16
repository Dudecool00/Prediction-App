# Forecast-defined margin calibration

Recorded 2026-10-16. Reuse saved 2023–2024 development and frozen 2025 diagnostic
forecasts. This is an adaptive diagnostic of already-examined data, with zero new
prediction-model fits and no forecast correction or promotion.

- Keep every game, including actual blowouts. Use fixed absolute compact-EPA
  forecast bands: `[0,3)`, `[3,7)`, `[7,14)`, `[14,infinity)`. Exactly 3, 7 and 14
  enter the higher band. All methods share the EPA-defined game IDs for comparison.
- Report 2023, 2024, combined development and 2025 separately; never pool 2025 into
  a development choice. Score both horizons separately, with Tuesday-only figures.
- Within bands, report margin/score/total MAE, signed margin bias, fraction of
  margin errors within 3/7/14 points, and the reference favorite's predicted and
  actual margin and win fraction. Predicted ties have no favorite; retain their
  errors and report their count, excluding them only from favorite summaries.
- For each method and period, describe `actual_home_margin = intercept + slope *
  predicted_home_margin`. Ideal mean calibration has intercept 0 and slope 1.
  Bootstrap 5,000 season/week blocks with seed 20261015 for the descriptive slope
  range. Report no slope when predicted-margin standard deviation is below one
  point: a nearly constant control cannot identify that relationship reliably.
- These OLS descriptions and ranges are not fitted forecast adjustments. Sparse
  bands, overlapping development summaries and duplicated refresh are not new
  independent evidence. Do not stretch forecasts from a 2025 fitted slope.
- Reuse hash-verified caches; retain derived summaries and charts only. No raw
  downloads, copied training data, new environment or change to the enriched gate.

Background: [forecast calibration](https://arxiv.org/abs/2108.03210) and
[outcome-conditioning pitfalls](https://arxiv.org/abs/1512.09244). The implemented
linear diagnostic is a limited mean-calibration check, not the papers' full method.
