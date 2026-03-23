---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/486
last_comment_id:
---

# SDLC Prompt Enforcement: Stage-by-Stage Agent Orchestration

## Problem

PR #483 was completed by a dev-session that bypassed the entire SDLC pipeline — no test, review, docs, or gate stages executed. The PM spawned a dev-session, which assessed the PR and completed it in 130 seconds with "no stage progress to render."

**Current behavior:**
The dev-session agent description says "execute the complete SDLC pipeline in a single session." The PM prompt injection tells the PM to "spawn a dev-session" with no instruction to orchestrate stages. The dev-session runs the whole pipeline (or skips it entirely) in one shot.

**Desired outcome:**
The PM orchestrates SDLC work stage-by-stage: assess which stage is next, spawn a dev-session for that one stage, verify the result, then progress to the next stage. Dev-sessions are single-stage executors that report back to the PM. All prompts use positive/instructive language.

## Prior Art

- **Issue #465 / PR #466**: SDLC Redesign Phase 2 — established ChatSession/DevSession architecture. Bridge became dumb (nudge loop), PM owns orchestration, DevSession executes. Architecture is correct; prompts don't enforce it.
- **Issue #467**: Pipeline cleanup — removed dead code, `simple` sessions, added e2e tests. Cleaned up plumbing but didn't address prompt content.
- **Issue #474**: PM persona cleanup — removed playlist references, verified dev-session dispatch injection exists. Didn't address stage-by-stage orchestration.
- **Issue #459 / PR #464**: SDLC Redesign Phase 1 — defined the full architecture, introduced session_type discriminator.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #465/466 | Built ChatSession/DevSession architecture | Correct architecture, but dev-session.md still describes itself as full-pipeline executor |
| #474 | Verified PM dispatch injection | Only checked injection exists, not what it says. PM injection lacks stage orchestration instructions |

**Root cause pattern:** Each iteration built correct plumbing but left the agent instructions unchanged. The agents follow their prompts, not the architecture diagrams.

## Data Flow

1. **Telegram message** → Bridge classifies as `sdlc`
2. **Bridge** → Constructs `enriched_message` in `sdk_client.py:1343-1395`
3. **PM prompt injection** (`sdk_client.py:1384`) → Appends "spawn a dev-session" instruction
4. **PM (ChatSession)** → Reads instruction, spawns dev-session Agent with a prompt
5. **Dev-session** → Reads its own `dev-session.md` system prompt + PM's prompt
6. **Dev-session** → Executes work (currently: entire pipeline or nothing)
7. **Dev-session** → Returns result to PM
8. **PM** → Composes delivery message → Telegram

The enforcement gap is at steps 3-5: the PM injection doesn't instruct stage-by-stage orchestration, and dev-session.md tells the agent to run the whole pipeline.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (review prompt wording before deploy)
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies prompt text and validation logic only.

## Solution

### Key Elements

- **PM prompt injection rewrite**: Instruct the PM to assess the current SDLC stage, spawn one dev-session for that stage, verify the result, then decide the next stage
- **dev-session.md rewrite**: Single-stage executor — receives a stage assignment, executes it, reports result
- **PM persona audit**: Verify the private persona file reinforces stage orchestration
- **Completion validation**: When an SDLC session completes without stage progress, log a warning
- **Positive language pass**: Rewrite all "NEVER/do NOT" patterns as instructive "do X" statements

### Flow

**SDLC message arrives** → PM assesses current stage → PM spawns dev-session for ONE stage → dev-session executes stage → dev-session reports result → PM verifies → PM spawns next dev-session (or reports done)

### Technical Approach

- Rewrite `sdk_client.py:1384-1394` PM injection to include stage assessment and single-stage dispatch instructions
- Rewrite `.claude/agents/dev-session.md` as single-stage executor
- Add informational warning in `agent/hooks/pre_tool_use.py` when Bash commands include `gh pr` operations on SDLC sessions without stage completion (soft guidance, not a block)
- Add completion check in stop hook when SDLC session has no stage_states recorded
- Audit `~/Desktop/Valor/personas/project-manager.md` for alignment

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is prompt text changes

### Empty/Invalid Input Handling
- [ ] Verify PM still functions when stage assessment returns ambiguous state (e.g., PR exists but no tests run)
- [ ] Verify dev-session handles receiving a stage assignment with missing context gracefully

### Error State Rendering
- [ ] Verify the completion warning for "no stage progress" is visible in bridge logs

## Test Impact

- [ ] `tests/unit/test_post_tool_use_sdlc.py` — UPDATE: if SDLC state tracking changes, assertions may need updating
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: may need new assertions for stage-by-stage model

No major test breakage expected — changes are primarily to prompt text, not Python logic.

## Rabbit Holes

- Building a hard-blocking stage gate system — the enforcement should be prompt-level guidance, not infrastructure blocks
- Rewriting the entire SDLC skill system — only the prompt injection and agent description need changing
- Adding new pipeline state machine logic — the existing `stage_states` field on AgentSession is sufficient

## Risks

### Risk 1: PM ignores stage instructions
**Impact:** Same behavior as today — dev-session runs whole pipeline
**Mitigation:** The completion warning catches this. Iterate on prompt wording based on observed behavior.

### Risk 2: Stage-by-stage is too slow for simple PRs
**Impact:** Docs-only PRs take 5 dev-session spawns instead of 1
**Mitigation:** PM instructions should include: for trivial/docs-only work, assess whether full pipeline is warranted. The PM makes the judgment call.

## Race Conditions

No race conditions identified — changes are to prompt text and logging, all synchronous.

## No-Gos (Out of Scope)

- Hard-blocking merge guards in SDK hooks — this is prompt-level guidance only
- Changing bridge classification logic — classification is correct
- Modifying the nudge loop or job queue — not relevant
- Building a stage UI — #461 (Observer UI) is separate

## Update System

No update system changes required — prompt text changes are picked up on next bridge restart. The private persona file syncs via iCloud.

## Agent Integration

No agent integration required — this modifies the prompts that agents receive, not the tools they can use. No MCP or bridge changes needed.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` to document stage-by-stage orchestration model
- [ ] Add entry to `docs/features/README.md` if new feature doc created

## Success Criteria

- [ ] dev-session.md describes a single-stage executor
- [ ] PM prompt injection at `sdk_client.py:1384` instructs stage-by-stage orchestration
- [ ] PM persona file reviewed and aligned with stage model
- [ ] All prompt text uses positive/instructive language (no "NEVER"/"do NOT" patterns)
- [ ] SDLC sessions that complete without stage progress generate a log warning
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-builder
  - Role: Rewrite all prompt text (sdk_client injection, dev-session.md, persona audit)
  - Agent Type: builder
  - Resume: true

- **Validator (prompts)**
  - Name: prompt-validator
  - Role: Verify all prompts use positive language, stage model is consistent across files
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite dev-session agent definition
- **Task ID**: build-dev-session-prompt
- **Depends On**: none
- **Validates**: manual review — agent description matches single-stage model
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `.claude/agents/dev-session.md` as single-stage executor
- Remove "complete SDLC pipeline in a single session"
- Add: receives stage assignment from PM, executes that stage, reports result

### 2. Rewrite PM prompt injection
- **Task ID**: build-pm-injection
- **Depends On**: none
- **Validates**: `grep -c "one dev-session" agent/sdk_client.py` returns > 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `sdk_client.py:1384-1394` to instruct stage-by-stage orchestration
- Include: assess current stage, spawn one dev-session per stage, verify result

### 3. Audit PM persona file
- **Task ID**: build-persona-audit
- **Depends On**: none
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `~/Desktop/Valor/personas/project-manager.md`
- Verify stage orchestration is reinforced
- Add stage guidance if missing

### 4. Add completion warning
- **Task ID**: build-completion-warning
- **Depends On**: none
- **Validates**: `tests/unit/test_pipeline_integrity.py`
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- In stop hook or session completion path, log warning when SDLC session has no stage_states

### 5. Positive language pass
- **Task ID**: build-positive-language
- **Depends On**: build-dev-session-prompt, build-pm-injection
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: false
- Grep all prompt files for "NEVER", "do NOT", "don't", "cannot"
- Rewrite each as positive instruction

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-dev-session-prompt, build-pm-injection, build-persona-audit, build-completion-warning, build-positive-language
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify dev-session.md describes single-stage model
- Verify PM injection includes stage orchestration
- Verify no "NEVER"/"do NOT" patterns remain in agent prompts
- Run test suite

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: prompt-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/chat-dev-session-architecture.md`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No NEVER in dev-session | `grep -c "NEVER\|do NOT" .claude/agents/dev-session.md` | exit code 1 |
| Single-stage language | `grep -c "single.stage\|one stage\|assigned stage" .claude/agents/dev-session.md` | output > 0 |
| PM injection has stage | `grep -c "one dev-session\|stage.by.stage\|one stage" agent/sdk_client.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

Resolved — PM decides both:
1. ~~Max stage iterations~~ — PM uses judgment on when to escalate vs keep iterating
2. ~~Stage skipping for trivial work~~ — PM assesses whether full pipeline is warranted
