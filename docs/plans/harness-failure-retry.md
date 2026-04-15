---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/977
last_comment_id: null
revision_applied: true
---

# Harness Startup Failure Retry with Persona-Aligned Messages

## Problem

When the `claude` binary is missing from PATH (e.g., after a bridge restart in a shell that didn't inherit the right environment), the harness immediately fails and sends a raw Python exception string to Telegram — and the original request is silently lost.

**Current behavior:**
- `_run_harness_subprocess()` in `agent/sdk_client.py` catches `FileNotFoundError` and returns `("Error: CLI harness not found — [Errno 2] No such file or directory: 'claude'", None, None)`
- `get_response_via_harness()` propagates that string as the result
- `BackgroundTask._run_work()` sees a non-empty result and sends it via `self.messenger.send()`
- The user receives a raw technical error string in Telegram
- No retry is attempted; the session completes and is cleaned up; the original request is gone

**Desired outcome:**
- Transient `FileNotFoundError` failures are retried silently up to 3 times before any Telegram message is sent
- After 3 failures, exactly one persona-aligned message is delivered: "Tried a few times but couldn't get Claude to start — looks like the CLI may not be on PATH. You can resend once that's sorted."
- Other harness failures (non-FileNotFoundError) also produce persona-aligned messages, not raw exception strings _(de-scoped: only FileNotFoundError is retried; other errors surface as-is — see No-Gos)_

## Freshness Check

**Baseline commit:** `ab724843dfdf45f69d155e703def9824593cc768`
**Issue filed at:** 2026-04-15T03:22:15Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/sdk_client.py:1683-1685` — `FileNotFoundError` catch in `_run_harness_subprocess()` — confirmed still present, line moved from ~1592 cited in issue to 1683
- `agent/messenger.py:151-152` — `BackgroundTask._run_work()` sends result without error-pattern check — confirmed still present
- `agent/agent_session_queue.py:145,2637` — `_extract_agent_session_fields()` and `_enqueue_nudge()` — both confirmed present and unchanged

**Cited sibling issues/PRs re-checked:**
- Stall-retry and health monitor are mentioned in the issue body as existing systems that don't cover this case — both confirmed still present and separate from the targeted fix path

**Commits on main since issue was filed (touching referenced files):**
- `aea1c1a0` fix(harness): session continuity via --resume with unconditional context budget (#976) — irrelevant to retry logic, touches `sdk_client.py` but only adds stale-UUID fallback path

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** The issue cited `sdk_client.py:1592` for the error return; current line is 1683. The error string constant `"Error: CLI harness not found — "` is unchanged.

## Prior Art

- **PR #957** fix(harness): remove dead send_cb API, lock no-op contract with integration test — refactored harness streaming, not related to retry
- **PR #958** / **Issue #958** (closed): Separator overflow crashes — different error class, handled in `BackgroundTask._run_work()` via `err_str` pattern match (line ~178). Demonstrates the existing pattern of intercepting result strings before sending.
- **Issue #976** / **PR #981**: session continuity via --resume — adds stale-UUID retry in harness, different failure mode

No prior attempts to add retry logic for `FileNotFoundError` specifically.

## Data Flow

1. **Worker pops AgentSession** from Redis queue
2. **`agent_session_queue.py`** calls `get_response_via_harness()` via `BackgroundTask.run(do_work())`
3. **`_run_harness_subprocess()`** in `sdk_client.py` calls `asyncio.create_subprocess_exec()` — throws `FileNotFoundError` if `claude` not on PATH
4. **`_run_harness_subprocess()`** catches `FileNotFoundError`, returns `("Error: CLI harness not found — ...", None, None)`
5. **`get_response_via_harness()`** receives the tuple, returns the error string as `result_text`
6. **`BackgroundTask._run_work()`** sets `self._result = <error string>` and calls `self.messenger.send(self._result)`
7. **Telegram** receives the raw error string

**After the fix:**
- Step 4 returns a sentinel tuple indicating a transient failure (e.g., `returncode=None`)
- Step 5 detects the sentinel and checks `cli_retry_count` stored in `extra_context`
- If `agent_session is None`: return raw unchanged (preserve existing behavior — no retry without a session record)
- If `cli_retry_count < 3`: update `extra_context["cli_retry_count"]`, call `transition_status(agent_session, "pending", "harness-retry")` to reuse the existing record, log a warning, return `""` (BackgroundTask skips send on empty)
- If `cli_retry_count >= 3`: return the persona-aligned message string
- Step 6 delivers persona-aligned message (or skips on empty)

## Architectural Impact

- **New field on `AgentSession.extra_context`**: `cli_retry_count` stored as an integer. Uses the existing `extra_context` DictField — no model schema change needed.
- **One new import**: `transition_status` from `models.session_lifecycle`. Already used elsewhere in the module.
- **No interface changes**: `get_response_via_harness()` signature unchanged. The detection and retry logic lives in `agent_session_queue.py` (the caller), not inside `sdk_client.py`.
- **Coupling**: No new coupling introduced. The retry path uses `transition_status()` — the same mechanism used for all non-terminal status moves throughout the codebase.
- **Reversibility**: Removing the change requires reverting the `do_work()` interception and the `transition_status()` call. Low risk.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this is a pure Python change to existing internal modules with no new external dependencies.

## Solution

### Key Elements

- **Transient error sentinel**: `_run_harness_subprocess()` already returns `(error_string, None, None)` (returncode=None) for `FileNotFoundError`. This `returncode=None` is the sentinel — no new sentinel type needed.
- **Retry counter in `extra_context`**: `cli_retry_count` key stored in `AgentSession.extra_context`. Reading and writing uses the same dict-field pattern as `revival_context` and `classification_type`.
- **Re-queue on retry**: If `agent_session is None`, return `raw` immediately (B1 guard). If `cli_retry_count < 3`, update `extra_context["cli_retry_count"]`, call `transition_status(agent_session, "pending", "harness-retry")` to move the existing record back to pending (B2 — no new record created), then return `""` from `do_work()` so `BackgroundTask` skips the send.
- **Persona message on exhaustion**: If `cli_retry_count >= 3`, return the human-voiced message from `do_work()` so it is delivered normally.
- **Deterministic (non-transient) failures**: Any other harness result that is a raw error string (starts with `"Error:"`) gets mapped to a persona-aligned message regardless of retry — they never retry because the binary exists but produced a bad result.

### Flow

Telegram message → AgentSession created → Worker pops → harness fails with FileNotFoundError → guard: `agent_session is None`? → return raw if None → `cli_retry_count` checked → if < 3: `transition_status("pending", "harness-retry")` + counter increment + warning log → worker pops same record again → if >= 3: persona-aligned message delivered to Telegram

### Technical Approach

The fix is concentrated in one place: the `do_work()` coroutine and the post-completion logic in `agent_session_queue.py` around line 3798 (the harness call site).

**Change 1 — Intercept harness error result in the caller (`agent_session_queue.py`)**

Wrap `get_response_via_harness()` in a check that runs before sending. If the result starts with `"Error: CLI harness not found"` (returncode=None from `_run_harness_subprocess`), it is a transient startup failure.

**Critical guard (B1):** `agent_session` is initialized to `None` at line 3328 and only set if the Redis query finds a running session. All accesses to `agent_session` in the retry block must be guarded. If `agent_session is None`, skip the retry path and return `raw` unchanged — preserving the existing behavior:

```python
_HARNESS_NOT_FOUND_PREFIX = "Error: CLI harness not found"
_HARNESS_NOT_FOUND_MAX_RETRIES = 3

async def do_work() -> str:
    raw = await get_response_via_harness(...)
    if raw.startswith(_HARNESS_NOT_FOUND_PREFIX):
        # Guard: agent_session may be None if Redis lookup failed (race/recovery path)
        if agent_session is None:
            return raw
        # transient — attempt silent retry
        ec = agent_session.extra_context or {}
        retry_count = int(ec.get("cli_retry_count", 0))
        if retry_count < _HARNESS_NOT_FOUND_MAX_RETRIES:
            ec["cli_retry_count"] = retry_count + 1
            # Re-queue by transitioning the existing record (B2 — do NOT create a new one)
            agent_session.extra_context = ec
            await asyncio.to_thread(
                transition_status, agent_session, "pending", "harness-retry"
            )
            _ensure_worker(agent_session.worker_key, ...)
            logger.warning(
                "[%s] Harness not found — retry %d/%d",
                agent_session.session_id,
                retry_count + 1,
                _HARNESS_NOT_FOUND_MAX_RETRIES,
            )
            return ""  # BackgroundTask.send skips empty result
        else:
            return (
                "Tried a few times but couldn't get Claude to start — "
                "looks like the CLI may not be on PATH. "
                "You can resend once that's sorted."
            )
    return raw
```

Import `transition_status` from `models.session_lifecycle` alongside existing imports at the top of `agent_session_queue.py`.

**Why `transition_status` instead of `async_create` (B2):** `async_create()` would create a second Redis record while leaving the original in `status="running"`. The health monitor would eventually find the ghost running record and may spawn a duplicate execution or mark it permanently zombie. `transition_status()` updates the existing record in-place, updating secondary indexes correctly and keeping a single canonical record — matching the established contract for non-terminal status moves (e.g., `_enqueue_nudge()` at line 2712–2726).

**Change 2 — Skip finalization when silently re-queuing**

When `do_work()` returns `""` after a re-queue, the post-task code must NOT finalize the session to `"completed"` — it has already been transitioned to `"pending"` by `transition_status()`. Guard with a bool flag:

Implementation: use a `bool` flag `_harness_requeued` set inside `do_work()` scope and read in the finalization block. The flag is set synchronously before returning `""`, so no race is possible.

**Change 3 — No changes to `sdk_client.py`**

The error return from `_run_harness_subprocess()` is already correct: `(error_string, None, None)`. No changes needed there.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except FileNotFoundError` block in `_run_harness_subprocess()` is already tested by `test_binary_not_found` in `test_harness_streaming.py` — that test asserts the error string is returned. After this change, the test must be updated to assert the string is NOT sent to Telegram when `cli_retry_count < 3`.
- [ ] The finalization skip path (re-queued session must not be marked `completed`) must have a test asserting `complete_transcript()` is NOT called when `_harness_requeued=True`.

### Empty/Invalid Input Handling
- [ ] `do_work()` returning `""` must not trigger `BackgroundTask.send()` — verified by existing check `if send_result and self._result:` in `messenger.py:151` (empty string is falsy, already safe).
- [ ] `cli_retry_count` missing from `extra_context` defaults to 0 — tested via `int(ec.get("cli_retry_count", 0))`.

### Error State Rendering
- [ ] After 3 retries, exactly one persona-aligned message is sent — tested by asserting `messenger.send` called once with the persona string.
- [ ] Raw `"Error: CLI harness not found"` string must never appear in Telegram output after the fix.

## Test Impact

- [ ] `tests/unit/test_harness_streaming.py::TestHarnessStreaming::test_binary_not_found` — UPDATE: add an inline comment clarifying that this test validates the raw error string returned by `sdk_client.py` (i.e., the return value of `get_response_via_harness()`), NOT the post-interception behavior in `agent_session_queue.py`. This distinguishes it from the new `TestHarnessRetry` tests and prevents future readers from thinking the test covers retry behavior (N1). The assertion itself is still correct and does not need to change.
- [ ] `tests/unit/test_harness_retry.py` — CREATE new test file with `TestHarnessRetry` class covering: (a) retry counter increments on first failure and `do_work()` returns `""`, (b) retry counter increments on second failure, (c) persona message delivered on third failure, (d) `agent_session=None` case returns raw error string without retry.
- [ ] No integration tests touch this code path directly — the retry path requires an `agent_session` object in Redis, which is out of scope for existing unit tests.

## Rabbit Holes

- **Exponential backoff between retries**: The re-queued session goes back to the normal pending queue. Adding a `scheduled_at` delay is tempting but adds complexity for a transient PATH issue that usually resolves in seconds. Skip.
- **Classifying all harness errors as transient or deterministic**: Only `FileNotFoundError` is clearly transient. Other errors (parsing failures, API errors) should NOT be retried — they are deterministic and should produce persona-aligned messages on first occurrence. Don't build a classification table; just special-case `FileNotFoundError`.
- **Making `_run_harness_subprocess()` do the retry internally**: Tempting, but that function doesn't have access to the `AgentSession` model. Keeping the retry in the caller (queue module) maintains proper separation of concerns.
- **Resending the original message**: The re-queued session preserves all original fields including `initial_telegram_message`. No need to reconstruct it.

## Risks

### Risk 1: Infinite re-queue loop
**Impact:** If `cli_retry_count` is not reliably preserved across re-queues, the counter resets to 0 each time and the session loops forever.
**Mitigation:** `transition_status()` updates the existing record in-place — the same `extra_context` dict with the incremented `cli_retry_count` is written before the transition call. No delete-and-recreate means no field loss. Verify in test by asserting `cli_retry_count` on the re-queued session record after each retry.

### Risk 2: Finalization race on re-queued session
**Impact:** If `complete_transcript()` is called after `transition_status()` sets status to `"pending"`, the re-queued session is immediately finalized to `"completed"`, making it invisible to the worker.
**Mitigation:** Use the `_harness_requeued` flag to gate the `complete_transcript()` call. The flag is set synchronously inside `do_work()` before returning, so no race is possible within the same asyncio task.

### Risk 3: Worker not notified after re-queue
**Impact:** Re-queued session sits in Redis but no worker picks it up.
**Mitigation:** Call `_ensure_worker(agent_session.worker_key, ...)` after `transition_status()`, matching the pattern used in `_enqueue_nudge()` fallback path.

## Race Conditions

### Race 1: Re-queued session popped before finalization guard runs
**Location:** `agent_session_queue.py`, post-task finalization block
**Trigger:** Worker is fast enough to pop the re-queued `pending` session on a second worker loop before the current loop's `complete_transcript()` call
**Data prerequisite:** `_harness_requeued` flag must be set before finalization block executes
**State prerequisite:** `_harness_requeued=True` prevents `complete_transcript()` — making the call order safe
**Mitigation:** `do_work()` sets the flag synchronously; the `await task._task` ensures finalization doesn't run until `do_work()` completes; therefore `_harness_requeued` is always set before the finalization block. No race.

## No-Gos (Out of Scope)

- Retry for SDK (non-harness) execution failures — different execution path, separate concern
- Retry for sessions that hang mid-execution — covered by stall-retry in `monitoring/session_watchdog.py`
- Retry for orphaned running sessions with dead workers — covered by health monitor
- Configurable retry count — hardcode 3; make it a constant `_HARNESS_NOT_FOUND_MAX_RETRIES = 3`
- Metrics/alerting on retry events — deferred; `logger.warning()` is sufficient for now

## Update System

No update system changes required — this feature is purely internal to the worker/agent pipeline. No new config files, dependencies, or deployment steps.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. The fix is invisible to the agent itself; it only affects how the worker re-queues failed harness sessions and what message (if any) reaches Telegram.

## Documentation

- [x] Update `docs/features/stall-retry.md` to add a brief note distinguishing stall-retry (sessions that hang) from harness startup retry (sessions that fail instantly with FileNotFoundError). One paragraph is sufficient.
- [x] Add `## Harness Startup Retry` subsection to `docs/features/agent-session-health-monitor.md` or create `docs/features/harness-startup-retry.md` describing the new retry behavior.

## Success Criteria

- [ ] When `claude` binary is not found, session is silently re-queued up to 3 times before any Telegram message is sent
- [ ] Retry counter (`cli_retry_count` in `extra_context`) increments on each retry and is preserved across re-queues
- [ ] The final failure message after 3 retries is persona-aligned (no raw exception strings)
- [ ] Session is NOT finalized to `"completed"` after a silent re-queue — it remains `"pending"` for the worker to pop
- [ ] Non-`FileNotFoundError` harness result strings do NOT trigger retry — they are mapped to persona-aligned messages on first occurrence
- [ ] `tests/unit/test_harness_streaming.py` existing tests still pass
- [ ] New unit tests cover: retry counter increment, retry exhaustion message, finalization skip on re-queue, `agent_session=None` bypass
- [ ] `python -m ruff check . && python -m ruff format --check .` passes

## Team Orchestration

### Team Members

- **Builder (retry-logic)**
  - Name: retry-builder
  - Role: Implement harness startup retry in `agent_session_queue.py` and add unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (retry-logic)**
  - Name: retry-validator
  - Role: Verify retry behavior, counter persistence, and finalization guard
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update stall-retry and health-monitor docs to describe new retry path
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template.

## Step by Step Tasks

### 1. Add `_HARNESS_NOT_FOUND_MAX_RETRIES` constant and retry interception in `agent_session_queue.py`
- **Task ID**: build-retry-logic
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_retry.py` (create), `tests/unit/test_harness_streaming.py` (existing must pass)
- **Informed By**: Data Flow trace above, Technical Approach Change 1 and Change 2
- **Assigned To**: retry-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_HARNESS_NOT_FOUND_PREFIX = "Error: CLI harness not found"` and `_HARNESS_NOT_FOUND_MAX_RETRIES = 3` constants near top of `agent_session_queue.py`
- Import `transition_status` from `models.session_lifecycle` at the top of `agent_session_queue.py`
- Wrap `get_response_via_harness()` return value in the `do_work()` coroutine to detect the prefix
- Guard: if `agent_session is None`, return `raw` immediately (B1 — preserve existing behavior when Redis lookup failed)
- Read `cli_retry_count` from `agent_session.extra_context` (default 0)
- If count < `_HARNESS_NOT_FOUND_MAX_RETRIES`: update `extra_context["cli_retry_count"]`, call `await asyncio.to_thread(transition_status, agent_session, "pending", "harness-retry")` (B2 — reuse existing record, no new `async_create()`), call `_ensure_worker()`, log `logger.warning("[%s] Harness not found — retry %d/%d", ...)` (C1), set `_harness_requeued = True`, return `""`
- If count >= `_HARNESS_NOT_FOUND_MAX_RETRIES`: return persona-aligned message string
- Add `_harness_requeued` flag to finalization block guard (skip `complete_transcript()` when True)
- Create `tests/unit/test_harness_retry.py` with tests for: (a) first retry increments counter and returns `""`, (b) third retry returns persona message, (c) non-FileNotFoundError error does not retry, (d) agent_session=None bypasses retry and returns raw string

### 2. Validate retry logic
- **Task ID**: validate-retry-logic
- **Depends On**: build-retry-logic
- **Assigned To**: retry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_harness_retry.py tests/unit/test_harness_streaming.py -v`
- Verify `cli_retry_count` is preserved in the re-queued session's `extra_context`
- Verify `complete_transcript()` is NOT called when `_harness_requeued=True`
- Run `python -m ruff check agent/agent_session_queue.py && python -m ruff format --check agent/agent_session_queue.py`

### 3. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-retry-logic
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Add paragraph to `docs/features/stall-retry.md` distinguishing stall-retry from harness startup retry
- Create `docs/features/harness-startup-retry.md` describing the new retry behavior, trigger condition, counter storage, and persona message

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: retry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`
- Confirm all Success Criteria are met
- Confirm docs exist at `docs/features/harness-startup-retry.md`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Harness retry tests pass | `pytest tests/unit/test_harness_retry.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Harness retry doc exists | `test -f docs/features/harness-startup-retry.md` | exit code 0 |
| No raw error strings in new code | `grep -n "Error: CLI harness not found" agent/agent_session_queue.py` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | B1: `agent_session` may be `None` in retry block — `AttributeError` if Redis lookup failed | Technical Approach Change 1, Task 1 | Added `if agent_session is None: return raw` guard at top of retry detection block, before any `agent_session` access |
| BLOCKER | Adversary, Operator | B2: Original `status="running"` session left orphaned after re-queue — health monitor may spawn duplicate or zombie | Technical Approach Change 1 and Change 2, Architectural Impact | Replaced `AgentSession.async_create()` with `transition_status(agent_session, "pending", "harness-retry")` — existing record reused, no ghost running record |
| CONCERN | Operator | C1: No `logger.warning` in retry branch — retry events invisible in production | Technical Approach Change 1, Task 1 | Added `logger.warning("[%s] Harness not found — retry %d/%d", session_id, retry+1, max)` immediately before `return ""` |
| NIT | — | N1: `test_binary_not_found` note could mislead future readers | Test Impact section | Added clarifying inline comment distinguishing raw-string validation (sdk_client.py) from retry-interception tests (agent_session_queue.py) |

---

## Open Questions

None — the implementation approach is clear and all assumptions have been verified against the current codebase.
