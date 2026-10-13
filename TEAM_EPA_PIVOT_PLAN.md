# Team EPA and weekly NFL margin: discussion draft

Date: October 13, 2026. Status: implementation started with team data, joint score regression,
chronological backtesting, and team app pages. Later feature/calibration milestones remain open.

## Objective and confirmed preference

Predict both teams' final scores and the final scoring margin of each scheduled NFL regular-season
game using information available before the forecast. EPA is the central measure of team efficiency.
The user added final-score projections: a 31.2–27.5 projection must yield +3.7 and -3.7 margins
depending on the selected team. Fit scores jointly and derive the margin and total from the same
displayed values so the outputs agree.

The user selected an early-week forecast with a pre-kickoff refresh. Each forecast version must keep its original inputs, prediction time, model version, and eventual outcome. The two forecast horizons are evaluated separately.

Suggested first release: Tuesday after the prior week's available results have been ingested, then a refresh one hour before each game's kickoff. The exact publication times remain a product decision. Missing Monday-night data should remain visible rather than being treated as available by assumption.

## What the current repository gives us

The repository currently downloads weekly player statistics and schedules through nflreadpy, stores content-addressed Parquet files with checksummed manifests, builds lagged QB features, runs chronological weekly evaluation, calibrates uncertainty, and displays research through Streamlit. Linux and Windows CI run linting, formatting, typing, and tests.

The installed nflreadpy package already exposes load_pbp and load_team_stats. The current historical ingestion module does not download play-by-play. Existing player-statistics schemas contain passing_epa and rushing_epa, but those fields are not a substitute for an audited team offense/defense play-by-play dataset.

Much of the modeling code has explicit player_id, target_passing_yards, QB feature lists, season restrictions, and kickoff-minus-one-hour assumptions. Reuse the storage utilities and evaluation patterns; adapt the contracts and model modules rather than assuming they are already generic.

Existing weather research and live prospective forecasts remain unfinished. The team project needs its own readiness rules.

## Disposition of QB work

| Existing asset | Proposed treatment | Reason |
| --- | --- | --- |
| Raw player statistics, source manifests, starter evidence, reports, and frozen bundles | Preserve as legacy research | Retains reproducibility and any later QB enrichment without recollection |
| Passing-yards target, yard/attempt rolling features, fitted QB estimators, QB calibration | Retire from the active team model | They estimate a different outcome; their learned parameters and residuals cannot transfer |
| QB prop selection, passing-yard settlement, and appearance enrollment pages | Retire from the main app workflow after the replacement works | The product unit becomes a scheduled game |
| Full historical starter audits and gamebook adjudication | Keep as optional legacy evidence | Unresolved QB labels do not block an EPA-only team baseline |
| Current depth charts, stable QB identities, timestamped starter/availability evidence | Keep a compact optional context layer | Helps identify a change from the quarterback represented in recent team EPA |
| Generic evidence storage, freshness checks, snapshot journals, and source hashes | Reuse or generalize | Useful for both forecast horizons and later injury context |
| Odds conversion and generic probability/EV arithmetic | Keep for a later optional spread comparison | Signed margins, ties, pushes, and spread sign conventions need a new contract |

Remove dependencies on QB-specific tables from the active team path before considering physical cleanup. No deletion is proposed in this planning step. The team baseline must be runnable without loading QB models or completing the QB participation registry.

QB information can leave the core modeling pipeline while team passing efficiency remains essential. Player identity is an optional adjustment to a team rating, not the unit of prediction. QB-named play flags such as qb_dropback are still useful for classifying team plays.

## Data design

1. **Play table:** immutable seasonal play-by-play snapshots, with game/play identity, offense, defense, EPA, play classification, and relevant game-state fields.
2. **Team-game table:** one row per team per completed game, with EPA sums, eligible-play counts, offense/defense rates, and auditing metadata. Join against all completed schedule games rather than against QB appearances.
3. **Matchup feature table:** one row per game per forecast time, containing strictly prior team histories for both sides, known schedule context, and feature availability.
4. **Forecast journal:** both forecast versions, source/model hashes, predicted signed margin, optional probabilities/intervals, and eventual final margin.

Define target_home_margin = home_score - away_score, including overtime. A prediction of +4.8 means the designated home team is favored by 4.8 points in the model; -4.8 favors the away team. The display can translate this into a team-name label. Neutral games do not get a home-field effect merely because one team is designated home.

Do not train separate independent home and away predictions for the same game. Derive the opposite team's margin by negation. If both team perspectives are used for any modeling experiment, keep the game together in folds and count the outcome once in evaluation.

## EPA definitions and initial features

Use play-level **epa**, not **qb_epa**, for team efficiency. qb_epa changes attribution on receiver-fumble plays. Defensive EPA allowed is the opponent offense's EPA on plays against that defense; lower is better.

Before fitting, publish the play inclusion contract. Start with ordinary offensive plays with valid team IDs and finite EPA; exclude kneels and spikes. Audit nullified plays, penalties, two-point tries, sacks, and scrambles explicitly. Dropback efficiency includes sacks and scrambles; designed-rush efficiency excludes dropbacks. Special teams use a separate feature definition. Missing EPA is never silently zero.

Core first-model features:

- Offensive EPA/play and defensive EPA/play allowed.
- Offensive and defensive dropback EPA/play and designed-rush EPA/play.
- Recent weighted history and longer season history, each with eligible-play counts.
- Home/neutral context and rest differential, derived from the schedule.
- Prior-season ratings shrunk toward a historical league baseline for early-season games.

Aggregate rates as weighted EPA sums divided by weighted eligible-play counts, so a low-volume game does not automatically receive the same weight as a high-volume game. Select decay, window lengths, and prior strength using development folds. Fit any league baselines and opponent adjustments using only history available at that fold's cutoff.

Add features in controlled comparisons rather than all at once:

- Opponent-adjusted offense and defense ratings, with regularization for limited history.
- EPA success rate, pace, and special-teams efficiency.
- Alternative treatment of extreme game states and turnover-related volatility.
- Timestamped starter changes and a shrinkage-based QB adjustment once historical feature availability is defensible.
- Forecast weather and broader injury context once source coverage is verified.

Use the same play rules for both teams and across historical and current-season data. Filtering an extreme game state may use that play's contemporaneous state, never the game's eventual margin. If using win-probability filters in a market-independent model, exclude spread-adjusted Vegas win probabilities.

## Model sequence

1. Establish a trivial margin baseline and a prior-performance team-rating baseline, with a home-field term fitted on earlier training data.
2. Fit a two-output Ridge regressor to home and away final scores using the teams' EPA matchup features and schedule context, deriving margin and total from those scores. This is the first candidate because coefficients make each feature's contribution easier to inspect.
3. Compare the existing XGBoost tooling using the same games and time cutoffs. Promote it only if chronological evaluation shows a useful improvement.
4. Calibrate intervals and win/tie probabilities with team-margin residuals from separate historical calibration games. Existing QB residuals and the rounded-yard settlement model are inapplicable.

Do not convert an EPA difference into points with an assumed multiplier. Learn the relationship
from earlier games. A joint score model keeps score and margin outputs consistent; a full drive
simulation can remain a later experiment.

Suggested v1 scope is a market-independent forecast. Historical spread lines can be a reference benchmark, clearly labeled by their timing. If a later model includes market lines, maintain it as a separately identified model using lines captured at the corresponding prediction time.

## Evaluation and availability

Proposed historical coverage: 2016 onward, subject to an initial schema and completeness audit. This supplies more game-level observations than the current 2022–2024 sample without requiring all available NFL history. Keep regular season as the first cohort; treat postseason as a later, separately evaluated extension.

Use earlier seasons to initialize models, walk forward through 2022–2024 for model/feature selection, and label 2025 as an already-accessed retrospective diagnostic. The QB project has accessed 2025 outcomes; a new target does not make that season a fresh blind holdout. Already examined 2026 data is also retrospective. Freeze the first team-model protocol before publishing and evaluating future, unplayed games.

At each historical forecast time, admit only earlier results whose defined availability precedes that cutoff. Train preprocessing and ratings inside the temporal split. Each early-week forecast uses one recorded slate cutoff; each refresh uses its own recorded pre-kickoff cutoff. Never overwrite the early-week prediction with the refresh.

Latest downloadable historical EPA may contain later corrections or be generated by an upstream model fitted later. Those historical tests must be labeled retrospective simulations, not claims that these exact inputs were published then. Real timestamped snapshots are needed for prospective evidence.

Report:

- Margin MAE, RMSE, bias, and error by season and week.
- Error for early-season games and neutral-site games.
- Paired comparison against the non-EPA baseline on identical games.
- Interval coverage and width, and probability calibration if probabilities are published.
- Early-week versus refresh accuracy on the same games.
- Market comparison only where line timing and coverage support it; closing lines are a later-information benchmark for Tuesday forecasts.

NFL margins are signed integers, with ties and concentrated scoring margins. Any later spread-probability layer must validate its treatment of key margins and push mass rather than copying the passing-yard distribution approximation.

## Repo implementation sequence

| Milestone | Deliverable and completion check |
| --- | --- |
| 1. Team data foundation | Add a separate play-by-play ingestion/cache namespace and team-game builder; reconcile both sides of completed schedule games, missing data, play counts, and team aliases |
| 2. EPA margin baseline | Build prior-only matchup features and chronological Ridge evaluation; compare against a simple team-rating baseline on the same games |
| 3. Feature comparison and uncertainty | Compare opponent adjustment, window choices, and optional added context; calibrate team-margin intervals and freeze a candidate protocol |
| 4. Weekly team interface | Replace the primary QB workflow with a weekly slate, margin forecasts, team efficiency breakdowns, source timestamps, and forecast history |
| 5. Prospective tracking and cleanup | Save early-week and pre-kickoff predictions before outcomes, score them separately, then remove unused QB runtime dependencies and archive legacy pages |

Suggested code additions are data/team_games.py, features/team_epa.py, features/matchups.py, and modeling/team_margin.py, plus explicit team CLI commands and namespaces. Reuse data/storage.py. Generalize shared time/identity validation deliberately. Keep the package name stable during the first modeling milestone to avoid a broad rename unrelated to predictive behavior.

The existing QB policy and season guards stay attached to legacy QB work. Write a team-specific policy with its own target, permissible seasons, forecast horizons, feature list, calibration, and promotion criteria. Do not disable global safeguards just to fit the new model.

Required correctness checks include EPA sign and denominator, dropback classification, both-team coverage, neutral sites, rest, missing-versus-zero handling, and current/future outcome mutation. Model and rating estimates must not change when unavailable outcomes are altered. Use the existing CI to run these meaningful checks.

## Choices still open for discussion

- Exact early-week publication time and pre-kickoff cutoff.
- Whether the first screen should prioritize predicted margin alone or also calibrated win probability.
- Whether sportsbook lines should remain comparison-only in v1; this draft recommends that approach.
- Historical start year after checking source coverage.

## Primary source references

- [nflverse play-by-play field dictionary](https://raw.githubusercontent.com/nflverse/nflreadr/master/data-raw/dictionary_pbp.csv): EPA, QB EPA, dropbacks, play flags, success, and Vegas-adjusted probabilities.
- [nflverse schedule field dictionary](https://raw.githubusercontent.com/nflverse/nflreadr/master/data-raw/dictionary_schedules.csv): scores, result sign, neutral locations, rest, and spread conventions.
- [nflverse data availability schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html): play-by-play updates and later stat corrections; actual source freshness still requires verification.
- [nflreadpy](https://nflreadpy.nflverse.com/): existing Python data-source ecosystem.
