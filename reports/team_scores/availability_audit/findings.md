# Dated availability source audit

Source check recorded October 14, 2026 in America/Chicago (October 15 UTC).
This audit uses schedule metadata for 272 regular-season 2025 games and 1,088 team/horizon
slots. It reads no final scores or actual starter labels and performs zero fits.

| Coverage | Tuesday lock | Kickoff minus one hour |
|---|---:|---:|
| Team-game slots | 544 | 544 |
| Listed QB leader from eligible depth snapshot | 544 | 544 |
| Selection differs with zero versus 24-hour delay | 11 | 2 |
| Week 1 injury reports with designations | 0 | 25 |
| Week 1 explicit empty designation statements | 0 | 3 |
| Week 1 unavailable reports | 32 | 4 |

There are 20 known depth-leader changes between the two cutoffs. These establish source
variation worth testing once identities and development coverage are audited; they do not
measure accuracy improvement. All-depth coverage is conditional on the timestamp assumptions
below. Listed QB leaders remain separate from confirmed starters.

## Depth source and assumptions

The selected source contains 21,429 QB rows and 7,072 whole-team snapshot counts over 221
load times, covering August 2025 through March 2026. Keep ESPN identity, position slot/rank,
team and `dt`; discard the larger distribution after selection. The retained source has
one empty QB snapshot, illustrating why selection must use whole charts rather than carry
the last row for a previously listed player.

The [nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html)
defines `dt` as record-load time. The [update schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html)
documents daily appended ESPN editions after the source change following 2024. Neither
verifies original source publication or historical immunity to later revisions.

Use load plus 24 hours strictly before the prediction cutoff and report zero-delay
sensitivity. The latest eligible team chart must be no more than 72 hours old from load.
Require exactly one distinct rank-one QB across slots; ties, removed players, empty charts,
stale charts and missing charts stay unknown without fallback to an older identity.

The [producer at a commit preceding the 2025 season](https://github.com/nflverse/nflverse-rosters/blob/140739bf9c6f80bbe435db1d058f92514141c337/exec/update-depth-charts.R)
remaps GSIS IDs across historical rows with the then-current player mapping. The adapter
therefore excludes GSIS/name fields. It supplies a listed ESPN QB identity only, with no
complete active/IR roster or game-start confirmation. The raw distribution SHA256 matches
the earlier pregame audit's depth source; original source artifacts remain intact.

## Preserved injury pilot

Three distinct NFL Week 1 article entities passed exact capture-URL and archive-index
digest checks. Capture timestamps are **2025-09-03 20:35:03**, **September 4 15:36:35** and
**September 6 00:25:21 UTC**. Raw CDX index, original entities and integrity receipts remain
local. See the preserved [first edition](https://web.archive.org/web/20250903203503id_/https://www.nfl.com/news/nfl-week-1-injury-report-player-statuses-for-all-16-games-2025-season),
[second edition](https://web.archive.org/web/20250904153635id_/https://www.nfl.com/news/nfl-week-1-injury-report-player-statuses-for-all-16-games-2025-season) and
[third edition](https://web.archive.org/web/20250906002521id_/https://www.nfl.com/news/nfl-week-1-injury-report-player-statuses-for-all-16-games-2025-season).

At Week 1 refresh cutoffs, 28 team sections are eligible: 25 with designations and three
explicit empty statements. Four are unavailable. No edition exists by the Tuesday lock,
so all early-week injury inputs remain unknown. One covered team has an Out designation
whose position is absent; its position-group counts remain null. This is report coverage,
not a complete injury roster or a measured forecast benefit.

Select the latest whole edition captured strictly before each cutoff, at most 72 hours old.
If the latest edition omits a team, do not restore an older section. The parser excludes
navigation, matchup metadata and already-completed game sections, distinguishing missing,
partial/pending, formal and explicitly empty reports. Unknown positions are never inferred
from player names, and no model identity crosswalk is fabricated.

The CDX SHA1/base32 digest covers the original HTTP entity; one capture is gzip encoded.
Verify those bytes before bounded decompression, then parse the article. Local gzip containers
and the preserved index have SHA256 checksums. A redirect, altered entity or conflicting
metadata fails visibly. Current content, today's receipt and the article's first publication
date cannot prove when a particular revised edition existed. Retrieval happened in 2026;
historical eligibility comes from the verified archived capture.

## Storage, reproduction and remaining work

Run `nfl-prop team-availability-fetch`, then `nfl-prop team-availability-audit` with an existing
team cache including 2025. Fetch reuses valid cached sources; the cutoff audit is offline.
Local results use `reports/local/team_availability/`. The raw namespace occupies **203,354
bytes** and the single processed audit **35,746 bytes**, totaling **239,100 bytes**. No new
environment, training table or forecast grid was added. Full article bodies and player
datasets are excluded from Git; [summary and source digests](results.json) are retained.

Reader checks verify source policy/code, manifests, artifact hashes and recomputed cutoff
records. Thirteen fixture cases cover temporal invariance, removed/ambiguous/stale leaders,
timezone-aware January dates, parser scope, missingness, archive identity/digests, bounded
gzip, corruption, outcome-column exclusion and preservation of existing inputs.

The enriched 2025 replay remains gated. Next, audit broader archived injury coverage and
dated 2022–2024 development availability. Full active/IR rosters, starter confirmation and
historical forecast weather coverage remain unresolved. Any added inputs must earn their
place against compact EPA on identical development games before a new freeze and replay.
The original model choices, readiness audit and prior 2025 diagnostics remain unchanged;
2025 remains previously examined diagnostic data.
