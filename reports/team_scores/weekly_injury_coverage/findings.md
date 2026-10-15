# Weekly injury archive coverage

Recorded October 15, 2026. Outcome-free source audit: 272 games, 1,088 team/horizon slots,
18 weeks and zero model fits. The original Week 1 pilot, model choices and replay gate remain intact.

At the kickoff refresh, **390 of 544 team appearances (71.7%)** have formal reports:
386 with designations and four explicit empty statements. Another 13 are partial/pending
and 141 are unavailable. **None of this source's preserved editions is eligible by Tuesday**;
all 544 early slots remain unknown. This measures this archive/source policy, not whether
any injury information existed elsewhere at the time.

| Week | Team appearances | Formal refresh reports | Partial/pending | Unavailable |
|---|---:|---:|---:|---:|
| 1 | 32 | 28 | 0 | 4 |
| 2 | 32 | 26 | 0 | 6 |
| 3 | 32 | 28 | 0 | 4 |
| 4 | 32 | 30 | 0 | 2 |
| 5 | 28 | 2 | 0 | 26 |
| 6 | 30 | 2 | 0 | 28 |
| 7 | 30 | 30 | 0 | 0 |
| 8 | 26 | 2 | 0 | 24 |
| 9 | 28 | 25 | 3 | 0 |
| 10 | 28 | 2 | 0 | 26 |
| 11 | 30 | 26 | 0 | 4 |
| 12 | 28 | 22 | 4 | 2 |
| 13 | 32 | 24 | 1 | 7 |
| 14 | 28 | 26 | 0 | 2 |
| 15 | 32 | 29 | 2 | 1 |
| 16 | 32 | 28 | 2 | 2 |
| 17 | 32 | 30 | 0 | 2 |
| 18 | 32 | 30 | 1 | 1 |

The 141 unavailable refresh slots comprise 78 stale editions, 28 unverified editions,
27 omitted team sections and eight slots with no earlier edition. Six formal/partial slots
contain an Out designation without a position; position-group counts stay null. A reported
zero Out count is not a complete healthy/active/IR roster or a starter confirmation.

## Timing and integrity

Discover canonical weekly NFL article URLs through a preserved, date-bounded archive catalog.
Query each original URL over its NFL week and select only editions needed at Tuesday's lock
or kickoff minus one hour. Do not download unrelated late versions or use final game scores.
Capture time must be strictly before the cutoff and at most 72 hours old. The latest whole
edition must supply the team section; no older report fills an omission or failed verification.

The exact archive `id_` URL and original entity SHA1/base32 must match the preserved CDX index
before decoding. Some HTTP entities retain gzip, so verification precedes bounded decompression.
Local gzip containers, indexes, manifests and cutoff tables retain checksums. A previously
verified pilot body can supply the **identical timestamp, original URL and digest**; this is
reuse of the same edition, not fallback to an older report. Its manifest is an explicit input.
Digest mismatches, redirects and failed transports remain visible and cannot become zero injuries.

Article layouts vary: team labels appear as headings or standalone paragraphs. Empty headings
reset attribution; unlabeled players cannot migrate to the preceding team. Empty comma separators
are ignored, while malformed/missing separators or unbalanced annotations make that team's
counts unknown. Pending notes remain distinct from explicit empty reports. January 2026 editions
are bound to the 2025 Week 18 schedule, with publication/modification dates checked against the
week and capture. No player identity mapping or injury weight is invented.

## Source limits and development follow-up

This collection uses canonical URLs only. Archived campaign/query variants may add coverage,
including a later Week 8 edition; they need their own exact-version and consistency audit.
Weeks 5, 6, 8 and 10 have especially sparse refresh coverage; all weeks remain in the cohort. Missing
reports are never dropped to improve a metric. Canonical archive gaps and failed captures
are not evidence that teams published no report.

Bounded official injury-table catalogs also contain 18 regular-week URLs for 2022, 18 for 2023,
seven for 2024 and two for 2025 within the requested season windows. Catalog presence alone
does not establish a pre-cutoff report. One official 2023 Week 2 entity, captured September 17
at 09:16:12 UTC, matches its archive digest and has server-rendered tables. Its historical
table parser, player mapping and full timely-coverage audit remain pending; no development
input is replaced using this probe.

Primary references: [weekly article archive catalog](https://web.archive.org/cdx/search/cdx?url=www.nfl.com%2Fnews%2Fnfl-week-&matchType=prefix&from=20250801&to=20260106&filter=original%3A.%2Ainjury-report.%2A&collapse=urlkey&output=json&filter=statuscode%3A200),
[2025 Week 2 article](https://www.nfl.com/news/nfl-week-2-injury-report-player-statuses-2025-season),
[preserved official 2023 table](https://web.archive.org/web/20230917091612id_/https://www.nfl.com/injuries/league/2023/REG2).
Raw bodies/player lists stay ignored locally. [Source digests and coverage details](results.json)
are retained without athlete records.

## Reproduction and next model step

Use the existing environment and canonical team cache including 2025:

```
nfl-prop team-injury-fetch
nfl-prop team-injury-audit
```

Fetch reuses validated local versions and resumes completed weeks after index transport errors.
Offline audit outputs `reports/local/team_injury_coverage/coverage.csv` and `results.json`.
Source requests cap catalog rows, versions, entity size and retained bodies (8 MB); only two
week workers run. The actual raw collection plus processed audit is about 1.84 MB, with a
small separate development probe. No new environment, training table or forecast grid is added.

The enriched 2025 replay remains gated. Next audit query variants and the dated development
tables, then assess context additions on identical 2023–2024 games before changing the research
freeze. Dated active/IR roster/starter confirmation and historical forecast weather still need
work. No new 2025 MAE or model promotion follows from source coverage; 2025 remains previously
examined diagnostic data.
