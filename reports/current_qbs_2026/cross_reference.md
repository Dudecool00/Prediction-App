# Current QB cross-reference: 2026

Cache retrieved: 2026-09-15T17:45:13.202530+00:00.

Source snapshot(s): 2026-09-15T12:39:14Z.

ESPN-derived depth charts distributed by [nflverse](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts), loaded through nflreadpy. [Upstream ESPN loader](https://github.com/nflverse/nflverse-rosters/blob/main/exec/update-depth-charts.R). [Field definitions](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html).

## Summary

| Measure | Value |
| --- | ---: |
| Historical QBs (2022-2024) | 112 |
| Historical QB-game rows | 1960 |
| Teams covered | 32 |
| Current chart QBs | 92 |
| Historical QBs matched to current charts | 61 |
| Historical QBs not matched | 51 |
| Historical rows lost if filtered | 429 |
| Share of historical rows lost if filtered | 21.9% |
| Current QBs without sample history | 31 |
| Current QBs without GSIS mapping | 0 |
| Historical rows actually deleted | 0 |

## Recommendation

Keep historical rows for training. Restrict the upcoming-game player selector using a fresh depth-chart/roster check, with confirmed starter and game status checked separately. Selecting historical rows by 2026 membership would use future survival information to choose the backtest cohort and discard valid injury/backup/retirement-era games.

Not matched means not found at QB by GSIS ID in this snapshot; it does not establish retirement. Free agency, injured reserve, practice squads, chart omissions, position changes, and ID issues all require separate evidence. ESPN-listed rank 1 is not a guaranteed starter. No historical rows were deleted and no model was changed.

Charts are selected per team from the latest recorded snapshot, not by each player's latest appearance. The latter would mistakenly keep departed players. This report is frozen at its source timestamp; refresh before using it for a future game.

There are 31 current QBs with no matching 2022-2024 history and 0 without a GSIS mapping. Do not exclude newcomers simply because no history matches.

Current 2026 charts cannot establish who started a particular 2022-2024 game; the earlier 37 historical starter discrepancies still need contemporaneous sources.

## Historical players not matched to a current QB chart

Retirement status is unverified for this list.

| Player | GSIS ID | Historical game rows |
| --- | --- | ---: |
| AJ McCarron | 00-0031288 | 2 |
| Anthony Brown | 00-0037175 | 2 |
| Bailey Zappe | 00-0038108 | 15 |
| Blaine Gabbert | 00-0027948 | 3 |
| Brandon Allen | 00-0032434 | 4 |
| Brett Rypien | 00-0034955 | 6 |
| Brian Hoyer | 00-0026625 | 3 |
| Bryce Perkins | 00-0035939 | 5 |
| C.J. Beathard | 00-0033936 | 10 |
| Chad Henne | 00-0026197 | 3 |
| Chase Daniel | 00-0026544 | 3 |
| Chris Oladokun | 00-0037324 | 1 |
| Chris Streveler | 00-0035752 | 1 |
| Clayton Tune | 00-0038582 | 11 |
| Colt McCoy | 00-0027688 | 4 |
| David Blough | 00-0035040 | 2 |
| Davis Webb | 00-0033550 | 1 |
| Derek Carr | 00-0031280 | 42 |
| Desmond Ridder | 00-0038122 | 24 |
| Dorian Thompson-Robinson | 00-0038583 | 13 |
| Easton Stick | 00-0035282 | 5 |
| Hendon Hooker | 00-0038550 | 3 |
| Jacob Eason | 00-0036226 | 1 |
| Jake Browning | 00-0035100 | 11 |
| Jake Haener | 00-0038998 | 8 |
| Jaren Hall | 00-0038598 | 3 |
| Jeff Driskel | 00-0032436 | 8 |
| Jimmy Garoppolo | 00-0031345 | 19 |
| Joe Milton III | 00-0039398 | 1 |
| John Wolford | 00-0034899 | 2 |
| Josh Johnson | 00-0026300 | 5 |
| Kyle Trask | 00-0036928 | 6 |
| Logan Woodside | 00-0034438 | 1 |
| Matt Barkley | 00-0030533 | 1 |
| Matt Ryan | 00-0026143 | 12 |
| Mike White | 00-0034401 | 11 |
| Nate Sudfeld | 00-0032792 | 2 |
| Nathan Peterman | 00-0033958 | 4 |
| Nick Foles | 00-0029567 | 2 |
| Nick Mullens | 00-0033319 | 13 |
| PJ Walker | 00-0033275 | 11 |
| Russell Wilson | 00-0029263 | 41 |
| Ryan Tannehill | 00-0029701 | 21 |
| Sean Clifford | 00-0038391 | 2 |
| Taylor Heinicke | 00-0031800 | 18 |
| Teddy Bridgewater | 00-0031237 | 6 |
| Tim Boyle | 00-0034177 | 7 |
| Tom Brady | 00-0019596 | 17 |
| Trace McSorley | 00-0035146 | 5 |
| Trevor Siemian | 00-0032156 | 7 |
| Will Levis | 00-0039152 | 21 |

## Current QB chart entries

| Team | Rank | Player | Historical rows | Match status |
| --- | ---: | --- | ---: | --- |
| ARI | 1 | Jacoby Brissett | 23 | matched_history |
| ARI | 2 | Gardner Minshew II | 31 | matched_history |
| ARI | 3 | Carson Beck | unmatched | no_2022_2024_sample_history |
| ATL | 1 | Michael Penix Jr. | 5 | matched_history |
| ATL | 2 | Tua Tagovailoa | 41 | matched_history |
| ATL | 3 | Cooper Rush | 27 | matched_history |
| ATL | 4 | Jack Strand | unmatched | no_2022_2024_sample_history |
| BAL | 1 | Lamar Jackson | 45 | matched_history |
| BAL | 2 | Tyler Huntley | 15 | matched_history |
| BAL | 3 | Joe Fagnano | unmatched | no_2022_2024_sample_history |
| BAL | 4 | Skylar Thompson | 9 | matched_history |
| BUF | 1 | Josh Allen | 49 | matched_history |
| BUF | 2 | Kyle Allen | 9 | matched_history |
| CAR | 1 | Bryce Young | 30 | matched_history |
| CAR | 2 | Kenny Pickett | 30 | matched_history |
| CAR | 3 | Haynes King | unmatched | no_2022_2024_sample_history |
| CHI | 1 | Caleb Williams | 17 | matched_history |
| CHI | 2 | Tyson Bagent | 9 | matched_history |
| CHI | 3 | Case Keenum | 4 | matched_history |
| CHI | 4 | Miller Moss | unmatched | no_2022_2024_sample_history |
| CIN | 1 | Joe Burrow | 43 | matched_history |
| CIN | 2 | Joe Flacco | 17 | matched_history |
| CLE | 1 | Deshaun Watson | 19 | matched_history |
| CLE | 2 | Shedeur Sanders | unmatched | no_2022_2024_sample_history |
| CLE | 3 | Taylen Green | unmatched | no_2022_2024_sample_history |
| CLE | 4 | Dillon Gabriel | unmatched | no_2022_2024_sample_history |
| DAL | 1 | Dak Prescott | 37 | matched_history |
| DAL | 2 | Sam Howell | 19 | matched_history |
| DEN | 1 | Bo Nix | 17 | matched_history |
| DEN | 2 | Jarrett Stidham | 7 | matched_history |
| DEN | 3 | Sam Ehlinger | 4 | matched_history |
| DET | 1 | Jared Goff | 51 | matched_history |
| DET | 2 | Joshua Dobbs | 17 | matched_history |
| GB | 1 | Jordan Love | 36 | matched_history |
| GB | 2 | Tyrod Taylor | 14 | matched_history |
| HOU | 1 | C.J. Stroud | 32 | matched_history |
| HOU | 2 | Davis Mills | 24 | matched_history |
| HOU | 3 | Graham Mertz | unmatched | no_2022_2024_sample_history |
| IND | 1 | Daniel Jones | 32 | matched_history |
| IND | 2 | Anthony Richardson Sr. | 15 | matched_history |
| IND | 3 | Riley Leonard | unmatched | no_2022_2024_sample_history |
| JAX | 1 | Trevor Lawrence | 43 | matched_history |
| JAX | 2 | Quinn Ewers | unmatched | no_2022_2024_sample_history |
| KC | 1 | Patrick Mahomes | 49 | matched_history |
| KC | 2 | Justin Fields | 38 | matched_history |
| KC | 3 | Garrett Nussmeier | unmatched | no_2022_2024_sample_history |
| LA | 1 | Matthew Stafford | 40 | matched_history |
| LA | 2 | Stetson Bennett IV | unmatched | no_2022_2024_sample_history |
| LA | 3 | Ty Simpson | unmatched | no_2022_2024_sample_history |
| LA | 4 | Matthew Caldwell | unmatched | no_2022_2024_sample_history |
| LAC | 1 | Justin Herbert | 47 | matched_history |
| LAC | 2 | Trey Lance | 6 | matched_history |
| LV | 1 | Kirk Cousins | 39 | matched_history |
| LV | 2 | Fernando Mendoza | unmatched | no_2022_2024_sample_history |
| LV | 3 | Aidan O'Connell | 20 | matched_history |
| MIA | 1 | Malik Willis | 15 | matched_history |
| MIA | 2 | Kyle McCord | unmatched | no_2022_2024_sample_history |
| MIA | 3 | Brady Cook | unmatched | no_2022_2024_sample_history |
| MIN | 1 | Kyler Murray | 36 | matched_history |
| MIN | 2 | Carson Wentz | 11 | matched_history |
| MIN | 3 | J.J. McCarthy | unmatched | no_2022_2024_sample_history |
| NE | 1 | Drake Maye | 13 | matched_history |
| NE | 2 | Tommy DeVito | 11 | matched_history |
| NE | 3 | Behren Morton | unmatched | no_2022_2024_sample_history |
| NO | 1 | Tyler Shough | unmatched | no_2022_2024_sample_history |
| NO | 2 | Spencer Rattler | 7 | matched_history |
| NO | 3 | Zach Wilson | 21 | matched_history |
| NYG | 1 | Jaxson Dart | unmatched | no_2022_2024_sample_history |
| NYG | 2 | Jameis Winston | 20 | matched_history |
| NYJ | 1 | Geno Smith | 49 | matched_history |
| NYJ | 2 | Cade Klubnik | unmatched | no_2022_2024_sample_history |
| PHI | 1 | Jalen Hurts | 47 | matched_history |
| PHI | 2 | Andy Dalton | 22 | matched_history |
| PHI | 3 | Tanner McKee | 2 | matched_history |
| PHI | 4 | Cole Payton | unmatched | no_2022_2024_sample_history |
| PIT | 1 | Aaron Rodgers | 35 | matched_history |
| PIT | 2 | Mason Rudolph | 12 | matched_history |
| PIT | 3 | Will Howard | unmatched | no_2022_2024_sample_history |
| PIT | 4 | Drew Allar | unmatched | no_2022_2024_sample_history |
| SEA | 1 | Sam Darnold | 33 | matched_history |
| SEA | 2 | Drew Lock | 11 | matched_history |
| SEA | 3 | Jalen Milroe | unmatched | no_2022_2024_sample_history |
| SF | 1 | Brock Purdy | 40 | matched_history |
| SF | 2 | Mac Jones | 35 | matched_history |
| SF | 3 | Kurtis Rourke | unmatched | no_2022_2024_sample_history |
| TB | 1 | Baker Mayfield | 46 | matched_history |
| TB | 2 | Jalon Daniels | unmatched | no_2022_2024_sample_history |
| TEN | 1 | Cam Ward | unmatched | no_2022_2024_sample_history |
| TEN | 2 | Mitchell Trubisky | 20 | matched_history |
| WAS | 1 | Jayden Daniels | 17 | matched_history |
| WAS | 2 | Marcus Mariota | 18 | matched_history |
| WAS | 3 | Athan Kaliakmanis | unmatched | no_2022_2024_sample_history |

Attribution: ESPN and nflverse contributors. [nflverse distribution license](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). This report filters, groups, and joins their data; no ESPN article text is reproduced.
