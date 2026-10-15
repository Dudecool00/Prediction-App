# Next dated roster source check

Checked October 15, 2026. This is source-access evidence, not a roster feature or model test.

nflverse documents a depth-source change after 2024: its dated `dt` field identifies record
load time from 2025 onward. Earlier cached week labels alone still do not establish when
a particular chart was available. See the [source dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html).

A bounded probe verified an [official Vikings depth-chart capture](https://web.archive.org/web/20240906182949id_/https://www.vikings.com/team/depth-chart)
at **2024-09-06 18:29:49 UTC**, before the Week 1 refresh cutoff. The exact resolved capture
URL and original HTTP entity SHA1/base32 match the preserved CDX index:
`2UPSA6DOKUCH5S5UEHEP3AOT5KHNUYQ7`. The original entity is 66,576 bytes and decodes to
293,546 bytes. Three tables contain 31 rows including headers, with one ordered QB row.
Entity SHA256 is `a3e09238b978d6b3d22656dfe3b3a08c7fc11fd891f9e65c550d0dcf808f6ee6`.
Raw index/entity and the verification summary remain in ignored local reports.

This capture does not qualify for the Tuesday lock. A listed first-string player is not
a confirmed starter, a complete active/IR roster or a cross-provider player identity.
One team/week proves access, not 2022–2024 league-wide coverage. An earlier probe of an
official news article exceeded the 1 MB response limit and was rejected; no roster data
were accepted from it.

The next source step is a bounded capture inventory of official depth-chart pages across
development seasons, before writing an adapter. Declare freshness, latest-whole-chart,
unknown/tied-rank and player-identity rules first. Preserve all game cutoffs, including missing
ones, and verify exact captured entities. Do not substitute current pages or article publication
dates for archived versions. Complete active/IR roster coverage remains a separate gap.

Historical weather still needs its development comparison before any revised freeze.
The original enriched replay gate stays in force, and the PEM-inspired comparison follows
the original plan.
