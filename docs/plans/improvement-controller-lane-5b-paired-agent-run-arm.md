---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3311
last_comment_id: 5685151250
revision_applied: true
revision_applied_at: 2026-09-16T09:31:00Z
---

# Improvement controller lane 5b: paired agent-run arm and the deferred evaluations

## Problem

Lane 4's harness compares retrieval parameters over a frozen memory corpus: its
arm boundary is a subprocess running one JSON job spec, and its endpoints are
ranked ids scored against gold ids. Lane 5 ran the first complete research
cycle inside that arm shape and recorded everything it could not measure as a
provisional assumption. Two measurements stayed out of reach, and both need an
arm whose candidate is behavior, not parameters.

**Current behavior:**
Lane 5's skill-acquisition investigation ends at integration with its evaluation
step honestly deferred (`deferred: no agent-run arm`): nothing in the harness
can run a complete bounded agent session as a candidate and compare it against
the prior capability. Lane 5's cheap-inference investigation resolved
`keyless_integrated` (Muse Spark 1.3 reachable through the existing OpenRouter
key) but no experiment has run an eligible open-source session on the cheap
provider against the incumbent under a frozen contract. The overturning
observation on lane 5's provisional assumption ("an agent-run paired evaluation
shows no gain") has no arm that could produce it.

**Desired outcome:**
An `agent_run` arm job mode behind the same subprocess boundary, two deferred
evaluations run end to end through it (one skill-acquisition comparison, one
cheap-inference comparison), the reuse observation recorded on the case, and a
report that states what was measured, what it does not establish, and what
would change the answer.

## Freshness Check

**Baseline commit:** `edbdc231f`
**Issue filed at:** 2026-09-14T10:53:00Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/improvement_eval/arm_worker.py` — issue cites the subprocess/JSON-spec arm boundary — holds; `JOB_MODES = ("restore", "retrieve", "digest")` at `:47`, `handle_job` at `:83`, `run_arm_job` in `tools/improvement_eval/arena.py:170`
- `tools/improvement_eval/runner.py:310` — `ARM_PARAM_KEYS = frozenset({"limit", "rrf_k", "min_rrf_score"})` — holds; `evaluate` at `:618`, Gate 0 contract-freeze at `:675-689`, Gate 1 incumbent-parity at `:759-794`
- `tools/improvement_experiment.py:81` — `ENVELOPES` admits only `retrieval_parameters` — holds
- `tools/improvement_resources.py` — `probe` with verified/absent/unknown states — holds; `RESOURCES` at `:52`
- `tools/improvement_eligibility.py:83` — `is_open_source` fails closed to client — holds; already called by `tools/improvement_eval/judges/serves_charter.py:111`
- `tools/improvement_plan_arm.py:14` — `PlannerArmRunner` docstring hands the multi-tick arm to #3311 — holds (landed after filing, see below)
- `tools/improvement_eval/calibration.py:62` — `MIN_REFERENCE_SET_SIZE = 20` — holds; the project has 0 reference items, so the serves-charter judge is uncalibrated on this machine

**Cited sibling issues/PRs re-checked:**
- #3217 (lane 5) — CLOSED 2026-09-15, squash `fb97c7e89` — the dependency this lane waited on is green
- #3216 (lane 4) — CLOSED; #3215 (lane 3) — CLOSED; #3218 (lane 6) — CLOSED

**Commits on main since issue was filed (touching referenced files):**
- `fb97c7e89` Lane 5 squash — directly relevant: shipped `PlannerArmRunner`, the `keyless_integrated` disposition (no vault wait for cheap inference), and the calibration-floor blocker. Shifts the plan's premise in two places, both recorded in Technical Approach.
- `f02683282` Lane 3 squash, `5362c7fce` Lane 4 squash — the dependency surfaces this lane builds on; confirm the merged names, do not re-derive them.

**Active plans in `docs/plans/` overlapping this area:** none — lane plans are archived; `recursive-self-improvement.md` (parent) names this lane as the home for the deferred scope, which is coordination, not overlap.

**Notes:** The issue's acceptance criterion "the cheap-inference experiment runs only after `probe` reports the credential verified" is stale: lane 5 resolved that path `keyless_integrated`, so there is no vault item for `probe` to verify. The criterion is revised to gate on the recorded disposition instead (see Solution). The serves-charter calibration floor (20 reference items, 0 present) means any verdict resting on that judge is `infra_failure`; the plan scores task rubrics as primary and records the charter leg as a known limitation (see Risks).

## Prior Art

- **PR #3337 (lane 5)**: First complete research cycle inside the retrieval-parameter arm shape. Deferred exactly this lane's scope with the `deferred: no agent-run arm` disposition and the `keyless_integrated` cheap-inference finding. Its `PlannerArmRunner` (single-tick, gains read from records only) is the closest existing "agent behavior as an arm" shape and the explicit handoff point.
- **PR #3309 (lane 4)**: The frozen-input harness this lane extends: subprocess arm boundary, writer guard, blinded envelopes, Holm correction, parity/corruption/verdict-disjointness gates. The issue requires those tests to pass unchanged.
- **#2082 / `docs/features/hybrid-retrieval-eval.md`**: The one prior paired evaluation of retrieval over this corpus, cited on every retrieval experiment. This lane's evaluations are the first paired evaluations of agent behavior; they cite #2082 as the methodological prior, not as a result.
- **Lane 6 (`tools/improvement_recursion/`, `compare run`)**: Compares research processes as arms over the same opportunities. The multi-tick dispatch-and-evaluate arm this lane builds plugs into that comparison surface.

## Research

No relevant external findings — proceeding with codebase context and training data. The work is purely internal: it extends the repo's own harness, judges, and investigation kinds, with no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

1. **Entry point**: an operator freezes a protocol naming the `agent_task` envelope, a frozen task set (task specs with rubric refs), candidate/incumbent agent manifests, and judge-scored endpoints — same `freeze_protocol` path as retrieval experiments.
2. **Runner**: `evaluate` loads the frozen experiment, resolves the judge roster (serves-charter plus frozen task rubrics), exports the corpus, and spawns two private arm Redises — unchanged through Gate 0 and the digest comparison.
3. **Arm worker**: each arm restores the frozen corpus (parity), arms the writer guard, then runs one bounded agent session per trial (`claude -p` or provider-routed) under the arm's env; the session's work lands in a scratch keyspace, never the frozen corpus.
4. **Teardown**: the worker asserts the frozen corpus digest is unchanged (a write slipped past isolation invalidates the arm), and returns per-trial outcomes plus the candidate manifest.
5. **Judges**: task rubrics score each trial outcome; serves-charter scores the charter leg (expected `infra_failure` on this machine until the 20-item floor is met — recorded, not hidden).
6. **Output**: one `ImprovementEvaluation` with verdict, per-endpoint scores with Holm correction, the arm-assignment digest, and the calibration note — the same record shape lane 5 produced.

## Architectural Impact

- **New dependencies**: none. The agent session is spawned as a subprocess (`claude -p` or the provider route lane 5's investigation already used), same as the arm worker itself.
- **Interface changes**: `JOB_MODES` gains `agent_run`; `ARM_PARAM_KEYS` gains the agent-manifest keys; `ENVELOPES` gains `agent_task`. All three are additive allowlist extensions — retrieval paths never see the new keys.
- **Coupling**: the runner learns one new trial shape (judge-scored outcomes alongside ranked ids). Scoring stays behind the existing `JudgeFn` roster; no new judge framework.
- **Data ownership**: frozen task specs live in the protocol (owned by `freeze_protocol`); trial outcomes live on the evaluation (owned by `_write_evaluation`). The scratch keyspace is arm-local and dies with the arm.
- **Reversibility**: high. The new mode is unreachable unless a protocol names it; deleting the branch removes the feature without touching retrieval behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (frozen task-set approval, report review)
- Review rounds: 2+ (harness changes carry lane 4's gates; two-judge review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane 4 merged: harness surface | `python -c "from tools.improvement_eval.runner import evaluate, capture_baseline, freeze_protocol, compute_contract_digest; from tools.improvement_eval.arena import run_arm_job"` | The calls this lane extends and the file it edits inside the harness |
| Lane 4 merged: `charter_digest` on the evaluation record | `python -c "from models.improvement_evaluation import ImprovementEvaluation as E; assert hasattr(E,'charter_digest')"` | `apply_verdict` records the charter the verdict was measured under |
| Lane 3 merged: CLI module | `python -c "import tools.improvement"` | The argparse tree the report commands attach to |
| Lane 3 merged: paid-inference meter | `python -c "import tools.paid_inference_meter"` | Agent-run spend settles against unit 2 |
| Lane 5 merged: skill disposition + cheap finding | `python -c "from tools.improvement_investigations import RESOURCE_DISPOSITIONS; assert 'keyless_integrated' in RESOURCE_DISPOSITIONS"` | The recorded inputs this lane's evaluations consume |
| `redis-server` binary | `redis-server --version` | Per-arm private Redis |
| Python pin matches the repo | `python -c "import pathlib,sys; want=pathlib.Path('.python-version').read_text().strip(); got='.'.join(map(str,sys.version_info[:3])); sys.exit(0 if got.startswith(want) else 1)"` | Worktree venv is on the committed pin |

Run all checks via `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-5b-paired-agent-run-arm.md`.

## Solution

TODO

### Key Elements

- **The `agent_run` arm job mode**: a second candidate shape behind the same subprocess boundary. Where `retrieve` restores, arms, retrieves ranked ids, and returns them, `agent_run` restores, arms, runs one bounded agent session per trial on the frozen task set, and returns judge-scored outcomes. The corpus restore and teardown digest re-check are identical, so parity, blinding, and corruption guarantees carry over structurally.
- **The frozen task set**: task specs (task id, prompt, rubric ref, spend budget) frozen in the protocol exactly like queries are today. The candidate manifest names the skill, persona, model, and prompt content hashes the arm varies; `blinding.identity_tokens` must still hold across the new fields.
- **Judge-scored endpoints**: the roster reuses the calibrated serves-charter judge and adds task-specific rubrics frozen in the protocol. Rubrics are the primary scoring leg on this machine; the charter leg's `infra_failure` (0 of 20 reference items) is recorded as a known limitation, not a verdict.
- **The skill-acquisition evaluation**: runs end to end from lane 5's recorded gap through a paired agent-run comparison against the prior capability, and records the reuse observation on the case. This overturns or confirms lane 5's provisional assumption with a real observation.
- **The cheap-inference experiment**: an eligible open-source session on the cheap provider (Muse Spark 1.3, already `keyless_integrated` through the existing OpenRouter key) versus the incumbent, under a frozen contract, with `is_open_source` checked at the call site and no client context in either arm.

### Flow

**Starting point** → operator freezes an `agent_task` protocol (tasks, manifests, rubrics, endpoints) → **runner** spawns two private arms, both restore the frozen corpus → **incumbent arm** runs the prior capability per trial, reproduces its recorded baseline → **candidate arm** runs the new behavior per trial → **judges** score every trial outcome → **statistics** apply Holm across endpoints → **end state**: one `ImprovementEvaluation` with verdict, and the case carries a reuse observation instead of a deferred disposition.

### Technical Approach

- Extend `tools/improvement_eval/arm_worker.py` with the `agent_run` mode: restore corpus, arm the writer guard, run the bounded session per trial under the arm's child env (private Redis socket, scratch content path, arm project key), assert the frozen digest unchanged at teardown, return per-trial outcomes with the manifest. Bounds (timeout, max turns, spend cap) travel in the job spec; the runner's allowlist keeps ambient config out.
- Extend `ARM_PARAM_KEYS` with exactly the agent-manifest keys (model, skill, persona, prompt hash, bounds) and `ENVELOPES` in `tools/improvement_experiment.py` with `agent_task` ranges. Retrieval keys stay refused on agent protocols and vice versa; each mode's validator rejects the other's keys.
- Adapt the runner's trial loop by branch, not by rewrite: retrieval trials keep ranked-id scoring against gold ids; agent trials score outcomes through the `JudgeFn` roster. Gate 1 for agent trials compares the incumbent's outcomes against the recorded baseline under a frozen per-task tolerance, defaulting to pass/fail agreement per task (score-within-margin is a named per-rubric extension the operator may choose at freeze time). The baseline record stores each trial's outcome plus the agreed bit; the Gate 1 check requires all(agree) and names mismatching trial ids in the evaluation note.
- Task rubrics are frozen text in the protocol, scored by a rubric judge that returns a numeric outcome per trial. serves-charter stays on the roster for the charter leg; with 0 of 20 reference items present it reports its floor refusal, which the evaluation records alongside the rubric scores. The verdict rests on rubrics; the report names the charter leg as unmeasured.
- The skill-acquisition evaluation consumes lane 5's recorded `skill_acquisition` investigation (gap, candidates, vetting, integration) and the prior capability as incumbent; on completion it writes the reuse observation to the case and clears the deferred disposition.
- The cheap-inference experiment gates on the recorded `keyless_integrated` disposition (the credential is already usable; no vault wait), checks `is_open_source` at the call site for both arms, and carries a test proving a client-keyed project is refused before any session spawns.
- Money: agent-run spend settles through lane 3's meter against unit 2 with the `arm:<arm_run_id>:` resource prefix, following the `PlannerArmRunner` convention; subscription turns and wall seconds ride the lane 6 `BudgetUse` shape. Enforcement is pre-trial reserve plus post-trial settle per task budget, with the existing `run_arm_job` timeout as the hard backstop; an over-budget trial is a harness error counting toward `infra_failure_cap`, never a scored zero. First evaluations freeze 2-4 tasks: bounded spend by default, with the supervisor confirming or overriding the weight tradeoff at freeze time.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Identify `except Exception` blocks in `arm_worker.py`, `arena.py`, and the new rubric judge — each must have a corresponding test asserting observable behavior (InfraFailure with the trial id, harness-error counting toward the cap, or a logged refusal)
- [ ] The agent-session spawn path must prove a spawn failure arrives as a harness error (excluded trial counting toward `infra_failure_cap`), never as a zero score

### Empty/Invalid Input Handling
- [ ] Document what an `agent_run` job with an empty task set, a task with a blank prompt, or a manifest missing the model does (refuse at load with InfraFailure, before any Redis spawns)
- [ ] Add tests for empty-input edge cases in the new mode validator and the rubric judge (blank outcome scores zero with a named reason, never throws)

### Error State Rendering
- [ ] Test the timed-out agent session path end to end: the trial is excluded, the cap counts it, and the evaluation record names the trial — the report shows an honest shortfall, not a silent drop
- [ ] Verify the serves-charter floor refusal renders on the evaluation as a recorded limitation alongside rubric scores, not as a crash or a missing leg

## Test Impact

- [ ] `tests/unit/test_improvement_eval_arena.py` — UPDATE: extend mode coverage with `agent_run` teardown-digest cases (a session write to the frozen corpus invalidates the arm)
- [ ] `tests/unit/test_improvement_eval_runner_guards.py` — UPDATE: new allowlist keys rejected/accepted per mode (retrieval keys refused on agent protocols and vice versa)
- [ ] `tests/unit/test_improvement_eligibility.py` — UPDATE: add the call-site refusal case (client-keyed project refused before any session spawns)
- [ ] `tests/unit/test_improvement_eval_runner.py` — UPDATE: Gate 1 tolerance branch for agent trials (baseline outcome plus agreed bit, all(agree) check naming mismatches)
- [ ] Lane 4's parity, blinding, corruption, and verdict-disjointness tests — NO CHANGE: they run unchanged as the regression net (asserted by the Verification rows below)

## Rabbit Holes

- Making agent runs deterministic (seeds, temperature zero, replay). The design accepts nondeterminism and checks tolerance instead; exact reproduction of a session is a research project, not this lane.
- A general task-spec language for frozen tasks. The protocol needs task ids, prompts, rubric refs, and budgets — a schema, not a language. Anything richer is a separate slug.
- Calibrating serves-charter as part of this lane (collecting 20 architectural corrections). That is genuine work with its own acquisition path; this lane records the limitation and scores rubrics instead.
- Rebuilding the runner's statistics for judge-scored endpoints. Holm across endpoints already applies to any endpoint list; only the per-trial scorer changes.

## Risks

### Risk 1: Agent sessions are slow and flaky; evaluations time out or burn unit 2
**Impact:** The two deferred evaluations never complete, or cost far more than budgeted.
**Mitigation:** Per-trial bounds (timeout, max turns, spend cap) frozen in the contract; small task sets (2-4 tasks) for the first evaluations; spend settles through the meter so overruns are visible before they are large.

### Risk 2: Nondeterminism makes Gate 1 parity meaningless
**Impact:** The incumbent fails its own baseline on re-run and every evaluation aborts.
**Mitigation:** Baseline comparison uses a frozen per-task tolerance (pass/fail agreement or score within margin), not byte equality; the tolerance is part of the frozen contract and the report shows the raw outcomes.

### Risk 3: Session side-effects escape the arm
**Impact:** An agent run writes to production Redis or the frozen corpus and invalidates the comparison (or worse).
**Mitigation:** The session runs under the arm's child env with a scratch keyspace; the teardown digest re-check fails the arm on any frozen-corpus write; a red-first test proves a deliberate corpus write invalidates the arm.

## Race Conditions

### Race 1: Two arms sharing one task artifact path
**Location:** arm worker `agent_run` mode, scratch content path
**Trigger:** incumbent and candidate arms run concurrently; if scratch paths collide, one arm reads the other's session artifacts.
**Data prerequisite:** each arm's scratch path must exist before its first trial runs.
**State prerequisite:** paths are unique per arm (derived from the arm socket tmpdir, which `arm_redis_server` already makes unique per arm).
**Mitigation:** derive the scratch path from the arm's own tmpdir inside the worker; assert at trial start that the path belongs to this arm; never accept a scratch path from the job spec.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3218] Any writer for `ImprovementRelease`, exposure assignment, rollback, or promotion on an `accept` verdict. This lane's evaluations record verdicts; promotion stays lane 6's.
- [SEPARATE-SLUG #3177] Calibrating the serves-charter judge (collecting the 20 architectural corrections the floor requires). This lane records the floor refusal as a limitation and scores task rubrics instead.
- [EXTERNAL] Placing any credential. The cheap-inference path is already `keyless_integrated` through the existing OpenRouter key; if a vaulted credential is ever needed, Tom places it in `m-valor`. No controller module writes `.env` or invokes `op` for writes.
- [ORDERED] Amending `docs/improvement-charter.md` or `models/improvement_charter.py`. The session may `propose-amendment`; only Tom authorizes.
- [ORDERED] Turning `IMPROVEMENT__ENABLED` on in any machine's `.env`. Evaluation runs carry the switch in the invoking shell; leaving the loop on is an operating decision Tom makes after reading the report.

## Update System

No update system changes required — this feature is purely internal to the improvement controller. No new dependencies, no new config files, no migration steps. The Popoto models touched (evaluation records, case observations) use existing fields; if the build finds a new field is needed, the plan is revised to add a migration function in `scripts/update/migrations.py` before implementation.

## Agent Integration

No new CLI entry point and no bridge changes. The new capability is reachable through the existing surfaces: `valor-improve` subcommands for freezing and reporting (extended additively), and lane 6's `compare run --arm-runner` for the multi-tick comparison arm. Integration tests verify an operator can freeze an `agent_task` protocol and read its evaluation through the CLI.

## Documentation

- [ ] Create `docs/features/agent-run-arm.md` describing the agent_run arm mode, the frozen task-set contract, and how to read its report
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update the lane 5 archive plan's No-Gos section? No — archive plans are history; the tracking link on #3311 carries the handoff. (Recorded here so the builder does not "fix" the archive.)

## Success Criteria

- [ ] An `agent_run` arm job mode exists behind the same subprocess boundary, and lane 4's parity, blinding, corruption, and verdict-disjointness tests still pass unchanged
- [ ] One skill-acquisition evaluation runs end to end from lane 5's recorded gap through a paired agent-run comparison, and the reuse observation is recorded on the case
- [ ] The cheap-inference experiment runs gated on the recorded `keyless_integrated` disposition, and a test proves a client-keyed project is refused at the call site
- [ ] The report states what was measured, what it does not establish, and what would change the answer
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (arm-mode)**
  - Name: arm-builder
  - Role: `agent_run` job mode in the arm worker, scratch keyspace, teardown digest, red-first invalidation test
  - Agent Type: builder
  - Resume: true

- **Validator (arm-mode)**
  - Name: arm-validator
  - Role: Verify the new mode against lane 4's unchanged gates (parity, blinding, corruption, verdict-disjointness)
  - Agent Type: validator
  - Resume: true

- **Builder (runner-evaluations)**
  - Name: eval-builder
  - Role: Runner trial branch for agent tasks, Gate 1 tolerance, rubric roster, the two deferred evaluations, call-site eligibility refusal
  - Agent Type: builder
  - Resume: true
  - Domain: Redis/Popoto data

- **Validator (runner-evaluations)**
  - Name: eval-validator
  - Role: Verify both evaluations end to end, the reuse observation lands on the case, and the report states its limits
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `test-engineer` - Test implementation and strategy
- `documentarian` - Documentation updates

**Domain expertise (no dedicated agent — prompt a Tier 1 agent):**
There is no standing pool of "specialist" agents. For domain-specific work, assign a `builder` (or `code-reviewer` for review-only work), add a `Domain: <tag>` line to the task, and paste the matching rules from `DOMAIN_FRAMING.md` into the task's assignment.

## Step by Step Tasks

### 1. Arm worker agent_run mode
- **Task ID**: build-arm-mode
- **Depends On**: none
- **Validates**: tests/unit/test_improvement_eval_arena.py (extended), new tests/unit/test_improvement_eval_agent_run.py (create)
- **Assigned To**: arm-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `agent_run` to `JOB_MODES`; validate the task set and manifest at load (refuse before any Redis spawns)
- Run one bounded session per trial under the arm child env with a scratch keyspace derived from the arm tmpdir
- Assert the frozen corpus digest unchanged at teardown; return per-trial outcomes with the manifest
- Red-first: a deliberate frozen-corpus write invalidates the arm

### 2. Validate arm mode against lane 4 gates
- **Task ID**: validate-arm-mode
- **Depends On**: build-arm-mode
- **Assigned To**: arm-validator
- **Agent Type**: validator
- **Parallel**: false
- Run lane 4's parity, blinding, corruption, and verdict-disjointness suites unchanged; all green
- Mutate the teardown check (remove the digest assert) and prove the red test catches it

### 3. Runner branch, envelopes, rubrics, both evaluations
- **Task ID**: build-evaluations
- **Depends On**: validate-arm-mode
- **Validates**: tests/unit/test_improvement_eval_runner_guards.py (extended), tests/unit/test_improvement_eligibility.py (extended), new rubric-judge tests (create)
- **Assigned To**: eval-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `ARM_PARAM_KEYS` and `ENVELOPES` with the agent keys; mode validators refuse each other's keys
- Branch the trial loop: agent trials score through the `JudgeFn` roster; Gate 1 uses the frozen per-task tolerance
- Run the skill-acquisition evaluation end to end; record the reuse observation on the case
- Run the cheap-inference experiment gated on `keyless_integrated`; prove client-keyed refusal at the call site
- Settle spend through the meter with the `arm:<arm_run_id>:` prefix

### 4. Validate evaluations and report
- **Task ID**: validate-evaluations
- **Depends On**: build-evaluations
- **Assigned To**: eval-validator
- **Agent Type**: validator
- **Parallel**: false
- Both evaluations ran end to end; reuse observation is on the case; the report states measured, unestablished, and answer-changing conditions
- serves-charter floor refusal is recorded as a limitation, not hidden

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-evaluations
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-run-arm.md`
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: eval-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lane 4 arena tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_arena.py -q` | exit code 0 |
| Lane 4 blinding tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_blinding.py -q` | exit code 0 |
| Lane 4 calibration tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_calibration.py -q` | exit code 0 |
| Mode validators refuse cross-mode keys | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_runner_guards.py -q` | exit code 0 |
| Lane 4 runner tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_runner.py -q` | exit code 0 |
| Lane 4 corpus tests still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_eval_corpus.py -q` | exit code 0 |
| Client-keyed refusal at call site | `scripts/pytest-clean.sh tests/unit/test_improvement_eligibility.py -q` | exit code 0 |
| No ImprovementRelease writer in this lane | `grep -rn "ImprovementRelease(" tools/improvement_eval/arm_worker.py tools/improvement_experiment.py \| wc -l` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Gate 1 tolerance undecided: pass/fail vs margin parked in Open Questions while Task 3 needs the baseline record shape now | rev1: pass/fail agreement frozen as default in Technical Approach, margin as named per-rubric extension | Freeze pass/fail agreement per task as default; baseline stores per-trial outcome plus agreed bit; Gate 1 requires all(agree) and names mismatching trial ids |
| CONCERN | Risk & Robustness | Spend-cap enforcement point missing: bounds travel in the job spec but nothing enforces spend mid-session | rev1: pre-trial reserve plus post-trial settle added to Technical Approach money bullet | Pre-trial meter reserve plus post-trial settle on the arm:<arm_run_id>: prefix; run_arm_job timeout_s is the hard backstop; over-budget trial is a harness error toward the cap |
| CONCERN | History & Consistency | Success criterion 1 names parity/corruption/disjointness suites but the Verification table runs only arena/blinding/calibration/runner_guards; Test Impact omits the runner suite Task 3 branches | rev1: runner plus corpus rows added to Verification; runner suite listed as UPDATE in Test Impact | Add scripts/pytest-clean.sh tests/unit/test_improvement_eval_runner.py -q and test_improvement_eval_corpus.py -q rows (exit code 0); list the runner suite as UPDATE in Test Impact |
| NIT | Scope & Value | Risk 1 assumes 2-4 tasks while Open Question 1 asks whether small is right | rev1: 2-4 tasks stated as frozen default; question reframed as confirm-or-override | State 2-4 tasks as the frozen default in Technical Approach; reframe the question as confirm-or-override |


## Open Questions

1. Task-set size for the first evaluations (default frozen at 2-4 tasks per Technical Approach): does the supervisor confirm the bounded-spend default, or override toward more statistical weight for the first run?
2. Gate 1 tolerance metric: pass/fail agreement per task is simple and robust; score-within-margin preserves more signal but needs a margin per rubric. Which should the frozen contract carry?
