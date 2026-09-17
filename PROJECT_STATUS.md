# Project status

Updated 2026-09-17. Milestones 0–2 are merged; Milestone 3 model/uncertainty work is ready for review.

## What works

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

- **55 tests pass on local Windows Python 3.12.14 and 3.14.2**; pip check passes in both.
- Ruff lint/format checks and strict mypy pass (19 source files).
- Tests cover current/future outcome mutation, calibration/training separation, delayed-result
  cutoffs, game grouping, opponent totals, rookie retention, finite-sample interval ranks,
  strict smoothed tail probabilities, hand-calculated metrics, offline CLI, and cache mismatch.
- Two real-data runs reproduce all four research artifact hashes. Prediction SHA-256:
  `c9d2bd50de638323a108d63fe9b544e756a11a8f86b1352beb7daa661ab4b761`.
- Historical table remains byte-identical, SHA-256:
  `5bceef915e3ff9216710f6be47c505fc39611d6b2ffa3d7bac77f3d6d44261cc`.
- Dependencies include XGBoost CPU 3.2.0 and Plotly 7.1.0, pinned for Python 3.11+ support.
- Local pytest still emits native Polars access-violation diagnostics, despite all assertions
  passing and exit code 0. No fault handler or tests are disabled. Earlier independent
  [Milestone 2 CI](https://github.com/Dudecool00/Prediction-App/actions/runs/35046398100)
  passed Linux/Python 3.11 and Windows/Python 3.12 with zero such messages.
- Browser inspection confirms all four offline calibration plots and the legend render.
- [Milestone 3 CI runs](https://github.com/Dudecool00/Prediction-App/actions?query=branch%3Acodex%2Fmilestone-3-uncertainty)
  check Linux/Python 3.11 and Windows/Python 3.12; consult the run matching the PR's current commit.

## GitHub

[Foundation PR #1](https://github.com/Dudecool00/Prediction-App/pull/1) is merged (`41f0e93`).
[Milestone 2 PR #2](https://github.com/Dudecool00/Prediction-App/pull/2) is merged (`38aeda3`).
Milestone 3 uses `codex/milestone-3-uncertainty`, based on that merged main branch.

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
- No production upcoming-game prediction, manual odds/EV engine, Streamlit UI, or prediction log
  yet. Close the weather/calibration/starter gaps, then proceed to Milestone 4 manual odds math.
  Keep 2025 out of development decisions. Statistical accuracy does not establish profitability.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev]'
.\.venv\Scripts\nfl-prop.exe fetch --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe build
.\.venv\Scripts\nfl-prop.exe evaluate --report-dir reports/milestone_2
.\.venv\Scripts\nfl-prop.exe research --report-dir reports/milestone_3
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
```

Build/evaluate/research use verified local caches. Fetch reuses its cache unless `--refresh`
is explicit. To refresh the prospective QB audit separately, run
`python -m nfl_prop_model.data.current_qbs --refresh`. See README for full setup and limitations.
