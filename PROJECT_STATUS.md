# Project status

Updated 2026-09-15 UTC and America/Chicago.

## Completed data foundation: Milestones 0 and 1

- Python package, CLI entry point, isolated environment, dependency constraints, Ruff, mypy,
  pytest, Git ignores, setup guide, and an offline GitHub Actions workflow.
- Downloaded actual 2022–2024 weekly player statistics and schedules through nflreadpy 0.1.5.
  Preserved cached Parquet, schemas, timestamps, source links, licenses, and SHA-256 hashes.
- Validated the `game_id` join and built 1,960 regular-season QB-game rows with 22 columns,
  including five lagged performance features and two prior-history counts.
- Retained backups, early exits, 79 zero-attempt rows, and 112 cold-start rows.
- Generated the [initial quality report](reports/milestone_1/2022_2023_2024/audit.md),
  [source schemas](reports/milestone_1/2022_2023_2024/source_schema.md), and full JSON audit.
- Source and data hashes connect the processed table to its implementation and inputs.

## Current 2026 depth-chart audit

- Added a refreshable ESPN-derived nflverse depth-chart cross-reference and source cache.
  Latest source snapshot: **2026-09-15 12:39:14 UTC**, retrieved at 17:45:13 UTC that day.
- All 32 teams covered: 92 current QBs, 61 matching historical QBs, 51 unmatched historical QBs.
  Current entries include 31 QBs without 2022-2024 sample history; none lack a GSIS mapping.
- A current-membership filter would discard 429 historical games (21.9%). **No historical rows
  were deleted.** Current status is a prospective eligibility review, never a training feature
  or a retrospective cohort filter. Chart absence does not establish retirement.
- Generated [player cross-reference](reports/current_qbs_2026/cross_reference.md), JSON with
  source hashes and IDs, and [dated retirement evidence](reports/current_qbs_2026/retirement_notes.md).
  Today's charts do not resolve historical starter discrepancies or confirm future starters.

## Completed research baseline: Milestone 2

- Added `nfl-prop evaluate`: prior-five-game mean, season-to-date mean, and Ridge with fixed
  alpha=1. Median imputation, missing indicators, scaling, and regression fit only on each
  training fold. The feature allowlist excludes current outcomes, diagnostics, and 2026 status.
- 2022 provides 633 initial training rows. Every 2023-2024 recorded QB-game is scored:
  **1,327 games, 36 weekly folds, three forecasts, zero evaluation rows dropped**.
- Training cutoffs precede each week's earliest pregame timestamp. A training outcome must
  clear kickoff plus 24 hours strictly before that cutoff. The 2025 holdout remains untouched.
- Results in [evaluation.md](reports/milestone_2/evaluation.md); JSON records all fold cutoffs,
  medians, scaler parameters, coefficients, versions, metrics, and source hashes. Predictions
  are in ignored `data/processed/baselines_2022_2024/`; estimators are fitted transiently.

| Forecast | MAE | RMSE | Bias |
| --- | ---: | ---: | ---: |
| Prior-five mean | 72.03 | 91.97 | +2.35 |
| Season-to-date mean | 70.58 | 91.57 | -4.11 |
| Ridge | 68.80 | 85.72 | +10.21 |

All units are yards; positive bias means overprediction. Ridge improves aggregate error but
has greater bias and does not win every subgroup. Its no-history MAE is 98.24 yards (29 cases).
This is an appearance-conditioned research comparison. Starter-specific/production validity,
statistical significance, calibration, uncertainty intervals, and profitability remain unproven.

## Validation evidence

- Pinned installations and **45 tests pass** under Windows Python 3.14.2 (`.venv`) and 3.12.14
  (`.venv-check`). The 12 added modeling cases verify cutoffs, delayed results, grouped games,
  fold preprocessing, current/future outcome mutation, forbidden columns, holdout rejection,
  newcomer fallbacks, numerical metrics, and deterministic offline CLI evaluation.
- Ruff lint and formatting pass; strict mypy passes for all 15 source files.
- `pip check` reports no broken requirements in either environment.
- Full-data fetch reused the cache. Two offline builds produced identical manifests and
  output SHA-256 `5bceef915e3ff9216710f6be47c505fc39611d6b2ffa3d7bac77f3d6d44261cc`.
- The current-chart audit also succeeds offline; its report references that same historical
  table hash. A direct file checksum after the cross-reference confirms the table is unchanged.
- Full-data evaluation reproduced prediction SHA-256
  `6180fb2545dbe6ee1212469e39fb9177638bf5f2a5127fe997325a59e04fcae4` on repeated Python 3.14 runs.
  Historical data remain unchanged. Floating-point equality across platforms is not promised.
- The foundation's [GitHub Actions run](https://github.com/Dudecool00/Prediction-App/actions/runs/35043536050)
  passes on Ubuntu/Python 3.11 and Windows/Python 3.12. Milestone 2 uses the same CI matrix.
  NumPy 2.4.6 / SciPy 1.17.1 were selected with wheels for both 3.11 and 3.14.

## GitHub review

The foundation is published as [PR #1](https://github.com/Dudecool00/Prediction-App/pull/1),
commit `ae5124c` on `codex/data-foundation`. Milestone 2 uses `codex/milestone-2-baselines`, based
on that foundation so its review can show only the new baseline changes. Merge the foundation
first and retarget the baseline PR to `main` before merging it. Neither milestone is merged yet.

**Environment issue still open:** pytest emits native `Windows fatal exception: access violation`
diagnostics during Polars execution, while all assertions complete and the process exits 0.
Reproduced inside and outside the sandbox, on Python 3.12 and 3.14, with Polars 1.38.1 and
1.44.2, and with the compatibility runtime. Version/runtime swaps did not resolve it.
No fault handler or test has been disabled. Polars 1.44.2's standard runtime is restored.
Local diagnostic output is kept in ignored `reports/local/test-*.txt`. The numerical tests
and CLI succeed, but clean native-runtime validation on another machine/CI remains outstanding.

## Data limitations and current modeling results

- 56,457 source player rows and 854 schedule rows yield 1,960 QB rows across 112 players.
- 66 unknown-player/position rows are counted in exclusions. No included QB has a duplicate
  key, unmatched game, missing target, or malformed kickoff in this snapshot.
- 37 schedule-listed starters lack a matching QB target row. The audit lists all cases.
  `schedule_reported_starter` is unverified and excluded from predictors. Reconcile it before
  starter-specific evaluation; do not interpret unmatched labels as proven inactive players.
- Inactive QBs absent from statistics are not reconstructed; history is regular-season and
  sample-limited. Current historical files may contain revisions unavailable pregame.
- Probability calibration, interval coverage, historical betting ROI, EV engine, and UI are
  not implemented. The baseline report above measures point-error performance only.
- 2025-season analysis is reserved. The schedules loader internally reads all seasons, then
  returns only the requested development seasons. January 2025 dates in the 2024 season are valid.

## Reproduce from this workspace

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev]'
.\.venv\Scripts\nfl-prop.exe fetch --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe build --report-dir reports/local
.\.venv\Scripts\nfl-prop.exe evaluate --report-dir reports/milestone_2
.\.venv\Scripts\python.exe -m nfl_prop_model.data.current_qbs --refresh
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
```

Use `fetch --refresh` only when a new upstream snapshot is wanted. The README documents clean
setup on Windows and macOS/Linux, data paths, restoring older cached snapshots, and all features.
`evaluate` fits each fold and writes its comparison in one command. Production prediction,
weather, and app-start commands will be added in their own milestones.

## Next milestone

Review the foundation PR, baseline bias and limited-history failures, reconcile historical starter
labels, and independently verify native diagnostics.
Milestone 3 adds one nonlinear model and chronological uncertainty calibration, with incremental
schedule/opponent/weather features. Keep 2025 out of feature/model selection.
