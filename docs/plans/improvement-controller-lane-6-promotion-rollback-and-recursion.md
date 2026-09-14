---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-14
revised: 2026-09-14
tracking: https://github.com/tomcounsell/ai/issues/3218
last_comment_id: 5603830035
revision_applied: true
revision_applied_at: 2026-09-14T11:19:43Z
---

# Improvement controller lane 6: production promotion, rollback drills, and the recursive comparison

## Problem

Lane 4 ends at a verdict. When `tools/improvement_eval/runner.py::evaluate` writes an
`ImprovementEvaluation` with `verdict="accept"`, the loop has shown that a candidate beat its
incumbent on held-out inputs under a frozen contract, and then nothing happens. No module on
`main` or on lane 4's branch creates an `ImprovementRelease`. The record exists with every field
the parent plan asked for (`exposure`, `rollback_plan`, `observation_window_ends_at`, `outcome`,
`approved_by`, `charter_digest`) and every one is null on every row, because there are no rows.

That is the smaller half of the gap. The larger half is the two claims charter §6 actually cares
about. **Ability improved** needs held-out *and* production gains over the incumbent, which means a
release has to be exposed, observed for a stated window, and have its outcome written back against
a baseline frozen before exposure. **Recursive improvement demonstrated** needs a changed research
process to produce greater validated gains per comparable budget on fresh opportunities, which
means the process in force has to be named by digest on every revision, the opportunity set has to
be provably unworked, and the two arms' budgets have to be accounted in the same units and shown.
`ImprovementModelRevision.research_process_digest` exists for exactly this and has no writer.

**Current behavior:**

- An `accept` verdict is terminal. There is no proposal, no exposure record, no observation
  window, and no rollback path. "We can undo this" is not written anywhere because there is
  nothing to undo.
- No candidate-surface denylist exists in code. Gap G in the parent plan promises that a manifest
  naming `docs/improvement-charter.md` or `models/improvement_charter.py` is refused before
  construction; today nothing checks.
- There is no code path that names the two preconditions for automated promotion. The model
  docstring says promotion is disabled; a docstring is not a gate.
- No rollback has ever been executed against a release record, because none exists. A rollback
  that has never run is a plan, not a capability.
- Nothing distinguishes one research process from another. Two planner ticks that select cases
  by different rules leave identical records, so a later reader cannot tell which process produced
  which gain, and a level-3 claim is unfalsifiable.
- The dashboard has no release lineage. `ui/data/improvement.py` exports four getters
  (`get_coverage`, `get_goals`, `get_intervention_burden`, `get_provisional_assumptions`), pinned
  as an exact list at `tests/unit/test_ui_app.py:721-733` so no lane can add an activity counter.

**Desired outcome:**

- A release is proposed from an `accept` verdict with its kind, surfaces, candidate ref, base
  revision, exposure plan, rollback plan, and observation plan all written at proposal time; the
  proposal is refused if any surface is on the denylist, if the manifest lacks a candidate ref, or
  if the evaluation's charter digest is not the pinned one.
- A rollback drill executes the rollback plan for real against the proposed release in a throwaway
  worktree, verifies the tree is restored to the base revision on every declared surface, and
  writes a drill record that names what was exercised and what was not. Approval is refused
  without a passed drill.
- Exposure is a recorded event with a merge SHA from the ordinary SDLC pipeline, a frozen baseline
  of the production metrics the observation plan names, and an `exposed_at`. The window closes on
  its stated date, the outcome is written back with raw counts and denominators beside every rate,
  and a detection decline is reported as a detection decline rather than as a gain.
- Automated promotion remains disabled. The function that would promote refuses every call and
  names both unmet preconditions: evaluator secrets and production credentials separated from
  candidate execution, and a human-amended charter naming the reversible surfaces. There is no
  setting, env key, or file flag that changes its answer.
- The research process is a digestable specification. A recursive comparison freezes a contract
  naming two process digests, a fresh opportunity set, and one budget cap per arm; runs both arms;
  refuses a claim when the budgets are not comparable or unit-1 spend is unknown; and writes an
  `ImprovementEvaluation` with effect, confidence interval, correction, and verdict.
- A claim report states, for each ladder level, whether the evidence supports it, with the
  confidence interval, the correction, and the observation that would falsify it, and says plainly
  when it does not.
- The dashboard renders release lineage as a fifth getter and never presents experiment count or
  merged-patch count as improvement.

## Freshness Check

**Baseline commit:** `89f800876` (`main`, 2026-09-14). Build head: lane 4's `session/sdlc-3216` at
`fe6f55072` (PR #3309, open), which is where `tools/improvement_eval/` and the `accept` verdict live.
**Issue filed at:** 2026-09-07T04:45:59Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `ImprovementRelease` fields (`models/improvement_release.py:50-84`): the issue's list of
  `rollback_plan` and `observation_window_ends_at` still holds; `charter_digest` was added by lane
  2b (`:84`). `RELEASE_STATES` (`:37-43`) is five values. No writer anywhere. Still holds.
- `ImprovementModelRevision.research_process_digest` (`models/improvement_model_revision.py:70`):
  still declared, still no writer. Still holds.
- `ui/data/improvement.py` "exports exactly three getters": **drifted**. Lane 2b (`aff4d7e2e`)
  added `get_goals`; the pin at `tests/unit/test_ui_app.py:721-733` is four names. The rule the pin
  enforces is unchanged; the count is four and becomes five with this lane.
- `docs/improvement-charter.md`: v2, `effective: 2026-09-07`, no section naming reversible
  surfaces. Precondition 2 is verifiably unmet against the pinned text.

**Cited sibling issues/PRs re-checked:**
- #3177 (parent): open. Its plan is the family plan; lane 6 is its last numbered lane.
- #3217 (lane 5, dependency): open, no commits on `session/sdlc-3217`, no plan document. Treated
  as a seam, not a blocker (see Technical Approach, "What this lane assumes from lane 5").
- #3216 (lane 4): open, PR #3309 open at `fe6f55072`, critique round 4 READY TO BUILD, build
  checkpoints landed. This lane's proposal reads its `accept` verdict, so #3216 is a build
  prerequisite.
- #3215 (lane 3): open, no commits. Its `valor-improve` CLI and unit-1 meter do not exist; this
  lane ships its own entry point and reads unit 1 through a seam that reports unknown.
- #3255 (lane 2b): closed 2026-09-09 via PR #3275. Its comment on #3218 (id 5603830035) assigns
  merge authority through the pipeline and the evaluator-replacement release type to this lane;
  both are in scope below.
- #3274 (lane 7): closed 2026-09-14 via PR #3299. It landed after the issue was filed and changes
  the budget picture: unit 3 now has a meter (`tools/infrastructure_budget.py`) and a ledger
  (`InfrastructureReservation`, `spend_receipt` evidence), and the artifact retention root moved
  outside the checkout (`POPOTO_IMPROVEMENT_CONTENT_PATH`). The comparison's budget reader uses
  both.

**Commits on main since issue was filed (touching referenced files):**
- `5b994db3f` lanes 1 and 2 (#3224): created every `Improvement*` model the issue names. The issue
  was filed by that build as a child; the models landed nine hours later. Irrelevant to premise.
- `aff4d7e2e` lane 2b (#3275): added `charter_digest` to the release and `get_goals` to the
  dashboard. Partially addresses (the charter-digest field this lane's proposal gate reads).
- `37dc10b33` lane 7 (#3299): unit-3 meter, ledger, operating report. Changes the budget
  accounting the comparison can do; does not touch releases.

**Active plans in `docs/plans/` overlapping this area:** `recursive-self-improvement.md` (the
family plan, source of the lane split) and `improvement-controller-lane-4-frozen-evaluation-inputs.md`
(lane 4, owns `tools/improvement_eval/`; this lane imports from it and modifies nothing there).
No plan claims releases, drills, promotion, or the recursive comparison.

**Notes:** The premise is intact. Two facts changed the shape of the plan rather than its
premise: the getter count (four, not three) and lane 7's unit-3 ledger (the comparison can now
account one of the two USD units from records rather than declaring both unknown).

## Prior Art

- **PR #3224** (lanes 1 and 2): created `ImprovementRelease` with `rollback_plan` and
  `observation_window_ends_at` declared as "written at proposal time, before anything is exposed",
  and the docstring rule that promotion is disabled. Declared the shape; wrote no row.
- **PR #3275** (lane 2b): added `charter_digest` to the release and made the charter loader refuse a
  file whose owner is not Tom. The "charter is not a candidate surface" rule is code at the loader;
  the denylist that refuses it as a *surface* is still this lane's.
- **PR #3299** (lane 7): the unit-3 meter's `admit → release | settle` shape, with every reservation
  carrying a paired idempotent release and a refusal recorded as a row. The release lifecycle here
  follows the same discipline: every transition is a recorded event with a reason code, and a
  refusal is a return value, never a silent no-op. Its operating report's "each answer degrades
  independently" structure is the model for the claim report.
- **PR #3309** (lane 4, open): the frozen-contract primitives (`freeze_protocol`,
  `compute_contract_digest`), the `accept` verdict, and `tools/improvement_eval/statistics.py`
  (clustered bootstrap, Holm). The recursive comparison reuses all three and adds no statistics.
- **Bridge self-healing level 4** (`docs/features/bridge-self-healing.md:181`): the one existing
  rollback mechanism in this repo, `git revert HEAD` on a crash pattern, disabled unless
  `data/auto-revert-enabled` exists. It is prior art in both directions. Its revert-and-verify
  shape is what a rollback plan of kind `git_revert` rehearses. Its file-flag enable is exactly
  the shape the promotion gate must not have: a `touch` is not a charter amendment, so the gate
  reads no file, setting, or env key.
- **No closed issue or merged PR** has attempted a release record, an observation window, a drill,
  or a process comparison. `gh issue list --state closed --search "release rollback promotion
  improvement"` returns nothing related.

## Research

**Queries used:**
- `compute-matched comparison agent self-improvement evaluation fresh tasks budget matched baseline pitfalls`
- `rollback drill game day practice rollback before deploy SRE observation window post-deployment verification`

**Key findings:**
- Budget-matched comparison is the difference between measuring a process and measuring its
  budget. The budget-aware evaluation literature found that a reported reasoning-strategy
  "improvement" was an unfair comparison because the strategy used far more tokens, and that under
  matched budgets a plain baseline won on every dataset
  ([Budget-Aware Evaluation of LLM Reasoning Strategies](https://aclanthology.org/2024.emnlp-main.1112.pdf)).
  The plan matches budgets by giving both arms the same cap in every unit, counting retries and
  evaluation inside the cap, and refusing a verdict when the arms' accounted spend differs by more
  than a stated tolerance.
- Fresh tasks are non-negotiable: one harness benchmark found roughly 40% of harness updates that
  improved validation reduced out-of-distribution transfer, and a position paper states that
  protected evaluation plus matched compute is what separates genuine improvement from evaluator
  exploitation or increased search
  ([PAST-Bench](https://arxiv.org/html/2608.04003v1),
  [The Last AI Built by Humans](https://arxiv.org/html/2609.11873v1)). The plan decides freshness
  by record lookup and hashes the opportunity set into the frozen contract.
- Evolving evaluators break cross-round comparison; the mitigation is freezing evaluators within an
  epoch and validating replacements against an independent anchor
  ([Your Agent May Misevolve](https://arxiv.org/pdf/2509.26354)). The evaluator-replacement release
  kind requires a calibration reference and a written argument, and the comparison pins
  `evaluator_version` in its contract.
- A rollback path is real only once exercised; a drill record should name the fault injected, the
  restore step, the verification that ran, the time it took, and explicitly which parts were
  exercised versus placeholder, so a later reader cannot mistake "the alert fired" for "somebody was
  woken" ([Rollback Drills](https://medium.com/@Modexa/model-deployments-real-test-rollback-drills-ed121d9526b6),
  [SRE School on rollback](https://sreschool.com/blog/rollback/)). The drill record carries
  `exercised` and `not_exercised` lists and the wall time of every step.
- Post-deployment verification depends on telemetry tagged with deployment metadata and a stated
  observation window with stop conditions ([SRE game days](https://adhdecode.com/articles/sre/sre-game-days/)).
  The observation plan names its metrics, baseline window, and window length at proposal time,
  and the baseline is frozen at exposure.

## Spike Results

Two spikes ran as code reads against `main` and lane 4's head; both resolved without a prototype.

### spike-1: Does lane 4's manifest carry enough to drill a rollback?
- **Assumption**: "The candidate manifest names the candidate code ref and base revision, so a
  release can be drilled from the experiment alone."
- **Method**: code-read (`tools/improvement_eval/runner.py:197-215`,
  `tests/unit/test_improvement_eval_runner.py:119`, `tools/improvement_eval/corpus.py:112-193`)
- **Finding**: The manifest is `{"protocol_ref", "base_revision"}`; `base_revision` is present,
  a candidate code ref is not. The corpus export records `git_sha` of the checkout, which is the
  incumbent's SHA, not the candidate's.
- **Confidence**: high
- **Impact on plan**: `propose()` takes `candidate_ref` explicitly, requires `base_revision` from the
  manifest or the argument (and refuses when both are present and disagree), and refuses with
  `MANIFEST_LACKS_BASE_REVISION` rather than guessing. Lane 5, which constructs manifests, is asked
  through the issue to add `candidate_ref`; nothing here depends on it doing so.

### spike-2: Can the two promotion preconditions be checked, or only declared?
- **Assumption**: "Both preconditions are events outside the repo, so the gate can only be a
  constant."
- **Method**: code-read (`docs/improvement-charter.md`, `models/improvement_charter.py:137-139,234`,
  `docs/features/improvement-controller.md:46-56`)
- **Finding**: Precondition 2 (a human-amended charter naming reversible surfaces) is checkable:
  the pinned charter's `text` is a `ContentField`, and a section heading matching
  `reversible surfaces` is absent from v2. Precondition 1 (credential separation from candidate
  execution) has no attestation record anywhere and is a property of the execution environment,
  which no module here can observe.
- **Confidence**: high
- **Impact on plan**: the gate checks precondition 2 against the pinned charter text and reports
  precondition 1 as unmet by construction, with the docstring naming the event that would change
  it and the fact that no setting, env key, or file flag reads into it. A test flips precondition
  2 with a synthetic charter and asserts the gate still refuses on precondition 1.

## Data Flow

Two flows, one per claim level. Both start from records lane 4 writes and end in records this lane
writes; nothing here mutates an evaluation or an experiment after the fact.

**Release flow (claim level 2)**

1. **Entry point**: `ImprovementEvaluation` row with `state="complete"`, `verdict="accept"`, written by
   `tools/improvement_eval/runner.py::evaluate`. Its `experiment_id` names an `ImprovementExperiment`
   whose `manifest` (verifying store) carries `base_revision` and whose `candidate_surfaces` names
   what changed.
2. **`lifecycle.propose`**: reads the evaluation and experiment, re-hashes the manifest on load (the
   verifying store does this), checks the evaluation's `charter_digest` against
   `ImprovementCharter.pinned(project_key).digest`, runs `denylist.denied_surfaces` over the
   surfaces, validates the rollback plan and observation plan shapes, and writes one
   `ImprovementRelease(state="proposed")` with `kind`, `surfaces`, `candidate_ref`, `base_revision`,
   `exposure` (plan), `rollback_plan`, `observation` (plan), `charter_digest`. Every refusal raises
   `ReleaseRefused(code)`; nothing is written on refusal.
3. **`drill.run`**: creates a temporary git worktree at `candidate_ref` under the artifact retention
   root (never inside the checkout), executes the rollback plan's steps (`git revert --no-commit
   base_revision..candidate_ref`), asserts `git diff --quiet base_revision -- <surface>` for every
   declared surface and for the whole tree, runs the plan's `verify` commands with a timeout, removes
   the worktree in `finally`, and writes `rollback_drill` (JSON summary) plus `drill_log`
   (ContentField, full transcript) on the release. A drill never changes state; it stamps
   `rollback_drill.result`.
4. **`lifecycle.approve`**: requires `rollback_drill.result == "pass"` stamped after the last
   `rollback_plan` write, requires `approved_by` to be a non-empty name outside the agent-identity
   set, consults `promotion.promotion_gate` and records its answer on the release as
   `promotion_gate`, stamps `approved_at` and a provisional `observation_window_ends_at =
   approved_at + observation.window_days` (restamped from `exposed_at` at step 6), transitions to
   `approved`.
5. **`lifecycle.open_pr`**: assembles the PR body (verdict, effect, CI, correction, contract digest,
   charter digest, rollback plan, drill record, comparison if any) and runs `gh pr create` from
   `candidate_ref` through an injected runner; records `exposure.pr_number`. Merging is the
   pipeline's and a human's; this lane never calls `gh pr merge`.
6. **`lifecycle.expose`**: resolves the PR's merge commit through `gh pr view --json mergeCommit,state`
   (injected runner), refuses when not merged, computes the **baseline** from
   `observation.metrics` over the `baseline_window_days` before now using `observation.measure`,
   writes `exposure.merge_sha`, `exposed_at`, `outcome.baseline`, **restamps
   `observation_window_ends_at = exposed_at + window_days`** (the window is measured from
   exposure, so its end is anchored to exposure; the approve-time value is provisional) and appends
   `{"event": "window_restamped", "from": <approve-time value>, "to": <new value>}` to
   `outcome.history`, transitions to `observing`.
7. **`lifecycle.close_window`**: refuses before `observation_window_ends_at` unless `--force` with a
   recorded reason; refuses with `verdict="undetermined", reason="EVIDENCE_EXPIRED"` when
   `now - exposed_at > EVIDENCE_TTL_DAYS` (the evidence rows the window needs have expired, so a
   count would be a gap reported as a number); measures the same metrics over
   `[exposed_at, observation_window_ends_at]`; computes `window_shortfall_days = max(0,
   window_days - (closed_at - exposed_at).days)`, zero on an on-time close and positive only under
   `--force`; writes `outcome.window`, `outcome.deltas` (raw counts and denominators beside every
   rate), `outcome.detection_declined`, `outcome.verdict` in `held | regressed | undetermined`
   (with `outcome.reason` naming `EVIDENCE_TRUNCATED`, `EVIDENCE_EXPIRED`, `DETECTION_DECLINED`, or
   `ZERO_DENOMINATOR` on `undetermined`), `outcome.claim_level_2_supported`, `outcome.falsifier`;
   transitions to `accepted` on `held`, stays `observing` otherwise with `outcome.rollback_recommended`.
8. **`lifecycle.rollback`**: from `observing` or `accepted`, executes the rollback plan for real in a
   temporary worktree of `main` (`git revert -m 1 <merge_sha>` or plain revert, commit message
   carrying `Refs #<issue>` for the hotfix guard), pushes through the injected runner
   (`git push origin HEAD:main`, or `HEAD:<name>` under `--branch <name>`), and **transitions to
   `rolled_back` only when the push returned 0 and `git ls-remote origin main` resolves to
   `revert_sha`**. A refused push (branch protection, `.githooks/pre-push`, a moved head) appends
   `{"event": "rollback_push_refused", "stderr": ..., "revert_sha": ...}` to `outcome.history`,
   leaves the state unchanged, and raises `ReleaseRefused("ROLLBACK_PUSH_REFUSED")`; the revert
   commit is reported so the operator can push it by hand or re-run with `--branch`. On success it
   records `outcome.rollback` with the revert SHA, the verification result, and `propagation:
   "requires /update on fleet machines"`. Rollback is the one deliberately pipeline-exempt path in
   this lane: it is an incident surface, and a revert that waits on critique and review is a
   rollback that arrives after the damage. `--branch` is the pipeline-shaped alternative for a
   rollback that is not urgent; the CLI prints the `gh pr create` command for it.
9. **Output**: `get_release_lineage` joins release → evaluation → experiment → case for the
   dashboard partial and `show`; `report.claim_report` reads accepted releases with
   `claim_level_2_supported` for level 2.

**Comparison flow (claim level 3)**

1. **Entry point**: two `ResearchProcessSpec` values (or two digests already written on
   `ImprovementModelRevision.research_process_digest` by lane 5), a candidate opportunity list, and
   one budget cap.
2. **`freshness.fresh_opportunities`**: for each candidate `ImprovementCase` id, fresh means state in
   `OPEN_CASE_STATES`, no `ImprovementExperiment.case_id` or `ImprovementInvestigation.case_id`
   references it, and no prior comparison's manifest lists it. Returns `(fresh, excluded_with_reasons)`.
3. **`compare.freeze`**: builds the protocol (`arms: {a, b}` digests, `opportunity_ids`,
   `opportunity_set_digest`, `budget_cap` per arm in unit-1 USD, unit-3 USD, subscription turns,
   and wall seconds, `primary_endpoint: validated_gain`, `minimum_worthwhile_effect`, `stopping_rule:
   finite batch`, `evaluator_version`), freezes it through lane 4's `freeze_protocol`, writes an
   `ImprovementExperiment(state="frozen", candidate_surfaces=["research_process"])` with the manifest
   citing `protocol_ref`, and returns the contract digest.
4. **`compare.run`**: loads the experiment, verifies the contract digest, resolves the `ArmRunner`
   (from `--arm-runner module:attr` by lazy import, else the registry), randomizes arm order
   (`arm_assignment_digest`), calls `ArmRunner.run` once per arm with the same opportunity ids and
   the same cap, receives per-opportunity validated gains and a `BudgetUse`, reads accounted spend
   through `BudgetReader` (unit 3 from `InfrastructureReservation` rows whose `resource` name
   carries the `arm:<arm_run_id>:` prefix; unit 1 from the seam, `None` until lane 3), and checks
   `budgets_comparable`.
5. **Statistics**: paired deltas per opportunity (`gain_b - gain_a`, with a rejected or inconclusive
   arm result scored 0), `clustered_bootstrap_ci` clustered by `priority_area`, `evaluate_family` for
   the (single) primary endpoint, verdict `accept` when the lower bound clears the minimum
   worthwhile effect, `reject` when the upper bound is below zero, `inconclusive` otherwise or on
   any budget refusal.
6. **Output**: one `ImprovementEvaluation(evaluator_version="recursive-comparison/1")` with
   `effect`, `confidence_interval`, `correction="holm"`, `notes` carrying the budget accounting per
   arm and the refusal reason if any. On `accept`, one `ImprovementModelRevision` with
   `research_process_digest` of the winning arm, `prediction`, `supersedes_id` of the current
   revision, and the previous revision moved to `superseded`.

## Architectural Impact

- **New dependencies**: none external. Two new packages, `tools/improvement_release/` and
  `tools/improvement_recursion/`, import `models.*`, `tools/improvement_eval/{runner,statistics}`
  (public functions only), `tools/infrastructure_budget`, and the standard library. `git` and `gh`
  are invoked through an injectable runner, the shape `tools/improvement_resources.py` already
  uses.
- **Interface changes**: `ImprovementRelease` gains eight fields and one state, all additive.
  `ui/data/improvement.py` gains one getter; the pinned list grows to five. `pyproject.toml` gains
  one script. Nothing in `tools/improvement_eval/` changes.
- **Coupling**: the release lifecycle depends on lane 4's evaluation shape (`verdict`, `effect`,
  `charter_digest`) and on the experiment manifest carrying `base_revision`. The comparison depends
  on lane 4's `freeze_protocol` and statistics. Both dependencies are on merged or nearly merged
  code. The two seams left open for lanes 3 and 5 (`BudgetReader.unit1`, `ArmRunner`) are
  protocols with a refusing default, so absence is a named condition rather than an import error.
- **Data ownership**: this lane is the sole writer of `ImprovementRelease` and of
  `ImprovementModelRevision` rows produced by a comparison. Lane 5 remains the writer of planner
  revisions; both write `research_process_digest` through the same canonical function.
- **Reversibility**: additive fields on an immortal record cannot be removed without a subtractive
  migration (lane 4's Risk 6 applies verbatim). The packages, the script entry, the partial, and
  the getter are all deletable. `accepted` in `RELEASE_STATES` is reversible only while no row
  holds it.

## Appetite

**Size:** Large

**Team:** Solo dev (lead orchestrates builders and validators), PM check-in at critique, code reviewer at PR.

**Interactions:**
- PM check-ins: 1-2 (critique round; the lane 5 seam confirmation)
- Review rounds: 2+ (PR review with mutation proofs for each guard)

Large because the lane carries two independent deliverables (the release lifecycle with its drill,
and the comparison with its budget accounting) plus a dashboard surface and a schema change, and
because the tests that matter are integration tests against a real git repository and a real Redis.
The parts are separable: the release lifecycle can ship and be drilled with no comparison, and the
comparison can ship with the replay runner and no release. The build order below puts the release
path first because it is what a human can exercise the day it lands.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane 4 merged or checked out as head | `git merge-base --is-ancestor $(git rev-parse origin/session/sdlc-3216) HEAD && echo ok` | `tools/improvement_eval/runner.py` and the `accept` verdict exist on the build head |
| `git` and `gh` on PATH | `git --version && gh --version` | drill worktrees and PR resolution |
| `gh` authenticated | `gh auth status` | `open_pr` and `expose` resolve PRs |
| Artifact retention root writable | `.venv/bin/python -c "from models.verifying_artifact_store import _default_base_path; import os; p=_default_base_path(); os.makedirs(p, exist_ok=True); assert os.access(p, os.W_OK); print(p)"` | drill worktrees and drill logs live there |
| Test Redis claimable | `.venv/bin/python -c "from tests.db_claim import redis_test_url; print(redis_test_url())"` | every lifecycle test runs against a claimed test db |
| Charter pinned | `.venv/bin/python -c "from models.improvement_charter import ImprovementCharter; ImprovementCharter.load_from_file(); assert ImprovementCharter.pinned(project_key='valor'); print('pinned')"` | the proposal gate compares digests against the pinned row |

Run all checks via `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-6-promotion-rollback-and-recursion.md`.

## Solution

### Key Elements

- **Release lifecycle** (`tools/improvement_release/lifecycle.py`): six states, every transition a
  function that checks its preconditions, records a reason, and refuses loudly. Proposal writes
  the rollback and observation plans before anything else can happen.
- **Rollback drill** (`tools/improvement_release/drill.py`): executes the rollback plan against the
  proposed release in a throwaway worktree, verifies restoration on every declared surface, and
  writes a record that names what it exercised and what it did not.
- **Promotion gate** (`tools/improvement_release/promotion.py`): the one function that would enable
  automated promotion; it refuses and names both preconditions. No setting, env key, or file flag
  feeds it.
- **Candidate-surface denylist** (`tools/improvement_release/denylist.py`): the charter file, the
  charter model, identity, release policy, the vault, and the git hooks; a proposal naming any is
  refused before a row exists, and lane 5's constructor can call the same function.
- **Observation measurement** (`tools/improvement_release/observation.py`): production metrics from
  `ImprovementEvidence` with denominators, a frozen baseline at exposure, and an explicit
  detection-declined signal.
- **Research process digest** (`tools/improvement_recursion/process.py`): a canonical, sorted-key
  `sha256:<hex>` of a `ResearchProcessSpec`, the one function both lane 5 and the comparison use.
- **Freshness** (`tools/improvement_recursion/freshness.py`): an opportunity is fresh when no
  record shows it was worked; decided by lookup, hashed into the contract.
- **Budget accounting** (`tools/improvement_recursion/budget.py`): one cap per arm in four units,
  accounted spend per arm from records, unknown never read as zero.
- **Comparison** (`tools/improvement_recursion/compare.py`): a frozen contract, two arms behind an
  `ArmRunner` protocol, paired statistics from lane 4, one evaluation row, one revision on accept.
- **Claim report** (`tools/improvement_recursion/report.py`): three ladder levels, each with
  supported-or-not, interval, correction, falsifier, and why-not.
- **Release lineage on the dashboard** (`ui/data/improvement.py::get_release_lineage`, partial,
  template): what was released, from which verdict, drilled or not, window open or closed, outcome.

### Flow

**Operator path (level 2)**

`accept` verdict exists → `valor-improve-release propose --evaluation E --kind core_workflow
--candidate-ref session/x --surfaces a.py b.py --rollback-plan plan.json --observation obs.json`
→ release `proposed` → `drill --release R` → `rollback_drill.result=pass` → `approve --release R
--approved-by "Tom Counsell"` → `approved`, window end stamped → `open-pr --release R` → PR through
the pipeline, merged by its normal gates → `expose --release R` → `observing`, baseline frozen →
window elapses → `close-window --release R` → `accepted` (or `rollback --release R --reason ...`
→ `rolled_back`) → dashboard lineage and `report` show level 2 supported or not.

**Comparison path (level 3)**

Two process specs → `compare fresh --candidates c1 c2 c3` → fresh set → `compare freeze --arm-a
a.json --arm-b b.json --opportunities c1 c3 --budget budget.json` → experiment `frozen` → lane 5
registers an `ArmRunner` → `compare run --experiment X` → evaluation `recursive-comparison/1` with
verdict → on accept, new model revision → `report` shows level 3 supported or not, with the budget
accounting per arm.

### Technical Approach

**Model changes (`models/improvement_release.py`).** `RELEASE_STATES` becomes
`("proposed", "approved", "observing", "accepted", "rolled_back", "withdrawn")`; six values, under
the schema-gate cap of 8 (`tests/unit/test_improvement_models.py:99`). New plain fields: `kind`
(vocabulary `RELEASE_KINDS = ("core_workflow", "evaluator", "infrastructure")`, validated in code,
deliberately not indexed since three values on an immortal table with a `state` index already is
not worth a second index set), `candidate_ref`, `base_revision`, `exposed_at` (`DatetimeField`),
`observation` (JSON plan), `rollback_drill` (JSON summary), `promotion_gate` (JSON, the gate's
answer at approval). One `ContentField(store=verifying_artifact_store)`: `drill_log`, the full
drill transcript, re-hashed on load like `manifest` and `judge_records`. The docstring keeps its
sentence that promotion is disabled and this record does not enable it, and adds the transition
table. Migration: `confirm_improvement_release_lane6_fields`, additive, precedent
`_migrate_confirm_improvement_v2_fields`.

**Transitions.** One private `_transition(release, *, to, allowed_from, event)` writes
`state`, appends `{event, at, detail}` to a bounded `history` list inside `outcome` (the record's
existing free JSON field; no new index), and `save()`s. Every public function checks its
preconditions first and raises `ReleaseRefused(code, detail)` from a closed vocabulary of codes
(`EVALUATION_NOT_ACCEPT`, `CHARTER_DRIFT`, `SURFACE_DENIED`, `MANIFEST_LACKS_BASE_REVISION`,
`CANDIDATE_REF_CONFLICT`, `DRILL_REQUIRED`, `DRILL_STALE`, `APPROVER_NOT_HUMAN`, `PR_NOT_MERGED`,
`WINDOW_OPEN`, `WINDOW_EXCEEDS_EVIDENCE_TTL`, `WRONG_STATE`, `EVALUATOR_RELEASE_NEEDS_CALIBRATION`,
`ROLLBACK_PUSH_REFUSED`). Refusals never write state; the one exception is `ROLLBACK_PUSH_REFUSED`,
which appends a history event naming the orphaned revert SHA before raising, because a revert
commit that exists and never reached `main` is exactly the fact the record must carry.

| From | Event | To | Guard |
|---|---|---|---|
| (none) | `propose` | `proposed` | accept verdict, pinned charter digest, surfaces allowed, plans valid |
| `proposed` | `drill` | `proposed` | writes `rollback_drill`; no state change |
| `proposed` | `approve` | `approved` | drill passed after last plan write; human approver; gate recorded |
| `approved` | `open_pr` | `approved` | records `exposure.pr_number` |
| `approved` | `expose` | `observing` | PR merged; baseline frozen; `exposed_at`; window end restamped from `exposed_at` |
| `observing` | `close_window` | `accepted` on `held`, else stays | window elapsed or forced with reason; evidence inside TTL and untruncated |
| `observing`, `accepted` | `rollback` | `rolled_back` | revert executed, verified, pushed, and confirmed on the remote by `ls-remote` |
| `proposed`, `approved` | `withdraw` | `withdrawn` | reason recorded |

**Proposal gates, in order.** (1) Evaluation exists, `state == "complete"`, `verdict == "accept"`.
(2) `evaluation.charter_digest == ImprovementCharter.pinned(project_key).digest`; a mismatch is
`CHARTER_DRIFT`, charter §12's "reassess pending actions against the new authority before further
effects". (3) `kind` in `RELEASE_KINDS`; `evaluator` additionally requires `calibration_ref`
(an artifact reference on the verifying store) and a non-empty `argument` (the §6 written argument
that the replacement better assesses progress), refused as `EVALUATOR_RELEASE_NEEDS_CALIBRATION`
otherwise; `infrastructure` needs no comparison per the parent plan but still needs a drill.
(4) `surfaces` non-empty, each a normalized repo-relative path with no `..` or glob, none denied.
(5) `base_revision` from the manifest, or from the argument when the manifest lacks it, refused as
`CANDIDATE_REF_CONFLICT` when both exist and differ and as `MANIFEST_LACKS_BASE_REVISION` when
neither exists. (6) `candidate_ref` resolves (`git rev-parse --verify`) through the runner.
(7) Rollback plan validates: `{"kind": "git_revert", "verify": [cmd, ...], "propagation":
"/update"}`; `verify` may be empty, in which case the drill records `verify: not_exercised`.
(8) Observation plan validates: `window_days >= 1`, `baseline_window_days >= 1`,
`baseline_window_days + window_days <= 28` (inside the 30-day evidence TTL, refused as
`WINDOW_EXCEEDS_EVIDENCE_TTL`), `metrics` a non-empty subset of `OBSERVATION_METRICS`.

**The drill (`drill.py`).** `run(release, *, runner=None, root=None)`:
1. Refuses to run inside any git checkout that is not a fresh worktree it created; the worktree is
   created under `<retention root>/drills/<release id>/<timestamp>/` with `git worktree add
   --detach <path> <candidate_ref>` from the repo the CLI runs in.
2. Records `base_revision` and `candidate_ref` SHAs, `git diff --stat base..candidate`.
3. Pre-revert range checks, each a recorded `fail` that stops the drill before any revert runs:
   - `git merge-base --is-ancestor <base_revision> <candidate_ref>` nonzero →
     `reason: BASE_NOT_ANCESTOR`. A candidate that rebased onto or merged newer `main` has a base
     that is no longer in its history, and `<base>..<cand>` would then include unrelated `main`
     commits; the drill refuses to revert what the release did not introduce.
   - `git rev-list --merges <base_revision>..<candidate_ref>` non-empty →
     `reason: MERGE_COMMITS_IN_RANGE`, listing the merge SHAs. `git revert --no-commit` on a
     range aborts on a merge commit ("no -m option"), and reverting each with `-m 1` rehearses a
     different operation from the one the plan declares; the operator re-proposes from a
     linear candidate branch.
   - `changed = git diff --name-only <base_revision> <candidate_ref>`; `undeclared = [p for p in
     changed if not any(p == s or p.startswith(s.rstrip("/") + "/") for s in surfaces)]`;
     non-empty → `reason: UNDECLARED_SURFACE_CHANGED`, `paths: undeclared`. This is the check
     that makes the declared surfaces a claim the drill can falsify: a candidate that touches a
     file the release did not declare fails here, which is the fixture
     `test_drill_fails_when_undeclared_surface_differs` seeds (one extra file in the candidate
     commit, absent from `--surfaces`).
4. Executes `git revert --no-commit <base_revision>..<candidate_ref>`; on a conflict, records the
   conflicting paths and the result is `fail` with `reason: revert_conflict`.
5. Asserts restoration: `git diff --quiet <base_revision> -- <surface>` for each declared surface,
   then `git diff --quiet <base_revision>` for the whole tree; any nonzero exit is `fail` with the
   differing paths listed. After a clean full-range revert of a linear range the tree equals the
   base by construction, so this step is the sequence-completed invariant (it catches a revert
   that stopped partway, a hook that rewrote a file, or a step executor bug), while step 3 is the
   check that carries the declared-surface claim.
6. Runs each `verify` command in the worktree with `timeout=TIMEOUTS.improvement_drill_verify_seconds`
   (a new `TimeoutSettings` field, default 300, catalogued per `docs/features/config-timeout-catalog.md`),
   recording exit code, wall seconds, and the last 2 KB of output.
7. Removes the worktree (`git worktree remove --force`) in `finally`, and `git worktree prune`.
8. Writes `rollback_drill = {drilled_at, base_revision, candidate_ref, steps: [...], restored,
   result, reason?, paths?, exercised: ["range_checks", "revert", "tree_restoration", "verify"?],
   not_exercised: ["fleet_update", "production_traffic", "merge_commit_revert"], seconds}` and
   `drill_log` (full transcript). `not_exercised` is the honest list: a drill proves a range revert
   on the candidate branch restores the tree; it does not prove the fleet picked it up, and it does
   not rehearse the `-m 1` merge-commit revert that `rollback()` runs on `main`. The feature doc
   states that the two operations differ and why the drill still stands as evidence (the same
   step executor, the same restoration checks, the same declared surfaces).
The real `rollback()` reuses the same step executor against a worktree of `main` with
`git revert -m 1 <merge_sha>` when the merge commit has two parents and plain `git revert` when the
PR was squash-merged, commits with `Roll back improvement release <id>: <reason> (Refs #3218)`
so `.githooks/commit-msg` accepts it, and pushes through the runner (`git push origin HEAD:main`,
or `HEAD:<name>` under `--branch <name>`). The transition to `rolled_back` happens only after the
push returns 0 and `git ls-remote origin main` (or the named branch) resolves to `revert_sha`; any
other outcome is `ROLLBACK_PUSH_REFUSED` with the state unchanged and the orphaned `revert_sha`
recorded in `outcome.history`. It records the same shape plus `revert_sha`, `pushed_to`, and
`propagation: "requires /update on fleet machines"`. Under `--branch`, `propagation` also names
the PR the operator must open, and the CLI prints the `gh pr create` command.

**Promotion gate (`promotion.py`).**
```python
PRECONDITION_CREDENTIAL_SEPARATION = "credential_separation"
PRECONDITION_CHARTER_REVERSIBLE_SURFACES = "charter_names_reversible_surfaces"

@dataclass(frozen=True)
class PromotionGate:
    automated: bool          # always False in this build
    unmet: tuple[str, ...]
    detail: dict

def promotion_gate(project_key: str = "valor") -> PromotionGate: ...
def promote_automatically(release_id: str, *, project_key: str = "valor") -> None:
    """Raise PromotionDisabled naming every unmet precondition. Never writes."""
```
Precondition 1 is unmet by construction: there is no attestation record for evaluator-secret and
production-credential separation from candidate execution, and a worktree is not a security
boundary; the docstring names the event (a separate process identity for evaluation, with
candidate execution unable to read evaluator secrets or production credentials) and states that no
setting, env key, or file flag reads into it. Precondition 2 reads the pinned charter's `text` and
looks for a section heading (`^#{1,3}\s+.*reversible surfaces`, case-insensitive); v2 has none, so
it is unmet against the current file, and a test with a synthetic charter proves the check bites
and proves the gate still refuses on precondition 1 alone. `approve()` stores the gate's answer on
the release so the record shows what was true when a human approved it.

**Denylist (`denylist.py`).** `CANDIDATE_SURFACE_DENYLIST` as normalized path prefixes:
`docs/improvement-charter.md`, `models/improvement_charter.py`, `config/identity.json`,
`tools/improvement_release/`, `.env`, `.env.example`, `.githooks/`. `denied_surfaces(surfaces)`
returns the offenders in input order; `refuse_denied(surfaces)` raises `SurfaceDenied`. A path is
normalized with `posixpath.normpath` and refused outright if it escapes the repo or contains a glob
character, because a denylist that can be bypassed by `./docs/../docs/improvement-charter.md` is
not a denylist. Identity and persona files beyond `config/identity.json` are named in the parent
plan's No-Go but live in the private vault (`~/Desktop/Valor/identity.json`), outside the repo;
the drill's worktree cannot reach them, and a surface must be repo-relative.

**Observation (`observation.py`).** `OBSERVATION_METRICS = ("corrections_total",
"corrections_architectural", "coverage_ticks", "architectural_correction_rate")`.
`measure(project_key, start, end) -> dict` reads `ImprovementEvidence.recent(project_key,
limit=READ_LIMIT)` (the same read `ui/data/improvement.py::_rows` performs) filtered to
`[start, end)`, counts by kind and classification, counts coverage rows by `source_ref` prefix
`coverage:`, and computes the rate as `corrections_architectural / coverage_ticks` with `None` when
the denominator is zero. The read is capped and newest-first (`READ_LIMIT = 1000`,
`ui/data/improvement.py:41`; `ImprovementEvidence.recent` at `models/improvement_evidence.py:149`),
so a busy span can drop its oldest rows without any error; `measure` therefore returns
`truncated: True` when `len(rows) == limit and rows[-1].created_at > start`, meaning the oldest
row returned is still inside the requested window and older rows may exist that the read did not
reach. An undercounted baseline would read as `regressed`, so a truncated window is never
scored. `compare_windows(baseline, window, *, window_days, baseline_days)` returns
`verdict="undetermined", reason="EVIDENCE_TRUNCATED"` when either window is truncated; otherwise
it normalizes both to per-day, sets `detection_declined = window.coverage_ticks_per_day <
0.8 * baseline.coverage_ticks_per_day`, and returns `verdict`: `held` when the rate did not rise
past the baseline plus its noise band and detection did not decline, `regressed` when the rate rose
past the band, `undetermined` when either denominator is zero or detection declined. Evidence
rows expire at 30 days (`Meta.ttl = 86400 * 30`, `models/improvement_evidence.py:146`);
`EVIDENCE_TTL_DAYS = 30` in `observation.py` is pinned equal to `ImprovementEvidence.Meta.ttl` by a
test, and `close_window` returns
`undetermined` with `reason="EVIDENCE_EXPIRED"` when `now - exposed_at > EVIDENCE_TTL_DAYS`,
because a late operator-invoked close would otherwise count a window whose early rows are gone
and report the gap as a number. The partial's window row surfaces both reasons. Charter §11's
"do not treat fewer detected bugs as improvement when detection declined" is the `undetermined`
branch, and `claim_level_2_supported` is true only on `held` with `window_shortfall_days == 0` and
the evaluation's held-out effect still positive. The falsifier written to `outcome` is the
observation that would overturn the claim: "architectural correction rate over a later
`window_days` window exceeds the baseline band with coverage at or above baseline".

**Research process digest (`process.py`).**
```python
@dataclass(frozen=True)
class ResearchProcessSpec:
    selection_rule: str
    investigation_budget_split: dict[str, float]   # by INVESTIGATION_KINDS
    revision_cadence_seconds: int
    planner_prompt_digest: str
    skill_digest: str
    extra: dict = field(default_factory=dict)

def research_process_digest(spec: ResearchProcessSpec) -> str: ...  # "sha256:<hex>" of canonical JSON
```
Canonical JSON is `json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))`, so key order
never changes the digest. Validation: `unknown = set(split) - set(INVESTIGATION_KINDS)` raises
`ValueError`; the sum check `0.99 <= sum(split.values()) <= 1.01` applies **only to a non-empty
split**. Lane 5's plan (`docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md:541-544`)
writes every revision with `investigation_budget_split={}`, and an empty split is a legitimate
"no split declared", so the canonical function must digest it rather than refuse it. Two tests pin
this: `ResearchProcessSpec(..., investigation_budget_split={})` digests to a `sha256:` string, and
lane 5's fixture values (`selection_rule="ordinal-lexicographic-v1"`, `extra={"ranking_module_digest":
...}`) digest identically through `research_process_digest` and lane 5's
`tools/improvement_ranking.py::process_digest` once both exist (the second test is written against
lane 5's function by import and skipped with a named reason while that module is absent, so it
bites the day lane 5 lands). This is the one function lane 5 is asked to call when it writes
`ImprovementModelRevision.research_process_digest`; the comparison accepts any `sha256:` string
from a revision row, so a lane 5 that computes its own digest is still comparable as long as the
two arms differ.

**What this lane assumes from lane 5, and what it does without it.** Lane 5 (#3217) is open with
no commits. The comparison needs three things a running loop provides, and each is a seam:
- *A research process to run.* `ArmRunner` is a `Protocol` with one method,
  `run(process_digest, opportunity_ids, budget_cap, arm_run_id) -> ArmResult`. `ReplayArmRunner`
  (in `arms.py`, exercised by tests) returns gains and budget use from a fixture. `compare run`
  resolves its runner in two ways. `--arm-runner <module>:<attr>` (for example
  `tools.improvement_plan_arm:PlannerArmRunner`) is resolved with `importlib.import_module(mod)`
  then `getattr(mod, attr)()`; an `ImportError` or `AttributeError` becomes
  `ArmRunnerAbsent("ARM_RUNNER_ABSENT", detail=str(exc))` and the CLI exits 2. When the flag is
  omitted, `get_arm_runner()` returns the runner registered in-process by `register_arm_runner(...)`
  and raises `ArmRunnerAbsent` otherwise. The flag exists because lane 5 registers
  `PlannerArmRunner` from the research CLI's entry (`valor-improve`, lane 5 plan `:554-562`), a
  process `valor-improve-release compare run` never runs in; without the flag the production
  comparison would be `ARM_RUNNER_ABSENT` forever and the verification row would pass for the
  wrong reason. The import is lazy, at the moment `compare run` executes, so the incident binary
  imports nothing from the research CLI at module load and the coupling argument in Agent
  Integration holds. The registry path stays so a `compare` mounted under lane 3's `valor-improve`
  works without the flag. Lane 5's planner tick, once it exists, is named by the flag or registers
  itself; this plan asks for that in the issue comment and builds nothing that waits on it.
- *A process digest on revisions.* Written by lane 5 through `research_process_digest`. The
  comparison reads `ImprovementModelRevision.query.filter(project_key=..., state="current")` to
  name the incumbent process when `--arm-a` is omitted, and refuses with `INCUMBENT_PROCESS_UNKNOWN`
  when no current revision carries a digest.
- *Opportunities.* Fresh cases are lane 2's `ImprovementCase` rows and need nothing from lane 5;
  the freshness check reads experiments and investigations that lane 4 and lane 5 write.
And from lane 3 (#3215): unit-1 paid-inference metering. `BudgetReader.unit1_usd(arm_run_id)`
returns `None` until a meter exists; `budgets_comparable` treats `None` as unknown, and unknown
refuses a level-3 claim with `BUDGET_UNKNOWN:unit1` in the evaluation's notes. This is charter §8
applied: uncertain or missing metering is not zero cost.

**Budget (`budget.py`).** `BudgetCap` and `BudgetUse` carry `unit1_usd`, `unit3_usd`,
`subscription_turns`, `wall_seconds`, each `float | int | None`. Unit 3 is tagged through the
resource name, the one field an admitting caller controls: lane 7's `admit()`
(`tools/infrastructure_budget.py:260`) takes `resource`, `project_key`, `settings`, `now` and writes
`reason="admitted"` on every admitted row (`:357`), so `reason` cannot carry an arm run id, but the
row's `resource` is `resource.name` (`:352`). An arm runner admits every reservation it makes with
`ResourceDecl(name=f"arm:{arm_run_id}:{resource_name}", ...)`; `ReplayArmRunner` does the same
against the test ledger so the reader is exercised end to end. `LedgerBudgetReader.unit3_usd(arm_run_id)`
filters `InfrastructureReservation.query.filter(project_key=...)` by
`row.resource.startswith(f"arm:{arm_run_id}:")`, sums `settled_usd if settled_usd is not None else
amount_usd` over rows with `state in ("reserved", "settled")`, and **returns `None` when zero rows
match**, so an arm that admitted nothing through the ledger is `BUDGET_UNKNOWN:unit3` rather than a
free arm. `unit1_usd` returns `None`; `subscription_turns` and `wall_seconds` come from the
`ArmResult`. `budgets_comparable(a, b, cap, tolerance=0.10)` returns `(ok, reasons)`: `False` with
`BUDGET_UNKNOWN:<unit>` for any `None` on either side, `False` with `BUDGET_EXCEEDED:<arm>:<unit>`
when use exceeds the cap, `False` with `BUDGET_MISMATCH:<unit>` when the two arms' use differs by
more than the tolerance of the cap. A unit whose cap is explicitly `0` with `0` use on both sides
is `ok` for that unit (a unit not budgeted is not a mismatch); a `None` cap is never `ok`. The
evaluation's `notes` always carry both arms' use in every unit, so the budget accounting is shown
whether or not the verdict is a claim.

**Comparison (`compare.py`).** `freeze(...)` writes the protocol through lane 4's
`freeze_protocol` and the experiment through the same fields lane 4's `compute_contract_digest`
hashes (`hypothesis`, `mechanism`, `falsifier`, `candidate_surfaces=["research_process"]`,
`manifest`), so the contract digest is computed by lane 4's function and verified by it on load.
`run(experiment_id, *, runner=None, budget_reader=None, rng_seed=None)` verifies `state ==
"frozen"` and the digest, refuses when the two digests are equal (`ARMS_IDENTICAL`), randomizes
arm order with a seeded RNG and records `arm_assignment_digest`, runs both arms, builds paired
deltas, clusters by the opportunity's `priority_area`, calls `clustered_bootstrap_ci` and
`evaluate_family` from `tools/improvement_eval/statistics.py`, decides the verdict, and writes one
`ImprovementEvaluation` with `experiment_id`, `contract_digest`, `charter_digest` (pinned),
`evaluator_version="recursive-comparison/1"`, `blinded=False` (the arms are processes, not judged
artifacts; the field is honest rather than decorative), `trials=len(opportunities)`, `effect`,
`confidence_interval`, `correction="holm"`, `notes`. Any exception from an arm is
`infra_failure`, never a result. On `accept`, `_write_revision` creates the new
`ImprovementModelRevision` and supersedes the current one in that order, so a crash between the
two leaves two `current` rows (detectable) rather than none.

**Claim report (`report.py`).** `claim_report(project_key) -> dict` with `levels[1..3]`, each
`{name, supported: bool, evidence: [...], confidence_interval, correction, falsifier, why_not}`.
Level 1 reads lane 5's cycle when it exists (an experiment with a complete evaluation whose case
moved state) and otherwise says "no complete cycle recorded". Level 2 reads accepted releases with
`outcome.claim_level_2_supported`. Level 3 reads `recursive-comparison/` evaluations with verdict
`accept` and comparable budgets. Each level degrades independently on a read failure, the shape
lane 7's `generate_report` established. `render(report) -> str` prints it; the dashboard partial
shows the same three rows. No count of experiments, patches, or releases appears anywhere in the
report or the partial; a Verification row asserts the absence.

**Dashboard.** `get_release_lineage(project_key="valor") -> dict` returns `releases` (each with
`id`, `state`, `kind`, `surfaces`, `candidate_ref`, `evaluation: {verdict, effect,
confidence_interval}`, `experiment: {hypothesis, contract_digest}`, `case: {title, priority_area}`,
`drill: {result, drilled_at}`, `window: {exposed_at, ends_at, days_remaining}`, `outcome:
{verdict, claim_level_2_supported}`), `promotion_gate: {automated, unmet}`, `unavailable`,
`no_releases_yet`. The partial `ui/templates/improvement/releases.html` renders the lineage as one
row per release and the gate as a sentence ("Automated promotion: disabled; unmet: ..."). The
index page links it beside the other three. The exact pinned list in
`test_dashboard_never_offers_experiment_or_patch_counts` grows to five.

**CLI (`cli.py`).** `argparse` with subparsers; every subcommand takes `--project-key` (default
`valor`), prints one JSON object on stdout, exits 0 on success, 2 on a `ReleaseRefused` or
`ArmRunnerAbsent` (the code and detail printed), 1 on an unexpected error with the traceback
logged. `drill`, `open-pr`, `expose`, and `rollback` accept `--runner-log <path>` so a test can
inspect every subprocess call. `close-window --due` lists observing releases whose window has
ended, for a future tick to call.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `lifecycle.py` has no `except Exception: pass`. Every guard raises `ReleaseRefused(code)`; `tests/unit/test_improvement_release_lifecycle.py` has one test per code in the closed vocabulary, each asserting the code, that no row was written (proposal) or no state changed (later transitions), and that the refusal is in the CLI's exit-2 JSON.
- [ ] `drill.py` wraps subprocess execution in `try/finally` for worktree removal only; a failing step is a recorded `fail`, not an exception. Tests: a revert conflict (seeded by a base-revision commit touching the same lines) yields `result=fail, reason=revert_conflict` with the conflicting path listed; a `verify` timeout yields `fail` with `timeout` recorded; a candidate whose base is not an ancestor (base branch advanced by one commit after the candidate branched, candidate not rebased) yields `fail, reason=BASE_NOT_ANCESTOR`; a candidate range containing a merge commit yields `fail, reason=MERGE_COMMITS_IN_RANGE` with the merge SHA listed; a candidate commit touching a file absent from `--surfaces` yields `fail, reason=UNDECLARED_SURFACE_CHANGED, paths=[that file]`; in every case the worktree directory no longer exists afterwards and no revert ran on a pre-check failure (`steps` carries no `revert` entry).
- [ ] `lifecycle.rollback` with a runner whose `git push` returns nonzero: state unchanged, `outcome.history` carries `rollback_push_refused` with the revert SHA, `ReleaseRefused("ROLLBACK_PUSH_REFUSED")` raised, CLI exit 2. With a runner whose push returns 0 but whose `ls-remote` answers a different SHA (a head that moved under the push): same refusal, so the transition never rests on the push's exit code alone.
- [ ] `compare.py` catches any exception from an `ArmRunner` and writes `infra_failure`, mirroring lane 4's runner; test asserts the evaluation row exists with that verdict and `notes` naming the exception type.
- [ ] `observation.py` and `lineage.py` catch read failures and mark the result `unavailable` (the dashboard pattern from `get_goals`); tests monkeypatch `ImprovementEvidence.recent` to raise and assert `unavailable=True` with a `logger.warning` captured, and that `get_release_lineage` still returns the promotion gate.
- [ ] `report.py`: each level degrades independently; test raises inside the level-2 read and asserts levels 1 and 3 are still populated and level 2 says "could not be determined".

### Empty/Invalid Input Handling
- [ ] `propose` with empty `surfaces`, a surface containing `..` or `*`, an empty `candidate_ref`, an observation plan with `window_days=0` or with `baseline_window_days + window_days > 28`, a rollback plan with an unknown `kind`: each refused with its code, no row written.
- [ ] `approve` with `approved_by` empty, whitespace, or in `AGENT_IDENTITIES` (`valor`, `valor-engels`, `agent`, `system`, `claude`): refused `APPROVER_NOT_HUMAN`.
- [ ] `research_process_digest` with an `investigation_budget_split` naming a kind outside `INVESTIGATION_KINDS`, or a non-empty split summing outside `[0.99, 1.01]`: `ValueError`, tested. An empty split (`{}`, lane 5's every revision) digests to a `sha256:` string, tested.
- [ ] `observation.measure` on a window whose read hits `READ_LIMIT` with the oldest returned row still inside the window: `truncated=True`; `compare_windows` on a truncated window: `undetermined`, `reason=EVIDENCE_TRUNCATED`, never scored. `close_window` when `now - exposed_at` exceeds `EVIDENCE_TTL_DAYS`: `undetermined`, `reason=EVIDENCE_EXPIRED`, and a test pins `EVIDENCE_TTL_DAYS == ImprovementEvidence`'s TTL in days so the two cannot drift apart silently.
- [ ] `LedgerBudgetReader.unit3_usd` with zero `InfrastructureReservation` rows carrying the `arm:<arm_run_id>:` resource prefix: `None`, so the verdict carries `BUDGET_UNKNOWN:unit3`; with rows carrying a different arm's prefix only: still `None` (the filter is by prefix, never by project alone).
- [ ] `compare run --arm-runner nosuch.module:Runner` and `--arm-runner tools.improvement_recursion.arms:NoSuchAttr`: `ARM_RUNNER_ABSENT` with the import or attribute error in `detail`, exit 2; `--arm-runner tools.improvement_recursion.arms:ReplayArmRunner` resolves and runs.
- [ ] `fresh_opportunities` with an empty candidate list returns `([], [])`; with an unknown case id returns it in `excluded` with reason `NOT_FOUND`.
- [ ] `budgets_comparable` with both arms `None` in a unit: `BUDGET_UNKNOWN`, never `ok`; with a zero cap in a unit and zero use: `ok` for that unit (a unit not budgeted is not a mismatch).
- [ ] `compare run` on an experiment with zero opportunities: refused `NO_OPPORTUNITIES` at freeze, so run never sees it; test asserts freeze refuses.
- [ ] `close_window` on a release with zero coverage ticks in either window: `verdict=undetermined`, `claim_level_2_supported=False`, rate `None`, and the partial renders "no denominator" rather than 0%.

### Error State Rendering
- [ ] `/_partials/improvement/releases/` with a raising lineage read renders "release lineage unavailable" and still renders the promotion-gate sentence (`tests/unit/test_ui_app.py`, seeded by monkeypatching the getter).
- [ ] With no releases, the partial renders "no release proposed yet; the first arrives from an accepted evaluation" and no table.
- [ ] A release in `observing` with `outcome.verdict=regressed` renders "window closed: regressed, rollback recommended", never "accepted".
- [ ] The CLI prints refusal codes on stdout as JSON and exits 2; `tests/integration/test_improvement_release_end_to_end.py` asserts the exit code and parses the JSON for at least `EVALUATION_NOT_ACCEPT` and `DRILL_REQUIRED`.

### Mutation proofs (each guard, measured)
Every guard below is mutated once during the build and the test that catches it is named in the PR body, per `feedback_mutation_check_each_guard_every_round`:
- charter-digest gate (`propose`): skip the comparison → `test_propose_refuses_charter_drift` red
- denylist (`propose`): return `[]` from `denied_surfaces` → `test_propose_refuses_charter_surface` red
- drill-required (`approve`): drop the check → `test_approve_refuses_without_drill` red
- human-approver (`approve`): drop the identity set → `test_approve_refuses_agent_identity` red
- gate precondition 1: return `automated=True` when precondition 2 is met → `test_gate_refuses_on_credential_separation_alone` red
- undeclared-surface pre-check (`drill`): return `[]` for `undeclared` → `test_drill_fails_when_undeclared_surface_differs` red
- ancestry pre-check (`drill`): skip `merge-base --is-ancestor` → `test_drill_fails_when_base_not_ancestor` red
- tree-restoration (`drill`): skip the whole-tree diff → `test_drill_fails_when_revert_leaves_residue` red (fixture: a `verify` command that writes a file before the restoration check runs, so the post-revert tree differs from base on an undeclared path)
- rollback push gate (`rollback`): transition on `returncode == 0` without the `ls-remote` comparison → `test_rollback_refuses_when_remote_head_differs` red
- evidence-truncated (`observation`): drop the `truncated` flag → `test_compare_windows_undetermined_when_truncated` red
- evidence-expired (`close_window`): drop the TTL check → `test_close_window_undetermined_when_evidence_expired` red
- window-restamp (`expose`): keep the approve-time `observation_window_ends_at` → `test_expose_restamps_window_end_from_exposed_at` red
- budget-unknown (`compare`): treat `None` as 0 → `test_compare_refuses_claim_on_unknown_unit1` red
- unit-3 zero-rows (`budget`): return `0.0` from `unit3_usd` on no matched rows → `test_unit3_unknown_when_no_arm_rows` red
- freshness (`freeze`): skip the experiment lookup → `test_freeze_refuses_worked_opportunity` red
- arms-identical (`run`): drop the check → `test_run_refuses_identical_arms` red
- detection-declined (`close_window`): drop the coverage comparison → `test_close_window_undetermined_when_detection_declines` red

## Test Impact

- [ ] `tests/unit/test_ui_app.py::TestImprovementPartials::test_dashboard_never_offers_experiment_or_patch_counts` — UPDATE: the exact pinned list becomes five names with `get_release_lineage` inserted in sorted position (`get_coverage`, `get_goals`, `get_intervention_burden`, `get_provisional_assumptions`, `get_release_lineage`); the assertion stays an exact list.
- [ ] `tests/unit/test_ui_app.py::TestImprovementPartials::test_index_page_links_all_improvement_partials` — UPDATE: add the `/_partials/improvement/releases/` link assertion.
- [ ] `tests/unit/test_improvement_models.py` (`INDEXED_VOCABULARIES[ImprovementRelease]`) — UPDATE: `RELEASE_STATES` gains `accepted` (six values, under the cap of 8); the vocabulary map reads the constant, so the update is to the docstring-recorded TTL/state expectations only if any test enumerates the five names literally (none does today; the row is a no-op guard).
- [ ] `tests/unit/test_migrations.py` — UPDATE: add a per-migration registration test `test_confirm_improvement_release_lane6_fields_registered` in the shape of `test_improvement_models_registration_marker_exists` (`tests/unit/test_migrations.py:738-742`): `assert "confirm_improvement_release_lane6_fields" in MIGRATIONS`, unpack `fn, description = MIGRATIONS[...]`, `assert fn is _migrate_confirm_improvement_release_lane6_fields`, `assert description`. No test enumerates `MIGRATIONS` keys as a list; each migration pins its own registration.
- [ ] `tests/unit/test_env_declaration_readers.py` — UPDATE only if a new `.env.example` declaration is added; this plan adds none (the gate reads no env key), so expected disposition is no change, recorded here so a builder who adds one knows the reader test will bite.
- [ ] `tests/unit/test_improvement_operating_report.py` — no change; the claim report is a separate module and does not alter `generate_report`.

No existing test covers a release row, a drill, a promotion gate, a process digest, or a comparison; every test for those is new.

## Rabbit Holes

- **Building the bundle.** "Immutable bundle digest activated for newly assigned Jobs; active Jobs stay pinned" is a per-Job routing layer that needs a promotion authority to route for. Nothing can promote, so there is nothing to pin. Exposure is the mechanism that exists (a merged PR reaching the fleet through `/update`), recorded honestly as `unit: fleet`.
- **Scheduling the window close.** A launchd job or reflection to close windows on time is lane 3's controller tick. `close-window --due` is the callable; wiring a scheduler here would create a second cadence owner.
- **Running a real research process for the comparison.** Lane 5's planner is the production `ArmRunner`. Writing one here, or a "simplified" planner to have something to compare, would produce a comparison of two things nobody runs. The replay runner exercises every statistical and budget path; the production runner registers itself.
- **Metering unit 1 here.** The paid-inference meter is lane 3's and touches the harness. `unit1_usd` returns `None` and the comparison says so.
- **A generic feature-flag system for `setting_restore` rollbacks.** No Redis-backed settings override exists; inventing one so a rollback could flip a flag is a new subsystem. Rollback plan kind is `git_revert` only, which matches the only exposure mechanism.
- **Auto-rollback on a regressed window.** Tempting because the drill proves the revert works. It is an automated production action taken on a metric with a 14-day denominator, and charter §6 says fewer detected bugs with less detection is not a signal. `close_window` recommends; a human runs `rollback`.
- **Blinding the comparison.** The arms are processes, and the judge of each opportunity's gain is lane 4's blinded evaluation inside each arm. Adding a second blinding layer over process identity has nothing to blind: the process digest is the arm.
- **Rendering full drill transcripts on the dashboard.** The transcript is on the verifying store for `show`; the partial renders `result` and `drilled_at`.

## Risks

### Risk 1: The drill proves less than a reader assumes
**Impact:** A `pass` drill reverts a worktree; it does not prove the fleet, a running session, or a merge commit with a different parent shape reverts the same way. A reader who treats "drilled" as "rollback capability proven in production" is misled.
**Mitigation:** The record carries `exercised` and `not_exercised` lists on every drill, the dashboard renders "drilled (worktree)" rather than "drilled", and the feature doc's first paragraph on drills says what a pass establishes. `rollback()` records the same shape with `propagation` naming the step it did not perform.

### Risk 2: The observation metric moves for reasons unrelated to the release
**Impact:** Architectural corrections over 14 days vary with workload, task difficulty, and who was around to correct. A release could read as `held` or `regressed` on noise.
**Mitigation:** Raw counts and denominators beside every rate; coverage ticks per day compared across windows with the 0.8 decline threshold; `undetermined` on a zero denominator; a noise band on the baseline rate derived from its own count (Wilson interval at 95%); and `claim_level_2_supported` requiring both the held-out effect and a `held` window. Level 2 is stated with the interval, the correction, and the falsifier, never as a bare "improved".

### Risk 3: Budget accounting is incomplete and reads as matched
**Impact:** Unit 1 is unmetered. If `None` were ever read as zero, two arms with wildly different paid-inference spend would look budget-matched and a level-3 claim would measure the budget.
**Mitigation:** `budgets_comparable` refuses on any `None`; the evaluation's notes carry every unit for both arms; `test_compare_refuses_claim_on_unknown_unit1` is mutation-checked. Unit 3 has the same hazard in a quieter form: a ledger sum over zero matched rows is `0.0` unless the reader says otherwise, so `LedgerBudgetReader.unit3_usd` returns `None` on zero matched rows and the arm runner tags its reservations through `ResourceDecl.name` (the only field `admit()` lets a caller set); `test_unit3_unknown_when_no_arm_rows` is mutation-checked. The plan states plainly that until lane 3 meters unit 1, no level-3 claim can be made here, and the report says so in the `why_not` field.

### Risk 4: Lane 5 defines its own process digest and the arms cannot be compared
**Impact:** If lane 5 hashes something other than a `ResearchProcessSpec`, the incumbent digest on the current revision is opaque, and `--arm-a` cannot be reconstructed as a spec.
**Mitigation:** The comparison accepts any `sha256:` digest for an arm and needs only that the two differ, so an opaque incumbent digest is still comparable. The issue comment to #3217 asks lane 5 to call `research_process_digest` so a later reader can reconstruct the spec; it is a request, not a dependency.

### Risk 5: The lane ships before #3216 merges and builds on a moving head
**Impact:** `evaluate`, `freeze_protocol`, `compute_contract_digest`, and the statistics module are on an open PR. A rebase after a lane 4 change could alter the contract-digest computation under a frozen comparison.
**Mitigation:** Same disposition as lane 4's Risk 4: the build branches from lane 4's head and re-bases once on merge; the comparison tests compute the digest through lane 4's function rather than a copied one, so a change there is a test failure here, not a silent divergence. Build task 1 records the lane 4 SHA the build started from.

### Risk 6: Adding fields and a state to an immortal record cannot be undone
**Impact:** `accepted` and the eight new fields are permanent once a row carries them.
**Mitigation:** The fields are plain (no index), so the only irreversible artifact is the state name; the transition table is in the docstring and the migration entry records the schema version. Nothing subtractive.

### Risk 7: The denylist becomes a false sense of safety
**Impact:** A path-prefix denylist refuses the charter by name. It does not refuse a candidate that edits `models/__init__.py` to import a different charter module, or a candidate that changes `load_from_file`'s owner check.
**Mitigation:** The denylist is one of three checks, and the plan says so: the loader's owner refusal (lane 2b) and the pinned-digest gate at proposal both fire regardless of surface names. The feature doc lists what the denylist does not catch. `models/improvement_charter.py` is on the list precisely because the loader is the second guard.

### Risk 8: `gh` and `git` calls in the lifecycle make tests depend on the network
**Impact:** `open_pr` and `expose` shell to `gh`; a test that hits GitHub is slow and flaky.
**Mitigation:** Every subprocess goes through an injectable `Runner` (the `tools/improvement_resources.py` shape); tests inject a recording runner that returns canned `gh pr view` JSON. The drill uses real `git` against a temporary repository created by the test, which is local and deterministic.

## Race Conditions

### Race 1: Two operators transition the same release concurrently
**Location:** `tools/improvement_release/lifecycle.py::_transition`
**Trigger:** `approve` and `withdraw` run within the same second from two shells.
**Data prerequisite:** The release row at the state each caller read.
**State prerequisite:** Exactly one terminal write per transition.
**Mitigation:** `_transition` re-reads the row immediately before `save()` and refuses `WRONG_STATE` if `state` no longer equals `allowed_from`. Popoto has no CAS, so a sub-millisecond interleave can still double-write; the `history` list inside `outcome` records both events with timestamps, so the double-write is detectable and the second event names the conflict. Operator-invoked transitions on an immortal audit record are rare enough that detectability is the right cost; lane 3's journal is the place a fenced transition belongs, and this lane does not build a second one.

### Race 2: A crash between writing the new revision and superseding the old one
**Location:** `tools/improvement_recursion/compare.py::_write_revision`
**Trigger:** Process dies after `ImprovementModelRevision(state="current").save()` and before the previous row's `state="superseded"` save.
**Data prerequisite:** The evaluation row is already written with `verdict=accept`.
**State prerequisite:** At most one `current` revision per project.
**Mitigation:** Write order is new-then-supersede so the failure mode is two `current` rows, never zero. `report.claim_report` and `compare run` both detect `>1 current` and report `REVISION_CONFLICT` with both ids; `compare run` refuses to run until an operator supersedes one by hand through the CLI's `revision supersede` subcommand.

### Race 3: The drill's worktree outlives the drill
**Location:** `tools/improvement_release/drill.py::run`
**Trigger:** SIGKILL during a `verify` command.
**Data prerequisite:** none
**State prerequisite:** The retention root holds no stale worktrees that a later `git worktree add` refuses to overwrite.
**Mitigation:** Worktree paths carry a timestamp so a stale one never collides; `run` calls `git worktree prune` before adding; `drill --sweep` removes any drill worktree older than a day. The `finally` handles every exception path; only a hard kill leaves residue, and the next drill prunes it.

### Race 4: Evidence expires under the observation window
**Location:** `tools/improvement_release/observation.py::measure`
**Trigger:** `baseline_window_days + window_days` approaches the 30-day evidence TTL.
**Data prerequisite:** Baseline rows still present when the window closes.
**State prerequisite:** none
**Mitigation:** The baseline is measured and frozen onto `outcome.baseline` at `expose`, never recomputed; `close_window` reads only the window. The proposal gate caps `baseline_window_days + window_days` at 28 so even the baseline read at exposure is inside the TTL. The cap protects an on-time close; a late close is a separate hazard, since nothing schedules `close_window` and an operator can run it weeks after the window ended, so `close_window` refuses to score a window once `now - exposed_at > EVIDENCE_TTL_DAYS` (`EVIDENCE_EXPIRED`) rather than counting a window whose early rows have expired. A capped read is the third hazard: `measure` reports `truncated` when the newest-first read of `READ_LIMIT` rows did not reach the window's start, and a truncated window is `undetermined` (`EVIDENCE_TRUNCATED`).

### Race 5: The charter is amended between proposal and approval
**Location:** `lifecycle.approve`
**Trigger:** Tom commits a charter v3 and `load_from_file` pins a new digest while a release sits in `proposed`.
**Data prerequisite:** `release.charter_digest` from proposal time.
**State prerequisite:** Charter §12: reassess pending actions under the new authority before further effects.
**Mitigation:** `approve` re-compares `release.charter_digest` with the pinned digest and refuses `CHARTER_DRIFT`; the operator withdraws and re-proposes under the new digest, and the withdrawn record keeps the old digest as evidence of what it was admitted under.

## No-Gos (Out of Scope)

- [ORDERED] Enabling automated promotion by any path. Gated on two events: evaluator secrets and production credentials separated from candidate execution, and a human-amended charter naming the reversible surfaces. `promote_automatically` exists to refuse and name both; nothing in this lane, and no setting, env key, or file flag, changes its answer. Anti-criteria: the "No promotion flag exists" and "No file-flag enable" Verification rows.
- [ORDERED] Job-level bundle activation and pinning ("activated for newly assigned Jobs; active Jobs stay pinned; rollback repoints the default bundle"). Waits on the same charter amendment: a routing layer for promotions is built when something can promote. Exposure is recorded as `unit: fleet` through the merged PR.
- [EXTERNAL] Separating evaluator secrets and production credentials from candidate execution. A separate process identity and credential scope is an operations change on Tom's machines; this lane names it as precondition 1 and cannot satisfy it.
- [EXTERNAL] Amending `docs/improvement-charter.md` to name reversible surfaces. Only Tom authorizes charter changes (§12). The gate checks the pinned text for the section; writing it is not this lane's, and `docs/improvement-charter.md` is on the denylist.
- [SEPARATE-SLUG #3217] The production `ArmRunner` (lane 5's planner tick run under a pinned process spec), the writer of planner-tick `ImprovementModelRevision` rows, and `candidate_ref` on the candidate manifest. This lane ships the protocol, the replay runner, the digest function, and the `ARM_RUNNER_ABSENT` refusal. Anti-criterion: the "Comparison refuses without an arm runner" Verification row.
- [SEPARATE-SLUG #3215] Unit-1 paid-inference metering, the `valor-improve` CLI, the control journal, and scheduling `close-window --due` on the controller tick. `BudgetReader.unit1_usd` returns `None` until lane 3 meters it; `valor-improve-release` is a separate binary by design.
- [SEPARATE-SLUG #3216] Any change to `tools/improvement_eval/`. Imported, never modified. Anti-criterion: the "Lane 4 harness untouched" Verification row.
- [DESTRUCTIVE] Auto-rollback on a regressed observation window. `close_window` writes `rollback_recommended`; a human runs `rollback`. Anti-criterion: `grep -c 'rollback(' tools/improvement_release/lifecycle.py` inside `close_window`'s body is asserted zero by `test_close_window_never_calls_rollback`.
- [DESTRUCTIVE] Running the drill or the rollback inside the repo checkout. Both refuse any path that is not a worktree they created under the retention root. Anti-criterion: `test_drill_refuses_checkout_path`.

## Update System

- `scripts/update/migrations.py` gains `_migrate_confirm_improvement_release_lane6_fields`, registered in `MIGRATIONS` as `confirm_improvement_release_lane6_fields`, following `_migrate_confirm_improvement_v2_fields` (`:1463`, registered `:1644`). Purely additive: `ImprovementRelease` gains `kind`, `candidate_ref`, `base_revision`, `exposed_at`, `observation`, `rollback_drill`, `promotion_gate`, and the `drill_log` ContentField; `RELEASE_STATES` gains `accepted`. No field is removed, no index set is stripped, nothing is backfilled; the entry is the durable schema-version marker.
- `pyproject.toml [project.scripts]` gains `valor-improve-release = "tools.improvement_release.cli:main"`. `/update` already reinstalls the project (`uv sync` / editable install) on every run, so the entry point propagates with no update-script change.
- No new dependency. The drill uses `git` and the exposure step uses `gh`, both already required by the SDLC tooling. No `.env.example` declaration: the promotion gate reads no env key by design, and a Verification row asserts no `PROMOTION` key exists in `config/settings.py` or `.env.example`.
- No launchd job, no reflection registration. Window closing is operator-invoked (`valor-improve-release close-window`); scheduling it is lane 3's controller tick once that exists, and the CLI's `--due` listing is what a tick would call.
- Fleet: single-machine, the `valor` owner, unchanged. A rollback commit pushed to `main` reaches other machines through the ordinary `/update`, which the rollback record names as the propagation step it did not perform.

## Agent Integration

- New CLI entry point `valor-improve-release = "tools.improvement_release.cli:main"` in `pyproject.toml [project.scripts]`, with subcommands `propose`, `drill` (and `drill --sweep`, Race 3's cleanup of stale drill worktrees), `approve`, `open-pr`, `expose`, `close-window` (and `close-window --due`), `rollback` (and `rollback --branch <name>`), `withdraw`, `show`, `gate`, `compare fresh`, `compare freeze`, `compare run` (and `compare run --arm-runner <module>:<attr>`), `revision supersede` (Race 2's operator remedy for two `current` revisions), and `report`. The agent reaches every capability in this plan through its Bash tool via this entry point. It is a separate binary from lane 3's planned `valor-improve` on purpose: rollback and the drill are incident surfaces that must work when the control journal is unreachable, and a break-glass tool that imports the research CLI couples in the wrong direction. The one place the two meet is `compare run --arm-runner`, which imports the named module lazily at run time, so the binary's import graph stays free of the research CLI while the production comparison can still name lane 5's runner. `docs/tools-reference.md:356` (`valor-improve release compare`, marked planned) is re-pointed to `valor-improve-release compare`.
- The bridge imports nothing new and the poll registry is untouched. Approval is a CLI argument (`--approved-by <human>`) recorded on the release; no message, poll, or `AskUserQuestion` is sent. The claim report is printed by `report` and rendered by the dashboard; it is not pushed to Telegram by this lane.
- Integration tests: `tests/integration/test_improvement_release_end_to_end.py` invokes `valor-improve-release` as a subprocess against a claimed test Redis for `propose → drill → approve → expose → close-window` on a seeded `accept` evaluation, and `tests/integration/test_improvement_release_drill.py` runs the drill against a real temporary git repository. A grep row in Verification confirms `pyproject.toml` references `tools.improvement_release.cli:main`.
- The dashboard partial `/_partials/improvement/releases/` is read by `ui/app.py` through `ui.data.improvement.get_release_lineage`, the same pattern as the three existing partials.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/improvement-release.md`: the release lifecycle and its state machine, the drill record shape and what it does and does not exercise, the exposure and observation-window semantics with the frozen baseline, the promotion gate and both preconditions, the denylist, the recursive comparison's contract and budget accounting, the claim ladder report, and the `valor-improve-release` reference.
- [ ] Update `docs/features/improvement-controller.md`: "What exists today" gains lane 6; the authority table's "Evaluation and release" row cites the gate module; the records table's `ImprovementRelease` row names the new fields; the dashboard section lists the fifth getter.
- [ ] Update `docs/features/improvement-evaluation.md` (lane 4's doc) with one paragraph on what consumes an `accept` verdict, linking to the release doc; no change to the harness description.
- [ ] Add `docs/features/improvement-release.md` to the `docs/features/README.md` index table.
- [ ] Update `docs/tools-reference.md`: add `valor-improve-release` with every subcommand in the Agent Integration list, including `revision supersede`, `drill --sweep`, `close-window --due`, `rollback --branch`, and `compare run --arm-runner`; re-point the planned `valor-improve release compare` line.
- [ ] Update `docs/plans/critiques/recursive-self-improvement-capability-matrix.md`: rows for release lifecycle, drill, promotion gate, denylist, process digest, comparison, claim report, each graded on the four axes with "implemented" only.

### Inline Documentation
- [ ] `models/improvement_release.py` docstring: the six states and their transitions, the new fields, and the unchanged sentence that promotion is disabled and this record does not enable it.
- [ ] `tools/improvement_release/promotion.py` docstring: both preconditions, the event that satisfies each, and the statement that no setting, env key, or file flag reads into the gate.
- [ ] `tools/improvement_recursion/compare.py` docstring: why budgets are matched by cap and verified by accounting, why unknown unit-1 spend refuses a claim, and what "fresh" means by record lookup.

## Success Criteria

Mapped to the issue's acceptance criteria in order.

- [ ] **Release records carry exposure, rollback plan, and observation window before exposure.** `propose` writes `exposure` (plan), `rollback_plan`, and `observation` (plan) on the row it creates; `approve` stamps `observation_window_ends_at` before any PR is opened; a test asserts all four are non-null on a release in `approved` with `exposed_at is None`.
- [ ] **A rollback drill is executed against a real release record and recorded.** `tests/integration/test_improvement_release_drill.py` proposes a release from a real temporary git repository, runs `drill`, and asserts `rollback_drill.result == "pass"`, `restored is True`, the `exercised`/`not_exercised` lists, and that `drill_log` loads from the verifying store with a matching digest. A second test seeds a conflicting base commit and asserts `fail` with `revert_conflict`. The build's PR body includes one drill record run against the lane's own first release (kind `infrastructure`) so a drill has been executed against a real record before anything is exposed. That self-drill candidate is a one-commit branch off the lane head touching only `docs/features/improvement-release.md`, proposed with `--surfaces docs/features/improvement-release.md`, from a seeded `accept` evaluation whose `notes` carry `lane-6-self-drill`, then withdrawn. `tools/improvement_release/` is on the denylist and stays there; the lane's own release-policy files are never a candidate surface, and a self-drill whose surfaces named them is refused `SURFACE_DENIED` by design. The candidate must satisfy `set(git diff --name-only <base> <cand>) == set(surfaces)` so the drill's `UNDECLARED_SURFACE_CHANGED` pre-check passes.
- [ ] **An observation window closes and its outcome is written back.** The end-to-end test exposes a release with a canned merged PR, seeds evidence in the baseline window and the observation window, advances the clock past `observation_window_ends_at`, runs `close_window`, and asserts `outcome.window`, `outcome.deltas` with raw counts and denominators, `outcome.verdict`, and state `accepted` on `held`. A second test seeds fewer coverage ticks in the window and asserts `undetermined` with `detection_declined=True`.
- [ ] **The recursive comparison runs on budget-matched fresh opportunities with the budget accounting shown.** `tests/unit/test_improvement_recursion_compare.py` freezes a contract over fresh seeded cases, runs both arms through `ReplayArmRunner` with equal caps, and asserts the evaluation's `effect`, `confidence_interval`, `correction="holm"`, and that `notes` carries both arms' use in all four units. A worked opportunity is refused at freeze; a mismatched budget yields `inconclusive` with `BUDGET_MISMATCH`.
- [ ] **`research_process_digest` distinguishes the arms.** `run` refuses `ARMS_IDENTICAL` on equal digests; the accept path writes an `ImprovementModelRevision` whose `research_process_digest` equals arm B's and whose `supersedes_id` names the prior current revision.
- [ ] **Automated promotion remains disabled, and the code path names both preconditions.** `promotion_gate("valor")` returns `automated=False` with both names in `unmet` against the real pinned charter; `promote_automatically` raises `PromotionDisabled` listing them; a synthetic charter with a "Reversible surfaces" section clears only precondition 2 and the gate still refuses; no `PROMOTION` key exists in `config/settings.py` or `.env.example`.
- [ ] **A level-2 or level-3 claim states its interval, correction, and falsifier, or the report says plainly that the evidence does not support it.** `claim_report` on an empty project returns all three levels `supported=False` with a `why_not` sentence each; on the end-to-end fixture, level 2 is `supported=True` with the interval, correction, and falsifier populated; level 3 is `supported=False` with `why_not` naming `BUDGET_UNKNOWN:unit1` when unit 1 is unmetered.
- [ ] **The dashboard never presents experiment count or merged-patch count as improvement.** The pinned getter list is exactly five; the anti-criterion grep over `ui/data/improvement.py` and the releases template is zero; the partial renders lineage, not counts.
- [ ] Tests pass (`/do-test`), including the full `tests/unit/test_ui_app.py` and `tests/unit/test_improvement_models.py`.
- [ ] Documentation updated (`/do-docs`): `docs/features/improvement-release.md` exists and is indexed; `improvement-controller.md`, `improvement-evaluation.md`, `tools-reference.md`, and the capability matrix are updated.
- [ ] `pyproject.toml` references `tools.improvement_release.cli:main` and `valor-improve-release --help` exits 0 from the venv.
- [ ] Every mutation proof in Failure Path Test Strategy is recorded in the PR body with the test that went red.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly; they deploy team members and coordinate. Every builder gets an explicit worktree cwd and the lane 4 head SHA the build started from.

### Team Members

- **Builder (release lifecycle and model)**
  - Name: release-builder
  - Role: `models/improvement_release.py` changes, migration, `tools/improvement_release/{__init__,lifecycle,denylist,promotion,observation,lineage}.py`, their unit tests
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (drill and rollback)**
  - Name: drill-builder
  - Role: `tools/improvement_release/drill.py`, the step executor shared with `rollback`, the `TimeoutSettings` field, `tests/integration/test_improvement_release_drill.py`
  - Agent Type: builder
  - Domain: subprocess/untrusted-input (paths, refs, commands from a plan JSON)
  - Resume: true

- **Builder (recursion)**
  - Name: recursion-builder
  - Role: `tools/improvement_recursion/{__init__,process,freshness,budget,arms,compare,report}.py` and their unit tests
  - Agent Type: builder
  - Resume: true

- **Builder (CLI and dashboard)**
  - Name: surface-builder
  - Role: `tools/improvement_release/cli.py`, `pyproject.toml` entry, `ui/data/improvement.py::get_release_lineage`, `ui/app.py` partial, `ui/templates/improvement/releases.html`, index link, `tests/unit/test_ui_app.py` updates, `tests/integration/test_improvement_release_end_to_end.py`
  - Agent Type: builder
  - Resume: true

- **Validator (guards)**
  - Name: guard-validator
  - Role: run every mutation proof in Failure Path Test Strategy against the built code, in its own worktree, and record which test went red for each; verify no `except Exception: pass` in new modules
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: release-documentarian
  - Role: the Documentation section's tasks
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: run the Verification table, confirm every Success Criterion, produce the report
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Model, migration, denylist, promotion gate
- **Task ID**: build-release-model
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_models.py`, `tests/unit/test_migrations.py`, `tests/unit/test_improvement_release_denylist.py` (create), `tests/unit/test_improvement_release_promotion_gate.py` (create)
- **Informed By**: spike-2 (precondition 2 is checkable against pinned charter text; precondition 1 is unmet by construction)
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: true
- Record the lane 4 head SHA the build started from in the first commit message.
- Extend `RELEASE_STATES` with `accepted`; add `RELEASE_KINDS`; add the eight fields and the `drill_log` ContentField; update the docstring with the transition table.
- Add `_migrate_confirm_improvement_release_lane6_fields` and register it.
- Write `denylist.py` with normalization and glob/escape refusal; `promotion.py` with the gate, the two precondition constants, `promote_automatically`, and `PromotionDisabled`.
- Tests: schema gate still green with six states; denylist refuses the charter, the model, `..`, and `*`; gate refuses on the real charter, on a synthetic charter that satisfies precondition 2, and never reads an env key (monkeypatch `os.environ` with a `PROMOTION_ENABLED=1` and assert no change).

### 2. Observation measurement and lineage
- **Task ID**: build-observation
- **Depends On**: build-release-model
- **Validates**: `tests/unit/test_improvement_release_observation.py` (create)
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `observation.py` (`OBSERVATION_METRICS`, `EVIDENCE_TTL_DAYS = 30`, `measure` with the `truncated` flag, `compare_windows` with the Wilson band, the 0.8 coverage threshold, and the `EVIDENCE_TRUNCATED` branch) and `lineage.py` (`release_lineage`).
- Tests: seeded evidence in two windows yields `held`, `regressed`, and `undetermined` on the three fixtures; zero denominator yields `None` rate; a raising read yields `unavailable`; a read that hits the limit inside the window yields `truncated=True` and `undetermined`; `EVIDENCE_TTL_DAYS` equals the model's TTL.

### 3. Lifecycle
- **Task ID**: build-lifecycle
- **Depends On**: build-observation
- **Validates**: `tests/unit/test_improvement_release_lifecycle.py` (create)
- **Informed By**: spike-1 (manifest lacks `candidate_ref`; `base_revision` present)
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `lifecycle.py`: `ReleaseRefused` and the closed code vocabulary (including `ROLLBACK_PUSH_REFUSED` and `WINDOW_EXCEEDS_EVIDENCE_TTL`), `_transition` with re-read, `propose`, `approve`, `open_pr`, `expose` (restamping `observation_window_ends_at` from `exposed_at` with a `window_restamped` history event), `close_window` (`EVIDENCE_EXPIRED` check, `window_shortfall_days` from `exposed_at`), `rollback` (push, `ls-remote` confirmation, `--branch`), `withdraw`, the injectable `Runner`.
- Tests: one per refusal code; the happy path through `approved` with a recording runner; `expose` with canned `gh pr view` JSON freezes the baseline and restamps the window end (`test_expose_restamps_window_end_from_exposed_at`); `close_window` before the window end refuses `WINDOW_OPEN`, with `--force` records the reason and a positive shortfall, on time records shortfall 0, past the TTL yields `EVIDENCE_EXPIRED`; `rollback` with a refused push and with a moved remote head both leave state unchanged; `approve` re-checks charter drift (Race 5); `test_close_window_never_calls_rollback`.

### 4. Drill and rollback execution
- **Task ID**: build-drill
- **Depends On**: build-release-model
- **Validates**: `tests/unit/test_improvement_release_drill.py` (create), `tests/integration/test_improvement_release_drill.py` (create)
- **Assigned To**: drill-builder
- **Agent Type**: builder
- **Parallel**: true
- Write `drill.py`: worktree creation under the retention root, the step executor (shared with `rollback`), the three pre-revert range checks (`BASE_NOT_ANCESTOR`, `MERGE_COMMITS_IN_RANGE`, `UNDECLARED_SURFACE_CHANGED`), revert, per-surface and whole-tree restoration checks, `verify` with the new `TimeoutSettings.improvement_drill_verify_seconds`, `finally` removal, `--sweep`, the record shape with `exercised`/`not_exercised`.
- Add the timeout field to `config/settings.py` and its row in `docs/features/config-timeout-catalog.md`.
- Tests against a temporary git repository built by the test: pass; revert conflict; base not an ancestor; merge commit in range; undeclared surface changed (pre-check bites, no revert ran); post-revert residue (whole-tree check bites); verify timeout; worktree removed on every path; refuses a checkout path; `drill_log` round-trips through the verifying store.

### 5. Process digest, freshness, budget, arms
- **Task ID**: build-recursion-core
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_recursion_process.py`, `tests/unit/test_improvement_recursion_freshness.py`, `tests/unit/test_improvement_recursion_budget.py` (create all)
- **Assigned To**: recursion-builder
- **Agent Type**: builder
- **Parallel**: true
- Write `process.py`, `freshness.py`, `budget.py` (`BudgetCap`, `BudgetUse`, `BudgetReader`, `LedgerBudgetReader`, `budgets_comparable`), `arms.py` (`ArmRunner`, `ArmResult`, `ReplayArmRunner`, `register_arm_runner`, `get_arm_runner`, `ArmRunnerAbsent`).
- Tests: canonical digest independent of key order; invalid split refused; empty split digests; lane 5 fixture digests identically through both functions (skipped by name until `tools/improvement_ranking.py` exists); freshness excludes cases with an experiment, an investigation, or a prior comparison, with reasons; `LedgerBudgetReader` sums seeded `InfrastructureReservation` rows whose `resource` carries the `arm:<arm_run_id>:` prefix (admitted through lane 7's `admit()` with a `ResourceDecl` named that way, never by writing `reason`), returns `None` on zero matched rows and `None` for unit 1; `budgets_comparable` on unknown, exceeded, mismatched, ok, and an explicit zero cap.
- `ReplayArmRunner` admits its fixture spend through `admit()` under the arm-prefixed resource name so the ledger reader is exercised on the same path a production runner uses; `arms.py` also exposes `resolve_arm_runner(spec: str)` for the `--arm-runner module:attr` form.

### 6. Comparison and claim report
- **Task ID**: build-comparison
- **Depends On**: build-recursion-core, build-lifecycle
- **Validates**: `tests/unit/test_improvement_recursion_compare.py`, `tests/unit/test_improvement_recursion_report.py` (create both)
- **Assigned To**: recursion-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `compare.py` (`freeze`, `run`, `_write_revision`, `revision supersede` helper) importing `freeze_protocol`, `compute_contract_digest`, `clustered_bootstrap_ci`, `evaluate_family` from lane 4, and `report.py` (`claim_report`, `render`).
- Tests: freeze refuses worked opportunities and zero opportunities; run refuses identical arms and an absent runner, resolves `--arm-runner` by lazy import and refuses an unimportable or missing attribute with the error in `detail`; equal-cap replay yields accept/reject/inconclusive on three fixtures with the interval and Holm recorded; unknown unit 1 yields inconclusive with `BUDGET_UNKNOWN:unit1`; an arm that admitted nothing yields `BUDGET_UNKNOWN:unit3`; an arm exception yields `infra_failure`; accept writes the revision new-then-supersede; two current revisions are reported as `REVISION_CONFLICT`; the report degrades per level and never contains a count.

### 7. CLI, dashboard, end-to-end
- **Task ID**: build-surface
- **Depends On**: build-lifecycle, build-drill, build-comparison
- **Validates**: `tests/unit/test_ui_app.py`, `tests/integration/test_improvement_release_end_to_end.py` (create)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `cli.py` and register `valor-improve-release`; add `get_release_lineage`, the partial route, the template, and the index link; update the two `test_ui_app.py` pins.
- End-to-end test as a subprocess of the CLI against a claimed test Redis and a temporary git repository: seed experiment and accept evaluation → propose → drill → approve → open-pr (recording runner) → expose (canned merged PR) → seed evidence → close-window → `accepted`; then `report` shows level 2 supported and level 3 not, with `why_not`.
- Run the drill against this lane's own first release record and paste the record into the PR body. The candidate is a one-commit branch off the lane head that touches only `docs/features/improvement-release.md` (base = the lane head, `--surfaces docs/features/improvement-release.md`, `--kind infrastructure`, seeded `accept` evaluation with `notes` carrying `lane-6-self-drill`), drilled, then withdrawn. The declared surfaces must equal the candidate's changed paths exactly, and none may be on the denylist; do not widen or shorten `CANDIDATE_SURFACE_DENYLIST` to make the self-drill pass.

### 8. Guard validation
- **Task ID**: validate-guards
- **Depends On**: build-surface
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- In a separate worktree, apply each mutation from Failure Path Test Strategy one at a time, run the named test, record red/green, revert. Report any guard whose test stays green as a blocker.
- Grep new modules for `except Exception` and confirm each logs or re-raises.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guards
- **Assigned To**: release-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section: the new feature doc, the four updates, the index row, the tools reference, the capability matrix rows.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row; confirm every Success Criterion; confirm the PR body carries the drill record and the mutation table; produce the report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests for the lane pass | `scripts/pytest-clean.sh tests/unit/test_improvement_release_lifecycle.py tests/unit/test_improvement_release_drill.py tests/unit/test_improvement_release_denylist.py tests/unit/test_improvement_release_promotion_gate.py tests/unit/test_improvement_release_observation.py tests/unit/test_improvement_recursion_process.py tests/unit/test_improvement_recursion_freshness.py tests/unit/test_improvement_recursion_compare.py tests/unit/test_improvement_recursion_report.py tests/unit/test_improvement_models.py tests/unit/test_ui_app.py tests/unit/test_migrations.py -q` | exit code 0 |
| End-to-end release lifecycle | `scripts/pytest-clean.sh tests/integration/test_improvement_release_end_to_end.py tests/integration/test_improvement_release_drill.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/improvement_release tools/improvement_recursion models/improvement_release.py ui/data/improvement.py ui/app.py scripts/update/migrations.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/improvement_release tools/improvement_recursion models/improvement_release.py ui/data/improvement.py ui/app.py scripts/update/migrations.py` | exit code 0 |
| Entry point registered | `grep -c 'valor-improve-release = "tools.improvement_release.cli:main"' pyproject.toml` | output contains 1 |
| Promotion gate refuses on current charter | `.venv/bin/python -c "from tools.improvement_release.promotion import promotion_gate; g = promotion_gate('valor'); assert g.automated is False and set(g.unmet) == {'credential_separation', 'charter_names_reversible_surfaces'}, g; print('refused')"` | output contains refused |
| No promotion flag exists (anti-criterion) | `grep -rci 'promotion' config/settings.py .env.example` | match count == 0 |
| No file-flag enable for promotion (anti-criterion) | `grep -rc 'promotion-enabled\|promote-enabled' tools/improvement_release tools/improvement_recursion` | match count == 0 |
| Charter is a denied surface | `.venv/bin/python -c "from tools.improvement_release.denylist import denied_surfaces; d = denied_surfaces(['docs/improvement-charter.md', 'models/improvement_charter.py', 'tools/x.py']); assert d == ['docs/improvement-charter.md', 'models/improvement_charter.py'], d; print('denied')"` | output contains denied |
| Dashboard exports exactly five getters | `.venv/bin/python -c "import ui.data.improvement as m; names = [n for n in dir(m) if n.startswith('get_')]; assert names == ['get_coverage', 'get_goals', 'get_intervention_burden', 'get_provisional_assumptions', 'get_release_lineage'], names; print('pinned')"` | output contains pinned |
| Dashboard never returns an experiment or patch count (anti-criterion) | `grep -c 'experiment_count\|merged_patch\|patch_count\|len(experiments)' ui/data/improvement.py ui/templates/improvement/releases.html` | match count == 0 |
| Lane 4 harness untouched | `git diff --stat main -- tools/improvement_eval/ \| tail -1 \| grep -c 'changed'` | match count == 0 |
| Process digest is deterministic and canonical | `.venv/bin/python -c "from tools.improvement_recursion.process import ResearchProcessSpec, research_process_digest as d; a = ResearchProcessSpec(selection_rule='rank', investigation_budget_split={'probe': 0.5, 'web_research': 0.5}, revision_cadence_seconds=3600, planner_prompt_digest='sha256:0', skill_digest='sha256:0'); b = ResearchProcessSpec(selection_rule='rank', investigation_budget_split={'web_research': 0.5, 'probe': 0.5}, revision_cadence_seconds=3600, planner_prompt_digest='sha256:0', skill_digest='sha256:0'); assert d(a) == d(b) and d(a).startswith('sha256:'); print('canonical')"` | output contains canonical |
| Comparison refuses without an arm runner | `.venv/bin/python -c "from tools.improvement_recursion.arms import get_arm_runner, ArmRunnerAbsent\ntry:\n    get_arm_runner()\nexcept ArmRunnerAbsent as e:\n    print('ARM_RUNNER_ABSENT', e)"` | output contains ARM_RUNNER_ABSENT |
| Comparison resolves a named arm runner by lazy import | `.venv/bin/python -c "from tools.improvement_recursion.arms import resolve_arm_runner, ReplayArmRunner; r = resolve_arm_runner('tools.improvement_recursion.arms:ReplayArmRunner'); assert isinstance(r, ReplayArmRunner); print('resolved')"` | output contains resolved |
| Incident binary imports nothing from the research CLI at load | `.venv/bin/python -c "import sys; import tools.improvement_release.cli; assert not [m for m in sys.modules if m in ('tools.improvement', 'tools.improvement_ranking', 'tools.improvement_plan_arm')], 'research CLI imported at load'; print('decoupled')"` | output contains decoupled |
| Evidence TTL constant matches the model | `.venv/bin/python -c "from tools.improvement_release.observation import EVIDENCE_TTL_DAYS; from models.improvement_evidence import ImprovementEvidence; assert EVIDENCE_TTL_DAYS * 86400 == ImprovementEvidence.Meta.ttl, (EVIDENCE_TTL_DAYS, ImprovementEvidence.Meta.ttl); print('ttl pinned')"` (`Meta.ttl = 86400 * 30`, `models/improvement_evidence.py:146`) | output contains ttl pinned |
| Release states are six and include accepted | `.venv/bin/python -c "from models.improvement_release import RELEASE_STATES as S; assert len(S) == 6 and 'accepted' in S and S[0] == 'proposed', S; print('states ok')"` | output contains states ok |
| Migration registered | `grep -c '"confirm_improvement_release_lane6_fields"' scripts/update/migrations.py` | output contains 1 |
| Feature doc exists and is indexed | `test -f docs/features/improvement-release.md && grep -c 'improvement-release.md' docs/features/README.md` | output contains 1 |
| Tools reference re-pointed | `grep -c 'valor-improve-release compare' docs/tools-reference.md` | output > 0 |
| No controller module writes the charter (anti-criterion, carried from lane 2b) | `grep -rEc 'open\([^)]*improvement-charter[^)]*"w' tools/improvement_release tools/improvement_recursion` | match count == 0 |
| Lifecycle never rolls back on its own (anti-criterion) | `.venv/bin/python -c "import ast, inspect; from tools.improvement_release import lifecycle; src = inspect.getsource(lifecycle.close_window); tree = ast.parse(src); calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]; print(calls.count('rollback'))"` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_improvement_release_*.py tests/unit/test_improvement_recursion_*.py \| grep -v '# open bug'` | exit code 1 |

## Critique Results

Critique round 1, 2026-09-14, FULL depth, mode: sequential lenses (Agent tool unavailable in the stage runner's context; no finding below was independently corroborated by a second critic).

Revision pass, 2026-09-14T11:19:43Z: every concern and nit below is embedded in the plan body at the
sections named in "Addressed By"; none was declined. Each Implementation Note is kept verbatim as the
critic wrote it so a builder can compare the note against the section that absorbed it.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Observation window end is anchored to approval, not exposure. `approve` stamps `observation_window_ends_at = approved_at + window_days`, but the window is measured over `[exposed_at, observation_window_ends_at]` and `exposed_at` is only known after the PR merges. Every approval-to-merge delay shortens the window, `window_shortfall_days` is nonzero on every real release, and `claim_level_2_supported` (which requires shortfall 0) can never be true. | Data Flow steps 4, 6, 7; transition table; `build-lifecycle` tests; mutation proof `test_expose_restamps_window_end_from_exposed_at` | In `lifecycle.expose`, after `exposed_at = now`, restamp `release.observation_window_ends_at = exposed_at + timedelta(days=release.observation["window_days"])` and append `{"event": "window_restamped", "from": <approve-time value>, "to": <new value>}` to `outcome.history`. In `close_window`, `window_shortfall_days = max(0, window_days - (closed_at - exposed_at).days)`, zero on an on-time close and positive only under `--force`. The approve-time stamp stays, so the SC1 test (four fields non-null in `approved` with `exposed_at is None`) still passes. |
| CONCERN | Risk & Robustness | Drill range revert has two unhandled shapes and a vacuous check. `git revert --no-commit <base>..<cand>` aborts on any merge commit in the range ("no -m option"), and a candidate that rebased onto or merged newer `main` makes `base_revision` a non-ancestor so the range includes unrelated `main` commits. After a clean full-range revert the tree equals base by construction, so the whole-tree diff can only fail on the conflict path and the fixture for `test_drill_fails_when_undeclared_surface_differs` cannot be built from it. | Technical Approach, drill steps 3 and 5; Failure Path drill row; `build-drill`; mutation proofs (undeclared-surface, ancestry, tree-restoration) | In `drill.run` before the revert: `git merge-base --is-ancestor <base> <cand>` nonzero → `result="fail", reason="BASE_NOT_ANCESTOR"`; `git rev-list --merges <base>..<cand>` non-empty → `result="fail", reason="MERGE_COMMITS_IN_RANGE"` (or revert each with `-m 1`, recorded per commit); `changed = git diff --name-only <base> <cand>`; `undeclared = [p for p in changed if not any(p == s or p.startswith(s.rstrip("/") + "/") for s in surfaces)]`; non-empty → `result="fail", reason="UNDECLARED_SURFACE_CHANGED", paths=undeclared`. Keep the post-revert whole-tree diff as the sequence-completed invariant. The drill rehearses a range revert on the candidate branch while `rollback()` runs `git revert -m 1 <merge_sha>` on `main`; keep `merge_commit_revert` in `not_exercised` and say in the feature doc that the two differ. |
| CONCERN | Risk & Robustness | Evidence reads can silently truncate or expire under a window. `measure` reads `ImprovementEvidence.recent(project_key, limit=READ_LIMIT)` (`ui/data/improvement.py:41,61`, `READ_LIMIT = 1000`; `models/improvement_evidence.py:149` returns newest-first, capped), so a busy 28-day span over 1000 rows drops the oldest baseline rows and an undercounted baseline reads as `regressed`; rows expire at 30 days (`models/improvement_evidence.py:146`), so a late operator-invoked `close_window` measures a window whose early rows are gone and reports a count, never a gap. | Technical Approach, Observation; Data Flow step 7; Race 4; Failure Path observation row; Verification row "Evidence TTL constant matches the model" | In `measure`, `if len(rows) == limit and rows[-1].created_at > start: return {..., "truncated": True}`; `compare_windows` returns `verdict="undetermined", reason="EVIDENCE_TRUNCATED"` when either window is truncated. In `close_window`, `if now - release.exposed_at > timedelta(days=EVIDENCE_TTL_DAYS): verdict="undetermined", reason="EVIDENCE_EXPIRED"` with `EVIDENCE_TTL_DAYS = 30` pinned equal to the model's TTL by a test. Surface both reasons in the partial's window row. |
| CONCERN | Scope & Value | The self-drill collides with the denylist. Success Criterion 2 and task 7 drill "this lane's own first release record" with "surfaces = this lane's new files", which include `tools/improvement_release/`, an entry on `CANDIDATE_SURFACE_DENYLIST` (release policy). `propose` refuses it with `SURFACE_DENIED` as written, and the path of least resistance for a builder is to drop that prefix from the denylist, weakening the guard the criterion exists to prove. | Success Criterion 2; task `build-surface` self-drill bullet; Open Question 4 resolved | The self-drill candidate must satisfy `set(git diff --name-only <base> <cand>) ∩ denied == ∅` and, with the drill's undeclared-surface pre-check, must equal the declared `surfaces`. Concretely: a one-commit branch off the lane head touching only `docs/features/improvement-release.md` (or `tools/improvement_recursion/`), proposed with `--surfaces` equal to exactly those paths, `--kind infrastructure`, from a seeded `accept` evaluation whose `notes` carry `lane-6-self-drill`, then withdrawn. `tools/improvement_release/` stays denied; the lane's own release-policy files are never a candidate surface. Alternatively drop the PR-body self-drill and let the integration test's real record satisfy SC2. |
| CONCERN | Scope & Value | `rollback` pushes a revert straight to `main` from the CLI, bypassing the PR pipeline the plan requires for exposure, and transitions to `rolled_back` without saying what happens when the push is refused (branch protection, `.githooks/pre-push`, a moved head). A `rolled_back` row whose revert never reached `main` is the false record charter §12 forbids. | Data Flow step 8; Technical Approach, drill section (`rollback()`); refusal vocabulary; Failure Path rollback row; mutation proof `test_rollback_refuses_when_remote_head_differs` | In `lifecycle.rollback`: `push = runner(["git", "push", "origin", "HEAD:main"])`; `if push.returncode != 0:` append history `{"event": "rollback_push_refused", "stderr": push.stderr[-2048:], "revert_sha": sha}`, leave state unchanged, `raise ReleaseRefused("ROLLBACK_PUSH_REFUSED")`. Transition to `rolled_back` only when the push returned 0 and `git ls-remote origin main` equals `revert_sha`. Offer `--branch <name>` to push the revert to a branch and print the `gh pr create` command as the pipeline-shaped alternative. Add `ROLLBACK_PUSH_REFUSED` to the closed refusal vocabulary and state in the plan that rollback is the one deliberately pipeline-exempt path because it is an incident surface. |
| CONCERN | History & Consistency | Unit-3 accounting cannot be tagged the way the plan says. `LedgerBudgetReader.unit3_usd` sums `InfrastructureReservation` rows "whose `reason` carries the arm run id", but lane 7's `admit()` (`tools/infrastructure_budget.py:260-266`) accepts only `resource`, `project_key`, `settings`, `now` and writes `reason="admitted"` on every admitted row (`:357`). No caller can put an arm run id into `reason`, so unit 3 sums to `0.0` for both arms, `budgets_comparable` sees a match, and Risk 3's "unknown never read as zero" is violated for the one unit the plan says it can account. | Technical Approach, Budget; Data Flow comparison step 4; Risk 3; `build-recursion-core`; mutation proof `test_unit3_unknown_when_no_arm_rows` | Arm runners admit with `ResourceDecl(name=f"arm:{arm_run_id}:{resource_name}", ...)` (the row's `resource` is `resource.name`, `:352`). `LedgerBudgetReader.unit3_usd(arm_run_id)` filters `InfrastructureReservation.query.filter(project_key=...)` by `row.resource.startswith(f"arm:{arm_run_id}:")`, sums `settled_usd if settled_usd is not None else amount_usd` over `state in ("reserved", "settled")`, and returns `None` when zero rows match so the verdict carries `BUDGET_UNKNOWN:unit3`. A zero cap with zero matched rows is `ok` only when the cap is explicitly `0`, never on `None`. |
| CONCERN | History & Consistency | `research_process_digest` rejects lane 5's only spec. Lane 6 raises `ValueError` when `investigation_budget_split` sums outside `[0.99, 1.01]`, while lane 5's plan on `main` (`docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md:541-544`, `47e264f5f`) writes every revision with `investigation_budget_split={}`. An empty split sums to 0, so the one canonical function lane 5 is asked to call refuses lane 5's spec, and lane 5's fallback `tools/improvement_ranking.py::process_digest` would diverge from lane 6's on exactly the incumbent digest the comparison needs. | Technical Approach, Research process digest; Failure Path digest row; `build-recursion-core` tests | `split = spec.investigation_budget_split; unknown = set(split) - set(INVESTIGATION_KINDS); if unknown: raise ValueError(...); if split and not 0.99 <= sum(split.values()) <= 1.01: raise ValueError(...)`. Add a test that `ResearchProcessSpec(..., investigation_budget_split={})` digests to a `sha256:` string, and a second that lane 5's fixture values (`selection_rule="ordinal-lexicographic-v1"`, `extra={"ranking_module_digest": ...}`) digest identically through both functions once both exist. |
| CONCERN | History & Consistency | Arm-runner registration lives in a binary `compare run` never runs in. Lane 5 registers `PlannerArmRunner` from `tools/improvement.py`'s CLI entry, lane 3's `valor-improve` (lane 5 plan `:554-562`), while lane 6 ships `compare run` only under `valor-improve-release`, a separate binary that by design imports nothing from the research CLI. Nothing registers a runner in the process that runs `compare run`, so the production comparison is `ARM_RUNNER_ABSENT` permanently and the "Comparison refuses without an arm runner" verification row passes for the wrong reason. | Technical Approach, "What this lane assumes from lane 5"; Agent Integration; `build-comparison` tests; Verification rows "resolves a named arm runner" and "imports nothing from the research CLI at load"; Open Question 1 resolved | `compare run --arm-runner tools.improvement_plan_arm:PlannerArmRunner`, resolved with `importlib.import_module(mod)` then `getattr(mod, attr)()`; `ImportError`/`AttributeError` → `ArmRunnerAbsent("ARM_RUNNER_ABSENT", detail=str(exc))`, exit 2. When the flag is omitted, fall back to `get_arm_runner()` (the registry) so a lane-3-mounted `compare` still works. The lazy import keeps the incident binary free of the research CLI at import time, which preserves the coupling argument in Agent Integration; resolve Open Question 1 to say this. |
| NIT | Scope & Value | The Agent Integration subcommand list omits `revision supersede` (Race 2's operator remedy) and `drill --sweep` (Race 3's cleanup), so a tools-reference entry written from that list misses the two commands an operator needs during an incident. | Agent Integration subcommand list; Documentation tools-reference task | Add both to the subcommand list in Agent Integration and to the `docs/tools-reference.md` Documentation task. |
| NIT | Structural check | Update System cites `_migrate_confirm_improvement_v2_fields` at `scripts/update/migrations.py:1462` and its registration at `:1643`; on today's `main` they are `:1463` and `:1644`. Test Impact's `tests/unit/test_migrations.py` row says "whichever test enumerates `MIGRATIONS` keys"; the precedent is a per-migration registration test (`tests/unit/test_migrations.py:738-742`, `assert "<name>" in MIGRATIONS` plus `fn is _migrate_<name>`), not an enumeration. | Update System line refs; Test Impact migration row | Correct the two line numbers and name the per-migration test shape so the builder writes `test_confirm_improvement_release_lane6_fields_registered` in that pattern. |

Critique round 2, 2026-09-14, FULL depth, mode: sequential lenses (Agent tool unavailable in the stage runner's context; no finding below was independently corroborated by a second critic). Every round-1 concern and nit was verified closed against the revised text and against the cited code on `main` and lane 4's head (`fe6f55072`): the window restamp, the three drill range guards, `EVIDENCE_TRUNCATED` / `EVIDENCE_EXPIRED` with the TTL pin (`models/improvement_evidence.py:146`), the docs-only self-drill, the rollback push gate, the `ResourceDecl.name` arm prefix (`tools/infrastructure_budget.py:352,357`; `models/improvement_infrastructure_ledger.py:89-95`), the empty-split branch, the `--arm-runner` lazy import, the subcommand list, and the migration line refs. The rows below are new; two follow from lane 4's actual row shape and two from lane 5's plan revision (`c3852d389`) landing after round 1.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | `expose` stamps `exposed_at = now` and freezes the baseline "over the `baseline_window_days` before now", but exposure is the merge (Rabbit Holes: "a merged PR reaching the fleet through `/update`") and `expose` is operator-invoked. Any delay between merge and the `expose` call puts post-merge rows inside the baseline and starts the window after the change was already live, so the baseline is not frozen before exposure and the window under-measures. | pending | `gh pr view <n> --json mergeCommit,mergedAt,state`; `exposed_at = datetime.fromisoformat(mergedAt.replace("Z", "+00:00"))`; `baseline = measure(project_key, exposed_at - timedelta(days=baseline_window_days), exposed_at)`; `observation_window_ends_at = exposed_at + timedelta(days=window_days)` (the round-1 restamp, now from `mergedAt`). Refuse `EVIDENCE_EXPIRED` at `expose` when `now - (exposed_at - timedelta(days=baseline_window_days)) > timedelta(days=EVIDENCE_TTL_DAYS)`, since the baseline rows are already gone. Record `{"event": "exposed", "merged_at": ..., "expose_called_at": now}` in `outcome.history` so a late call is visible. The canned `gh pr view` JSON in the recording runner gains a `mergedAt` field; `test_expose_restamps_window_end_from_exposed_at` asserts the end equals `mergedAt + window_days`, not `call_time + window_days`. |
| CONCERN | Risk & Robustness | `rollback()` builds "a temporary worktree of `main`" from the repo the CLI runs in and pushes `HEAD:main`. On any machine whose local `main` is behind `origin/main` (the common case during an incident), the revert commits on a stale parent and the push is rejected non-fast-forward, so every re-run refuses `ROLLBACK_PUSH_REFUSED` with the same stderr until the operator notices they must fetch first. The refusal is correct; the trap is silent in the plan. | pending | In `rollback`, before the worktree: `runner(["git", "fetch", "origin", target])` where `target = branch or "main"`; then `runner(["git", "worktree", "add", "--detach", path, f"origin/{target}"])`; record `parent_sha = git rev-parse origin/<target>` in `outcome.history` beside `revert_sha`. Push `HEAD:<target>` and confirm with `git ls-remote origin refs/heads/<target>` equal to `revert_sha`. Under `--branch <name>` for a branch that does not exist on the remote, the fetch fails; fall back to `origin/main` as the parent and record `pushed_to: <name>`. The recording runner in `test_rollback_refuses_when_remote_head_differs` returns 0 for the fetch and the worktree add so the existing fixture still exercises the push gate. |
| CONCERN | History & Consistency | The plan still describes lane 5's "fallback `tools/improvement_ranking.py::process_digest`" and plans a test that lane 5's fixture digests identically through both functions, skipped by name until that module lands. Lane 5's plan on `main` was revised after round 1 (`c3852d389`): it ships `tools/improvement_ranking.py::process_spec_json(spec)` (canonical bytes only), sets `research_process_digest` solely by importing lane 6's function, and carries a Verification row asserting no `def process_digest` exists in its modules (`docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md:565-577`, `:1724`). A builder following this plan writes a test that skips forever, and the "would diverge" sentence describes a function lane 5 has committed to never writing. | pending | `pytest.importorskip("tools.improvement_ranking", reason="lane 5 (#3217) not landed")`; `from tools.improvement_ranking import process_spec_json`; `spec = ResearchProcessSpec(selection_rule="ordinal-lexicographic-v1", investigation_budget_split={}, revision_cadence_seconds=..., planner_prompt_digest="sha256:...", skill_digest="sha256:...", extra={"ranking_module_digest": "sha256:..."})`; `assert research_process_digest(spec) == "sha256:" + hashlib.sha256(process_spec_json(spec).encode("utf-8")).hexdigest()`. Keep `research_process_digest` hashing exactly `json.dumps(asdict(spec), sort_keys=True, separators=(",", ":")).encode("utf-8")`, the byte form lane 5's `test_process_spec_canonical_bytes` pins. Replace the "would diverge" sentence and the `:541-544` citation (now `:559-577`) so the plan describes lane 5 as it is on `main`. |
| CONCERN | History & Consistency | Lane 4's single evaluation writer stores `effect` and `confidence_interval` as JSON strings (`json.dumps(ctx.effect, sort_keys=True)`, `origin/session/sdlc-3216:tools/improvement_eval/runner.py:499-505`) of dicts keyed by endpoint name (`ctx.effect = {o.name: o.mean for o in outcomes}`, `:782`), and `notes` as a newline-joined string (`:508`). The plan reads `effect` as if it were a number ("the evaluation's held-out effect still positive") and writes the comparison's `effect`, `confidence_interval`, and `notes` without naming a shape, so the lineage getter, the claim report, and `claim_level_2_supported` either raise on a `str` or diverge between the two evaluator versions. | pending | In `lifecycle` (or a small `tools/improvement_release/evaluation_read.py`): `def effect_of(evaluation, endpoint): raw = evaluation.effect; d = json.loads(raw) if isinstance(raw, str) else (raw or {}); return d.get(endpoint)`; same for `confidence_interval`, whose per-endpoint value on lane 4's row is `{"lower", "upper", "n", "raw_p_value", "adjusted_p_value"}` (`runner.py:783-790`). `claim_level_2_supported` uses `effect_of(evaluation, primary_endpoint) > 0` where `primary_endpoint` comes from the frozen protocol (`load_protocol(experiment)["primary_endpoint"]`, `runner.py:197`). `compare.run` writes `effect=json.dumps({"validated_gain": mean_delta}, sort_keys=True)`, `confidence_interval=json.dumps({...}, sort_keys=True)`, and `notes="\n".join([...])` with the budget accounting as one `budget=<json>` line so `report.py` parses both evaluator versions with one function. The lineage test seeds the evaluation through lane 4's `_write_evaluation` shape, not a hand-built dict, so the reader is exercised against the real string form. |
| NIT | Risk & Robustness | `history` inside `outcome` is described as "a bounded `history` list" and the bound is never stated; `outcome` is a plain JSON field on an immortal row and `rollback_push_refused` events carry 2 KB of stderr each. | pending | Name the bound (for example `HISTORY_MAX = 50`, oldest dropped, with a `history_truncated: true` marker) so the builder does not pick one silently. |
| NIT | Scope & Value | Gate (5) raises `CANDIDATE_REF_CONFLICT` when the manifest's `base_revision` and the `--base-revision` argument disagree, so the operator reads a refusal about the candidate ref when the disagreement is about the base. Lane 5's plan on `main` (`:589-594`) now writes `candidate_ref` on the manifest as well, so a candidate-ref disagreement is a second, distinct case. | pending | Name the base disagreement `BASE_REVISION_CONFLICT`, and reserve `CANDIDATE_REF_CONFLICT` for a manifest `candidate_ref` that exists and differs from `--candidate-ref`; both stay in the closed vocabulary with one refusal test each. |
| NIT | History & Consistency | Two lane 5 citations drifted under lane 5's revision: `:541-544` (now a journal-seam table) for the empty split, and `:554-562` for the arm-runner registration (now `:579-589`). | pending | Re-point both to the "Provided to lane 6 (#3218)" section (`:554-594`) so a builder lands on the right paragraph. |

## Resolved Questions

Charter §9 rules out routine research questions to Tom, so each item carried a default the build
proceeds on. The critique round settled all four; the build follows the resolutions below and
nothing remains open.

1. **Separate binary or a mount point for lane 3.** Resolved: separate binary, with one lazy seam.
   `valor-improve-release` is its own entry point for the incident-surface reason in Agent
   Integration. Critique concern 8 showed the cost of a hard separation: lane 5 registers its
   `PlannerArmRunner` from the research CLI's process, which `compare run` never runs in. The
   resolution is `compare run --arm-runner <module>:<attr>`, resolved by `importlib` at the moment
   the subcommand executes, so the binary imports nothing from the research CLI at load (asserted by
   a Verification row) and the production comparison can still name lane 5's runner. The in-process
   registry stays for a `compare` mounted under lane 3's `valor-improve`; a
   `register_subcommands(subparsers)` seam can be added when lane 3 plans it.
2. **Should `open_pr` exist in this lane at all?** Resolved: keep it, on an `approved` release only,
   through the injectable runner; it never calls `gh pr merge`. Opening the PR is "opening a PR
   through the ordinary pipeline with its evaluation verdict, contract digest, and comparison
   attached" (parent plan), which is the merge-authority path charter §6 grants; merge stays with
   the pipeline's gates and the human-owned signals. The one pipeline-exempt path in this lane is
   `rollback`, and Data Flow step 8 says why and what `--branch` offers instead.
3. **The `held` band.** Resolved: module constants in `observation.py` with a docstring. A Wilson 95%
   interval on the baseline architectural-correction rate is the noise band, `regressed` means the
   window rate exceeds the band's upper bound, and `detection_declined` is 0.8 of baseline coverage
   per day. Nothing else in this family tunes a statistical threshold from settings, and the
   critique raised no objection.
4. **Drilling this lane's own release in the build.** Resolved: do it, against a docs-only candidate.
   The self-drill's candidate is a one-commit branch off the lane head touching only
   `docs/features/improvement-release.md`, proposed with exactly that surface, `--kind
   infrastructure`, from a seeded `accept` evaluation whose `notes` carry `lane-6-self-drill`, then
   withdrawn. Critique concern 4 showed that "surfaces = this lane's new files" would name
   `tools/improvement_release/`, which the denylist refuses; the denylist keeps that entry, and the
   self-drill proves the drill on a candidate the release policy allows.
