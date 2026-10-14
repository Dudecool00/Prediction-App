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
| `processed/team_epa/` | Validated team-games, completed/scheduled games and manifest |
| Other `processed/team_*/` namespaces | Experimental features, predictions, diagnostics and selection records |
| `journal/team_scores/snapshot-*/` | Append-only prospective features, training rows, predictions and provenance |

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

Use CLI `--data-dir` to select another cache directory and `NFL_PROP_DATA_DIR` for the app.
All data subdirectories are ignored, including local artifacts from historical workflows.
