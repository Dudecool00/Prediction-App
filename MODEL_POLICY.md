# QB passing-yards development candidate

This policy is a development candidate, not a frozen or production model. The reserved
2025 season stays closed. The executable definition is `modeling/policy.py`; the audit
exports its version and SHA-256 so a report identifies the exact choices it describes.

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

1. Resolve the remaining cohort/source requirements and explicitly freeze this policy's
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

## Reproduce the diagnostics

```powershell
.\.venv\Scripts\nfl-prop.exe calibration-audit
.\.venv\Scripts\streamlit.exe run app.py
```

Choose **Calibration audit**. The offline CLI writes full JSON and Markdown to
`reports/local/calibration/`; the committed Markdown is a dated snapshot. Its JSON output
includes all six models, history/season splits, matched-availability denominators,
probability scores, reliability-bin support, source hashes, and the policy.
