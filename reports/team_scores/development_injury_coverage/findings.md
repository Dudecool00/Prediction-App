# Dated development injury tables

Recorded October 15, 2026. This outcome-free audit covers 815 canonical games and 3,260
team/horizon slots: 2022 initialization and 2023–2024 development. It performs zero model fits
and selects no 2025 games, scores or quarterback labels. The 2022 canonical cache contains
271 completed games; the canceled Buffalo–Cincinnati game is excluded upstream. Each
development season retains all 272 games, including games with missing injury context.

**This official-table archive cannot yet support the matched injury model comparison.**
No Tuesday slot has observed game-status designations from this source. Pre-kickoff coverage
is particularly sparse in 2024, and a dated table does not prove final team-report completeness.
The active EPA model, compact research choice, frozen evidence and enriched replay gate
are unchanged. No new MAE or model promotion follows from this source audit.

| Season / role | Refresh team slots | Designations present | Practice only / pending | Unavailable |
|---|---:|---:|---:|---:|
| 2022 initialization | 542 | 186 (34.3%) | 16 | 340 |
| 2023 development | 544 | 373 (68.6%) | 13 | 158 |
| 2024 development | 544 | 80 (14.7%) | 6 | 458 |

At Tuesday 10 a.m. Chicago, all 542 slots in 2022 and all 544 in 2024 are unavailable;
2023 has 32 practice-only/pending slots and 512 unavailable slots. These are coverage
results for this bounded archive policy, not claims that injury information did not exist.

“Designations present” means at least one observed Out, Questionable or Doubtful game-status
cell in that team's table. Observed Out counts describe the visible table, and may be zero
when another designation is present. They do not prove a complete final report, healthy roster,
active/IR roster or starting lineup. Blank game-status cells, empty team sections and practice
participation never establish zero injuries. Every record has `model_eligible=false` and
`final_report_verified=false`. These counts cannot be directly compared to the formal weekly
article classification in the separate 2025 audit.

## What the archive established

Three preserved catalogs identify 18 regular-week URLs for 2022, 18 for 2023 and seven for
2024. Exact-week indexes selected 58 editions needed at the forecast cutoffs. Fifty-five
original HTTP entities match their preserved archive digests; three transport failures remain
unverified. Thirteen verified 2023 entities actually display the previous week's report despite
their requested current-week URLs. The parser rejects them using the body title and headings.
Catalog presence, a plausible URL and a successful HTTP response are insufficient proof.

The 2024 catalog lacks 11 regular-week URLs in the requested season window. Those missing
weeks account for 324 unavailable refresh team appearances; another 134 have no earlier
edition. In 2023, the 158 unavailable refresh slots comprise 122 stale editions, 30 unverified
editions and six with no earlier edition. The corresponding 2022 counts are 128 stale,
30 unverified and 182 without an earlier edition.

## Timing, attribution and reproducibility

The collector selects the latest whole indexed edition strictly before Tuesday 10 a.m.
America/Chicago or kickoff minus one hour. January games retain their NFL season. Eligible
captures must be at most 72 hours old. Repeated identical captures remain in the week index
so freshness can be assessed at their actual capture times. No later edition or older fallback
fills a failed verification, removed status, omitted section or schedule mismatch.

Original entity SHA1/base32 must match the CDX receipt and the resolved `id_` URL before
decoding. Indexes, original gzip containers and manifests have local checksums; offline readers
re-derive cutoff rows and summaries. Parser titles, season/week headings, matchup abbreviations,
adjacent team labels and exact table headers must agree. A missing table cannot transfer the
opponent's rows. Player identity is an NFL profile slug or unique document name within that
edition only; ambiguous duplicates fail. There is no invented GSIS crosswalk or player weight.
Unknown Out positions invalidate position-group counts. Team records elsewhere on the page
are ignored.

```
nfl-prop team-injury-development-fetch
nfl-prop team-injury-development-audit
```

Use the existing environment and canonical cache containing 2022–2024. Fetch resumes completed
weeks and raw index checkpoints; failed indexes do not become empty archives or cancel unrelated
weeks. The audit is offline and projects only six schedule metadata columns from the canonical
cache. Output is `reports/local/team_injury_development/{results.json,coverage.csv}` with a small
checksummed cutoff artifact under `data/processed/team_injury_development/`.

Actual retained raw files total **3,921,791 bytes**; the processed audit adds **71,878 bytes**,
about **4.0 MB combined**. Limits are 100 catalog rows per season, 80 index rows per week,
1 MB per entity, 8 MB retained report bodies and two collection workers. No environment,
training table, forecast grid or old dataset is duplicated. Raw player lists remain ignored;
[curated source digests, body week labels and coverage](results.json) contain no athlete rows.

Primary evidence includes the [preserved 2023 Week 2 table](https://web.archive.org/web/20230917091612id_/https://www.nfl.com/injuries/league/2023/REG2)
and the [Week 5 URL serving a Week 4 body](https://web.archive.org/web/20231001160406id_/https://www.nfl.com/injuries/league/2023/REG5).
The curated JSON retains all selected archive URLs, index digests and original-entity receipts.

## Next development test

Audit preserved **2023–2024 weekly news reports**, starting with the 2024 gap, using the same
formal designation definition as the 2025 weekly-article adapter. Establish report completeness
and matched cutoff coverage before comparing compact EPA with injury context on identical
2023–2024 games. Missing games must remain in the scoring cohort. Do not fill this gap with
season-final injury files or drop uncovered games to improve a metric. The enriched 2025 replay
remains gated; 2025 is previously examined diagnostic data, not a fresh holdout.
