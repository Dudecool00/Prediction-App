# Team EPA feature experiments

Retain the original EPA forecast model. The best early-week margin result adds scoring and
play volume, reducing MAE by only 0.011 points (about 0.1%). Its approximate week-block
gain range spans -0.058 to +0.078 points; seasonal gains are inconsistent and its refresh
gain is effectively zero. This comparison does not establish a reliable improvement.

| Early-week version | Margin MAE | Per-team score MAE |
| --- | ---: | ---: |
| Original EPA model | 9.811 | 7.333 |
| EPA + scoring history | 9.804 | 7.328 |
| EPA + scoring + play volume | 9.800 | 7.334 |
| EPA + opponent adjustment | 9.836 | 7.324 |
| EPA + scoring + volume + opponents | 9.825 | 7.320 |

The scoring additions slightly improve total error, but these fixed variants do not materially
improve the primary margin objective. The tested opponent variant worsens margin error; this
does not rule out other opponent adjustments. All 4,890 control rows match the prior saved
backtest exactly. No evaluation games or extreme outcomes were removed. Retained code, inputs,
prediction hashes and precise metrics are in [the summary](feature_experiments_summary.json).

Next experiments should define a small chronological tuning protocol for the training window,
recent-form decay and prior shrinkage, then assess compact availability/resting-starter context.
Do not select new hyperparameters by repeatedly choosing the best 2022–2024 aggregate result.

Four fixed ablations, identical games/cutoffs, Ridge alpha 20. Published forecasts retain the original model pending assessment.

2022–2024 are development seasons. Historical publication/revisions are not reconstructed. 2025 is already accessed. Gains are retrospective, not prospective validation.

Score MAE counts both team scores; margin/total count each game once. Higher paired gain means lower margin error versus the current EPA model.

## Overall

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 815 | 7.710 | 9.688 | 0.660 | 10.597 | 13.772 | -0.583 | 10.783 | 13.630 | 1.321 |
| early_week | prior_team_scores | 815 | 7.374 | 9.300 | -0.074 | 9.916 | 12.922 | -0.634 | 10.465 | 13.377 | -0.148 |
| early_week | ridge_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| early_week | ridge_epa_scoring_scores | 815 | 7.328 | 9.238 | 0.013 | 9.804 | 12.768 | -0.701 | 10.469 | 13.354 | 0.025 |
| early_week | ridge_epa_scoring_volume_opponent_scores | 815 | 7.320 | 9.263 | 0.154 | 9.825 | 12.797 | -0.271 | 10.496 | 13.396 | 0.307 |
| early_week | ridge_epa_scoring_volume_scores | 815 | 7.334 | 9.256 | -0.010 | 9.800 | 12.785 | -0.707 | 10.485 | 13.389 | -0.020 |
| early_week | ridge_opponent_epa_scores | 815 | 7.324 | 9.269 | 0.299 | 9.836 | 12.779 | -0.485 | 10.551 | 13.431 | 0.597 |
| pregame_refresh | league_scores | 815 | 7.710 | 9.688 | 0.658 | 10.597 | 13.773 | -0.587 | 10.783 | 13.630 | 1.317 |
| pregame_refresh | prior_team_scores | 815 | 7.370 | 9.296 | -0.075 | 9.909 | 12.921 | -0.649 | 10.456 | 13.368 | -0.150 |
| pregame_refresh | ridge_epa_scores | 815 | 7.331 | 9.255 | 0.213 | 9.805 | 12.754 | -0.933 | 10.523 | 13.415 | 0.426 |
| pregame_refresh | ridge_epa_scoring_scores | 815 | 7.325 | 9.234 | 0.025 | 9.804 | 12.758 | -0.705 | 10.456 | 13.354 | 0.051 |
| pregame_refresh | ridge_epa_scoring_volume_opponent_scores | 815 | 7.327 | 9.265 | 0.151 | 9.819 | 12.788 | -0.348 | 10.497 | 13.410 | 0.302 |
| pregame_refresh | ridge_epa_scoring_volume_scores | 815 | 7.330 | 9.252 | 0.000 | 9.805 | 12.773 | -0.710 | 10.478 | 13.387 | 0.001 |
| pregame_refresh | ridge_opponent_epa_scores | 815 | 7.328 | 9.266 | 0.316 | 9.819 | 12.763 | -0.560 | 10.544 | 13.437 | 0.632 |

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
| early_week | ridge_epa_scoring_scores | 2022 | 271 | 7.197 | 9.065 | 0.630 | 9.086 | 11.780 | -0.887 | 10.973 | 13.782 | 1.261 |
| early_week | ridge_epa_scoring_scores | 2023 | 272 | 7.534 | 9.482 | -0.039 | 10.245 | 13.422 | -1.075 | 10.513 | 13.397 | -0.078 |
| early_week | ridge_epa_scoring_scores | 2024 | 272 | 7.254 | 9.161 | -0.551 | 10.080 | 13.040 | -0.142 | 9.924 | 12.870 | -1.103 |
| early_week | ridge_epa_scoring_volume_opponent_scores | 2022 | 271 | 7.232 | 9.105 | 0.809 | 9.152 | 11.786 | -0.827 | 11.048 | 13.882 | 1.617 |
| early_week | ridge_epa_scoring_volume_opponent_scores | 2023 | 272 | 7.524 | 9.501 | 0.220 | 10.248 | 13.446 | -0.250 | 10.555 | 13.427 | 0.439 |
| early_week | ridge_epa_scoring_volume_opponent_scores | 2024 | 272 | 7.204 | 9.178 | -0.565 | 10.071 | 13.097 | 0.261 | 9.887 | 12.860 | -1.130 |
| early_week | ridge_epa_scoring_volume_scores | 2022 | 271 | 7.215 | 9.096 | 0.635 | 9.051 | 11.787 | -0.910 | 11.018 | 13.858 | 1.270 |
| early_week | ridge_epa_scoring_volume_scores | 2023 | 272 | 7.532 | 9.490 | -0.021 | 10.243 | 13.438 | -1.096 | 10.496 | 13.404 | -0.042 |
| early_week | ridge_epa_scoring_volume_scores | 2024 | 272 | 7.253 | 9.177 | -0.642 | 10.104 | 13.068 | -0.115 | 9.942 | 12.888 | -1.285 |
| early_week | ridge_opponent_epa_scores | 2022 | 271 | 7.262 | 9.116 | 1.195 | 9.203 | 11.794 | -1.154 | 11.158 | 13.904 | 2.390 |
| early_week | ridge_opponent_epa_scores | 2023 | 272 | 7.553 | 9.549 | 0.174 | 10.258 | 13.482 | -0.390 | 10.660 | 13.526 | 0.347 |
| early_week | ridge_opponent_epa_scores | 2024 | 272 | 7.157 | 9.137 | -0.470 | 10.043 | 12.998 | 0.086 | 9.839 | 12.843 | -0.939 |
| pregame_refresh | league_scores | 2022 | 271 | 7.428 | 9.315 | 1.094 | 9.562 | 12.334 | -0.494 | 11.293 | 13.963 | 2.188 |
| pregame_refresh | league_scores | 2023 | 272 | 7.889 | 9.993 | 1.046 | 11.118 | 14.440 | -1.096 | 11.012 | 13.817 | 2.092 |
| pregame_refresh | league_scores | 2024 | 272 | 7.811 | 9.744 | -0.163 | 11.107 | 14.433 | -0.170 | 10.045 | 13.095 | -0.326 |
| pregame_refresh | prior_team_scores | 2022 | 271 | 7.146 | 9.045 | 0.418 | 9.039 | 11.794 | -0.615 | 10.886 | 13.718 | 0.837 |
| pregame_refresh | prior_team_scores | 2023 | 272 | 7.545 | 9.536 | 0.090 | 10.389 | 13.517 | -1.126 | 10.528 | 13.457 | 0.181 |
| pregame_refresh | prior_team_scores | 2024 | 272 | 7.419 | 9.299 | -0.732 | 10.294 | 13.379 | -0.205 | 9.956 | 12.918 | -1.464 |
| pregame_refresh | ridge_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | ridge_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | ridge_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |
| pregame_refresh | ridge_epa_scoring_scores | 2022 | 271 | 7.180 | 9.057 | 0.649 | 9.058 | 11.746 | -0.889 | 10.988 | 13.789 | 1.298 |
| pregame_refresh | ridge_epa_scoring_scores | 2023 | 272 | 7.548 | 9.496 | -0.031 | 10.282 | 13.468 | -1.049 | 10.482 | 13.389 | -0.062 |
| pregame_refresh | ridge_epa_scoring_scores | 2024 | 272 | 7.245 | 9.145 | -0.540 | 10.069 | 12.993 | -0.178 | 9.898 | 12.871 | -1.079 |
| pregame_refresh | ridge_epa_scoring_volume_opponent_scores | 2022 | 271 | 7.232 | 9.105 | 0.877 | 9.109 | 11.754 | -0.804 | 11.093 | 13.907 | 1.754 |
| pregame_refresh | ridge_epa_scoring_volume_opponent_scores | 2023 | 272 | 7.540 | 9.514 | 0.200 | 10.265 | 13.474 | -0.338 | 10.537 | 13.437 | 0.400 |
| pregame_refresh | ridge_epa_scoring_volume_opponent_scores | 2024 | 272 | 7.210 | 9.169 | -0.621 | 10.079 | 13.068 | 0.096 | 9.864 | 12.866 | -1.241 |
| pregame_refresh | ridge_epa_scoring_volume_scores | 2022 | 271 | 7.196 | 9.084 | 0.655 | 9.032 | 11.752 | -0.892 | 11.034 | 13.854 | 1.310 |
| pregame_refresh | ridge_epa_scoring_volume_scores | 2023 | 272 | 7.548 | 9.501 | -0.011 | 10.279 | 13.474 | -1.074 | 10.474 | 13.400 | -0.021 |
| pregame_refresh | ridge_epa_scoring_volume_scores | 2024 | 272 | 7.247 | 9.164 | -0.641 | 10.101 | 13.028 | -0.165 | 9.927 | 12.891 | -1.282 |
| pregame_refresh | ridge_opponent_epa_scores | 2022 | 271 | 7.255 | 9.110 | 1.224 | 9.148 | 11.758 | -1.177 | 11.178 | 13.919 | 2.448 |
| pregame_refresh | ridge_opponent_epa_scores | 2023 | 272 | 7.564 | 9.553 | 0.175 | 10.268 | 13.487 | -0.461 | 10.631 | 13.534 | 0.350 |
| pregame_refresh | ridge_opponent_epa_scores | 2024 | 272 | 7.164 | 9.127 | -0.447 | 10.039 | 12.978 | -0.045 | 9.825 | 12.838 | -0.894 |

## Paired margin gains

| horizon | model | games | margin_mae_gain | week_bootstrap_gain_lower | week_bootstrap_gain_upper |
| --- | --- | --- | --- | --- | --- |
| early_week | ridge_epa_scoring_scores | 815 | 0.007 | -0.059 | 0.069 |
| early_week | ridge_epa_scoring_volume_scores | 815 | 0.011 | -0.058 | 0.078 |
| early_week | ridge_opponent_epa_scores | 815 | -0.025 | -0.117 | 0.065 |
| early_week | ridge_epa_scoring_volume_opponent_scores | 815 | -0.014 | -0.118 | 0.081 |
| pregame_refresh | ridge_epa_scoring_scores | 815 | 0.000 | -0.057 | 0.056 |
| pregame_refresh | ridge_epa_scoring_volume_scores | 815 | 0.000 | -0.058 | 0.057 |
| pregame_refresh | ridge_opponent_epa_scores | 815 | -0.014 | -0.102 | 0.069 |
| pregame_refresh | ridge_epa_scoring_volume_opponent_scores | 815 | -0.014 | -0.105 | 0.070 |

Week-block bootstrap ranges are approximate diagnostics. Serial dependence and candidate selection are not corrected.
