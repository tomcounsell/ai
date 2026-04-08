---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/827
last_comment_id: null
---

# Fix PM Session Teammate Restriction Injection

## Problem

When a PM-role agent session is created via `python -m tools.valor_session create --role pm`, the spawned Claude Code subprocess receives a **"RESTRICTION: This user has read-only Teammate access"** message injected into its context — overriding the project-manager persona and blocking all SDLC work.

**Current behavior:** PM sessions created without a Telegram chat title (e.g., CLI-created sessions with `chat_id=0`) are treated as direct messages (`is_dm=True`). The `build_context_prefix()` function in `bridge/context.py` applies the Teammate read-only restriction to all DMs unconditionally, without checking the session's actual role. The spawned subprocess sees the restriction, recognises the SDLC request as valid, and refuses.

**Desired outcome:** PM sessions are never restricted by the Teammate read-only guard, regardless of chat origin. The `session_type` field is the authoritative signal for permissions — not `is_dm`.

## Prior Art

- **PR #652**: Rename SessionType.CHAT to PM + add TEAMMATE as first-class type — Introduced the PM/TEAMMATE role split but did not update `build_context_prefix` to use `session_type`.
- **PR #796**: Purge ChatSession/DevSession vocabulary; use AgentSession with role — Completed the vocabulary purge but left `build_context_prefix`'s `is_dm` parameter in place as a leaky abstraction.
- **PR #813**: fix: local session type now reflects SESSION_TYPE env var — Fixed a related issue (session type resolution for local/CLI sessions) but did not address the restriction injection ordering bug.

## Data Flow

1. **Entry point**: Worker creates `AgentSession` with `session_type="pm"` and `chat_id=0` (no Telegram chat)
2. **`agent/sdk_client.py:1474`**: Imports `build_context_prefix` from `bridge.context`
3. **`agent/sdk_client.py:1476`**: Calls `build_context_prefix(project, chat_title is None, sender_id)` — `chat_title is None` evaluates to `True`, so `is_dm=True`
4. **`bridge/context.py:130`**: `if is_dm:` fires, injects the Teammate restriction string into `context_parts`
5. **`agent/sdk_client.py:1493`**: `_session_type = None` is set — resolution happens AFTER the restriction was already injected
6. **`agent/sdk_client.py:1500`**: `_session_type = "pm"` is resolved from Redis — too late; the enriched message already contains the restriction
7. **Output**: Claude Code subprocess receives the restriction in its first message and refuses SDLC work

## Architectural Impact

- **Interface changes**: `build_context_prefix(project, is_dm: bool, sender_id)` → `build_context_prefix(project, session_type: str | None, sender_id)`. All three call sites must update.
- **Call sites**: 3 total — `agent/sdk_client.py:1476`, `bridge/catchup.py:168`, `bridge/reconciler.py:148`
- **Coupling**: Decreases coupling — the function no longer conflates DM origin with role. `session_type` is the single authoritative signal.
- **Reversibility**: Easy — the change is localized to one function signature and three call sites.

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

- **`bridge/context.py`**: Replace `is_dm: bool` parameter in `build_context_prefix` with `session_type: str | None`. The Teammate restriction fires only when `session_type == "teammate"`. Remove all `is_dm` usage inside this function.
- **`agent/sdk_client.py`**: Move the `_session_type` resolution block (lines 1493–1501) to BEFORE the `build_context_prefix` call (currently line 1476). Pass `_session_type` to `build_context_prefix`.
- **`bridge/catchup.py` and `bridge/reconciler.py`**: Update call sites to pass `session_type=None` (neither is a Teammate DM context — restriction should never fire for these paths).

### Flow

CLI creates PM session → Worker picks up session → `sdk_client.py` resolves `_session_type="pm"` FIRST → `build_context_prefix(project, session_type="pm", sender_id)` called → no restriction injected (only fires for `session_type="teammate"`) → PM persona context injected → SDLC work proceeds

### Technical Approach

- Move `_session_type` resolution ~20 lines earlier in `sdk_client.py` (before line 1476)
- Change `build_context_prefix` signature from `is_dm: bool` to `session_type: str | None`
- Change restriction guard from `if is_dm:` to `if session_type == SessionType.TEAMMATE:`
- Remove the `is_dm`-specific "Direct message to Valor" context line (replace with `if not project and not session_type:` or just remove — it was a fallback for DM-with-no-project context, not needed for structured sessions)
- Update 3 call sites to pass `session_type` instead of `is_dm`

## Failure Path Test Strategy

### Exception Handling Coverage

- `bridge/context.py`: No exception handlers in scope — `build_context_prefix` is a pure function.
- `agent/sdk_client.py`: `_session_type` resolution is already wrapped in `try/except Exception: pass` at line 1501. After reordering, the `except` block will default `_session_type = None`, which correctly means "no restriction applied." This is safe behavior.

### Empty/Invalid Input Handling

- `build_context_prefix(project=None, session_type=None)` → no restriction, empty string returned. Test this.
- `build_context_prefix(project=None, session_type="teammate")` → restriction injected, DM context string. Test this.
- `build_context_prefix(project=None, session_type="pm")` → no restriction, no project context. Test this.

### Error State Rendering

- No user-visible output from this function directly — it builds a prefix injected into the agent's first message. Error states (empty prefix, missing project) are benign — Claude receives less context but no restriction.

## Test Impact

- [ ] `tests/unit/test_bridge_logic.py::TestBuildContextPrefix` — UPDATE: replace all `is_dm=True/False` calls with `session_type="teammate"/"pm"/None`; update the local `build_context_prefix` stub (lines 91-111) to match new signature; add new tests for PM and Dev session types receiving no restriction
- [ ] `tests/e2e/test_message_pipeline.py::TestContextBuilding` — UPDATE: update calls at lines 212-224 to use new signature (`session_type=None` for non-DM, `session_type="teammate"` for DM case)
- [ ] `tests/unit/test_pm_channels.py` — UPDATE: 3 `patch("bridge.context.build_context_prefix", return_value="")` patches (lines 90, 120, 152) do not use the signature and are unaffected; no changes needed
- [ ] `tests/unit/test_cross_repo_gh_resolution.py:101` — UPDATE: `patch("bridge.context.build_context_prefix", ...)` — same: returns mock, signature not inspected; no change needed
- [ ] `tests/integration/test_message_routing.py` — UPDATE: local `build_context_prefix` stub at line 96 uses `is_dm: bool` — update to `session_type: str | None`; update calls at lines 169, 384, 385

## Rabbit Holes

- **Auditing all `is_dm` usages in `bridge/routing.py`** — `is_dm` there is used for Telegram event origin (group vs. private chat) which is a legitimate and separate concern. Do not touch these.
- **Removing `is_dm` from `_resolve_persona` in `sdk_client.py`** — `_resolve_persona` at line 1671 uses `is_dm` to set the `persona` variable for persona loading. This is a different signal path (persona config, not permission restriction) and is out of scope for this bug fix.
- **Refactoring all DM handling** — This bug is specifically about the restriction injection. Broader DM handling is out of scope.

## Risks

### Risk 1: DM-from-Telegram Teammate restriction regresses
**Impact:** Real Telegram DM users (non-agent) would lose the Teammate read-only restriction, potentially getting full permissions.
**Mitigation:** Telegram DM messages go through `bridge/telegram_bridge.py` which sets `is_dm = event.is_private`. The `session_type` for these AgentSessions is set to `"teammate"` by the bridge routing logic (confirmed in `bridge/routing.py:207` — `if is_dm: return SessionType.TEAMMATE`). So passing `session_type="teammate"` to `build_context_prefix` correctly fires the restriction for real Telegram DMs. Write a regression test to confirm.

### Risk 2: `bridge/catchup.py` and `bridge/reconciler.py` pass wrong session_type
**Impact:** If catchup or reconciler sessions should have Teammate restrictions in some cases, passing `None` would silently drop the restriction.
**Mitigation:** Both files hardcode `False` for `is_dm` today, meaning the restriction never fires for these paths currently. Passing `session_type=None` preserves existing behavior. No regression risk.

## Race Conditions

No race conditions identified — `_session_type` resolution is a synchronous Redis read. Moving it 20 lines earlier in a sequential function does not introduce concurrency concerns.

## No-Gos (Out of Scope)

- Refactoring `is_dm` out of `bridge/routing.py` or `bridge/telegram_bridge.py`
- Changing persona resolution logic in `_resolve_persona`
- Modifying Teammate permission levels
- Any changes to `bridge/catchup.py` or `bridge/reconciler.py` beyond the call site update

## Update System

No update system changes required — this fix is purely internal to `bridge/context.py` and `agent/sdk_client.py`. No new dependencies, no config files, no migration needed.

## Agent Integration

No agent integration changes required — this is a bridge/sdk-internal fix that corrects context injection. No MCP server changes, no `.mcp.json` changes, no tool wrapping needed. The fix is transparent to the agent's external interface.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` to note that `build_context_prefix` uses `session_type` (not `is_dm`) for permission guards — a one-line addition to the existing context-building description.
- [ ] No new feature doc needed — this is a bug fix restoring intended behavior.

## Success Criteria

- [ ] `build_context_prefix` accepts `session_type: str | None` instead of `is_dm: bool`
- [ ] PM-role sessions (`session_type="pm"`) receive no Teammate read-only restriction in their context prefix
- [ ] Teammate-role sessions (`session_type="teammate"`) still receive the restriction (no regression)
- [ ] Dev-role sessions (`session_type="dev"`) receive no restriction
- [ ] `session_type` is resolved BEFORE `build_context_prefix` is called in `sdk_client.py`
- [ ] All 3 call sites updated: `sdk_client.py:1476`, `catchup.py:168`, `reconciler.py:148`
- [ ] Unit tests in `test_bridge_logic.py` updated and passing with new assertion for PM/Dev/Teammate context prefixes
- [ ] All affected tests updated and passing (`pytest tests/unit/ -x -q`)

## Team Orchestration

### Team Members

- **Builder (context-fix)**
  - Name: context-fix-builder
  - Role: Update `build_context_prefix` signature and all call sites; reorder `_session_type` resolution in `sdk_client.py`
  - Agent Type: builder
  - Resume: true

- **Validator (tests)**
  - Name: test-validator
  - Role: Verify all tests pass and new PM/Teammate/Dev assertions are present
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Update `build_context_prefix` signature and call sites
- **Task ID**: build-context-fix
- **Depends On**: none
- **Validates**: `tests/unit/test_bridge_logic.py`, `tests/e2e/test_message_pipeline.py`, `tests/integration/test_message_routing.py`
- **Assigned To**: context-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- In `bridge/context.py`: change `build_context_prefix(project, is_dm: bool, sender_id=None)` → `build_context_prefix(project, session_type: str | None, sender_id=None)`; change `if is_dm:` → `if session_type == SessionType.TEAMMATE:`; remove `is_dm`-specific "Direct message" context line (or replace with `if not project:` generic fallback)
- In `agent/sdk_client.py`: move `_session_type` resolution block (lines 1493–1501) to before the `build_context_prefix` call (line 1476); update call to pass `_session_type` instead of `chat_title is None`
- In `bridge/catchup.py:168`: update call to pass `session_type=None`
- In `bridge/reconciler.py:148`: update call to pass `session_type=None`
- Update `tests/unit/test_bridge_logic.py`: replace `is_dm=True/False` with `session_type` calls; update local stub signature; add test `test_pm_session_no_restriction` and `test_teammate_session_restriction_present`
- Update `tests/e2e/test_message_pipeline.py`: update calls at `TestContextBuilding` to new signature
- Update `tests/integration/test_message_routing.py`: update local stub and call sites

### 2. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-context-fix
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` — verify all pass
- Run `pytest tests/e2e/ -x -q` — verify context building tests pass
- Run `python -m ruff check bridge/context.py agent/sdk_client.py bridge/catchup.py bridge/reconciler.py`
- Confirm `build_context_prefix` no longer accepts `is_dm` anywhere
- Confirm `grep -n "is_dm" bridge/context.py` returns zero results

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| E2E tests pass | `pytest tests/e2e/ -x -q` | exit code 0 |
| No is_dm in context.py | `grep -n "is_dm" bridge/context.py` | exit code 1 |
| Lint clean | `python -m ruff check bridge/context.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/context.py agent/sdk_client.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
