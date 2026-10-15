# Dated development injury news

Recorded October 15, 2026. This source qualification covers all **544 games / 2,176 team-horizon
slots** in 2023–2024. It reads six schedule metadata fields, fits zero models, and selects no
2025 data. Existing EPA models, previous source collections, research choices and replay gates
remain unchanged. This is source coverage, not an accuracy improvement or a new MAE result.

The weekly news definition matches the frozen 2025 article parser: a team section must contain
formal designations or explicitly state there are none. Missing sections, N/A, pending notes,
malformed lists and unknown positions remain distinguishable. A formal team report does not
establish a complete active/IR roster or a confirmed starter.

| Season | Canonical-only formal refresh reports | With qualified tracking editions | Games with both teams formal | Games with both position-group counts |
|---|---:|---:|---:|---:|
| 2023 | 330/544 (60.7%) | 330/544 (60.7%) | 153/272 | 149/272 |
| 2024 | 62/544 (11.4%) | 127/544 (23.3%) | 60/272 | 58/272 |

Tracking editions add a net **65 formal team reports and 31 games with both teams covered**
in 2024. The canonical-only figures for both-team reports were 153 games in 2023 and 29 in
2024. The final 2023 refresh cohort has 330 formal, 82 partial/pending and 132 unavailable team
slots; 2024 has 127 formal, nine partial/pending and 408 unavailable slots. All games remain
in the cohort. These formal-report counts must not be compared directly to the less specific
observed-designation counts in the official-table audit.

Neither season has an eligible report by Tuesday 10 a.m. Chicago: all **1,088 early team slots**
remain unknown under this source policy. That does not imply no injury information existed
elsewhere or that current-week game-status designations should be expected by Tuesday.

## Preserved evidence and limits

Canonical catalogs identify all 18 weekly URLs for 2023 and 16 for 2024. The tracking catalogs
add independently preserved editions, including a 2024 Week 13 article missing from the
canonical catalog. Week 7 remains absent from the bounded 2024 archive catalogs. This measures
preservation within the requested season window, not whether an article was published.

The collection selects 63 canonical editions and 18 tracking editions across 22 tracking URLs.
Seventy-four original HTTP entities verify against their CDX digests; seven canonical 2023
receipts remain unverified. All 18 selected tracking bodies verify their canonical identity and
are supported by the existing article parser. Four selected canonical editions have duplicate
player ambiguities and are rejected by that same frozen parser.

For 2023, the 132 unavailable refresh slots comprise 96 unverified editions, 30 unsupported
editions, four omitted team sections and two with no earlier edition. For 2024, the 408
unavailable slots comprise 268 stale editions, 72 omitted sections, 36 with no earlier edition,
30 without an archived weekly URL and two unsupported editions. Position information is unknown
for some Out players; a valid total does not automatically qualify position-group features.

This evidence remains insufficient to treat injury context as a complete development dataset.
The model-comparison and enriched-replay readiness fields remain false. Historical forecast
weather and dated active/IR roster and starter evidence still have separate requirements.

## Timing, identity and storage

Each original URL retains its exact date-bounded index, capture receipts and compressed original
entities. Original SHA1/base32 and resolved archive `id_` URL must match before decoding. The
latest whole edition strictly before Tuesday 10 a.m. Chicago or kickoff minus one hour governs
each slot, with a 72-hour freshness limit. Publication and modification dates must agree with
the NFL week and capture. January editions remain attached to their NFL season.

Only recognized tracking parameters are accepted; language, season and other content selectors
cannot masquerade as tracking links. Each tracking body must identify the exact expected
canonical article in its canonical link. Across URL forms, different editions at the same latest
capture timestamp remain ambiguous. A newer failed, unsupported or omitted section cannot
inherit an older report. Edition selection never depends on observed scores or model errors.

Canonical and tracking indexes retain repeated captures, including identical bodies at later
timestamps. This differs from the digest-collapsed index in the older 2025 collection: the formal
definition and forecast cutoffs match, but these source-coverage percentages are not a controlled
comparison of archive collection methods across seasons. Any future replay must declare its
exact source manifests and collection policy.

Raw files total **5,498,523 bytes** and the processed audit adds **62,805 bytes**; the two small
variant-discovery indexes add **1,610 bytes**, approximately **5.6 MB combined**. Limits are 100
rows per catalog, eight tracking URLs per week, 80 indexed versions per URL, 1 MB per entity,
8 MB retained bodies and two concurrent workers. No environment, training table or forecast
grid is added. Raw player lists stay ignored; the repository keeps only aggregate findings and
[source digests / cutoff coverage](results.json).

```
nfl-prop team-injury-news-fetch
nfl-prop team-injury-news-audit
```

Fetch reuses the canonical collection and resumes unfinished exact-URL requests. Offline audit
re-derives the source classifications, per-game cutoffs, summaries and artifact checksums.
Outputs are `reports/local/team_injury_news/{results.json,coverage.csv}` plus a small checksummed
cutoff artifact. Optional initialization should be fetched separately with `--seasons 2022`,
so 2023–2024 sources do not need another collection.

Primary references: [2024 Week 1 weekly article](https://www.nfl.com/news/week-1-injury-report-for-2024-nfl-season),
[2023 Week 8 weekly article](https://www.nfl.com/news/week-8-nfl-injury-report-for-2023-season),
and [preserved Week 13 tracking edition](https://web.archive.org/web/20241127211429id_/https://www.nfl.com/news/week-13-injury-report-for-2024-nfl-season?utm_source=dlvr.it&utm_medium=twitter).
Current live pages are discovery references only; their latest content is not used as historical
pregame evidence. The curated JSON records the exact eligible editions and failed receipts.

## Next model test

Qualify 2022 initialization separately before fitting injury coefficients. Then preregister one
small comparison: compact EPA, compact EPA plus availability flags alone, and compact EPA plus
two coarse Out-count contrasts with those flags. The availability-only control tests whether an
apparent gain comes from archive coverage rather than injury information. Listed QB Out counts
do not identify the starting quarterback; weights must not assume that they do.

Keep weekly EPA history and fits locked Tuesday. Only verified context refreshes before kickoff;
Thursday outcomes cannot enter Sunday's weekly fit. Evaluate every development game, keep
unknown context explicit, and use both-covered games only as a labeled secondary sensitivity.
Report score/margin MAE and paired uncertainty before changing the pre-2025 research choice.
Do not widen the feature search or drop uncovered games to manufacture a gain. The enriched
2025 replay remains gated, and 2025 remains previously examined diagnostic data.
