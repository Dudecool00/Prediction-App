# EPA with QB history and injury observations

Retain current EPA. No tested context variant establishes a dependable margin improvement.
All 4,890 league/prior-score/EPA control predictions match the initial comparison exactly.

| Version | Early-week margin MAE | Refresh margin MAE | Refresh per-team score MAE |
| --- | ---: | ---: | ---: |
| Current EPA | 9.811 | 9.805 | 7.331 |
| EPA + prior QB history | 9.819 | 9.801 | 7.322 |
| EPA + injury observations | 9.815 | 9.842 | 7.374 |
| EPA + QB history + injuries | 9.822 | 9.853 | 7.368 |

The QB-history refresh gain is only 0.003 points, with approximate paired week-block range
[-0.103, +0.117]. It does not support a forecast-model change. Injury-count variants worsen
refresh margin and score error. These results evaluate the stated proxy/count features,
not the general value of knowing the actual starting lineup.

The historical cache reconciles all 4,734 2016–2024 team-games, with 6,041 passer rows and
47,640 injury rows. Missing passer EPA remains missing (236 rows). Seventeen injury rows
outside the schedule are counted and excluded; six ambiguous Note statuses are retained as
unknown. NFL season/week values are validated as integers before schema normalization.

Early-week forecasts have eligible partial injury observations in only **2 of 1,630 team
slots**. Refresh has them in **1,617 of 1,630**, but the prior primary passer is reported Out
in only 26 slots, and explicit Rest/Out observations occur in only four. Missing or absent
reports are not complete injury/IR rosters. The 24-hour timing assumption can exclude a
later modified row even if an earlier report once existed; overwritten states are not rebuilt.

The next information layer should use timestamped current-starter announcements and coaching
rest plans with independent coverage tracking. Historical weekly depth charts and retrospective
starter audits cannot establish exact pregame publication. Prospective source capture will
be needed to assess those inputs without later-game knowledge. The current forecast model
remains unchanged. See [the summary](qb_injury_context_summary.json) for exact metrics, audits,
feature policies, source/code hashes and prediction artifacts.

Three fixed additions to the current EPA model, same games/cutoffs and Ridge alpha 20.

QB identity is a past-primary-passer proxy, not today's confirmed starter. Injury observations require source modification plus 24 hours before the cutoff. Missing observations remain unknown. Historical publication/revisions are not reconstructed.

Rest flags require an explicit Rest reason on an Out report. Coaching announcements are not reconstructed. This research comparison does not change published forecasts.

2022–2024 are development seasons; 2025 is already accessed.

## Coverage

| horizon | games | home_qb_known_games | away_qb_known_games | home_observed_report_games | away_observed_report_games | home_reported_primary_out_games | away_reported_primary_out_games | home_rest_report_games | away_rest_report_games |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | 815 | 765 | 762 | 1 | 1 | 0 | 0 | 0 | 0 |
| pregame_refresh | 815 | 767 | 766 | 807 | 810 | 15 | 11 | 3 | 1 |

## Overall

| horizon | model | games | score_mae | score_rmse | score_bias | margin_mae | margin_rmse | margin_bias | total_mae | total_rmse | total_bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| early_week | league_scores | 815 | 7.710 | 9.688 | 0.660 | 10.597 | 13.772 | -0.583 | 10.783 | 13.630 | 1.321 |
| early_week | prior_team_scores | 815 | 7.374 | 9.300 | -0.074 | 9.916 | 12.922 | -0.634 | 10.465 | 13.377 | -0.148 |
| early_week | ridge_epa_injury_scores | 815 | 7.334 | 9.260 | 0.185 | 9.815 | 12.774 | -0.902 | 10.531 | 13.410 | 0.369 |
| early_week | ridge_epa_qb_history_scores | 815 | 7.326 | 9.241 | 0.120 | 9.819 | 12.742 | -1.012 | 10.493 | 13.388 | 0.240 |
| early_week | ridge_epa_qb_injury_scores | 815 | 7.327 | 9.242 | 0.107 | 9.822 | 12.750 | -0.990 | 10.485 | 13.383 | 0.214 |
| early_week | ridge_epa_scores | 815 | 7.333 | 9.260 | 0.199 | 9.811 | 12.768 | -0.919 | 10.538 | 13.416 | 0.398 |
| pregame_refresh | league_scores | 815 | 7.710 | 9.688 | 0.658 | 10.597 | 13.773 | -0.587 | 10.783 | 13.630 | 1.317 |
| pregame_refresh | prior_team_scores | 815 | 7.370 | 9.296 | -0.075 | 9.909 | 12.921 | -0.649 | 10.456 | 13.368 | -0.150 |
| pregame_refresh | ridge_epa_injury_scores | 815 | 7.374 | 9.283 | 0.285 | 9.842 | 12.730 | -0.874 | 10.558 | 13.516 | 0.569 |
| pregame_refresh | ridge_epa_qb_history_scores | 815 | 7.322 | 9.235 | 0.151 | 9.801 | 12.707 | -0.986 | 10.517 | 13.406 | 0.302 |
| pregame_refresh | ridge_epa_qb_injury_scores | 815 | 7.368 | 9.285 | 0.198 | 9.853 | 12.725 | -0.928 | 10.550 | 13.524 | 0.396 |
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
| early_week | ridge_epa_injury_scores | 2022 | 271 | 7.231 | 9.097 | 0.994 | 9.115 | 11.798 | -1.307 | 11.088 | 13.849 | 1.988 |
| early_week | ridge_epa_injury_scores | 2023 | 272 | 7.558 | 9.537 | 0.044 | 10.232 | 13.461 | -1.159 | 10.649 | 13.513 | 0.089 |
| early_week | ridge_epa_injury_scores | 2024 | 272 | 7.212 | 9.140 | -0.482 | 10.096 | 13.000 | -0.240 | 9.858 | 12.850 | -0.963 |
| early_week | ridge_epa_qb_history_scores | 2022 | 271 | 7.190 | 9.076 | 0.991 | 9.165 | 11.881 | -1.357 | 10.872 | 13.724 | 1.982 |
| early_week | ridge_epa_qb_history_scores | 2023 | 272 | 7.556 | 9.505 | -0.147 | 10.222 | 13.366 | -1.389 | 10.650 | 13.517 | -0.293 |
| early_week | ridge_epa_qb_history_scores | 2024 | 272 | 7.232 | 9.137 | -0.481 | 10.068 | 12.931 | -0.291 | 9.959 | 12.912 | -0.962 |
| early_week | ridge_epa_qb_injury_scores | 2022 | 271 | 7.189 | 9.074 | 0.975 | 9.164 | 11.877 | -1.331 | 10.869 | 13.721 | 1.949 |
| early_week | ridge_epa_qb_injury_scores | 2023 | 272 | 7.555 | 9.506 | -0.159 | 10.217 | 13.363 | -1.361 | 10.653 | 13.523 | -0.318 |
| early_week | ridge_epa_qb_injury_scores | 2024 | 272 | 7.235 | 9.140 | -0.491 | 10.083 | 12.960 | -0.278 | 9.934 | 12.891 | -0.983 |
| early_week | ridge_epa_scores | 2022 | 271 | 7.234 | 9.100 | 1.010 | 9.114 | 11.801 | -1.322 | 11.098 | 13.856 | 2.019 |
| early_week | ridge_epa_scores | 2023 | 272 | 7.557 | 9.537 | 0.058 | 10.232 | 13.465 | -1.179 | 10.643 | 13.510 | 0.117 |
| early_week | ridge_epa_scores | 2024 | 272 | 7.207 | 9.137 | -0.469 | 10.085 | 12.978 | -0.258 | 9.876 | 12.865 | -0.937 |
| pregame_refresh | league_scores | 2022 | 271 | 7.428 | 9.315 | 1.094 | 9.562 | 12.334 | -0.494 | 11.293 | 13.963 | 2.188 |
| pregame_refresh | league_scores | 2023 | 272 | 7.889 | 9.993 | 1.046 | 11.118 | 14.440 | -1.096 | 11.012 | 13.817 | 2.092 |
| pregame_refresh | league_scores | 2024 | 272 | 7.811 | 9.744 | -0.163 | 11.107 | 14.433 | -0.170 | 10.045 | 13.095 | -0.326 |
| pregame_refresh | prior_team_scores | 2022 | 271 | 7.146 | 9.045 | 0.418 | 9.039 | 11.794 | -0.615 | 10.886 | 13.718 | 0.837 |
| pregame_refresh | prior_team_scores | 2023 | 272 | 7.545 | 9.536 | 0.090 | 10.389 | 13.517 | -1.126 | 10.528 | 13.457 | 0.181 |
| pregame_refresh | prior_team_scores | 2024 | 272 | 7.419 | 9.299 | -0.732 | 10.294 | 13.379 | -0.205 | 9.956 | 12.918 | -1.464 |
| pregame_refresh | ridge_epa_injury_scores | 2022 | 271 | 7.272 | 9.141 | 1.004 | 9.097 | 11.616 | -1.355 | 11.235 | 14.119 | 2.007 |
| pregame_refresh | ridge_epa_injury_scores | 2023 | 272 | 7.665 | 9.594 | 0.201 | 10.374 | 13.577 | -1.147 | 10.582 | 13.560 | 0.402 |
| pregame_refresh | ridge_epa_injury_scores | 2024 | 272 | 7.183 | 9.105 | -0.348 | 10.053 | 12.914 | -0.122 | 9.860 | 12.840 | -0.696 |
| pregame_refresh | ridge_epa_qb_history_scores | 2022 | 271 | 7.168 | 9.052 | 1.027 | 9.107 | 11.783 | -1.365 | 10.925 | 13.744 | 2.054 |
| pregame_refresh | ridge_epa_qb_history_scores | 2023 | 272 | 7.570 | 9.527 | -0.123 | 10.275 | 13.420 | -1.319 | 10.650 | 13.525 | -0.247 |
| pregame_refresh | ridge_epa_qb_history_scores | 2024 | 272 | 7.226 | 9.120 | -0.448 | 10.019 | 12.860 | -0.277 | 9.978 | 12.936 | -0.895 |
| pregame_refresh | ridge_epa_qb_injury_scores | 2022 | 271 | 7.252 | 9.135 | 0.991 | 9.214 | 11.713 | -1.407 | 11.069 | 14.021 | 1.982 |
| pregame_refresh | ridge_epa_qb_injury_scores | 2023 | 272 | 7.650 | 9.580 | -0.030 | 10.376 | 13.521 | -1.246 | 10.638 | 13.575 | -0.060 |
| pregame_refresh | ridge_epa_qb_injury_scores | 2024 | 272 | 7.202 | 9.132 | -0.364 | 9.967 | 12.873 | -0.132 | 9.944 | 12.957 | -0.728 |
| pregame_refresh | ridge_epa_scores | 2022 | 271 | 7.219 | 9.089 | 1.018 | 9.068 | 11.765 | -1.363 | 11.101 | 13.857 | 2.035 |
| pregame_refresh | ridge_epa_scores | 2023 | 272 | 7.573 | 9.546 | 0.058 | 10.269 | 13.491 | -1.150 | 10.610 | 13.510 | 0.117 |
| pregame_refresh | ridge_epa_scores | 2024 | 272 | 7.202 | 9.122 | -0.434 | 10.075 | 12.942 | -0.286 | 9.861 | 12.860 | -0.869 |

## Paired margin gains

| horizon | model | games | margin_mae_gain | week_bootstrap_gain_lower | week_bootstrap_gain_upper |
| --- | --- | --- | --- | --- | --- |
| early_week | ridge_epa_qb_history_scores | 815 | -0.008 | -0.108 | 0.099 |
| early_week | ridge_epa_injury_scores | 815 | -0.004 | -0.012 | 0.003 |
| early_week | ridge_epa_qb_injury_scores | 815 | -0.011 | -0.113 | 0.097 |
| pregame_refresh | ridge_epa_qb_history_scores | 815 | 0.003 | -0.103 | 0.117 |
| pregame_refresh | ridge_epa_injury_scores | 815 | -0.037 | -0.184 | 0.099 |
| pregame_refresh | ridge_epa_qb_injury_scores | 815 | -0.048 | -0.203 | 0.107 |

Positive gain means lower margin error versus current EPA. Week-block ranges are approximate; serial dependence/candidate selection are not corrected.
