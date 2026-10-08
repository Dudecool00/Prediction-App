# Prospective postgame roster reconciliation

As of 2026-10-08T00:56:23.423313+00:00.

170 enrolled candidates; 0 corroborated. Participation validation is incomplete; forecasts remain disabled.

- pending_game: 170

Enrollment registry SHA-256: `d5b36e96b5582c38a2d93aa11e2827f441bda340d3da47c0812dd0fffe288906`

Roster rules SHA-256: `38b329fa6de5efdbb51b56a10bfa0e1a39a3594d8f05cfde857ce0394fa5a96b`

## Current source fingerprints

Schedules retrieved 2026-10-08T00:52:42.766568+00:00; SHA-256 `df6c27ae55bb173a5292c29ad37709174f563ff2384cbb2f9ab8b7a192438bd0`.

2026 stats retrieved 2026-10-08T00:52:43.391185+00:00; SHA-256 `a9e855804b527ac9f591ef4c9a51ae528b823f1129e4901e5ad520f858ee37ba`.

Code fingerprints SHA-256: `4d7b31f5b7c01d19c5c67f3ca305d918bf52130a675895099cb44c294664b19a`. Full JSON retains each package file hash and cached roster/identity source metadata.

## Source interpretation

- version: 1
- scope: prospective_postgame_roster_reconciliation
- denominator: The existing registry's complete candidate pool; enrollment is never replaced
- availability: Completed game, matching kickoff, and evidence retrieved after kickoff plus 24 hours
- identity: Unique ESPN-to-GSIS QB mapping must agree with the original candidate IDs
- played: Period-zero valid roster entry with didNotPlay=false plus a recorded QB stats target
- dnp: Explicit didNotPlay=true, starter=false, valid=false plus no recorded QB appearance
- ambiguous: didNotPlay=false with valid=false is unresolved, even when stats exist
- conflict: Roster DNP with a QB stats target remains a conflict, including zero-yard targets
- activation: Roster active is ignored; DNP and missing roster entries do not establish inactive status
- claims: Compare enrollment-time starter annotations with retrospective flags; outcome agreement is not source-content adjudication
- forecast: No model execution, probabilities, EV, calibration validation or production enablement

The completed Week 4 contract check is retrospective source inspection, not an enrolled cohort outcome. Roster flags can corroborate participation or DNP, but do not verify an article's meaning or establish inactive status.

ESPN event rosters and nflverse QB stats/identity distribution (CC-BY-4.0). Raw ESPN responses remain local; their rights are separate from nflverse's distribution license.
