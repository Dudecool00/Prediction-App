# Frozen compact-EPA 2025 weekly replay

Declared on 2026-10-16 before this replay reads 2025 outcomes or fits models.
The user explicitly authorized this narrower milestone while retaining the original
enriched replay gate. 2025 has previously been examined elsewhere in the project;
this is a retrospective diagnostic, not an untouched holdout.

- Reuse the verified simplicity, dated-injury and archived-weather development
  decisions. All three must retain `compact_epa`. Preserve their hashes and choices
  in a separate freeze packet before loading the canonical outcome artifacts.
- Exactly eight inputs: home/away season offensive and defensive EPA, home field,
  rest difference, and home/away prior-game counts. Use the existing train-only
  median imputer (with missing indicators), scaler and two-output Ridge, alpha 20.
  No tuning, additional candidate fitting or selection from 2025 results.
- All 272 regular-season 2025 games, Weeks 1–18. Training begins with available
  2022–2024 games; later weeks add only earlier-week 2025 results available strictly
  before Tuesday 10:00 America/Chicago. The existing kickoff-plus-24-hour result
  availability assumption applies; a Monday result can miss the next Tuesday lock.
- EPA, rest, training cohort and coefficients stay Tuesday-locked. Fit once per
  week (18 fits) and reuse at kickoff minus one hour. With no context inputs,
  refresh predictions must equal early-week predictions; these are duplicated
  forecast horizons, not independent observations.
- Prespecified descriptive controls are the existing league-score and prior-team
  scoring formulas. They do not select or replace the frozen model.
- Scores are clipped at zero and rounded to tenths. Home margin is home score
  minus away score; away margin is its negative; total is their sum.
- Primary metric: early-week game margin MAE. Also report score MAE (544 team
  scores), total MAE, RMSE and bias; weekly errors; paired week-block bootstrap
  ranges against the controls; and game-level predicted versus actual charts.
  Retain every game, including blowouts. Do not use these results to retune.
- Check reconstructed 2022–2024 compact features against the preserved development
  artifact. Test future-result/EPA invariance, strict cutoff boundaries, missing
  cohorts, refresh identity, score arithmetic and cache integrity before real fits.
- Offline reuse of existing caches only. Save hashes, fold receipts, 2025 feature
  rows and predictions; no copied training/raw datasets or new environment.
  The published 28-input app model and the enriched replay gate remain unchanged.
