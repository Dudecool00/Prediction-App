# Chronological EPA tuning

Retain the current EPA settings. None of the ten alternatives cleared the predeclared
validation rules. The procedure selected the default for 2022, 2023 and 2024, producing
the same early-week margin MAE of 9.811 and per-team score MAE of 7.333. Refresh margin MAE
remains 9.805. All 1,630 EPA control predictions match the initial saved comparison exactly.

The strongest eligible validation gains were only 0.018 points for selection before 2022
and 0.034 before 2023, below the locked 0.05-point threshold. The default already had the
lowest validation margin error before 2024. The score guard also excludes a three-season
training setting before 2022 because its score error increases by more than 0.05 points.

For diagnosis only, the following table scores each fixed setting over the same 815
2022–2024 early-week games. These aggregate results were not used to select settings for
those seasons; all ten alternatives have higher margin error than the default.

| Fixed setting | Margin MAE | Per-team score MAE |
| --- | ---: | ---: |
| Default: full training, five recent games, 0.85 decay, 200 prior plays | 9.811 | 7.333 |
| Uniform recent weighting | 9.814 | 7.331 |
| 400 prior plays | 9.814 | 7.341 |
| 0.65 recent decay | 9.819 | 7.330 |
| 100 prior plays | 9.831 | 7.333 |
| Eight recent games | 9.831 | 7.331 |
| Three recent games | 9.834 | 7.325 |
| Slow combination with five training seasons | 9.862 | 7.328 |
| Five training seasons | 9.900 | 7.330 |
| Three training seasons | 9.975 | 7.376 |
| Fast combination with three training seasons | 10.025 | 7.369 |

The paired gain and resampling ranges below are zero because the selected procedure keeps
the same model, not because its prediction error is zero or its accuracy is certain.
These results evaluate this fixed grid only. The exact rules, candidate diagnostics,
selection cutoffs and artifact/source hashes are retained in
[the summary](chronological_tuning_summary.json).

Next controlled comparisons can examine forecast-time QB availability, known starter-rest
plans, injury/weather context, or a different model/loss. These history-setting changes
do not address unexpected game outcomes. The periods remain already examined development
data; no untouched-holdout or prospective performance claim is made.

Choose settings using only the preceding two seasons. Lock each setting before Week 1, then refit on available games during the following season. Refresh uses the same choice.

2022–2024 remain development simulations. 2025 is already accessed. No automatic forecast promotion occurs.

## Season selections

| season | selected_profile | validation_games | selection_gain | lock_utc |
| --- | --- | --- | --- | --- |
| 2022 | ridge_epa_scores | 528 | 0.000 | 2022-09-06T15:00:00+00:00 |
| 2023 | ridge_epa_scores | 543 | 0.000 | 2023-09-05T15:00:00+00:00 |
| 2024 | ridge_epa_scores | 543 | 0.000 | 2024-09-03T15:00:00+00:00 |

## Overall

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | chronologically_tuned_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| early_week | ridge_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| pregame_refresh | chronologically_tuned_epa_scores | 815 | 7.331 | 9.255 | 0.213 | 9.805 | 12.754 | -0.933 | 10.523 | 13.415 | 0.426 |
| pregame_refresh | ridge_epa_scores | 815 | 7.331 | 9.255 | 0.213 | 9.805 | 12.754 | -0.933 | 10.523 | 13.415 | 0.426 |

## By season

| horizon | model | season | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | chronologically_tuned_epa_scores | 2022 | 271 | 7.234 | 9.100 | 1.010 | 9.114 | 11.801 | -1.322 | 11.098 | 13.856 | 2.019 |
| early_week | chronologically_tuned_epa_scores | 2023 | 272 | 7.557 | 9.537 | 0.058 | 10.232 | 13.465 | -1.179 | 10.643 | 13.510 | 0.117 |
| early_week | chronologically_tuned_epa_scores | 2024 | 272 | 7.207 | 9.137 | -0.469 | 10.085 | 12.978 | -0.258 | 9.876 | 12.865 | -0.937 |
| early_week | ridge_epa_scores | 2022 | 271 | 7.234 | 9.100 | 1.010 | 9.114 | 11.801 | -1.322 | 11.098 | 13.856 | 2.019 |
| early_week | ridge_epa_scores | 2023 | 272 | 7.557 | 9.537 | 0.058 | 10.232 | 13.465 | -1.179 | 10.643 | 13.510 | 0.117 |
| early_week | ridge_epa_scores | 2024 | 272 | 7.207 | 9.137 | -0.469 | 10.085 | 12.978 | -0.258 | 9.876 | 12.865 | -0.937 |
| pregame_refresh | chronologically_tuned_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | chronologically_tuned_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | chronologically_tuned_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |
| pregame_refresh | ridge_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | ridge_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | ridge_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |

## Paired outer margin gains

| horizon | games | margin_mae_gain | week_bootstrap_gain_lower | week_bootstrap_gain_upper |
| --- | --- | --- | --- | --- |
| early_week | 815 | 0.000 | 0.000 | 0.000 |
| pregame_refresh | 815 | 0.000 | 0.000 | 0.000 |

Week-block ranges are approximate and do not correct serial dependence or earlier development decisions. Score error counts both team scores; margin/total count each game once.
