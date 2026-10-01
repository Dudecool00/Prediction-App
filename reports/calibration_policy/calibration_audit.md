# Development calibration and candidate policy

Generated 2026-10-01T21:51:40.340366+00:00.

Candidate: `xgb_schedule`; status: `candidate_pending_freeze_and_holdout`. Below five prior model-sample games: research only; abstain prospectively.

## Candidate's nominal 90% intervals by pregame history

| history_bucket | n | distinct_players | distinct_games | distinct_weeks | pooled_coverage | pooled_mean_width | matched_available_n | matched_coverage | paired_pooled_coverage | matched_mean_width |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0: no sample history | 29 | 29 | 29 | 15 | 0.828 | 283.032 | 0 | None | None | None |
| 1-4 prior games | 139 | 48 | 124 | 36 | 0.849 | 282.565 | 61 | 0.869 | 0.770 | 362.056 |
| 5+ prior games | 1159 | 79 | 539 | 36 | 0.904 | 278.480 | 1159 | 0.898 | 0.904 | 271.722 |

Matched coverage and paired pooled coverage use the same available rows. A missing matched value means insufficient same-history residuals; it is not zero coverage.

## Candidate's per-fold calibration support

| history_bucket | min_residuals | median_residuals | max_residuals | folds_with_30_residuals |
| --- | --- | --- | --- | --- |
| 0: no sample history | 0 | 5.000 | 18 | 0 |
| 1-4 prior games | 9 | 26.000 | 65 | 13 |
| 5+ prior games | 149 | 191.000 | 208 | 36 |

Regenerated full JSON contains all six models, 50/80/90% intervals, season splits, probability scores and reliability bins by history, source hashes, and the full policy.

## Limits

- All rows condition on recorded QB participation, including backups and early exits; this is not a verified pregame starter cohort.
- Counts of rows are not independent sample sizes. Players repeat, QBs share games, and adjacent six-week calibration windows overlap. No confidence or conformal guarantee is asserted.
- History-matched intervals require 30 same-bucket residuals and are diagnostic only. Unavailable intervals stay unavailable; paired pooled metrics use exactly the same available subset.
- History buckets use each game's pregame model-sample count, not age, career experience, today's depth charts, or the current outcome. Probability lines are fixed diagnostics, not sportsbook lines.
- Candidate selection and these checks use development outcomes. The policy is not frozen, 2025 remains closed, and no production forecast or EV is enabled.

## Provenance

Policy SHA-256: `bdd760259e84d29d290dc1512bfb36348eb6170321c077d1a736d87a8a8669a3`.

Research manifest SHA-256: `dddadb88b2c4479b51c24b51568f2c8c30c9981f5f1b499de2223476009b7ac1`.

Derived from [nflverse](https://github.com/nflverse/nflverse-data) data, distributed under [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md).
