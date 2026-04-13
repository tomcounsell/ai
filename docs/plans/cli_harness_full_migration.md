---
status: Shipped
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-04-12
tracking: https://github.com/tomcounsell/ai/issues/912
last_comment_id:
revision_applied: true
---

# CLI Harness Full Migration — All Session Types

## Problem

The system is half-migrated after PRs #868 and #902. Dev sessions have a CLI harness path guarded by `DEV_SESSION_HARNESS=claude-cli`, but PM and teammate sessions are hardcoded to the SDK execution path. The `DEV_SESSION_HARNESS` feature flag adds complexity without providing value: the CLI harness (`claude -p`) is strictly better than the SDK for all session types (hooks, memory, slash commands, YOLO mode), and the default should no longer be SDK.

**Current behavior:**

1. Dev sessions default to `get_agent_response_sdk()` unless `DEV_SESSION_HARNESS=claude-cli` is set explicitly.
2. PM and teammate sessions always execute via `get_agent_response_sdk()` regardless of `DEV_SESSION_HARNESS`.
3. `get_agent_response_sdk()` contains extensive message enrichment and intent classification logic that runs on every PM/teammate session turn.
4. Worker startup runs a harness health check only if `DEV_SESSION_HARNESS != sdk`, meaning the check is skipped in the default (SDK) mode.

**Desired outcome:**

- All session types (dev, pm, teammate) route to `get_response_via_harness()`.
- `DEV_SESSION_HARNESS` env var is removed from all code and config.
- The SDK execution branch in `agent_session_queue.py` is deleted.
- `get_agent_response_sdk()` is deleted (message enrichment logic extracted to a shared helper).
- Worker always validates the CLI harness at startup.

## Freshness Check

**Baseline commit:** `git rev-parse HEAD` at plan time
**Issue filed at:** 2026-04-12T05:58:39Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `agent/agent_session_queue.py:3263–3265` — `DEV_SESSION_HARNESS` env var read and `_use_cli_harness` flag — **still holds**
- `agent/agent_session_queue.py:3267–3312` — `if _use_cli_harness:` / `else:` branch with both execution paths — **still holds**
- `agent/agent_session_queue.py:2809` — `from agent import ... get_agent_response_sdk` import — **still holds**
- `agent/sdk_client.py:1451` — `get_response_via_harness()` — **still holds**
- `agent/sdk_client.py:1635` — `get_agent_response_sdk()` — **still holds**
- `agent/__init__.py:34,49` — exports `get_agent_response_sdk` — **still holds**
- `.env.example:55` — `# DEV_SESSION_HARNESS=sdk` — **still holds**
- `worker/__main__.py:164–184` — harness health check gated by `DEV_SESSION_HARNESS != sdk` — **still holds**

**Cited sibling issues/PRs re-checked:**

- #868 — merged 2026-04-10, Phases 1-2 CLI harness routing for dev sessions — complete
- #902 — merged 2026-04-11, Phases 3-5 pipeline move, PM persona, hook cleanup — complete
- #780 — original harness abstraction design, closed/resolved via above PRs

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None

## Prior Art

- **PR #868** — Add CLI harness abstraction for dev sessions (Phases 1-2). Implemented `get_response_via_harness()`, routed dev sessions via `_use_cli_harness` flag. Did not touch PM/teammate sessions.
- **PR #902** — Complete harness abstraction: Phases 3-5. Pipeline state machine, PM persona update, `session_registry` deletion. PM/teammate sessions still on SDK.
- **Issue #780** — Original harness abstraction design. Established `get_response_via_harness()`, PM spawn via `valor_session` CLI.

This issue is the deliberate third and final phase: remove the half-migrated state.

## Data Flow

### Current (before migration)

1. **Telegram message** → bridge creates `AgentSession` → worker dequeues
2. **`process_session()`** → enriches message with media/YouTube/links
3. **`agent_session_queue.py:3264`** → reads `DEV_SESSION_HARNESS` env var
4. **If `session_type=dev` AND `DEV_SESSION_HARNESS=claude-cli`** → `get_response_via_harness()` with raw `_turn_input`
5. **Otherwise (PM, teammate, or dev with default SDK)** → `get_agent_response_sdk()`:
   - Resolves working dir
   - Calls `build_context_prefix()` → adds PROJECT/FOCUS/TECH/REPO
   - Appends `FROM:`, `SESSION_ID:`, `TASK_SCOPE:`, `SCOPE:` headers
   - Runs Haiku intent classifier (teammate vs PM dispatch)
   - Calls `ValorAgent.query()` via Claude Agent SDK

### After migration

1. **Telegram message** → bridge creates `AgentSession` → worker dequeues
2. **`process_session()`** → enriches message with media/YouTube/links
3. **New `build_harness_turn_input()`** → builds context-prefixed message for all session types (PROJECT, FROM, SESSION_ID, TASK_SCOPE, SCOPE headers)
4. **Always** → `get_response_via_harness()` with enriched `_turn_input`, `working_dir`, env vars
5. **Post-completion** → dev sessions trigger `_handle_dev_session_completion()` (gated on `_session_type == "dev"` instead of `_use_cli_harness`)

## Architectural Impact

- **Removed dependency**: `get_agent_response_sdk()` is deleted, removing the Claude Agent SDK execution loop from the hot path entirely
- **Interface change**: `agent_session_queue.py` no longer imports `get_agent_response_sdk` from `agent/__init__.py`; the export is removed
- **New internal helper**: `build_harness_turn_input()` extracted from `get_agent_response_sdk()` to `agent/sdk_client.py` (or `agent/agent_session_queue.py`), covering session context injection for all session types
- **Reduced coupling**: Worker startup no longer depends on `DEV_SESSION_HARNESS` to decide whether to run the harness health check; it always runs it
- **Reversibility**: Low — reversing would require re-implementing the SDK execution path. The migration is intentionally one-way.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| CLI harness binary present | `which claude` | `claude -p` execution |
| Redis running | `redis-cli ping` | Session queue access |

Run all checks: `python scripts/check_prerequisites.py docs/plans/cli_harness_full_migration.md`

## Solution

### Key Elements

- **`build_harness_turn_input()`**: New helper extracted from `get_agent_response_sdk()` that builds the context-prefixed message (PROJECT, FROM, SESSION_ID, TASK_SCOPE, SCOPE) for any session type. Called in `agent_session_queue.py` before `get_response_via_harness()`.
- **Simplified routing in `agent_session_queue.py`**: Remove the `DEV_SESSION_HARNESS` read, `_use_cli_harness` flag, and the `if/else` branch. One `do_work()` using `get_response_via_harness()` for all session types.
- **Dev session completion gate updated**: `if _use_cli_harness and not task.error:` → `if _session_type == "dev" and not task.error:` so PM/teammate sessions do not trigger dev SDLC post-processing.
- **Worker health check unconditional**: Remove the `if _harness_mode != "sdk":` gate; always verify CLI harness at startup.
- **`get_agent_response_sdk()` deleted**: The function body is removed from `sdk_client.py`. Its context-enrichment bootstrap is extracted to `build_harness_turn_input()`. The export is removed from `agent/__init__.py`.

### Flow

Telegram message → `process_session()` enriches media/links → `build_harness_turn_input()` adds PROJECT/FROM/SESSION context → `get_response_via_harness()` spawns `claude -p` → streams text via send_cb → post-completion: dev sessions trigger SDLC handler, PM/teammate do not

### Technical Approach

**Step 1: Extract `build_harness_turn_input()`** in `agent/sdk_client.py`

Extract the following logic from `get_agent_response_sdk()` into a standalone function:
```python
async def build_harness_turn_input(
    message: str,
    session_id: str,
    sender_name: str | None,
    chat_title: str | None,
    project: dict | None,
    task_list_id: str | None,
    session_type: str | None,
    sender_id: int | None,
    classification: str | None = None,
    is_cross_repo: bool = False,
) -> str:
```
This function calls `build_context_prefix()`, appends `FROM:`, `SESSION_ID:`, `TASK_SCOPE:`, `SCOPE:` headers, and returns the enriched message. No Haiku classifier, no ValorAgent — pure message construction.

**`sender_name` None guard**: Do NOT produce `"FROM: None"`. Use:
```python
if sender_name:
    enriched_message += f"\n\nFROM: {sender_name}"
```

**`classification` / cross-repo GITHUB header**: Mirror the logic from `get_agent_response_sdk()` lines 1841–1849. Only inject `GITHUB: org/repo` when `session_type != "pm"` AND `classification == ClassificationType.SDLC` AND `is_cross_repo is True`. The caller in `agent_session_queue.py` resolves these from `session.classification_type` and `(project_key != "valor")`. Pass them explicitly.

Note: The Haiku intent classifier and teammate mode flag that currently run inside `get_agent_response_sdk()` are **not** needed in the CLI harness path. The `claude -p` process reads the CLAUDE.md persona directly and handles its own routing. The session mode (`TEAMMATE`, `PM`) is already recorded on `AgentSession` by the bridge at session creation time.

**Haiku reclassification audit**: Before deleting the Haiku intent classifier branch from `get_agent_response_sdk()`, add a temporary log line at the `_teammate_mode = True` branch (lines ~1862–1875):
```python
logger.info("Haiku reclassified PM→teammate: session_id=%s", session_id)
```
Deploy on main for 14 days. Zero logs confirms the assertion "no known production edge cases depend on this." If any logs appear, evaluate whether the behavior should be preserved via a CLAUDE.md note before proceeding with deletion.

**Step 2: Update `agent_session_queue.py`**

At lines 3263–3312, replace:
```python
_harness_mode = os.environ.get("DEV_SESSION_HARNESS", "sdk")
_use_cli_harness = _session_type == "dev" and _harness_mode != "sdk"

if _use_cli_harness:
    ...
    async def do_work() -> str:
        return await get_response_via_harness(...)
else:
    async def do_work() -> str:
        return await get_agent_response_sdk(...)
```

With:
```python
from agent.sdk_client import build_harness_turn_input, get_response_via_harness

_harness_input = await build_harness_turn_input(
    message=_turn_input,
    session_id=session.session_id,
    sender_name=session.sender_name,
    chat_title=session.chat_title,
    project=project_config,
    task_list_id=task_list_id,
    session_type=_session_type,
    sender_id=session.sender_id,
    classification=getattr(session, "classification_type", None),
    is_cross_repo=(project_key != "valor"),
)

async def _harness_send_cb(text: str) -> None:
    await send_cb(session.chat_id, text, session.telegram_message_id, agent_session)

async def do_work() -> str:
    return await get_response_via_harness(
        message=_harness_input,
        send_cb=_harness_send_cb,
        working_dir=str(working_dir),
        env={
            "AGENT_SESSION_ID": session.agent_session_id or "",
            "CLAUDE_CODE_TASK_LIST_ID": task_list_id or "",
        },
    )
```

Also remove the `from agent import ... get_agent_response_sdk` import at line 2809.

**Steering message enrichment**: The steering message pop at line 3360–3371 replaces `_turn_input` in-place. The `build_harness_turn_input()` call at line 3172+ happens AFTER the steering pop — so steering messages automatically go through enrichment. No special handling needed. The concern about "pre-enriched by the steering sender" was incorrect: steering messages are raw strings (e.g., "continue", "fix the failing tests") and MUST be enriched with PROJECT/SESSION_ID context headers exactly like regular messages. The proposed code already handles this correctly because `build_harness_turn_input(message=_turn_input, ...)` uses whatever `_turn_input` holds at that point.

**Step 3: Update the dev-completion gate**

At line 3355, change:
```python
if _use_cli_harness and not task.error:
```
To:
```python
if _session_type == "dev" and not task.error:
```

**Step 4: Update `worker/__main__.py`**

Remove the `if _harness_mode != "sdk":` gate and the `DEV_SESSION_HARNESS` env var logic. Always run `verify_harness_health("claude-cli")` at startup. If the check fails, **call `sys.exit(1)`** with a clear recovery message:
```python
logger.critical(
    "CLI harness 'claude' not found or unhealthy — "
    "install with: npm install -g @anthropic-ai/claude-code\n"
    "Worker cannot start without the harness binary."
)
sys.exit(1)
```
This replaces the previous non-fatal log warning. With SDK fallback removed, a missing binary would silently fail every session — making startup fatal is the correct behavior. The launchd service will log the exit and alert operators.

**Step 5: Delete `get_agent_response_sdk()`**

Delete the function body from `agent/sdk_client.py` (lines 1635–end). Remove from `agent/__init__.py` exports (lines 34, 49). Remove the import from `bridge/telegram_bridge.py` (line 88, only the `get_agent_response_sdk` part).

**Step 6: Remove `.env.example` line**

Delete `# DEV_SESSION_HARNESS=sdk` from `.env.example:55`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `build_harness_turn_input()` must handle `session_id=None` gracefully (no SESSION_ID header skipped rather than crashing)
- [ ] `build_context_prefix()` call inside `build_harness_turn_input()` already handles `project=None` — verify this stays true
- [ ] `get_response_via_harness()` already wraps `FileNotFoundError` for missing binary — existing coverage sufficient

### Empty/Invalid Input Handling

- [ ] `build_harness_turn_input()` with empty `message` should return a context-only prefix (not crash)
- [ ] `build_harness_turn_input()` with `sender_name=None` must not produce malformed "FROM: None" header — guard: `if sender_name: enriched_message += f"\n\nFROM: {sender_name}"`
- [ ] Steering message path: `_turn_input` is replaced by `steering_msgs[0]` at line 3362, BEFORE `build_harness_turn_input()` is called — steering messages go through enrichment automatically; test that a steering message still gets PROJECT/SESSION_ID headers in the harness input

### Error State Rendering

- [ ] If `build_harness_turn_input()` raises, the error must propagate to the user (not silently swallowed)
- [ ] Worker startup harness check failure must emit a log warning visible to operators

## Test Impact

- [ ] `tests/unit/test_harness_streaming.py::TestWorkerHarnessRouting::test_default_harness_is_sdk` — **DELETE**: asserts SDK is default, which no longer exists
- [ ] `tests/unit/test_harness_streaming.py::TestWorkerHarnessRouting::test_harness_env_var_recognized` — **DELETE**: tests env var opt-in that no longer exists
- [ ] `tests/integration/test_session_spawning.py::TestHarnessRoutingDecision::test_dev_session_with_sdk_default_skips_harness` — **DELETE**: SDK fallback path removed
- [ ] `tests/integration/test_session_spawning.py::TestHarnessEnvVarRouting::test_env_var_sdk_is_default` — **DELETE**: SDK default removed
- [ ] `tests/integration/test_session_spawning.py::TestHarnessDispatch::test_pm_session_skips_harness` — **DELETE**: PM sessions now always use harness
- [ ] `tests/integration/test_session_spawning.py::TestHarnessDispatch::test_dev_session_with_sdk_default_uses_sdk` — **DELETE**: SDK execution path removed
- [ ] `tests/integration/test_session_spawning.py` — **ADD**: `test_all_session_types_route_to_harness` — verifies that `process_session()` calls `get_response_via_harness` for `session_type` in `["dev", "pm", "teammate"]`, regardless of env var
- [ ] `tests/unit/test_cross_repo_gh_resolution.py` — **UPDATE**: 6 test cases call `get_agent_response_sdk()` directly; rewrite to verify cross-repo context is present in the `_harness_input` returned by `build_harness_turn_input()` (check `GITHUB: org/repo` in the returned string)
- [ ] `tests/unit/test_sdk_client.py::test_get_agent_response_sdk` — **DELETE**: function is removed
- [ ] `tests/unit/test_pm_channels.py` — **UPDATE**: 3 test cases call `get_agent_response_sdk()` directly for PM mode assertions; rewrite to test `build_harness_turn_input()` produces correct PROJECT context for PM-mode sessions (skip Haiku classifier concerns — those no longer apply)
- [ ] `tests/integration/test_bridge_routing.py::test_get_agent_response_sdk_no_workflow_id` — **DELETE**: function is removed
- [ ] `tests/e2e/conftest.py` — **UPDATE**: `mock_agent_response` fixture patches `agent.sdk_client.get_agent_response_sdk`; update to patch `agent.sdk_client.get_response_via_harness` instead

## Rabbit Holes

- **Don't recreate the Haiku intent classifier** in the harness path. The CLI harness (`claude -p`) uses the CLAUDE.md and system prompt to determine its own persona. The bridge already sets `session_mode` on `AgentSession` at creation. No re-classification needed.
- **Don't port `ValorAgent` to the harness** — it's the SDK-specific agent class. After this migration it can be deleted in a follow-up cleanup PR if nothing else references it.
- **Don't add a new harness abstraction layer** — goal is to remove complexity, not swap one indirection for another.
- **Don't handle teammate permission restrictions in `build_harness_turn_input()`** through complex conditional logic — the `build_context_prefix()` call already handles this (`RESTRICTION: This user has read-only Teammate access...`). No new logic needed.
- **Don't migrate the worktrees** — `.worktrees/` are in-flight branches; their stale copies of the code will be updated when they rebase onto main post-merge.

## Risks

### Risk 1: PM sessions lose Haiku intent classification
**Impact:** PM sessions that previously were re-classified as "teammate" based on message content will now always run as PM. The bridge already sets the `session_type` at creation time based on channel config, so this should be fine — but any edge case where the Haiku classifier overrode the channel config will behave differently.
**Mitigation:** A temporary `logger.info("Haiku reclassified PM→teammate: session_id=%s")` line must be deployed at the `_teammate_mode = True` branch and left in place for 14 days before the function is deleted (see Step 1). Zero logs over that window validates the "no known production edge cases" assertion. If logs appear, evaluate whether to preserve the reclassification behavior via a CLAUDE.md note rather than inline code.

### Risk 2: Message context headers missing for PM/teammate sessions
**Impact:** If `build_harness_turn_input()` is not called or contains a bug, PM sessions will receive raw Telegram messages without PROJECT/FROM/SESSION_ID context. The PM agent will still run but without the structured context prefix.
**Mitigation:** The new helper is independently testable. Write a unit test asserting the output contains all expected headers before wiring it into `agent_session_queue.py`.

### Risk 3: Worker health check now mandatory and fatal
**Impact:** If `claude` binary is unavailable at startup, the worker previously succeeded with `DEV_SESSION_HARNESS=sdk` fallback. After this change, the health check runs unconditionally and is **fatal** (`sys.exit(1)`) — the worker will not start without a healthy harness binary.
**Mitigation:** The `sys.exit(1)` includes a clear recovery message pointing to the install command. The launchd service will log the exit. This is the correct behavior: silent per-session failures are worse than a loud startup failure. Operators are expected to have `claude` installed — this is a hard dependency after SDK removal.

## Race Conditions

No new race conditions introduced. The existing session lock, heartbeat loop, and steering message pop patterns are unchanged. `build_harness_turn_input()` is a pure function — no shared mutable state.

## No-Gos (Out of Scope)

- Deleting `ValorAgent` class from `sdk_client.py` — it may still be referenced in tests or tooling; defer to a follow-up cleanup
- Migrating the `session_mode` / teammate permission logic to CLAUDE.md — that's a persona architecture concern, separate from the harness routing
- Changing how the bridge creates sessions or sets `session_type` — routing change only
- Updating worktree branches — they run against their own copies and will pick up changes on rebase

## Update System

No update script changes required. `DEV_SESSION_HARNESS` is an env var, not a file or binary dependency. Removing it from `.env.example` is sufficient. The CLI harness binary (`claude`) was already a hard requirement after PR #902.

## Agent Integration

No MCP or bridge changes required. This is an internal routing change in `agent_session_queue.py` and `agent/sdk_client.py`. The bridge creates sessions exactly as before; only the execution path changes.

## Documentation

- [ ] Update `docs/features/pm-dev-session-architecture.md` — remove SDK references in the execution section; update the diagram to show CLI harness as the single execution path for all session types
- [ ] Update `docs/features/bridge-worker-architecture.md` — if it mentions SDK execution or `DEV_SESSION_HARNESS`, remove those references
- [ ] Update `agent/hooks/subagent_stop.py:59` docstring — remove `DEV_SESSION_HARNESS=claude-cli` reference

## Success Criteria

- [ ] `DEV_SESSION_HARNESS` appears nowhere in `agent/`, `worker/`, `bridge/`, `tests/`, `.env.example` (verified by `grep`)
- [ ] `get_agent_response_sdk` appears nowhere in production code (only in git history)
- [ ] `agent_session_queue.py` has a single `do_work()` definition using `get_response_via_harness()` for all session types
- [ ] `build_harness_turn_input()` is unit-tested: output contains PROJECT, FROM, SESSION_ID headers for pm/teammate/dev session types
- [ ] All deleted tests removed, new `test_all_session_types_route_to_harness` passes
- [ ] `pytest tests/unit/ -x -q` passes
- [ ] Worker startup no longer reads `DEV_SESSION_HARNESS`
- [ ] `docs/features/pm-dev-session-architecture.md` updated
- [ ] New test confirms PM/teammate sessions never trigger dev SDLC post-processing: `_handle_dev_session_completion` is only called when `session_type == "dev"`, verified with mocked harness responses for all three session types
- [ ] New test confirms `build_harness_turn_input()` output contains `SESSION_ID:` header for pm/teammate/dev session types regardless of message content
- [ ] New test confirms `build_harness_turn_input()` injects `GITHUB: org/repo` header only for dev SDLC cross-repo sessions, not for PM/teammate

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement all code changes in `agent_session_queue.py`, `agent/sdk_client.py`, `worker/__main__.py`, `agent/__init__.py`, `bridge/telegram_bridge.py`, `.env.example`
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Delete obsolete tests, update changed tests, add new `test_all_session_types_route_to_harness`
  - Agent Type: test-engineer
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify routing change is correct: no SDK execution path, no DEV_SESSION_HARNESS references, build_harness_turn_input produces correct output
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `pm-dev-session-architecture.md` and `bridge-worker-architecture.md`
  - Agent Type: documentarian
  - Resume: true

- **Final Validator**
  - Name: final-validator
  - Role: Run full unit test suite and verify all success criteria
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Build routing changes
- **Task ID**: build-routing
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py`, `tests/integration/test_session_spawning.py`
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract `build_harness_turn_input()` from `get_agent_response_sdk()` in `agent/sdk_client.py` — function takes `(message, session_id, sender_name, chat_title, project, task_list_id, session_type, sender_id, classification=None, is_cross_repo=False)`, returns enriched string with PROJECT/FROM/SESSION_ID/TASK_SCOPE/SCOPE headers; guard `sender_name=None` (skip FROM header); inject `GITHUB: org/repo` only when `session_type != "pm"` AND `classification == SDLC` AND `is_cross_repo`
- Add temporary Haiku reclassification audit log at `_teammate_mode = True` branch in `get_agent_response_sdk()`: `logger.info("Haiku reclassified PM→teammate: session_id=%s", session_id)` — BEFORE deleting the function; leave in production for 14 days to confirm zero reclassification events
- Remove `DEV_SESSION_HARNESS` read and `_use_cli_harness` flag from `agent_session_queue.py:3263–3265`
- Replace `if _use_cli_harness: ... else: ...` block with a single `do_work()` using `get_response_via_harness()` and calling `build_harness_turn_input()` first
- Update dev-completion gate: `if _use_cli_harness and not task.error:` → `if _session_type == "dev" and not task.error:`
- Remove `get_agent_response_sdk` import from `agent_session_queue.py:2809`
- Delete `get_agent_response_sdk()` body from `agent/sdk_client.py`
- Remove `get_agent_response_sdk` from `agent/__init__.py` exports (lines 34, 49)
- Remove `get_agent_response_sdk` import from `bridge/telegram_bridge.py:88`
- Update `worker/__main__.py`: remove `DEV_SESSION_HARNESS` env var read and fallback; always call `verify_harness_health("claude-cli")`; make health check **fatal** — `sys.exit(1)` with install instructions if check fails
- Delete `# DEV_SESSION_HARNESS=sdk` from `.env.example`
- Update `agent/hooks/subagent_stop.py:59` docstring to remove DEV_SESSION_HARNESS reference

### 2. Build test updates
- **Task ID**: build-tests
- **Depends On**: build-routing
- **Validates**: `tests/unit/`, `tests/integration/`
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `test_default_harness_is_sdk` and `test_harness_env_var_recognized` from `tests/unit/test_harness_streaming.py::TestWorkerHarnessRouting`
- Delete SDK-default tests from `tests/integration/test_session_spawning.py`: `test_dev_session_with_sdk_default_skips_harness` (TestHarnessRoutingDecision), `test_env_var_sdk_is_default` (TestHarnessEnvVarRouting), `test_pm_session_skips_harness` (TestHarnessDispatch), `test_dev_session_with_sdk_default_uses_sdk` (TestHarnessDispatch)
- Add `test_all_session_types_route_to_harness` in `tests/integration/test_session_spawning.py`: mock `get_response_via_harness`, assert it is called for each of `["dev", "pm", "teammate"]` regardless of env
- Update `tests/unit/test_cross_repo_gh_resolution.py`: replace 6 `get_agent_response_sdk()` calls with direct `build_harness_turn_input()` calls; assert output contains `GITHUB: org/repo` header
- Delete `test_get_agent_response_sdk` from `tests/unit/test_sdk_client.py`
- Update `tests/unit/test_pm_channels.py`: rewrite 3 tests to call `build_harness_turn_input()` and assert PROJECT context in output
- Delete `test_get_agent_response_sdk_no_workflow_id` from `tests/integration/test_bridge_routing.py`
- Update `tests/e2e/conftest.py`: change mock target from `agent.sdk_client.get_agent_response_sdk` to `agent.sdk_client.get_response_via_harness`

### 3. Validate routing
- **Task ID**: validate-routing
- **Depends On**: build-routing, build-tests
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `grep -rn "DEV_SESSION_HARNESS" agent/ worker/ bridge/ tests/ .env.example` returns empty
- Verify `grep -rn "get_agent_response_sdk" agent/ worker/ bridge/` returns empty
- Verify `agent_session_queue.py` has exactly one `do_work()` definition (not inside an if/else)
- Verify `build_harness_turn_input()` exists in `agent/sdk_client.py` and returns correct headers for pm/teammate/dev
- Run `pytest tests/unit/ -x -q`

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-dev-session-architecture.md`: remove SDK references in the execution section; update execution flow to show CLI harness as the single path
- Update `docs/features/bridge-worker-architecture.md`: remove any SDK execution path or DEV_SESSION_HARNESS references

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` — must pass
- Run `python -m ruff check .` — must pass
- Run `python -m ruff format --check .` — must pass
- Verify all success criteria in the Success Criteria section

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| No DEV_SESSION_HARNESS | `grep -rn "DEV_SESSION_HARNESS" agent/ worker/ bridge/ .env.example` | exit code 1 |
| No get_agent_response_sdk | `grep -rn "get_agent_response_sdk" agent/ worker/ bridge/` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Worker health check fatal | `grep -n "sys.exit" worker/__main__.py` | shows exit call near harness health check |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Simplifier | Test names in Test Impact do not match actual codebase | Fix test names before build | Run `pytest --collect-only -q` on the 2 test files and correct all 3 mismatched names |
| BLOCKER | Skeptic, Archaeologist, Adversary, Operator, Simplifier | `build_harness_turn_input()` lacks `classification` param — cross-repo SDLC GITHUB header is lost | Add `classification` param OR document harness self-detection | Add `classification: str \| None = None` and `is_cross_repo: bool = False` to signature; caller resolves from `session.classification_type` |
| BLOCKER | Adversary | `sender_name=None` produces "FROM: None" header — guard not specified | Specify guard in Step 1 | Use `if sender_name: enriched_message += f"\n\nFROM: {sender_name}"` |
| BLOCKER | Operator, Skeptic | Worker health check non-fatal with no fallback — silent degradation when `claude` binary missing | Make health check fatal OR add graceful degradation flag | In `worker/__main__.py:184–187`: either `sys.exit(1)` or set `_harness_unavailable = True` flag checked in `process_session()` |
| BLOCKER | User, Archaeologist | Haiku reclassification removal not validated — "no known production edge cases" asserted without evidence | Audit production logs or add temporary log line | Deploy `logger.info("Haiku reclassified PM→teammate")` for 14 days; confirm zero occurrences before removing |
| CONCERN | Skeptic, Adversary, Simplifier | Steering messages bypass `build_harness_turn_input()` — PM/teammate sessions steered without context headers | Document or fix steering path | Check `output_router.py`; if messages are plain text, apply `build_harness_turn_input()` to steering msgs too, or verify SESSION_ID/PROJECT are in env vars |
| CONCERN | User | Success criteria are all technical — no user-facing routing validation | Add user-facing success criterion | Add: "new test confirms PM/teammate sessions never trigger dev SDLC post-processing and receive correct context headers regardless of message content" |

---

## Open Questions

None — the issue is fully specified. Ready to build.
