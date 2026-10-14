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
The app reads saved snapshots offline. `NFL_PROP_DATA_DIR` sets its data directory;
CLI commands accept `--data-dir`.

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

## Repository and validation

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

CI checks Linux/Python 3.11 and Windows/Python 3.12. The package and `nfl-prop` entry point keep
their existing names for compatibility. Historical QB passing-yard and betting workflows are
recoverable from [the pre-cleanup commit](https://github.com/Dudecool00/Prediction-App/tree/0ea8952bde323f31b085373e757fd1a7e9bbd5a3).
Raw datasets, local reports, model files and forecast journals stay outside Git;
[data notes](data/README.md) describe their storage and attribution.
