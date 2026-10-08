# Prospective candidate participation registry

Audit as of 2026-10-08T00:20:15.503617+00:00.

Enrolled 2026-10-08T00:20:15.401359+00:00: 170 candidates across 29 games.

All enrolled rows remain in the denominator. No forecasts enabled; participation validation remains incomplete.

- pending_game: 170

Registry SHA-256: `d5b36e96b5582c38a2d93aa11e2827f441bda340d3da47c0812dd0fffe288906`

Snapshot SHA-256: `f32b85664961bce8903c17a5fb4290267775fdc2814417dfb49c4798609a3e36`

Protocol SHA-256: `31e2e9dc94bca0e4ee662c730672e911055b6fbea1a27c4fafbbbd47b616d223`

## Prespecified rules

- version: 1
- scope: prospective_candidate_participation_audit
- enrollment: Every chart candidate in the chosen feature snapshot, including abstentions and unmapped IDs
- deadline: Register before every enrolled game's scheduled kickoff minus one hour
- overlap: Each game can belong to only one local registry; no replacement after outcomes
- outcomes: Wait for both scores, kickoff plus 24 hours, and a stats snapshot retrieved after that time
- appearance: An explicit regular-season QB stats row with a finite passing-yards value; zero attempts/yards are retained
- absence: No recorded QB appearance is not proof of inactive/DNP; never manufacture a zero target
- missing: Unmapped identities, schedule changes and missing source coverage retain their denominator rows
- model: No predictions, probabilities, calibration tests, model selection or production enablement
- next_validation: Independent gameday roster/gamebook adjudication and a later prespecified forecast evaluation are still required
