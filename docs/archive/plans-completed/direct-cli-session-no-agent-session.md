---
status: Complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/1001
last_comment_id:
---

# Direct CLI sessions must not create AgentSession records

## Problem

Every time a developer runs `claude` directly at the terminal (e.g. `/update`, `/do-plan`, or any interactive session), the `UserPromptSubmit` hook creates an `AgentSession` record in Redis tagged `local-{uuid}`. These records were introduced for observability and parent-child linking in worker-spawned sessions, but for unspawned direct CLI sessions they have no value. They accumulate in the queue, appear as `running` in `agent_session_scheduler list`, and generate noise in the worker's startup recovery log every restart.

**Current behavior:**

- `user_prompt_submit.py` always creates an `AgentSession` on first prompt, regardless of whether the session was spawned by the worker or launched by a human directly.
- On every worker restart, `_recover_interrupted_agent_sessions_startup()` logs `[startup-recovery] Skipping recent session <uuid>` for each live direct CLI session, even though those sessions are irrelevant to the worker.
- The session list and dashboard show these as `running` until the stop hook marks them complete — or forever if the stop hook fails.

**Desired outcome:**

- Running `claude` directly (no `VALOR_PARENT_SESSION_ID`, no `SESSION_TYPE` in env) creates **no** `AgentSession` record.
- `agent_session_scheduler list` contains only sessions the worker is responsible for.
- Worker startup recovery log contains no skip/abandon lines for sessions the worker never owned.
- Memory extraction, transcript backup, and all other hook functionality continue normally for direct CLI sessions.

## Freshness Check

**Baseline commit:** `0a2ff585`
**Issue filed at:** 2026-04-16T06:59:19Z
**Disposition:** Unchanged — issue was filed minutes ago; no commits have landed on these files since.

**File:line references re-verified:**

- `.claude/hooks/user_prompt_submit.py:96-104` — `SESSION_TYPE` read at line 97, `VALOR_PARENT_SESSION_ID` at line 104, `create_local` called at line 106 — all confirmed present
- `agent/sdk_client.py:1052` — `VALOR_PARENT_SESSION_ID` injected for PM/Teammate parents — confirmed at line 1052
- `agent/sdk_client.py:1062` — `SESSION_TYPE` injected for all worker-spawned sessions — confirmed at line 1062
- `.claude/hooks/stop.py:138-139` — `_complete_agent_session` checks `agent_session_id` in sidecar and returns early if absent — confirmed at lines 138-139
- `.claude/hooks/post_tool_use.py:355-356` — `_update_agent_session` checks `agent_session_id` and returns early if absent — confirmed at lines 355-356

**Cited sibling issues/PRs re-checked:**

- #986 — Startup recovery hijacks live local CLI sessions — closed, merged via PR #989. Fix addressed recovery behavior; root cause (session creation) untouched.
- PR #821 — Child session parent linkage via `VALOR_PARENT_SESSION_ID` — merged. This is the mechanism the gate relies on; confirmed it sets `VALOR_PARENT_SESSION_ID` correctly.

**Commits on main since issue was filed:** None touching the referenced files.

**Active plans in `docs/plans/` overlapping this area:** None — `ls -lt docs/plans/*.md | head -5` shows no active plans touching hooks or session creation.

## Prior Art

- **Issue #986 / PR #989**: [Startup recovery hijacks live local CLI sessions](https://github.com/tomcounsell/ai/issues/986) — Fixed the downstream symptom: worker no longer re-enqueues or resumes sessions it didn't own. This plan addresses the upstream root cause: those sessions should never be created in the first place.
- **PR #821**: [Child session parent linkage via VALOR_PARENT_SESSION_ID](https://github.com/tomcounsell/ai/pull/821) — Introduced `VALOR_PARENT_SESSION_ID` to wire parent-child AgentSession hierarchy. The gate in this plan reuses that discriminator.

## Research

No relevant external findings — this is a purely internal hook gating change with no external library or API dependencies.

## Data Flow

The change affects a single decision point in the `UserPromptSubmit` hook:

1. **Entry**: Claude Code fires `UserPromptSubmit` on every user prompt; hook receives JSON on stdin including `session_id`, `prompt`, `cwd`
2. **Sidecar check**: Hook reads `~/.claude/session_logs/{session_id}/agent_session.json` — if `agent_session_id` already recorded, re-activates existing session and returns
3. **Gate (NEW)**: If neither `VALOR_PARENT_SESSION_ID` nor `SESSION_TYPE` is present in `os.environ`, skip `AgentSession` creation entirely; write nothing to sidecar; return
4. **Create (gated)**: Only reached for worker-spawned sessions — calls `AgentSession.create_local()`, stores `agent_session_id` in sidecar for subsequent hook calls
5. **Downstream hooks** (`PostToolUse`, `Stop`): Read `agent_session_id` from sidecar; already guard on absence (return early) — no change needed

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — `AgentSession.create_local()` signature unchanged; callers outside the hook are unaffected
- **Coupling**: Decreases — direct CLI sessions no longer produce Redis records the worker must filter out
- **Data ownership**: No change — worker-spawned sessions continue to own their `AgentSession` records
- **Reversibility**: Trivial — remove the two-line guard to restore prior behavior

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Gate in `user_prompt_submit.py`**: A two-condition check before the `create_local` call. If both env vars are absent, skip creation and return.
- **Sidecar stays empty**: With no `agent_session_id` written, all downstream hooks (`PostToolUse`, `Stop`) hit their existing early-return guards — no code changes needed in those files.
- **Memory extraction unaffected**: `_run_memory_extraction()` in `stop.py` calls `memory_bridge.extract()` directly and has no `AgentSession` dependency.

### Flow

Direct CLI invocation → `UserPromptSubmit` fires → sidecar has no `agent_session_id` → gate checks env vars → neither present → **skip creation, return** → session runs normally with memory/transcript features intact

Worker-spawned invocation → `UserPromptSubmit` fires → sidecar has no `agent_session_id` → gate checks env vars → `SESSION_TYPE` present → **proceed to `create_local`** → AgentSession created, sidecar updated → downstream hooks work as before

### Technical Approach

In `.claude/hooks/user_prompt_submit.py`, inside the `else` branch (first prompt, no existing `agent_session_id`), add a guard before `AgentSession.create_local()`:

```python
# Only create AgentSession for worker-spawned sessions.
# Direct CLI invocations (no parent worker, no session type) produce no record —
# they add noise to the queue without providing value.
if not os.environ.get("VALOR_PARENT_SESSION_ID") and not os.environ.get("SESSION_TYPE"):
    return
```

No other files require changes. The sidecar will contain no `agent_session_id` for direct CLI sessions, and all three downstream hooks that consume the sidecar already handle this case with early returns.

## Failure Path Test Strategy

### Exception Handling Coverage

The hook's `AgentSession` block is wrapped in `except Exception: pass` (silent failure). The gate runs *before* that block, so it never touches exception-handling paths. No new handlers introduced.

- Existing silent-failure wrapper is unchanged — it already fires on `create_local` errors, not on the gate skip

### Empty/Invalid Input Handling

- `os.environ.get("VALOR_PARENT_SESSION_ID")` and `os.environ.get("SESSION_TYPE")` return `None` for absent keys — falsy, gate activates correctly
- Empty string values (`SESSION_TYPE=""`) are also falsy — treated the same as absent; this is intentional (a blank `SESSION_TYPE` is not a valid worker-spawned indicator)

### Error State Rendering

The gate is a silent no-op from the user's perspective — no user-visible output. No error-state rendering needed.

## Test Impact

- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_omits_session_type_when_env_var_unset` — **UPDATE**: currently asserts `create_local` is called once (without `session_type` kwarg). After the fix, `create_local` must NOT be called at all when both `SESSION_TYPE` and `VALOR_PARENT_SESSION_ID` are absent. Assert `mock_create.assert_not_called()` instead.
- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_passes_session_type_teammate_to_create_local` — **Unchanged**: `SESSION_TYPE=teammate` is set, so gate passes and `create_local` is called. This test continues to pass as-is.

New tests to add (in the same file):

- [ ] `test_main_skips_create_local_when_no_env_vars` — both env vars absent → `create_local` not called
- [ ] `test_main_creates_session_when_session_type_set` — `SESSION_TYPE` present → `create_local` called
- [ ] `test_main_creates_session_when_parent_session_id_set` — `VALOR_PARENT_SESSION_ID` present → `create_local` called
- [ ] `test_main_creates_session_when_both_env_vars_set` — both present → `create_local` called once

## Rabbit Holes

- **Suppressing memory extraction for direct CLI sessions** — memory ingestion and extraction work fine without an `AgentSession`; don't touch them
- **Cleaning up stale existing `local-*` records** — out of scope; the startup recovery already handles abandoned ones gracefully; a separate cleanup pass is a separate concern
- **Dashboard observability for direct CLI sessions** — direct CLI is developer tooling, not production traffic; no observability requirement here

## Risks

### Risk 1: Future code adds a path that assumes AgentSession always exists for local sessions

**Impact:** Silent breakage if new code reads from the sidecar and assumes `agent_session_id` is present without checking
**Mitigation:** The early-return pattern is already established in three hook files; new code should follow the same pattern. The guard condition in the hook is clearly commented.

### Risk 2: A worker-spawned session is missing `SESSION_TYPE` in some code path

**Impact:** That session would incorrectly skip `AgentSession` creation, breaking parent-child linkage
**Mitigation:** `sdk_client.py:1062` injects `SESSION_TYPE` for all sessions with a known type (verified in freshness check). The only case where it could be absent is a future code path that spawns `claude` without going through `sdk_client`. The plan's acceptance tests catch this for the current paths.

## Race Conditions

No race conditions identified — the gate reads `os.environ` (process-stable, set once at spawn time) and makes a synchronous creation decision. No shared mutable state involved.

## No-Gos (Out of Scope)

- Cleaning up existing stale `local-*` records already in Redis
- Modifying the startup recovery guard (PR #989 is sufficient for the transition period)
- Dashboard changes for the loss of direct CLI session observability
- Changing `SESSION_TYPE` injection in `sdk_client.py`

## Update System

No update system changes required — this is a hook file change with no new dependencies, config files, or service restarts needed.

## Agent Integration

No agent integration required — this is a hook-internal change. The bridge and MCP servers are unaffected.

## Documentation

- [ ] Update `docs/features/claude-code-memory.md` — add a note that direct CLI sessions (no `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID`) do not create `AgentSession` records; memory extraction still runs via transcript at session end
- [ ] No new feature doc needed — this is a bug fix removing unintended behavior

## Success Criteria

- [ ] Running `claude` directly (no worker, no `SESSION_TYPE`, no `VALOR_PARENT_SESSION_ID`) creates **no** `AgentSession` record in Redis
- [ ] `python -m tools.agent_session_scheduler list` shows no `local-*` entries after a direct CLI session
- [ ] Worker startup recovery log contains no `[startup-recovery] Skipping recent session` lines for direct CLI sessions
- [ ] Worker-spawned sessions (always have `SESSION_TYPE`) continue to create `AgentSession` records
- [ ] `pytest tests/unit/test_hook_user_prompt_submit.py -v` passes with updated and new gate tests
- [ ] Memory extraction (`_run_memory_extraction`) still fires at direct CLI session end — verify via `logs/hooks.log` or manual test

## Team Orchestration

### Team Members

- **Builder (hook-gate)**
  - Name: hook-builder
  - Role: Add gate condition to `user_prompt_submit.py`; update and add tests in `test_hook_user_prompt_submit.py`
  - Agent Type: builder
  - Resume: true

- **Validator (hook-gate)**
  - Name: hook-validator
  - Role: Verify gate behavior, run tests, confirm no regressions in stop/post_tool_use hooks
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

#### 1. Implement gate and update tests
- **Task ID**: build-hook-gate
- **Depends On**: none
- **Validates**: `tests/unit/test_hook_user_prompt_submit.py`
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add gate condition in `.claude/hooks/user_prompt_submit.py` before `AgentSession.create_local()` call (lines 96-120): skip if neither `VALOR_PARENT_SESSION_ID` nor `SESSION_TYPE` is set
- Update `TestMainCallChain::test_main_omits_session_type_when_env_var_unset`: change `mock_create.assert_called_once()` to `mock_create.assert_not_called()`; also add `monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)`
- Add four new test cases in `TestMainCallChain`: no-env-vars skips, SESSION_TYPE creates, VALOR_PARENT_SESSION_ID creates, both-set creates

#### 2. Validate implementation
- **Task ID**: validate-hook-gate
- **Depends On**: build-hook-gate
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_hook_user_prompt_submit.py -v` — all tests pass
- Run `pytest tests/unit/test_stop_hook.py -v` — no regressions
- Confirm `stop.py::_complete_agent_session` early-return at line 139 handles empty sidecar correctly (code read)
- Confirm `post_tool_use.py::_update_agent_session` early-return at line 356 handles empty sidecar correctly (code read)
- Run `python -m ruff check .claude/hooks/user_prompt_submit.py` — lint clean

#### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hook-gate
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/claude-code-memory.md` — add note about direct CLI sessions not creating AgentSession records

#### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` — full unit suite passes
- Run `python -m ruff check . && python -m ruff format --check .` — clean
- Verify all Success Criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/user_prompt_submit.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/user_prompt_submit.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — scope is well-defined, gate condition is unambiguous, all downstream paths handle empty sidecar.
