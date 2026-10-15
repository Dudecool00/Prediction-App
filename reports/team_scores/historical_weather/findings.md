# Historical weather source audit

1087 games, 2174 game/horizon records, zero model fits.

Statuses: `{'complete': 1431, 'roof_unknown': 472, 'fixed_indoor': 268, 'moved_venue_excluded': 2, 'source_unavailable': 1}`. Raw cache: 7,266,818 bytes.

| Season | Games | Complete Tuesday weather | Complete refresh weather |
| --- | ---: | ---: | ---: |
| 2022 | 271 | 177 | 177 |
| 2023 | 272 | 180 | 180 |
| 2024 | 272 | 178 | 177 |
| 2025 | 272 | 181 | 181 |

All 1,432 outdoor game/horizon records were attempted. The only remaining failure is the
refresh for `2024_07_BAL_TB`: the October 21, 2024 12 UTC `f012` metadata request returns
HTTP 404. No alternate run is substituted. A bounded resumption recovered 136 transient
DNS/connection failures and retained earlier failure receipts. All 2025 outdoor slots have
the three requested fields; 33 fixed-indoor and 58 uncertain-roof games remain distinct.

Reconstructed from checksummed responses offline. Run reference, valid time, grid cell, heights, forecast process, and units are checked for each accepted point. The six-hour-ten-minute publication delay remains an assumption; retrieval today does not prove historical publication. Each value is the nearest three-hour kickoff forecast.

Temperature is Celsius, wind is m/s, and precipitation is instantaneous rate in mm/h, not hourly accumulation. Gusts and precipitation type are unavailable. Indoor and unknown roof rows retain null weather; fixed-indoor exposure is zero. Missing source rows are retained.

Seven 2025 international venue assignments override the old domestic defaults using the [preseason announcement](https://www.nfl.com/news/nfl-announces-2025-international-games-to-feature-seven-games-in-five-countries). This is a static announced-venue assumption, not a reconstruction of schedule revisions. The moved 2022 CLE-BUF game remains excluded. City coordinates are approximate, not stadium measurements.

No active model, frozen research result, old source manifest or replay gate was changed. Next: a compact 2023-2024 weather comparison and dated roster/starter source decision. The full enriched 2025 replay remains blocked; 2025 is previously examined diagnostic data.

The raw namespace plus both preserved processed audit versions occupy **7,459,261 bytes**
(about 7.46 MB). No global grids, environment or per-fold training copies were added.
The weather report summaries occupy about 15 KB in Git; raw responses and city sources
remain ignored. Storage totals exclude existing inputs, temporary test outputs and the
separate small roster access probe.

Validation: all **192 tests** pass locally and in
[Windows/Python 3.12 and Linux/Python 3.11 CI](https://github.com/Dudecool00/Prediction-App/actions/runs/38808133236).
The 27 new tests cover a real source fixture, strict cutoff equality, January/run years,
unit/process/time/height/cell mismatches, malformed/oversized responses, missingness,
venue overrides, full-cohort retention, outcome-column exclusion, checksummed resumption
and offline auditing. Ruff, formatting and mypy pass. The existing dated-injury artifact
reader still reproduces its compact-EPA research choice without refitting.

A [verified official depth-chart pilot](remaining_sources.md) identifies a possible next
source for earlier roster timing. It does not clear the remaining roster/starter gate.

Source: [NCAR GFS operational archive](https://gdex.ucar.edu/datasets/d084001/), DOI 10.5065/D65D8PWK, CC BY 4.0. Geocoding: [Open-Meteo](https://open-meteo.com/en/docs/geocoding-api), CC BY 4.0.
