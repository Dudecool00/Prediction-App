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

At cleanup, the tree had 63 tracked files and 25 Python source modules, down from 161 files and 60
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

## Original model plan resumed

The [simplicity check](reports/team_scores/model_simplicity/findings.md) uses the existing
weekly predictions to prevent the negligible injury gain from deciding the research choice.
Context must earn incremental improvement against compact EPA; eligible models within one
block standard error prefer fewer declared inputs. This is an explicit adaptive follow-up,
preserving the original `compact_availability` record and all previous artifacts.

Selecting on 2023 gives compact EPA for the 272-game 2024 check. Its margin MAE is 10.209
versus the same-run 28-input control's 10.325; per-team score MAE is 7.209 versus 7.311.
The 2024 paired margin gain range is −0.097 to +0.319, so the gain remains uncertain.
Both horizons are identical for this candidate because EPA history and fits lock on Tuesday;
it contains no refresh-specific context. Across 2023–2024, the separate simulated pre-2025
research choice also selects compact EPA, eight inputs with 20 allocated weights/intercepts
versus the injury candidate's 16 inputs and 52. This supports simplification, without
establishing that parameter count explains the high MAE or promoting the active model.

Stages two/three of the original plan now have a separate simplicity assessment and revised
research choice. Stage one still needs dated roster/starter/injury evidence and audited
adapters. Stage four, the full enriched 2025 weekly replay, remains gated; zero 2025 fits or
new 2025 outcomes were used. No further weather collector work was added in this follow-up.
The two derived revision caches total about 120 KB, with upstream features referenced rather than copied.
Seven new selection/timing/artifact cases bring the full suite to 78 tests.

## Dated availability evidence

The [source audit](reports/team_scores/availability_audit/findings.md) adds an outcome-free
check of all 272 scheduled 2025 games at both cutoffs. Dated ESPN depth charts provide a
listed QB leader for all 544 team-game appearances per horizon; 20 leaders change between
Tuesday and kickoff. A 24-hour assumed delay changes 11 early selections and two refresh
selections relative to zero delay. Load timestamps are not verified publication times, and
listed leaders do not establish starters or complete active/IR rosters.

Three distinct archived Week 1 injury editions now have original-entity digests matching
the preserved archive index. Eligible editions cover 28 of 32 teams before kickoff refresh:
25 with designations and three explicit empty statements. Four remain unknown; all 32 are
unknown at Tuesday's lock. One covered team has an unclassified Out position, so its position
group counts remain unknown. Current-page content and first-publication timestamps are excluded.

This advances stage one with dated evidence but leaves the enriched 2025 replay gated.
Zero fits or 2025 outcome reads occurred. Stages two/three retain their compact EPA research
choice. The next source work is broader archived injury coverage and dated development
availability; starter/active-roster confirmation and historical weather coverage remain pending.

The raw sources and processed audit occupy 239,100 bytes. Three source modules, one test file
and two compact curated reports were added; datasets/article bodies stay outside Git. Existing
environments and model caches were reused. Thirteen source/timing/integrity cases add coverage
for strict cutoffs, future-snapshot invariance, empty charts, archive receipts, gzip bounds,
missing reports, outcome exclusion and cache preservation.

## Repository health audit, October 15, 2026

Reviewed merged `main` initially at `e8ccc2c`, then incorporated `91afc64` when PR #25 merged
during the audit. The review covers eleven team-model merges since PR #15. Two merged
Windows runs (#21 and #23) failed at dependency validation because the hosted runner's
preinstalled `pipx` required a newer `platformdirs` than this project's lock. Linux passed
both runs; the initial main snapshot passed all 78 tests and checks on both operating systems.
The combined tree includes the thirteen availability cases for 91 total tests.

CI now creates a separate environment per runner and uses its interpreter for installation,
dependency checks, lint, formatting, typing and tests, with stable platform check names.
The dependency lock and all model/data source files retain their bytes. No additional local
environment or model fit was needed. Saved research/model artifacts, frozen choices, five
score journals, two weather captures, dated availability, output consistency, imports and
local documentation links are covered by the integrity audit. See
[evidence and limits](reports/team_scores/repository_health.md); final combined CI results
are recorded in PR #26.

GitHub reports `main` as unprotected with required checks off. Requiring the two platform
checks before merging is recommended; repository access rules were not modified.

## Weekly injury source coverage, October 15, 2026

The [full-season source check](reports/team_scores/weekly_injury_coverage/findings.md) now
audits all 18 weeks of 2025 using schedule metadata only. At kickoff refresh, 390/544 team
appearances have formal reports, 13 are partial/pending and 141 unavailable. All 544 Tuesday
slots remain unknown for this canonical article archive. Missing refresh data comprise 78
stale editions, 28 unverified editions, 27 missing sections and eight without an earlier
edition. Six slots lack an Out player's position, leaving position-group counts unknown.

Selected 29 editions, with 26 newly verified bodies and exact-version reuse of a prior
Week 1 proof. Unknown capture states stay explicit. Original source modules, artifacts,
model choices and replay gate remain intact; zero fits or 2025 outcome-column reads occur.
Raw collection plus current cutoff table occupy about 1.84 MB, with an 80 KB development
source probe. No new environment, training-table copy or forecast grid was added.

Official catalogs provide candidate weekly table URLs for 2022–2024, and one preserved
2023 body matches its digest. This does not yet establish timely development coverage or
an identity adapter. Next audit campaign/query variants and development tables, then test
refresh context against compact EPA before changing the freeze. Full enriched replay
remains gated; source coverage does not establish an accuracy gain.
