# 2025 team EPA diagnostic

Saved expanding chronological predictions from the unchanged model. Already accessed diagnostic data; not an untouched holdout. No model selection or recalibration applied.

## Metrics

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 272 | 7.875 | 9.887 | -0.222 | 11.060 | 14.162 | -0.299 | 10.973 | 13.800 | -0.443 |
| early_week | prior_team_scores | 272 | 7.519 | 9.414 | 0.030 | 10.328 | 13.142 | -0.440 | 10.681 | 13.482 | 0.061 |
| early_week | ridge_epa_scores | 272 | 7.559 | 9.449 | 0.255 | 10.483 | 13.209 | -0.575 | 10.732 | 13.515 | 0.509 |
| pregame_refresh | league_scores | 272 | 7.875 | 9.887 | -0.221 | 11.059 | 14.162 | -0.298 | 10.972 | 13.799 | -0.442 |
| pregame_refresh | prior_team_scores | 272 | 7.515 | 9.405 | 0.025 | 10.315 | 13.125 | -0.458 | 10.701 | 13.475 | 0.050 |
| pregame_refresh | ridge_epa_scores | 272 | 7.540 | 9.417 | 0.244 | 10.447 | 13.154 | -0.560 | 10.715 | 13.480 | 0.488 |

## Error distribution and prediction spread

| horizon | games | margin_mae | margin_rmse | margin_bias | median_absolute_error | p90_absolute_error | predicted_margin_sd | actual_margin_sd | largest_10_errors_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | 272 | 10.483 | 13.209 | -0.575 | 8.550 | 22.890 | 6.269 | 14.151 | 0.114 |
| pregame_refresh | 272 | 10.447 | 13.154 | -0.560 | 8.550 | 22.590 | 6.299 | 14.151 | 0.114 |

## Outcome bands (postgame description)

| horizon | outcome_band | games | margin_mae | absolute_error_points |
| --- | --- | --- | --- | --- |
| early_week | Actual margin 0–7 | 135 | 5.654 | 763.300 |
| early_week | Actual margin 21+ | 49 | 21.904 | 1073.300 |
| early_week | Actual margin 8–20 | 88 | 11.531 | 1014.700 |
| pregame_refresh | Actual margin 0–7 | 135 | 5.659 | 763.900 |
| pregame_refresh | Actual margin 21+ | 49 | 21.788 | 1067.600 |
| pregame_refresh | Actual margin 8–20 | 88 | 11.478 | 1010.100 |

## Largest errors (retained in all metrics)

| horizon | week | away_team | home_team | predicted_away_score | predicted_home_score | away_score | home_score | predicted_home_margin | target_home_margin | absolute_margin_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | 5 | HOU | BAL | 21.500 | 27.700 | 44.000 | 10.000 | 6.200 | -34.000 | 40.200 |
| early_week | 3 | CIN | MIN | 23.000 | 20.900 | 10.000 | 48.000 | -2.100 | 38.000 | 40.100 |
| early_week | 3 | ATL | CAR | 27.500 | 19.600 | 0.000 | 30.000 | -7.900 | 30.000 | 37.900 |
| early_week | 14 | WAS | MIN | 22.000 | 19.000 | 0.000 | 31.000 | -3.000 | 31.000 | 34.000 |
| early_week | 11 | LAC | JAX | 23.800 | 21.500 | 6.000 | 35.000 | -2.300 | 29.000 | 31.300 |
| early_week | 16 | CIN | MIA | 19.800 | 25.500 | 45.000 | 21.000 | 5.700 | -24.000 | 29.700 |
| early_week | 8 | MIA | ATL | 20.800 | 25.800 | 34.000 | 10.000 | 5.000 | -24.000 | 29.000 |
| early_week | 7 | LA | JAX | 22.200 | 21.700 | 35.000 | 7.000 | -0.500 | -28.000 | 27.500 |
| early_week | 7 | MIA | CLE | 23.000 | 20.600 | 6.000 | 31.000 | -2.400 | 25.000 | 27.400 |
| early_week | 1 | MIA | IND | 23.700 | 21.800 | 8.000 | 33.000 | -1.900 | 25.000 | 26.900 |
| pregame_refresh | 3 | CIN | MIN | 23.100 | 21.000 | 10.000 | 48.000 | -2.100 | 38.000 | 40.100 |
| pregame_refresh | 5 | HOU | BAL | 21.600 | 27.700 | 44.000 | 10.000 | 6.100 | -34.000 | 40.100 |
| pregame_refresh | 3 | ATL | CAR | 27.600 | 19.800 | 0.000 | 30.000 | -7.800 | 30.000 | 37.800 |
| pregame_refresh | 14 | WAS | MIN | 22.200 | 18.900 | 0.000 | 31.000 | -3.300 | 31.000 | 34.300 |
| pregame_refresh | 11 | LAC | JAX | 23.900 | 21.500 | 6.000 | 35.000 | -2.400 | 29.000 | 31.400 |
| pregame_refresh | 16 | CIN | MIA | 20.500 | 25.900 | 45.000 | 21.000 | 5.400 | -24.000 | 29.400 |
| pregame_refresh | 8 | MIA | ATL | 20.700 | 25.900 | 34.000 | 10.000 | 5.200 | -24.000 | 29.200 |
| pregame_refresh | 7 | LA | JAX | 22.100 | 21.600 | 35.000 | 7.000 | -0.500 | -28.000 | 27.500 |
| pregame_refresh | 7 | MIA | CLE | 23.100 | 20.700 | 6.000 | 31.000 | -2.400 | 25.000 | 27.400 |
| pregame_refresh | 10 | BUF | MIA | 28.900 | 18.900 | 13.000 | 30.000 | -10.000 | 17.000 | 27.000 |

Open diagnostics.html for predicted/actual score and margin plots, weekly baseline comparisons, team/week heatmaps and selectable team trajectories. game_predictions.csv retains every prediction.

Source revisions and exact historical publication are not reconstructed. Results require kickoff plus 24 hours before entering training or features. Outcome-selected subgroups describe errors and cannot be treated as pregame filters or evidence of a causal explanation.
