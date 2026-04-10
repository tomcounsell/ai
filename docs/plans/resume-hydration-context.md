---
status: Critiqued
type: feature
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/874
last_comment_id:
---

# PM Session Resume Hydration: Inject Recent Commits into First Turn

## Problem

When a PM session resumes mid-SDLC-pipeline, the Claude Agent SDK subprocess starts cold with no memory of completed stages. The information needed to avoid rediscovery is already captured on disk by `save_session_snapshot()` in `resume.json`, but the resumed session never sees it on the first turn. The agent wastes tool calls re-reading files, re-running tests, and re-dispatching already-committed stages.

**Current behavior:**
Session `tg_valor_-1003449100931_502` (PR #868) was resumed three times. On each resume, the agent re-read `sdk_client.py` ~14 times, re-read the plan doc 10 times, ran the full unit suite 4 times, and re-dispatched a BUILD stage whose commit was already in `git log`. Roughly 60-80 of 213 tool calls were wasted on rediscovery.

**Desired outcome:**
When a PM session resumes, the first turn contains a `<resumed-session-context>` block with recent branch commits. The agent correlates commit headlines against plan stages and skips completed work without file reads or `git log` calls.

## Freshness Check

**Baseline commit:** `7e636ef7347a275337716a0a1caa6c252023f251`
**Issue filed at:** 2026-04-10T06:31:39Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_logs.py:25-64` -- `_get_git_summary()` captures `git log --oneline -3` + `git status` -- still holds
- `agent/session_logs.py:67-123` -- `save_session_snapshot()` writes resume.json calling `_get_git_summary()` at line 111 -- still holds
- `agent/agent_session_queue.py:697-722` -- steering message drain + prepend pattern -- still holds, no changes since issue filed
- `agent/agent_session_queue.py:2655-2678` -- `save_session_snapshot` call at session execution start -- still holds (line 2665)

**Cited sibling issues/PRs re-checked:**
- #780 -- still open (harness abstraction work in progress)
- #868 -- merged 2026-04-10 (Phases 1-2 of harness abstraction). Changed `_execute_agent_session` but did NOT touch the steering drain block (lines 697-722) or the snapshot call (line 2665).

**Commits on main since issue was filed (touching referenced files):**
- `27bb2c51` Fix CLI status summary miscounts -- irrelevant (touched line 3749 display bug only)
- `d24dd07f` Add CLI harness abstraction -- irrelevant to this work (added new harness routing code, did not change steering drain or snapshot call)

**Active plans in `docs/plans/` overlapping this area:** `agentsession-harness-abstraction.md` touches `_execute_agent_session` but at a different layer (harness routing); no overlap with the steering drain block.

**Notes:** All file:line references from the issue are accurate as of baseline commit.

## Prior Art

No prior issues found related to resume hydration or injecting context into resumed sessions.

## Data Flow

1. **Entry point**: Worker calls `_pop_agent_session()` (hot path) or `_pop_agent_session_with_fallback()` (drain timeout / exit diagnostic), both of which transition a pending session to running
2. **Resume hydration (new)**: After steering drain in both paths, call shared `_maybe_inject_resume_hydration(chosen, worker_key)`. Detect whether this is a resume by checking for 2+ `*_resume.json` files in `logs/sessions/{session_id}/` (1 file = first start only). If resume detected and `chosen.working_dir` is set and `chosen.session_type == "pm"`, call `_get_git_summary()` and prepend a `<resumed-session-context>` block to `chosen.message_text`
3. **Steering drain (existing)**: At line 697 / line 828, `pop_all_steering_messages()` drains queued messages and appends them to `chosen.message_text` (after hydration context)
4. **Session execution**: `_execute_agent_session()` passes `session.message_text` to the Claude Agent SDK. The agent sees the context block on its first turn
5. **Snapshot capture**: `save_session_snapshot()` at line 2665 writes `resume.json` as before (unchanged)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Resume detection**: Check for `*_resume.json` files in the session's log directory (`logs/sessions/{session_id}/`). If 2+ exist, this is a resume (1 file = first start snapshot only).
- **Git summary capture**: Call the existing `_get_git_summary()` with a bumped depth of `-10` commits for the injected hint (the snapshot writer continues using `-3` independently).
- **Message prepend**: Prepend a `<resumed-session-context>` tagged block to `chosen.message_text` following the same pattern as the steering message drain.
- **Session-type scoping**: Apply to PM sessions only. Dev sessions are one-shot stage executors; teammate sessions are conversational. Neither benefits from commit-based stage hints.

### Flow

**Session popped** (either path) -> resume hydration check -> if resume + PM + working_dir set: prepend context block -> steering messages drained -> session executes with hint on first turn

### Technical Approach

**Hook point**: Extract an `async _maybe_inject_resume_hydration(chosen, worker_key)` helper. Call it from both `_pop_agent_session()` (after line 722) and `_pop_agent_session_with_fallback()` (after line 848). This prevents the two code paths from diverging and ensures sessions popped via the sync fallback also receive hydration.

**Resume detection via filesystem**: Check `SESSION_LOGS_DIR / chosen.session_id` for existing `*_resume.json` files. Since `save_session_snapshot(event="resume")` is called on every session start (line 2665), the first start always produces exactly 1 resume file. Therefore, resume is detected when 2+ `*_resume.json` files exist (meaning the session has been started at least twice). No new Redis keys or model fields needed.

**Working directory guard**: If `chosen.working_dir` is falsy (empty string or None), skip hydration entirely with a debug log. Do not rely on `_get_git_summary()`'s fallback to the repo root, as that would produce misleading git state for worktree-isolated sessions.

**`_get_git_summary()` with depth override**: Add an optional `log_depth` parameter to `_get_git_summary()` (default 3, preserving current behavior). The hydration block calls it with `log_depth=10` to capture more of the branch's commit history for stage correlation.

**Session-type gate**: Check `chosen.session_type == "pm"` before injecting. Skip silently for other session types.

**Prepend format**:
```
<resumed-session-context>
This session is resuming. The following commits already exist on the branch:
{git log --oneline -10 output}
{git status --short output}
If any of these commits satisfy a stage in your current plan, skip that stage
and proceed to the next uncompleted stage. Do not re-dispatch work that is
already committed.
</resumed-session-context>
```

**Silent failure**: The entire block is wrapped in try/except with a logger.warning on failure, matching the steering drain pattern. If anything fails, session start proceeds without the hint.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new resume hydration block has a try/except that logs a warning on failure -- test that a subprocess error in `_get_git_summary()` does not crash session start
- [ ] Test that a missing/unreadable session log directory does not crash session start

### Empty/Invalid Input Handling
- [ ] `_get_git_summary()` with `log_depth=10` in a non-git directory returns an error string (already handled by existing code)
- [ ] Empty session log directory (no prior resume.json files) correctly skips hydration
- [ ] Session with exactly 1 resume.json (first start only) correctly skips hydration
- [ ] Session with falsy `working_dir` (None or empty string) correctly skips hydration with debug log

### Error State Rendering
- [ ] Not applicable -- no user-visible output from this feature; the hint is internal to the agent prompt

## Test Impact

No existing tests affected -- the steering drain block at lines 697-722 has no direct unit tests in `test_agent_session_queue.py` (confirmed by grep). The `_pop_agent_session` tests focus on field extraction, pop lock contention, and sustainability throttle. The new hydration block follows the same pattern and sits after the drain, so existing tests remain valid.

## Rabbit Holes

- **Parsing commit messages to auto-detect stage completion**: The hint is advisory text for the LLM to interpret. Do not build a structured stage-commit mapping system -- that would be fragile and duplicates what the agent already does with plan documents.
- **Persisting hydration state in Redis**: The hint is ephemeral and reconstructed from git on each resume. Adding Redis state would create a new failure mode for zero benefit.
- **Extending to dev sessions**: Dev sessions execute a single stage and do not resume mid-pipeline. Adding hydration there would add complexity for a scenario that does not occur in practice.

## Risks

### Risk 1: `_get_git_summary()` subprocess timeout on slow git repos
**Impact:** Session start delayed by up to 10 seconds (existing timeout in `_get_git_summary`).
**Mitigation:** The existing 10-second timeout in `_get_git_summary()` already handles this. The hint is skipped on timeout with a warning log.

### Risk 2: Agent ignores or misinterprets the hint
**Impact:** Falls back to current rediscovery behavior. No worse than today.
**Mitigation:** The hint format is explicit about what to do ("skip that stage and proceed to the next"). If the agent still rediscovers, nothing breaks -- the hint is purely advisory.

## Race Conditions

No race conditions identified -- the hydration block runs synchronously inside `_pop_agent_session()` after the pop lock is released but before the session is returned to the worker. Only one worker processes a given session at a time.

## No-Gos (Out of Scope)

- Structured stage-commit mapping or automated stage skip logic
- Changes to `save_session_snapshot()` or the resume.json format
- Hydration for dev or teammate sessions
- New Redis keys or AgentSession model fields
- Changes to the steering message drain logic

## Update System

No update system changes required -- this feature is purely internal to the worker's session pop logic. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a worker-internal change to the session queue. No new MCP servers, tool registration, or bridge changes needed.

## Documentation

- [ ] Create `docs/features/resume-hydration-context.md` describing the feature
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update docstring on `_get_git_summary()` to document the `log_depth` parameter

## Success Criteria

- [ ] When a PM session is resumed (has 2+ prior resume.json files), `chosen.message_text` is prepended with a `<resumed-session-context>` block containing recent branch commits
- [ ] Hydration works via both `_pop_agent_session()` and `_pop_agent_session_with_fallback()` paths (shared helper)
- [ ] The hint uses `_get_git_summary()` with `log_depth=10` -- one code path for git state capture
- [ ] Initial (non-resume) PM session starts (1 or 0 resume.json files) do NOT receive the hint
- [ ] Non-PM sessions (dev, teammate) never receive the hint regardless of resume state
- [ ] PM sessions with falsy `working_dir` never receive the hint (guard prevents wrong-directory git summary)
- [ ] If git summary fails, the prepend silently skips with a warning log -- session start never crashes
- [ ] No new AgentSession fields, no new Redis keys
- [ ] Unit test: resumed PM session gets prepend; fresh PM session does not; dev session does not; falsy working_dir does not
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hydration)**
  - Name: hydration-builder
  - Role: Implement resume detection, git summary depth parameter, and message prepend
  - Agent Type: builder
  - Resume: true

- **Validator (hydration)**
  - Name: hydration-validator
  - Role: Verify prepend behavior, silent failure, and session-type gating
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `log_depth` parameter to `_get_git_summary()`
- **Task ID**: build-git-summary-depth
- **Depends On**: none
- **Validates**: tests/unit/test_resume_hydration.py (create)
- **Assigned To**: hydration-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `log_depth: int = 3` parameter to `_get_git_summary()` in `agent/session_logs.py`
- Use `log_depth` in the `git log --oneline -{log_depth}` subprocess call instead of hardcoded `-3`
- Update the function docstring to document the parameter
- Write a unit test in `tests/unit/test_resume_hydration.py` verifying `log_depth` controls the number of commits returned

### 2. Add resume hydration to both pop paths via shared helper
- **Task ID**: build-hydration-prepend
- **Depends On**: build-git-summary-depth
- **Validates**: tests/unit/test_resume_hydration.py (create)
- **Assigned To**: hydration-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `async _maybe_inject_resume_hydration(chosen: AgentSession, worker_key: str)` helper in `agent/agent_session_queue.py`
- Import `SESSION_LOGS_DIR` from `agent.session_logs` and `_get_git_summary`
- Guard: if `chosen.session_type != "pm"`, return silently (dev/teammate sessions skip)
- Guard: if `chosen.working_dir` is falsy, log debug and return (avoid wrong-directory git summary)
- Check for `*_resume.json` files in `SESSION_LOGS_DIR / chosen.session_id` -- if fewer than 2 found, return (1 file = first start only, 2+ = genuine resume)
- Call `_get_git_summary(working_dir=chosen.working_dir, log_depth=10)`
- Prepend the `<resumed-session-context>` block to `chosen.message_text`
- Call `await chosen.async_save()` to persist the updated message_text
- Log info: number of prior resume files found, hydration injected
- Wrap entire body in try/except: log warning and return on any exception (matching steering drain error handling pattern)
- Call `_maybe_inject_resume_hydration(chosen, worker_key)` from `_pop_agent_session()` (after line 722, before return)
- Call `_maybe_inject_resume_hydration(chosen, worker_key)` from `_pop_agent_session_with_fallback()` (after line 848, before return)

### 3. Write unit tests for resume hydration
- **Task ID**: build-hydration-tests
- **Depends On**: build-hydration-prepend
- **Validates**: tests/unit/test_resume_hydration.py
- **Assigned To**: hydration-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: `_get_git_summary(log_depth=10)` produces 10 commit lines (validates Task 1)
- Test: PM session with 2+ prior resume.json files gets `<resumed-session-context>` prepended
- Test: PM session with exactly 1 resume.json file (first start only) does NOT get prepend
- Test: PM session with no resume.json files does NOT get prepend
- Test: Dev session with 2+ prior resume.json files does NOT get prepend
- Test: PM session with falsy `working_dir` does NOT get prepend (even with 2+ resume files)
- Test: If `_get_git_summary()` raises an exception, helper returns without crashing
- Test: The prepend includes output from `_get_git_summary(log_depth=10)`
- Test: Glob pattern `*_resume.json` matches actual filename format from `save_session_snapshot(event="resume")`
- Use mocks for filesystem checks and `_get_git_summary` to keep tests fast and isolated

### 4. Validate implementation
- **Task ID**: validate-hydration
- **Depends On**: build-hydration-tests
- **Assigned To**: hydration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no new fields on AgentSession model
- Verify no new Redis keys
- Verify `_get_git_summary()` default behavior unchanged (log_depth=3)
- Run full test suite
- Run ruff check and format

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hydration
- **Assigned To**: hydration-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/resume-hydration-context.md`
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hydration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hydration tests exist | `pytest tests/unit/test_resume_hydration.py -v` | exit code 0, covers log_depth + hydration + guards |
| No new model fields | `git diff HEAD -- models/agent_session.py` | output contains  |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-10. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary, Operator | `_pop_agent_session_with_fallback()` (lines 828-848) has a duplicate steering drain that also needs the hydration block. Sessions popped via the sync fallback path would silently skip hydration. | Plan revised: Task 2 updated to cover both paths via shared helper | Extract `_maybe_inject_resume_hydration(chosen, worker_key)` async helper; call from both `_pop_agent_session()` and `_pop_agent_session_with_fallback()` |
| CONCERN | Skeptic, Adversary | `chosen.working_dir` may be empty/None at pop time. The fallback in `_get_git_summary()` would silently produce git state for the wrong directory, which is worse than no summary. | Plan revised: explicit guard added to Task 2 | If `chosen.working_dir` is falsy, skip hydration entirely with a debug log. Do not rely on the fallback path. |
| CONCERN | Skeptic | Resume detection checking for 1+ `*_resume.json` files will always trigger after first start, since `save_session_snapshot(event="resume")` is called at line 2665 on every session start. Must check for 2+ files to distinguish "resumed at least once" from "first start only". | Plan revised: resume detection threshold updated to 2+ files | The glob `*_resume.json` matches the filename format. But 1 file = first start, 2+ files = resumed. Task 2 and tests updated. |
| NIT | Simplifier | Hydration context should be prepended before steering messages so the agent orients itself before processing new instructions. | Accepted: prepend order revised in Task 2 | Hydration block runs first, steering drain appends after. |
| NIT | Operator | `test_session_logs.py` for `log_depth` parameter is not in the Verification table. | Plan revised: merged into `test_resume_hydration.py` | Keep all tests in one file to simplify verification. |

---

## Open Questions

No open questions -- all design decisions resolved by the issue's recon:

1. **Resume detection**: Filesystem check for prior `*_resume.json` files (most reliable, no new state)
2. **Session-type scoping**: PM sessions only (dev sessions are one-shot, teammate sessions are conversational)
3. **Commit depth**: `log_depth=10` for the injected hint; snapshot writer stays at default `3`
