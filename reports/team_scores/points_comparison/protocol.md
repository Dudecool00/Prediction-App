# EPA, opponent-adjusted points, and a fixed combination

Recorded 2026-10-16 before candidate fits. This is an adaptive research design
after reviewing earlier 2025 diagnostics. Simulated pre-season locks are not actual
historical preregistration; 2025 remains an already-examined diagnostic.

Three candidates, with the existing league-score and prior-team-score controls:

1. **Compact EPA**: reuse the hash-verified eight-input frozen forecasts exactly.
2. **Opponent-adjusted points**: one scoring equation fitted to two team-score
   observations per available game. Each team's score is league intercept + its
   offensive rating + the opponent's defensive points-conceded rating + a venue
   term. The venue coding is +0.5 for home, -0.5 for away, zero at neutral sites;
   its coefficient is the full home margin advantage. Positive defense coefficients
   mean more points conceded; power rating is offense minus defense. Rating
   differences therefore supply neutral-site matchup margin in points.
3. **Equal blend**: average the displayed EPA and points-model home/away score
   forecasts separately, then apply the existing coherent tenths score contract.
   Fixed 50/50 weights; no stacking fit or weight search.

The points model is **PEM-inspired**, not a reconstruction of an unknown formula.
It captures separate scoring/defense and opponent strength. EPA supplies the
efficiency signal in the combination; the points model alone does not measure
per-possession efficiency, garbage-time value or resume quality.

- Use Ridge alpha 20, unscaled team indicator columns, a fitted intercept, and the
  deterministic SVD solver. Sample weights have a fixed 365-day half-life from
  kickoff to the current Tuesday lock; both team observations share game weight.
  No alpha/half-life/estimator/weight search. Unknown teams get zero relative
  offense/defense and the pooled intercept, with their unknown count recorded.
- Fit only on 2022+ earlier-season/earlier-week games with results strictly before
  Tuesday 10:00 Chicago. Preserve kickoff-plus-24-hour availability and every
  completed game, including blowouts. No current-week results enter refresh.
- Fit points once per forecast week: 36 development fits for 2023–2024, followed
  by 18 diagnostic fits for 2025. Reuse each fit at refresh. No new EPA or blend fit.
- The two-output EPA pipeline and original cache/gates remain untouched. Same
  game cohorts, cutoffs, scoring arithmetic and full-season metrics for all models.
- Record protocol and source hashes before development fits. Choose on 2023 for
  simulated 2024 evaluation; separately freeze a research choice using 2023–2024
  before the first 2025 candidate fit. Selection rejects 2025 outcomes. Require
  >=0.05 margin MAE gain against **both EPA and prior-team scoring**, with positive
  paired week-block 95% lower bounds against both. At both horizons, score MAE
  regression <=0.05 and total <=0.10 versus both controls; each development season's
  margin regression <=0.25 versus both. Lowest eligible margin MAE wins, then EPA,
  points, blend fixed order. Fallback EPA is a research reference, not promotion.
- Report all three candidates in 2025 regardless of frozen choice. No 2025 winner
  changes the choice, weight or coefficients fitted at earlier locks. Five-thousand
  week-block paired draws use seed 20261015; ranges do not erase adaptive design.
- Include predicted-margin band calibration with common EPA-defined groups and
  descriptive all/actual-blowout/other breakdowns; these never filter primary scores.
- Retain fit receipts, parameter counts/effective degrees of freedom, team ratings,
  predictions and source hashes in a small separate cache. Reuse existing data and
  environment offline; no new raw downloads or training-table copies per fit.

Ridge implementation: [official documentation](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html).
The penalty has the same numerical value as EPA but different feature scaling,
so it is not an assertion of equal effective model complexity.
