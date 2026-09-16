# prediction-app

An educational NFL quarterback passing-yards research project. The eventual application will
compare calibrated projections with manually entered sportsbook lines and prices.

**Current scope: Milestones 0–2 — audited data and chronological baseline evaluation.** It downloads NFL
statistics through `nflreadpy`, caches them as Parquet, builds one row per recorded regular-season
QB-game, and compares rolling forecasts with Ridge regression using strictly lagged features.
The EV engine, calibrated uncertainty, and Streamlit UI are later milestones.
Results are estimates, may be wrong, and may lose money. No profitability claim has been established.

## Start here

Requires Python 3.11+ and internet access for installation and the first download. Run commands
from the repository root. No API key or `.env` file is required.

### Windows PowerShell

```powershell
git clone https://github.com/Dudecool00/prediction-app.git
cd prediction-app
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev]'
.\.venv\Scripts\nfl-prop.exe fetch --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe build --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe evaluate
```

The explicit executable paths avoid PowerShell activation-policy issues. If you already have
this workspace, start at `python -m venv .venv`; do not clone a second copy inside it.

### macOS / Linux

```bash
git clone https://github.com/Dudecool00/prediction-app.git
cd prediction-app
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements-dev.lock -e '.[dev]'
.venv/bin/nfl-prop fetch --seasons 2022 2023 2024
.venv/bin/nfl-prop build --seasons 2022 2023 2024
.venv/bin/nfl-prop evaluate
```

`requirements-dev.lock` pins the tested dependency versions. It is a pip constraints file,
not a platform-specific wheel lock. Omitting `-c requirements-dev.lock` resolves newer allowed
development dependencies. Python 3.11 is the minimum; see `PROJECT_STATUS.md` for tested runtimes.

## What the commands produce

| Command / location | Purpose |
| --- | --- |
| `nfl-prop fetch` | Download the default 2022–2024 sample, or reuse a verified cache |
| `nfl-prop fetch --refresh` | Retrieve a new snapshot while preserving older raw files/manifests |
| `nfl-prop build` | Rebuild and audit from cached data; requires no network |
| `nfl-prop evaluate` | Fit rolling/Ridge baselines in weekly folds and report 2023–2024 errors offline |
| `data/processed/baselines_2022_2024/manifest.json` | Out-of-fold prediction filename/hash, method, versions, and fold metadata |
| `reports/local/baselines/evaluation.md` | Baseline comparison by season and observed-history bucket |
| `reports/local/baselines/evaluation.json` | Metrics, cutoffs, fold preprocessing, coefficients, and provenance |
| `data/raw/2022_2023_2024/manifest.json` | Retrieval timestamp, package versions, observed schemas, source links, and SHA-256 hashes |
| `data/processed/2022_2023_2024/manifest.json` | Input manifest, pipeline version, feature allowlist, and output filename/hash |
| `reports/local/2022_2023_2024/audit.md` | Inclusion counts, missingness, sample by season, and limitations |
| `reports/local/2022_2023_2024/source_schema.md` | Every source column, actual dtype, and null count |
| `reports/local/2022_2023_2024/audit.json` | Complete machine-readable audit and provenance |

All commands accept `--data-dir PATH`. `build` and `evaluate` accept `--report-dir PATH`;
`build` adds a season-named subdirectory, while `evaluate` writes directly into the specified
report directory. Run one writer at a time in a given data directory.
Parquet files contain a content hash in their filenames; read the manifest to locate the latest
one. Rebuilding the same inputs and code produces the same table. Report-generation timestamps
change on each run. Raw and processed data, local reports, and virtual environments are ignored
by Git. Old hash-named files are deliberately retained.

To reproduce the version-controlled initial audit:

```powershell
.\.venv\Scripts\nfl-prop.exe build --report-dir reports/milestone_1
```

Read the [initial audit](reports/milestone_1/2022_2023_2024/audit.md) and
[actual schemas](reports/milestone_1/2022_2023_2024/source_schema.md).
Refreshing later may change results because nflverse can revise historical data. To reproduce
an older raw snapshot, restore its archived `manifest-*.json` as `manifest.json` in that same
season directory; keep the hash-named files it references. The manifest time is **our retrieval
time**, not an upstream last-update timestamp.

## Cohort and target

The target is the source's `passing_yards`, renamed `target_passing_yards`. Inclusion requires a
`position == "QB"` regular-season statistics row, a matching schedule game with final scores,
and a nonmissing target. There is **no minimum attempts, yards, or starts requirement**. Backups,
zero-attempt rows, and early exits remain. Negative passing-yard values are not clipped.

The initial sample contains **1,960 QB-game rows, 112 QBs, and 815 regular-season games**:
633 rows for 2022, 663 for 2023, and 664 for 2024. It retains 79 zero-attempt rows.
There are no duplicate QB keys or unmatched QB game IDs.

Known source issues: 66 rows have no player identity or position and are counted among non-QB /
unknown-position exclusions. There are 37 schedule-reported starters without a matching QB target
row. The audit lists them. `schedule_reported_starter` is an **unverified retrospective label**;
these discrepancies must be reconciled before starter-specific modeling. It is not a predictor.

Absent/inactive QBs are not reconstructed. Missing targets are not replaced with zero. A
cancelled game absent from both sources cannot be counted as an exclusion; a present game
without final scores is counted and excluded. Historical rescheduling is reflected in the
source's recorded kickoff, not reconstructed from an original schedule snapshot.

## Current 2026 QB cross-reference

Run this after the historical build to compare the sample with ESPN-derived depth charts
distributed by nflverse:

```powershell
.\.venv\Scripts\python.exe -m nfl_prop_model.data.current_qbs --refresh
```

Omit `--refresh` to reuse the verified local cache without a download. The first run needs
network access. This audit accepts `--data-dir` and `--report-dir`; its default report is
[the current QB cross-reference](reports/current_qbs_2026/cross_reference.md), with a JSON
companion containing player IDs, team/rank, source timestamps, and input hashes.

The **September 15, 2026, 12:39:14 UTC** source snapshot covers 32 teams and 92 QBs. Stable
GSIS IDs match 61 of our 112 historical QBs; 51 are unmatched. Filtering historical data by
current membership would discard **429 of 1,960 games (21.9%)**. There are 31 current QBs
without 2022-2024 sample history, and all current entries have a GSIS mapping in this snapshot.

Keep historical training rows. Use fresh charts and roster/game status to build a future
prediction selector, with starters checked separately. Missing from a chart does not establish
retirement; [dated retirement evidence](reports/current_qbs_2026/retirement_notes.md) is recorded
separately. The audit chooses each team's latest recorded snapshot before selecting QBs, so
departed players are not retained from older charts. It rejects missing team/QB coverage and
ambiguous IDs. It does not filter or modify the historical table, produce a starter guarantee,
or resolve the 37 historical starter discrepancies. Current membership is never a historical
predictor or backtest filter. Refresh before using these entries for an upcoming game.

## Features and leakage controls

**Leakage** means giving a model information that was unavailable when its prediction would
have been made. For example, this game's passing yards must never enter this game's recent mean.

Rows are sorted by actual recorded kickoff, grouped by stable `player_id`, and shifted before
rolling. For results `[100, 200, 300]`, the prior-game means are `[null, 100, 150]`.

| Allowed feature | Definition |
| --- | --- |
| `passing_yards_lag1` | Previous recorded regular-season QB-game passing yards |
| `passing_yards_mean3` | Mean of up to three earlier recorded QB games |
| `passing_yards_mean5` | Mean of up to five earlier recorded QB games |
| `attempts_mean5` | Mean attempts in up to five earlier recorded QB games |
| `passing_yards_season_mean` | Mean yards in earlier QB games from the same season |
| `prior_games_in_sample` | Earlier QB-game records in the downloaded sample |
| `prior_season_games_in_sample` | Earlier QB-game records in this season/sample |

The short rolling windows follow a player across teams and seasons. The seasonal mean resets.
All history is regular season only. Counts measure observed sample history, **not career
experience**. The first row stays null; the 112 cold-start rows are retained. Feature construction
uses no global mean, backfill, full-season aggregate, or preprocessing fit. During evaluation,
Ridge fits imputation inside each training fold.

`prediction_time_utc` is a conceptual historical timestamp one hour before kickoff, not a
claim that a forecast was actually produced then. Schedule dates/times are interpreted in
`America/New_York` and converted to UTC with daylight-saving rules. A previous game's statistics
are assumed available 24 hours after its kickoff. `history_available_at_utc` must precede each
prediction timestamp; the build fails otherwise. This conservative assumption does not prove
the original publication time or eliminate leakage from later source corrections.

Baseline estimators select `FEATURE_COLUMNS` from `features/quarterback.py` explicitly.
Current-game targets, attempts, completions, and schedule-reported starter labels are diagnostic
columns, not predictors. Scores are used only to identify completed games and do not enter the
table. Closing market lines, schedule weather observations, injuries, and depth-chart labels
are excluded from the feature set.

## Quality checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pip check
```

On macOS/Linux, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
Tests use synthetic fixtures and mocked loaders; they do not download NFL data. They cover
hand-calculated rolling features, current/future outcome mutation, player isolation, team changes,
season resets, input ordering, UTC conversion, duplicate and schema rejection, missing-data
counts, retained zero-attempt rows, cache integrity, preserved snapshots, and offline rebuilds.
Current-chart tests also cover team coverage, future snapshots, departed players, ambiguous IDs,
retained historical rows, and newcomers with missing identity mappings or sample history.
Model tests verify weekly cutoffs, delayed result availability, game grouping, train-only
preprocessing, forecast invariance to current/future outcomes and diagnostic columns, holdout
rejection, cold-start fallbacks, hand-calculated metrics, and reproducible offline evaluation.

Missing cache: run `fetch` with the same seasons and data directory before `build`.
Checksum mismatch: restore the original file or explicitly `fetch --refresh`.
Schema mismatch: inspect the source schema before adapting a transformation; do not guess a
replacement column. Invalid/missing kickoff times or unmatched QB games stop the build.

**Current Windows validation caveat:** 45 tests pass under Python 3.12 and 3.14, but pytest prints
native access-violation diagnostics during Polars calls.
The processes complete with exit code 0; the CLI succeeds. The local cause remains unresolved
after testing another Polars version and its compatibility runtime. No fault handler is suppressed.
Independent GitHub CI passes all 45 tests on Linux/Python 3.11 and Windows/Python 3.12 without
those native diagnostics. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for evidence.

## Architecture and learning guide

```text
nflreadpy -> cached Polars/Parquet snapshots -> source contracts
    -> quarterback-game target table -> shifted features -> audit + processed Parquet
    -> weekly training folds -> rolling forecasts + Ridge -> predictions + error report
```

| File | What to study |
| --- | --- |
| `src/nfl_prop_model/data/ingest_nfl.py` | Explicit seasons, fetch vs cache reuse, source provenance |
| `src/nfl_prop_model/data/storage.py` | Content-addressed files, checksums, atomic manifest replacement |
| `src/nfl_prop_model/data/schemas.py` | Contracts based on observed schemas; errors instead of silent fixes |
| `src/nfl_prop_model/data/quarterback_games.py` | Inclusion rules, validated joins, pregame timestamps |
| `src/nfl_prop_model/features/quarterback.py` | Grouping, shifting, rolling, cold starts, predictor allowlist |
| `src/nfl_prop_model/data/audit.py` | Reproducible quality evidence and disclosed limitations |
| `src/nfl_prop_model/data/current_qbs.py` | Refreshable current-chart audit, separate from historical modeling |
| `src/nfl_prop_model/modeling/baselines.py` | Feature allowlist, rolling fallbacks, and Ridge preprocessing |
| `src/nfl_prop_model/modeling/evaluate.py` | Weekly cutoffs, fit/predict separation, and error metrics |
| `src/nfl_prop_model/modeling/report.py` | Saved predictions, fold diagnostics, and readable results |
| `src/nfl_prop_model/cli.py` | Small command layer connecting the components |
| `tests/` | Executable examples of correct math and leakage protections |

In an interview: explain how stable player/game identifiers prevent join ambiguity, how changing
a future outcome proves the feature invariants, why raw snapshots are preserved, and why the
current cohort cannot yet be treated as a validated set of pregame starters.

Polars handles ingestion and transformations without a pandas conversion. Local Parquet is enough
for this milestone; DuckDB can be added if queries require it. An executable CLI and generated
audit replace a notebook for now so there is only one transformation implementation to maintain.
NumPy arrays connect Polars to scikit-learn without a pandas conversion. NumPy and SciPy are
pinned to releases with Python 3.11 and 3.14 wheels. Weather and UI dependencies come later.

## Milestone 2 results and reproduction

```powershell
.\.venv\Scripts\nfl-prop.exe evaluate --report-dir reports/milestone_2
```

`evaluate` trains each fold and scores it in one command. No separate full-sample training or
production prediction command is provided yet. It uses the cached 2022–2024 table, preserves it,
and saves one prediction per evaluated QB-game/model to ignored, hash-named Parquet files.
The [evaluation report](reports/milestone_2/evaluation.md) and JSON companion include source and
prediction hashes, package versions, all fold cutoffs, preprocessing values, and coefficients.

Use 2022 as initial training history. Evaluate **all 1,327 recorded QB-games in 2023–2024** across
36 weekly folds. Before each week's earliest prediction timestamp, fit using only games whose
kickoff plus 24 hours is strictly earlier. Medians, scaling, and Ridge are learned from that
training fold. Every model scores the same rows, including backups and 29 players' first sample
appearances during evaluation. This is an appearance-conditioned research cohort; the 37
historical starter discrepancies still prevent validated starter-specific conclusions.

| Forecast | MAE (yards) | RMSE (yards) | Bias (yards) |
| --- | ---: | ---: | ---: |
| Prior-five-game mean | 72.03 | 91.97 | +2.35 |
| Season-to-date mean | 70.58 | 91.57 | -4.11 |
| Ridge | 68.80 | 85.72 | +10.21 |

Ridge reduces overall MAE by 3.23 yards and RMSE by 6.25 relative to the prior-five baseline,
but its positive bias is larger. It does not win every subgroup. No-history cases remain hard:
Ridge MAE is 98.24 yards for those 29 observations. These are descriptive development results;
statistical significance, probability calibration, interval coverage, and betting ROI are unmeasured.

Rolling forecasts fall back to the training-fold target mean when no history exists. The seasonal
forecast first falls back to the prior-five mean. Ridge uses median imputation, missing indicators,
standard scaling, and fixed alpha=1 with the SVD solver; no hyperparameter tuning was done.
Coefficients in the JSON report use standardized features and do not imply causal effects.

**The 2025 season remains reserved** until model and feature decisions are frozen. Historical
fetch/build and evaluation reject 2025+ analysis. `load_schedules` internally reads an all-seasons
file before filtering; only requested development seasons are stored/analyzed here. January 2025
games belonging to the **2024 season** are valid development data.

Next: reconcile historical starter labels and investigate bias and limited-history cases.
Milestone 3 adds one nonlinear model and chronological uncertainty
calibration, with schedule/opponent/weather features assessed incrementally. The EV engine and
Streamlit application follow later. Statistical accuracy is not historical profitability.

## Sources and data rights

See [data/README.md](data/README.md) for dataset-level attribution, licenses, and transformations.
This project uses public football data, manual market input in later milestones, and manual
bet placement outside the app. No sportsbook credentials, scraping, or automated betting is used.
