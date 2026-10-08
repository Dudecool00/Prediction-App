# prediction-app

An educational NFL quarterback passing-yards research project. Compare historical projections
with manually entered sportsbook lines and prices in a local Streamlit app.

**Current scope: historical forecasts, manual odds/EV, and a local research interface.** It downloads NFL
statistics through `nflreadpy`, caches them as Parquet, builds one row per recorded regular-season
QB-game, and compares rolling forecasts, Ridge, and XGBoost using strictly lagged features.
Research now includes prediction intervals, threshold probabilities, and calibration diagnostics.
The Streamlit interface compares manual prices and saves research snapshots. Upcoming-game
forecasts, historical weather, and final model selection remain unfinished.
An **Upcoming QBs** page now joins scheduled 2026 games to current depth-chart candidates,
with source-age checks and explicit starter/identity review needs.
The page also saves dated manual starter and active-status reviews with separate expiry checks.
**Current features** audits the seven QB lags and four schedule inputs from available
2022–2026 appearances, with source timestamps, history/rest evidence and explicit blockers.
Results are estimates, may be wrong, and may lose money. No profitability claim has been established.

A fixed candidate has now completed the reserved 2025 diagnostic: **664 QB-games**,
**66.12-yard MAE** and **90.96% coverage** for nominal 90% intervals averaging **278.48 yards**
wide. [Read the frozen holdout report](reports/frozen_holdout/holdout.md). The checkpoint
preceded outcome access; this diagnostic does not enable upcoming forecasts. The 2025 season
is now accessed and cannot be reused as untouched validation for a revised candidate.

## Start here

Requires Python 3.11+ and internet access for installation and the first download. Run commands
from the repository root. No API key or `.env` file is required.

### Windows PowerShell

```powershell
git clone https://github.com/Dudecool00/prediction-app.git
cd prediction-app
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev,app]'
.\.venv\Scripts\nfl-prop.exe fetch --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe build --seasons 2022 2023 2024
.\.venv\Scripts\nfl-prop.exe evaluate
.\.venv\Scripts\nfl-prop.exe research
```

The explicit executable paths avoid PowerShell activation-policy issues. If you already have
this workspace, start at `python -m venv .venv`; do not clone a second copy inside it.

### macOS / Linux

```bash
git clone https://github.com/Dudecool00/prediction-app.git
cd prediction-app
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements-dev.lock -e '.[dev,app]'
.venv/bin/nfl-prop fetch --seasons 2022 2023 2024
.venv/bin/nfl-prop build --seasons 2022 2023 2024
.venv/bin/nfl-prop evaluate
.venv/bin/nfl-prop research
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
| `nfl-prop research` | Compare XGBoost feature groups and calibrated intervals/probabilities offline |
| `data/processed/research_2022_2024/manifest.json` | Hash-named context table, points, intervals, probabilities, and provenance |
| `reports/local/research/research.md` | Point errors, incremental feature comparisons, coverage, and probability scores |
| `reports/local/research/calibration.html` | Interactive offline reliability plots with bin sample counts |
| `data/processed/baselines_2022_2024/manifest.json` | Out-of-fold prediction filename/hash, method, versions, and fold metadata |
| `reports/local/baselines/evaluation.md` | Baseline comparison by season and observed-history bucket |
| `reports/local/baselines/evaluation.json` | Metrics, cutoffs, fold preprocessing, coefficients, and provenance |
| `data/raw/2022_2023_2024/manifest.json` | Retrieval timestamp, package versions, observed schemas, source links, and SHA-256 hashes |
| `data/processed/2022_2023_2024/manifest.json` | Input manifest, pipeline version, feature allowlist, and output filename/hash |
| `reports/local/2022_2023_2024/audit.md` | Inclusion counts, missingness, sample by season, and limitations |
| `reports/local/2022_2023_2024/source_schema.md` | Every source column, actual dtype, and null count |
| `reports/local/2022_2023_2024/audit.json` | Complete machine-readable audit and provenance |

All commands accept `--data-dir PATH`. `build`, `evaluate`, and `research` accept `--report-dir PATH`;
`build` adds a season-named subdirectory, while the modeling commands write directly into the specified
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
unknown-position exclusions. The original audit flagged 37 schedule-listed starters without a
matching QB target row. The [starter reconciliation](reports/starter_audit/starter_audit.md) now resolves all 37
as incorrect schedule labels using retrospective ESPN event-roster evidence and stable IDs.
Corrections remain a separate audit overlay; `schedule_reported_starter` stays unchanged and
is not a predictor. The [full starter coverage audit](reports/starter_coverage/starter_coverage.md)
now resolves 1,617 of all 1,630 development team-games; 13 remain unresolved.

Absent/inactive QBs are not reconstructed. Missing targets are not replaced with zero. A
cancelled game absent from both sources cannot be counted as an exclusion; a present game
without final scores is counted and excluded. Historical rescheduling is reflected in the
source's recorded kickoff, not reconstructed from an original schedule snapshot.

## Prospective feature snapshots

```powershell
.\.venv\Scripts\nfl-prop.exe current-features --refresh
.\.venv\Scripts\streamlit.exe run app.py
```

Choose **Current features**. This separate command requires the verified 2022–2024 table
and a completed local 2025 diagnostic/access record. It reads the existing checksummed 2025
feature artifact as prior history; it never downloads/rescores that season or fits a model.
Only 2026 statistics, schedules and ESPN-derived charts are refreshed. Omit `--refresh` for
offline reads; `--days` accepts 1–28, with `--data-dir` and `--report-dir` overrides.

A result is available at the later of kickoff plus 24 hours and its source retrieval.
Historical snapshots must also precede the requested time. Only available completed regular
QB appearances enter the rolling/season means and counts; zeros, negative yards and backups
remain. Current/future game outcomes do not enter their candidate's features. The seven QB
definitions match the historical shifted features; rest uses all earlier completed schedule
games in 2026, including games without a given QB appearance. Unknown or intervening uncompleted
games block rest features. First-season rest is nullable; schedule location must be known.

The report preserves all chart candidates. It separates feature checks from the five-game
history minimum and starter/active evidence. Cache/chart expiry, ID gaps, missing completed
QB team/game statistics, changed/conflicting status reviews and snapshots at/after kickoff
minus one hour are explicit blockers. Manual claims retain their 24h starter / 6h active
windows; their source contents are not independently verified. Prospective participation
validation and production approval remain open, so no predictions, probabilities or EV are
generated, even if all per-candidate checks pass.

Each CLI run creates a new checksummed snapshot in ignored `data/prospective/snapshot-*/`.
The UI reads caches without network access and saves only when **Save feature snapshot** is
clicked. JSON downloads and `reports/local/current_features/` include the source manifests,
feature order/values/availability, all-prior-row digest, last-five input rows, current-season
IDs, unresolved rest games, status context and Python source hashes. Snapshot reads detect
changed report bytes; snapshots do not recheck freshness at a later clock time.

The [October 6 feature audit](reports/prospective/current_features.md) contains 29 games and
170 candidates: 73 pass feature checks; 125 meet the five-game minimum. It uses 144 available
2026 QB-games. All starter/active statuses remain unknown and zero forecasts are enabled.
This is a dated research snapshot, not prospective validation or a claim of past publication.

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
or perform historical starter reconciliation. Current membership is never a historical
predictor or backtest filter. Refresh before using these entries for an upcoming game.

## Historical starter reconciliation

```powershell
.\.venv\Scripts\nfl-prop.exe starter-audit --refresh
.\.venv\Scripts\streamlit.exe run app.py
```

Choose **Starter audit** in the sidebar. The command cross-checks the 37 flagged historical
starter labels against ESPN's explicit event-roster QB starter flags and nflverse's
ESPN-to-GSIS identity mapping. The October 1 audit reconciles all 37 to existing QB target rows:
four cases in 2022 and 33 in 2024. For example, 2022 Week 8 lists Winston in the cached
schedule, while ESPN lists Dalton as the starter and the statistics contain Dalton's target.
No missing passing-yard outcome is filled with zero or assigned to a different player.

Omit `--refresh` to reuse the verified evidence offline. `--data-dir` selects the raw data;
`--report-dir` defaults to `reports/local/starters`. The first run downloads identity mappings
and historical event evidence only for flagged cases. The UI reads local files and works
without the historical model-results cache. It offers the case table, source links, and JSON download.

Hash-named evidence and timestamped manifests are retained under
`data/raw/starter_evidence_2022_2024/`. Input, event/season/week/team identity, QB flags,
and ESPN-to-GSIS mapping must agree; corrupted or contradictory evidence fails visibly.
Zero or multiple QB starters, missing mappings, or missing targets remain unresolved.
A failed refresh preserves the active manifest. No starter is inferred from the largest
passing total, first passer, depth-chart rank, or name similarity.

The [derived report](reports/starter_audit/starter_audit.md) and JSON include per-case URLs,
retrieval times, source hashes, and the original statistics/schedule manifest. This is
retrospective reconciliation, not proof of pregame knowledge. This initial flagged audit left
1,593 other team-game labels unverified; the full audit below extends that coverage.
All 1,960 historical QB rows, targets, features, and existing
starter flags are unchanged; no starter-only model cohort is enabled. This development
audit does not load 2025 outcomes; the separate frozen diagnostic is documented above.

### Full development starter coverage

```powershell
nfl-prop starter-coverage --collect
# Read all hash-verified evidence offline:
nfl-prop starter-coverage
```

Choose **Starter coverage** in the app. This separate audit preserves both teams in every
completed regular-season 2022–2024 schedule game, including missing source/identity/target
cases. It verifies ESPN event ID, season, week, both teams and recorded kickoff, explicit
period-zero QB flags, unique ESPN/GSIS mappings and existing same-team targets. No starter
is selected from passing volume or inferred from names. Missing targets never become zeros.

`--collect` downloads missing/failed event evidence with four workers and reuses verified
per-game checkpoints after interruption. `--refresh` requests every event again; incomplete
refreshes retain the previous active manifest and archive the attempted collection. Hash-named
raw evidence and manifests remain under ignored `data/raw/starter_coverage_2022_2024/`.
The existing flagged-label audit has its own unchanged cache. Reads verify all source hashes,
URLs, dates, denominator and input/rule bindings. `--data-dir` and `--report-dir` are supported.

The October 8 collection covers **815 games / 1,630 team-games**: **1,573** schedule labels
agree, **44** differ and **13** remain unresolved. All original 37 flagged corrections remain
resolved; seven additional discrepancies concern schedule-listed QBs who also had target rows,
so the initial missing-target check could not find them. Eight slots have kickoff disagreements,
three lack a unique QB starter flag, and two mapped roster starters lack an included target.
These cases remain open; a passing-yard target is never manufactured.

The descriptive overlay retains all **1,960 QB rows**: 1,617 match resolved roster starters,
325 are other recorded QBs, and 18 belong to unresolved slots. It does not filter training data,
change starter columns, refit/recalibrate an estimator, re-evaluate 2025 or enable forecasts.
These retrospective checks still do not establish pregame starter knowledge or the accuracy
of an upcoming-game selector. [Derived report](reports/starter_coverage/starter_coverage.md)
and [summary](reports/starter_coverage/summary.json) include a checksummed compact JSONL cross-reference
for every team-game; raw ESPN responses remain local.

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

**Current Windows validation caveat:** tests pass under Python 3.12 and 3.14, but pytest prints
native access-violation diagnostics during Polars calls.
The processes complete with exit code 0; the CLI succeeds. The local cause remains unresolved
after testing another Polars version and its compatibility runtime. No fault handler is suppressed.
Milestone 2 GitHub CI passed all 45 tests on Linux/Python 3.11 and Windows/Python 3.12 without
those native diagnostics. See [PROJECT_STATUS.md](PROJECT_STATUS.md) for current evidence.

## Architecture and learning guide

```text
nflreadpy -> cached Polars/Parquet snapshots -> source contracts
    -> quarterback-game target table -> shifted features -> audit + processed Parquet
    -> weekly training folds -> rolling forecasts + Ridge -> predictions + error report
    -> schedule/opponent context -> training/calibration/test folds -> XGBoost comparisons
    -> residual intervals + threshold probabilities -> coverage and reliability reports
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
| `src/nfl_prop_model/features/context.py` | Prior opponent totals and schedule features with coverage checks |
| `src/nfl_prop_model/modeling/uncertainty.py` | Separate calibration window, interval ranks, signed residual tails |
| `src/nfl_prop_model/modeling/research.py` | Fixed XGBoost feature comparisons and probability diagnostics |
| `src/nfl_prop_model/modeling/research_report.py` | Research provenance, tables, and offline Plotly charts |
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
appearances during evaluation. This is an appearance-conditioned research cohort; the remaining
unverified starter labels still prevent validated starter-specific conclusions.

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

Historical fetch/build and development evaluation reject 2025+ analysis. The separate
frozen 2025 diagnostic above is complete. `load_schedules` internally reads an all-seasons
file before filtering; only requested development seasons are stored/analyzed here. January 2025
games belonging to the **2024 season** are valid development data.

## Milestone 3 model and uncertainty results

```powershell
.\.venv\Scripts\nfl-prop.exe research --report-dir reports/milestone_3
```

The [research report](reports/milestone_3/research.md) compares all 1,327 evaluation QB-games
across 36 weeks with no exclusions. Its JSON companion includes every fold's cutoffs,
calibration counts, residual summaries, feature importance, source hashes, and diagnostics.
`calibration.html` contains interactive reliability plots and is regenerated, not committed.
XGBoost CPU 3.2.0 and Plotly 7.1.0 are pinned; no GPU or external chart service is needed.

Before each week, reserve the latest six eligible weeks (at least 100 rows) for calibration
and fit on earlier available results. Calibration and evaluation targets never fit the model.
Features for each row can use earlier available game results. The three tree feature sets
use the same fixed 150 depth-2 trees; there is no evaluation-based tuning or early stopping.
All six forecasts share this protocol, which differs from the larger training window in
Milestone 2. Compare models within the same report.

| Forecast | MAE (yards) | RMSE (yards) | Bias (yards) |
| --- | ---: | ---: | ---: |
| Prior-five mean | 72.06 | 92.04 | +2.42 |
| Season-to-date mean | 70.61 | 91.64 | -4.04 |
| Ridge | 69.88 | 87.54 | +10.98 |
| XGBoost: QB history | 68.15 | 85.80 | +3.94 |
| XGBoost: + schedule | **67.51** | 85.60 | +3.81 |
| XGBoost: + opponent | 67.57 | **85.30** | +3.01 |

Schedule features reduce MAE by 0.64 yards versus QB history alone, with improvement in both
seasons. Adding opponent history increases overall MAE by 0.06 yards but lowers RMSE by 0.29.
Its contribution is mixed by season. These small development-sample differences do not
establish statistical significance or choose a production model.

Schedule inputs are designated home, neutral site, and each team's elapsed days since its
previous regular-season game that season (first-game rest remains missing). Opponent inputs
are prior-five-game gross passing yards allowed and attempts faced, summed over all passers
and shifted before use. New context is saved separately; the original QB table is unchanged.
Weather is deferred pending a usable historical forecast source, stadium map, and roof policy;
see the [weather readiness note](reports/milestone_3/weather_readiness.md).

Intervals at 50%, 80%, and 90% use finite-sample corrected absolute calibration-error ranks.
Nominal 90% intervals cover **89.68%** for schedule XGBoost and **89.90%** with opponent inputs,
with mean total widths of **279.01** and **278.73 yards**. Overall coverage hides weaknesses:
for the 29 no-history cases these are **82.76%** and **86.21%**, respectively. Repeated players,
shared games, and time dependence mean the assumptions for a formal coverage guarantee are
not established; these are observed results, not per-player guarantees.

Signed residuals also yield smoothed over probabilities for fixed diagnostic thresholds
150.5, 200.5, 250.5, and 300.5 yards. Reports include Brier scores, log loss, and reliability
bins with counts. These are not historical sportsbook lines or validated betting probabilities.
Unverified starter labels, the participation-conditioned sample, and retrospective source
revisions remain limitations. Development research excludes the separate 2025 diagnostic.

Next: close weather-data readiness and investigate limited-history calibration and starter
labels before upcoming-game forecasts. Statistical accuracy does not establish profitability.

## Manual odds and local app

Install the optional interface, then regenerate research once to save calibration residuals:

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev,app]'
.\.venv\Scripts\nfl-prop.exe research
.\.venv\Scripts\streamlit.exe run app.py
```

The app serves only on `127.0.0.1` by default. Open the URL printed in the terminal.
Use **Compare a line**, **Model results**, and **Saved snapshots**. Select a historical
season/player/game/model, enter a whole- or half-yard line and signed American prices, then
compare both sides. Prices and market lines never change the point forecast or its interval.
Changing the game, model, calibration artifacts, or page clears the previous comparison.
`NFL_PROP_DATA_DIR` can select another local cache directory. No external sportsbook is connected.

The command-line equivalent uses the same engine:

```powershell
.\.venv\Scripts\nfl-prop.exe quote --player-id 00-0034857 --game-id 2024_01_ARI_BUF --model xgb_schedule --line 225.5 --over-odds=-110 --under-odds=-110 --sportsbook "Hypothetical example"
.\.venv\Scripts\nfl-prop.exe settlement-audit --report-dir reports/milestone_4
```

`quote` accepts `--data-dir`, `--report-dir`, optional `--game-spread` and `--game-total`.
Spread/total are recorded notes, not model inputs. JSON retains unrounded values; display
rounding never feeds calculations. Both American +100 and -100 mean decimal 2.0; zero,
fractional prices, and values between -100 and +100 are rejected. Quarter-yard lines are
unsupported. The [example](reports/milestone_4/example/quote.md) uses invented demonstration
prices explicitly supplied to the command, not historical market observations.

**Push-aware math:** expected profit per dollar is `p_win * (decimal_odds - 1) - p_loss`.
A push refunds the stake. Fair odds and probability edge use `p_win / (p_win + p_loss)`;
the comparison's break-even probability uses the same no-push basis. Edge is shown in
percentage points, separately from estimated EV percent. No stake is recommended.

Manual-line probabilities round point-plus-calibration-residual samples to whole yards.
Each infinite tail gets a half-count pseudocount; integer push mass is the empirical count
divided by `n + 1`. Half-yard lines have zero push mass. No fixed Gaussian variance or
selected-game outcome enters the calculation. This explicit settlement approximation
differs from the original continuous residual tail at exact boundaries. The
[settlement audit](reports/milestone_4/settlement_audit.md) measures that difference:
Ridge and all XGBoost half-line probabilities are unchanged on the four diagnostic thresholds.
Integer push estimates remain experimental: schedule XGBoost assigned zero push mass in
5 observed push cases across those four integer thresholds. The UI flags integer-line uncertainty.

**Saved snapshots** are historical research comparisons, not evidence that a prediction or
price was recorded before the original game. Each save exclusively creates a new UUID-named
JSON file in ignored `data/journal/`, retaining inputs, outputs, timestamps, model/data hashes,
and assumptions. The app has no edit/delete operation; checksums detect changed contents.
These are local files under your control, not tamper-proof storage. Files are never silently
overwritten. CLI quote reports can be regenerated; journal records are separate.

This completes Milestone 4's historical integration and begins Milestone 5. The interface
does **not** produce upcoming-game forecasts. Production eligibility/starter handling, weather,
and prospective cohort/source validation remain next work. Limited-history diagnostics,
candidate freezing and the reserved holdout assessment are now documented below.

## Calibration audit and candidate policy

```powershell
.\.venv\Scripts\nfl-prop.exe calibration-audit
```

Choose **Calibration audit** in the app to inspect 50/80/90% interval coverage, distinct
players/games/weeks, and fixed-threshold probability scores and reliability bins by pregame
history. It reads verified format-2 research caches offline, without refitting or changing
saved forecasts. `--report-dir` defaults to `reports/local/calibration`.

History-matched intervals are a diagnostic comparison with a fixed minimum of 30 same-bucket
residuals. Unavailable intervals stay missing; paired pooled metrics use the same available
subset. Row counts do not imply independent samples. The new audit rejects holdout rows,
changed protocols, incomplete model cohorts, inconsistent identities/timestamps, unavailable
calibration results, and damaged files.

[The candidate policy](MODEL_POLICY.md) selects fixed schedule XGBoost and existing pooled
calibration for development. Below five prior available model-sample games, it would abstain
from prospective probabilities and EV; historical comparisons stay available with a warning.
The reusable candidate definition is not production approval. A specific checkpoint has
now completed the 2025 diagnostic described above; preparation alone does not open outcomes.

Prepare a reproducible development estimator and a draft holdout protocol for review:

```powershell
.\.venv\Scripts\nfl-prop.exe prepare-candidate
.\.venv\Scripts\nfl-prop.exe verify-candidate --bundle models/candidates/<candidate-directory>
```

Preparation uses verified 2022–2024 caches offline. It saves a new XGBoost JSON model,
training/calibration/embargo tables, residuals, source archive, dependency versions and
content hashes under ignored `models/candidates/`. Verification checks the saved split
and exactly reproduces calibration predictions. The Markdown/JSON summary goes to
`reports/local/candidate/`. Use `--output-dir` and `--report-dir` to change these locations.

The draft protocol keeps the estimator and development residual pool fixed throughout
2025 while allowing earlier available results to supply lagged inputs. All recorded
appearances remain in diagnostics, with five-game-history results shown separately.
It differs from weekly-refit development research. Preparation does not freeze the policy
or open the holdout. Read [the artifact and protocol details](MODEL_POLICY.md).

Freeze that exact candidate before a reserved 2025 diagnostic:

```powershell
.\.venv\Scripts\nfl-prop.exe freeze-candidate --bundle models/candidates/<candidate-directory>
.\.venv\Scripts\nfl-prop.exe evaluate-holdout --frozen models/frozen/<frozen-directory>
```

Freezing uses development artifacts only. Evaluation verifies frozen executable hashes
before downloading 2025 sources, keeps the estimator/residual pool fixed, and reports
every recorded appearance plus history/week splits. The access/attempt record and raw/
processed holdout files stay under ignored `data/holdout/2025/`; Markdown/JSON summaries
go to `reports/local/holdout/`. A completed invocation reuses the saved results. An audited
failure needs `--resume` under the same freeze. Development commands still reject 2025.
See [the protocol and limits](MODEL_POLICY.md) before running it. Upcoming forecasts
remain disabled; an accessed holdout cannot become untouched validation for a revised model.
See the [dated audit](reports/calibration_policy/calibration_audit.md) for support and limits.

## Upcoming QB readiness

```powershell
.\.venv\Scripts\nfl-prop.exe upcoming --refresh
.\.venv\Scripts\streamlit.exe run app.py
```

Choose **Upcoming QBs** in the sidebar. This page works independently of the historical
research cache. It shows the next 7–28 days of dated regular-season games and all currently
listed QBs for both teams. It produces a review list, **not upcoming forecasts**.

`upcoming` downloads only 2026 schedules and ESPN-derived depth charts through `nflreadpy`.
It caches hash-verified Parquet files and timestamped manifests in `data/raw/upcoming_2026/`;
subsequent runs reuse them unless `--refresh` is supplied. `--days` defaults to 14 and accepts
1–28; `--report-dir` defaults to `reports/local/upcoming`. The UI only reads local caches.
The 2025 holdout and player-outcome datasets are not loaded by this command.

- A source download older than 24 hours or a team chart older than 48 hours is marked
  **Refresh needed**. These are application policies, not source-accuracy guarantees.
  Freshness is recomputed against the current clock when the page reruns.
- The latest complete snapshot for each team determines membership. Departed QBs do not
  linger because of older individual rows. Newcomers and missing-ID cases remain visible.
- Rank 1 is **unconfirmed**. Injuries, active status, depth-rank ties, and late game changes
  need separate checks. Schedule QB IDs are not used to declare starters.
- Started games and games with either score recorded are excluded. Unknown kickoffs are
  counted and retained in the JSON audit; times are not guessed.
- Matching 2022–2024 history provides reference counts only. Absence from a current chart
  does not establish retirement, and no historical records are deleted.

The [September 22 readiness snapshot](reports/milestone_5/upcoming.md) contains 91 current QBs,
32 upcoming games and 182 QB/game rows in a 14-day window. It is a frozen audit; refresh the
cache for current use. See JSON for timestamps, source hashes, identity gaps, and review reasons.

### Record prospective status evidence

Select a game and QB on **Upcoming QBs**, then use **Record a status review**. For each known
status, supply a source URL and its publication time, including a time zone. Use a source that
explicitly refers to this player and this game; a general depth rank is not starter evidence.
Choose `unknown` with empty evidence fields when a dated claim is unavailable. The app records
the review time automatically and rejects future publication times, stale source caches, and
games that have started or recorded a score. It rechecks the selected candidate when saving.

Starter evidence has a 24-hour window and active-status evidence a 6-hour window. **Both** the
publication time and review time must meet their window. These are conservative application
policies, not guarantees. A review of an old article does not make its evidence fresh. Active
status does not establish health, participation, or playing time. The app saves supplied links
and claims; it does not fetch or independently verify their contents.

Every save creates a new checksummed file in ignored `data/status_reviews/`, retaining the
matchup, kickoff, chart snapshot, ESPN/GSIS identity, source manifest, evidence, and notes.
The latest review available at a report's cutoff supplies **both** statuses; `unknown` clears
an earlier claim without deleting it. Changed matchup/kickoff/chart/identity requires rechecking.
Two current confirmed QBs on the same team, simultaneous reviews, or starter-plus-inactive
evidence remain flagged. When a starter changes, review the former starter as `not_starter` or
`unknown` as well. Missing-ID QBs stay visible and their identity warning remains.

The table and format-2 readiness download show manual review statuses. `nfl-prop upcoming`
reads these local reviews offline too. Review records cannot change training data or enable
forecasts; source freshness, history, identity, and model checks remain separate. The frozen
September 22 report predates this workflow and has no manual reviews.

### Archive primary status evidence and enroll participation candidates

`capture-status` fetches an HTTPS article from NFL.com or the candidate's official club
website. It checks every redirect, archives the HTML locally, reads the article's publication
timestamp, and checks exact excerpts against its visible text. Supply an explicit reviewer
annotation, a quote containing the candidate's full name, and context excerpts naming both
clubs and the scheduled week. Combined excerpts are limited to 25 words.

```powershell
nfl-prop capture-status --game-id GAME --espn-id ID --kind availability --claim ruled_out `
  --source-url https://www.CLUB.com/news/ARTICLE --quote "Full Name ... Game Status: Out" `
  --context-quote "Club-Opponent" --context-quote "Week 5"
nfl-prop current-features
nfl-prop register-participation --snapshot data/prospective/snapshot-ID
nfl-prop participation-audit --registry data/participation/cohort-ID
```

Starter annotations accept `confirmed` or `not_starter`; availability annotations accept
`active`, `inactive`, or `ruled_out`. **The excerpt's meaning is a reviewer annotation, not an
automatic semantic verification or proof of participation.** Ruled out is an injury designation,
distinct from a gameday inactive list. Active status must never be inferred from chart rank,
practice participation, or the absence of an injury designation. Articles need one unambiguous
timezone-aware publication timestamp within the preceding seven days; captures must precede
kickoff minus one hour. Reads verify both record and HTML hashes offline. Current checks retain
the existing 24-hour starter and 6-hour availability windows, require the same candidate context,
and flag contradictory claims. The **Current features** evidence panel shows these archives
separately from the older manual reviews. All upcoming forecasts remain disabled.

Enrollment fixes **every** candidate in a fresh, unchanged feature snapshot before the earliest
game cutoff. Blocked and unmapped candidates remain included, and a game cannot be enrolled
twice. A copied snapshot, protocol hash, source/code fingerprints and registration time remain
in ignored `data/participation/`. The participation audit also writes `enrollment.json`, a public
export of this fixed denominator and fingerprints that excludes raw article HTML and excerpts.

After each game, refresh 2026 sources and run `participation-audit` against the **existing** registry.
The offline audit waits for both scores, kickoff plus 24 hours, and fresh postgame source snapshots.
Explicit QB appearances retain zero attempts and zero or negative yards. Missing identities,
schedule changes and source gaps retain their rows; missing QB stats never become zero targets.
A candidate absent from the QB stats is labeled `no_recorded_qb_appearance`, which still requires
independent roster/gamebook adjudication to establish DNP or inactive status. This initial registry
does not generate predictions or validate calibration. The page exposes registry outcome counts.

[The first prospective enrollment report](reports/prospective_participation/participation.md)
preserves the protocol and status at registration. Its games have not finished; empirical
participation validation remains pending. No model fitting or new 2025 evaluation occurs.

### Reconcile the fixed pool with postgame rosters

```powershell
nfl-prop participation-reconcile --registry data/participation/cohort-ID --refresh
# Later offline reads use the verified local cache:
nfl-prop participation-reconcile --registry data/participation/cohort-ID
```

Refresh retrieves only 2026 schedule/stat snapshots, then requests ESPN event rosters for
completed **enrolled** games with fresh sources at least 24 hours after kickoff. Before any
enrolled games qualify, it makes no ESPN roster/position or player-identity requests. The app
reads cached evidence offline and shows per-candidate roster checks under **Current features**.
Missing evidence retains the original candidate and its unresolved status.

Reconciliation verifies event ID, year, regular-season week, both teams, kickoff and completion,
checks roster references and literal period-zero flags, and compares a unique ESPN-to-GSIS
mapping with the original candidate IDs. A newly mapped player does not rewrite an originally
unmapped candidate. Hashes and retrieval times protect offline evidence reads; failed refreshes
preserve the prior active manifest. The original enrollment, snapshot and protocol remain intact.

A valid non-DNP roster entry plus a recorded QB stats target corroborates participation,
including zero-yard/zero-attempt rows. Explicit DNP with no recorded QB appearance corroborates
**DNP only**, not inactive status. DNP plus a QB target is a conflict. `didNotPlay=false` with
`valid=false` is ambiguous; the roster's `active` field is ignored because it is false even for
starters in the inspected completed-game response. Missing targets remain unknown rather than
becoming zeros. NFL/ESPN coverage disagreements and identity changes remain unresolved.

The report compares the frozen enrollment-time starter annotation with retrospective starter
flags. That comparison does not verify an article's meaning, make expired evidence current,
prove gameday activation, or establish forecast calibration. The roster interpretation rules
are a separate prespecified addendum; the first enrollment's rules and 170-row denominator are
unchanged. [Read the initial reconciliation report](reports/participation_reconciliation/reconciliation.md)
and [the retrospective source-contract check](reports/participation_reconciliation/source_contract.md).
All enrolled games remain pending at this report's cutoff; forecasts stay disabled.

### Review the meaning of archived pregame claims

Choose **Pregame evidence review** in the Streamlit sidebar. The page displays the saved
article text, its official source link, publication/retrieval times, recorded claim, expiry
and review history. Supply your name, a `supported`, `contradicted` or `unclear` verdict,
and a reason, then confirm you read the article and checked the player/game context.
These are named reviewer judgments; names are self-reported and independence is not certified.

```powershell
nfl-prop claim-review-queue
nfl-prop review-claim --evidence-id ARCHIVE_ID --reviewer "Your name" `
  --verdict unclear --notes "Explain whether the archived text supports this exact claim."
```

Both commands operate offline on verified 2026 caches and archived article bytes. Each save
creates a new checksummed record under ignored `data/claim_reviews/`, bound to the entire
archive annotation and candidate context. The CLI records the caller's stated verdict;
it performs no semantic classification. The latest verdict per named reviewer supplies the
displayed result; older records remain intact. Different reviewers' verdicts, or differing
verdicts from the same reviewer at the same timestamp, produce a conflict.

Saving rechecks candidate context and source freshness. Expired articles, changed candidates,
stale source caches and kickoff-minus-one-hour cutoffs block a new pregame review. Reviewing
never restarts the original 24-hour starter or 6-hour availability window. Expired claims
and their earlier review histories stay visible. A supported verdict does not prove gameday
activation or participation and does not enable forecasts. Original capture annotations,
enrollment, training/model/calibration files and the single completed 2025 access stay unchanged.

The [dated review-readiness check](reports/pregame_claim_review/readiness.md) has two unreviewed
articles, one already expired. Fresh evidence is needed near the actual game cutoff; this
workflow does not fill in missing human verdicts or status evidence.

### Remaining forecast validation work

The fixed checkpoint and 2025 diagnostic are complete. Verify prospective feature
availability, identities and dated starter/active evidence, then validate the participation
cohort for the intended starter use. Historical starter coverage and weather remain
unfinished; unverified weather stays excluded. Revised model choices require a new
evaluation period because 2025 is now accessed.

## Sources and data rights

See [data/README.md](data/README.md) for dataset-level attribution, licenses, and transformations.
This project uses public football data, manual market input, and manual
bet placement outside the app. No sportsbook credentials, scraping, or automated betting is used.
