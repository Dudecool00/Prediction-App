# Weekly model simplicity follow-up

Adaptive development rule using existing predictions, not a fresh holdout. No new fits or 2025 outcomes.

2023-only selection for 2024: **compact_epa**.

| horizon | model | games | margin_mae | score_mae | total_mae |
| --- | --- | --- | --- | --- | --- |
| early_week | compact_availability | 272 | 10.208 | 7.208 | 9.928 |
| early_week | compact_combined | 272 | 10.179 | 7.262 | 9.997 |
| early_week | compact_epa | 272 | 10.209 | 7.209 | 9.925 |
| early_week | compact_temperature | 272 | 10.186 | 7.264 | 9.996 |
| early_week | ridge_epa_scores | 272 | 10.325 | 7.311 | 10.165 |
| early_week | simplicity_selected | 272 | 10.209 | 7.209 | 9.925 |
| pregame_refresh | compact_availability | 272 | 10.180 | 7.300 | 10.021 |
| pregame_refresh | compact_combined | 272 | 10.154 | 7.305 | 10.061 |
| pregame_refresh | compact_epa | 272 | 10.209 | 7.209 | 9.925 |
| pregame_refresh | compact_temperature | 272 | 10.177 | 7.229 | 9.951 |
| pregame_refresh | ridge_epa_scores | 272 | 10.325 | 7.311 | 10.165 |
| pregame_refresh | simplicity_selected | 272 | 10.209 | 7.209 | 9.925 |

Separate simulated pre-2025 research choice: **compact_epa**. Original choice **compact_availability** remains intact. Published model unchanged.

Context additions must earn at least 0.05 points against compact EPA, with score/total/season and paired-range guards. Among eligible candidates within one block standard error of the best margin MAE, prefer fewer declared inputs. The rule was added after seeing the old results; following-season separation does not undo that earlier access.

Enriched 2025 replay remains gated; this command performs zero 2025 fits.

## What this supports

All 272 following-season games are retained. Against the same-run 28-input control, the
selected compact model improves 2024 margin MAE by 0.115 points, with an approximate paired
season/week-block range of **−0.097 to +0.319**. The range includes no improvement. Score MAE
improves by 0.102 and total MAE by 0.240. These results support compact EPA as a manageable
research baseline; they do not establish a reliable accuracy breakthrough or justify promotion.

The candidate has identical early/refresh predictions because its EPA/history and coefficients
lock on Tuesday and it contains no refresh-specific context. A lower 2024 error from another
context candidate cannot change the choice already simulated from 2023. Both years had been
examined before this follow-up, so the following-season separation does not create an untouched
validation season or erase adaptive selection risk.

The separate pre-2025 choice uses 544 development games from 2023–2024. Compact EPA gains
0.147 margin points against the control (approximate range +0.023 to +0.271). The injury
candidate gains just 0.000735 against compact EPA, range 0.000 to +0.00222, failing the
new incremental gate. Temperature and combined context fail it as well. Across both
season-start audits, compact EPA has eight raw/nine encoded inputs and 20 allocated
weights/intercepts, versus injury's 16 raw/25 encoded and 52 allocated. Both initially
have nine active encoded inputs: parameter allocation alone does not prove overfitting.

No new penalty or estimator search was performed. No unavailable game was dropped to improve
the results, and no missing injury report became a healthy roster. The original recorded
`compact_availability` choice remains intact; this is a separate adaptive research record.

## Replay plan and reproduction

The audit/development/freeze stages have usable core model evidence. The enriched 2025
replay still needs dated roster/starter/injury versions and audited adapters. It has not run.
This follow-up uses no 2025 outcomes, performs no 2025 fits, and adds no weather collection.
Earlier 2025 graphics remain previously accessed diagnostic evidence.

Run `nfl-prop team-simplicity-check` after the existing weekly experiment. Local output uses
`reports/local/team_simplicity/`; the derived cache references upstream inputs and occupies
59,545 bytes. Original features and predictions are not copied or refitted. Source/policy,
upstream manifest, artifact and frozen-choice guards fail visibly for changes or corruption.
The actual recording time is current; the September 2024/2025 locks are simulated cutoffs.

[The comparison chart](comparison.html) uses Plotly's CDN. [Decision details](results.json)
include the adaptive rule, checks, source/input hashes, original complexity/coverage and
following-season errors. This curated JSON summarizes the local record; its Parquet artifact
stays ignored and is read through the processed manifest rather than from this report folder.
