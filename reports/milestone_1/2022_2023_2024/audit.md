# Milestone 1 data-quality report

Seasons: 2022, 2023, 2024. Regular-season QB rows only.

Source retrieved: 2026-09-15T03:08:35.222604+00:00. This is a retrieval time, not the source's last-update time.

Pipeline version: 0.1.0. Last included kickoff: 2025-01-06T01:20:00+00:00.

## Inclusion and integrity

| Check | Count |
| --- | ---: |
| source_player_rows | 56457 |
| source_schedule_rows | 854 |
| excluded_non_qb_or_unknown_position | 54408 |
| excluded_qb_non_regular_season | 89 |
| excluded_qb_uncompleted_games | 0 |
| excluded_qb_missing_target | 0 |
| included_qb_games | 1960 |
| included_unique_qbs | 112 |
| included_zero_attempt_rows | 79 |
| included_recorded_nonstarters | 367 |
| included_unknown_starter_status | 0 |
| source_player_duplicate_key_rows | 0 |
| source_player_null_key_rows | 66 |
| source_unknown_position_rows | 66 |
| source_qb_duplicate_keys | 0 |
| source_schedule_duplicate_keys | 0 |
| unmatched_qb_game_ids | 0 |
| completed_regular_schedule_games | 815 |
| unknown_scheduled_starter_slots | 0 |

Excluded counts are sequential and sum with included rows to the source player count.
Duplicate QB/schedule keys, unmatched QB game IDs, invalid matchups, and missing kickoff times stop the build; they are never silently deduplicated or guessed.

## Sample by season

| Season | QB games | QBs | No history | <5 prior games | Zero attempts | Unlisted as starter |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2022 | 633 | 83 | 83 | 323 | 16 | 95 |
| 2023 | 663 | 81 | 18 | 110 | 30 | 119 |
| 2024 | 664 | 78 | 11 | 58 | 33 | 153 |

## Feature missingness

| Feature | Nulls | Percent |
| --- | ---: | ---: |
| passing_yards_lag1 | 112 | 5.7% |
| passing_yards_mean3 | 112 | 5.7% |
| passing_yards_mean5 | 112 | 5.7% |
| attempts_mean5 | 112 | 5.7% |
| passing_yards_season_mean | 242 | 12.3% |
| prior_games_in_sample | 0 | 0.0% |
| prior_season_games_in_sample | 0 | 0.0% |

## Scheduled starters without a target row

Count: 37. Missing statistics are never replaced with zero.

- 2022_08_LV_NO: 00-0031503 (NO)
- 2022_11_PHI_IND: 00-0036879 (IND)
- 2024_08_ARI_MIA: 00-0034177 (MIA)
- 2024_08_KC_LV: 00-0038579 (LV)
- 2024_10_NYG_CAR: 00-0027973 (CAR)
- 2024_10_PIT_WAS: 00-0032268 (WAS)
- 2024_11_MIN_TEN: 00-0034771 (TEN)
- 2024_12_KC_CAR: 00-0027973 (CAR)
- 2024_12_DET_IND: 00-0026158 (IND)
- 2024_12_DAL_WAS: 00-0032268 (WAS)
- 2024_13_TEN_WAS: 00-0032268 (WAS)
- 2024_13_TB_CAR: 00-0027973 (CAR)
- 2024_14_NO_NYG: 00-0038476 (NYG)
- 2024_14_JAX_TEN: 00-0034771 (TEN)
- 2024_15_ATL_LV: 00-0038579 (LV)
- 2024_16_TEN_IND: 00-0026158 (IND)
- 2024_17_LV_NO: 00-0038998 (NO)
- 2024_17_IND_NYG: 00-0038476 (NYG)
- 2024_17_DAL_PHI: 00-0036389 (PHI)
- 2024_17_MIA_CLE: 00-0031503 (CLE)
- 2022_11_CAR_BAL: 00-0033275 (CAR)
- 2022_15_PIT_CAR: 00-0038102 (PIT)
- 2024_08_IND_HOU: 00-0026158 (IND)
- 2024_09_WAS_NYG: 00-0032268 (WAS)
- 2024_10_TEN_LAC: 00-0034771 (TEN)
- 2024_11_WAS_PHI: 00-0032268 (WAS)
- 2024_11_IND_NYJ: 00-0026158 (IND)
- 2024_12_TEN_HOU: 00-0034771 (TEN)
- 2024_13_LV_KC: 00-0035289 (LV)
- 2024_13_IND_NE: 00-0026158 (IND)
- 2024_13_TEN_WAS: 00-0034771 (TEN)
- 2024_14_CAR_PHI: 00-0027973 (CAR)
- 2024_14_LV_TB: 00-0035289 (LV)
- 2024_16_NYG_ATL: 00-0038476 (NYG)
- 2024_16_CLE_CIN: 00-0031503 (CLE)
- 2024_16_NO_GB: 00-0038998 (NO)
- 2024_18_MIA_NYJ: 00-0036212 (MIA)

## Interpretation and limitations

- This cohort consists of QBs with a statistics row, including backups, zero attempts, and early exits. Inactive players absent from this source are not reconstructed.
- Schedule-reported starter status is an unverified audit label, not a timestamped pregame feature or a filter. Mismatches need reconciliation before starter-specific evaluation; these counts do not prove who actually started.
- Lagged history uses prior regular-season QB records across teams and seasons. Season means reset each season. Playoff appearances are excluded from history.
- Cold starts stay null; counts measure history inside the downloaded sample, not career experience. A later training fold must fit any imputation.
- Prediction time is reconstructed as kickoff minus one hour. Prior statistics are assumed available 24 hours after their kickoff; this does not recover later corrections.
- Today's historical files contain revisions. Original forecast, injury, starter, and schedule snapshots are unavailable in this milestone.
- Cancelled games absent from both sources cannot be counted as exclusions. Games present without final scores are excluded and counted.
- Weather, opponent features, market data, probabilities, EV, and model evaluation are deferred. No model-versus-baseline or profitability result exists yet.
- 2025 is reserved for later holdout work; stored development tables exclude it. nflreadpy fetches a complete schedules file internally before filtering seasons.

Full schemas and source missingness: [source_schema.md](source_schema.md). Hashes and full audit: [audit.json](audit.json).

Data attribution: nflverse contributors and Lee Sharpe, [nflverse-data](https://github.com/nflverse/nflverse-data), [CC BY 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). This report summarizes and transforms their data.
