# Archived weather development comparison

2022 initialization, all development games, Tuesday-locked EPA and fits, no 2025 evaluation.

| horizon | model | games | margin_mae | score_mae | total_mae |
| --- | --- | --- | --- | --- | --- |
| early_week | compact_archived_weather | 544 | 10.418 | 7.469 | 10.396 |
| early_week | compact_epa | 544 | 10.335 | 7.383 | 10.279 |
| early_week | compact_weather_flags | 544 | 10.339 | 7.441 | 10.397 |
| pregame_refresh | compact_archived_weather | 544 | 10.428 | 7.431 | 10.250 |
| pregame_refresh | compact_epa | 544 | 10.335 | 7.383 | 10.279 |
| pregame_refresh | compact_weather_flags | 544 | 10.340 | 7.439 | 10.393 |

2023-only choice for 2024: **compact_epa**. Separate pre-2025 research choice: **compact_epa**.

The saved compact-EPA control is reproduced exactly for all 1,088 game/horizon predictions.
The experiment performs 216 fits across 72 season/week/horizon folds, with every development
game retained. Early-week weather worsens margin MAE by 0.083 points and refresh weather by
0.093. Paired approximate 95% season/week-block **gain** ranges versus EPA are
**−0.154 to −0.019** early and **−0.176 to −0.010** at refresh; negative means worse.
Weather also loses to the venue/coverage control: gains are −0.079 and −0.088 respectively.
These intervals do not correct the project's earlier adaptive exploration.

Both development seasons have worse weather margin point estimates. Early-week MAE is
10.617 versus EPA's 10.461 in 2023, and 10.219 versus 10.209 in 2024. The secondary complete-
outdoor subset also loses: 10.529 versus 10.442 early (358 games), and 10.547 versus 10.446
at refresh (357 games). Restricting to weather-covered games does not rescue the result;
the primary comparison still includes all 544 games.

The refreshed weather bundle slightly improves total MAE, 10.250 versus 10.279, but worsens
score MAE, 7.431 versus 7.383, and fails the margin requirements. This result rejects this
particular linear package; it does not establish that all weather modeling is ineffective.

Weather must improve early-week margin against both compact EPA and venue/coverage flags with a positive paired lower bound, while passing both-horizon score/total/season guards. All games remain in primary results. Unknown packages use neutral zero and shared flags; precipitation is a rate, not accumulated rain. Publication delay and static city/venue assumptions remain explicit.

These are previously examined, adaptively explored development seasons. The active model, earlier locks and original enriched-replay gate remain intact. Dated roster/starter coverage precedes the enriched 2025 replay; PEM testing follows the original plan.

The candidates use 8, 11 and 14 raw inputs, allocating 20, 26 and 32 weights/intercepts at
the recorded season-start fits. Ridge effective degrees of freedom per score output are
about 8.5–8.8 for compact EPA and 12.9–13.5 for weather. The extra parameters have not earned
inclusion here. The separate research cache occupies **205,875 bytes**; no source downloads,
environment or copied training tables were added by the comparison. See the
[comparison chart](comparison.html), [paired ranges](comparisons.csv) and
[recorded protocol](protocol.md). The chart uses Plotly's CDN.
