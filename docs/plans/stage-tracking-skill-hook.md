---
status: Planning
type: bug
appetite: Small
owner: valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/782
last_comment_id:
---

# Stage Tracking: Wire Skill Tool Invocations into PipelineStateMachine

## Problem

SDLC stage tracking never advances past the initial state for PM sessions. The dashboard shows every stage as `pending` (or `ISSUE=ready`) regardless of actual pipeline progress. `current_stage` is always `null`.

**Current behavior:** When a PM session invokes `Skill(skill="do-build")`, the `pre_tool_use` hook fires but falls through with no stage action. The `subagent_stop` hook only processes `agent_type == "dev-session"` — Skill completions are ignored entirely. `AgentSession.stage_states` is never written by the Skill path.

**Desired outcome:** When the PM calls a Skill that maps to a pipeline stage, the corresponding stage is recorded as `in_progress` when the Skill starts and `completed` (or `failed`) when it finishes. The dashboard reflects actual pipeline progress for all live PM sessions.

## Prior Art

- **Issue #492** (closed 2026-03-23): *Wire PipelineStateMachine.start_stage() into production SDLC dispatch path* — wired `start_stage()` for dev-session Agent tool calls only. Did not address Skill tool calls.
- **Issue #563** (closed 2026-03-30): *SDLC pipeline graph routing not wired into runtime* — wired `complete_stage()` / `fail_stage()` into the `subagent_stop` hook for dev-session completions. Same blind spot: only `agent_type == "dev-session"`.
- **Issue #704** (closed 2026-04-05): *SDLC router must use PipelineStateMachine instead of artifact inference* — fixed the router to read from `stage_states` rather than infer from artifacts. Exposed the underlying write gap: there is nothing writing `stage_states` for Skill-driven stages.
- **Issue #645** (closed 2026-04-03): *Implicit pipeline stage tracking via observable artifacts* — artifact inference approach, superseded by #704.
- **`tools/sdlc_stage_marker.py`**: A CLI that writes stage markers via `PipelineStateMachine`. Designed as a direct-invocation fallback for sessions where hooks don't fire. Currently invoked only by skills that explicitly call it (e.g., `do-plan` calls it at the top and bottom of execution). Skills that don't call it produce no stage records.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Issue #492 | Wired `start_stage()` into `_maybe_register_dev_session()` | Only triggered by `tool_name == "Agent"` with `subagent_type="dev-session"`. PM Skill invocations were never considered. |
| Issue #563 | Wired `complete_stage()` into `subagent_stop` hook | `subagent_stop` only fires for subagents spawned with `agent_type`. The Skill tool completes synchronously within the PM turn — no subagent stop event. |

**Root cause pattern:** Both fixes assumed SDLC stage execution = dev-session Agent spawn. PM sessions using the Skill tool were never part of the mental model.

## Data Flow

The Skill tool executes synchronously in the PM's Claude Code process. Unlike the Agent tool (which spawns a subprocess), Skill is a built-in tool that loads and executes a `.claude/commands/` file in-process. There is no subagent stop event. The lifecycle looks like:

1. **`pre_tool_use` fires** — `tool_name="Skill"`, `tool_input={"skill": "do-build", ...}`. Current code falls through with no action.
2. **Skill executes** — the skill file runs (e.g., `do-build.md`) using sub-tools within the PM's turn.
3. **`post_tool_use` fires** — `tool_name="Skill"`. Currently only runs the watchdog health check.
4. **`subagent_stop` never fires** — Skill is not an Agent; there is no subagent stop lifecycle.

Correct data flow after fix:
1. **`pre_tool_use` fires** → detect `tool_name=="Skill"` → map skill name to stage → call `_start_pipeline_stage(session_id, stage)`
2. **Skill executes** (no change)
3. **`post_tool_use` fires** → detect `tool_name=="Skill"` → call `_complete_pipeline_stage(session_id, stage)` using last known in_progress stage

The session ID is resolved via `session_registry.resolve(claude_uuid)` — the same mechanism already used by `pre_tool_use` for dev-session Agent calls.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies Python hook files with no new dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "from models.agent_session import AgentSession; list(AgentSession.query.filter(session_id='x'))"` | Session lookup requires Redis |

## Solution

### Key Elements

- **`pre_tool_use.py` — Skill handler**: Add a branch for `tool_name == "Skill"`. Extract the skill name from `tool_input["skill"]`. Map to a stage via `_SKILL_TO_STAGE`. Call `_start_pipeline_stage(session_id, stage)`. The session ID is resolved from `session_registry.resolve(claude_uuid)`.
- **`post_tool_use.py` — Skill completion handler**: Extend the post_tool_use hook to detect `tool_name == "Skill"`. Call `_complete_pipeline_stage(session_id)` which reads the current `in_progress` stage from the state machine and calls `complete_stage()`.
- **Skill → Stage mapping dict**: A module-level dict `_SKILL_TO_STAGE` in `pre_tool_use.py` mapping skill names to stage names. Shared with `post_tool_use.py` via import.
- **`sdlc_stage_marker.py` stays as-is**: The direct-CLI path remains as a fallback for local sessions where hooks don't fire. The new hook path handles bridge-initiated PM sessions.

### Flow

PM invokes Skill → `pre_tool_use` maps skill name → `start_stage()` called → Skill runs → `post_tool_use` fires → `complete_stage()` called → `stage_states` updated in Redis → dashboard shows progress

### Technical Approach

- Skill → Stage mapping lives in `pre_tool_use.py` (existing file for pre-tool logic) and is imported by the post_tool_use extension.
- `_complete_pipeline_stage(session_id)` looks up the current `in_progress` stage via `sm.current_stage()` rather than requiring the stage name to be passed explicitly. This avoids needing to store state between pre and post hooks.
- The existing `_start_pipeline_stage()` and `_record_stage_on_parent()` helpers are reused. No new helper architecture is needed.
- `post_tool_use.py` currently only re-exports `watchdog_hook`. It becomes a thin wrapper that runs both watchdog and the new Skill completion logic.
- All errors are swallowed with `logger.warning` — hooks must never crash the PM session.

**`_SKILL_TO_STAGE` mapping:**
```python
_SKILL_TO_STAGE: dict[str, str] = {
    "do-plan": "PLAN",
    "do-plan-critique": "CRITIQUE",
    "do-build": "BUILD",
    "do-test": "TEST",
    "do-patch": "PATCH",
    "do-pr-review": "REVIEW",
    "do-docs": "DOCS",
    "do-merge": "MERGE",
}
```

## Failure Path Test Strategy

### Exception Handling Coverage

- Both `_start_pipeline_stage` and `_record_stage_on_parent` already catch all exceptions and log warnings. The new Skill-handling branches must wrap calls in the same try/except pattern.
- Tests must assert that a `ValueError` from `start_stage()` (predecessor not completed) does not propagate.
- Tests must assert that a Redis failure in `AgentSession.query.filter` does not propagate.

### Empty/Invalid Input Handling

- If `tool_input.get("skill")` is missing or empty, the Skill handler must silently return `{}` with no side effects.
- If the skill name is not in `_SKILL_TO_STAGE` (e.g., `do-discover-paths`), the handler must silently return `{}`.
- If `session_registry.resolve(claude_uuid)` returns None (no session registered), the handler must skip gracefully.

### Error State Rendering

- Stage tracking failures are silent — they never surface to the PM as errors. The PM session continues unaffected. This is intentional (same pattern as existing hooks).

## Test Impact

- [ ] `tests/unit/test_pre_tool_use_start_stage.py` — UPDATE: add test class `TestSkillToolStartStage` covering: Skill tool triggers `_start_pipeline_stage`, unknown skill name is ignored, missing skill key is ignored, no session ID skips gracefully.
- [ ] `tests/unit/test_pre_tool_use_hook.py` — UPDATE: verify the main `pre_tool_use_hook` function dispatches the Skill branch (smoke test for the routing, not the full logic).

New test files to create:
- [ ] `tests/unit/test_post_tool_use_stage_completion.py` — covers `_complete_pipeline_stage()`: skill completion calls `complete_stage()`, unknown skill no-ops, missing session no-ops, exception from `complete_stage()` is swallowed.

## Rabbit Holes

- **Rewriting `post_tool_use.py` architecture**: The current file is a one-liner re-export. Don't over-engineer it into a plugin system — just add the Skill completion logic as a second function called from a thin dispatcher.
- **Storing stage name in hook state between pre and post**: Tempting to pass the stage name forward via a module-level dict keyed by `tool_use_id`. Unnecessary — `sm.current_stage()` reads it directly from Redis.
- **Handling Skill failures vs. successes differently in `post_tool_use`**: The `post_tool_use` hook receives no result/output from the Skill. Outcome classification (success/fail) requires the output tail. For Skill completions, default to `complete_stage()` (same safe default as `subagent_stop` uses for ambiguous outcomes). If the skill itself writes an OUTCOME contract, `classify_outcome` will catch it; but wiring full outcome classification to Skill completions is out of scope for this fix.
- **Replacing `sdlc_stage_marker.py`**: It works and has callers. Don't remove it — the two mechanisms coexist.

## Risks

### Risk 1: `start_stage()` predecessor check fails for early pipeline stages
**Impact:** If BUILD is called before PLAN is `completed`, `start_stage("BUILD")` raises `ValueError`. The hook catches it, but the stage is never recorded.
**Mitigation:** `_start_pipeline_stage()` already catches `ValueError` and logs a debug message. This is the correct behavior — if the pipeline state is inconsistent, the hook fails silently and the PM session continues. No additional mitigation needed.

### Risk 2: `post_tool_use` fires for every tool call
**Impact:** The new Skill completion check runs on every tool call. For non-Skill tools, it must be a fast no-op (just an `if tool_name != "Skill": return` check).
**Mitigation:** The guard is a single string comparison before any Redis calls. Negligible overhead.

## Race Conditions

The bridge is single-threaded asyncio. All hook calls for a given session are sequential. No race conditions identified.

## No-Gos (Out of Scope)

- Do NOT replace the dev-session Agent hook path in `pre_tool_use.py`.
- Do NOT rewrite `PipelineStateMachine` — it is correct.
- Do NOT wire outcome classification (success vs. fail detection) into Skill completions — default to `complete_stage()`.
- Do NOT modify `sdlc_stage_marker.py` or its callers.
- Do NOT handle `do-issue` → ISSUE stage mapping — issue creation is not a Skill invocation in the current architecture.

## Update System

No update system changes required — this feature modifies Python hook files only. No new dependencies, config files, or environment variables.

## Agent Integration

No agent integration required — this is a bridge-internal hook change. The hooks fire within the bridge process. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/sdlc-stage-handoff.md` to document the Skill tool → stage tracking path alongside the existing dev-session path.
- [ ] Add an inline comment in `pre_tool_use.py` explaining `_SKILL_TO_STAGE` and when it is used.

## Success Criteria

- [ ] When a PM session calls `Skill(skill="do-build")`, the BUILD stage transitions to `in_progress` in Redis
- [ ] When the `do-build` skill completes, BUILD transitions to `completed`
- [ ] `AgentSession.stage_states` is non-null after any SDLC skill runs
- [ ] Dashboard shows correct current stage for a live PM session
- [ ] Existing dev-session hook path unaffected — `tests/unit/test_pre_tool_use_start_stage.py` passes unchanged
- [ ] New unit tests cover: Skill → stage mapping in `pre_tool_use`, unknown skill name no-op, `post_tool_use` completion call
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (hooks)**
  - Name: hooks-builder
  - Role: Extend `pre_tool_use.py` and `post_tool_use.py` with Skill tool handling
  - Agent Type: builder
  - Resume: true

- **Test Engineer (hooks)**
  - Name: hooks-tester
  - Role: Write unit tests for new Skill hook paths in `pre_tool_use` and `post_tool_use`
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: hooks-validator
  - Role: Verify tests pass and existing test suite is unaffected
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Extend pre_tool_use.py with Skill handler
- **Task ID**: build-pre-tool-use
- **Depends On**: none
- **Validates**: tests/unit/test_pre_tool_use_start_stage.py, tests/unit/test_pre_tool_use_hook.py
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_SKILL_TO_STAGE` dict to `agent/hooks/pre_tool_use.py`
- Add `_handle_skill_tool_start(tool_input, claude_uuid)` function that looks up stage and calls `_start_pipeline_stage`
- Add `if tool_name == "Skill":` branch in `pre_tool_use_hook` that calls `_handle_skill_tool_start`

### 2. Extend post_tool_use.py with Skill completion handler
- **Task ID**: build-post-tool-use
- **Depends On**: build-pre-tool-use
- **Validates**: tests/unit/test_post_tool_use_stage_completion.py (create)
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_complete_pipeline_stage(session_id)` function in a new helper module or inline in `post_tool_use.py`
- Extend `post_tool_use.py` to dispatch both watchdog and Skill completion in one hook function
- Import `_SKILL_TO_STAGE` from `pre_tool_use` to guard non-SDLC Skill calls

### 3. Write unit tests
- **Task ID**: test-hooks
- **Depends On**: build-pre-tool-use, build-post-tool-use
- **Validates**: tests/unit/test_pre_tool_use_start_stage.py, tests/unit/test_post_tool_use_stage_completion.py
- **Assigned To**: hooks-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestSkillToolStartStage` class to `tests/unit/test_pre_tool_use_start_stage.py`
- Create `tests/unit/test_post_tool_use_stage_completion.py` with full coverage
- Run full unit suite and confirm no regressions

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: test-hooks
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pre_tool_use_start_stage.py tests/unit/test_pre_tool_use_hook.py tests/unit/test_post_tool_use_stage_completion.py -v`
- Confirm all success criteria are met
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_pre_tool_use_start_stage.py tests/unit/test_pre_tool_use_hook.py tests/unit/test_post_tool_use_stage_completion.py -v` | exit code 0 |
| Unit suite clean | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/hooks/pre_tool_use.py agent/hooks/post_tool_use.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hooks/pre_tool_use.py agent/hooks/post_tool_use.py` | exit code 0 |
| Skill mapping complete | `python -c "from agent.hooks.pre_tool_use import _SKILL_TO_STAGE; assert 'do-build' in _SKILL_TO_STAGE"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the root cause is confirmed, the solution is clear, and the scope is tight.
