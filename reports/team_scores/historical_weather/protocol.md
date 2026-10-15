# Historical weather adapter policy

Recorded October 15, 2026, before collection and any weather model fits. This step
collects context and audits coverage; it makes no model selection or 2025 score claims.

- Retain all 1,087 regular-season games in 2022–2025 and both prediction horizons,
  including the 271 completed games in 2022. Select only schedule metadata, never scores,
  actual starters, postgame roof state or observed weather.
- Lock early forecasts on Tuesday at 10 a.m. America/Chicago. Refresh context at kickoff
  minus one hour; this collector does not update EPA history, train a model or reveal results.
- Use the [NCAR operational GFS archive](https://gdex.ucar.edu/datasets/d084001/),
  DOI 10.5065/D65D8PWK, CC BY 4.0. Request selected points through
  [OPeNDAP projections](https://opendap.github.io/documentation/QuickStart.pdf).
- Select the latest 00/06/12/18 UTC run whose initialization plus six hours ten minutes
  is strictly before the cutoff. This is an assumed publication bound. Today's retrieval
  does not establish actual historical publication time or an immutable historical archive.
- Use the nearest three-hour forecast time to kickoff, ties toward the later time,
  maximum absolute offset 90 minutes. Require a positive forecast lead of 3–240 hours;
  the archive path uses the initialization's calendar year, including January games.
- Request instantaneous 2m temperature, 10m u/v wind, and surface precipitation rate.
  Check run reference, valid time, units, forecast process, heights and returned grid cell.
  Convert Kelvin to Celsius and wind components to magnitude in m/s. Convert precipitation
  rate to mm/h, preserving that this is a rate equivalent, not accumulated hourly rainfall.
  Gusts and precipitation type are unavailable. NaN remains missing; zero rain is valid.
- Reuse checksum-bound city coordinates and old venue metadata without modifying them.
  City/grid values are coarse approximations. Correct seven 2025 international assignments
  using the [preseason NFL announcement](https://www.nfl.com/news/nfl-announces-2025-international-games-to-feature-seven-games-in-five-countries).
  This static venue assumption does not reconstruct every historical schedule revision.
  Exclude moved 2022 CLE–BUF weather at both cutoffs. Fixed-indoor exposure is zero;
  retractable/canopy exposure is unknown, including Madrid. Weather stays null for both.
- Preserve bounded compressed response bodies and receipt times/checksums outside Git.
  Cap each metadata response at 600 kB, each point response at 20 kB, compressed raw data
  at 24 MB, and requests at two concurrent workers. No new environment or global grids.
  Retry transient errors once; integrity or schema errors fail visibly. Resume valid cached
  responses. Failed receipts require explicit `--retry-failed` and retain prior attempts.
- Rebuild the complete coverage table offline from raw responses; retain source-unavailable
  and partial rows. Keep model code, frozen historical evidence and the existing replay gate
  unchanged. No weather effect or MAE improvement is claimed by coverage alone.

Reproduce using an existing canonical team cache and pregame venue/city audit:

```powershell
nfl-prop team-historical-weather-fetch
nfl-prop team-historical-weather-audit
```

Outputs default to ignored `reports/local/team_historical_weather/`. Dated development
roster/starter inputs and a compact development weather comparison still precede the
full enriched 2025 replay. The previously examined 2025 season is diagnostic data.
