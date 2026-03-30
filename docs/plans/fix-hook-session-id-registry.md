---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/597
last_comment_id:
---

# Fix Hook Session ID Resolution via Bridge-Level Registry

## Problem

Hooks fired by the Claude Agent SDK execute in the **parent bridge process**, not inside the Claude Code subprocess. Four hook call sites read `os.environ.get("VALOR_SESSION_ID")` expecting the bridge session ID, but that env var is only injected into the subprocess environment at `sdk_client.py:903`. The result: every hook callback gets `None` and falls back to Claude Code's internal UUID.

**Current behavior:**
- `activity.jsonl` writes to `logs/sessions/{claude-code-uuid}/` instead of `logs/sessions/{bridge-session-id}/`
- Redis AgentSession tracking (`tool_call_count`, `last_activity`) updates the wrong record
- Bridge heartbeat shows only `"running Ns, communicated=False"` with no tool-level activity
- DevSession registration in `pre_tool_use.py` silently skips (VALOR_SESSION_ID is None)
- SubagentStop completion tracking in `subagent_stop.py` silently skips (same reason)
- A session ran for 7+ minutes with 20+ tool calls and the bridge saw nothing

**Desired outcome:**
- All 4 hook call sites resolve the correct bridge session ID
- `activity.jsonl` writes to the correct path under the bridge session ID
- Bridge heartbeat includes tool count and last tool name (e.g., `"running 120s, tools=15, last=Bash"`)
- DevSession registration and SubagentStop completion tracking work correctly

## Prior Art

- **Issue #374**: "Observer returns early on continuation sessions due to session cross-wire" -- Added `VALOR_SESSION_ID` env var injection. Fixed subprocess-to-subprocess mapping but did not account for hooks running in the parent process. This is the direct predecessor.
- **Issue #209**: "Audit: Bridge <> AgentSession <> SDK connectivity gaps" -- Earlier audit that identified session identity as fragile. Recommended the env var approach that #374 implemented.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #374 | Injected `VALOR_SESSION_ID` into Claude Code subprocess env | Based on incorrect assumption that hooks run in the subprocess. Hooks run in the parent process via the SDK's JSON control protocol -- the env var is invisible to them. |

**Root cause pattern:** The mental model of hook execution was wrong. The SDK relays hook events from child to parent via `_internal/query.py:312-326`. The fix was applied at the right conceptual level (pass session ID to hooks) but the wrong architectural layer (subprocess env instead of parent-process state).

## Data Flow

1. **Bridge receives message** -> `job_queue.py` creates a Job with `session_id` (e.g., `tg_valor_-1003449100931_247`)
2. **SDKAgentClient.query()** called with `session_id` -> sets `env["VALOR_SESSION_ID"] = session_id` on subprocess env
3. **Claude Code subprocess starts** -> SDK spawns child process with the env var set
4. **Tool call happens inside subprocess** -> SDK relays `PostToolUse` event to parent via JSON control protocol
5. **Hook callback fires in parent process** -> `watchdog_hook()` in `health_check.py` runs -> `os.environ.get("VALOR_SESSION_ID")` returns None because parent env was never modified
6. **Fallback to Claude Code UUID** -> session tracking goes to wrong Redis record and wrong log directory

**Fix inserts at step 4-5:** A module-level registry in the parent process maps Claude Code UUIDs to bridge session IDs. The first hook callback registers the mapping (using `input_data["session_id"]` as the key), and all subsequent lookups use the registry instead of `os.environ`.

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

- **Session ID registry** (`agent/hooks/session_registry.py`): A new module containing a module-level dict that maps Claude Code UUIDs to bridge session IDs, plus per-session tool activity counters (tool count, last 3 tool names)
- **Registration at query start**: `SDKAgentClient` registers the mapping before calling `client.query()`, using a callback or pre-registration pattern
- **Hook-side lookup**: All 4 call sites replace `os.environ.get("VALOR_SESSION_ID")` with a registry lookup
- **Heartbeat enrichment**: `BackgroundTask._watchdog()` reads tool count and last tool name from the registry to include in heartbeat logs
- **Cleanup on query completion**: Registry entries removed after `client.query()` returns (in a finally block)

### Flow

**Job starts** -> SDKAgentClient registers UUID-to-session mapping -> **Hook fires** -> Registry lookup resolves bridge session ID -> **Activity logged correctly** -> **Query completes** -> Registry entry cleaned up

### Technical Approach

- New module `agent/hooks/session_registry.py` with functions: `register(claude_uuid, bridge_session_id)`, `resolve(claude_uuid) -> str | None`, `record_tool_use(claude_uuid, tool_name)`, `get_activity(bridge_session_id) -> dict`, `unregister(claude_uuid)`
- The Claude Code UUID is not known before `client.query()` starts, so registration uses a two-phase approach: `SDKAgentClient` pre-registers the bridge session ID, and the first hook callback completes the mapping using `input_data["session_id"]`
- Alternatively, since `SDKAgentClient` is the only caller and hooks run in the same process, a simpler approach: store a "pending" session ID on the registry keyed by a sentinel, and the first hook callback promotes it to the real UUID key
- Track last 3 tool names (not just 1) per the community suggestion -- gives better stuck-detection signal (e.g., 50x Read in a row vs Read/Edit/Bash)
- `BackgroundTask._watchdog()` in `agent/messenger.py` imports from the registry to enrich its heartbeat log line
- TTL-based sweep as a safety net: `unregister()` is the primary cleanup, but a background check removes entries older than 30 minutes to prevent leaks from uncaught exceptions

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `resolve()` must return None (not raise) when the UUID is not in the registry -- hooks must degrade gracefully to Claude Code UUID
- [ ] `record_tool_use()` must silently handle missing registry entries (tool call before registration completes)
- [ ] `get_activity()` must return empty dict for unknown session IDs
- [ ] Registry cleanup in `SDKAgentClient` must be in a `finally` block to handle exceptions during `client.query()`

### Empty/Invalid Input Handling
- [ ] `register()` with None or empty string for either argument is a no-op (logged at debug level)
- [ ] `resolve()` with None returns None without raising
- [ ] `unregister()` with unknown UUID is a no-op

### Error State Rendering
- Not applicable -- this is backend plumbing with no user-visible UI. Errors surface in bridge logs.

## Test Impact

No existing tests affected -- the 4 call sites being modified have no dedicated unit tests that assert on the session ID resolution path. The `os.environ.get("VALOR_SESSION_ID")` calls are embedded in hook functions that are tested at integration level (where the env var is absent, matching the bug). New tests will cover the registry module directly.

## Rabbit Holes

- Modifying the Claude Agent SDK to pass custom context in hook inputs -- tempting but explicitly out of scope (SDK is a dependency, not owned)
- Adding process-level locking to the registry -- unnecessary since the bridge is single-threaded asyncio; dict operations on distinct keys are safe
- Tailing Claude Code's internal transcript files from the bridge -- unnecessary once activity.jsonl writes to the correct path
- Building a general-purpose hook context injection system -- over-engineering for 4 call sites

## Risks

### Risk 1: UUID not yet known when first hook fires
**Impact:** First tool call's activity logged under wrong session ID
**Mitigation:** Two-phase registration: pre-register bridge session ID, first hook callback completes the mapping. Any tool calls before completion use the pending mapping.

### Risk 2: Registry entry leaks on crash
**Impact:** Memory grows slowly over many sessions
**Mitigation:** TTL-based sweep removes entries older than 30 minutes. Each entry is ~200 bytes so even hundreds of leaked entries are negligible. The `finally` block in `SDKAgentClient` handles normal cleanup.

## Race Conditions

No race conditions identified -- the bridge runs as a single-threaded asyncio event loop. Dict mutations on distinct keys within a single event loop iteration are atomic. Concurrent sessions use different keys (different Claude Code UUIDs and different bridge session IDs), so there is no contention.

## No-Gos (Out of Scope)

- Not modifying the Claude Agent SDK
- Not removing the `VALOR_SESSION_ID` env var injection at `sdk_client.py:903` -- it remains for any code running inside the Claude Code subprocess (shell scripts, Python tools via Bash)
- Not changing the hook registration mechanism in `agent/hooks/__init__.py`
- Not adding inter-process communication for hook context (the registry is in-process only)

## Update System

No update system changes required -- this is a bridge-internal bug fix. All changes propagate via standard `git pull` in the update script.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The registry lives in the parent process and is invisible to Claude Code and MCP servers. No changes to `.mcp.json` or `mcp_servers/`.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to document the session ID registry as the hook-side resolution mechanism (replacing the env var approach for hooks)
- [ ] Add inline docstrings on the new `agent/hooks/session_registry.py` module

## Success Criteria

- [ ] All 4 `os.environ.get("VALOR_SESSION_ID")` call sites in hooks replaced with registry lookup
- [ ] `activity.jsonl` writes to `logs/sessions/{bridge-session-id}/`
- [ ] Redis AgentSession `tool_call_count` and `last_activity` update the correct record
- [ ] Bridge heartbeat includes tool count and last tool name
- [ ] DevSession registration in `pre_tool_use.py` fires correctly
- [ ] SubagentStop completion tracking in `subagent_stop.py` fires correctly
- [ ] Concurrent sessions maintain isolated registry entries
- [ ] Registry entries cleaned up after query completes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (registry)**
  - Name: registry-builder
  - Role: Implement session registry module and wire into all call sites
  - Agent Type: builder
  - Resume: true

- **Validator (registry)**
  - Name: registry-validator
  - Role: Verify all 4 call sites resolve correctly, heartbeat enriched, cleanup works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Session Registry Module
- **Task ID**: build-registry
- **Depends On**: none
- **Validates**: tests/unit/test_session_registry.py (create)
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/hooks/session_registry.py` with: `register()`, `resolve()`, `record_tool_use()`, `get_activity()`, `unregister()`, `_sweep_stale()`
- Module-level dict `_registry: dict[str, str]` mapping Claude Code UUID to bridge session ID
- Module-level dict `_activity: dict[str, dict]` tracking tool count and last 3 tool names per UUID
- TTL sweep removes entries older than 30 minutes

### 2. Wire Registration into SDKAgentClient
- **Task ID**: build-registration
- **Depends On**: build-registry
- **Validates**: tests/unit/test_session_registry.py
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: false
- In `sdk_client.py`, before `client.query()`, pre-register the bridge session ID
- In the `finally` block after `client.query()`, call `unregister()`
- Keep `env["VALOR_SESSION_ID"]` injection for subprocess use

### 3. Replace All 4 Hook Call Sites
- **Task ID**: build-hooks
- **Depends On**: build-registry
- **Validates**: tests/unit/test_session_registry.py
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: false
- `agent/health_check.py:417` -- replace `os.environ.get("VALOR_SESSION_ID")` with `session_registry.resolve()`
- `agent/hooks/pre_tool_use.py:167` -- same replacement
- `agent/hooks/subagent_stop.py:67` -- same replacement
- `agent/hooks/subagent_stop.py:305` -- same replacement
- Each call site: `from agent.hooks.session_registry import resolve; bridge_sid = resolve(input_data.get("session_id"))`

### 4. Enrich Heartbeat with Tool Activity
- **Task ID**: build-heartbeat
- **Depends On**: build-registry
- **Validates**: manual log inspection
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/messenger.py` `BackgroundTask._watchdog()`, import `get_activity()` from the registry
- Add tool count and last tool name to the heartbeat log line: `"running %ds, communicated=%s, tools=%d, last=%s"`
- Call `record_tool_use()` from `watchdog_hook()` in `health_check.py` (alongside existing activity stream)

### 5. Write Tests
- **Task ID**: build-tests
- **Depends On**: build-hooks, build-heartbeat
- **Assigned To**: registry-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit tests for registry: register/resolve/unregister lifecycle, concurrent keys, stale sweep, record_tool_use with last-3 tracking
- Unit test: resolve returns None for unknown UUID
- Unit test: unregister is idempotent

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: registry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify no remaining `os.environ.get("VALOR_SESSION_ID")` in hook files (grep check)
- Verify registry module has complete docstrings
- Lint and format check

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_session_registry.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/hooks/session_registry.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hooks/session_registry.py` | exit code 0 |
| No env var in hooks | `grep -rn 'os.environ.get.*VALOR_SESSION_ID' agent/health_check.py agent/hooks/pre_tool_use.py agent/hooks/subagent_stop.py` | exit code 1 |
| Registry imported | `grep -rn 'session_registry' agent/health_check.py agent/hooks/pre_tool_use.py agent/hooks/subagent_stop.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- the issue recon confirmed all assumptions and the solution approach is straightforward.
