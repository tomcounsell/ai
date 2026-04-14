---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/945
last_comment_id:
---

# Harness Streaming Regression Gap

## Problem

A recent hotfix (`1bc35398`) silenced `_harness_send_cb` in `agent_session_queue.py` so streaming chunks from the CLI harness no longer bypass the nudge loop and arrive as mid-sentence Telegram fragments. The fix is correct, but it left the system in a fragile state: there is no regression test for the no-op contract, the unit tests for `get_response_via_harness` create a false impression that streaming delivery is tested end-to-end, and several dead API surfaces remain that could accidentally re-enable the bug with a single refactor.

**Current behavior:** `_harness_send_cb` is `pass`. Zero integration test coverage of this contract. Unit tests in `test_harness_streaming.py` assert `send_cb.call_count >= 1/2`, which is correct for isolation but misleads developers into thinking streaming reaches the output handler in production. Dead flush constants and the `send_cb` parameter remain callable by future callers. The `full_text` fallback at `sdk_client.py:1603` has no WARNING log. `docs/features/harness-abstraction.md` mentions Telegram suppression but not email transport.

**Desired outcome:** The no-op contract is locked in by an integration test; dead streaming API surface is removed or gated; the fallback path is observable; `harness-abstraction.md` covers email; the unit test file has a comment distinguishing isolation scope from the production contract.

## Freshness Check

**Baseline commit:** `e234f114bdb947b9cb639cb1853605d46be1f608`
**Issue filed at:** 2026-04-14T04:23:39Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/agent_session_queue.py:3585` (issue) → now line 3669 (after other commits landed). `_harness_send_cb` is `pass`, comment still present. Claim still holds.
- `agent/sdk_client.py:1483-1484` — `_HARNESS_FLUSH_INTERVAL = 3.0` and `_HARNESS_FLUSH_CHAR_THRESHOLD = 2000` still present at those exact lines. Dead at the only production call site.
- `agent/sdk_client.py:1505` — `send_cb` parameter still present in `get_response_via_harness`.
- `agent/sdk_client.py:1603` — `final = result_text if result_text is not None else full_text` still present; no WARNING log in the fallback branch.
- `agent/output_handler.py:207` — `TelegramRelayOutputHandler` early-return on empty string still present.
- `tests/unit/test_harness_streaming.py:69` — `assert send_cb.call_count >= 1` confirmed.
- `tests/unit/test_harness_streaming.py:212` — `assert send_cb.call_count >= 2` confirmed.
- `EmailOutputHandler` — lives in `bridge/email_bridge.py:200`, not `agent/output_handler.py`. The harness suppression comment at line 3669 of `agent_session_queue.py` does not mention email transport. Confirmed gap.

**Cited sibling issues/PRs re-checked:**
- PR #868 — merged 2026-04-10; added `get_response_via_harness()` and the streaming/batching tests. The flush constants and `send_cb` parameter were introduced here.
- Issues #780, #912, #913 — all referenced as prior art. Not blockers.

**Commits on main since issue was filed (touching referenced files):**
- `712638dd` fix(lifecycle): prevent stale-save index orphans — irrelevant
- `82186dcc` fix(bridge): hydrate reply-thread context — irrelevant
- `697f7489` fix(session-health): recover slugless dev sessions — irrelevant
- `b3756bbf` docs: cascade updates for PM communication and harness streaming hotfixes — irrelevant (doc-only)
- No commits changed `sdk_client.py`, `test_harness_streaming.py`, or `output_handler.py` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** None (grep found no other plans referencing `send_cb` or `_harness_send_cb`).

**Notes:** Line numbers in `agent_session_queue.py` drifted by ~84 lines since the issue was filed. All functional claims still hold.

## Prior Art

- **PR #868** (Add CLI harness abstraction for dev sessions Phases 1-2, merged 2026-04-10) — Introduced `get_response_via_harness()` and the streaming/batching infrastructure. Established the `send_cb` parameter and flush constants. Did not introduce the no-op — that was `1bc35398`.
- **Hotfix `1bc35398`** — Made `_harness_send_cb` a no-op. Correct fix; no test coverage added.

No prior failed attempts to fix this class of bug.

## Data Flow

Tracing the production path from harness output to Telegram delivery:

1. **Entry**: `_execute_agent_session()` (agent_session_queue.py) defines `_harness_send_cb` as `async def: pass`.
2. **Harness execution**: `get_response_via_harness()` spawns `claude -p --output-format stream-json` subprocess. `content_block_delta` events call `send_cb(buffer)` — which is the no-op.
3. **Result event**: When the harness emits `{"type": "result"}`, `result_text` is set. Accumulated `full_text` is the fallback if no result event fires.
4. **BackgroundTask**: `task.run(do_work(), send_result=True)` waits for the coroutine. On completion, `BossMessenger` routes the final string through `send_to_chat()` → `route_session_output()` → nudge loop.
5. **Output delivery**: `TelegramRelayOutputHandler.send()` (or `EmailOutputHandler.send()`) writes the final message to Redis outbox / SMTP — exactly once.

The bug was at step 2: `send_cb` was previously wired to a real handler, causing mid-sentence fragments to step 5 before the turn was complete. The no-op at step 2 is the fix.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies beyond the existing test suite and Python environment.

## Solution

### Key Elements

- **Integration test** (`tests/integration/test_harness_no_op_contract.py`): Spy on `TelegramRelayOutputHandler.send`, exercise `_execute_agent_session` with a mocked harness that emits streaming events and a final result, assert exactly one delivery call (final result) and zero calls during streaming.
- **Unit test comment block** (`tests/unit/test_harness_streaming.py`): Add a module-level docstring/comment distinguishing what these isolation tests verify vs. what the integration test must verify.
- **Dead surface removal** (`agent/sdk_client.py`): Remove `send_cb` parameter from `get_response_via_harness` (making it `-> str` only) and remove `_HARNESS_FLUSH_INTERVAL` / `_HARNESS_FLUSH_CHAR_THRESHOLD` constants, or add a `stream: bool = False` guard making streaming an explicit opt-in. Full removal is simpler and matches current usage.
- **Fallback WARNING log** (`agent/sdk_client.py`): Add `logger.warning(...)` in the `full_text` fallback branch so operators can distinguish crash fallback from clean result delivery.
- **Suppression comment update** (`agent/agent_session_queue.py`): Update the `_harness_send_cb` comment to mention email transport, not just Telegram.
- **Docs update** (`docs/features/harness-abstraction.md`): Update the streaming chunk suppression section to explicitly state that `EmailOutputHandler` is equally affected.

### Flow

`_execute_agent_session` → calls `get_response_via_harness(message, working_dir, env)` (no `send_cb`) → harness accumulates result event → returns final string → `BackgroundTask` sends through nudge loop → `TelegramRelayOutputHandler` or `EmailOutputHandler` delivers once.

### Technical Approach

**Option A (preferred): Remove `send_cb` entirely from `get_response_via_harness`.**
- The function signature becomes `async def get_response_via_harness(message, working_dir, env) -> str`.
- The flush constants are removed.
- Internal buffer still accumulates `full_text` as crash fallback — no behavioral change.
- All callers updated (only one production call site: `agent_session_queue.py`).
- The 18 unit tests in `test_harness_streaming.py` that pass `send_cb=AsyncMock()` are updated to not pass `send_cb` and their `send_cb.call_count` assertions are removed (the function no longer calls any callback — the return value is the contract).

**Option B: Add `stream: bool = False` guard.**
- Keep `send_cb` as an optional parameter; only call it when `stream=True`.
- More future-proof for a hypothetical streaming UI, but adds complexity now.
- Rejected: no concrete streaming UI plans exist; YAGNI.

**Integration test strategy:**
- Use `unittest.mock.patch` to replace `get_response_via_harness` with a coroutine that yields synthetic streaming then a final result string.
- Alternatively, mock at the subprocess level (replace `asyncio.create_subprocess_exec` to emit synthetic NDJSON lines).
- Spy on `TelegramRelayOutputHandler.send` as a MagicMock.
- Assert: `send.call_count == 1`, call argument contains the final result text.
- The integration test lives in `tests/integration/` because it exercises the `_execute_agent_session` function boundary (not just `get_response_via_harness` in isolation).

**Fallback WARNING:**
```python
if result_text is None:
    logger.warning("Harness exited without a result event — falling back to accumulated text (%d chars)", len(full_text))
final = result_text if result_text is not None else full_text
```

## Failure Path Test Strategy

### Exception Handling Coverage
- The `_harness_send_cb` no-op comment update is documentation only — no exception handlers added.
- The `full_text` fallback branch adds a `logger.warning(...)` — no new exception handler; the existing non-zero exit code warning already covers subprocess failures.
- No `except Exception: pass` blocks are added by this work.

### Empty/Invalid Input Handling
- `get_response_via_harness` already returns `"Error: harness produced no output."` when both `result_text` is None and `full_text` is empty. No change to this path.
- The integration test should assert behavior when the harness emits only streaming chunks (no result event) — the fallback WARNING should fire and the accumulated text should be returned.

### Error State Rendering
- The WARNING log added to the fallback branch is the observable signal for ops — it reaches logs, not the user.
- The user always receives whatever text is returned from the harness function (or the error sentinel string). No user-visible change.

## Test Impact

- [ ] `tests/unit/test_harness_streaming.py` — UPDATE: All 18 tests that pass `send_cb=AsyncMock()` to `get_response_via_harness` must be updated to omit `send_cb` (removed parameter). `assert send_cb.call_count >= 1/2` assertions are removed. Add module-level comment block explaining isolation scope vs. production contract.
- [ ] No other existing tests are affected — `_harness_send_cb` is an internal function in `agent_session_queue.py` with no direct test coverage today.

## Rabbit Holes

- **Real streaming UI**: Implementing actual real-time streaming to Telegram. Not in scope; the no-op is the correct production behavior.
- **Retry logic for harness failures**: The fallback path is a crash safety net, not a retry mechanism. Adding retry logic is a separate concern.
- **`BackgroundTask` refactor**: The delivery path through `BossMessenger` is working correctly. Do not touch it.
- **Email fragment guard**: Adding a fragment guard to `EmailOutputHandler.send` is defensive but unnecessary while `_harness_send_cb` is a no-op. Not in scope.

## Risks

### Risk 1: Removing `send_cb` breaks a non-obvious caller
**Impact:** Build fails or a hidden call site breaks at runtime.
**Mitigation:** `grep -rn "get_response_via_harness"` confirms only one production call site (`agent_session_queue.py:3678`) and 18 unit test call sites. All will be updated.

### Risk 2: Integration test is too coupled to internal implementation
**Impact:** Test breaks on harmless refactors, adds maintenance burden.
**Mitigation:** The integration test mocks at the function boundary (`get_response_via_harness` return value), not at the subprocess level. The only invariant asserted is: `TelegramRelayOutputHandler.send` is called exactly once with the final result string. This is a behavioral contract, not an implementation detail.

## Race Conditions

No race conditions identified — this work is confined to synchronous parameter removal, logging additions, comment updates, and test additions. No shared state or async coordination paths are modified.

## No-Gos (Out of Scope)

- Real-time streaming delivery to Telegram (a streaming UI feature)
- Adding a fragment guard to `TelegramRelayOutputHandler` or `EmailOutputHandler`
- Refactoring `BackgroundTask` or `BossMessenger`
- Email transport improvements beyond a comment/doc update
- Any change to the nudge loop routing logic

## Update System

No update system changes required — this work is purely internal to `agent/sdk_client.py`, `agent/agent_session_queue.py`, tests, and docs. No new config, no new dependencies, no migration steps.

## Agent Integration

No agent integration required — this is an internal harness pipeline fix. No MCP servers, no `.mcp.json` changes, no bridge changes. The agent's external behavior (Telegram message delivery) is unchanged.

## Documentation

- [ ] Update `docs/features/harness-abstraction.md` — Streaming Chunk Suppression section: add explicit mention that `EmailOutputHandler` is equally affected by the no-op suppression. Remove the stale "Batched text delivery via send_cb" flowchart entry (it implied streaming was active).
- [ ] Update `docs/features/harness-abstraction.md` — Streaming and Batching section: remove or strike through the flush trigger bullets (`_HARNESS_FLUSH_INTERVAL`, `_HARNESS_FLUSH_CHAR_THRESHOLD`) since those constants are removed by this work.
- [ ] Update `docs/features/harness-abstraction.md` — Key Files table: reflect that `test_harness_streaming.py` now covers isolation only, and note the new `tests/integration/test_harness_no_op_contract.py` file.
- [ ] Add inline docstring to `get_response_via_harness` clarifying that it returns the final result string only — no streaming callback, no intermediate delivery.

## Success Criteria

- [ ] Integration test in `tests/integration/test_harness_no_op_contract.py` passes: `TelegramRelayOutputHandler.send` called exactly once (final result), zero calls during harness streaming event emission.
- [ ] `get_response_via_harness` no longer has a `send_cb` parameter; `_HARNESS_FLUSH_INTERVAL` and `_HARNESS_FLUSH_CHAR_THRESHOLD` are removed.
- [ ] `full_text` fallback path at `sdk_client.py` logs a `WARNING` when activated.
- [ ] `_harness_send_cb` comment in `agent_session_queue.py` mentions email transport.
- [ ] `docs/features/harness-abstraction.md` Streaming Chunk Suppression section mentions email transport.
- [ ] `tests/unit/test_harness_streaming.py` has a module-level comment block explaining isolation scope.
- [ ] `pytest tests/unit/test_harness_streaming.py` passes after `send_cb` parameter removal.
- [ ] `pytest tests/integration/test_harness_no_op_contract.py` passes.
- [ ] `python -m ruff check . && python -m ruff format --check .` clean.

## Team Orchestration

### Team Members

- **Builder (harness-cleanup)**
  - Name: harness-builder
  - Role: Remove `send_cb` from `get_response_via_harness`, add WARNING log, update comment and doc
  - Agent Type: builder
  - Resume: true

- **Test Engineer (integration-test)**
  - Name: test-engineer
  - Role: Write integration test asserting the no-op delivery contract
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Run full test suite, verify success criteria
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, test-engineer, validator, documentarian

## Step by Step Tasks

### 1. Remove dead streaming API from `get_response_via_harness`
- **Task ID**: build-harness-cleanup
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py`
- **Assigned To**: harness-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `send_cb` parameter from `get_response_via_harness` signature and all internal call sites (`await send_cb(buffer)` in the flush loop and final flush).
- Remove module-level constants `_HARNESS_FLUSH_INTERVAL` and `_HARNESS_FLUSH_CHAR_THRESHOLD`.
- Remove `send_cb=_harness_send_cb` kwarg from the single call site in `agent_session_queue.py`. Keep `_harness_send_cb` definition removal for the next sub-task, or remove it here if it has no other usages.
- Add `logger.warning("Harness exited without result event — falling back to accumulated streaming text (%d chars)", len(full_text))` in the `full_text` fallback branch of `get_response_via_harness` (before `final = result_text if result_text is not None else full_text`), gated on `result_text is None`.
- Update the `_harness_send_cb` comment in `agent_session_queue.py` to read: "Streaming chunks from the CLI harness are suppressed for all session types (Telegram and email). Forwarding them bypasses the nudge loop and sends mid-sentence fragments directly. BackgroundTask delivers the final result instead." Then remove the now-unused `_harness_send_cb` function and its call site.
- Update all 18 call sites in `tests/unit/test_harness_streaming.py` that pass `send_cb=AsyncMock()` — remove the `send_cb` kwarg and all `send_cb.call_count` assertions. Add a module-level docstring comment block at the top of the file: "NOTE: These tests verify `get_response_via_harness()` in isolation — they confirm that the function correctly parses harness NDJSON, accumulates text, and returns the result string. In production, no streaming callback is passed to this function; the no-op suppression contract is validated in `tests/integration/test_harness_no_op_contract.py`."
- Update `docs/features/harness-abstraction.md`: In the "Streaming and Batching" section, remove the flush trigger bullets (now dead). In the "Streaming Chunk Suppression" section, update text to explicitly mention: "This applies equally to all output transports — `TelegramRelayOutputHandler` and `EmailOutputHandler` — no transport receives real-time streaming output."
- Run `python -m ruff format . && python -m ruff check .` and fix any issues.
- Run `pytest tests/unit/test_harness_streaming.py -v` and confirm all tests pass.

### 2. Write integration test for the no-op delivery contract
- **Task ID**: build-integration-test
- **Depends On**: none
- **Validates**: `tests/integration/test_harness_no_op_contract.py` (create)
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_harness_no_op_contract.py`.
- The test mocks `get_response_via_harness` (patched in `agent.agent_session_queue`) to return a final result string directly, bypassing the subprocess. This is sufficient to validate the delivery contract at the `_execute_agent_session` boundary without spawning real subprocesses.
- Alternatively, mock `asyncio.create_subprocess_exec` to emit synthetic NDJSON lines (streaming events then a result event) — this tests the full harness parsing path too. Choose whichever approach is simpler given the existing test patterns in `tests/integration/`.
- Spy on `TelegramRelayOutputHandler.send` (or the Redis outbox write) as a `MagicMock` / `AsyncMock`.
- Exercise the harness execution path (either directly call `_execute_agent_session` with a minimal `AgentSession` fixture, or use an existing test helper if one exists).
- Assert: the output handler's `send` (or Redis write) is called exactly once. Assert: the single call contains the final result string (not a streaming fragment).
- Include a negative assertion: if the harness mock emits streaming chunks, `send` is NOT called during those emissions.
- Name the test class `TestHarnessNoOpDeliveryContract` and the primary test `test_streaming_chunks_suppressed_single_delivery`.
- Add a test `test_fallback_warning_logged` that confirms a `WARNING` log fires when the harness mock returns without a result event (full_text fallback path).
- Run `pytest tests/integration/test_harness_no_op_contract.py -v` and confirm all tests pass.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-harness-cleanup, build-integration-test
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_harness_streaming.py tests/integration/test_harness_no_op_contract.py -v`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Verify `get_response_via_harness` in `agent/sdk_client.py` no longer has a `send_cb` parameter.
- Verify `_HARNESS_FLUSH_INTERVAL` and `_HARNESS_FLUSH_CHAR_THRESHOLD` are gone from `sdk_client.py`.
- Verify `docs/features/harness-abstraction.md` mentions email transport in the suppression section.
- Report pass/fail for each criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_harness_streaming.py -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_harness_no_op_contract.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| send_cb removed | `grep -n "send_cb" agent/sdk_client.py` | exit code 1 |
| Flush constants removed | `grep -n "_HARNESS_FLUSH" agent/sdk_client.py` | exit code 1 |
| Email mentioned in doc | `grep "email" docs/features/harness-abstraction.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — scope is well-defined by the issue recon. Ready to proceed to critique.
