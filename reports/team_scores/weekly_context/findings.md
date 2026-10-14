# Weekly context development and 2025 readiness

2022 supplies the initial history; 2023–2024 supply 544 development games per horizon. No 2021/earlier history or 2025 outcomes enter this experiment. Fits and outcome-derived EPA/rest inputs lock on Tuesday; refreshed injuries/temperature are gated by their own cutoffs. This protocol differs from the older 2016-initialized evaluation.

| model | games | margin_mae | score_mae | total_mae |
| --- | --- | --- | --- | --- |
| compact_availability | 544 | 10.334 | 7.382 | 10.280 |
| compact_combined | 544 | 10.397 | 7.482 | 10.442 |
| compact_epa | 544 | 10.335 | 7.383 | 10.279 |
| compact_temperature | 544 | 10.401 | 7.483 | 10.441 |
| league_scores | 544 | 11.103 | 7.787 | 10.408 |
| prior_team_scores | 544 | 10.356 | 7.464 | 10.249 |
| ridge_epa_scores | 544 | 10.482 | 7.514 | 10.537 |

Frozen choice: **compact_availability**. Selection requires 0.05 points of margin improvement, limited score/total regression, seasonal stability, and a positive lower bound in the approximate paired week-block range. No published model is changed.

The exact-minimum rule can select extra inputs for a tiny difference: inspect the compact-EPA comparisons in `incremental_context.csv` before interpreting the lock. They are descriptive ablations added after the initial development run, and do not change its selection rule. A lower combined-model error does not establish that the added context helped rather than the reduced EPA inputs.

The availability trial measures partial observed Out counts, not confirmed starters or complete rosters. Missing reports stay unknown. Temperature uses older GFS city forecasts with a six-hour assumed publication buffer; refresh is at least 24 hours old. Wind/rain are excluded because 2022–2023 values are unavailable. Fixed indoor effects are zero; retractable/canopy exposure stays unknown. Observed game weather/roof and actual game QB labels never enter the candidates.

## Enriched 2025 replay

**Blocked by input readiness. No enriched 2025 prediction/evaluation has been run.**

- 2022-2024 expected-starter snapshots lack publication timestamps
- 2022-2025 weekly rosters lack publication timestamps
- 2025 injury rows lack publication/modification timestamps
- 2022-2023 wind/rain forecast archive absent; temperature trial is incomplete weather

2025 depth-chart snapshots do contain timestamps, but the development sources do not. A weekly game label or file retrieval date cannot stand in for pregame publication. NFL articles can be edited after kickoff; their initial publication date does not date every injury status. A dated source provider or preserved report versions is required before the full 272-game test can pass the readiness gate.

Sources: [nflverse timing](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html), [depth archives](https://github.com/nflverse/nflverse-data/releases/tag/depth_charts), [injury archives](https://github.com/nflverse/nflverse-data/releases/tag/injuries), [forecast archive](https://open-meteo.com/en/docs/previous-runs-api).

Features/predictions are stored once in ignored local Parquet files. Folds reference training game IDs by checksum rather than copying training tables. The compact HTML chart loads Plotly from its CDN. Exact historical source publication and revisions remain assumptions; 2025 has already been examined elsewhere.

## Interpretation of the additions

Pruning the EPA inputs supplies nearly all of the observed improvement. Compact EPA improves early margin MAE by 0.147 points against the 28-input control; the approximate paired week-block range is +0.023 to +0.273. It remains essentially tied with the prior team scoring control (10.335 versus 10.356), so this is limited development evidence.

Adding injury counts to compact EPA improves early margin MAE by only 0.0007 points (paired range 0.0000 to +0.0022); only two of 1,088 development team slots have an eligible Tuesday report. The pre-kickoff margin improvement is 0.025 points (range -0.140 to +0.185), while per-team score MAE worsens from 7.383 to 7.502 and total MAE from 10.279 to 10.507. These observations do not validate the injury addition.

Temperature adds 0.065 points of early margin error and 0.100 points of score error over compact EPA. This is evidence against this coarse temperature design; it does not test reliable stadium wind/rain or genuinely recent pre-kickoff forecasts. There are 21 outdoor games with unavailable temperature values in the 2023 archive.

The exact-minimum selection rule nevertheless locks `compact_availability` because of its tiny early margin advantage. This is a mechanical research choice, not an endorsement or published-model promotion. At both season-start early fits, compact EPA allocates 20 weights/intercepts versus 52 for the injury model; both have nine active encoded inputs because the injury inputs are initially entirely unknown. An additional simplicity rule would be a new, explicitly recorded development decision.

Source/result files added locally total approximately 1.7 MB. Existing environments and caches were reused. Only this finding, the compact chart and a combined results manifest are curated in Git. [All numerical results, source audits and the lock](results.json) are included; [the interactive comparison](development.html) uses a CDN.

The timing gaps describe the nflverse/Open-Meteo sources audited here. Other forecast archives
can be assessed separately. Provider documentation was also checked: [SportsDataIO's historical
guide](https://sportsdata.io/help/historical-data-integration-guide) describes injury snapshots
at game start and says historical depth charts are unavailable. [Sportradar's historical
feeds](https://developer.sportradar.com/football/docs/nfl-ig-historical-data) include weekly
injuries/depth charts, but its [update guide](https://developer.sportradar.com/football/docs/nfl-ig-update-frequencies)
says depth charts can change during or after games. Historical endpoint availability therefore
does not establish a preserved Tuesday or one-hour-before-kickoff version. Access and historical
versions must be verified before a provider can fill the gaps; no account or subscription was created.
