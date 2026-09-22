# Project status

Updated 2026-09-22. PR #3 is merged (`a2093da`); its Linux and Windows CI passed.

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

- **113 tests pass on local Windows Python 3.12.14 and 3.14.2**; pip check passes in both.
- Ruff lint/format checks and strict mypy pass (27 source files).
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
[PR #4](https://github.com/Dudecool00/Prediction-App/pull/4) is open; both CI platforms passed
on `c96a0d4`. Upcoming-QB readiness follows on `codex/milestone-5-upcoming-qbs`, based on PR #4.

## Open limitations and next work

- **Weather remains unfinished in Milestone 3.** The 2023 prior-day forecast probe returned
  temperature but no wind/precipitation. See [weather readiness](reports/milestone_3/weather_readiness.md)
  for source evidence and the remaining forecast archive, stadium map, and roof-policy work.
- 37 schedule-listed historical starters have no matching QB target; the starter label stays
  unverified and excluded. Reconcile before starter-specific forecasts. The cohort is conditioned
  on recorded participation and does not reconstruct inactive QBs.
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
