# Milestone 3: nonlinear forecasts and chronological uncertainty

Generated: 2026-09-17T21:22:25.808945+00:00

## Method

Evaluate every recorded 2023–2024 QB appearance, including backups and newcomers. 2022 supplies initial history; 2025 remains untouched. Retrain before each evaluation week. Reserve the latest six eligible weeks for calibration, fit on earlier games, and require at least 100 calibration rows. Results must clear kickoff plus 24 hours before the relevant cutoff. Training, calibration, and evaluation games are separate.

Compare two rolling forecasts, Ridge, and one fixed XGBoost model with three feature sets: QB history; QB plus schedule; QB plus schedule and opponent context. All six use the same training/calibration/evaluation cohorts. No tuning or early stopping uses evaluation outcomes. The baseline numbers differ from Milestone 2 because recent training observations are now reserved for calibration.

XGBoost uses 150 depth-2 trees, learning rate 0.05, min_child_weight=10, L2 penalty=10, full row/column sampling, histogram bins=256, squared-error objective, CPU, one worker, and seed 42. CPU-only version 3.2.0 retains Python 3.11 support. Missing values follow learned tree directions; the Ridge pipeline fits imputation/scaling only on training rows.

Schedule features are designated home, neutral site, and each team's days since its previous regular-season game in that season. Opponent features are prior-five-game gross passing yards allowed and pass attempts faced, including all recorded passers. Opponent history is shifted and checked for pregame availability.

## Point errors (yards)

| model | n | mae | rmse | bias |
| --- | --- | --- | --- | --- |
| prior_five_mean | 1327 | 72.058 | 92.036 | 2.424 |
| ridge | 1327 | 69.877 | 87.543 | 10.976 |
| season_to_date_mean | 1327 | 70.609 | 91.643 | -4.036 |
| xgb_context | 1327 | 67.568 | 85.305 | 3.015 |
| xgb_qb | 1327 | 68.148 | 85.805 | 3.944 |
| xgb_schedule | 1327 | 67.513 | 85.598 | 3.808 |

## Point errors by season

| group | model | n | mae | rmse | bias |
| --- | --- | --- | --- | --- | --- |
| 2023 | prior_five_mean | 663 | 72.514 | 93.089 | 2.352 |
| 2023 | ridge | 663 | 71.997 | 90.949 | 13.750 |
| 2023 | season_to_date_mean | 663 | 71.276 | 92.093 | -0.441 |
| 2023 | xgb_context | 663 | 69.752 | 87.674 | 5.027 |
| 2023 | xgb_qb | 663 | 70.189 | 87.856 | 4.504 |
| 2023 | xgb_schedule | 663 | 69.615 | 88.353 | 5.040 |
| 2024 | prior_five_mean | 664 | 71.601 | 90.972 | 2.496 |
| 2024 | ridge | 664 | 67.760 | 84.003 | 8.205 |
| 2024 | season_to_date_mean | 664 | 69.942 | 91.192 | -7.625 |
| 2024 | xgb_context | 664 | 65.388 | 82.871 | 1.006 |
| 2024 | xgb_qb | 664 | 66.111 | 83.707 | 3.385 |
| 2024 | xgb_schedule | 664 | 65.413 | 82.757 | 2.578 |

## Incremental feature contribution

Changes compare identical rows and fixed tree settings. Negative error changes are better. These are descriptive development comparisons without a significance claim.

| group | added_features | mae_change | rmse_change |
| --- | --- | --- | --- |
| all | schedule | -0.636 | -0.207 |
| all | opponent | 0.056 | -0.294 |
| 2023 | schedule | -0.574 | 0.497 |
| 2023 | opponent | 0.137 | -0.678 |
| 2024 | schedule | -0.697 | -0.950 |
| 2024 | opponent | -0.026 | 0.114 |

## Point errors by observed history

| group | model | n | mae | rmse | bias |
| --- | --- | --- | --- | --- | --- |
| 0: no sample history | prior_five_mean | 29 | 125.967 | 144.968 | 111.828 |
| 0: no sample history | ridge | 29 | 100.159 | 109.935 | 59.916 |
| 0: no sample history | season_to_date_mean | 29 | 125.967 | 144.968 | 111.828 |
| 0: no sample history | xgb_context | 29 | 86.543 | 98.646 | 32.575 |
| 0: no sample history | xgb_qb | 29 | 100.136 | 109.906 | 60.038 |
| 0: no sample history | xgb_schedule | 29 | 90.740 | 107.531 | 47.298 |
| 1-4 prior games | prior_five_mean | 139 | 76.923 | 102.126 | -21.242 |
| 1-4 prior games | ridge | 139 | 77.099 | 95.090 | 9.421 |
| 1-4 prior games | season_to_date_mean | 139 | 74.803 | 100.492 | -23.602 |
| 1-4 prior games | xgb_context | 139 | 76.443 | 94.877 | 6.092 |
| 1-4 prior games | xgb_qb | 139 | 75.539 | 92.686 | 5.735 |
| 1-4 prior games | xgb_schedule | 139 | 76.291 | 95.630 | 9.722 |
| 5+ prior games | prior_five_mean | 1159 | 70.125 | 89.004 | 2.525 |
| 5+ prior games | ridge | 1159 | 68.253 | 85.952 | 9.938 |
| 5+ prior games | season_to_date_mean | 1159 | 68.720 | 88.763 | -4.588 |
| 5+ prior games | xgb_context | 1159 | 66.029 | 83.717 | 1.906 |
| 5+ prior games | xgb_qb | 1159 | 66.462 | 84.244 | 2.325 |
| 5+ prior games | xgb_schedule | 1159 | 65.879 | 83.684 | 2.011 |

## Interval coverage and width

Intervals use the ceil((n+1) × nominal coverage)-th ordered absolute calibration error. Bounds are the raw point forecast plus/minus that radius, without clipping. Coverage below is an observed fraction, not a promised success rate.

| model | coverage | n | observed_coverage | mean_width |
| --- | --- | --- | --- | --- |
| prior_five_mean | 0.500 | 1327 | 0.494 | 113.502 |
| ridge | 0.500 | 1327 | 0.498 | 119.972 |
| season_to_date_mean | 0.500 | 1327 | 0.504 | 112.483 |
| xgb_context | 0.500 | 1327 | 0.502 | 112.627 |
| xgb_qb | 0.500 | 1327 | 0.505 | 112.873 |
| xgb_schedule | 0.500 | 1327 | 0.498 | 109.821 |
| prior_five_mean | 0.800 | 1327 | 0.795 | 234.267 |
| ridge | 0.800 | 1327 | 0.786 | 220.564 |
| season_to_date_mean | 0.800 | 1327 | 0.798 | 230.343 |
| xgb_context | 0.800 | 1327 | 0.783 | 213.052 |
| xgb_qb | 0.800 | 1327 | 0.789 | 216.854 |
| xgb_schedule | 0.800 | 1327 | 0.787 | 213.963 |
| prior_five_mean | 0.900 | 1327 | 0.905 | 312.453 |
| ridge | 0.900 | 1327 | 0.894 | 282.134 |
| season_to_date_mean | 0.900 | 1327 | 0.907 | 312.715 |
| xgb_context | 0.900 | 1327 | 0.899 | 278.730 |
| xgb_qb | 0.900 | 1327 | 0.898 | 284.549 |
| xgb_schedule | 0.900 | 1327 | 0.897 | 279.007 |

## 90% interval coverage by observed history

| model | history_bucket | n | observed_coverage | mean_width |
| --- | --- | --- | --- | --- |
| prior_five_mean | 0: no sample history | 29 | 0.517 | 321.090 |
| prior_five_mean | 1-4 prior games | 139 | 0.856 | 316.443 |
| prior_five_mean | 5+ prior games | 1159 | 0.921 | 311.758 |
| ridge | 0: no sample history | 29 | 0.759 | 288.477 |
| ridge | 1-4 prior games | 139 | 0.856 | 289.060 |
| ridge | 5+ prior games | 1159 | 0.903 | 281.144 |
| season_to_date_mean | 0: no sample history | 29 | 0.517 | 323.500 |
| season_to_date_mean | 1-4 prior games | 139 | 0.856 | 316.573 |
| season_to_date_mean | 5+ prior games | 1159 | 0.922 | 311.982 |
| xgb_context | 0: no sample history | 29 | 0.862 | 282.376 |
| xgb_context | 1-4 prior games | 139 | 0.849 | 283.607 |
| xgb_context | 5+ prior games | 1159 | 0.906 | 278.054 |
| xgb_qb | 0: no sample history | 29 | 0.793 | 290.291 |
| xgb_qb | 1-4 prior games | 139 | 0.849 | 289.673 |
| xgb_qb | 5+ prior games | 1159 | 0.906 | 283.790 |
| xgb_schedule | 0: no sample history | 29 | 0.828 | 283.032 |
| xgb_schedule | 1-4 prior games | 139 | 0.849 | 282.565 |
| xgb_schedule | 5+ prior games | 1159 | 0.904 | 278.480 |

## Diagnostic threshold probability scores

Thresholds 150.5, 200.5, 250.5, and 300.5 yards were fixed before evaluation; they are diagnostic events, not historical sportsbook lines. Over probabilities use the signed calibration residual tail with 0.5-count smoothing. Positive/negative residual patterns therefore affect these probabilities. No Gaussian error shape is assumed. Symmetric absolute-error intervals and the signed-residual CDF are distinct summaries.

| model | line | season | n | brier | log_loss |
| --- | --- | --- | --- | --- | --- |
| prior_five_mean | 150.500 | 2023 | 663 | 0.178 | 0.547 |
| prior_five_mean | 150.500 | 2024 | 664 | 0.164 | 0.503 |
| prior_five_mean | 200.500 | 2023 | 663 | 0.215 | 0.627 |
| prior_five_mean | 200.500 | 2024 | 664 | 0.223 | 0.643 |
| prior_five_mean | 250.500 | 2023 | 663 | 0.199 | 0.584 |
| prior_five_mean | 250.500 | 2024 | 664 | 0.203 | 0.589 |
| prior_five_mean | 300.500 | 2023 | 663 | 0.133 | 0.426 |
| prior_five_mean | 300.500 | 2024 | 664 | 0.114 | 0.382 |
| ridge | 150.500 | 2023 | 663 | 0.172 | 0.524 |
| ridge | 150.500 | 2024 | 664 | 0.151 | 0.472 |
| ridge | 200.500 | 2023 | 663 | 0.216 | 0.619 |
| ridge | 200.500 | 2024 | 664 | 0.211 | 0.609 |
| ridge | 250.500 | 2023 | 663 | 0.196 | 0.573 |
| ridge | 250.500 | 2024 | 664 | 0.192 | 0.562 |
| ridge | 300.500 | 2023 | 663 | 0.131 | 0.419 |
| ridge | 300.500 | 2024 | 664 | 0.107 | 0.367 |
| season_to_date_mean | 150.500 | 2023 | 663 | 0.174 | 0.538 |
| season_to_date_mean | 150.500 | 2024 | 664 | 0.162 | 0.501 |
| season_to_date_mean | 200.500 | 2023 | 663 | 0.215 | 0.625 |
| season_to_date_mean | 200.500 | 2024 | 664 | 0.226 | 0.657 |
| season_to_date_mean | 250.500 | 2023 | 663 | 0.198 | 0.584 |
| season_to_date_mean | 250.500 | 2024 | 664 | 0.202 | 0.590 |
| season_to_date_mean | 300.500 | 2023 | 663 | 0.131 | 0.421 |
| season_to_date_mean | 300.500 | 2024 | 664 | 0.114 | 0.388 |
| xgb_context | 150.500 | 2023 | 663 | 0.163 | 0.506 |
| xgb_context | 150.500 | 2024 | 664 | 0.145 | 0.463 |
| xgb_context | 200.500 | 2023 | 663 | 0.212 | 0.609 |
| xgb_context | 200.500 | 2024 | 664 | 0.210 | 0.614 |
| xgb_context | 250.500 | 2023 | 663 | 0.191 | 0.558 |
| xgb_context | 250.500 | 2024 | 664 | 0.190 | 0.560 |
| xgb_context | 300.500 | 2023 | 663 | 0.127 | 0.406 |
| xgb_context | 300.500 | 2024 | 664 | 0.107 | 0.368 |
| xgb_qb | 150.500 | 2023 | 663 | 0.166 | 0.512 |
| xgb_qb | 150.500 | 2024 | 664 | 0.149 | 0.471 |
| xgb_qb | 200.500 | 2023 | 663 | 0.211 | 0.608 |
| xgb_qb | 200.500 | 2024 | 664 | 0.211 | 0.613 |
| xgb_qb | 250.500 | 2023 | 663 | 0.195 | 0.567 |
| xgb_qb | 250.500 | 2024 | 664 | 0.192 | 0.563 |
| xgb_qb | 300.500 | 2023 | 663 | 0.129 | 0.409 |
| xgb_qb | 300.500 | 2024 | 664 | 0.106 | 0.367 |
| xgb_schedule | 150.500 | 2023 | 663 | 0.163 | 0.507 |
| xgb_schedule | 150.500 | 2024 | 664 | 0.146 | 0.464 |
| xgb_schedule | 200.500 | 2023 | 663 | 0.209 | 0.604 |
| xgb_schedule | 200.500 | 2024 | 664 | 0.211 | 0.613 |
| xgb_schedule | 250.500 | 2023 | 663 | 0.194 | 0.564 |
| xgb_schedule | 250.500 | 2024 | 664 | 0.191 | 0.561 |
| xgb_schedule | 300.500 | 2023 | 663 | 0.127 | 0.407 |
| xgb_schedule | 300.500 | 2024 | 664 | 0.106 | 0.368 |

Open `calibration.html` beside this report for four interactive reliability plots. It embeds Plotly for offline use and is regenerated rather than committed. Each point compares mean forecast probability with observed frequency in a fixed ten-percent bin. Hover for sample counts; empty bins are omitted. All bin values are in research.json.

## Limits and next checks

Football observations are time-dependent, repeated by player, and clustered by game. The exchangeability assumption behind split-conformal guarantees is not established. Report empirical coverage and width, including history subgroups; nominal 90% is not a guarantee for a particular player. Tail estimates and small bins are uncertain. These probabilities are initial research estimates, not validated betting probabilities.

The cohort is conditioned on recorded participation. The 37 historical starter-label discrepancies remain open, and source corrections may postdate games. Current depth charts never filter historical rows or enter predictors. Models have no injuries or confirmed pregame starter feature. No prices, ROI, EV, or production UI are evaluated.

Weather remains a separate data-readiness task: the 2023 GFS prior-day sample supplied temperature but no wind/precipitation. A verified stadium map and roof policy are also needed. Missing historical forecasts were not replaced by observed weather or zeros.

## Provenance

Historical QB-table hash: `5bceef915e3ff9216710f6be47c505fc39611d6b2ffa3d7bac77f3d6d44261cc`.

Prediction hash: `c9d2bd50de638323a108d63fe9b544e756a11a8f86b1352beb7daa661ab4b761`.

Data: [nflverse](https://github.com/nflverse/nflverse-data), [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). This report aggregates their data and derives forecasts and diagnostics.

Methods: [XGBoost](https://xgboost.readthedocs.io/en/release_3.2.0/), [conformal prediction overview](https://arxiv.org/abs/2107.07511), [archived forecast coverage](https://open-meteo.com/en/docs/previous-runs-api).
