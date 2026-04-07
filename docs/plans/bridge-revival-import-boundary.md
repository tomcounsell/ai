---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/yudame/ai/issues/758
last_comment_id:
---

# Bridge Revival Import Boundary

## Problem

`bridge/telegram_bridge.py` imports three revival-related functions from
`agent.agent_session_queue` that are not listed in the documented
[import boundary](../features/bridge-worker-architecture.md#import-boundary):

- `check_revival`
- `queue_revival_agent_session`
- `record_revival_cooldown`

The boundary doc lists only `enqueue_agent_session`, `register_callbacks`, and
`clear_restart_flag`. The enforcement test
`tests/unit/test_worker_entry.py::test_bridge_has_no_execution_function_imports`
uses a denylist of four execution functions and silently allows revival imports
to drift past the boundary.

**Current behavior:**
- Bridge imports revival functions inline at `bridge/telegram_bridge.py:1044-1049`.
- `check_revival` and `record_revival_cooldown` are called from message-receive flow at lines 1438, 1450.
- `queue_revival_agent_session` is called from the reply-handling flow at line 1083.
- Code, docs, and test are out of sync.

**Desired outcome:**
- Bridge imports from `agent.agent_session_queue` are restricted to the documented
  allowlist (`enqueue_agent_session`, `register_callbacks`, `clear_restart_flag`).
- Revival detection and queuing live in the worker domain (or a shared service the
  bridge calls via the existing allowed surface).
- The enforcement test is converted from denylist to allowlist so future drift is caught automatically.

## Prior Art

- **PR #(plan 90043ec7)**: "Plan: Decouple bridge from session execution: bridge does I/O only, worker owns all execution" -- the parent decoupling effort that established the import boundary. Revival was overlooked.
- No prior issues attempted to relocate the revival functions specifically.

## Architectural Impact

- **New dependencies**: None. Revival functions already exist in `agent.agent_session_queue`.
- **Interface changes**: Bridge stops importing 3 names; gains a single delegated entry point for revival via the existing allowed surface (or removes its inline call site entirely).
- **Coupling**: Decreases bridge -> agent_session_queue coupling from 6 imports to 3.
- **Data ownership**: Revival lifecycle (detection, prompt, cooldown, queueing) becomes worker-owned. The bridge only routes the user's reply text.
- **Reversibility**: Easy to revert; small surface area, behind a single helper.

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

- **Revival service relocation**: Keep `check_revival`, `queue_revival_agent_session`, `record_revival_cooldown` in `agent.agent_session_queue` but route bridge access through the existing worker boundary so the bridge does not import them directly.
- **Bridge revival hook**: Replace the three direct imports with a single `enqueue_agent_session(...)`-equivalent path for "user replied to revival prompt" messages. The bridge already has `enqueue_agent_session` allowed -- we extend it (or add one new allowed helper) to encompass revival-reply enqueuing and revival probing.
- **Allowlist enforcement test**: Convert `test_bridge_has_no_execution_function_imports` to assert that imports from `agent.agent_session_queue` are a *subset* of the documented allowed names.

### Flow

User sends Telegram message → bridge calls `maybe_check_revival(...)` (new allowed helper that wraps `check_revival` + `record_revival_cooldown`) → if revival info returned, bridge sends prompt via existing markdown helper → user replies → bridge detects "Unfinished work detected" parent text → bridge calls `enqueue_agent_session(..., revival_branch=...)` (extended) → worker handles the revival as a normal queued session.

### Technical Approach

- Add a thin allowed-surface helper(s) in `agent.agent_session_queue` that the bridge may import. Two options, plan recommends **Option 1**:
  - **Option 1 (preferred)**: Add `maybe_send_revival_prompt(...)` that internally calls `check_revival` and `record_revival_cooldown` and returns the prompt text (or None). Extend `enqueue_agent_session` to accept an optional `revival_branch` kwarg so the reply path uses the already-allowed function. Net new allowed import: 1 (`maybe_send_revival_prompt`).
  - **Option 2**: Document the 3 existing names in the boundary doc and add them to the allowlist. Rejected because it grows the bridge surface area against the spirit of the decoupling work.
- Update `docs/features/bridge-worker-architecture.md` to list the new allowed name.
- Convert the enforcement test to allowlist mode.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The bridge's revival reply handler currently has `except Exception as e: logger.debug(...)` at line 1091. Behavior is unchanged; existing logger assertion (if any) carries over.
- [ ] No new exception handlers introduced.

### Empty/Invalid Input Handling
- [ ] `maybe_send_revival_prompt` must return `None` for empty `working_dir_str` or missing project — covered by unit test.
- [ ] Revival reply path with no branch match in replied text must short-circuit without enqueuing — existing behavior, preserve.

### Error State Rendering
- [ ] If `maybe_send_revival_prompt` raises, the bridge must continue handling the message (existing try/except at the call site).

## Test Impact

- [ ] `tests/unit/test_worker_entry.py::TestImportDecoupling::test_bridge_has_no_execution_function_imports` — REPLACE: convert from denylist to allowlist. New name: `test_bridge_imports_from_agent_session_queue_match_allowlist`. Asserts the set of names imported from `agent.agent_session_queue` in `bridge/telegram_bridge.py` is a subset of the documented allowlist.
- [ ] Any existing unit tests that import `check_revival`, `queue_revival_agent_session`, or `record_revival_cooldown` from `agent.agent_session_queue` directly are unaffected — only the bridge's import surface changes. Verify with `grep -rn "from agent.agent_session_queue import" tests/`.
- [ ] Add a new unit test for `maybe_send_revival_prompt(...)` covering: returns None when no stale branches, returns prompt text + records cooldown when stale work exists.

## Rabbit Holes

- **Rewriting revival detection logic**: Out of scope. We are relocating the import boundary, not changing how revival decides what to revive.
- **Async refactor of `check_revival`**: Tempting but unrelated. Keep call signatures stable.
- **Generalizing the allowlist test to all bridge imports**: Worth doing later, but scope-creep here. Stay focused on `agent.agent_session_queue`.

## Risks

### Risk 1: Hidden caller of removed bridge imports
**Impact:** A different bridge module imports these names transitively and breaks at runtime.
**Mitigation:** `grep -rn "check_revival\|queue_revival_agent_session\|record_revival_cooldown" bridge/` before and after the change. Bridge unit tests must pass.

### Risk 2: Allowlist test misses re-exports
**Impact:** Someone re-exports a forbidden name through an allowed module.
**Mitigation:** Test parses `bridge/telegram_bridge.py` AST for `ImportFrom` nodes specifically targeting `agent.agent_session_queue`. Re-exports through other modules are explicitly out of scope (and would be caught by the denylist's broader sweep if needed in future).

## Race Conditions

No race conditions identified — this is a pure refactor of import boundaries and call indirection. All call sites remain in the same execution context with the same await semantics.

## No-Gos (Out of Scope)

- Changing revival detection behavior or heuristics.
- Moving revival functions to a new module path.
- Generalizing the boundary test to cover all bridge imports.
- Refactoring the bridge's reply-detection logic for revival prompts.

## Update System

No update system changes required — this is a purely internal refactor with no new dependencies, config, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. No MCP tool changes, no `.mcp.json` updates, no new tools exposed to the agent.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` import boundary section to add `maybe_send_revival_prompt` (or whichever helper name is chosen) to the allowed list.
- [ ] Add a paragraph noting that the enforcement test is now an allowlist (subset check), not a denylist.
- [ ] No new feature doc — existing `bridge-worker-architecture.md` is the canonical reference.

## Success Criteria

- [ ] `bridge/telegram_bridge.py` imports from `agent.agent_session_queue` are exactly: `enqueue_agent_session`, `register_callbacks`, `clear_restart_flag`, and (at most) one new revival helper (e.g., `maybe_send_revival_prompt`).
- [ ] `docs/features/bridge-worker-architecture.md` import boundary lists every name the bridge imports — and only those.
- [ ] `tests/unit/test_worker_entry.py` enforces the allowlist via subset check; `pytest tests/unit/test_worker_entry.py -q` passes.
- [ ] All existing bridge tests pass.
- [ ] Manual smoke: send a Telegram message in a project with stale branches; revival prompt fires; reply enqueues a revival session.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` clean.

## Team Orchestration

### Team Members

- **Builder (revival-boundary)**
  - Name: revival-boundary-builder
  - Role: Add revival helper to allowed surface, update bridge imports, convert test to allowlist
  - Agent Type: builder
  - Resume: true

- **Validator (revival-boundary)**
  - Name: revival-boundary-validator
  - Role: Verify imports match allowlist, run unit tests, smoke-check revival flow
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add allowed revival helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_revival_helper.py (create)
- **Assigned To**: revival-boundary-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `maybe_send_revival_prompt(project_key, working_dir, chat_id) -> dict | None` to `agent/agent_session_queue.py` that wraps `check_revival` + `record_revival_cooldown`.
- Extend `enqueue_agent_session` (or add minimal kwarg) to support the revival-reply enqueue path currently using `queue_revival_agent_session`.
- Add focused unit tests for the new helper.

### 2. Refactor bridge imports
- **Task ID**: build-bridge
- **Depends On**: build-helper
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: revival-boundary-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the 3 revival imports in `bridge/telegram_bridge.py` with a single import of the new helper.
- Update the message-receive flow (lines ~1438-1450) to use `maybe_send_revival_prompt`.
- Update the reply-handling flow (lines ~1083) to use the extended `enqueue_agent_session` path.
- Verify with `grep -n "from agent.agent_session_queue import" bridge/telegram_bridge.py`.

### 3. Convert enforcement test to allowlist
- **Task ID**: build-test
- **Depends On**: build-bridge
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: revival-boundary-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `test_bridge_has_no_execution_function_imports` with `test_bridge_imports_from_agent_session_queue_match_allowlist`.
- Use `ast.parse` to extract `ImportFrom` nodes targeting `agent.agent_session_queue` and assert names are a subset of the documented allowlist.
- Keep the docstring referencing the boundary doc.

### 4. Update boundary doc
- **Task ID**: document-boundary
- **Depends On**: build-test
- **Assigned To**: revival-boundary-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` import boundary section to match the new bridge imports.
- Note the allowlist enforcement test.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-helper, build-bridge, build-test, document-boundary
- **Assigned To**: revival-boundary-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_entry.py -q`.
- Run `pytest tests/unit/ -q -k "revival or worker_entry"`.
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Confirm `grep -n "from agent.agent_session_queue import" bridge/telegram_bridge.py` matches the allowlist.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Boundary test passes | `pytest tests/unit/test_worker_entry.py -x -q` | exit code 0 |
| Revival helper test passes | `pytest tests/unit/ -q -k revival` | exit code 0 |
| No forbidden bridge imports | `grep -E "check_revival\|queue_revival_agent_session\|record_revival_cooldown" bridge/telegram_bridge.py` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Should we name the new helper `maybe_send_revival_prompt` or split it into `check_revival_for_bridge` + `record_revival_cooldown_for_bridge`? The plan assumes one combined helper to minimize the bridge's allowed surface.
2. Should `enqueue_agent_session` be extended with a `revival_branch` kwarg, or should we add a second allowed helper `enqueue_revival_session`? Plan assumes extending `enqueue_agent_session` to keep the allowlist as small as possible.
