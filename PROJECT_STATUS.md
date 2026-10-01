# Project status

Updated 2026-10-01. PR #7 is merged (`aabb94a`); its Linux and Windows CI passed.

## New: limited-history calibration and candidate policy

- **Calibration audit** and `nfl-prop calibration-audit` read saved research artifacts
  offline. They report interval coverage/width and probability scores/reliability by pregame
  history, with distinct player/game/week counts and per-fold calibration support.
- A history-matched interval diagnostic requires 30 same-bucket residuals; unavailable
  intervals stay missing. Paired pooled coverage uses exactly the available matched subset.
  All six models and every development evaluation row remain in the diagnostics.
- Schedule XGBoost's pooled nominal 90% coverage is 82.76% for 29 no-history rows, 84.89%
  for 139 rows with 1–4 prior games, and 90.42% for 1,159 rows with 5+ games. No no-history
  pool reaches 30 residuals. Matched intervals are available for only 61 of 139 limited-history
  rows; their wider intervals do not establish general sparse-history readiness.
- [The candidate policy](MODEL_POLICY.md) chooses fixed schedule XGBoost and pooled
  calibration, excludes unverified weather, and would abstain prospectively below five prior
  available model-sample games. The policy is versioned/hashed but **not frozen**. Historical
  forecasts and training rows remain unchanged; 2025 is closed and no forecasts are enabled.
- Next: resolve cohort/source requirements and freeze reproducible estimator/calibration
  artifacts and the holdout protocol before accessing reserved outcomes. Prospective source
  verification, the remaining historical starter labels, and weather research remain open.

## New: prospective status review workflow

- **Upcoming QBs** now saves dated manual starter and active-status evidence separately.
  Each save preserves the candidate context, source manifest, supplied URLs/publication times,
  local review time, and notes in a new checksummed record. Source contents are not auto-verified.
- Starter evidence expires after 24 hours; active evidence after 6 hours. Both source and
  local review age count. Changed matchup/kickoff/chart/identity, conflicting starters,
  simultaneous reviews, and starter-plus-inactive evidence require another check.
- Saves re-read verified sources and reject stale caches or started/scored games. The latest
  review available at the report cutoff supplies both statuses; unknown clears an earlier claim.
  UI/downloads and the offline `upcoming` command show the overlay. No forecasts are enabled.
- October 1 source refresh: 93 QBs across 32 teams, 31 upcoming games and 180 candidate rows
  in the 14-day window. All have fresh chart/cache sources. Zero real manual status claims
  have been entered; those remain unknown. All 1,960 historical rows retain their original hash.
- The follow-up now diagnoses limited-history calibration and records a candidate policy.
  Prospective evidence still needs collection and independent review;
  historical starter coverage and weather sourcing remain incomplete.

## New: historical starter reconciliation

- `nfl-prop starter-audit [--refresh]` and the **Starter audit** page review the 37 schedule-listed
  starters missing from the historical QB target table. ESPN event rosters explicitly identify
  a QB starter; nflverse identity mappings connect ESPN IDs to stable GSIS IDs.
- October 1 evidence reconciles **all 37** to existing QB target rows: 4 in 2022, 33 in 2024,
  and zero unresolved flagged cases. These were incorrect schedule labels, not missing targets.
  [Read the derived report](reports/starter_audit/starter_audit.md).
- The raw evidence cache preserves source hashes, retrieval times, and timestamped manifests.
  Reads run offline; mismatched events/weeks/teams, contradictory flags, identity ambiguity,
  stale input manifests, and damaged files fail. Missing or nonunique starter evidence stays unresolved.
- All 1,960 training rows and the historical table hash remain unchanged. Corrections are a
  separate diagnostic overlay. The other **1,593 team-game starter labels remain unverified**;
  this does not establish pregame knowledge or enable starter-specific training.
- Next: collect prospective starter/active-status evidence, assess calibration for limited
  history, and document the model policy before opening the reserved holdout. Historical weather
  sourcing remains unfinished; keep it out of a frozen model unless its coverage is established.

## New: manual odds and local research interface

- Milestone 4 adds validated American prices, fair odds, break-even probabilities, edge in
  percentage points, and push-aware EV. `nfl-prop quote` connects arbitrary whole/half-yard
  lines to verified historical forecasts and saved chronological calibration errors.
- `nfl-prop settlement-audit` checks rounding differences and sparse integer push estimates.
  The original model/interval outputs remain unchanged; half-line Ridge/XGBoost probabilities
  match the original four diagnostic thresholds. Integer pushes remain experimental.
- Milestone 5 has a local Streamlit research preview: compare a line, inspect model results,
  and save/download research snapshots. Saves create new files without overwriting originals;
  checksum verification detects changed contents. No upcoming-game forecast is available yet.
- Install `.[dev,app]` with the constraints file and run `streamlit run app.py`. Regenerate
  `nfl-prop research` once to create format-2 calibration artifacts. See README for full commands.
- Tests cover numerical edge cases, absent/corrupt calibration, price/outcome independence,
  offline CLI, UI validation and stale-result clearing, snapshot collisions, and checksums.
  Final validation and CI results are recorded in the review PR.
- Reports: [manual example](reports/milestone_4/example/quote.md),
  [settlement audit](reports/milestone_4/settlement_audit.md). Prices are hypothetical;
  these outputs make no historical ROI or profitability claim.

## What works

- The new **Upcoming QBs** page and `nfl-prop upcoming` command join 2026 schedules to
  latest-per-team ESPN-derived depth charts. September 22 refresh: 91 QBs across 32 teams,
  32 games / 182 candidate rows in the next 14 days; all meet the 24h cache / 48h chart policy.
  Thirty current QBs have no development-sample history and remain visible. No missing ID
  mappings in this snapshot. [Readiness report](reports/milestone_5/upcoming.md).
- Upcoming source caches are separate from training data. Started/scored games are excluded,
  unknown times are audited, and stale charts / unconfirmed starters / ID gaps are explicit.
  This completes the current-candidate view, not a production forecasting pipeline.

- Cached, audited 2022–2024 nflverse data: **1,960 recorded regular-season QB-games**,
  112 QBs, 815 games, seven strictly lagged QB features. Backups, early exits, 79 zero-attempt
  rows, and 112 first-sample rows remain. Manifests preserve schemas, attribution, and hashes.
- Separate 2026 ESPN-derived current-QB audit. The September 15, 12:39:14 UTC snapshot has
  92 QBs across 32 teams; 61 match historical QBs, 51 historical QBs are unmatched, and 31
  current QBs have no sample history. No historical rows were removed. Absence is not retirement.
- `nfl-prop evaluate`: rolling means and Ridge, weekly chronological evaluation, train-only
  preprocessing, point errors by season/history. See [Milestone 2](reports/milestone_2/evaluation.md).
- `nfl-prop research`: one fixed XGBoost model with QB, schedule, and opponent feature groups;
  separate six-week calibration; 50/80/90% intervals; empirical residual probabilities;
  Brier/log-loss scores and offline interactive reliability plots. See
  [Milestone 3 research](reports/milestone_3/research.md) and its JSON companion.
- All six research forecasts score **1,327 QB-games over 36 weeks**, with zero dropped rows.
  Opponent means use earlier team games and all passers. Rest/home/neutral-site features are
  added separately. The 2025 season remains reserved; current depth charts never enter models.

## Latest comparison

All figures are yards under the same Milestone 3 training/calibration protocol.
Milestone 2 uses a larger training window, so compare models within a report.

| Forecast | MAE | RMSE | Bias |
| --- | ---: | ---: | ---: |
| Prior-five mean | 72.06 | 92.04 | +2.42 |
| Season-to-date mean | 70.61 | 91.64 | -4.04 |
| Ridge | 69.88 | 87.54 | +10.98 |
| XGBoost QB | 68.15 | 85.80 | +3.94 |
| XGBoost + schedule | **67.51** | 85.60 | +3.81 |
| XGBoost + opponent | 67.57 | **85.30** | +3.01 |

Schedule improves MAE in both years. Opponent inputs have mixed incremental effects; overall
MAE worsens by 0.06 yards while RMSE improves by 0.29. No significance or profitability claim.
Schedule XGBoost's nominal 90% intervals cover **89.68%**, averaging **279.01 yards total width**;
opponent XGBoost covers **89.90%**, width **278.73**. For 29 no-history cases, coverage drops to
82.76% and 86.21%. No production model is selected; fitted estimators remain transient.

## Validation

- **181 tests pass in the full local suite on Windows Python 3.12.14**; pip check passes.
  The 24 new calibration/policy cases include hand calculations, partial matched availability,
  outcome independence, source-coherent delayed results, cohort/protocol mismatch, corruption,
  offline CLI, lightweight diagnostic imports, and Streamlit audit/history-warning flows.
  The app also passes against the real research cache. Final CI belongs to the review PR.
- Ruff lint/format checks and strict mypy pass (34 source files).
- Tests cover current/future outcome mutation, calibration/training separation, delayed-result
  cutoffs, game grouping, opponent totals, rookie retention, finite-sample interval ranks,
  strict smoothed tail probabilities, hand-calculated metrics, offline CLI, and cache mismatch.
- The manual-market extension preserves all four original research artifact hashes.
  Format 2 adds 47,658 calibration residual records for arbitrary-line comparisons. Prediction SHA-256:
  `c9d2bd50de638323a108d63fe9b544e756a11a8f86b1352beb7daa661ab4b761`.
- Historical table remains byte-identical, SHA-256:
  `5bceef915e3ff9216710f6be47c505fc39611d6b2ffa3d7bac77f3d6d44261cc`.
- Dependencies include XGBoost CPU 3.2.0 and Plotly 7.1.0, pinned for Python 3.11+ support.
- Local pytest still emits native Polars access-violation diagnostics, despite all assertions
  passing and exit code 0. No fault handler or tests are disabled. Earlier independent
  [Milestone 3 CI](https://github.com/Dudecool00/Prediction-App/actions/runs/35276936509)
  passed Linux/Python 3.11 and Windows/Python 3.12 with zero such messages.
- Browser inspection confirms the comparison calculator and model-results page render.
  App tests exercise invalid prices, changing games, saving twice, and reading saved snapshots.
- Upcoming tests cover source timestamps, stale cache/chart separation, departing/current/new QBs,
  source identity errors, incomplete scores, unknown kickoffs, unsupported seasons, cache tampering,
  offline CLI, and the UI with no historical model cache. The browser preview could not attach
  during this follow-up; the new page is validated through Streamlit AppTest.
- CI installs the optional app and checks Linux/Python 3.11 and Windows/Python 3.12;
  consult the review PR's exact commit for remote results.

## GitHub

[Foundation PR #1](https://github.com/Dudecool00/Prediction-App/pull/1) is merged (`41f0e93`).
[Milestone 2 PR #2](https://github.com/Dudecool00/Prediction-App/pull/2) is merged (`38aeda3`).
[Milestone 3 PR #3](https://github.com/Dudecool00/Prediction-App/pull/3) is merged (`a2093da`).
Manual odds and the local research interface use `codex/milestone-4-manual-odds`.
[PR #4](https://github.com/Dudecool00/Prediction-App/pull/4) is merged (`354cc7f`).
[PR #5](https://github.com/Dudecool00/Prediction-App/pull/5) is merged (`3a43295`); both CI platforms
passed on `7f5b0e7`. [PR #6](https://github.com/Dudecool00/Prediction-App/pull/6) is merged
(`2033f9f`); both CI platforms passed on `42e2f9b`.
[PR #7](https://github.com/Dudecool00/Prediction-App/pull/7) is merged (`aabb94a`); all 157 tests
and both CI platforms passed on `dcae963`. The calibration follow-up uses `codex/calibration-policy`.

## Open limitations and next work

- **Weather remains unfinished in Milestone 3.** The 2023 prior-day forecast probe returned
  temperature but no wind/precipitation. See [weather readiness](reports/milestone_3/weather_readiness.md)
  for source evidence and the remaining forecast archive, stadium map, and roof-policy work.
- The 37 flagged historical starter labels are reconciled retrospectively; the remaining
  1,593 team-game labels still require independent verification before starter-specific evaluation.
  The prospective workflow now supports timestamped manual checks; actual evidence collection
  and independent source verification remain. The existing cohort
  remains conditioned on recorded participation and does not reconstruct inactive QBs.
- Small-history and tail calibration remain weak. Repeated players, shared games, and time
  dependence do not establish the assumptions behind formal conformal coverage guarantees.
  Reported coverage is empirical. Fixed diagnostic thresholds are not historical sportsbook lines.
- Source revisions and recorded kickoff times may differ from information available pregame.
  Kickoff plus 24 hours is an explicit result-availability assumption, not a publication record.
- No production upcoming-game prediction yet. The manual EV engine, research UI, and historical
  snapshot journal now work. Close weather/calibration/starter gaps, freeze the model, assess
  the reserved holdout, and build timestamped upcoming-game forecasts before production use.
  Keep 2025 out of development decisions. Statistical accuracy does not establish profitability.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev,app]'
.\.venv\Scripts\nfl-prop.exe fetch --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe build
.\.venv\Scripts\nfl-prop.exe evaluate --report-dir reports/milestone_2
.\.venv\Scripts\nfl-prop.exe research
.\.venv\Scripts\streamlit.exe run app.py
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
```

Build/evaluate/research use verified local caches. Fetch reuses its cache unless `--refresh`
is explicit. To refresh the prospective QB audit separately, run
`python -m nfl_prop_model.data.current_qbs --refresh`. See README for full setup and limitations.
