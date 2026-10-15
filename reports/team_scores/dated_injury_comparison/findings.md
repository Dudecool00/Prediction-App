# Dated injury development comparison

Recorded October 15, 2026. **Retain compact EPA. These coarse injury counts do not earn
their added parameters.** The exact saved EPA control is reproduced for all 1,088
game/horizon predictions. No outcomes or fits from 2025 enter this comparison.

2022 initialization; every 2023-2024 game; Tuesday-locked history and fits. This limited research test controls for missing reports and does not establish complete rosters or starters.

| horizon | model | games | margin_mae | score_mae | total_mae |
| --- | --- | --- | --- | --- | --- |
| early_week | compact_dated_out_counts | 544 | 10.335 | 7.383 | 10.279 |
| early_week | compact_epa | 544 | 10.335 | 7.383 | 10.279 |
| early_week | compact_report_flags | 544 | 10.335 | 7.383 | 10.279 |
| pregame_refresh | compact_dated_out_counts | 544 | 10.395 | 7.439 | 10.356 |
| pregame_refresh | compact_epa | 544 | 10.335 | 7.383 | 10.279 |
| pregame_refresh | compact_report_flags | 544 | 10.372 | 7.415 | 10.315 |

2023-only choice for 2024: **compact_epa**. Separate pre-2025 research choice: **compact_epa**.

Counts must improve refresh margin MAE by at least 0.05 against both EPA and flags-only, with a positive paired lower bound and score/total/season guards. Both-covered results are secondary; no uncovered games are removed from primary results. Unknown contrasts use neutral zero with identical availability controls in both context models.

The added rule and source sample are adaptive development. 2023/2024 were already examined. No 2025 outcomes or fits enter this command; original model choices and enriched replay gate remain intact. Full roster/starter timing and historical weather adapters remain required before that replay. The PEM-inspired comparison follows completion of the original plan.

## Evidence and limits

Refresh margin MAE worsens by **0.060 points** with injury counts versus compact EPA.
The approximate paired season/week-block gain range is -0.140 to +0.012, including no
improvement. Versus report-availability flags alone, the injury gain is -0.024 with a
range of -0.041 to -0.009. Score and total errors also worsen versus compact EPA.
The counts worsen the margin point estimate in both 2023 (10.534 versus 10.461) and
2024 (10.257 versus 10.209). These ranges do not correct earlier adaptive exploration.

The secondary both-position-covered sample contains 207 games: 149 in 2023 and 58 in
2024. Its refresh margin MAE is 10.064 for injury counts, 10.039 for compact EPA, and
10.010 for availability-only. Selecting this subset does not rescue the injury result;
all 544 games remain in the primary evaluation. No Tuesday formal reports qualify in
any of the three source seasons, so all three early forecasts are identical.

2022 initialization uniformly uses canonical news editions: **355/542** team appearances
have formal refresh reports, and 157/271 games have both position-count reports. The
unchanged parser/receipt checks determine which editions may supply counts; failed checks
remain unknown. Out positions remain unknown in six formal slots. The 2023-2024 inputs reuse
the independently verified canonical/tracking audit, with 330/544 and 127/544 formal reports
respectively.

One of 41 optional 2022 tracking indexes remained unavailable after bounded timeout/503
retries. Before these real fits, the protocol was clarified to exclude **all** 2022
tracking editions consistently, rather than select a partially collected sample. Raw
attempts remain preserved locally. The source samples differ by season and do not claim
complete injury, active/IR roster or starter coverage. A listed QB Out is not necessarily
the starting quarterback. This result does not establish that reliable starter/availability
information cannot help another properly evaluated model.

The candidates have 8, 13 and 15 raw inputs; the recorded season-start fits allocate
20, 30 and 34 weights/intercepts respectively, including preprocessing indicators.
The new 2022 raw namespace occupies **5,005,019 bytes**, including unused tracking proofs;
the complete new research cache occupies **173,889 bytes**. No environment or copied
training tables per fold were added. The [comparison chart](comparison.html) uses Plotly's
CDN; [exact results and source digests](results.json) retain the frozen policy and folds.

## Remaining original plan

The roster/starter timing and full historical forecast-weather adapters remain unresolved.
The original enriched-replay check was rerun and still blocks; the newer dated news sources
provide partial evidence, not complete replacements for those requirements. Preserve the
compact EPA choice while resolving those adapters, then freeze and run the complete 2025
weekly score/margin replay with predicted-versus-actual graphics. 2025 remains previously
examined diagnostic data. The PEM-inspired comparison follows that original work.
