# Historical weather readiness

Checked 2026-09-17. Weather is the remaining feature-source task in Milestone 3.

The intended input is a forecast available before a game's prediction cutoff. Recorded game
weather, later reanalysis, and a retrospectively stitched forecast series do not establish
what was available at that cutoff.

## Source check

[Open-Meteo's previous-runs documentation](https://open-meteo.com/en/docs/previous-runs-api)
describes fixed lead-time fields. Its documented coverage generally starts in January 2024;
GFS temperature has an earlier archive. A Chicago-area coverage probe at 41.8623, -87.6167
for 2023-09-10 used model `gfs_global`, UTC, and these hourly fields:

| Field | Returned hours | Nonmissing values |
| --- | ---: | ---: |
| `temperature_2m_previous_day1` | 24 | 24 |
| `wind_speed_10m_previous_day1` | 24 | 0 |
| `precipitation_previous_day1` | 24 | 0 |

The HTTP response was 200. This is a single coverage probe, not a verified stadium mapping
or proof that all dates share that coverage. Parameters were sent to the documented
`previous-runs-api.open-meteo.com/v1/forecast` endpoint; both date parameters were 2023-09-10.
No weather input was added to the model or substituted with zero.

The [historical-forecast API](https://open-meteo.com/en/docs/historical-forecast-api) stitches
successive runs, so it cannot substitute for a retained forecast initialization by itself.
The [single-runs API](https://open-meteo.com/en/docs/single-runs-api) retains initialization
identity, but its documented coverage does not supply this project's full 2022–2024 window.

## Remaining work

1. Assess alternative forecast archives or a clearly labeled temperature-only experiment.
   Measure date/model/variable coverage across the full development cohort before modeling.
2. Source a historical stadium map, including relocations and neutral sites, and define what
   roof status was knowable pregame. A recorded game roof value is not automatically pregame data.
3. Cache forecast initialization, lead time, valid time, units, retrieval time, source license,
   and content hashes. Verify availability against each prediction cutoff.
4. Compare incremental error, interval coverage, and probability scores on identical cohorts,
   retaining explicit missingness. Keep the 2025 season out of source/model selection.

The current PR completes nonlinear, schedule/opponent, and uncertainty research. It does not
claim the weather portion of Milestone 3 is complete.
