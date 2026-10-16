# Development depth-chart source inventory

Declared October 15, 2026, before the league-wide catalog requests. This inventories
possible sources; it downloads no depth-chart entities and creates no model features.

Use the [NFL official team-site directory](https://www.nfl.com/teams/) to pin the 32 current
club domains. This is an explicit retrospective domain assumption, not a historical
directory reconstruction. Query each domain's `/team/depth` prefix for seasons 2022–2024,
August 15 through January 16 of the following calendar year. Accept only the two conventional
`/team/depth-chart` and `/team/depthchart` paths, with optional trailing slash and no query.
Other historical domains and routes remain outside this bounded sample.

Use the [CDX API's documented filters and collapsing](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server):
HTTP 200, HTML, and the first indexed capture per UTC day per adjacent URL group. This
sampling can miss within-day revisions and later same-day editions; it cannot establish
the latest complete source version. Retain the raw index and query for each team/season.

Limit 96 catalog URLs, two workers, one retry for transport errors, 650 collapsed rows/index,
200 kB/index response, 500 kB for the NFL directory, and 4 MB compressed raw bodies overall.
Detect overflow using a 651st row; label the entire affected catalog truncated rather than
silently accepting a partial list. Resume valid receipts, preserving unavailable queries.
An explicit `--retry-failed` resumption can retry unavailable requests, retaining prior receipts.
Parse/schema/integrity errors fail visibly. Do not retry altered sources into acceptance.

Read only the canonical six schedule metadata fields. Retain all 815 development games,
both teams, and both horizons: 3,260 cutoff slots. A potential candidate must have an indexed
capture strictly before the cutoff, no more than 72 hours old. Equal-time, stale, empty,
truncated and unavailable catalogs supply no candidate. January dates follow the NFL season.

Every slot remains **unverified and model-ineligible**, even with an indexed candidate.
CDX metadata does not prove a readable chart, its team/position content, a complete active/IR
roster, first-string identity or confirmed starter. Before a later adapter can use any
snapshot, verify the exact archived entity and digest, parse the whole chart, and declare
identity, ties, missingness, freshness and latest-whole-snapshot rules. No current-page or
publication-date fallback. No scores, 2025-season sources or model fits enter this inventory.

Keep raw data and cutoff tables outside Git; publish compact coverage and limitations only.
The enriched replay gate stays intact. This step follows the completed weather comparison;
PEM testing remains after the original plan.
