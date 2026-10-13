# Team EPA score and margin research policy

October 13, 2026. First implementation; independent of the legacy QB candidate and holdout policy.

## Outcomes and consistency

The target is both teams' final score, including overtime, for completed regular-season games.
The designated home score and away score are fitted together using a two-output Ridge regressor.
Its predictions are clipped at zero and rounded to one decimal. Home margin is the rounded
difference of those displayed scores; away margin is its negation; total is their sum.
Fractional scores represent projected averages, not a claim that a game can end with fractional points.

One game is one observation. Team-level EPA aggregation has two rows per game, but the score
model has one canonical matchup row. Both team scores enter score-error summaries; each game
enters margin and total summaries once. Early-week and refresh results are separate.

## Data and features

Use independent per-season snapshots in `data/raw/team_epa/`, with selected unmodified source
columns, upstream schema metadata, retrieval times, package version, and file checksums.
Rebuild offline into `data/processed/team_epa/`. Preserve legacy QB files and frozen artifacts.

Eligible offense is a pass/rush-flagged play with finite team EPA, excluding `no_play`, kneels,
spikes, and two-point tries. Penalties on eligible plays are retained; nullified `no_play`
records are excluded. Dropbacks use `qb_dropback == 1`, including sacks and scrambles. Designed
rushes have `rush == 1` and no dropback. Defensive EPA is the same offensive EPA allowed by the
opponent defense; lower is better. Special teams do not enter the first EPA ratings.

Audit both team sides against all completed schedule games. Fail visibly for missing eligible
EPA on an entire team-game, mismatched identities, duplicate play keys, unknown dropback
classification, malformed completed-game kickoffs, or invalid scores. Exclude individual
missing/nonfinite EPA values with explicit counts; do not replace them with zero.

Normalize relocated franchise abbreviations for history continuity. Keep true neutral sites
separate from designated home teams. No QB participation threshold or starter-label gate applies
to this team cohort.

The explicit feature list is offense and defense EPA for all eligible plays, dropbacks, and
designed rushes, each for recent and season history, for each team; home field, rest difference,
and prior-game counts. Recent history is five available games with 0.85 decay per game, weighted
by play volume. Season rates use 200 prior-season pseudo-plays, with a prior-only league rate
fallback. Four prior-season pseudo-games support the non-EPA scoring baseline. Hyperparameters
are fixed for this first candidate, not selected on 2025 results.

Current scores, current EPA, market lines, observed game weather, and starter audit outcomes
are excluded from the EPA model feature list. No injury or QB adjustment is implied.

The prior-team scoring baseline also receives a home advantage estimated from earlier training
scores. It adds half that advantage to the home projection and subtracts half from the away
projection, preserving the projected total. Neutral sites receive no venue adjustment.

Rest uses the previous completed same-season schedule game independently of EPA availability.
For example, Monday's known game can determine Sunday rest even while its EPA remains ineligible
under the 24-hour rule. A missing or still-running intervening game leaves rest unknown; the
historical completion check also requires four hours since that prior kickoff. Live matchup
features use the full schedule history rather than only the upcoming slate.

## Timing and evaluation

Historical early-week features use Tuesday 10:00 America/Chicago on/before the first scheduled
game of that season/week; if that time would follow the first kickoff, use the preceding Tuesday.
Refresh features use recorded kickoff minus one hour. Earlier results become eligible strictly
after kickoff plus 24 hours. Each fit uses only completed games available before its forecast
cutoff. Preprocessing is fitted inside the corresponding training split.

This availability rule is a retrospective assumption, not verified historical publication.
Historical NFL revisions, rescheduling, and upstream EPA model vintages are not reconstructed.
Report backtests as retrospective simulations. Development reporting defaults to 2022–2024
with 2016–2021 initialization history. The QB project has accessed 2025; any 2025 team diagnostic
must be labeled already accessed. Previously viewed 2026 data is also retrospective.

The candidate is Ridge alpha 20 after training-only median imputation, missingness indicators,
and standardization. Require at least 64 earlier completed games. Compare against league scoring
and prior team scoring on identical games. Report score, margin, and total MAE/RMSE/bias by
season and horizon. A small retrospective gain does not establish market performance.

## Upcoming research snapshots

Forecast commands use the actual current UTC run time and sources retrieved no later than that
time. The current season's raw source snapshot must be under 24 hours old. Preserve all earlier
forecast versions. Live refresh runs require a game 1–24 hours before kickoff; historical
refresh comparisons use the fixed one-hour cutoff, so live forecast lead times remain explicit.

Each snapshot saves predictions, matchup features, training features/targets, upstream and
processed source manifests, model/source hashes, runtime versions, model policy, and run time.
The app only reads these artifacts. It does not fit or fetch automatically. Commands currently
provide research forecasts rather than an unattended scheduled service.

Publish no win/cover probabilities or intervals until team-margin and score uncertainty is
calibrated on separate chronological games. Do not reuse QB-yard residuals or yard settlement.
QB continuity, injury/weather information, outcome tracking summaries,
and prospective performance comparisons remain explicit next milestones.

## Fixed feature experiments

`team-experiment` compares the current controls with four Ridge-alpha-20 ablations: EPA plus
scoring history; EPA plus scoring and play volume; EPA plus opponent adjustment; and all three
additions together. Scoring rates use the existing four-prior-season-game shrinkage. Play
volume uses the same shrinkage for eligible offensive and defensive plays per game. These
are explicit additional allowlists, not automatic inclusion of target or metadata columns.

Opponent ratings separately fit all-play, dropback, and designed-rush EPA as an unpenalized
league intercept plus offensive-team and opposing-defensive-team effects. Use play counts
as weights, current season weight 1 and previous season weight 0.25. Penalize each team effect
with 200 pseudo-plays toward zero. Ratings equal intercept plus team effect; lower defensive
EPA allowed is better. Each rating fit uses only league results strictly available before
that matchup's prediction time. No future team identities or outcomes enter its fit.

Evaluate the identical games/cutoffs and controls across 2022–2024, with per-season and per-horizon
score/margin/total MAE, RMSE and bias. Margin MAE is primary, with score error and seasonal
stability assessed alongside it. Paired season/week-block resampling uses 5,000 draws and seed
20261013; these approximate ranges do not correct serial dependence or candidate selection.
Development gains require later prospective validation. Reports record feature policies,
code hashes, input snapshots, runtime versions and predictions. Stale code or corrupted
artifacts fail visibly. The command does not automatically promote a winning candidate.
