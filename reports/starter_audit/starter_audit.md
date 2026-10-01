# Historical starter reconciliation · 2022–2024

Generated 2026-10-01T20:15:11.716790+00:00. Evidence retrieved 2026-10-01T20:10:51.392661+00:00.

37 flagged labels; 37 reconciled to an existing QB target row; 0 need review.
All 1960 historical QB rows are retained. The other 1593 team-game slots have not been independently verified by this audit.

| Game | Team | Schedule-listed QB | ESPN-listed starter | Resolution |
| --- | --- | --- | --- | --- |
| 2022_08_LV_NO | NO | Jameis Winston | Andy Dalton | corrected_schedule_label |
| 2022_11_CAR_BAL | CAR | Phillip Walker | Baker Mayfield | corrected_schedule_label |
| 2022_11_PHI_IND | IND | Sam Ehlinger | Matt Ryan | corrected_schedule_label |
| 2022_15_PIT_CAR | PIT | Kenny Pickett | Mitchell Trubisky | corrected_schedule_label |
| 2024_08_ARI_MIA | MIA | Tim Boyle | Tua Tagovailoa | corrected_schedule_label |
| 2024_08_IND_HOU | IND | Joe Flacco | Anthony Richardson | corrected_schedule_label |
| 2024_08_KC_LV | LV | Aidan O'Connell | Gardner Minshew | corrected_schedule_label |
| 2024_09_WAS_NYG | WAS | Marcus Mariota | Jayden Daniels | corrected_schedule_label |
| 2024_10_NYG_CAR | CAR | Andy Dalton | Bryce Young | corrected_schedule_label |
| 2024_10_PIT_WAS | WAS | Marcus Mariota | Jayden Daniels | corrected_schedule_label |
| 2024_10_TEN_LAC | TEN | Mason Rudolph | Will Levis | corrected_schedule_label |
| 2024_11_IND_NYJ | IND | Joe Flacco | Anthony Richardson | corrected_schedule_label |
| 2024_11_MIN_TEN | TEN | Mason Rudolph | Will Levis | corrected_schedule_label |
| 2024_11_WAS_PHI | WAS | Marcus Mariota | Jayden Daniels | corrected_schedule_label |
| 2024_12_DAL_WAS | WAS | Marcus Mariota | Jayden Daniels | corrected_schedule_label |
| 2024_12_DET_IND | IND | Joe Flacco | Anthony Richardson | corrected_schedule_label |
| 2024_12_KC_CAR | CAR | Andy Dalton | Bryce Young | corrected_schedule_label |
| 2024_12_TEN_HOU | TEN | Mason Rudolph | Will Levis | corrected_schedule_label |
| 2024_13_IND_NE | IND | Joe Flacco | Anthony Richardson | corrected_schedule_label |
| 2024_13_LV_KC | LV | Gardner Minshew | Aidan O'Connell | corrected_schedule_label |
| 2024_13_TB_CAR | CAR | Andy Dalton | Bryce Young | corrected_schedule_label |
| 2024_13_TEN_WAS | TEN | Mason Rudolph | Will Levis | corrected_schedule_label |
| 2024_13_TEN_WAS | WAS | Marcus Mariota | Jayden Daniels | corrected_schedule_label |
| 2024_14_CAR_PHI | CAR | Andy Dalton | Bryce Young | corrected_schedule_label |
| 2024_14_JAX_TEN | TEN | Mason Rudolph | Will Levis | corrected_schedule_label |
| 2024_14_LV_TB | LV | Gardner Minshew | Aidan O'Connell | corrected_schedule_label |
| 2024_14_NO_NYG | NYG | Tommy DeVito | Drew Lock | corrected_schedule_label |
| 2024_15_ATL_LV | LV | Aidan O'Connell | Desmond Ridder | corrected_schedule_label |
| 2024_16_CLE_CIN | CLE | Jameis Winston | Dorian Thompson-Robinson | corrected_schedule_label |
| 2024_16_NO_GB | NO | Jake Haener | Spencer Rattler | corrected_schedule_label |
| 2024_16_NYG_ATL | NYG | Tommy DeVito | Drew Lock | corrected_schedule_label |
| 2024_16_TEN_IND | IND | Joe Flacco | Anthony Richardson | corrected_schedule_label |
| 2024_17_DAL_PHI | PHI | Jalen Hurts | Kenny Pickett | corrected_schedule_label |
| 2024_17_IND_NYG | NYG | Tommy DeVito | Drew Lock | corrected_schedule_label |
| 2024_17_LV_NO | NO | Jake Haener | Spencer Rattler | corrected_schedule_label |
| 2024_17_MIA_CLE | CLE | Jameis Winston | Dorian Thompson-Robinson | corrected_schedule_label |
| 2024_18_MIA_NYJ | MIA | Tua Tagovailoa | Tyler Huntley | corrected_schedule_label |

## Limits

- ESPN event-roster starter flags are retrospective evidence, not pregame knowledge.
- Only schedule-listed starters missing a QB target row are reviewed. Other schedule starter labels remain unverified, even when a target row exists.
- Training rows, targets, features, and schedule_reported_starter remain unchanged. Corrections are a separate diagnostic overlay, never a predictor or cohort filter.
- No starter is inferred from passing volume, first passer, depth rank, or player names. Missing or conflicting starter/identity evidence remains unresolved.
- No 2025 outcomes, current-season player statistics, forecasts, or EV are loaded.
- ESPN's public endpoints have no versioned schema guarantee. Refresh requires network access; local evidence is retained by hash with retrieval timestamps.

## Sources

ESPN event rosters and [nflverse player identities](https://github.com/nflverse/nflverse-data/releases/tag/players); ESPN and nflverse contributors. nflverse identities are distributed under CC-BY-4.0. ESPN's source material remains subject to its terms; raw rosters are kept locally. The report is a derived ID cross-reference. JSON retains per-case URLs, hashes, retrieval times, and the original statistics/schedule manifest.
