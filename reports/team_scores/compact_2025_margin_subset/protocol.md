# 2025 sensitivity: exclude final margins of 21 points or more

Requested on 2026-10-16 after reviewing the completed compact-EPA replay.
This is an outcome-conditioned, post-hoc diagnostic, not a revised primary evaluation.

- Reuse the verified frozen 2025 prediction panel; perform zero new fits and leave
  all training games, parameters, predictions and original results intact.
- Exclude a game when `abs(actual_home_score - actual_away_score) >= 21`.
  Exactly 21-point wins are excluded, irrespective of the winner. Ties remain.
- Apply the same game IDs to compact EPA, league scores and prior team scores.
  Keep early-week and refresh metrics separate; their predictions are identical.
- Recompute the original score/margin/total MAE, RMSE and bias, weekly metrics,
  and paired week-block bootstrap margin gains against each control. Use the
  existing 5,000-draw seed and procedure; no threshold search or model selection.
- Show all games, the retained subset and the excluded games side by side.
  Score errors count both teams; margin and total errors count each game once.
  Use Tuesday rows for plots and paired comparisons to avoid double counting.
- Compare each method's error reduction and relative performance, not just the
  model's lower MAE. Restricting actual margins also makes the scoring problem
  easier for controls; this cannot establish a pregame filtering strategy.
- Save compact derived tables, source hashes and charts. Reuse the existing cache
  and environment; download nothing and do not duplicate raw/training datasets.
  The enriched replay remains gated.
