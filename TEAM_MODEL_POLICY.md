# Team EPA score and margin research policy

Updated October 14, 2026. Contracts for the active team EPA forecasting and research pipeline.

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
Rebuild offline into `data/processed/team_epa/`. Retain source manifests and append-only forecast
snapshots. Historical QB workflows are recoverable from Git history after the repository cleanup.

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

## On-demand weather collection

`team-weather-fetch` verifies upcoming regular-season games against a fresh schedule and ESPN
event kickoff/teams, then geocodes the scheduled venue city. Country/state checks prevent a
same-name city or designated home city from silently replacing an international/neutral venue.
Coordinates remain a city/grid approximation. Outdoor forecasts are recorded for all venues;
no retractable-roof decision or weather score correction is inferred.

Use an explicit `gfs_global` Single Runs request at the latest six-hour UTC initialization
at least six hours ten minutes before the request. This is a conservative run-selection buffer;
the successful response receipt proves actual capture before the live prediction. Keep the
floored kickoff hour and following four hours, Celsius temperature, m/s wind/gusts and mm
hourly precipitation. Source units, hour arrays, values and receipt timing are validated.
Missing values stay null; failed games retain reasons, including when the entire slate fails.
GFS gusts were unavailable in the live probe. Provider coverage can be shorter than `--days`.

Each request creates a separate compact weather journal with source/query digests, collector
hash, run/capture times and kickoff. `team-forecast` collects before fitting and links this
context by checksum in a new sidecar, with identical games, kickoff and horizon required.
The original EPA manifest, feature allowlist and score weights remain unchanged. Neither the
app nor historical lookup fetches automatically. Commands are invoked manually.

`team-weather-lookup --date` interprets the date in America/Chicago and uses the cached schedule.
Choose the latest receipt strictly before the historical Tuesday or one-hour cutoff with the
same game/kickoff; do not relabel a late capture, observed weather, or changed kickoff. An absent
version stays unavailable. This collector is forward capture: its GFS Single Runs archive starts
April 2026. A separately audited historical adapter is required for older operational forecasts.
The small NCAR archive probe does not alter the frozen development policy or clear replay gates.

Primary source: [Single Runs API](https://open-meteo.com/en/docs/single-runs-api).

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

## Chronological setting selection

`team-tune` fixes the published EPA feature allowlist and Ridge alpha 20. Its 11 declared
profiles contain the default; three- and five-season training windows; three- and eight-game
recent windows; 0.65 and 1.0 recent decay; 100 and 400 prior pseudo-plays; a fast combined
profile (three training seasons, three recent games, 0.65 decay, 100 prior plays); and a slow
combined profile (five training seasons, eight recent games, 0.95 decay, 400 prior plays).
Season counts include the forecast's current NFL season. Training still requires at least
64 earlier available completed games, with preprocessing fitted inside each training split.

For each evaluation season, select using only early-week errors from its preceding two
completed NFL seasons. Each candidate's validation predictions are themselves chronological
fits. Choose the lowest margin MAE among settings whose per-team score MAE is no more than
0.05 points above the default. Require at least 0.05 points of margin-MAE improvement; otherwise
retain the default. Break equal margin errors by fixed profile order, with the default first.
Lock that choice before the season's first early-week prediction and use it for both forecast
horizons throughout that season. Refit on available games at each cutoff without reselecting
settings from that season's outcomes. Validation availability and equal model cohorts are
checked explicitly; the following season's outcomes never participate in selection.

The default evaluation uses 2020–2021 to select for 2022, 2021–2022 for 2023, and 2022–2023 for
2024. These periods remain already examined development data, not a new untouched holdout.
Record the computational protocol before evaluation, and retain candidate predictions,
selected predictions, season choices, validation metrics, temporal fits and input/code hashes.
Report subsequent-season score/margin/total error separately by season and horizon, with
approximate paired week-block gain ranges. The research command does not automatically change
published forecasts. The earlier feature experiments and forecasts remain readable because
configurable rating code lives in a separate module from the published feature implementation.

## QB history and injury observations

`team-context-fetch/build/evaluate` use independent 2016–2024 historical snapshots of passer
statistics and injury records. The three fixed Ridge-alpha-20 additions are QB history,
injury observations, and both with an interaction between reported unavailability and prior
passer EPA. Controls, games and chronological cutoffs match the initial comparison. No
current-game schedule QB labels, period-zero ESPN starter flags or actual participation in
the predicted game are model inputs. Existing retrospective starter audits remain audits.

The primary-passer proxy is the unique leader in attempts plus sacks in the last available
current-season team game. Ties and missing observations remain unknown. Do not carry a
previous-season player identity across an offseason without current-season evidence. Use
five earlier available passer games, 0.85 decay and 100 passing pseudo-plays at the prior-only
league rate for passer EPA. This rate covers attempts plus sacks; no scramble-inclusive
dropback claim is made. Include previous participation share, observed passer continuity
and history size. Player statistics retain the kickoff-plus-24-hour availability assumption.

Injury rows must have UTC modification timestamps and stable GSIS IDs. Observed availability
is modification plus 24 hours, strictly before the forecast cutoff. Use only same season,
week and team, with the latest eligible row per player. The archive does not reconstruct
earlier overwritten states or verify original publication/delivery. Missing eligible reports
are unknown, not a healthy team. An eligible partial report can establish reported Out or
Questionable/Doubtful status for the past primary passer, and observed Out counts for OL,
skill and defensive positions. These are report counts, not complete injury/IR rosters or
confirmed replacement starters. Ambiguous Note statuses stay unknown. Reported rest requires
Out with an explicit Rest reason; non-injury-related does not automatically mean rest.
No point deduction or replacement identity is invented from a coach's postgame decision.

Record the feature protocol before evaluation and retain source audits, code hashes,
candidate predictions, feature tables, temporal fits, coverage and per-season/per-horizon
score/margin/total metrics. Approximate paired week-block gains use 5,000 samples with seed
20261013. The 2022–2024 comparisons are already examined development simulations, and 2025
is already accessed. Historical timestamps and source revisions limit the inference; these
results do not alone establish prospective accuracy. Published forecasts remain the current
EPA model while context performance is assessed. Live source integration is separate.

Source definitions: [injury dictionary](https://nflreadr.nflverse.com/articles/dictionary_injuries.html)
and [nflverse data timing](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

## Richer play-by-play and schedule strength

`team-playbook-fetch/build/evaluate` retain the exact canonical EPA play IDs, source values,
eligible-play policy, team outcomes and original controls. Extra yards, sack, interception
and lost-fumble measurements are joined by unique game/play IDs from independent snapshots;
missing IDs or changed game/team identities fail visibly. The expanded aggregates must
reconcile to both canonical play counts and EPA sums. Raw and processed content hashes,
original source manifests, retrieval times and observed source schemas are retained.

Success means positive EPA. Explosive plays gain at least 20 yards on a dropback or 10 yards
on a designed rush, including sacks/scrambles in the dropback denominator. Sack rate uses
dropbacks with a known binary sack flag and is not a pressure measure. Turnovers mean an
interception or lost fumble, counted once per eligible play when both flags are known.
Missing/nonfinite yards and missing event flags are audited and excluded from the specific
measurement denominator, rather than counted as non-events. Defensive counts/exposures
mirror the opposing offense. Rates use five available games, 0.85 decay and 100 exposure
pseudo-plays at the prior-season rate, with a strictly past-only league fallback. Zero
exposure and no valid fallback remain unknown.

Strength fits reuse the existing regularized offense/defense EPA model: available current
season games have weight 1, prior-season games 0.25, and each team effect has a 200-play
penalty. Explicit strength features use the all-play adjusted offense and defense EPA.
SOS averages the current-cutoff ratings of previously faced opponents, with separate
opposing offense and opposing defense measures for the last five games and current season.
Repeated opponents count once per game. Recent history can cross an offseason; season SOS
starts unknown at Week 1. Higher opposing offense EPA means a harder defensive schedule;
lower opposing defense EPA allowed means a harder offensive schedule. Earlier games against
the evaluated team participate in the rating fit; this is not a leave-one-team-out rating.
No predicted-game results, future opponents or full-season hindsight ratings enter SOS.

Three fixed additions use richer play tendencies, opponent strength plus SOS, and both;
each uses the original two-output score pipeline with Ridge alpha 20. Keep identical games,
forecast horizons and expanding training cutoffs, and derive margins from the same rounded
scores. Record the computational policy before evaluation and save feature tables, source/code
hashes, folds, score/margin/total errors by season/horizon, and paired week-block margin gains
(5,000 samples, seed 20261014). These are already examined development seasons, with the
kickoff-plus-24-hour retrospective availability assumption and unreconstructed source revisions.
No automatic model promotion follows from the experiment. Future prospective validation
remains required for a selected revision; 2025 is already accessed.

Source definitions: [nflverse play-by-play dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html)
and its [source field definitions](https://github.com/nflverse/nflreadr/blob/main/data-raw/dictionary_pbp.csv).

## Completed-season graphical diagnostics

`team-diagnose --season 2025` reuses the unchanged current EPA model and its two scoring
controls. Filter team and schedule histories to the requested season or earlier before
constructing features and expanding training fits. Outcomes must be available strictly before
each prediction cutoff; check every model/horizon cohort against the completed schedule.
Use a separate per-season diagnostic namespace, preserving the initial evaluation and forecasts.
Record the computational policy before the run, and retain model/diagnostic hashes, source
provenance, runtime, temporal folds, predictions, CSV, tables and standalone offline HTML charts.

Compare predicted and actual home margins and both team scores, weekly errors against the
baselines, team/week error heatmaps and per-team trajectories. The same game appears in both
teams' diagnostic rows, but enters overall margin metrics once. Missing heatmap cells are
byes, not zero errors. Retain every game when displaying the largest errors and actual-margin
bands. Median and 90th-percentile errors, forecast/outcome standard deviations, score-residual
correlation, bias and descriptive actual-on-predicted calibration aid interpretation. Do not
interpret narrow forecast dispersion alone as underfitting: realized outcomes include residual
variation. Omit calibration slopes for the nearly constant league home-edge control.

Outcome-selected groups and calibration fits are postgame diagnostics and never predictor
filters or applied corrections. 2025 has already been accessed; this report is not an untouched
holdout, and it does not select a revised model. Source revisions and exact historical
publication remain unreconstructed. Proposed fixes require chronological selection and later
prospective verification; no forecast/model promotion is automatic.

## Feature, parameter and loss refinement

`team-refine` compares thirteen profiles using the canonical past-only features.
The controls retain the original 28-input two-score Ridge model. Feature ablations keep
overall recent/season EPA plus context (12), overall season EPA plus context (8), or remove
only the two history-count inputs (26). Other profiles use Ridge penalties 200/2000 instead
of 20, and MultiTaskElasticNet alpha 0.1/0.5 with l1_ratio 0.5. Elastic Net fits both score
targets jointly and can zero feature rows. Treat convergence warnings as failed fits.

On the compact eight inputs, compare median/absolute-error linear regression against
small squared/absolute-loss boosted trees. These candidates fit home margin and total;
reconstruct home/away scores before the common nonnegative/rounding rule derives displayed
margins. Median regression uses quantile 0.5, penalties 0.1/0.01/0.001 and the HiGHS solver.
The two weaker penalties follow an initial training-only audit that found nearly all
coefficients zero at 0.1. Preserve the initial eleven-profile comparison as well; this
adaptive development step does not create an independent evaluation period. Boosting has
80 iterations, learning rate 0.05, at most seven leaves and depth three, at least 40 samples
per leaf, and L2 penalty 10. Disable random early-stopping validation splits. None of the
candidates uses current-game outcomes, the actual starter audit, observed game weather,
market lines or the previously unsuccessful play/SOS additions as predictors in this round.

Simulate selection before each season's first early-week forecast using the preceding two
completed seasons' chronological early-week predictions. Require identical validation
cohorts and outcomes available strictly before the lock. Score MAE may exceed control by
at most 0.05 points and total MAE by 0.10; margin MAE must improve by at least 0.05.
Among eligible candidates within one season-week bootstrap standard error of the best
margin MAE, prefer linear models, fewer fitted active encoded inputs, stronger Ridge
shrinkage, then fixed family/profile order. Use 2,000 block samples, seed 20261014.
If no candidate qualifies, retain current EPA. The simplicity ranking is a declared
heuristic, not a universal parameter-count comparison across algorithms.

Fit complexity audits only on outcomes available before that season's lock. Count raw
inputs, imputation indicators, allocated scalar coefficients/intercepts and active encoded
inputs (coefficient magnitude over 1e-8 in either target). Audit standardized matrix rank,
correlations, and effective Ridge degrees of freedom per output, including the intercept.
Tree leaf values are reported separately and do not equal linear coefficient counts.
In-sample errors are diagnostics; selection uses chronological validation errors.

Keep the selected profile fixed throughout the season and use it for both horizons,
refitting coefficients only on outcomes available at each forecast. Candidate comparisons
cover early-week development seasons; evaluate the locked policy on 2022–2024 and only
the selected choice on 2025. 2025 is already accessed diagnostic data, so even this simulated
lock does not create a fresh holdout or prove prospective accuracy. Save policy before
computation, profiles, validation scores, audits, selections, folds, predictions and source/
code hashes in a separate namespace. Approximate paired 5,000-sample week-block gain ranges
keep development and 2025 separate. Source publication/revisions are not reconstructed;
no model/forecast promotion is automatic.

Primary method references: [linear regularization](https://scikit-learn.org/stable/modules/linear_model.html),
[feature selection](https://scikit-learn.org/stable/modules/feature_selection.html),
[complexity versus validation score](https://scikit-learn.org/stable/auto_examples/model_selection/plot_grid_search_refit_callable.html),
and [boosting controls/loss](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).

## Weekly context readiness and development

`team-pregame-audit`, `team-walkforward`, and `team-replay-check` implement the first three
stages and readiness gate of the proposed enriched 2025 test. The current audit blocks that
test. No enriched 2025 predictions or error scores have been produced by this experiment.
Resolving the source gaps requires a dated provider/report archive and an audited adapter;
there is no force option that assigns publication dates from week labels or retrieval time.

Restrict both training and EPA/scoring history to 2022 onward. The 2022 season provides
initialization; chronological development predictions cover 2023 and 2024, 544 games per
horizon. Do not compare these aggregate error values directly with the older 2016-initialized
2022–2024 report. Compare candidates and controls on identical development games instead.
The intended 2025 Week 1 fit uses 2022–2024, and later weeks add eligible prior-week results.

Lock all outcome-derived EPA/rest/scoring inputs on Tuesday 10:00 Chicago. Fit separate
horizon pipelines on historical features from the matching horizon once per Tuesday lock;
freeze coefficients until the next week. Earlier outcomes must be strictly available under
the kickoff-plus-24-hour assumption. Monday games can remain ineligible on Tuesday.
Do not let a pre-kickoff refresh use Thursday's result in the same NFL week.

Compare the existing 28-input EPA control and scoring controls with compact eight-input
season EPA; compact EPA plus eight observed QB/OL/skill/defense Out-count inputs; compact EPA
plus three temperature/cold/heat inputs; and both additions. All use Ridge alpha 20 with
training-only preprocessing and the same score/margin output contract. Injury counts are
partial observations, not a healthy roster or a confirmed starter. No eligible reports leave
these counts null. Use the existing modification-plus-24-hour assumption for development
injuries. No 2025 injury row without a timestamp becomes a model input.

Temperature comes from explicit GFS global previous-run forecasts at the floored kickoff
hour and the venue's geocoded city. Choose a lead of 1–7 whole days whose valid-time-minus-lead
plus six-hour publication buffer is strictly before the cutoff. This is a conservative
availability assumption, not an exact original run/publication record. The refresh uses at
least a 24-hour-old forecast. Do not use observed temperature, actual game roof, reanalysis,
or stitched historical-forecast values. Fixed indoor venues have zero weather exposure;
retractable/canopy venues stay unknown. The moved 2022 Cleveland–Buffalo game is excluded
from weather inputs. Wind/rain and some outdoor temperatures lack earlier archive coverage;
missing values remain explicit. The city/grid forecast is a coarse weather trial.

Record protocol and source hash before computation. Freeze the research choice using only
2023–2024 early-week errors: require at least 0.05 points of margin improvement over the EPA
control, score regression no greater than 0.05, total regression no greater than 0.10, and
per-season margin regression no greater than 0.25. The approximate paired season/week-block
95% gain range must have a positive lower bound (5,000 draws, seed 20261014). Choose lowest
eligible margin MAE, then fewer declared features, then fixed model order; otherwise retain
the control. This conservative gate is not a correction for all prior model exploration.
Report feature/encoded counts, fitted coefficients and effective Ridge degrees of freedom
from training-only fits at season-start locks. No candidate is automatically promoted.

Keep compact source schema/hash audits, forecast values, one feature/prediction table,
metrics, coverage, fold references and the frozen choice. Do not copy full training data per
fit. Cached choices fail visibly when code, artifact checksums or the frozen record change.
2025 remains previously accessed diagnostic data, even after a new research lock.

## Adaptive weekly simplicity follow-up

`team-simplicity-check` reuses the verified 2022-initialized weekly experiment; it does not
search new estimator families/penalties or fit again. Record the new rule before calculation
in a separate source/input-addressed namespace. The rule follows inspection of the previous
2023–2024 results and their tiny injury gain; it is adaptive development, not a fresh holdout.
Keep the original feature/score source bytes, artifacts and frozen choice intact.

Require every candidate to pass the original margin/score/total/season and paired-range
guards against the 28-input control. Context candidates must also pass those same guards
against compact EPA: at least 0.05 points of early margin improvement, a positive lower
paired-range bound, score regression at most 0.05, total at most 0.10, and single-season
margin regression at most 0.25. Pruning improvement cannot count as context improvement.
The control is always the fallback. Among eligible candidates within one bootstrap standard
error of the best early margin MAE, prefer fewer declared inputs, lower margin error, then
fixed model order. This feature-count ranking is an explicit simplicity heuristic.

Bootstrap by season/week with 5,000 draws, seed 20261015, using identical games and consistent
scores/targets for every model. Candidate gains are paired; the one-standard-error band uses
the best candidate's absolute margin errors. These ranges do not correct repeated model
exploration or establish independence. Complexity counts reuse training-only original audits.

Select with only 2023 outcomes strictly available before the 2024 first Tuesday lock. Assess
that unchanged choice on all 272 following-season games at both horizons. Separately record
a simulated pre-2025 choice from 2023–2024, with the original September 2, 15:00 UTC cutoff
and the actual current recording time. Prior inspection of 2024 is still disclosed; this
separation does not make the check independent. Save selected 2024 predictions, decisions,
metrics and source/artifact hashes, referencing upstream features rather than copying them.

No 2025 outcomes enter this command, and no enriched replay or published-model promotion
follows automatically. Its source, upstream manifest, artifacts and separate frozen record
must all pass reader guards. The original full replay readiness gate remains in force.

## Dated availability source audit

`team-availability-fetch/audit` are source validation commands, separate from model selection
and the enriched replay. Project only 2025 game ID, season/week, home/away teams and UTC kickoff
from the checksum-verified canonical schedule. Require all 272 regular-season games; never
read score or actual starter columns or call a fitting function. Audit 1,088 team/horizon
slots at Tuesday's lock and kickoff minus one hour. Record code/input policy before computing.

For 2025 depth charts, `dt` is the upstream record-load timestamp. Assume load plus 24 hours
strictly before the cutoff and report zero-delay sensitivity. Neither proves original
publication or prevents later source revisions. Select the latest eligible whole-team
snapshot and require its load age to be at most 72 hours. Require a unique distinct rank-one
QB ESPN ID across position slots. Missing, stale, empty and ambiguous charts stay unknown;
do not fall back to an older player row. Keep snapshot counts so a removed player cannot
survive an empty QB edition. Exclude GSIS IDs and names remapped retrospectively by the
producer. A listed depth leader is not a confirmed game starter or complete active/IR roster.

The injury adapter is limited to a 2025 Week 1 source pilot, at most 20 distinct editions and
1 MB per entity. Bind each exact `id_` capture URL and original entity SHA1/base32 digest to
the preserved CDX index. Verify the original encoded entity before bounded decompression;
one real capture retains HTTP gzip. Preserve local body/index SHA256 checksums. Never accept
a redirected version, current page, first-publication date or today's receipt as evidence
that this specific edition existed before a historical cutoff.

Select the latest whole edition captured strictly before the cutoff, no more than 72 hours
old, for the same season/week/team. An absent team section cannot inherit an older report.
Distinguish formal designations, an explicit empty designation statement, partial/pending
notes and unavailable reports. Parse only article-body sections, excluding navigation and
already-completed games. Missing positions among Out designations make all position-group
counts unknown. Names/slugs are parser identities only; no GSIS/ESPN mapping or complete
injury/IR roster is inferred. Derived audit records retain aggregate counts, not player names.

Raw sources and one compact cutoff table stay ignored. Repeated offline reads verify source
manifests, entity/index/artifact digests, source policy and recomputed cutoff records. Earlier
model source files, evidence and frozen choices remain unchanged. Full-season injury coverage,
2022–2024 dated development rosters/starters, complete availability and historical forecast
weather remain unresolved. Pilot coverage cannot clear the original replay readiness gate.
