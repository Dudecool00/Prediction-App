# Repository health audit

Checked October 15, 2026, initially against merged `main` at
[`e8ccc2c`](https://github.com/Dudecool00/Prediction-App/commit/e8ccc2c9bd9421a0809712772aba32d35fd56280),
then refreshed to [`91afc64`](https://github.com/Dudecool00/Prediction-App/commit/91afc64225ac127c8e17bf16a8b2708efb176e0f)
when [PR #25](https://github.com/Dudecool00/Prediction-App/pull/25) merged during the audit.
The review covers eleven team-model merges from PR #15 through #25, current source/tests,
local saved team artifacts and CI configuration. The original snapshot has 78 tests;
the combined tree includes the thirteen availability cases for 91 total.

## Failed merge checks

| Merged change | Main workflow | Finding |
|---|---|---|
| #21, repository cleanup | [38629234440](https://github.com/Dudecool00/Prediction-App/actions/runs/38629234440) | Linux passed; Windows stopped at `pip check` |
| #23, weather collection | [38660254158](https://github.com/Dudecool00/Prediction-App/actions/runs/38660254158) | Linux passed; Windows stopped at `pip check` |

Both Windows failures report the same conflict: the hosted runner's globally installed
`pipx 1.17.12` requires `platformdirs>=4.12.2`, while this project's tested constraints pin
`platformdirs==4.11.8`. Subsequent lint, typing and test steps were skipped on those Windows
jobs. The logs identify an environment conflict, not a failed model assertion.

The initial merged-main [workflow 38662787345](https://github.com/Dudecool00/Prediction-App/actions/runs/38662787345)
passed dependency checks, lint, formatting, strict typing and all **78 tests on each of
Linux/Python 3.11 and Windows/Python 3.12**. That later pass does not remove the recurring
environment risk: the workflow previously installed into the runner's global interpreter.

The repair creates an isolated `.venv` in each ephemeral CI job. Every installation and
validation step explicitly calls its platform-specific interpreter. Keep the dependency
constraints and model source bytes unchanged; do not relax `pip check` or suppress failures.
The job environments exist on GitHub runners, without adding another local environment.
The fix's exact-commit workflow results are recorded in its pull request.

## Read-only artifact and source checks

The initial thirteen local audit groups passed; the merged availability audit is checked
again when refreshing the combined branch:

- Canonical team cache; active score evaluation; feature experiments; chronological tuning;
  passer/injury comparison; playbook/SOS comparison; saved 2025 diagnostics; refinement;
  weekly context and original frozen choice; current simplicity choice.
- Five prospective score versions, with all 15 referenced tables verified, and both saved
  weather captures with their original receipt/timing checks.
- Source parsing/internal imports, local Markdown links and tracked repository contents.

The added availability reader revalidates its preserved source/index digests and all
1,088 cutoff records without reading score columns or fitting a model.

The nine core cache namespaces above contain 24 referenced artifacts; all pass their
reader/source guards and content checksums. The current simplicity cache and its frozen
choice also pass. Its older revision retains valid artifact bytes and remains an older
revision rather than being relabeled with current code. No cache was regenerated.

Prediction artifacts reconcile nonnegative finite scores, home/away signed margins and
totals. Where stored, latest training-result availability precedes prediction time. Feature
and modeling code retain explicit allowlists, training-only preprocessing, strict prior
outcome cutoffs and common-game comparison guards. Existing tests exercise future-data
invariance, source corruption, output consistency, research selection and app pages.

CLI help/version, local dependency validation, lint, formatting and strict typing pass.
All internal imports and local documentation targets resolve. The audited tracked tree
contains approximately 2.34 MB, with raw data, model binaries, environments and credentials
excluded. The integrity inspection creates no model fit or new training-table copy.

The local full-suite attempt also reproduces the previously observed Windows native-package
diagnostics and slow/stalled execution. Fresh Linux and Windows CI provide complete-suite
verification; no test or fault handler was disabled. This limitation concerns the local
runtime and is not evidence that all local execution paths are reliable.

## Remaining limits and merge policy

GitHub's branch response reports `main` as unprotected, with required status checks off.
Require `checks (ubuntu-latest, 3.11)` and `checks (windows-latest, 3.12)` before merging,
including checks against the current base. This audit does not change repository access
rules or merge the repair automatically.

Passing engineering checks does not establish predictive accuracy or complete historical
availability. The enriched 2025 replay remains gated by dated availability and historical
weather work. Existing 2025 diagnostics remain previously accessed evidence. No model is
promoted, no new MAE is claimed, and pending source adapters require their own review.
