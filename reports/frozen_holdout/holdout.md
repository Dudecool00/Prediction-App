# Frozen 2025 QB passing-yards holdout diagnostic

Completed: 2026-10-06T22:47:22.014436+00:00

Frozen before access: 2026-10-06T22:45:14.093990+00:00; first access began: 2026-10-06T22:47:18.764404+00:00.

Freeze SHA-256: `73fe5919573033cd3e604461a1a7c03a7472ad984d9700aa873d322d1322016d`

One fixed schedule XGBoost estimator and its development residual pool are used for every 2025 appearance. No refitting, recalibration, tuning or row filtering uses holdout outcomes. Earlier available outcomes supply strictly lagged inputs only.

The cohort is conditioned on recorded participation and includes backups, early exits, zero attempts and sparse history. The five-game subgroup is a history diagnostic. This does not validate prospective starters, playing time or betting EV. Production stays disabled; 2025 is now an accessed holdout and cannot be reused as untouched validation for a revised model.

## Counts

| qb_games | players | games | weeks | history_eligible_rows | scored_rows | dropped_evaluation_rows |
| --- | --- | --- | --- | --- | --- | --- |
| 664 | 81 | 272 | 18 | 594 | 664 | 0 |

## Point errors (yards)

| population | n | distinct_players | distinct_games | distinct_weeks | mae | rmse | bias |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5+ prior available games | 594 | 70 | 272 | 18 | 65.292 | 82.740 | 8.789 |
| all recorded appearances | 664 | 81 | 272 | 18 | 66.119 | 83.043 | 9.328 |

## Interval coverage and width

| population | coverage | n | distinct_players | distinct_games | distinct_weeks | observed_coverage | mean_width |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5+ prior available games | 0.500 | 594 | 70 | 272 | 18 | 0.532 | 117.793 |
| 5+ prior available games | 0.800 | 594 | 70 | 272 | 18 | 0.857 | 229.180 |
| 5+ prior available games | 0.900 | 594 | 70 | 272 | 18 | 0.907 | 278.478 |
| all recorded appearances | 0.500 | 664 | 81 | 272 | 18 | 0.520 | 117.793 |
| all recorded appearances | 0.800 | 664 | 81 | 272 | 18 | 0.857 | 229.180 |
| all recorded appearances | 0.900 | 664 | 81 | 272 | 18 | 0.910 | 278.478 |

## Fixed-threshold probability scores

| population | line | n | distinct_players | distinct_games | distinct_weeks | brier | log_loss |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5+ prior available games | 150.500 | 594 | 70 | 272 | 18 | 0.152 | 0.476 |
| 5+ prior available games | 200.500 | 594 | 70 | 272 | 18 | 0.217 | 0.621 |
| 5+ prior available games | 250.500 | 594 | 70 | 272 | 18 | 0.193 | 0.562 |
| 5+ prior available games | 300.500 | 594 | 70 | 272 | 18 | 0.097 | 0.335 |
| all recorded appearances | 150.500 | 664 | 81 | 272 | 18 | 0.156 | 0.486 |
| all recorded appearances | 200.500 | 664 | 81 | 272 | 18 | 0.210 | 0.605 |
| all recorded appearances | 250.500 | 664 | 81 | 272 | 18 | 0.181 | 0.534 |
| all recorded appearances | 300.500 | 664 | 81 | 272 | 18 | 0.089 | 0.308 |

## Exclusions and provenance

Complete week/history/reliability diagnostics, exclusions, source URLs/licenses, retrieval times, input/output hashes, frozen protocol and environment are in holdout.json. Source retrieval is retrospective; kickoff plus 24 hours remains an availability assumption. The static model protocol differs from weekly-refit development research; do not pool their metrics.

Model SHA-256: `c178735611d585265048c0bfb28c5cd6a48f91b21a4c4c2910703f7186374069`

Calibration SHA-256: `4c5e485de74e2a7ee717da882180afe1c3e995511cbb6da1362a4c5e6bb38026`

Repeated players/shared games/time dependence limit independent-sample claims. Coverage is empirical, threshold probabilities are diagnostics rather than historical sportsbook lines, and no ROI or profitability claim is established.

A completed invocation reuses the saved checksummed report. An interrupted or failed attempt requires explicit --resume under the same freeze and retains its audit trail.
