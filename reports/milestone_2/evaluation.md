# Milestone 2: chronological baseline evaluation

Generated: 2026-09-15T18:18:44.728214+00:00

## Scope and method

2022 supplies initial training history; every recorded QB appearance in 2023 and 2024 is scored once per model. The 2025 season remains untouched. All QBs, backups, zero-attempt games, and newcomers remain. This is a research comparison conditioned on recorded appearance, not validation of an upcoming-game starter selector.

Each season/week is one fold. Fit before its earliest pregame timestamp (one hour before kickoff), using only games whose kickoff plus 24 hours is strictly earlier. Refit weekly with an expanding training window. A week's outcomes cannot train its own model. Lagged features remain specific to each game's prediction time.

- **prior_five_mean:** mean of up to five previous recorded QB games; no history falls back to the training-fold target mean.
- **season_to_date_mean:** previous games' mean in the same season; falls back to the prior-five mean, then the training-fold target mean.
- **ridge:** the seven allowed lagged features; train-fold median imputation with missing indicators, standard scaling, and Ridge with fixed alpha=1, SVD solver. No tuning. An entirely missing training column is retained with zero imputation. Forecasts are not clipped.

## Counts

| Measure | Value |
| --- | ---: |
| source_rows | 1960 |
| warmup_rows | 633 |
| evaluated_qb_games | 1327 |
| folds | 36 |
| models | 3 |
| dropped_evaluation_rows | 0 |

## Overall comparison

| Group | Model | N | MAE | RMSE | Bias |
| --- | --- | ---: | ---: | ---: | ---: |
| all | prior_five_mean | 1327 | 72.03 | 91.97 | 2.35 |
| all | ridge | 1327 | 68.80 | 85.72 | 10.21 |
| all | season_to_date_mean | 1327 | 70.58 | 91.57 | -4.11 |

## By season

| Group | Model | N | MAE | RMSE | Bias |
| --- | --- | ---: | ---: | ---: | ---: |
| 2023 | prior_five_mean | 663 | 72.47 | 92.98 | 2.23 |
| 2023 | ridge | 663 | 69.74 | 87.21 | 12.65 |
| 2023 | season_to_date_mean | 663 | 71.24 | 91.98 | -0.56 |
| 2024 | prior_five_mean | 664 | 71.58 | 90.95 | 2.47 |
| 2024 | ridge | 664 | 67.86 | 84.20 | 7.77 |
| 2024 | season_to_date_mean | 664 | 69.92 | 91.17 | -7.65 |

## By observed sample history

| Group | Model | N | MAE | RMSE | Bias |
| --- | --- | ---: | ---: | ---: | ---: |
| 0: no sample history | prior_five_mean | 29 | 124.66 | 142.90 | 108.51 |
| 0: no sample history | ridge | 29 | 98.24 | 107.75 | 54.88 |
| 0: no sample history | season_to_date_mean | 29 | 124.66 | 142.90 | 108.51 |
| 1-4 prior games | prior_five_mean | 139 | 76.92 | 102.13 | -21.24 |
| 1-4 prior games | ridge | 139 | 73.68 | 89.67 | 5.02 |
| 1-4 prior games | season_to_date_mean | 139 | 74.80 | 100.49 | -23.60 |
| 5+ prior games | prior_five_mean | 1159 | 70.13 | 89.00 | 2.53 |
| 5+ prior games | ridge | 1159 | 67.48 | 84.60 | 9.71 |
| 5+ prior games | season_to_date_mean | 1159 | 68.72 | 88.76 | -4.59 |

## Season and history

| Group | Model | N | MAE | RMSE | Bias |
| --- | --- | ---: | ---: | ---: | ---: |
| 2023 / 0: no sample history | prior_five_mean | 18 | 126.79 | 145.81 | 111.17 |
| 2023 / 0: no sample history | ridge | 18 | 101.52 | 111.16 | 59.92 |
| 2023 / 0: no sample history | season_to_date_mean | 18 | 126.79 | 145.81 | 111.17 |
| 2023 / 1-4 prior games | prior_five_mean | 92 | 78.55 | 103.72 | -19.01 |
| 2023 / 1-4 prior games | ridge | 92 | 70.99 | 88.75 | 5.81 |
| 2023 / 1-4 prior games | season_to_date_mean | 92 | 75.97 | 101.21 | -21.54 |
| 2023 / 5+ prior games | prior_five_mean | 553 | 69.70 | 88.78 | 2.22 |
| 2023 / 5+ prior games | ridge | 553 | 68.50 | 86.06 | 12.25 |
| 2023 / 5+ prior games | season_to_date_mean | 553 | 68.64 | 88.02 | -0.71 |
| 2024 / 0: no sample history | prior_five_mean | 11 | 121.17 | 138.02 | 104.17 |
| 2024 / 0: no sample history | ridge | 11 | 92.86 | 101.91 | 46.63 |
| 2024 / 0: no sample history | season_to_date_mean | 11 | 121.17 | 138.02 | 104.17 |
| 2024 / 1-4 prior games | prior_five_mean | 47 | 73.75 | 98.93 | -25.62 |
| 2024 / 1-4 prior games | ridge | 47 | 78.95 | 91.45 | 3.46 |
| 2024 / 1-4 prior games | season_to_date_mean | 47 | 72.51 | 99.08 | -27.63 |
| 2024 / 5+ prior games | prior_five_mean | 606 | 70.52 | 89.21 | 2.81 |
| 2024 / 5+ prior games | ridge | 606 | 66.54 | 83.26 | 7.40 |
| 2024 / 5+ prior games | season_to_date_mean | 606 | 68.79 | 89.44 | -8.13 |

## Interpretation and limitations

Ridge minus prior-five mean: MAE -3.23 yards; RMSE -6.25 yards. Negative differences favor Ridge. These are descriptive development results, not evidence of a statistically established improvement or profitable betting.

MAE is average absolute error; RMSE gives larger misses more weight. Positive bias means overprediction. Experience buckets count observed games in this sample, not career games. Missing subgroup rows mean zero observations, not zero error.

The 37 historical schedule/starter discrepancies remain unresolved. Starter labels, 2026 membership, current-game attempts, and outcomes are excluded from predictors. Absent/inactive players are not reconstructed. Retrospective participation is an availability limitation of this cohort. Historical source revisions and the assumed 24-hour publication delay limit claims about information actually available pregame.

No probability distribution, calibrated intervals, historical prices, ROI, or production prediction UI is supplied in this milestone. Fold-level imputations, scaling, coefficients, cutoffs and sample counts are in evaluation.json. Coefficients are on standardized features and describe associations, not causal effects.

## Provenance

Historical table SHA-256: `5bceef915e3ff9216710f6be47c505fc39611d6b2ffa3d7bac77f3d6d44261cc`.

Out-of-fold prediction SHA-256: `6180fb2545dbe6ee1212469e39fb9177638bf5f2a5127fe997325a59e04fcae4`.

Data: [nflverse contributors](https://github.com/nflverse/nflverse-data), [CC BY 4.0 distribution](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). This report transforms their data into forecasts and aggregate error metrics.

Implementation references: [Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html), [imputation](https://scikit-learn.org/stable/modules/generated/sklearn.impute.SimpleImputer.html), [train-only preprocessing](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
