---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/354
---

# Remove Full-Pipeline SDLC Instructions from Worker Agent

## Problem

The worker agent (Claude Code subprocess) receives the full 9-stage SDLC pipeline orchestration in its system prompt via the `SDLC_WORKFLOW` constant. This causes the worker to invoke `/sdlc` autonomously and attempt to self-manage the entire pipeline (ISSUE -> PLAN -> BUILD -> TEST -> PATCH -> REVIEW -> PATCH -> DOCS -> MERGE) within a single session.

This conflicts with the Observer Agent, which was introduced in PR #321 to steer the worker one stage at a time. The result is a dual-control conflict: two components both trying to be the pipeline controller.

**Current behavior:**
- `load_system_prompt()` in `agent/sdk_client.py` prepends a 40-line `SDLC_WORKFLOW` constant containing full pipeline instructions before the SOUL.md persona
- The worker reads these instructions and invokes `/sdlc`, which creates 9 task items and tries to advance through all stages sequentially
- The Observer detects stage transitions after the worker pauses and sends coaching messages -- but these conflict with the worker's own autonomous pipeline execution
- Context window is wasted on orchestration instructions the worker doesn't need

**Desired outcome:**
- The worker receives only persona context (SOUL.md) plus a short worker-role instruction with safety rails
- The Observer is the sole controller of pipeline progression via coaching messages
- The `SDLC_WORKFLOW` constant is replaced with a minimal `WORKER_RULES` constant
- Tests updated to match the new prompt structure

## Prior Art

- **Issue #309 / PR #321**: Observer Agent -- replaced the auto-continue/summarizer chain with a stage-aware SDLC steerer. Introduced the Observer but intentionally left `SDLC_WORKFLOW` in place as a transitional safety net. The Observer is now stable and the safety net can be removed.
- **Issue #264**: TestLoadSystemPromptInjection tests -- these tests assert `SDLC_WORKFLOW` presence in the prompt and must be updated.

## Data Flow

1. **Entry point**: Telegram message arrives at the bridge, gets enqueued as an `AgentSession` job
2. **`_execute_job()`** in `agent/job_queue.py`: calls `get_agent_response_sdk()` which creates a `ValorAgent` instance
3. **`ValorAgent.__init__()`**: calls `load_system_prompt()` which prepends `SDLC_WORKFLOW` + SOUL.md + completion criteria -> system prompt
4. **`ValorAgent._create_options()`**: passes the system prompt to `ClaudeAgentOptions` -> Claude Code subprocess starts with these instructions
5. **Worker output**: flows back through `send_to_chat()` -> Observer Agent reads session state and decides to steer or deliver
6. **Observer steering**: coaching message is enqueued as a new `AgentSession` job -> worker starts again at step 2 with the Observer's coaching as the new prompt

The change targets step 3: replacing `SDLC_WORKFLOW` with `WORKER_RULES` in the system prompt construction.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: The `SDLC_WORKFLOW` export name changes to `WORKER_RULES` (tests import it directly)
- **Coupling**: Decreases coupling -- the worker no longer needs knowledge of the full pipeline; the Observer is the single point of control
- **Data ownership**: Pipeline orchestration responsibility moves entirely to the Observer (was already 90% there)
- **Reversibility**: Trivially reversible -- swap the constant back if needed

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (clear scope, no ambiguity)
- Review rounds: 1 (standard code review)

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **`WORKER_RULES` constant**: Replaces `SDLC_WORKFLOW` with a short instruction that tells the worker to execute the task given to it, preserving only the safety rails (never push code to main, code vs docs distinction)
- **Updated `load_system_prompt()`**: Uses `WORKER_RULES` instead of `SDLC_WORKFLOW`
- **Updated tests**: `TestSdlcWorkflowConstant` and `TestLoadSystemPromptInjection` rewritten to assert the new prompt structure

### Flow

**Worker starts** -> receives SOUL.md + WORKER_RULES (short safety rails) -> executes the task from the coaching message -> **output pauses** -> Observer reads session state -> Observer sends coaching message with next `/do-*` skill -> **Worker starts again**

### Technical Approach

1. Replace the `SDLC_WORKFLOW` constant (lines 108-148 of `sdk_client.py`) with a new `WORKER_RULES` constant containing:
   - "Execute the task given to you" framing
   - "NEVER commit code directly to main" safety rail
   - "Code changes go to session/{slug} branches" safety rail
   - "Plan/doc changes (.md, .json, .yaml) may be committed to main" carve-out
   - No pipeline stages, no `/sdlc` reference, no task creation instructions

2. Update `load_system_prompt()` to use `WORKER_RULES` instead of `SDLC_WORKFLOW`:
   - Keep the same prompt structure: rules first, then SOUL.md, then completion criteria
   - Update the docstring to reflect the new content

3. Update `tests/unit/test_sdk_client_sdlc.py`:
   - Rename `TestSdlcWorkflowConstant` to `TestWorkerRulesConstant`
   - Update import from `SDLC_WORKFLOW` to `WORKER_RULES`
   - Assert new content (safety rails present, no pipeline orchestration)
   - Update `TestLoadSystemPromptInjection` to assert `WORKER_RULES` content in prompt
   - Leave `TestCheckNoDirectMainPush` and `TestIsCodeFileInlined` entirely unchanged

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- the change only modifies string constants and test assertions

### Empty/Invalid Input Handling
- `load_system_prompt()` already handles missing SOUL.md with a fallback -- no change needed
- The new `WORKER_RULES` constant is a non-empty string literal -- cannot be None or empty

### Error State Rendering
- Not applicable -- this change affects system prompt content, not user-visible output

## Rabbit Holes

- **Modifying the Observer Agent**: The Observer already steers correctly -- its system prompt and tools are not part of this change. Resist the temptation to "improve" it simultaneously.
- **Removing SDLC references from CLAUDE.md**: CLAUDE.md is read by Claude Code's built-in context system and describes the project's workflow for human readers. Those references describe the overall architecture, not worker instructions. Leave them.
- **Changing the `/sdlc` skill**: The SKILL.md file describes the full pipeline for when `/sdlc` is explicitly invoked (e.g., by the Observer's coaching message). It should still exist. The change is about not having the worker *self-invoke* it.

## Risks

### Risk 1: Worker ignores Observer coaching
**Impact:** Without pipeline instructions, the worker might not know to invoke `/do-build` or `/do-test` when coached to do so.
**Mitigation:** The Observer's coaching messages are explicit (e.g., "Continue with /do-build"). The worker already follows skill invocations from prompts -- the coaching message *is* the instruction. The `/do-*` skills contain their own self-contained instructions.

### Risk 2: In-flight sessions break
**Impact:** Sessions that started with the old `SDLC_WORKFLOW` prompt might behave differently mid-pipeline.
**Mitigation:** Claude Code sessions have `continue_conversation=True` which preserves the original system prompt for continued sessions. New sessions get the new prompt; in-flight sessions keep their original prompt.

## Race Conditions

No race conditions identified. The change modifies a string constant that is read during `ValorAgent.__init__()` on the main thread. No concurrency or shared mutable state is involved.

## No-Gos (Out of Scope)

- Modifying the Observer Agent system prompt or tools (`bridge/observer.py`)
- Modifying the `/sdlc` skill (`SKILL.md`)
- Removing SDLC references from `CLAUDE.md` (those describe architecture, not worker instructions)
- Changing `_check_no_direct_main_push()` or its tests
- Modifying the `SOUL.md` persona file

## Update System

No update system changes required -- this is a code-internal change to a string constant and its tests. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this change modifies the worker's system prompt content. No MCP servers, tool registrations, or bridge imports are affected.

## Documentation

- [ ] Update `docs/features/observer-agent.md` (if it exists) to note the Observer is now the sole pipeline controller
- [ ] Add inline code comments on the new `WORKER_RULES` constant explaining its purpose and relationship to the Observer

## Success Criteria

- [ ] `SDLC_WORKFLOW` constant no longer exists in `agent/sdk_client.py`
- [ ] New `WORKER_RULES` constant contains safety rails but no pipeline orchestration
- [ ] `load_system_prompt()` uses `WORKER_RULES` (no `/sdlc` or pipeline stages in output)
- [ ] `_check_no_direct_main_push()` unchanged and its tests all pass
- [ ] All tests in `tests/unit/test_sdk_client_sdlc.py` pass with updated assertions
- [ ] `pytest tests/ -x -q` passes (no regressions)
- [ ] `python -m ruff check .` clean
- [ ] Observer system prompt and tools unchanged (verified by diff)

## Team Orchestration

### Team Members

- **Builder (sdk-client)**
  - Name: sdk-builder
  - Role: Replace SDLC_WORKFLOW with WORKER_RULES in sdk_client.py and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria met
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Replace SDLC_WORKFLOW with WORKER_RULES
- **Task ID**: build-worker-rules
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the `SDLC_WORKFLOW` constant in `agent/sdk_client.py` with a new `WORKER_RULES` constant containing only safety rails (no pipeline orchestration)
- Update `load_system_prompt()` to use `WORKER_RULES` and update its docstring
- Update the log message in `get_agent_response_sdk()` that references `sdlc_workflow=yes`

### 2. Update tests
- **Task ID**: build-tests
- **Depends On**: build-worker-rules
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: false
- Update imports in `tests/unit/test_sdk_client_sdlc.py` from `SDLC_WORKFLOW` to `WORKER_RULES`
- Rename `TestSdlcWorkflowConstant` to `TestWorkerRulesConstant` and update assertions
- Update `TestLoadSystemPromptInjection` to assert new content
- Verify `TestCheckNoDirectMainPush` and `TestIsCodeFileInlined` unchanged and passing

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and verify all tests pass
- Run `python -m ruff check .` and verify clean
- Verify `bridge/observer.py` has zero changes (Observer untouched)
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No SDLC_WORKFLOW | `grep -c 'SDLC_WORKFLOW' agent/sdk_client.py` | exit code 1 |
| WORKER_RULES exists | `grep -c 'WORKER_RULES' agent/sdk_client.py` | output > 0 |
| Observer unchanged | `git diff bridge/observer.py` | output contains "" |
| No pipeline in prompt | `python -c "from agent.sdk_client import load_system_prompt; p=load_system_prompt(); assert '/sdlc' not in p.lower(), 'prompt still references /sdlc'"` | exit code 0 |
| Safety rails in prompt | `python -c "from agent.sdk_client import load_system_prompt; p=load_system_prompt(); assert 'NEVER' in p and 'main' in p"` | exit code 0 |
