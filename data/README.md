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
