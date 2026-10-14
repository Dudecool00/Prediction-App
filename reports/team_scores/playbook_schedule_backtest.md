# Play-by-play and opponent/schedule strength: development result

Evaluated October 14, 2026. Retain the current EPA forecast model. The new datasets and
comparison interface are available for further research, but none of the three fixed
additions reduces margin MAE on these development seasons.

## What was added

- Success rate (positive EPA), explosive dropbacks (20+ yards), explosive designed rushes
  (10+ yards), sacks per dropback and turnovers per play, for offense and defense.
- Opponent-adjusted offense/defense strength, plus recent and season averages of previously
  faced opponents' offense/defense ratings at each forecast cutoff. Repeated opponents count
  per game; no future schedule or final-season hindsight rating enters these features.
- Saved weekly final-score/margin comparisons and matchup-level measures in the app.

2016–2024 covers 4,734 team-games and 295,330 eligible offensive plays. All added yards,
sack and turnover measurements are known on these eligible plays. Original EPA values and
play IDs are retained; expanded play counts/EPA sums reconcile to the canonical cache.
The code retains unknowns when measurements or history are missing.

## Decision

Same 815 games per horizon, 421 chronological cutoff groups and fixed Ridge alpha 20.
All 4,890 league/prior-scoring/current-EPA control predictions are identical to the initial
backtest. No error-heavy games were removed or outcomes used to set the feature definitions.

| Model | Early margin MAE | Refresh margin MAE | Early score MAE | Refresh score MAE |
|---|---:|---:|---:|---:|
| Current EPA | 9.811 | 9.805 | 7.333 | 7.331 |
| + Play tendencies | 9.862 | 9.862 | 7.371 | 7.365 |
| + Opponent strength and schedule | 9.826 | 9.846 | 7.332 | 7.334 |
| + Both | 9.892 | 9.922 | 7.366 | 7.368 |

Every margin-error point estimate worsens. All approximate paired week-block gain ranges
include zero. Opponent/schedule features slightly reduce early-week margin RMSE (12.708
versus 12.768) and bias, but do not reduce average margin error. Their per-team score change
is effectively zero and total-score MAE worsens. These tradeoffs do not support promotion.

2022–2024 are already examined development seasons. The kickoff-plus-24-hour assumption
does not establish historical publication time, and later source revisions are not
reconstructed. Opponent rating fits include available games against the evaluated team;
they are not leave-one-team-out ratings. The comparison does not establish that play-by-play
or schedule strength is useless under other models or feature choices. A revised choice
requires later prospective validation; 2025 is already accessed.

Validation: all 393 tests pass, including the saved weekly matchup view, denominator/unknown
checks, unavailable-data exclusion and source/code/checksum guards. Ruff, formatting, strict
typing and dependency checks pass. Existing local native Polars diagnostics were recorded;
no handlers or tests were disabled, and the full test process exited successfully.

See `playbook_schedule_summary.json` for exact metrics, source/code hashes, feature policy,
source retrieval times and paired comparison ranges. Complete local artifacts include the
predictions, features, temporal folds and original source manifests.

## Overall

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 815 | 7.710 | 9.688 | 0.660 | 10.597 | 13.772 | -0.583 | 10.783 | 13.630 | 1.321 |
| early_week | prior_team_scores | 815 | 7.374 | 9.300 | -0.074 | 9.916 | 12.922 | -0.634 | 10.465 | 13.377 | -0.148 |
| early_week | ridge_epa_play_schedule_scores | 815 | 7.366 | 9.315 | 0.361 | 9.892 | 12.855 | -0.303 | 10.621 | 13.485 | 0.722 |
| early_week | ridge_epa_play_scores | 815 | 7.371 | 9.313 | 0.171 | 9.862 | 12.902 | -0.831 | 10.564 | 13.434 | 0.341 |
| early_week | ridge_epa_schedule_scores | 815 | 7.332 | 9.260 | 0.437 | 9.826 | 12.708 | -0.388 | 10.616 | 13.473 | 0.873 |
| early_week | ridge_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| pregame_refresh | league_scores | 815 | 7.710 | 9.688 | 0.658 | 10.597 | 13.773 | -0.587 | 10.783 | 13.630 | 1.317 |
| pregame_refresh | prior_team_scores | 815 | 7.370 | 9.296 | -0.075 | 9.909 | 12.921 | -0.649 | 10.456 | 13.368 | -0.150 |
| pregame_refresh | ridge_epa_play_schedule_scores | 815 | 7.368 | 9.315 | 0.348 | 9.922 | 12.865 | -0.316 | 10.602 | 13.475 | 0.695 |
| pregame_refresh | ridge_epa_play_scores | 815 | 7.365 | 9.305 | 0.165 | 9.862 | 12.881 | -0.834 | 10.555 | 13.431 | 0.329 |
| pregame_refresh | ridge_epa_schedule_scores | 815 | 7.334 | 9.266 | 0.436 | 9.846 | 12.727 | -0.419 | 10.599 | 13.472 | 0.873 |
| pregame_refresh | ridge_epa_scores | 815 | 7.331 | 9.255 | 0.213 | 9.805 | 12.754 | -0.933 | 10.523 | 13.415 | 0.426 |

## By season

| horizon | model | season | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 2022 | 271 | 7.428 | 9.315 | 1.097 | 9.563 | 12.333 | -0.488 | 11.293 | 13.964 | 2.194 |
| early_week | league_scores | 2023 | 272 | 7.890 | 9.993 | 1.051 | 11.117 | 14.439 | -1.088 | 11.014 | 13.818 | 2.101 |
| early_week | league_scores | 2024 | 272 | 7.812 | 9.743 | -0.165 | 11.107 | 14.432 | -0.174 | 10.043 | 13.093 | -0.329 |
| early_week | prior_team_scores | 2022 | 271 | 7.159 | 9.055 | 0.432 | 9.067 | 11.807 | -0.578 | 10.887 | 13.731 | 0.863 |
| early_week | prior_team_scores | 2023 | 272 | 7.537 | 9.526 | 0.099 | 10.378 | 13.496 | -1.124 | 10.522 | 13.447 | 0.197 |
| early_week | prior_team_scores | 2024 | 272 | 7.427 | 9.312 | -0.750 | 10.300 | 13.390 | -0.200 | 9.986 | 12.943 | -1.500 |
| early_week | ridge_epa_play_schedule_scores | 2022 | 271 | 7.264 | 9.116 | 1.027 | 9.089 | 11.775 | -0.659 | 11.118 | 13.920 | 2.055 |
| early_week | ridge_epa_play_schedule_scores | 2023 | 272 | 7.619 | 9.627 | 0.407 | 10.482 | 13.637 | -0.318 | 10.762 | 13.593 | 0.813 |
| early_week | ridge_epa_play_schedule_scores | 2024 | 272 | 7.215 | 9.194 | -0.349 | 10.103 | 13.079 | 0.068 | 9.987 | 12.924 | -0.697 |
| early_week | ridge_epa_play_scores | 2022 | 271 | 7.212 | 9.068 | 0.833 | 9.002 | 11.745 | -1.056 | 11.008 | 13.818 | 1.666 |
| early_week | ridge_epa_play_scores | 2023 | 272 | 7.622 | 9.618 | 0.130 | 10.453 | 13.679 | -1.156 | 10.697 | 13.524 | 0.259 |
| early_week | ridge_epa_play_scores | 2024 | 272 | 7.279 | 9.245 | -0.448 | 10.128 | 13.200 | -0.283 | 9.989 | 12.948 | -0.896 |
| early_week | ridge_epa_schedule_scores | 2022 | 271 | 7.297 | 9.143 | 1.235 | 9.214 | 11.789 | -1.025 | 11.226 | 13.978 | 2.470 |
| early_week | ridge_epa_schedule_scores | 2023 | 272 | 7.562 | 9.549 | 0.400 | 10.258 | 13.430 | -0.297 | 10.758 | 13.578 | 0.800 |
| early_week | ridge_epa_schedule_scores | 2024 | 272 | 7.138 | 9.081 | -0.322 | 10.003 | 12.845 | 0.155 | 9.866 | 12.840 | -0.644 |
| early_week | ridge_epa_scores | 2022 | 271 | 7.234 | 9.100 | 1.010 | 9.114 | 11.801 | -1.322 | 11.098 | 13.856 | 2.019 |
| early_week | ridge_epa_scores | 2023 | 272 | 7.557 | 9.537 | 0.058 | 10.232 | 13.465 | -1.179 | 10.643 | 13.510 | 0.117 |
| early_week | ridge_epa_scores | 2024 | 272 | 7.207 | 9.137 | -0.469 | 10.085 | 12.978 | -0.258 | 9.876 | 12.865 | -0.937 |
| pregame_refresh | league_scores | 2022 | 271 | 7.428 | 9.315 | 1.094 | 9.562 | 12.334 | -0.494 | 11.293 | 13.963 | 2.188 |
| pregame_refresh | league_scores | 2023 | 272 | 7.889 | 9.993 | 1.046 | 11.118 | 14.440 | -1.096 | 11.012 | 13.817 | 2.092 |
| pregame_refresh | league_scores | 2024 | 272 | 7.811 | 9.744 | -0.163 | 11.107 | 14.433 | -0.170 | 10.045 | 13.095 | -0.326 |
| pregame_refresh | prior_team_scores | 2022 | 271 | 7.146 | 9.045 | 0.418 | 9.039 | 11.794 | -0.615 | 10.886 | 13.718 | 0.837 |
| pregame_refresh | prior_team_scores | 2023 | 272 | 7.545 | 9.536 | 0.090 | 10.389 | 13.517 | -1.126 | 10.528 | 13.457 | 0.181 |
| pregame_refresh | prior_team_scores | 2024 | 272 | 7.419 | 9.299 | -0.732 | 10.294 | 13.379 | -0.205 | 9.956 | 12.918 | -1.464 |
| pregame_refresh | ridge_epa_play_schedule_scores | 2022 | 271 | 7.254 | 9.111 | 1.024 | 9.068 | 11.736 | -0.668 | 11.161 | 13.939 | 2.048 |
| pregame_refresh | ridge_epa_play_schedule_scores | 2023 | 272 | 7.617 | 9.629 | 0.374 | 10.551 | 13.677 | -0.262 | 10.694 | 13.558 | 0.747 |
| pregame_refresh | ridge_epa_play_schedule_scores | 2024 | 272 | 7.231 | 9.196 | -0.353 | 10.143 | 13.101 | -0.019 | 9.952 | 12.909 | -0.705 |
| pregame_refresh | ridge_epa_play_scores | 2022 | 271 | 7.215 | 9.064 | 0.836 | 8.965 | 11.701 | -1.066 | 11.052 | 13.845 | 1.672 |
| pregame_refresh | ridge_epa_play_scores | 2023 | 272 | 7.617 | 9.615 | 0.106 | 10.490 | 13.687 | -1.108 | 10.662 | 13.506 | 0.212 |
| pregame_refresh | ridge_epa_play_scores | 2024 | 272 | 7.264 | 9.226 | -0.446 | 10.126 | 13.167 | -0.330 | 9.953 | 12.928 | -0.892 |
| pregame_refresh | ridge_epa_schedule_scores | 2022 | 271 | 7.270 | 9.137 | 1.255 | 9.181 | 11.762 | -1.048 | 11.243 | 13.986 | 2.510 |
| pregame_refresh | ridge_epa_schedule_scores | 2023 | 272 | 7.580 | 9.570 | 0.373 | 10.328 | 13.494 | -0.277 | 10.708 | 13.574 | 0.747 |
| pregame_refresh | ridge_epa_schedule_scores | 2024 | 272 | 7.152 | 9.084 | -0.317 | 10.025 | 12.860 | 0.066 | 9.849 | 12.833 | -0.633 |
| pregame_refresh | ridge_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | ridge_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | ridge_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |

## Paired margin gains

| horizon | model | games | margin_mae_gain | week_bootstrap_gain_lower | week_bootstrap_gain_upper |
| --- | --- | --- | --- | --- | --- |
| early_week | ridge_epa_play_scores | 815 | -0.051 | -0.174 | 0.067 |
| early_week | ridge_epa_schedule_scores | 815 | -0.015 | -0.119 | 0.089 |
| early_week | ridge_epa_play_schedule_scores | 815 | -0.081 | -0.264 | 0.101 |
| pregame_refresh | ridge_epa_play_scores | 815 | -0.057 | -0.180 | 0.063 |
| pregame_refresh | ridge_epa_schedule_scores | 815 | -0.041 | -0.138 | 0.059 |
| pregame_refresh | ridge_epa_play_schedule_scores | 815 | -0.117 | -0.298 | 0.065 |

Positive gain means lower margin error than current EPA. Week-block ranges are approximate; serial dependence and repeated candidate selection are not corrected.
