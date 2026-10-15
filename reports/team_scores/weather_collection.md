# Weather collection and historical source check

Checked October 14, 2026, America/Chicago (October 15 UTC). Collection adds preserved context;
no score-model promotion or enriched 2025 replay occurred.

## Live collection

Two invocations of `team-weather-fetch --horizon early_week --days 7` preserved separate
versions. The second covers 14 games from Seattle–Denver through Washington–San Francisco,
70 rows: temperature, wind speed and precipitation are present in every selected hour.
Gusts are null throughout; every receipt therefore correctly says `partial`.

The pinned GFS run is October 14 at 18:00 UTC. Capture receipts are October 15 after 00:15 UTC,
before all kickoffs. The London game uses the scheduled Wembley venue city, rather than
Jacksonville. The first version's missing Washington game remains preserved; the second
correctly normalizes ESPN's `WSH` abbreviation to the schedule's `WAS`.

Both weather journals together occupy 47,870 bytes; 14 city-geocoding caches occupy 82,221
bytes (130,091 total). Full point forecast responses are discarded after extracting five
hours. Data and local probes remain ignored by Git; no global weather grids or new environment
were created. API calls and cached coordinates are documented in each receipt.

`team-weather-lookup --date 2025-09-07 --horizon early_week` returned unavailable for all
13 games on that Chicago date, correctly refusing to backdate these new captures. The lookup
made no weather API requests. It uses the existing schedule, whose historical revisions are
not reconstructed. [Single Runs](https://open-meteo.com/en/docs/single-runs-api) GFS coverage
starts in April 2026, so a different historical adapter is required.

## Returning to the replay plan

The original gate still blocks the full 272-game enriched 2025 replay: timed development
starters/weekly rosters and 2025 injury versions are unresolved; the original weather adapter
also lacks earlier wind/rain. A narrow access test found a potential alternative in the
[NCAR GFS 0.25° operational forecast archive](https://gdex.ucar.edu/datasets/d084001/).

| Initialization UTC | Valid time UTC | Approximate requested point | Temperature K | Wind u/v m/s | Precipitation rate kg/m²/s |
| --- | --- | --- | ---: | --- | ---: |
| 2022-09-06 06:00 | 2022-09-09 00:00 | Inglewood 33.95, −118.34 | 304.40253 | 5.1768236 / 0.44539794 | 0 |
| 2023-09-05 06:00 | 2023-09-08 00:00 | Kansas City 39.05, −94.48 | 304.47195 | −2.0168555 / −0.65591794 | 0 |
| 2025-09-02 06:00 | 2025-09-05 00:00 | Philadelphia 39.90, −75.17 | 297.57806 | −0.9474194 / 4.2791653 | 0.0000264 |

Four public NCSS point requests per run succeeded without an account, returning about 200
bytes each. The 2022 metadata request was 303,295 bytes. The returned grid cells are nearest
0.25° cells, not the exact requested coordinates. Files are explicitly `f066` forecasts from
Tuesday 06:00 UTC; a six-hour-ten-minute release assumption puts availability before the
Tuesday 15:00 UTC development lock. Retrieval today is not proof of original publication.

Reproduce the first point via the [temperature request](https://tds.gdex.ucar.edu/thredds/ncss/grid/files/g/d084001/2022/20220906/gfs.0p25.2022090606.f066.grib2?var=Temperature_height_above_ground&latitude=33.95&longitude=-118.34&time=2022-09-09T00%3A00%3A00Z&accept=csv&vertCoord=2).
Other variables are `u-component_of_wind_height_above_ground`,
`v-component_of_wind_height_above_ground` at `vertCoord=10`, and `Precipitation_rate_surface`
without a vertical selection. Paths change to `2023/20230905/gfs.0p25.2023090506.f066.grib2`
and `2025/20250902/gfs.0p25.2025090206.f066.grib2` for the other rows.

These are three source probes, not a season coverage audit or model test. Instantaneous
precipitation rate must not be mislabeled as hourly accumulated precipitation; converting
temperature, wind components, sampling times and precipitation requires a declared historical
feature policy. Audit outdoor/roof exposure and moved venues before feature use. This evidence
does not modify the existing source manifest or frozen research selection.

Next is an audited historical adapter and dated availability sources, followed by compact
2023–2024 comparisons. Freeze settings before the enriched 2025 weekly replay; 2025 remains
already examined diagnostic data. The active EPA model and existing MAE evidence are unchanged.
