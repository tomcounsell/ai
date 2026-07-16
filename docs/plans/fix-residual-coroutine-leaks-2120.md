---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
revised: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2120
last_comment_id:
revision_applied: true
---

# Fix residual un-awaited-coroutine leaks: `_worker_loop` and `_evaluate_promise_async`

## Problem

Second half of the #2118 teardown-hang fix (PR #2119 merged). The full-suite ~99%
teardown wedge is a **class** of un-awaited-coroutine leaks: a test hands an eagerly-created
real coroutine to a seam that drops it (never awaited, never closed). At GC / session
teardown CPython finalizes the coroutine and emits `coroutine '…' was never awaited`; on a
contended machine the mass-finalization wedges the run before junitxml is written. #2118
fixed 3 of 5 leak families (`run_email_bridge`, `download_media`, `_ingest_attachments`),
cutting the sources 5 → 2. The two RESIDUAL leaks:

### 1. `_evaluate_promise_async` / `_run_async_safely` (production seam)
`bridge/promise_gate.py::_run_async_safely(coro)` calls `asyncio.run(coro)`. When an event
loop is **already running** (pytest-asyncio, or any async caller), `asyncio.run` raises
`RuntimeError("asyncio.run() cannot be called from a running event loop")` **before touching
the coroutine** — so the eagerly-created `_evaluate_promise_async(text)` coroutine at
`promise_gate.py:654` is neither awaited nor closed. The `except RuntimeError` branch logs and
returns `None` (heuristic fallthrough) but leaves the coroutine open → finalized later at
GC/teardown → `coroutine '_evaluate_promise_async' was never awaited`.

**Confirmed by deterministic repro** (`_run_async_safely(_evaluate_promise_async(text))` from
inside a running loop): emits `coroutine '_evaluate_promise_async' was never awaited` at
finalization.

### 2. `_worker_loop` (test task-lifecycle)
`agent/agent_session_queue.py:1720 async def _worker_loop(...)` is spawned in integration tests
via `asyncio.create_task(_worker_loop(...))`. On a failure/timeout path where the created task
is **not cancel-awaited to completion**, the task's underlying coroutine is finalized at GC and
emits `coroutine '_worker_loop' was never awaited`. Audit of all `create_task(_worker_loop)`
sites:

| Test | Teardown today | Verdict |
|------|----------------|---------|
| `test_slow_redis_no_loop_freeze.py:154` | `finally` gathers ticker/unrelated tasks but **NOT** `worker_task`; only awaited on the happy path via `wait_for` at L209 | **LEAKS** on any early failure/timeout |
| `test_worker_wedge_pending.py:245` | `finally` cancels + `wait_for(shield(...), 1.0)` | OK (reference pattern) |
| `test_progress_deadline_cancel.py:122` | `_teardown_loop` cancels + `wait_for(shield(...), 1.0)` | OK (reference pattern) |
| `test_worker_drain.py` (140/170/199/249) | `await _worker_loop(...)` directly (no create_task) or awaits the helper task | OK |
| `test_agent_session_queue_async.py` (672/713/759/852) | `await` / `await wait_for(_worker_loop(...))` directly | OK (wait_for cancel-awaits on timeout) |
| `test_remote_update.py` (343/374) | `await _worker_loop(...)` directly | OK |

The single clear leaker is `test_slow_redis_no_loop_freeze.py`.

### Why these are hard to see
Neither reproduces deterministically per-file; they only surface in a full integration run
because the leaked coroutine is held alive inside an event-loop / task **reference cycle**
until session-level `gc.collect()`, at which point the whole batch finalizes at once and wedges
teardown. The immediate-refcount-drop case (which fires mid-test and is attributable) is not
the problem; the cycle-held case (silent until teardown) is.

## Solution

Three changes — fix each source, then add a durable class-level guardrail.

1. **`_run_async_safely` (production):** in the `except RuntimeError` "running event loop"
   branch, `coro.close()` before returning `None`. `close()` finalizes the coroutine
   immediately and deterministically, so no "never awaited" warning is ever emitted. Behavior
   is unchanged: in production `_run_async_safely` is only reached from a **sync** CLI context
   with no running loop, so `asyncio.run` succeeds and the branch is never taken; the branch
   fires only under a test harness / async caller, where returning `None` (heuristic
   fallthrough) is already the contract. We close the coroutine we were handed rather than
   dropping it.

2. **`_worker_loop` test (`test_slow_redis_no_loop_freeze.py`):** add `worker_task` to the
   `finally` teardown so it is **cancelled and awaited to completion** on every exit path,
   matching the established `_teardown_loop` pattern (cancel → `await wait_for(shield(task),
   timeout)` swallowing `TimeoutError`/`CancelledError`). No production change.

3. **Class-level guardrail (`tests/conftest.py`):** add a non-suppressing
   `pytest_runtest_teardown(item, nextitem)` hook that runs `gc.collect()` inside
   `warnings.catch_warnings(record=True)` and, for any captured `coroutine '…' was never
   awaited` RuntimeWarning, **re-emits it as a loud per-test `RuntimeWarning`** attributed to
   the finishing test. Under `-W error::RuntimeWarning` this converts a silent ~99%
   session-teardown wedge into an attributable per-test teardown failure. Validated: cycle-held
   leaked coroutines (exactly the wedge case) ARE captured by `catch_warnings` around
   `gc.collect()`. The hook must be xdist-safe (runs in every worker), cheap (one `gc.collect()`
   per test — acceptable; teardown already does cleanup), and must never itself raise other than
   the intended re-emitted warning.

## Success Criteria

- [ ] `_run_async_safely` closes the coroutine on the running-loop branch; deterministic repro
  (call from inside a running loop) emits **zero** `_evaluate_promise_async` "never awaited"
  warnings under `-W error::RuntimeWarning`.
- [ ] `test_slow_redis_no_loop_freeze.py` cancel-awaits `worker_task` in `finally`; forcing an
  early failure no longer leaks `_worker_loop`.
- [ ] `pytest_runtest_teardown` guardrail present in `tests/conftest.py`; a deliberate
  cycle-held leaked coroutine surfaces as a loud, test-attributed RuntimeWarning (fail-fast
  under `-W error`), proven by a dedicated meta-test.
- [ ] Affected areas run clean under `-W error::RuntimeWarning`: promise-gate unit + the two
  worker-loop integration files, zero `never awaited` for these two coroutines.
- [ ] Existing promise-gate and worker-loop tests still pass unchanged in behavior.

## No-Gos

- Do NOT suppress via `filterwarnings` / `pytest.ini` — that hides the whole class. The
  guardrail must SURFACE, not silence.
- Do NOT change `_run_async_safely`'s return-`None` contract or the heuristic fallthrough; only
  stop leaking the coroutine.
- Do NOT re-solve #2064 (machine-global lock), #2060 (per-process db isolation), or the three
  already-fixed #2118 leaks.
- Do NOT make the guardrail hook itself flaky or expensive enough to slow the suite materially.

## Update System

No update-system changes required — this is a test-correctness fix plus one narrow production
guard (`coro.close()`), both purely internal. No new dependencies, config files, or migration
steps; nothing propagates to other machines beyond the normal git pull.

## Agent Integration

No agent integration required — no new CLI entry point and no bridge import changes. The
`promise_gate.evaluate_promise` sync API and its CLI (`cli_check_or_exit`) behave identically;
the only change is that a coroutine dropped on the never-taken-in-production running-loop branch
is now closed instead of leaked.

## Documentation

- [ ] Update `tests/README.md` to note the `pytest_runtest_teardown` un-awaited-coroutine
  guardrail (what it catches, how to read a failure, `-W error::RuntimeWarning` fail-fast).
- [ ] Add a short note to `docs/features/promise-gate.md` (if the loop-guard behavior is
  documented there) that `_run_async_safely` closes the coroutine on the running-loop
  fallthrough. If no such doc section exists, state so — no doc change beyond `tests/README.md`.

## Failure Path Test Strategy

The failure path is "a coroutine leaks a `never awaited` RuntimeWarning." We prove it is closed
three ways: (a) a promise-gate meta-test that calls `_run_async_safely` from inside a running
loop under `-W error::RuntimeWarning` and asserts no warning; (b) forcing an early exception in
the slow-redis test path and asserting `worker_task` is cancel-awaited (no leak); (c) a
guardrail meta-test that deliberately creates a cycle-held un-awaited coroutine and asserts the
`pytest_runtest_teardown` hook re-emits an attributable RuntimeWarning.

## Test Impact

- [ ] `tests/integration/test_slow_redis_no_loop_freeze.py` — UPDATE: add `worker_task`
  cancel-await to the `finally` block. Assertions unchanged.
- [ ] `tests/unit/test_promise_gate.py` (or a new focused case) — ADD: a meta-test asserting
  `_run_async_safely` closes the coroutine on the running-loop branch (no `never awaited` under
  `-W error::RuntimeWarning`). No existing case deleted.
- [ ] `tests/conftest.py` — UPDATE: add `pytest_runtest_teardown` guardrail hook (new hook, no
  existing hook removed).
- [ ] New meta-test for the guardrail (e.g. `tests/unit/test_coroutine_leak_guardrail.py`) —
  ADD: prove the hook re-emits an attributable warning for a cycle-held leaked coroutine.
- No other existing tests are affected — the production `_run_async_safely` change is a no-op on
  the branch every non-test caller takes (no running loop → `asyncio.run` succeeds).

## Rabbit Holes

- Do NOT try to make `_run_async_safely` actually RUN the coroutine on a running loop (dedicated
  thread + `asyncio.run`): that would make real Haiku API calls under the test harness and
  change behavior. Closing the coroutine preserves the exact current heuristic-fallthrough
  contract.
- Do NOT rewrite the two OK worker-loop teardowns (`test_worker_wedge_pending`,
  `test_progress_deadline_cancel`) — they already cancel-await.
- Do NOT chase immediate-refcount-drop leaks — those fire mid-test and are already attributable;
  the guardrail targets the cycle-held teardown-wedge class.
- Full-suite green on this non-quiesced box is NOT a success gate — environmental
  parallel-contention failures are expected and separate. Prove the two specific leaks are gone
  via targeted repro + the guardrail firing, and document the environmental limitation honestly.
