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

Use CLI `--data-dir` to select another cache directory and `NFL_PROP_DATA_DIR` for the app.
All data subdirectories are ignored, including local artifacts from historical workflows.
