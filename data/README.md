# Local team research data

Public nflverse datasets are accessed through `nflreadpy`. See
[nflverse](https://nflverse.nflverse.com/) and
[nflverse-data](https://github.com/nflverse/nflverse-data) for upstream attribution and
dataset terms. Retain source attribution when exporting results. Raw distributions are not
committed to this repository.

| Local namespace | Contents |
|---|---|
| `raw/team_epa/<season>/` | Selected play-by-play and schedule snapshots, schemas and retrieval manifests |
| `raw/team_context/<season>/` | Historical passer statistics and injury observations |
| `raw/team_playbook/<season>/` | Additional play measurements joined to canonical EPA play IDs |
| `raw/team_pregame/` | Compact QB roster/depth rows, untimed 2025 injury audit, venue IDs and source schema/hash evidence |
| `raw/team_pregame/weather/` | Single-hour previous-run forecast requests, geocoded city points and selected temperature table |
| `raw/team_availability/2025/` | Dated QB depth rows and whole-team snapshot inventory, excluding retrospectively mapped GSIS/name fields |
| `raw/team_availability/injury_week1_2025/` | Three compressed original archive entities, preserved CDX index and integrity receipts |
| `raw/team_injury_history/2025/` | Bounded canonical article catalog, weekly indexes and selected original entities at 2025 cutoffs |
| `raw/team_injury_history/development_probe/` | Official table catalog receipts and one verified 2023 body; not model inputs |
| `raw/team_weather/points/` | Cached venue-city geocoding responses, queries and digests |
| `processed/team_epa/` | Validated team-games, completed/scheduled games and manifest |
| Other `processed/team_*/` namespaces | Experimental features, predictions, diagnostics and selection records |
| `journal/team_scores/snapshot-*/` | Append-only prospective features, training rows, predictions and provenance |
| `journal/team_weather/snapshot-*/` | Five game hours per successful receipt, explicit missing-game reasons, pinned GFS run and capture times |

Parquet files are content addressed; manifests record checksums, schemas and source times.
Readers verify hashes, and refreshes retain earlier files/manifests. `team-build` runs offline
from the cached snapshots. Local reports go to `reports/local/`; curated team findings and
summaries are tracked under `reports/team_scores/`.

Team rows reconcile to the regular-season schedule. Missing EPA/context remains explicit;
identity conflicts, incomplete team pairs, corrupt artifacts and unavailable sources fail
visibly. Feature and training inputs require earlier eligible games. Retrospective result
availability uses kickoff plus 24 hours, not a claim about original publication time.
Injury modification times also use an explicit retrospective delay; prior-passer history
does not establish the current starter. See [the model policy](../TEAM_MODEL_POLICY.md).

The new weekly experiment reuses the original team/context caches and stores compact results
under `processed/team_walkforward/`. Its weekly folds reference training game IDs/checksums
instead of duplicating full training tables. Source schema audits preserve untimed roster,
starter and injury rows for inspection; these rows are excluded from pregame model features.
Forecast temperatures are archived model predictions with an assumed release buffer, not
observed game weather. See [the archive documentation](https://open-meteo.com/en/docs/previous-runs-api).

On-demand weather uses [Open-Meteo Single Runs](https://open-meteo.com/en/docs/single-runs-api),
venue metadata from ESPN, and [Open-Meteo geocoding](https://open-meteo.com/en/docs/geocoding-api).
Retain Open-Meteo's attribution when sharing weather values; its data is supplied under
[CC BY 4.0](https://open-meteo.com/en/terms). Coordinates are city/grid approximations.
Each capture preserves request URLs, source digests, collector hash, receipt time and original
kickoff. Only the five selected hours are stored; the full point forecast is discarded.
Unavailable gusts remain null rather than being inferred from wind speed. No observed roof
state or weather score adjustment is implied. The score snapshot links this separate artifact
by checksum, preserving the original model manifest and its historical readers.

The Single Runs GFS archive starts in April 2026. The offline lookup cannot use a receipt from
after a historical prediction cutoff. Separate [NCAR historical archive](https://gdex.ucar.edu/datasets/d084001/)
point probes succeeded for 2022, 2023 and 2025; these remain source-access evidence, outside
the frozen experiment and prospective receipt lookup. No global GRIB files were downloaded.

The dated availability audit selects only schedule metadata from the canonical game artifact.
It stores 1,088 team/horizon cutoff records under `processed/team_availability/`, with source,
policy and artifact guards. It reads no score or actual starter columns and performs no fits.
The entire new raw/processed namespace occupies 239,100 bytes at the recorded source check.

For depth, nflverse documents `dt` as record-load time and appends daily ESPN updates. Use
a declared 24-hour delay with zero-delay sensitivity; this is an availability assumption,
not proof of original publication or immunity to later archive revisions. Select the latest
whole-team snapshot, keeping empty, ambiguous or stale QB charts unknown. The pinned producer
remaps GSIS IDs across old records, so the audit uses original ESPN IDs only. See the
[depth dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html) and
[producer at its pre-2025 commit](https://github.com/nflverse/nflverse-rosters/blob/140739bf9c6f80bbe435db1d058f92514141c337/exec/update-depth-charts.R).

The injury pilot uses preserved NFL article editions through the Internet Archive. Verify
the exact archive URL and CDX SHA1/base32 digest against the original HTTP entity before
decoding it; one edition is itself gzip encoded. Local gzip containers and raw index have
SHA256 checksums. Archived capture time, rather than today's receipt or the article's first
publication date, establishes cutoff eligibility. No redirected version or live page is a
fallback. Only aggregate counts enter derived records; article bodies remain ignored locally.
Missing reports and unknown player positions stay unknown. This pilot is Week 1 only and
does not supply a complete IR roster, a starter confirmation or a model identity crosswalk.
See [coverage and provenance](../reports/team_scores/availability_audit/findings.md).

The [weekly injury coverage audit](../reports/team_scores/weekly_injury_coverage/findings.md)
adds all 18 weeks in a separate namespace. `team-injury-fetch` stores only editions needed
at a cutoff, caps retained bodies at 8 MB and reuses completed weeks after an interrupted
index request. `team-injury-audit` runs offline, producing 1,088 records under
`processed/team_injury_coverage/`. The recorded raw collection and current audit total about
1.84 MB; the development probe is about 80 KB. Earlier decoder revisions remain separate.

The 390 formal refresh reports are partial source coverage. Thirteen slots are pending or
malformed and 141 unavailable; all 544 Tuesday slots are unknown for this source. Source
variants, failed capture verification and sparse earlier editions remain explicit limits.
An exact Week 1 entity may be reused from the original pilot only when its original URL,
capture timestamp and digest match; the pilot manifest is a checksum-bound input. No report
is carried from an older edition to fill a failed or omitted section. Neither original
availability evidence nor model research locks are rewritten.

Use CLI `--data-dir` to select another cache directory and `NFL_PROP_DATA_DIR` for the app.
All data subdirectories are ignored, including local artifacts from historical workflows.

`team-historical-weather-fetch` collects selected 2022–2025 operational GFS points into
`raw/team_historical_weather/`; `team-historical-weather-audit` reconstructs coverage offline
into `processed/team_historical_weather/`. This separate namespace preserves older source
and model hashes. It uses at most two request workers and a 24 MB compressed-body budget.
Every accepted point checks forecast identity, units, heights, valid time and grid location.
The historical release buffer and static announced-venue assumptions remain explicit;
these are not observations or newly captured prospective forecasts. See the
[declared policy](../reports/team_scores/historical_weather/protocol.md). Keep
[NCAR](https://gdex.ucar.edu/datasets/d084001/) and
[Open-Meteo](https://open-meteo.com/en/docs/geocoding-api) CC BY 4.0 attribution when sharing.

`team-archived-weather-compare` reconstructs only 2022–2024 weather offline, verifies identical
development cohorts and reproduces the saved EPA control before accepting a research result.
It stores context deltas, one prediction panel and fold IDs under `processed/team_archived_weather/`;
the original model and source artifacts remain intact. No additional weather download or
2025 score evaluation is performed. See the [development result](../reports/team_scores/weather_comparison/findings.md).

`team-depth-catalog-fetch/audit` inventory official chart-page archive indexes for 2022–2024.
They use the current NFL club directory as an explicit domain assumption and store bounded
indexes under `raw/team_depth_catalog/`, with one cutoff table under `processed/team_depth_catalog/`.
Indexed candidates remain unverified and model-ineligible: chart bodies and player identities
require a separate audited adapter. See the [source inventory policy](../reports/team_scores/depth_catalog/protocol.md).

`team-compact-replay` is a separate, offline 2025 diagnostic using the frozen eight-input
research choice. It verifies the existing simplicity, dated-injury and archived-weather
decisions before freezing hashes, then reads the canonical outcomes. Only 2025 feature rows,
predictions and fold receipts are added under `processed/team_compact_replay/`; training
and raw datasets are reused. Cached runs are verified without refitting. This command does
not supply missing roster inputs or clear the original enriched replay gate. The
[declared protocol](../reports/team_scores/compact_2025/protocol.md) fixes all comparisons.
