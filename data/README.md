# Data provenance and storage

Verified against actual `nflreadpy` 0.1.5 output on 2026-09-15 UTC. The initial downloaded
seasons are 2022, 2023, and 2024. No raw or processed datasets should be committed to Git.

| Dataset | Loader | Attribution / source | License |
| --- | --- | --- | --- |
| Weekly player statistics | `load_player_stats(seasons, summary_level="week")` | nflverse contributors, [stats_player release](https://github.com/nflverse/nflverse-data/releases/tag/stats_player) | [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md) |
| Games and schedules | `load_schedules(seasons)` | Lee Sharpe and nflverse contributors, [schedules release](https://github.com/nflverse/nflverse-data/releases/tag/schedules), [nfldata](https://github.com/nflverse/nfldata) | [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md) |
| Current 2026 depth charts | `load_depth_charts([2026])` | ESPN and nflverse contributors, [depth_charts release](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts) | [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md) (nflverse distribution) |

The downloaded assets are `stats_player/stats_player_week_{season}.parquet` and
`schedules/games.parquet` under the nflverse-data release-download URL. No headshot images are
downloaded or redistributed; source URL strings remain in the ignored raw table.

The [nflreadpy package](https://nflreadpy.nflverse.com/) itself is MIT licensed, distinct from
the data license. [Player-stat documentation](https://nflreadr.nflverse.com/reference/load_player_stats.html)
describes the box-score target; the [schedule dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)
documents Eastern kickoff times. These references inform the explicit source contract; the actual
downloaded schemas, including changes such as `team` and `game_id`, are preserved in each manifest.

Transformations: persist the returned Polars frames to Parquet, select regular-season QB records,
validate the schedule join and game completion, rename target/diagnostic fields, convert times to
UTC, and compute lagged regular-season features. nflreadpy filters the all-seasons schedule
download before it is cached here. These are transformed snapshots, not copies of upstream byte
streams. Hashes identify the local Parquet files.

`raw/<seasons>/` stores hash-named source frames and timestamped manifests. `manifest.json`
selects the current snapshot. `processed/<seasons>/` stores hash-named modeling tables and a
manifest linking each table to its source inputs. Cache reads verify SHA-256; damaged caches fail
without a hidden redownload. Refreshes preserve older raw files and manifests. Retrieval time is
not the source's update/publication time. Historical corrections are a known as-of limitation.

The audit reports all source missingness, duplicate/null identity counts, sequential exclusions,
cold starts, zero-attempt appearances, and missing schedule-listed starters. No 2022-2024
injury, depth-chart, weather forecast, or market-price datasets have been ingested. A stadium
reference table and its provenance will be added when weather features are implemented.

## Current depth-chart cross-reference

`raw/current_depth_2026/` caches `depth_charts/depth_charts_2026.parquet` as returned by nflreadpy.
The [nflverse updater](https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R)
uses `nflverse.espn::espn_depth_charts` and nflverse's ESPN-to-GSIS ID mapping. Direct ESPN pages
were not used as a second independent validation. The cache manifest records our retrieval time,
observed schema, nflreadpy version, local hash, release link, and distribution license.

The post-2024 [depth-chart schema](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html)
uses `dt` snapshots rather than the older weekly format. The audit selects the latest recorded
chart per team at or before cache retrieval, then selects `pos_abb == "QB"` and validates all
32 teams. It joins `gsis_id` to historical `player_id`, retaining unmatched players for review.
It neither infers retirement nor edits training data. ID mappings may be revised upstream.

The checked snapshot is 2026-09-15 12:39:14 UTC; 92 QBs across 32 teams. Its derived Markdown/JSON
reports in `reports/current_qbs_2026/` retain attribution, source timestamps, and historical-table
hashes. Source `dt` and our retrieval time are distinct. These current charts are not evidence of
what was known before historical games; refresh and verify roster/game status for future use.

Tests use hand-authored fictitious teams/players and numerical examples, not redistributed data.
If sharing generated tables or reports, preserve the source attribution, license link, and
description of changes above. No endorsement by the NFL or nflverse is implied.

## Baseline evaluation outputs

`processed/baselines_2022_2024/` stores hash-named out-of-fold predictions and a manifest linking
them to the unchanged historical table. `reports/milestone_2/` contains aggregate metrics and
fold-level preprocessing/coefficient diagnostics. Training uses 2022 for warmup and earlier
available games for each 2023-2024 week. No 2025-season results or market prices are ingested.
Prediction errors are derived research outputs, not a historical betting-return dataset.

## Nonlinear model and uncertainty outputs

`processed/research_2022_2024/` stores separate hash-named context features, point predictions,
interval bounds, and diagnostic threshold probabilities. It preserves the original QB table
and checks that its raw manifest matches the cached player/schedule inputs before joining.
No new football dataset is downloaded by `research`.

Opponent totals sum recorded passing yards and attempts across all positions, then shift
prior-five regular-season team-game means. Yards are gross before sack subtraction. Missing
team-game coverage fails; first-history values stay missing. Rest is elapsed days since the
previous regular-season game in the same season, with no value for a season's first game.
Designated home and neutral-site status come from the matched schedule. Retrospective schedule
corrections remain an as-of limitation. Roof and observed weather columns are excluded.

The report manifest records context missingness, all input/output hashes, environment, model
settings, fold cutoffs, calibration samples, empirical coverage, and probability diagnostics.
Historical weather is not ingested; [readiness findings](../reports/milestone_3/weather_readiness.md)
explain the missing source coverage. Diagnostic thresholds are not historical market data.

Research manifest format 2 adds `calibration_residuals`, with fold/model, source player/game
IDs, UTC cutoff/availability evidence, and signed errors from the separate calibration window.
Regenerate `research` once after upgrading. The original four artifacts remain byte-identical.
Manual quotes verify artifact hashes and calibration timing before pricing a custom line.
`data/journal/` contains user-saved, append-only-through-the-app research snapshots and is
ignored by Git. They capture today's analysis of historical forecasts, not original pregame logs.
## Upcoming 2026 source cache

`nfl-prop upcoming --refresh` creates `raw/upcoming_2026/` with nflreadpy 2026 schedules
and ESPN-derived depth charts. Each dataset is stored by SHA-256 and each retrieval keeps
a timestamped manifest. Refresh does not replace older snapshots or alter historical tables.
The UI reads only verified local files and recalculates source age on rerun. No 2025 player
outcomes or current-season player statistics enter this command.

Schedule attribution: [Lee Sharpe / nflverse games](https://github.com/nflverse/nfldata/blob/master/data/games.csv).
Depth-chart attribution: [ESPN via nflverse](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts),
[updater](https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R).
nflverse distribution: CC-BY-4.0. Times are converted from Eastern to UTC, team snapshots
are filtered, and QBs are joined to future dated games. Rank does not confirm starting or
active status; the source does not establish a player's retirement.

## Manual prospective status reviews

`data/status_reviews/` is ignored by Git. Each UI save exclusively creates a checksummed JSON
record with an ID, local UTC review time, full source manifest, selected 2026 candidate context,
separate starter/active claims, their supplied source URLs/publication times, and notes. The app
has no edit/delete API. Checksums detect changed contents; they do not authenticate the human
reviewer or prove source accuracy. Raw source pages are not downloaded or redistributed.

Verified offline reads reject corrupted records and mismatched filenames. Reports use only
records saved by their cutoff, selecting the latest review per game/team/ESPN identity. A later
`unknown` claim clears earlier status. Kickoff, opponent, home/away designation, GSIS identity,
depth rank, or team-chart snapshot changes invalidate the review. Starter evidence expires
after 24 hours, active evidence after 6 hours, measured from both source and local review time.
Conflicting claims remain visible. Saving requires fresh underlying schedule/chart caches and
a future, unscored game. This is a manual review overlay; historical model data remains unchanged.

## Primary articles and prospective participation enrollment

`capture-status` writes append-only `status_evidence/evidence-ID/` directories with an
archived HTML article and a checksummed annotation. HTTPS hosts are limited to NFL.com or
the candidate's official club domain, with every redirect checked. Source publication metadata,
retrieval time, exact short excerpts, full candidate/game/chart context and source-manifest
fingerprints are retained. These archives are local and ignored by Git; the project does not
assign nflverse's license to club articles or redistribute full pages. An annotation expresses
the reviewer's reading of the excerpt. Matching bytes and dates do not establish claim accuracy,
active status, or actual participation. Reading reports performs no article download.

`participation/cohort-ID/` copies the complete pregame feature snapshot and its fixed enrollment
protocol. A local exclusive lock serializes enrollment; each game can occur in only one registry.
All candidates, including unknown identities and blocked rows, remain enrolled. The registry
rejects registration after any game's kickoff-minus-one-hour cutoff and verifies hashes offline.
Derived audits use only local, verified 2026 schedules/stats. Missing outcome coverage remains
unknown; zero-valued recorded appearances remain valid observations. No 2025 evaluation,
estimator execution or production prediction is part of this workflow.

The public `enrollment.json` export contains candidate identities/game contexts, feature hashes,
blockers, publication/retrieval times and source/code/registry fingerprints. It omits article
HTML and quoted text. Its schedule/stat/chart attribution remains nflverse's CC-BY-4.0
distribution, with ESPN-derived depth charts. Club evidence retains its own source URL and rights.

## Historical starter evidence (retrospective)

`nfl-prop starter-audit --refresh` adds `raw/starter_evidence_2022_2024/`, separate from training
and upcoming caches. Only the flagged historical team/game slots are queried. The command
retains the ESPN event identity and roster response plus the QB position definition; it uses
`nflreadpy.load_players()` for a current ESPN-to-GSIS reference map. The identity table contains
IDs, name, and position, with no player outcomes. Summary odds and market fields are discarded.

ESPN provides the event-roster `starter`, `didNotPlay`, `valid`, and `period` flags. Event ID,
regular-season year/week, both teams, and the roster's event/team reference must agree. The
unique QB starter is mapped by IDs and checked for an existing same-team QB target row.
No player is assigned by name or statistical volume. Missing or conflicting evidence remains
unresolved. This source is retrospective and does not establish historical pregame knowledge.

JSON evidence and the identity Parquet file are hash-verified; refreshes retain old files and
manifests. A failed source-validation refresh leaves the active manifest intact. ESPN has no
versioned schema guarantee for these public endpoints. Raw ESPN rosters remain local and are
not redistributed or assigned nflverse's license. The nflverse identity distribution is CC-BY-4.0.
[The derived reconciliation report](../reports/starter_audit/starter_audit.md) retains attribution
and per-case source URLs/times/hashes. No model input, outcome, or original starter flag is changed.

## Calibration diagnostics and policy provenance

`nfl-prop calibration-audit` reads only the hash-verified features, predictions, and calibration
residuals in `processed/research_2022_2024/`. It checks the exact saved research protocol,
development seasons, complete six-model cohorts, feature identities/history/timestamps, fold
counts/cutoffs, and calibration result availability. Calibration history groups join to their
own game's pregame feature row. No model is refitted and no source or research cache is written.

The generated report preserves the research manifest hash, artifact hashes, historical source,
code provenance, candidate policy and its hash, and per-fold group support. Generation rejects
a manifest changed during reading. Outputs default to ignored `reports/local/calibration/`.
The committed Markdown and policy JSON are derived snapshots; regenerate full diagnostic JSON
with the CLI. They do not expose a frozen estimator or establish that source revisions were known
before the historical games. Source data attribution remains nflverse / CC-BY-4.0.
