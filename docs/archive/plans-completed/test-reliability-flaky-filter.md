---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/476
last_comment_id:
---

# Test Reliability: Flaky Filter + Deterministic Baseline Parsing

## Problem

The test pipeline misclassifies flaky tests as regressions and relies on LLM text parsing for baseline verification despite claiming deterministic classification. This causes false-positive pipeline blocks and non-reproducible classification results.

**Current behavior:**
1. A test that fails once on the branch but would pass on retry is sent straight to baseline verification, where it passes on main and gets classified as a "regression" -- even though it is flaky and would pass on the branch too.
2. The baseline-verifier agent's Step 5 says "deterministic, no LLM judgment" but the actual mechanism is an LLM reading raw pytest verbose output and deciding per-test status. This is inherently non-deterministic.
3. No validation ensures every input test ID ends up in exactly one classification bucket. A test could silently drop out of the results if the LLM misparses the output.

**Desired outcome:**
- Flaky tests are detected via branch-side retry before baseline verification, preventing false regression classification.
- Baseline classification uses `--junitxml` structured output parsed deterministically (xml.etree.ElementTree), not LLM text interpretation.
- A completeness check guarantees every input test ID appears in exactly one of regressions, pre_existing, or inconclusive.

## Prior Art

- **Issue #363 / PR #369**: "Verify pre-existing test failures against main instead of hand-waving" -- Introduced the baseline-verifier subagent and the current classification pipeline. Succeeded in establishing the baseline comparison pattern but left the parsing as LLM-driven.
- **Issue #471 / PR #478**: Test coverage gaps -- Added nudge loop, cross-project routing, and revival path tests. Unrelated to this work but is the most recent test infrastructure change.
- Historical flaky test commits: 6bdc7dd5, ba683de9, 25e13d46 -- document recurring flaky test pain.

## Data Flow

1. **Entry point**: do-test detects test failures (pytest exit code 1)
2. **NEW - Branch-side retry**: do-test re-runs failing tests once on the branch. Pass on retry = flaky (filtered out). Fail on retry = genuine failure, proceeds to baseline verification.
3. **Baseline verifier**: Receives filtered failing test IDs, creates worktree at main, runs tests with `--junitxml=/tmp/baseline-results.xml`
4. **NEW - XML parsing**: Parse junitxml output with xml.etree.ElementTree instead of LLM interpreting raw output
5. **NEW - Completeness check**: Assert len(regressions) + len(pre_existing) + len(inconclusive) == len(input_test_ids) and no duplicates
6. **Output**: Structured JSON classification returned to do-test

## Architectural Impact

- **New dependencies**: None -- pytest junitxml is built-in, xml.etree.ElementTree is stdlib
- **Interface changes**: The baseline-verifier JSON contract gains an optional `flaky` field (array of test IDs filtered by retry). The core regressions/pre_existing/inconclusive contract is unchanged.
- **Coupling**: No change -- modifications are within the do-test skill and baseline-verifier agent specs
- **Reversibility**: Fully reversible -- these are markdown spec changes to agent/skill definitions

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

All changes are to markdown specification files (agent definition and skill definition). No Python source code modifications.

## Prerequisites

No prerequisites -- this work has no external dependencies. All capabilities (pytest junitxml, xml.etree.ElementTree) are available in the standard Python toolchain.

## Solution

### Key Elements

- **Branch-side retry in do-test**: Before dispatching baseline-verifier, re-run failing tests once on the branch. Tests that pass on retry are classified as flaky and excluded from baseline verification.
- **junitxml output in baseline-verifier**: Replace raw pytest verbose output with `--junitxml` flag. Parse the XML file deterministically instead of having the LLM interpret text.
- **Completeness assertion in baseline-verifier**: After classification, validate that the union of all buckets equals the input set with no duplicates and no missing IDs.

### Flow

**Test failures detected** -> Re-run failures on branch (retry) -> Filter flaky (pass on retry) -> Send genuine failures to baseline-verifier -> Run on main with --junitxml -> Parse XML deterministically -> Completeness check -> Return classification JSON

### Technical Approach

1. **do-test SKILL.md changes (branch-side retry)**:
   - Add a new section "Flaky Filter (Branch-Side Retry)" between "Collect Failing Test Node IDs" (Step 1) and "Dispatch Baseline Verifier" (Step 3)
   - Instruct do-test to re-run the failing test IDs with `pytest <ids> -v --tb=short`
   - Tests that PASS on retry: add to a `flaky` list, exclude from baseline verification input
   - Tests that FAIL on retry: keep in `FAILING_TEST_IDS` for baseline verification
   - Report flaky tests in the result summary as informational (not blocking)

2. **baseline-verifier.md changes (junitxml parsing)**:
   - Step 4: Add `--junitxml=/tmp/baseline-results-${TIMESTAMP}.xml` to the pytest command
   - Step 5: Replace the current "parse pytest verbose output" instruction with explicit XML parsing instructions using xml.etree.ElementTree
   - Provide the exact parsing logic: iterate `<testcase>` elements, check for `<failure>`, `<error>`, `<skipped>` child elements
   - Map XML status to classification rules (same table, deterministic implementation)

3. **baseline-verifier.md changes (completeness check)**:
   - Add a new Step 5.5 "Validate Classification Completeness" after parsing
   - Assert: `set(regressions) | set(pre_existing) | set(inconclusive) == set(failing_test_ids)`
   - Assert: no test ID appears in more than one bucket
   - If validation fails: log a warning and classify any missing tests as inconclusive

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope -- all changes are to markdown specification files

### Empty/Invalid Input Handling
- [ ] baseline-verifier Step 0 already handles empty input (returns empty classification)
- [ ] Spec must address: what if junitxml file is empty or malformed? Classify all as inconclusive.
- [ ] Spec must address: what if branch-side retry produces no output? Skip flaky filter, proceed with original list.

### Error State Rendering
- [ ] Flaky test results must appear in the do-test summary table (informational row)
- [ ] Completeness check failures must be logged visibly in raw_output

## Test Impact

No existing tests affected -- all changes are to markdown agent/skill specification files (.claude/agents/baseline-verifier.md and .claude/skills/do-test/SKILL.md). No Python source code, test files, or interfaces are modified. The specifications guide LLM agent behavior at runtime; there are no unit tests for markdown specs.

## Rabbit Holes

- **Installing pytest-rerunfailures plugin**: Tempting but unnecessary. The branch-side retry is a single re-run in the spec, not a plugin dependency. Adding a plugin would require propagating it to all environments.
- **Retry count tuning**: One retry is sufficient for flaky detection. Multiple retries add latency without meaningful signal improvement.
- **JSON report format instead of junitxml**: pytest has `--json-report` via a plugin, but junitxml is built-in and requires no new dependencies.
- **Parsing baseline pytest output with regex**: Half-measure between LLM parsing and structured XML. junitxml is strictly better.

## Risks

### Risk 1: junitxml test ID format mismatch
**Impact:** Test IDs in junitxml (`classname` + `name` attributes) may not match the pytest node ID format used by do-test (e.g., `tests/unit/test_foo.py::TestClass::test_method` vs `test_foo.TestClass.test_method`). Classification would silently fail.
**Mitigation:** The spec will include explicit instructions for reconstructing node IDs from junitxml attributes, with a mapping example. The completeness check will catch any mismatches.

### Risk 2: Branch-side retry masks real intermittent failures
**Impact:** A test that fails 50% of the time would pass the retry and be classified as "flaky" rather than investigated.
**Mitigation:** Flaky tests are reported in the summary (not silently dropped). The flaky list is visible for human review. A single retry balances signal vs pipeline speed.

## Race Conditions

No race conditions identified -- all operations are sequential within a single agent execution. The branch-side retry runs before baseline verification dispatch. The junitxml file is written and read within the same baseline-verifier execution with a unique timestamp suffix.

## No-Gos (Out of Scope)

- Persisting flaky test history across pipeline runs (future work)
- Auto-quarantining flaky tests with markers
- Fixing any actual flaky tests (this issue is about detection and classification only)
- Modifying Python source code -- all changes are spec-only

## Update System

No update system changes required -- this modifies markdown specification files only. No new dependencies, no config changes, no migration steps.

## Agent Integration

No agent integration required -- this modifies the agent specification files that already define agent behavior. The baseline-verifier agent and do-test skill are already integrated into the SDLC pipeline. The changes refine their internal instructions without adding new tools, MCP servers, or bridge code.

## Documentation

- [ ] Update `docs/features/README.md` index table to add entry for test reliability / flaky filter
- [ ] Create `docs/features/test-reliability.md` describing the flaky filter, junitxml parsing, and completeness check mechanisms

### Inline Documentation
- [ ] The modified spec files (.claude/agents/baseline-verifier.md and .claude/skills/do-test/SKILL.md) serve as their own documentation

## Success Criteria

- [ ] `.claude/skills/do-test/SKILL.md` includes branch-side retry section with clear instructions
- [ ] `.claude/agents/baseline-verifier.md` Step 4 uses `--junitxml` flag
- [ ] `.claude/agents/baseline-verifier.md` Step 5 contains deterministic XML parsing instructions (no LLM interpretation of raw output)
- [ ] `.claude/agents/baseline-verifier.md` includes completeness validation step
- [ ] Flaky tests reported in do-test summary as informational (not blocking)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (specs)**
  - Name: spec-builder
  - Role: Modify baseline-verifier.md and do-test SKILL.md specifications
  - Agent Type: builder
  - Resume: true

- **Validator (specs)**
  - Name: spec-validator
  - Role: Verify spec changes are internally consistent and complete
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add branch-side retry to do-test
- **Task ID**: build-flaky-filter
- **Depends On**: none
- **Assigned To**: spec-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "Flaky Filter (Branch-Side Retry)" section to `.claude/skills/do-test/SKILL.md` between Step 1 (Collect Failing Test Node IDs) and Step 3 (Dispatch Baseline Verifier)
- Instruct re-running failing tests once on the branch before baseline dispatch
- Add flaky test reporting to the Result Aggregation section
- Add `flaky` field to the baseline-verifier dispatch context (so verifier knows which were filtered)

### 2. Replace LLM parsing with junitxml in baseline-verifier
- **Task ID**: build-junitxml
- **Depends On**: none
- **Assigned To**: spec-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify Step 4 in `.claude/agents/baseline-verifier.md` to add `--junitxml` flag
- Rewrite Step 5 with explicit xml.etree.ElementTree parsing instructions
- Include node ID reconstruction from junitxml classname+name attributes
- Handle malformed/empty XML (classify all as inconclusive)

### 3. Add completeness validation to baseline-verifier
- **Task ID**: build-completeness
- **Depends On**: build-junitxml
- **Assigned To**: spec-builder
- **Agent Type**: builder
- **Parallel**: false
- Add Step 5.5 "Validate Classification Completeness" to `.claude/agents/baseline-verifier.md`
- Assert union of all buckets equals input set
- Assert no duplicates across buckets
- Classify missing tests as inconclusive with warning

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-flaky-filter, build-completeness
- **Assigned To**: spec-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify do-test flaky filter section is between correct steps
- Verify baseline-verifier junitxml instructions are complete and deterministic
- Verify completeness check covers all edge cases
- Verify JSON output contract includes optional `flaky` field
- Run `/do-test` to confirm no regressions

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: spec-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/test-reliability.md`
- Add entry to `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flaky filter in do-test | `grep -c "Flaky Filter" .claude/skills/do-test/SKILL.md` | output > 0 |
| junitxml in baseline-verifier | `grep -c "junitxml" .claude/agents/baseline-verifier.md` | output > 0 |
| Completeness check in baseline-verifier | `grep -c "Completeness" .claude/agents/baseline-verifier.md` | output > 0 |
| Feature docs exist | `test -f docs/features/test-reliability.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- all three fixes are well-scoped with clear implementation paths and no external dependencies.
