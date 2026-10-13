# Team EPA score and margin backtest

Joint final-score projections; each margin comes from the same rounded scores.

Retrospective simulation with kickoff-plus-24-hour availability. Historical source revisions are not reconstructed. 2025 is already accessed, not a fresh holdout.

Score error counts both team scores; margin and total error count each game once. Forecast horizons are scored separately. No calibrated probabilities yet.

## Overall metrics

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 815 | 7.710 | 9.688 | 0.660 | 10.597 | 13.772 | -0.583 | 10.783 | 13.630 | 1.321 |
| early_week | prior_team_scores | 815 | 7.374 | 9.300 | -0.074 | 9.916 | 12.922 | -0.634 | 10.465 | 13.377 | -0.148 |
| early_week | ridge_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| pregame_refresh | league_scores | 815 | 7.710 | 9.688 | 0.658 | 10.597 | 13.773 | -0.587 | 10.783 | 13.630 | 1.317 |
| pregame_refresh | prior_team_scores | 815 | 7.370 | 9.296 | -0.075 | 9.909 | 12.921 | -0.649 | 10.456 | 13.368 | -0.150 |
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
| early_week | ridge_epa_scores | 2022 | 271 | 7.234 | 9.100 | 1.010 | 9.114 | 11.801 | -1.322 | 11.098 | 13.856 | 2.019 |
| early_week | ridge_epa_scores | 2023 | 272 | 7.557 | 9.537 | 0.058 | 10.232 | 13.465 | -1.179 | 10.643 | 13.510 | 0.117 |
| early_week | ridge_epa_scores | 2024 | 272 | 7.207 | 9.137 | -0.469 | 10.085 | 12.978 | -0.258 | 9.876 | 12.865 | -0.937 |
| pregame_refresh | league_scores | 2022 | 271 | 7.428 | 9.315 | 1.094 | 9.562 | 12.334 | -0.494 | 11.293 | 13.963 | 2.188 |
| pregame_refresh | league_scores | 2023 | 272 | 7.889 | 9.993 | 1.046 | 11.118 | 14.440 | -1.096 | 11.012 | 13.817 | 2.092 |
| pregame_refresh | league_scores | 2024 | 272 | 7.811 | 9.744 | -0.163 | 11.107 | 14.433 | -0.170 | 10.045 | 13.095 | -0.326 |
| pregame_refresh | prior_team_scores | 2022 | 271 | 7.146 | 9.045 | 0.418 | 9.039 | 11.794 | -0.615 | 10.886 | 13.718 | 0.837 |
| pregame_refresh | prior_team_scores | 2023 | 272 | 7.545 | 9.536 | 0.090 | 10.389 | 13.517 | -1.126 | 10.528 | 13.457 | 0.181 |
| pregame_refresh | prior_team_scores | 2024 | 272 | 7.419 | 9.299 | -0.732 | 10.294 | 13.379 | -0.205 | 9.956 | 12.918 | -1.464 |
| pregame_refresh | ridge_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | ridge_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | ridge_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |
