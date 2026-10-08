# Retrospective 2026 roster source-contract check

Checked 2026-10-08T00:53:24.017341+00:00.

Completed event 401872968: 2026 Week 4, Packers at Buccaneers, October 4 at 17:00 UTC.

This completed-game source inspection is separate from the 170 enrolled candidates. It is not a prospective cohort result.

| Team | ESPN QB ID | Starter | Did not play | Valid | Derived roster state |
| --- | --- | --- | --- | --- | --- |
| TB | 4596472 | True | False | True | reported_participant |
| TB | 3052587 | False | True | False | reported_dnp |
| TB | 3120590 | False | False | False | ambiguous_flags |
| GB | 4036378 | True | False | True | reported_participant |
| GB | 14163 | False | True | False | reported_dnp |

The active field is false for all five QB entries, including both starters. It is ignored by reconciliation. The invalid non-DNP backup entry remains ambiguous. DNP does not establish gameday inactive status, and a reported participant still needs agreement with the independent QB stats target.

[ESPN event header](https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=401872968)

Header JSON SHA-256: `42fed331ccaa0fa0699f788f7e0070eaeeb5605f61aaf340250d8e2d6e2f1d1f`.

[TB event roster](https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/401872968/competitions/401872968/competitors/27/roster?limit=1000); saved JSON SHA-256 `6367905232f23fec57c091dd6ae2dbd1b2cdaa314c3b91e9937b20a7aee36c97`.

[GB event roster](https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/401872968/competitions/401872968/competitors/9/roster?limit=1000); saved JSON SHA-256 `52b39163149021c8f2bf0d78c6d8c9ae860a4823185d078465de2c43f8271e87`.

Raw ESPN responses are stored locally and not redistributed. This report preserves selected factual flags and source fingerprints; it does not assign nflverse licensing to ESPN data.
