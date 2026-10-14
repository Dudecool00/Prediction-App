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

1. Test timestamped starter/replacement changes against a compact EPA baseline.
2. Calibrate score/margin intervals using only earlier available residuals.
3. Score preserved prospective forecasts as outcomes become available.
4. Compare narrowly scoped special-teams/pace inputs with chronological selection.

[The model policy](TEAM_MODEL_POLICY.md) defines feature, cutoff, output and evaluation contracts.
