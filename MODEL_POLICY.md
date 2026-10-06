# QB passing-yards development candidate

This policy is a development candidate, not a frozen or production model. The reserved
2025 season stays closed until a specific bundle/protocol/code checkpoint is frozen. The
executable definition is `modeling/policy.py`; the audit
exports its version and SHA-256 so a report identifies the exact choices it describes.

The specific October 6 checkpoint has now been frozen and evaluated once on 2025.
[Its report](reports/frozen_holdout/holdout.md) records the exact checkpoint and first-access
times. The reusable candidate definition below remains distinct from that immutable checkpoint.
Upcoming forecasts stay disabled; 2025 is now an accessed holdout for future model revisions.

## Point forecast and features

Use `xgb_schedule`: the seven lagged QB features plus designated home, neutral site,
and both teams' rest days. Keep the existing fixed 150-tree, depth-2 XGBoost settings;
there is no new tuning. The [exported candidate policy](reports/calibration_policy/candidate_policy.json)
lists the full feature allowlist and parameters.

Schedule XGBoost has the lowest pooled development MAE (67.51 yards), with improvements
over QB-only XGBoost in both evaluated seasons. Opponent features have mixed incremental
results. This is a development choice, not evidence of superiority on unseen data.
Weather is excluded because historical pregame forecast coverage remains unverified.

## Calibration and history support

Retain the latest six eligible weeks for calibration, with at least 100 pooled QB-game
residuals. Fit the point model on earlier games; training and calibration games stay
separate. Results must clear the existing kickoff-plus-24-hour availability assumption
before the relevant cutoff. Use the existing finite-sample absolute-error order statistic
for 50/80/90% intervals and the smoothed signed-residual tail for diagnostic probabilities.

Require at least **five prior available model-sample QB appearances** for any future
candidate probability or EV. Below five, abstain prospectively and keep the QB visible
for research. Counts refer to the modeled sample, not career experience. All historical
training, calibration, and research evaluation rows remain; this gate does not change
saved forecasts or probabilities. Meeting it does not grant production eligibility.

The [development audit](reports/calibration_policy/calibration_audit.md) finds:

| Pregame sample history | Evaluation QB-games | Distinct players | Pooled 90% coverage |
| --- | ---: | ---: | ---: |
| None | 29 | 29 | 82.76% |
| 1–4 games | 139 | 48 | 84.89% |
| 5+ games | 1,159 | 79 | 90.42% |

History-matched intervals are **diagnostic only**, requiring 30 residuals from the same
pregame history bucket in that fold. The minimum is an application rule, not a statistical
guarantee or a tuned optimum. No no-history pool reaches it (0–18 residuals). For 1–4 games,
only 61 of 139 evaluation rows have matched intervals; their 86.89% coverage compares with
77.05% pooled coverage on those same 61 rows. Matched intervals are wider, averaging
362.06 yards. These subset results do not establish a general solution for sparse history.

For 5+ games, matched intervals cover 89.82%, versus 90.42% pooled, with mean widths of
271.72 versus 278.48 yards. Retain pooled calibration for the candidate. No evaluated game's
outcome selects that player's pool or interval radius. Missing matched intervals
stay unavailable and comparisons use the same available subset.

Repeated players, shared games, time dependence, and overlapping calibration windows
prevent treating row counts as independent sample sizes. Coverage is empirical; there is
no formal coverage, confidence, or profitability guarantee. Fixed half-yard thresholds
are diagnostics, not historical sportsbook lines. Integer push estimates remain experimental.

## Before a holdout run

1. Fix the diagnostic cohort/source requirements and explicitly freeze this policy's
   version, code, data, fitted-estimator, and calibration artifacts. Record their hashes
   and timestamp before accessing 2025 targets.
2. Fix the holdout cohort, history eligibility, result-availability rule, evaluation metrics,
   and missing-data handling before inspecting outcomes. Preserve diagnostics for every
   recorded appearance, including rows ineligible under the prospective history rule.
3. Run the reserved holdout once. Report results against the frozen candidate. A failed
   candidate is not revised and retested against the same season as a new untouched holdout.
4. Validate prospective features, identities, starter/active evidence, and the participation
   cohort before enabling forecasts. The historical sample includes backups and early exits;
   reconciled starter labels do not prove pregame knowledge or solve selection bias.

This step reads only existing 2022–2024 research artifacts. It does not fit or serialize a
production estimator, open the holdout, change historical rows, or enable upcoming forecasts.

## Prepare artifacts for review

```powershell
.\.venv\Scripts\nfl-prop.exe prepare-candidate
.\.venv\Scripts\nfl-prop.exe verify-candidate --bundle models/candidates/<candidate-directory>
```

Preparation fits a separate development estimator using only verified 2022–2024 caches.
All development results must clear kickoff plus 24 hours strictly before the fixed
February 1, 2025 UTC cutoff. The latest six season/weeks supply at least 100 calibration
rows; only earlier available results fit the estimator. Rows too late for training but
outside calibration are retained in an embargo table. Sparse-history appearances stay
in both training and calibration. The five-game prospective gate does not filter them.

Each run saves a new directory containing the XGBoost JSON model, development features,
training/calibration/embargo tables, calibration predictions and residuals, and a source
archive with the dependency constraints. A content-addressed manifest records file hashes,
policy/protocol hashes, feature order, source manifests, environment versions and cutoffs.
Offline verification reconstructs the split and exactly reproduces the saved calibration
predictions, residuals and interval radii. Checksums detect changed contents; they do not
authenticate an unknown bundle's author. Use trusted project artifacts.

The executable **draft** holdout protocol is `modeling/candidate.py`. It proposes a fixed
estimator and fixed development residual pool for all 2025 games, with no holdout refitting,
recalibration or tuning. Earlier holdout appearances may supply strictly lagged inputs
only after their results clear the existing 24-hour availability rule. This differs from
the weekly-refit development evaluation; report their results separately.

The draft cohort is all recorded regular-season QB appearances, including backups,
early exits, zero attempts and newcomers. Report MAE as the primary point metric plus
RMSE/bias, empirical interval coverage/width and probability diagnostics at the existing
four half-yard thresholds. Report all appearances and the five-prior-game subgroup
separately, preserving player/game/week counts and exclusions. Missing lagged/rest features
use native XGBoost missing handling; invalid identities, nonfinite targets and invalid
timestamps fail. No sportsbook ROI or starter-specific validation is claimed.

**Prepared does not mean frozen.** Preparation does not access the holdout. Review the
diagnostic cohort, protocol and exact bundle, then explicitly freeze
their hashes before any 2025 outcome access. Export time is retrospective; it does not
prove historical pregame source availability. Prospective starter/active evidence,
feature availability and participation-cohort validation still gate upcoming forecasts.

## Freeze and evaluate the reserved diagnostic

```powershell
.\.venv\Scripts\nfl-prop.exe freeze-candidate --bundle models/candidates/<candidate-directory>
.\.venv\Scripts\nfl-prop.exe evaluate-holdout --frozen models/frozen/<frozen-directory>
```

Freezing copies the exact verified candidate and adds a content-addressed freeze record
and a source archive for the evaluator. It fixes the all-recorded-appearance diagnostic
cohort, metrics, fixed estimator/calibration, lag/result-availability rules, and missing-data
handling above. Starter labels are diagnostic only; incomplete prospective starter/source
verification remains a separate gate for upcoming forecasts rather than a claim about
this retrospective cohort. No model choice is made from 2025 outcomes.

The evaluator verifies the checkpoint and current executable/dependency hashes before
opening any 2025 cache or downloading outcomes. It uses the original bundled development
history rather than refreshed 2022–2024 targets. QB history is rebuilt across earlier
available appearances; rest days reset each season and use completed regular-season
schedule games. The model and development residuals remain fixed, and all included
QB appearances receive diagnostic predictions even below the prospective history gate.

Ignored `data/holdout/2025/access.json` records first access and each attempt. A completed
command reads the saved checksummed artifacts without refitting or rerunning scoring.
Another freeze is rejected once access has begun. A failed/interrupted attempt requires
`--resume` with the same checkpoint and retains its failure audit. A stale writer lock
requires inspection before removal. These are local project safeguards, not proof of
nonaccess outside this workspace. Source retrieval and the freeze occur retrospectively.

The evaluation does not enable forecasts or establish starter-specific calibration or
betting profitability. Once accessed, 2025 stays an accessed holdout; revised model
choices require a new independent evaluation period.

## Prospective feature availability audit

`nfl-prop current-features` and **Current features** now calculate the same seven QB lags
and four schedule inputs without fitting/loading an estimator. They use the unchanged
development table, the completed 2025 feature artifact as accessed prior history, and a
separate 2026 snapshot. No 2025 download, recalibration or holdout rerun occurs.

Result availability is `max(kickoff + 24h, source retrieval)` and must be at/before the audit
time. Saved historical sources must have been observed by that time. Means/counts use only
available earlier regular-season appearances. Schedule rest resets in 2026 and uses earlier
completed team games; intervening uncompleted/unknown games block it. Nullable season means
and first-season rest retain their model definitions; zeros/negative yards/sparse history
stay visible. Snapshot time at/after scheduled kickoff minus one hour blocks forecast use.

This audits feature readiness only. The five-available-game gate, current ID/freshness/
coverage checks, dated manual status expiry/conflict checks, independent source-content
verification, prospective participation-cohort validation and production enablement remain
separate. Manual active claims do not establish health or playing time. No candidate produces
a prediction, probability or EV. Status URLs are preserved as reviewer-supplied claims.

New checksummed local snapshots preserve inputs, cutoffs, feature values, history/rest
evidence and executable source hashes. They establish when this application observed a
snapshot; they do not reconstruct historical publication or prevent upstream revisions.
Future independent validation must specify the participation/settlement cohort before outcomes.

## Reproduce the diagnostics

```powershell
.\.venv\Scripts\nfl-prop.exe calibration-audit
.\.venv\Scripts\streamlit.exe run app.py
```

Choose **Calibration audit**. The offline CLI writes full JSON and Markdown to
`reports/local/calibration/`; the committed Markdown is a dated snapshot. Its JSON output
includes all six models, history/season splits, matched-availability denominators,
probability scores, reliability-bin support, source hashes, and the policy.
