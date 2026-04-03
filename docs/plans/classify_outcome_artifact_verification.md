---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/665
last_comment_id:
---

# classify_outcome Must Parse OUTCOME Contracts, Not Just Text Patterns

## Problem

When issues #653-657 were processed in parallel, the REVIEW stage was marked "completed" on all 5 PRs without a single actual GitHub PR review being posted. The `classify_outcome()` method in `bridge/pipeline_state.py` uses naive text patterns (`"approved" in tail`) that match agent chatter instead of structured output.

Meanwhile, the `/do-pr-review` skill already emits a structured OUTCOME contract:
```
<!-- OUTCOME {"status":"success|partial|fail","stage":"REVIEW","artifacts":{...}} -->
```

But `classify_outcome()` completely ignores it. The structured signal is there — we just don't read it.

**Secondary problem:** The text pattern `"approved" in tail` treats GitHub approval as REVIEW success. But our SDLC principle says tech debt and nits must also be addressed — "approved" is not enough. The OUTCOME contract already distinguishes `"success"` (zero findings) from `"partial"` (approved but has findings), enabling the `("REVIEW", "partial"): "PATCH"` edge in `pipeline_graph.py`. That edge never fires because classify_outcome can't detect "partial".

**Design principle:** We incentivize and instruct agents towards good actions, not block or create friction. The fix is to parse what agents already report, not add a verification gate that overrides their decisions.

## Prior Art

- **Issue #563 / PR #601**: "SDLC pipeline graph routing not wired into runtime" — Wired `classify_outcome()` into the runtime pipeline via `subagent_stop.py`. Made classify_outcome live code instead of dead code.
- **PR #433**: "Replace inference-based stage tracking with PipelineStateMachine" — Created the state machine and `classify_outcome()` method. Established the text-pattern approach.
- **Issue #463 / PR #472**: "Add CRITIQUE stage to SDLC pipeline" — Added CRITIQUE patterns to classify_outcome.

## Data Flow

1. **Dev-session runs** a skill (e.g., `/do-pr-review`)
2. **Skill emits** an OUTCOME contract as the last line of output: `<!-- OUTCOME {"status":"partial","stage":"REVIEW",...} -->`
3. **subagent_stop.py** `_extract_output_tail()`: Reads last ~500 chars from agent transcript
4. **subagent_stop.py** `_record_stage_on_parent()`: Calls `sm.classify_outcome(stage, stop_reason, output_tail)`
5. **pipeline_state.py** `classify_outcome()`: **Currently ignores the OUTCOME block**, matches text patterns → returns "success" when it should return "partial"
6. **subagent_stop.py**: Routes to `complete_stage()` (wrong) instead of `fail_stage()` (which handles partial → PATCH routing)

The fix is at step 5: parse the OUTCOME contract first, fall back to text patterns only when no contract is found.

## Architectural Impact

- **No new dependencies**: JSON parsing of an HTML comment already in the output
- **Interface changes**: `classify_outcome()` signature unchanged. Internal behavior adds Tier 0 (OUTCOME parsing) before existing tiers
- **No subprocess calls**: Unlike the prior plan, this approach requires zero `gh` CLI calls — the structured data is already in the output
- **Reversibility**: Easy — remove the OUTCOME parsing and fall back to current text-pattern behavior
- **Extensibility**: As other skills adopt the OUTCOME contract, classification automatically improves without code changes

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — the OUTCOME contract format is already emitted by `/do-pr-review`.

## Solution

### Key Elements

1. **Tier 0 — OUTCOME contract parsing**: Before any text pattern matching, scan `output_tail` for `<!-- OUTCOME {...} -->` and parse the JSON. If found, use `status` directly ("success", "partial", "fail")
2. **Preserve existing tiers as fallback**: Tier 1 (stop_reason) and Tier 2 (text patterns) remain for skills that don't yet emit OUTCOME contracts
3. **Extend OUTCOME contract to BUILD and TEST skills**: Add OUTCOME emission to `/do-build` and `/do-test` skills so they also report structured outcomes

### Flow

```
Stage completes → Tier 0 (OUTCOME contract) → Tier 1 (stop_reason) → Tier 2 (text patterns) → "ambiguous"
```

If Tier 0 finds a valid OUTCOME block, it returns immediately — no text pattern matching needed.

### Technical Approach

**In `pipeline_state.py`:**
- Add `_parse_outcome_contract(output_tail: str) -> dict | None` — regex for `<!-- OUTCOME ({...}) -->`, parse JSON, return dict or None
- In `classify_outcome()`, call `_parse_outcome_contract()` first. If it returns a valid dict with `status`, return that status directly
- Validate that parsed `stage` matches the expected stage (guard against mismatched contracts)

**In skill definitions:**
- `/do-build` (`SKILL.md`): Add OUTCOME contract emission (status based on whether PR was created)
- `/do-test` (`SKILL.md`): Add OUTCOME contract emission (status based on test pass/fail counts)
- `/do-docs` (if applicable): Add OUTCOME contract emission

**Why this is better than artifact verification:**
- Zero latency: no subprocess calls, no network requests, no timeouts
- Respects agent autonomy: the agent reports its own assessment via structured data
- "Partial" flows naturally: REVIEW with findings → "partial" → PATCH cycle → re-REVIEW
- Extensible: any skill can adopt the contract without classify_outcome code changes

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_parse_outcome_contract()` returns None on malformed JSON (graceful fallback to Tier 1/2)
- [ ] `_parse_outcome_contract()` returns None on missing `status` key
- [ ] `_parse_outcome_contract()` returns None when no OUTCOME block exists in output

### Empty/Invalid Input Handling
- [ ] Test with empty output_tail — falls through to Tier 2
- [ ] Test with OUTCOME block containing unexpected `stage` — falls through to Tier 2
- [ ] Test with multiple OUTCOME blocks — uses the last one (most recent)

### Error State Rendering
- [ ] Not applicable — classify_outcome has no user-visible output

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_end_turn_with_test_pass` — UPDATE: add parallel test with OUTCOME contract
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_end_turn_build_with_pr` — UPDATE: add parallel test with OUTCOME contract
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_none_stop_reason_uses_patterns` — no change (tests Tier 2 fallback)
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_critique_ready_to_build_is_success` — no change (CRITIQUE doesn't use OUTCOME yet)
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_critique_needs_revision_is_fail` — no change

## Rabbit Holes

- Adding `gh` CLI subprocess calls for artifact verification — the OUTCOME contract already provides the signal without network calls
- Building a generic artifact registry — unnecessary when structured output parsing suffices
- Refactoring `_infer_stage_from_artifacts()` — that method serves display progress inference, not outcome classification
- Making OUTCOME emission mandatory for all skills immediately — adopt incrementally, text patterns are a fine fallback

## Risks

### Risk 1: Agent doesn't emit OUTCOME block
**Impact:** Falls through to existing Tier 1/2 behavior (no regression)
**Mitigation:** Text patterns remain as fallback. Monitor adoption rate and add OUTCOME contracts to skills incrementally.

### Risk 2: Agent emits incorrect OUTCOME status
**Impact:** Pipeline trusts the agent's self-report, could route incorrectly
**Mitigation:** This is the intended design — we trust and nudge agents, not gate them. The skill instructions are explicit about when to use "partial" vs "success". If an agent misreports, the fix is in the skill prompt, not in adding verification.

## Race Conditions

No race conditions — `classify_outcome()` is called synchronously within `_record_stage_on_parent()`, and OUTCOME parsing is a pure string operation.

## No-Gos (Out of Scope)

- Adding subprocess calls to `classify_outcome()` for artifact verification — contradicts the "nudge, don't gate" principle
- Requiring all skills to emit OUTCOME contracts before shipping — incremental adoption is fine
- Changing the OUTCOME contract format — the existing format works
- Modifying `_infer_stage_from_artifacts()` — different purpose (display progress)

## Update System

No update system changes required — this is a bridge-internal change to `pipeline_state.py` and skill prompt updates with no new dependencies or configuration.

## Agent Integration

No MCP server changes needed. The OUTCOME contract is emitted by skills (prompt-level) and parsed by `classify_outcome()` (Python-level). The agent integration is implicit: skills are already instructed to emit OUTCOME blocks, and this change makes the pipeline actually read them.

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` to document the three-tier classification approach (Tier 0: OUTCOME contract, Tier 1: stop_reason, Tier 2: text patterns)
- [ ] Document the OUTCOME contract format in `docs/features/pipeline-state-machine.md` so future skill authors know how to emit it

## Success Criteria

- [ ] `classify_outcome()` parses `<!-- OUTCOME {...} -->` blocks from output_tail before falling back to text patterns
- [ ] REVIEW stage returning "partial" (tech_debt/nits found) correctly triggers PATCH cycle via pipeline graph
- [ ] Malformed or missing OUTCOME blocks gracefully fall through to existing Tier 1/2 behavior
- [ ] `/do-build` skill emits OUTCOME contract on completion
- [ ] `/do-test` skill emits OUTCOME contract on completion
- [ ] All existing `TestClassifyOutcome` tests still pass (no regressions)
- [ ] New tests cover OUTCOME parsing: valid, malformed, missing, stage mismatch, and "partial" status
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (outcome-parsing)**
  - Name: outcome-builder
  - Role: Implement _parse_outcome_contract() and integrate into classify_outcome(), add OUTCOME contracts to skills
  - Agent Type: builder
  - Resume: true

- **Validator (outcome-parsing)**
  - Name: outcome-validator
  - Role: Verify implementation meets all success criteria
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tier 1 agents sufficient for this work.

## Step by Step Tasks

### 1. Add _parse_outcome_contract() and integrate as Tier 0
- **Task ID**: build-outcome-parsing
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_parse_outcome_contract(output_tail: str) -> dict | None` as a module-level function
- Regex: `r'<!-- OUTCOME (\{.*?\}) -->'` — extract last match, parse JSON
- Return parsed dict if valid JSON with `status` key, else None
- In `classify_outcome()`, call `_parse_outcome_contract(output_tail)` before Tier 1
- If parsed and `status` is in ("success", "fail", "partial"), return it immediately
- If parsed `stage` doesn't match expected stage, log warning and fall through to Tier 1/2
- Log the tier used: `logger.info(f"classify_outcome({stage}): OUTCOME contract -> {status}")`

### 2. Add OUTCOME contract to /do-build and /do-test skills
- **Task ID**: build-skill-contracts
- **Depends On**: none (parallel with task 1)
- **Validates**: manual review of skill files
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: true
- Add OUTCOME contract emission instructions to `.claude/skills/do-build/SKILL.md`
  - success: PR created, tests not yet run
  - fail: build failed, no PR created
- Add OUTCOME contract emission instructions to `.claude/skills/do-test/SKILL.md`
  - success: all tests passed
  - fail: test failures found
  - partial: tests passed but with warnings/flaky tests

### 3. Write tests for OUTCOME parsing
- **Task ID**: build-tests
- **Depends On**: build-outcome-parsing
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: valid OUTCOME block → returns parsed status
- Test: REVIEW with "partial" status → returns "partial"
- Test: malformed JSON in OUTCOME block → falls through to Tier 2
- Test: missing status key → falls through to Tier 2
- Test: no OUTCOME block → falls through to Tier 2
- Test: stage mismatch (OUTCOME says BUILD, expected REVIEW) → falls through to Tier 2
- Test: multiple OUTCOME blocks → uses last one
- Test: existing Tier 2 tests still pass unchanged

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: outcome-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pipeline_state_machine.py -v`
- Run `python -m ruff check bridge/pipeline_state.py`
- Run `python -m ruff format --check bridge/pipeline_state.py`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_pipeline_state_machine.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/pipeline_state.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/pipeline_state.py` | exit code 0 |
| Parser exists | `grep -c '_parse_outcome_contract' bridge/pipeline_state.py` | output > 0 |
| Tier 0 integrated | `grep -c '_parse_outcome_contract' bridge/pipeline_state.py` | output > 1 |
| OUTCOME in build skill | `grep -c 'OUTCOME' .claude/skills/do-build/SKILL.md` | output > 0 |
| OUTCOME in test skill | `grep -c 'OUTCOME' .claude/skills/do-test/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-03. Verdict: READY TO BUILD -->
| Severity | Critic(s) | Finding | Resolution |
|----------|-----------|---------|------------|
| CONCERN | Skeptic, Adversary | classify_outcome docstring only lists "success"/"fail"/"ambiguous" — needs "partial" added | Address in Task 1 |
| CONCERN | Operator, Skeptic | "partial" routes through fail_stage() which shows as "failed" in dashboard — misleading for approved-with-nits | Pre-existing; note as follow-up |
| CONCERN | Operator | Task 2 validation is "manual review" — no automated check | Use grep verification commands |
| CONCERN | Simplifier | /do-docs OUTCOME emission mentioned in Solution but not tasked | Remove mention or add task |
| NIT | Adversary | Regex notation differs between Solution and Task 1 | Align during build |
| NIT | Archaeologist | Filename references "artifact_verification" which was the prior rejected approach | Not worth renaming |

---

## Open Questions

No open questions — the OUTCOME contract format is already defined in `/do-pr-review`, the pipeline graph already handles "partial", and the parsing is straightforward.
