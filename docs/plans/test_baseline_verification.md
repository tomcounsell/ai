---
status: Shipped
type: feature
appetite: Medium
owner: Valor
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/363
last_comment_id:
---

# Test Baseline Verification

## Problem

During the `/do-test` phase, when tests fail on a feature branch, the agent frequently claims "X failures are pre-existing on main" -- but **never actually verifies this**. The do-test skill (`SKILL.md`) has zero logic for baseline comparison. The claim is pure LLM improvisation, which means:

1. **Regressions slip through** -- a test broken by the branch gets dismissed as pre-existing
2. **No confidence** -- the human reviewer cannot trust the claim without manually checking
3. **Flaky tests get a free pass** -- tests that pass on main but intermittently fail get lumped in as "pre-existing"

Additionally, when regressions are found, the current pipeline can get stuck in a test-then-patch-then-test loop with no escape. The agent needs a circuit breaker to escalate back to planning when fixes are not converging.

**Current behavior:**
When tests fail on a feature branch, the agent eyeballs the failures, invents a classification ("these look pre-existing"), and reports it as fact. There is no baseline comparison, no verification, and no structured output distinguishing regressions from pre-existing failures.

**Desired outcome:**
Test failures are classified by running the failing tests against `main`, producing a verified breakdown table. Regressions must be fixed; pre-existing failures are reported but do not block. If regression fixes do not converge after 3 attempts, the pipeline escalates to planning instead of spinning.

## Prior Art

No prior issues found related to baseline verification or regression circuit breakers. The closest related work:

- **Typed Skill Outcomes** (shipped): Established the `<!-- OUTCOME {...} -->` contract that do-test already emits. The baseline verifier extends this contract with new artifact fields (`regressions`, `pre_existing`, `regression_fix_attempt`).
- **do-patch skill** (shipped): Already has an `ITERATION_CAP` mechanism (default 3). The circuit breaker proposed here is complementary -- do-patch caps individual fix attempts, while the regression counter caps the test-patch-test loop across invocations.

## Data Flow

1. **Entry point**: `/do-test` runs `pytest` and collects failing test node IDs
2. **Baseline verifier subagent**: Receives failing test node IDs, creates a temporary git worktree at `main` HEAD, runs only the failing tests against main, classifies each as regression/pre-existing/inconclusive
3. **Classification return**: Subagent returns structured JSON with `baseline_commit`, `regressions`, `pre_existing`, `inconclusive`, and `raw_output`
4. **do-test integration**: Incorporates classification into the results table, adjusts OUTCOME status (`fail` only if regressions exist, `partial` if only pre-existing), and includes regression counter in artifacts
5. **Observer routing**: Observer reads the OUTCOME. If `status: fail` with regressions, routes to `/do-patch`. If `status: blocked` with `next_skill: /do-plan`, routes back to planning with escalation context
6. **Circuit breaker**: do-test reads `regression_fix_attempt` from prior OUTCOME artifacts (passed through by the Observer). After 3 attempts with the same persistent regressions, emits `status: blocked` instead of `status: fail`

## Architectural Impact

- **New dependencies**: None -- uses only `git worktree` and `pytest` via bash
- **Interface changes**: do-test OUTCOME artifacts gain new fields (`regressions`, `pre_existing`, `inconclusive`, `regression_fix_attempt`, `max_regression_fix_attempts`, `persistent_regressions`, `baseline_commit`). These are additive -- existing consumers that do not read these fields are unaffected
- **Coupling**: The baseline-verifier subagent is loosely coupled. do-test dispatches it and receives structured JSON. The verifier has no knowledge of the broader pipeline
- **Data ownership**: do-test owns failure classification. The subagent is a delegate, not a new owner
- **Reversibility**: Fully reversible -- remove the subagent definition and the dispatch block from do-test, and the system reverts to unverified classification

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on classification rules and escalation thresholds)
- Review rounds: 1 (code review of skill/agent definitions)

The implementation is skill/agent markdown definitions plus OUTCOME format extensions. No Python code changes. The main complexity is getting the classification rules and escalation flow right.

## Prerequisites

No prerequisites -- this work has no external dependencies. It uses `git worktree` (available in standard git) and `pytest` (already a project dependency).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| git worktree support | `git worktree list` | Temporary worktree for baseline runs |
| pytest available | `pytest --version` | Test execution in worktree |

## Solution

### Key Elements

- **Baseline Verifier Subagent**: New agent definition (`.claude/agents/baseline-verifier.md`) that receives failing test node IDs, creates a temporary worktree at main, runs only those tests, and returns classified results
- **do-test Failure Baseline Verification section**: New section in do-test SKILL.md that dispatches the subagent after collecting failures and integrates results into the report
- **Regression Circuit Breaker**: Counter tracking in OUTCOME artifacts that escalates to `/do-plan` after 3 failed fix attempts for the same regressions
- **Verified Output Format**: Structured failure classification table replacing the vague "X pre-existing" claims

### Flow

**do-test detects failures** -> dispatch baseline-verifier subagent with failing test IDs -> **subagent creates worktree at main** -> runs failing tests against main -> **classifies each failure** -> returns structured JSON -> **do-test integrates results** -> emits OUTCOME with classification and regression counter -> **Observer routes** based on status (fail/partial/blocked)

### Technical Approach

- The subagent uses `git worktree add /tmp/baseline-verify-<timestamp> main` for isolation. The worktree is always cleaned up (`git worktree remove`) in a finally-equivalent block
- Only failing tests are run against main (not the full suite), keeping verification fast
- Classification is deterministic: branch-FAIL + main-FAIL = pre-existing, branch-FAIL + main-PASS = regression, branch-FAIL + main-ERROR/SKIP = inconclusive
- The regression counter persists via OUTCOME artifacts passed between skill invocations. The counter resets when the set of regressions changes (different test IDs = new problem = reset counter)
- The `blocked` status with `next_skill: /do-plan` is already supported by the Observer routing logic (see typed-skill-outcomes.md)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The baseline-verifier subagent must handle `git worktree add` failures (e.g., worktree already exists) gracefully and return an inconclusive classification rather than crashing
- [ ] `pytest` execution errors in the worktree (exit code 2) must be caught and classified as inconclusive, not treated as passes

### Empty/Invalid Input Handling
- [ ] If do-test passes an empty list of failing test IDs to the verifier, it should return immediately with an empty classification (no worktree created)
- [ ] If all tests in the failing list no longer exist on main (file deleted), classify as inconclusive

### Error State Rendering
- [ ] The verification results table must render correctly even with zero regressions, zero pre-existing, or zero inconclusive results
- [ ] The escalation OUTCOME must include enough context for `/do-plan` to present a meaningful question to the human

## Rabbit Holes

- **Running full test suite on main**: Only run the specific failing tests. Running the full suite would be slow and unnecessary
- **Caching verification results across sessions**: Results are only valid within a single pipeline run. Main moves forward between sessions
- **Auto-fixing pre-existing failures**: Out of scope. The verifier classifies; it does not fix
- **Flaky test detection via repeated runs**: Tempting but would triple verification time. A single main run is sufficient for the 3-way classification
- **Making max_regression_fix_attempts configurable**: Hardcode to 3. Adding configuration adds surface area without clear benefit

## Risks

### Risk 1: Worktree cleanup failure
**Impact:** Orphaned worktrees in `/tmp` consuming disk space
**Mitigation:** The subagent instructions include explicit cleanup. Additionally, `/tmp` is cleared on reboot. Add a defensive `git worktree prune` at the start of verification

### Risk 2: Tests behave differently in worktree due to missing env/config
**Impact:** False classifications (test passes on main in normal repo but fails in worktree due to missing `.env`)
**Mitigation:** The subagent copies essential config files (`.env`) into the worktree before running tests. Document this in the agent definition

### Risk 3: Observer does not handle `blocked` status correctly
**Impact:** Pipeline gets stuck instead of escalating to planning
**Mitigation:** Verify Observer routing logic handles `blocked` + `next_skill` before deploying. The typed-skill-outcomes doc says ambiguous statuses fall through to LLM Observer, which should handle it

## Race Conditions

No race conditions identified. The baseline-verifier creates an isolated worktree and runs tests sequentially. The worktree is created at a specific commit (main HEAD at invocation time) and is not affected by concurrent operations on other branches. The OUTCOME artifacts are passed as structured data, not shared mutable state.

## No-Gos (Out of Scope)

- Do NOT run the full test suite on main -- only the specific failing tests
- Do NOT do the verification inline in do-test -- use the subagent to protect context
- Do NOT let the agent retry more than 3 times -- escalate, do not spin
- Do NOT auto-skip or auto-delete failing tests as a "fix"
- Do NOT cache verification results across sessions -- only within a single pipeline run
- Do NOT modify the Observer or bridge Python code -- this is purely skill/agent definition work
- Do NOT add flaky test detection or retry logic

## Update System

No update system changes required -- this is a skill/agent definition change with no new dependencies, config files, or migration steps.

## Agent Integration

- New subagent definition (`.claude/agents/baseline-verifier.md`) must be added. This agent type is dispatched by do-test via the Task tool, not invoked by users or the bridge directly
- No new MCP servers or tools needed -- the subagent uses only git and pytest via bash
- No changes to `.mcp.json`
- No changes to `bridge/telegram_bridge.py`
- Integration testing: run `/do-test` on a branch with known regressions and verify the classification table appears in the output with correct verdicts

## Documentation

### Feature Documentation
- [ ] Create `docs/features/test-baseline-verification.md` describing the verification flow, classification rules, and escalation mechanism
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Add inline comments in `.claude/skills/do-test/SKILL.md` explaining the baseline verification dispatch and circuit breaker logic
- [ ] Add inline comments in `.claude/agents/baseline-verifier.md` explaining classification rules and worktree management

## Success Criteria

- [ ] `.claude/agents/baseline-verifier.md` exists with complete agent definition including worktree management, test execution, and classification logic
- [ ] `.claude/skills/do-test/SKILL.md` includes "Failure Baseline Verification" section that dispatches the subagent and integrates results
- [ ] `.claude/skills/do-test/SKILL.md` includes regression counter tracking in the OUTCOME contract
- [ ] do-test OUTCOME format includes `regressions`, `pre_existing`, `inconclusive`, `baseline_commit`, and `regression_fix_attempt` artifact fields
- [ ] OUTCOME status is `fail` only when regressions exist; `partial` when only pre-existing failures remain
- [ ] After 3 failed regression fix attempts, OUTCOME emits `status: blocked` with `next_skill: /do-plan`
- [ ] Verified output format includes the classification table with test-level verdicts
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (baseline-verifier-agent)**
  - Name: agent-builder
  - Role: Create the baseline-verifier agent definition
  - Agent Type: builder
  - Resume: true

- **Builder (do-test-integration)**
  - Name: skill-builder
  - Role: Modify do-test SKILL.md with verification dispatch and circuit breaker
  - Agent Type: builder
  - Resume: true

- **Validator (verification-flow)**
  - Name: flow-validator
  - Role: Verify the end-to-end data flow from failure detection through classification to OUTCOME emission
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create baseline-verifier agent definition
- **Task ID**: build-agent
- **Depends On**: none
- **Assigned To**: agent-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/agents/baseline-verifier.md` with:
  - Worktree creation at main HEAD (`git worktree add /tmp/baseline-verify-<timestamp> main`)
  - Defensive `git worktree prune` at start
  - Copy `.env` to worktree if it exists
  - Run only specified failing test node IDs: `cd /tmp/baseline-verify-<ts> && pytest <test-ids> -v --tb=short`
  - Parse pytest output to determine pass/fail/error/skip per test
  - Apply classification rules (branch-FAIL + main-FAIL = pre-existing, etc.)
  - Return structured JSON with `baseline_commit`, `regressions`, `pre_existing`, `inconclusive`, `raw_output`
  - Always clean up worktree in a finally block (`git worktree remove /tmp/baseline-verify-<ts>`)

### 2. Modify do-test SKILL.md with baseline verification
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "Failure Baseline Verification" section after "Result Aggregation" with:
  - When test failures are detected, collect all failing test node IDs
  - Dispatch baseline-verifier subagent via Task tool with failing IDs
  - Wait for structured JSON response
  - Integrate classification into results table
  - Replace vague claims with verified classification table
- Add regression counter tracking:
  - Read `regression_fix_attempt` from previous OUTCOME context if available
  - Increment counter if same regressions persist
  - Reset counter if regression set changes
  - Emit `status: blocked` after 3 attempts with `next_skill: /do-plan`
- Update OUTCOME contract section with new artifact fields
- Update status rules: `fail` only for regressions, `partial` for pre-existing only

### 3. Validate end-to-end flow
- **Task ID**: validate-flow
- **Depends On**: build-agent, build-skill
- **Assigned To**: flow-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify baseline-verifier agent definition is complete and self-contained
- Verify do-test SKILL.md correctly dispatches the subagent and handles all classification outcomes
- Verify OUTCOME format changes are backward compatible (new fields are additive)
- Verify the escalation flow: 3 attempts -> blocked status -> next_skill /do-plan
- Verify the regression counter reset logic (different test IDs = reset)
- Check that Observer can parse the new OUTCOME fields (additive = should work)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-flow
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/test-baseline-verification.md`
- Add entry to `docs/features/README.md` index table (maintain alphabetical order)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: flow-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Agent definition exists | `test -f .claude/agents/baseline-verifier.md && echo OK` | output contains OK |
| do-test has verification section | `grep -c "Baseline Verification" .claude/skills/do-test/SKILL.md` | output > 0 |
| do-test has circuit breaker | `grep -c "regression_fix_attempt" .claude/skills/do-test/SKILL.md` | output > 0 |
| OUTCOME has regression fields | `grep -c "regressions" .claude/skills/do-test/SKILL.md` | output > 0 |
| Feature doc exists | `test -f docs/features/test-baseline-verification.md && echo OK` | output contains OK |
| Feature index updated | `grep -c "Test Baseline Verification" docs/features/README.md` | output > 0 |
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

---

## Open Questions

1. **Escalation routing**: ~~The typed-skill-outcomes doc says the Observer falls through to the LLM for ambiguous statuses like `blocked`. Should we add explicit `blocked` handling to the Observer Python code, or is the LLM fallback sufficient for now?~~ **Resolved**: LLM fallback is sufficient for now -- may already work. Make escalation OUTCOME notes explicit enough (include "route to /do-plan" language) so the LLM Observer routes correctly. Verify with dry runs after implementation. No Observer Python changes.

2. **Counter persistence mechanism**: ~~This requires the Observer to forward the previous OUTCOME context when re-invoking `/do-test`. Is this already how the Observer works?~~ **Resolved**: Claude Sonnet can read the prior `<!-- OUTCOME -->` block directly from conversation history. No explicit Observer wiring needed. Instruct do-test to scan conversation context for the most recent OUTCOME block to extract `regression_fix_attempt`.

3. **Worktree environment fidelity**: ~~Are there other environment/config files beyond `.env` needed?~~ **Resolved**: Copy only `.env`. SOUL.md and other identity-related configs are being moved to env config, so `.env` copy covers all runtime needs.
