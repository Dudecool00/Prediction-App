# Project status

Updated October 14, 2026. Active scope: weekly NFL team final-score and margin forecasts.

## Current model

Two-output Ridge (penalty 20) uses 28 prior EPA/schedule inputs. It predicts both team scores;
signed margins and totals derive from their common nonnegative, one-decimal outputs.
Historical early-week and pre-kickoff evaluations use strictly earlier available outcomes.
Source publication/revisions are not reconstructed, and 2025 is already examined diagnostic data.

| Evidence | Early margin MAE | Result |
|---|---:|---|
| Current model, 2022–2024 | 9.811 | Retained baseline; score MAE 7.333 |
| Compact eight-input Ridge, 2022–2024 | 9.816 | Nearly equal margins with 20 weights/intercepts versus 60 |
| Prior-season refinement selection, 2022–2024 | 9.818 | No consistent margin improvement; score MAE 7.286 |
| Current model, 2025 diagnostic | 10.483 | Prior scoring reaches 10.328; neither ordering is reliable |

## Retained development tools

- [Initial evaluation](reports/team_scores/initial_backtest.md): baseline scores/margins and provenance.
- [Feature experiments](reports/team_scores/feature_experiments_backtest.md): scoring, volume and opponent EPA.
- [Chronological tuning](reports/team_scores/chronological_tuning_backtest.md): form/prior/training settings.
- [Player context](reports/team_scores/qb_injury_context_backtest.md): compact prior-passer and injury observations.
- [Playbook/SOS](reports/team_scores/playbook_schedule_backtest.md): richer plays and opponent/schedule strength.
- [2025 diagnostics](reports/team_scores/2025_diagnostics/findings.md): predicted/actual graphics and large errors.
- [Model refinement](reports/team_scores/model_refinement/findings.md): pruning, shrinkage, median loss and small trees.

Each research tool has independent caches and keeps the baseline model unchanged. Negative
comparisons remain useful evidence. The thirteen-profile refinement includes two explicitly
recorded weaker median penalties after an initial training-only underfitting audit.

## Repository cleanup

The active tree has 63 tracked files and 25 Python source modules, down from 161 files and 60
modules. Removed the standalone QB passing-yard, sportsbook/EV, old candidate/holdout and
participation workflows, their tests/reports, redundant plans and XGBoost dependency.
The app now has two team pages, and the CLI exposes only team commands.

All 18 retained team/storage/reporting source files keep their exact bytes; source/model hashes
and saved team evidence remain valid. The compact team passer/injury comparison is retained
because it supports the next context experiment. Old work is recoverable at
[commit 0ea8952](https://github.com/Dudecool00/Prediction-App/tree/0ea8952bde323f31b085373e757fd1a7e9bbd5a3).
Local raw data, forecast journals and frozen historical artifacts remain excluded from Git.

Lint, formatting, strict typing, dependency, internal-import and documentation-link checks pass.
All seven saved team cache readers pass their code/checksum guards. The retained suite has 47
team tests, including app checks, and CI runs it on fresh Linux/Python 3.11 and Windows/Python 3.12.
Local Windows native-package imports remain slow and emit the previously observed diagnostics;
tests and fault handlers remain enabled.

## Next model work

The [weekly context experiment](reports/team_scores/weekly_context/findings.md) completes the
historical source audit, development comparison and research lock. History begins in 2022;
544 games in 2023–2024 are compared per horizon with Tuesday-locked outcome features/fits.
Eight-input EPA margin MAE is 10.335 versus the same-run 28-input control's 10.482, with
per-team score MAE 7.383 versus 7.514. These are different windows/rules from the older table.
Temperature worsens the compact model. Injury counts add only 0.0007 points of early margin
improvement with two eligible team reports; pre-kickoff gains remain uncertain and score
error worsens. The exact-minimum rule mechanically locks the injury candidate for research,
but no candidate is promoted. The active model remains the original 28-input EPA model.

The enriched 272-game 2025 replay **has not run**. Its readiness gate fails because the
2022–2024 starter snapshots and 2022–2025 weekly rosters are untimed, the restored 2025 injury
archive is untimed, and earlier wind/rain forecasts are missing. A provider with dated report
versions and an audited adapter is needed before advancing. No week or retrieval date is
substituted for publication. Published forecasts and prior 2025 diagnostics remain unchanged.

The addition has three source modules, one test file, and three curated report files. Compact
source/result files total about 1.7 MB; existing environments/caches were reused and training
tables are not duplicated per fold. The new suite adds nine timing, cohort, output, attribution
and artifact checks. The combined tree has 70 tracked files, 28 source modules and 56 tests.

Next: resolve dated roster/starter/injury inputs, reconsider the extra parameters using the
recorded development evidence, then freeze a fully audited policy before the 2025 replay.
Interval calibration and preserved prospective forecasts remain subsequent validation work.

## Weather collection and resumed replay audit

`team-forecast` now captures weather on demand and links a versioned context artifact before
reporting success. Separate collection and offline date lookup commands are available. Checks
preserve five hours per game, the pinned GFS initialization, actual receipt time, venue city,
units, unknown values and checksums. Earlier captures and score manifests remain intact.
Weather is not fitted into the published score model yet.

The [live/source check](reports/team_scores/weather_collection.md) captured temperature, wind
speed and precipitation for all 14 upcoming games, including London, in 70 hourly rows.
Gusts remain unknown. Two preserved captures plus city caches occupy about 130 KB locally;
no new environment, global grids or training-table copies were added by collection.

On returning to step one of the replay plan, bounded NCAR point requests recovered archived
temperature, 10 m wind components and precipitation rate for the 2022, 2023 and 2025 openers.
This improves the earlier weather-source finding: wind/rain are absent from the selected
Open-Meteo development archive, not necessarily from every archive. Full weather coverage,
units/temporal conversion, pre-cutoff availability assumptions and an adapter remain unaudited.
The original readiness gate still fails; no enriched 2025 predictions or new MAE were produced.

Continue by resolving dated roster/starter/injury versions and auditing the historical weather
adapter, then run small 2023–2024 comparisons before freezing and replaying 2025. Extra weather
inputs must earn inclusion against compact EPA on identical games. The recorded tiny injury
gain does not justify expanding the active model. The addition has one source module, one test
file with 15 cases and this compact source-check report; CI covers 71 total tests.

[The model policy](TEAM_MODEL_POLICY.md) defines feature, cutoff, output and evaluation contracts.
