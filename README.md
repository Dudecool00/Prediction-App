# NFL team EPA forecasts

Predict weekly NFL final scores and scoring margins from prior offensive/defensive EPA
and schedule context. The active model is a two-output Ridge regression with 28 inputs.
Scores can be fractional: **31.2–27.5** gives **+3.7 / −3.7**, with margins derived from
the same displayed scores. The Streamlit app has **Team scores** and **Team backtest** pages.

## Setup

Python 3.11+ is required. Run these commands from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e '.[dev,app]'
.\.venv\Scripts\nfl-prop.exe team-fetch
.\.venv\Scripts\nfl-prop.exe team-build
.\.venv\Scripts\nfl-prop.exe team-evaluate
.\.venv\Scripts\streamlit.exe run app.py
```

Defaults fetch/build 2016–2024 and evaluate 2022–2024. On macOS/Linux, create the environment
with `python3 -m venv .venv` and use `.venv/bin/` executables. No API key is required.
`requirements-dev.lock` is a tested pip constraints file. Streamlit is an optional `app` extra.

## Development injury coverage

For dated development injury coverage (2022 initialization and 2023–2024 development):

```powershell
.\.venv\Scripts\nfl-prop.exe team-injury-development-fetch
.\.venv\Scripts\nfl-prop.exe team-injury-development-audit
```

These commands use the existing canonical team cache, including those seasons. The bounded
archive collection resumes completed weeks; the audit runs offline and reads schedule metadata
only. Reports go to `reports/local/team_injury_development/`. Blank game-status cells remain
unknown, and observed designations do not establish a complete final report. This source audit
does not train models or clear the enriched 2025 replay gate.
See the [development coverage findings](reports/team_scores/development_injury_coverage/findings.md)
for the measured source gaps and next comparison requirements.

To qualify weekly news reports using the 2025 article definition:

```powershell
.\.venv\Scripts\nfl-prop.exe team-injury-news-fetch
.\.venv\Scripts\nfl-prop.exe team-injury-news-audit
```

These commands default to 2023–2024. Run `--seasons 2022` separately for initialization,
so the existing development collection can be reused without downloading those years again.
Canonical and recognized tracking-link editions retain separate archive proofs. Each tracking
edition must identify the same canonical article before its counts can be used in this source
audit. The latest whole edition governs each cutoff; tied or unverified editions remain unknown.
Reports include canonical-only coverage, combined coverage and games with both teams covered.
They are saved under `reports/local/team_injury_news/`. Source qualification performs no model fits.
See the [weekly-news findings](reports/team_scores/development_injury_news/findings.md)
for measured coverage and the proposed small injury comparison.

## Current-season forecasts

Include every season through the current NFL season. For 2026:

```powershell
$teamSeasons = 2016..2026
.\.venv\Scripts\nfl-prop.exe team-fetch --seasons $teamSeasons
.\.venv\Scripts\nfl-prop.exe team-build --seasons $teamSeasons
.\.venv\Scripts\nfl-prop.exe team-forecast --horizon early_week
```

Refresh current-season sources with `team-fetch --seasons 2026 --refresh`, rebuild with the
full season list, then run `team-forecast --horizon pregame_refresh` 1–24 hours before kickoff.
Commands are manual. Each forecast records its actual run time, features, training inputs,
and source/model hashes in a separate `data/journal/team_scores/snapshot-*/` directory.
`team-forecast` first collects a new game-window weather version, then links it in `weather.json`
beside the score manifest. Collection checks the live schedule and actual venue city, including
neutral sites. Temperature, wind speed, gusts and precipitation cover five hourly timestamps;
unavailable values remain null. Weather is collected context: the EPA score weights are unchanged.
The app reads saved snapshots offline. `NFL_PROP_DATA_DIR` sets its data directory;
CLI commands accept `--data-dir`.

Weather can also be collected independently, with an optional Chicago game date:

```powershell
.\.venv\Scripts\nfl-prop.exe team-weather-fetch --horizon early_week --days 7
.\.venv\Scripts\nfl-prop.exe team-weather-fetch --horizon pregame_refresh --date 2026-10-15
.\.venv\Scripts\nfl-prop.exe team-weather-lookup --date 2025-09-07 --horizon early_week
```

Use a future date within the requested window for collection. Refresh accepts games 1–24 hours
away. Past dates require the offline lookup, which uses the cached schedule and only receipts
captured strictly before Tuesday's cutoff or kickoff minus one hour. Missing historical versions
stay unavailable. Today's forecast cannot fill a 2025 gap. Live runs record their actual prompt
time, which can differ from the fixed historical cutoff. Repeated calls preserve earlier versions.
If live and cached score schedules disagree, refresh/rebuild the team cache before linking them.
The [collection check](reports/team_scores/weather_collection.md) records coverage and storage.

Historical early-week forecasts use Tuesday 10:00 America/Chicago before the week's first game;
refreshes use kickoff minus one hour. Earlier results become eligible at kickoff plus 24 hours,
a retrospective availability assumption. History may contain later source revisions.
The 2025 season has already been examined and is diagnostic data.

## Model development

| Command | Purpose |
|---|---|
| `team-experiment` | Compare scoring history, play volume and opponent-adjusted EPA |
| `team-tune` | Select training/form/prior settings using preceding seasons |
| `team-context-fetch`, `team-context-build`, `team-context-evaluate` | Compare compact passer-history and injury context |
| `team-playbook-fetch`, `team-playbook-build`, `team-playbook-evaluate` | Compare play-by-play tendencies, opponent strength and SOS |
| `team-diagnose --season 2025` | Plot score/margin predictions against actual outcomes |
| `team-refine` | Compare compact inputs, regularization, loss and small nonlinear models |
| `team-pregame-audit` | Audit roster/starter/injury timing and cache compact development temperature forecasts |
| `team-walkforward` | Compare weekly locked context models on 2023–2024 and freeze a research choice |
| `team-replay-check` | Check input readiness for the enriched 2025 replay; blocked inputs prevent any fit |
| `team-simplicity-check` | Reuse weekly predictions to assess simpler selection on 2024 and record a separate pre-2025 research choice |
| `team-availability-fetch` | Cache dated 2025 QB depth snapshots and a small preserved Week 1 injury-report pilot |
| `team-availability-audit` | Check source coverage at 2025 forecast cutoffs using schedule metadata only |
| `team-injury-fetch` | Cache only needed editions of canonical 2025 weekly injury articles, within an 8 MB body budget |
| `team-injury-audit` | Audit all 18 weeks at both cutoffs offline; no model fit or outcome-column reads |

Prefix commands with `.\.venv\Scripts\nfl-prop.exe`. Fetch/build the required seasons before
evaluation; a 2025 diagnostic needs a team cache including 2025. Research outputs go to
ignored `reports/local/` and separate processed namespaces. Comparisons retain common games,
strict outcome cutoffs, coherent scores/margins, and source/checksum guards. Model changes
require assessment of later-game performance; research commands do not promote candidates.

The current model's 2022–2024 early-week margin MAE is **9.811 points** and per-team score MAE
is **7.333**. Its 2025 diagnostic margin MAE is **10.483**. The eight-input Ridge nearly matches
development margin accuracy at **9.816**, but the refinement selection does not reliably
improve margins. The current model remains active.

Read [the model policy](TEAM_MODEL_POLICY.md), [current status](PROJECT_STATUS.md), and
[refinement findings](reports/team_scores/model_refinement/findings.md). Retained reports
under `reports/team_scores/` document earlier comparisons and their provenance.

The weekly context experiment starts history in **2022**, compares candidates on **2023–2024**,
and freezes outcome-derived history and training at Tuesday's cutoff. Both horizons fit on
earlier available games; the refresh updates context without learning same-week game results.
The source audit currently blocks the enriched 2025 replay: development starter snapshots and
weekly rosters are untimed, restored 2025 injuries are untimed, and 2022–2023 forecast wind/rain
are absent from the selected Open-Meteo archive. A small NCAR point-forecast probe has now
verified an alternative for older wind/rain; its historical adapter and full coverage audit are
still pending. The temperature trial is explicitly partial. The published model stays active.

Reuse your existing environment and caches. The commands need a team cache including
2022–2025 and a context cache including 2022–2024. If those seasons are absent, fetch/build
them with the existing commands and explicit `--seasons` lists before running:

```powershell
.\.venv\Scripts\nfl-prop.exe team-pregame-audit
.\.venv\Scripts\nfl-prop.exe team-walkforward
.\.venv\Scripts\nfl-prop.exe team-replay-check
```

Outputs go to `reports/local/team_walkforward/`. The audit caches selected QB rows and one
weather hour per game; no global forecast grids or new Python environment are needed.
The experiment stores one feature/prediction table and fold references, avoiding copies of
the training data per weekly fit. Its chart uses Plotly's CDN to keep the HTML small.

The [simplicity follow-up](reports/team_scores/model_simplicity/findings.md) requires each
context addition to earn improvement against compact EPA itself, then prefers fewer inputs
within one block standard error of the best eligible error. It selects on 2023 for the
following 2024 check and on 2023–2024 for a separate simulated pre-2025 choice. Both choose
compact EPA. This is an explicit adaptive development decision after seeing earlier results;
it preserves the original lock, performs no new fits, and does not use 2025 outcomes.
Run `team-simplicity-check` after `team-walkforward`; outputs use `reports/local/team_simplicity/`.
The enriched replay still requires the dated availability audit and validated adapters.

The [dated availability audit](reports/team_scores/availability_audit/findings.md) now finds
listed QB leaders for all 544 team-game appearances at both 2025 cutoffs, with 20 changes
between Tuesday and kickoff. The Week 1 injury pilot covers 28 of 32 teams at the refresh
and none at Tuesday's lock. These are source-coverage results, with no fits or outcome reads.
Listed depth leaders are not confirmed starters; earlier development roster versions,
full-season injuries and historical weather coverage still need work before the enriched replay.

With the existing team cache including 2025, run:

```powershell
.\.venv\Scripts\nfl-prop.exe team-availability-fetch
.\.venv\Scripts\nfl-prop.exe team-availability-audit
```

The fetch reuses validated local sources. The audit runs offline and writes to
`reports/local/team_availability/`. Selected QB rows, whole-team snapshot counts, three
compressed archived injury editions and the cutoff audit occupy about 239 KB locally.
The original readiness gate and model research choices remain intact.

The [weekly injury follow-up](reports/team_scores/weekly_injury_coverage/findings.md) extends
the source check to all 18 weeks. Formal reports cover **390/544** team appearances at
kickoff refresh; 13 are partial and 141 unavailable. None of this archive's editions is
eligible by Tuesday. These gaps remain unknown and no new score weights are fitted.
Run `team-injury-fetch`, then `team-injury-audit`; output uses
`reports/local/team_injury_coverage/`. New raw/audit/probe data occupy about 2 MB locally.
Archived query variants and dated development tables need further auditing before context
comparisons, a revised freeze and the enriched 2025 replay. Existing caches are reused.

## Repository and validation

The next bounded injury experiment uses separately qualified 2022 initialization and the
existing 2023-2024 dated news audit. It compares compact EPA with report-availability controls
and two coarse Out-count contrasts, retaining every development game. Its
[declared protocol](reports/team_scores/dated_injury_comparison/protocol.md) requires improvement
against both EPA and availability-only; QB counts do not establish the starting QB.

```powershell
.\.venv\Scripts\nfl-prop.exe team-injury-news-fetch --seasons 2022
.\.venv\Scripts\nfl-prop.exe team-injury-news-audit --seasons 2022 --report-dir reports/local/team_injury_news_2022
.\.venv\Scripts\nfl-prop.exe team-dated-injury-compare
```

The comparison also requires the existing `team-walkforward` and 2023-2024 news audit caches.
It reuses source data and stores context deltas and one prediction panel under ignored local
directories. It neither runs 2025 nor clears the enriched replay gate. The PEM-inspired
three-model comparison follows completion of the original replay plan.

- `src/nfl_prop_model/data/`: source snapshots, validation and content-addressed storage.
- `src/nfl_prop_model/features/`: past-only EPA and experimental matchup inputs.
- `src/nfl_prop_model/modeling/`: forecasting, chronological comparisons and diagnostics.
- `src/nfl_prop_model/app.py`, `team_app.py`, `cli.py`: app and command entry points.
- `tests/`: team data, timing, model, artifact and UI regression checks.
- `reports/team_scores/`: reproducible results, findings and plots.

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

CI checks Linux/Python 3.11 and Windows/Python 3.12 in isolated virtual environments so
preinstalled runner packages cannot conflict with the project's pinned dependencies.
The [repository health audit](reports/team_scores/repository_health.md) records merge/check
history and saved-artifact validation. The package and `nfl-prop` entry point keep
their existing names for compatibility. Historical QB passing-yard and betting workflows are
recoverable from [the pre-cleanup commit](https://github.com/Dudecool00/Prediction-App/tree/0ea8952bde323f31b085373e757fd1a7e9bbd5a3).
Raw datasets, local reports, model files and forecast journals stay outside Git;
[data notes](data/README.md) describe their storage and attribution.
