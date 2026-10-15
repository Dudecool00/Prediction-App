# Official depth-chart source inventory

815 games, 3260 team/horizon slots; 575 indexed candidates within 72h. **Zero verified chart entities, model-eligible slots or model fits.**

| Season | Team slots per horizon | Tuesday candidates | Refresh candidates |
| --- | ---: | ---: | ---: |
| 2022 | 542 | 91 | 95 |
| 2023 | 544 | 66 | 49 |
| 2024 | 544 | 162 | 112 |

The sample supplies potential captures for **17.6%** of cutoff slots, spanning 515 distinct
URL/timestamp/digest editions. All 96 team/season queries were attempted. After one explicit
bounded resumption, 92 catalogs are indexed and four remain unavailable because of transport
failures: 2023 SEA/TB/WAS and 2024 BAL. Those four account for 136 cutoff slots. No catalog
is silently interpreted as empty because its request failed. There are no truncated catalogs.

This is a bounded, daily-collapsed index sample, not latest-version proof or complete historical coverage. Current official directory domains are a retrospective assumption; other domains/routes and within-day revisions may be missing. Empty, unavailable and truncated catalogs remain explicit. Never treat index availability as a readable chart, confirmed starter, identity mapping or active/IR roster.

Raw catalog data: 203,886 bytes. Next, inventory exact URL editions and verify a bounded set of selected entities before building any chart feature adapter. All original enriched-replay gates remain in force.

Raw plus processed inventory data occupy **258,173 bytes**. Earlier failed request receipts
remain attached to recovered requests. The official directory's 32 domains are checked for
unique team associations, including mixed absolute/relative profile links. The daily sample
also retains the exact 2024 Vikings capture verified in the prior pilot; that single body
remains separate evidence and is not extrapolated to the other catalog entries.

Reproduce with `team-depth-catalog-fetch`, then `team-depth-catalog-audit`. Auditing runs
offline from preserved directory/index responses and reads no score or actual-starter
columns. Each candidate table row remains explicitly `model_eligible=false`. The public
JSON omits the machine-specific workspace path while retaining schedule hashes. The 13
new catalog checks cover domain association, source/date/schema checks, strict cutoff and
staleness, unavailable/truncated cases, outcome exclusion, cache corruption and storage caps.
The full local suite passes all 234 tests, including 29 weather-comparison checks.

This sample does not clear the roster/starter requirement for the enriched replay. Broadening
the source sample or changing its freshness must be declared separately before feature fits;
an index candidate alone cannot establish a usable chart or complete active/IR roster.
