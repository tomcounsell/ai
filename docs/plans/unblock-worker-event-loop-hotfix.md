---
status: Planning
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-19
tracking: https://github.com/tomcounsell/ai/issues/1055
last_comment_id:
---

# Unblock Worker Event Loop — Memory Extraction Hotfix (Layers 1+2)

## Problem

The worker process runs a single `asyncio.run()` event loop that drives every session's execution plus the reflection scheduler, session-notify listener, heartbeat tasks, and per-session watchdogs. Three synchronous `anthropic.Anthropic(...)` calls inside `agent/memory_extraction.py` run on that loop during post-session extraction. Because they are synchronous HTTP calls with no explicit timeout and no executor wrap, a single network hang freezes the entire worker — heartbeats stop, other projects' sessions stop making progress, the reflection scheduler pauses, and the session-notify listener stops waking.

**Current behavior** (observed on session `tg_valor_-1003449100931_681` on 2026-04-18):

- 05:58:31 UTC — result successfully delivered to Telegram
- 05:58:31 → 11:56:37 UTC — **zero worker log entries for ~6 hours**. No heartbeats, no reflection ticks, no session-notify events, no lifecycle transitions.
- The session stayed `status="running"` the entire time.
- The bridge-hosted watchdog reported `LIFECYCLE_STALL duration=21490s+` but took no action (log-only).
- Any follow-up user messages that tried to steer the session were orphaned against a session that looked alive but was frozen.

Root cause (traced in issue #1055):

1. Three sync `anthropic.Anthropic(...)` call sites in `agent/memory_extraction.py` (lines 100/105, 277/285, 412/413). Each constructs the sync HTTP client inside an `async def` with no `asyncio.to_thread` wrap and no explicit `timeout=` kwarg. Empirically the SDK default timeout does not fire in the half-open-TCP case — hence the 6-hour stall.
2. In `agent/session_executor.py::_execute_agent_session`, `complete_transcript(...)` is called **after** `await task._task` (line 1191). The awaited task is `BackgroundTask._run_work` (in `agent/messenger.py`), which awaits `run_post_session_extraction(...)` at line 218 **before** returning. So any hang in extraction blocks transcript finalization AND the downstream `_handle_dev_session_completion` at line 1293 that nudges the PM.

**Desired outcome:**

- Post-session memory extraction never blocks the worker event loop. All Anthropic calls from async code are async-native (`AsyncAnthropic`) wrapped in `asyncio.wait_for` with an explicit SDK-level `timeout=` kwarg (belt + suspenders — see Research).
- Memory extraction is **not** a prerequisite for session finalization. A hang or timeout of any duration in extraction does not delay `complete_transcript()` or dev→PM steering.
- Existing `try/except Exception` handlers in `memory_extraction.py` are preserved — failures remain non-fatal and silent (extraction is best-effort, not critical path).
- This PR is independently deployable. The remaining two layers (validator hook + watchdog auto-recovery) ship as a separate follow-up.

## Freshness Check

**Baseline commit:** `d6f25136d926f5ca4b841a4305cc0d7b8f876fa7`
**Issue filed at:** 2026-04-19T15:01:13Z (today, same-day plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/memory_extraction.py:100` and `:105` — sync `anthropic.Anthropic(...)` + `client.messages.create(...)` in `extract_observations_async` — still holds exactly.
- `agent/memory_extraction.py:277` and `:285` — sync client + `messages.create(...)` in `extract_post_merge_learning` — still holds exactly.
- `agent/memory_extraction.py:412` and `:413` — sync client + `messages.create(...)` in `detect_outcomes_async` — still holds exactly.
- `agent/session_executor.py:1191` — `await task._task` — confirmed at that line.
- `agent/session_executor.py:1224` — `complete_transcript(session.session_id, status=final_status)` on happy path — confirmed.
- `agent/session_executor.py:1244/1250` — `complete_transcript(...)` in the `else` fallback from #917 — confirmed at :1250.
- `agent/session_executor.py:1293` — `await _handle_dev_session_completion(...)` — confirmed.
- `agent/messenger.py:205` — `_run_work` signature — confirmed.
- `agent/messenger.py:218` — `await run_post_session_extraction(...)` inside `_run_work` — confirmed.
- `agent/intent_classifier.py:204` — reference pattern: sync client wrapped via `asyncio.to_thread(_call_api)` — confirmed.
- `bridge/media.py:349` — reference pattern: sync client wrapped via `asyncio.get_event_loop().run_in_executor(None, lambda: ...)` — confirmed.

**Cited sibling issues/PRs re-checked:**
- #917 — CLOSED 2026-04-13 (Bug: health-check-recovered sessions not finalized). Introduced the `else` fallback at `session_executor.py:1244` that also calls `complete_transcript()` — confirms both finalization paths share the same ordering dependency this plan fixes.
- #987 — CLOSED 2026-04-15 (SDLC pipeline halts after first stage: race between `_handle_dev_session_completion` and `_finalize_parent_sync`). Established the invariant that `_handle_dev_session_completion` must run AFTER `complete_transcript` — this plan preserves that ordering.
- #1019 — OPEN (Worker lifecycle audit: open investigations). Broader investigation; this issue is narrower and independently fixable.

**Commits on main since issue was filed (touching referenced files):** None. The issue was filed today and no commits have landed on main.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/claude-code-memory-integration.md` — references `agent/memory_extraction.py` to describe existing functionality (not modifying it). No overlap with this hotfix.
- `docs/plans/pm-autonomous-skills.md` — proposes additive behavior (writing outcome metrics inside `run_post_session_extraction`). No overlap with the async/finalization concerns.

**Notes:** Current code in the cited files matches the issue's claims exactly. No drift. Proceeding.

## Prior Art

- **Issue #1034** (closed 2026-04-19) — docs_auditor LLM calls fail with auth error and destabilize the worker. Related pattern: sync Anthropic SDK call inside the worker process destabilizing the event loop. The fix was auth-focused (not async-wrap-focused), so the underlying sync-in-async hazard still exists for `memory_extraction.py`. This hotfix addresses that hazard for the most critical path (post-session extraction).
- **Issue #867** (closed 2026-04-10) — Race: nudge re-enqueue stomped by worker finally-block `finalize_session()`. Related to the finalization ordering in `session_executor.py` this plan touches. The fix moved finalization ordering around nudge; this plan preserves that ordering while also decoupling extraction from it.
- **PR #593** (merged 2026-03-30) — Memory agent integration: metadata-aware recall. Original memory system work. Not a prior fix attempt.
- **PR #515** (merged 2026-03-24) — Subconscious Memory: persistent agent memory. Introduced `agent/memory_extraction.py` with sync Anthropic calls. This hotfix corrects the async-safety gap introduced here.

No prior attempts have tried to async-wrap memory extraction or decouple it from finalization. This is the first fix.

## Research

**Queries used:**
- `anthropic python SDK AsyncAnthropic client timeout parameter 2026`
- `asyncio.wait_for vs SDK timeout double timeout pattern python httpx best practice`

**Key findings:**

1. **`anthropic.AsyncAnthropic` has the same init signature as `anthropic.Anthropic`**; it uses `httpx.AsyncClient` under the hood and accepts the same `timeout=` kwarg (default 10 minutes, accepts float seconds or `httpx.Timeout`). Source: [anthropic-sdk-python README](https://github.com/anthropics/anthropic-sdk-python/blob/main/README.md), [deepwiki — Synchronous and Asynchronous Clients](https://deepwiki.com/anthropics/anthropic-sdk-python/4.2-synchronous-and-asynchronous-clients). This means the migration is a literal `Anthropic` → `AsyncAnthropic` swap + `await` on `messages.create(...)`.

2. **SDK timeout alone is not sufficient** in the face of half-open TCP sockets. The SDK relies on httpx's timeout, which depends on receiving socket events; if the OS-level socket is wedged (e.g., NAT timeout on a long-idle connection), the SDK may not observe the timeout. Layering `asyncio.wait_for(..., timeout=N)` around the SDK call provides a hard bound that fires from a separate asyncio timer, independent of socket state. Source: [httpx issue #1387 — httpx does not wrap asyncio.exceptions.TimeoutError](https://github.com/encode/httpx/issues/1387), [python-httpx timeout docs](https://www.python-httpx.org/advanced/timeouts/).

3. **Double-timeout tradeoff:** `asyncio.wait_for` creates a new task wrapper, which has a small overhead vs using SDK-native timeout alone. For a best-effort, non-critical-path extraction call, this overhead is negligible. The empirical 6-hour stall in #1055 confirms that SDK defaults alone are insufficient — belt + suspenders is warranted here.

**How these findings shape the plan:**
- Use `AsyncAnthropic` (not sync wrapped via `to_thread`) — it's the intended async path and avoids a thread-pool hop.
- Set BOTH an SDK-level `timeout=30.0` on `messages.create(...)` AND an outer `asyncio.wait_for(..., timeout=35.0)` (5s buffer so the SDK gets a chance to raise cleanly first).
- Catch `asyncio.TimeoutError` explicitly in addition to the existing `except Exception` — make the failure log message distinguishable from other failures for observability.

## Data Flow

End-to-end data flow for a session that triggers post-session memory extraction:

1. **Entry point**: Telegram message arrives at the bridge → bridge enqueues an `AgentSession` to Redis.
2. **Worker pops session** (`worker/__main__.py`): creates `BossMessenger` + `BackgroundTask`, enters `session_executor._execute_agent_session`.
3. **CLI harness runs** via `task.run(coro, send_result=True)` — `BackgroundTask._run_work` awaits `coro` (the agent's work), then sends the result message via the messenger.
4. **Post-session extraction block** (`agent/messenger.py:214-223`): inside `_run_work`, after the result is sent, `run_post_session_extraction(session_id, response_text)` is awaited.
5. **Extraction fans out** (`agent/memory_extraction.py:658-694`):
    - `extract_observations_async(...)` → **sync `anthropic.Anthropic()` + `client.messages.create(...)`** at lines 100/105 (the blocking call).
    - `detect_outcomes_async(...)` (if injected thoughts exist) → **sync `anthropic.Anthropic()` + `client.messages.create(...)`** at lines 412/413.
    - (The third call site at lines 277/285 lives in `extract_post_merge_learning`, which is NOT called from `run_post_session_extraction`; it is called from the post-merge learning hook in `agent/post_merge_learning.py`. Scope still includes it — see "Why all three" below.)
6. **Return to session_executor** (`agent/session_executor.py:1191`): `await task._task` returns only after `_run_work` completes (i.e., after extraction finishes or raises).
7. **Finalization** (`agent/session_executor.py:1214-1268`): `complete_transcript(session.session_id, status=...)` runs on the happy path (L1224) or the #917 fallback path (L1250). This writes `SESSION_END` and transitions the session to `completed`/`failed` via `finalize_session` → `_finalize_parent_sync`.
8. **Dev→PM nudge** (`agent/session_executor.py:1293`): `_handle_dev_session_completion(...)` enqueues a nudge to the parent PM session.

**The hotfix:** Make step 5's calls non-blocking. Then either (a) make step 6 independent of step 5 (fire-and-forget extraction) or (b) reorder so finalization runs before extraction even if extraction still awaits. This plan chooses (b) for Layer 2 — see Technical Approach for rationale.

**Why all three sync call sites are in scope** (even the one not on the finalization hot path): the issue's acceptance criterion is `grep -n "anthropic\.Anthropic(" agent/ worker/` returns zero matches in production modules reachable from the worker event loop. `extract_post_merge_learning` is called from `agent/post_merge_learning.py`, which runs under the reflection scheduler — still on the worker event loop — so the same hazard applies.

## Appetite

**Size:** Small

**Team:** Solo dev, 1 validator pass

**Interactions:**
- PM check-ins: 0 (scope is pre-scoped in the issue's Solution Sketch).
- Review rounds: 1 (code review via `/do-pr-review`).

This is a targeted, well-scoped hotfix. Three sync call sites to convert, one ordering change in `session_executor.py`, and two tests. Estimated build time: 60-90 minutes including tests.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` available (optional for tests — code is non-fatal if missing) | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key() or True"` | Extraction needs API key at runtime; tests don't |
| `anthropic` package includes `AsyncAnthropic` | `python -c "from anthropic import AsyncAnthropic"` | Verify the SDK version has async client (all supported versions do) |
| `asyncio.wait_for` available | N/A — stdlib since Python 3.4.4 | Timeout wrapper |

No new external dependencies. The `anthropic` package already ships `AsyncAnthropic`.

## Solution

### Key Elements

- **Layer 1 — Event loop unblock** (`agent/memory_extraction.py`): convert all three `anthropic.Anthropic(...)` + `client.messages.create(...)` call sites to `anthropic.AsyncAnthropic(...)` + `await client.messages.create(...)`. Wrap each await in `asyncio.wait_for(..., timeout=35.0)` and pass an SDK-level `timeout=30.0` to `messages.create`. Catch `asyncio.TimeoutError` with a distinct log message.
- **Layer 2 — Decouple extraction from finalization** (`agent/session_executor.py` + `agent/messenger.py`): move the post-session extraction call OUT of `BackgroundTask._run_work` and into `_execute_agent_session`, scheduled AFTER `complete_transcript(...)` runs on both the happy path and the #917 fallback path. Extraction runs as a best-effort fire-and-forget `asyncio.create_task` with a guard that cancels pending extractions cleanly on worker shutdown.
- **Tests**: two new tests prove the invariants. One asserts that a hung Haiku client (sleeps past timeout) does not block session finalization. One asserts that `asyncio.TimeoutError` in extraction does not propagate out of `_run_work` or the new extraction task as an unhandled exception.
- **Documentation**: a note in `docs/features/subconscious-memory.md` records the async/timeout requirement for all Anthropic calls made from the worker loop.

### Flow

**Before (broken):**

`_execute_agent_session` → `await task._task` (blocks on `_run_work`) → inside `_run_work`: await extraction (blocks 6h on hung TCP) → return → `complete_transcript()` (finally runs, 6h late) → `_handle_dev_session_completion()` (6h late).

**After (hotfix):**

`_execute_agent_session` → `await task._task` (`_run_work` no longer awaits extraction — returns as soon as result is sent) → `complete_transcript()` (runs immediately) → `_handle_dev_session_completion()` (nudges PM immediately) → fire-and-forget `asyncio.create_task(run_post_session_extraction(...))` scheduled to the worker loop (background; hard-timeout-bounded at 35s; failures logged, never propagated).

### Technical Approach

**Layer 1 pattern** (applied to all three call sites in `agent/memory_extraction.py`):

```python
# BEFORE (current, blocking):
import anthropic
client = anthropic.Anthropic(api_key=api_key)
message = client.messages.create(
    model=MODEL_FAST,
    max_tokens=500,
    messages=[...],
)

# AFTER (async-safe, double-timeout):
import anthropic
client = anthropic.AsyncAnthropic(api_key=api_key, timeout=30.0)
try:
    message = await asyncio.wait_for(
        client.messages.create(
            model=MODEL_FAST,
            max_tokens=500,
            messages=[...],
            timeout=30.0,  # SDK-level timeout
        ),
        timeout=35.0,  # outer hard-stop timeout
    )
except asyncio.TimeoutError:
    logger.warning(
        "[memory_extraction] Anthropic call exceeded 35s hard timeout (non-fatal); "
        "extraction skipped for session_id=%s",
        session_id,
    )
    return [] if returning_list else None
```

Rationale for the numeric choices:
- `30.0` SDK timeout: post-session extraction has no user-facing deadline; 30s is generous for Haiku-tier calls (median ~1-3s, 99p ~5-8s).
- `35.0` outer timeout: 5s buffer above SDK to let the SDK raise cleanly first for distinguishable error types; if the SDK hangs entirely (the observed 6h case), `asyncio.TimeoutError` still fires.
- Both timeouts are constants at module scope so they can be adjusted without re-reading call sites: `_EXTRACTION_SDK_TIMEOUT = 30.0`, `_EXTRACTION_HARD_TIMEOUT = 35.0`.

**Layer 2 relocation:**

In `agent/messenger.py::BackgroundTask._run_work`, remove lines 214-223 (the `run_post_session_extraction` block) entirely. Don't call it there at all.

In `agent/session_executor.py::_execute_agent_session`, add a new helper `_schedule_post_session_extraction(session, response_text)` that creates a fire-and-forget `asyncio.create_task(...)`. Schedule this task AFTER both finalization paths complete (both `if agent_session:` L1224 and `else:` fallback L1250), but BEFORE the `_handle_dev_session_completion` call at L1293 (so the nudge goes out synchronously with finalization, and extraction runs concurrently in the background).

The scheduled task is NOT awaited from `_execute_agent_session`. It is registered in a module-level `weakref.WeakSet` (or simple list that the worker shutdown drains) so that:
- Pending extractions are given a chance to complete on graceful worker shutdown (bounded: `asyncio.wait(pending_extractions, timeout=5.0)`).
- Abrupt shutdown simply cancels them — they are non-critical.
- The task itself wraps the extraction in `try/except Exception` so any error (including `asyncio.CancelledError`) is swallowed with a debug log.

**Why reorder to "extraction after finalization" rather than keep awaiting (with a timeout) in `_run_work`?**

Even with a 35-second hard timeout on extraction, blocking `_run_work` for up to 35 seconds blocks finalization for up to 35 seconds. That is 35 seconds during which the worker event loop heartbeat tasks could stall from perspectives of other watchdogs, and the user-visible `completed` state is delayed. Fire-and-forget makes the pipeline latency independent of extraction latency — the correct async pattern.

**Why not a separate thread for extraction?**

`AsyncAnthropic` is already async-native; spawning a thread just to hold the HTTP client adds no resilience over `asyncio.create_task` with `asyncio.wait_for`. Threads also escape the event loop's shutdown coordination, which makes the "cancel pending extractions on worker shutdown" requirement messier.

**What about the reference patterns in `agent/intent_classifier.py:204` and `bridge/media.py:349`?**

Those wrap sync `anthropic.Anthropic` via `asyncio.to_thread` / `run_in_executor`. That pattern is ALSO correct and keeps the API calls off the event loop. I'm not using that pattern here because:
- `AsyncAnthropic` is a cleaner fit — it avoids the thread-pool hop.
- The existing sync wrappers in those files don't have explicit timeouts; migrating them is NOT in scope for this hotfix (they live outside `agent/memory_extraction.py`). They will be covered by Layer 3's validator hook in the follow-up.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/memory_extraction.py` — three functions with outer `try/except Exception` that currently swallow SDK failures silently. Test: stub the AsyncAnthropic client to raise, assert `logger.warning(...)` with a distinguishable message is emitted.
- [ ] `agent/memory_extraction.py` — NEW `except asyncio.TimeoutError` branches. Test: stub the client to sleep past 35s (use `asyncio.sleep(40)` wrapped in a fake async context), assert `logger.warning` with the "35s hard timeout" message is emitted and the function returns the "nothing extracted" value.
- [ ] `agent/session_executor.py` — extraction scheduling block. Test: stub `run_post_session_extraction` to raise `RuntimeError`, assert the exception is caught inside the fire-and-forget task wrapper and does not propagate out; assert `logger.debug` was called (existing error tolerance pattern).

### Empty/Invalid Input Handling

- [ ] `extract_observations_async` already short-circuits on empty `response_text` (L86). This plan preserves that guard; no new test needed.
- [ ] `run_post_session_extraction` already wraps in try/except and cleans up session state in `finally` (L671-691). No new test needed.
- [ ] Verify: if `response_text` is None/empty, NO Anthropic call is made (guard fires first). Test exists in `tests/unit/test_memory_extraction.py::TestDetectOutcomes::test_empty_thoughts` and `test_empty_response`; extend to confirm no client instantiation occurs in that path.

### Error State Rendering

- [ ] The feature has no user-visible output — extraction is background-only. Failure is logged only. Log messages must be distinct per failure class (timeout vs. auth vs. other) so operators can diagnose from logs alone. Test covered in Exception Handling Coverage above.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` — ADD new test class `TestEventLoopSafety` with three cases:
    - `test_asyncio_timeout_is_caught_and_logged`: Patches `anthropic.AsyncAnthropic` to return a client whose `messages.create` awaits `asyncio.sleep(40)`; asserts `extract_observations_async` returns `[]` within ~35s and emits a WARNING log matching the "hard timeout" message. Also run an analogous assertion for `detect_outcomes_async` and `extract_post_merge_learning`.
    - `test_sdk_timeout_is_caught_and_logged`: Patches `anthropic.AsyncAnthropic` to raise `anthropic.APITimeoutError` (SDK's internal timeout type); asserts graceful handling.
    - `test_no_sync_anthropic_client_in_production_code`: A static assertion that `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` returns zero matches (run via `subprocess.run`), serving as a regression canary.
- [ ] `tests/unit/test_messenger.py::TestBackgroundTask` — UPDATE tests that currently exercise `_run_work` to remove any assumption that extraction runs synchronously inside `_run_work`. Specifically verify the new invariant: `_run_work` returns BEFORE extraction would complete (by checking extraction was NOT called by the time `_run_work` returned — relocated to `_execute_agent_session`).
- [ ] NEW `tests/integration/test_session_finalization_decoupled.py` — Integration test: spawn a minimal session via the session executor with a stubbed Haiku client that `asyncio.sleep(40)`s. Assert the session reaches `completed`/`failed` status in Redis within 5 seconds (bounded window) while the extraction task is still in-flight. This is the key acceptance test for Layer 2.
- [ ] NEW `tests/unit/test_session_executor_extraction_decoupling.py` — Unit test: call `_execute_agent_session` with a stubbed `run_post_session_extraction` that raises `TimeoutError`; assert `complete_transcript` was called, `_handle_dev_session_completion` was called, and the TimeoutError did NOT propagate out of `_execute_agent_session`.
- [ ] `tests/unit/test_memory_extraction.py` — existing tests for `extract_observations_async`, `detect_outcomes_async`, `extract_post_merge_learning` may need to UPDATE their mocks from patching `anthropic.Anthropic` to patching `anthropic.AsyncAnthropic`. Audit each test that mocks the SDK and update accordingly.

## Rabbit Holes

- **Migrating `bridge/routing.py:606/646`, `tools/classifier.py:84/322`, `agent/intent_classifier.py:204`, `bridge/media.py:349`**: All sync-in-async, some already wrapped, some not. OUT OF SCOPE for this hotfix. Will be covered by Layer 3's validator hook and cleanup PR. Touching them here balloons the review surface and risks destabilizing bridge routing (high-traffic code).
- **Rewriting `run_post_session_extraction` entirely**: tempting to refactor the function shape since we're in it. DON'T. Keep the surgical discipline — the three sync calls are the only things changing.
- **Adding a configurable timeout**: don't pull `_EXTRACTION_HARD_TIMEOUT` into a settings file yet. Module-level constant is fine; if operators need to tune it, that's a separate issue.
- **Watchdog auto-recovery** (Layer 4): NOT in this plan. The issue's Downstream section explicitly separates layers 3+4 into a follow-up.
- **Adding a semaphore or rate limiter for concurrent extractions**: fire-and-forget with no concurrency limit is fine — post-session extraction runs once per session completion, and a 35s hard timeout caps unbounded growth. Adding a semaphore now would be premature optimization.
- **Extracting a shared `AsyncAnthropic` singleton**: the sync version has a singleton in `bridge/routing.py`. Don't propagate that pattern here — the overhead of constructing `AsyncAnthropic` per-call is negligible, and a shared client across the worker loop introduces shutdown-ordering complexity we don't need.

## Risks

### Risk 1: Fire-and-forget extraction starvation on shutdown
**Impact:** On graceful worker shutdown, in-flight extraction tasks may be cancelled mid-call, losing the observation/outcome data. Less severely, ungraceful shutdown (SIGKILL) loses them for sure.
**Mitigation:** Extraction is already best-effort (non-fatal try/except). Shutdown-drain helper awaits pending extractions with `asyncio.wait(timeout=5.0)` — completes most in time, cancels the rest. Loss is acceptable: these are best-effort learnings, not critical-path data. Document this in `docs/features/subconscious-memory.md`.

### Risk 2: Race between extraction task and session deletion
**Impact:** If a session is deleted from Redis while its fire-and-forget extraction task is still running, the extraction may write to stale state (e.g., `clear_session(session_id)` in the `finally` block of `run_post_session_extraction`). This is benign today (`clear_session` is idempotent and operates on an in-memory dict) but worth flagging.
**Mitigation:** No code change required — the existing `clear_session` implementation is safe against this. Add a note in `docs/features/subconscious-memory.md` that extraction tasks are orphan-safe.

### Risk 3: `asyncio.TimeoutError` vs `anthropic.APITimeoutError` handling confusion
**Impact:** The Anthropic SDK raises its own `APITimeoutError` subclass of `anthropic.APIError`. The outer `asyncio.wait_for` raises `asyncio.TimeoutError`. If we only catch one, the other propagates and crashes the non-fatal guarantee.
**Mitigation:** Catch `asyncio.TimeoutError` explicitly with a distinct log message, then let the existing outer `except Exception` catch the SDK's `APITimeoutError` and everything else. The outer handler already preserves non-fatality. Explicit test case for both in `tests/unit/test_memory_extraction.py::TestEventLoopSafety`.

### Risk 4: Timeout values too aggressive for slow regions
**Impact:** 30s SDK + 35s outer may be too tight for agents running in high-latency regions, causing benign timeouts to log warnings.
**Mitigation:** These are post-session EXTRACTION calls on a FAST model (Haiku). Empirically Haiku responses on `messages.create(max_tokens=500)` complete in 1-5 seconds. 30s covers 6-10x typical latency. If operators observe widespread timeouts in practice, bump the constants — simple change, no structural impact.

### Risk 5: Breaking existing tests that patch `anthropic.Anthropic`
**Impact:** Test suite fails after the swap to `AsyncAnthropic` because mocks target the wrong symbol.
**Mitigation:** Audit and update mocks in `tests/unit/test_memory_extraction.py` as part of the build. Listed in Test Impact. The `/do-build` skill will run the test suite after changes and flag any broken mocks for patch.

## Race Conditions

### Race 1: Fire-and-forget extraction vs. worker shutdown
**Location:** `agent/session_executor.py` (new extraction scheduling block) and `worker/__main__.py` (shutdown sequence).
**Trigger:** Worker receives SIGTERM while an extraction task is mid-flight on the event loop.
**Data prerequisite:** The extraction task expects `session_id` to still exist in `agent/memory_hook._INJECTED_THOUGHTS` for `detect_outcomes_async`. The cleanup in `run_post_session_extraction`'s `finally` block calls `clear_session(session_id)`, which removes this.
**State prerequisite:** The AsyncAnthropic client's httpx connection pool must be drained or cancelled cleanly.
**Mitigation:** Worker's graceful shutdown path awaits pending extractions with a bounded `asyncio.wait(timeout=5.0)`. Tasks exceeding this are cancelled — the `except Exception` inside the wrapper catches `CancelledError`. `AsyncAnthropic` manages its httpx client's lifecycle via `__aexit__` semantics; we don't explicitly close it (per-call instantiation), so garbage collection handles cleanup.

### Race 2: Finalization running before result is fully persisted to Redis
**Location:** `agent/session_executor.py:1214-1268`.
**Trigger:** Fast finalization path under the new ordering could theoretically run before the `await self.messenger.send(...)` at `agent/messenger.py:212` has persisted the result message to Redis (via the TelegramRelayOutputHandler).
**Data prerequisite:** `messenger.send(...)` must complete BEFORE `complete_transcript` so that the SESSION_END transcript marker is written after the last message.
**State prerequisite:** The ordering in `_run_work` is: `await self._result = await coro` → `await messenger.send(result)` → return. No change. Finalization still runs AFTER `_run_work` returns.
**Mitigation:** This race already existed and is already handled by the pre-existing ordering (`messenger.send` is awaited inside `_run_work`; `_run_work` returns only after send completes). The plan doesn't change this sequence. Only the extraction block is moved OUT of `_run_work`.

### Race 3: Extraction task completing after session record is deleted
**Location:** `agent/memory_extraction.py:686-691` (`clear_session(session_id)` in finally block).
**Trigger:** A user or operator deletes the session's Popoto record while the orphaned extraction task is still running.
**Data prerequisite:** Memory records saved by `extract_observations_async` use `project_key` (not session_id) for persistence, so they survive session deletion fine.
**State prerequisite:** `clear_session(session_id)` only touches an in-memory dict keyed by session_id — safe to call with a stale ID (it just no-ops).
**Mitigation:** No code change required. Existing behavior is orphan-safe.

## No-Gos (Out of Scope)

- **Layer 3 — Validator hook** in `.claude/hooks/validators/` that rejects new `anthropic.Anthropic(` usage in async-reachable modules. Separate follow-up issue (to be filed after this PR merges).
- **Layer 4 — Watchdog auto-recovery** in `monitoring/session_watchdog.py::check_stalled_sessions()`. The watchdog currently logs `LIFECYCLE_STALL` but doesn't act; this plan does NOT change that. Separate follow-up issue.
- **Migrating other sync Anthropic call sites** in `bridge/routing.py`, `tools/classifier.py`, `agent/intent_classifier.py`, `bridge/media.py`. Already-wrapped sites (intent_classifier, bridge/media) are safe today; the others are separate concerns handled in the Layer 3 follow-up.
- **Refactoring the reflection scheduler** or the docs_auditor sync path from #1034. #1034 was auth-focused, not async-focused. Separate concern.
- **Adding observability metrics** for extraction latency or timeout rate. Worth having, but not a hotfix requirement. Can be added later without blocking this PR.
- **Configurable timeouts via settings**. Module-level constants are sufficient for now.

## Update System

**No update system changes required** — this is a pure code change in `agent/memory_extraction.py`, `agent/messenger.py`, and `agent/session_executor.py`. No new dependencies, no new config files, no migration steps. The next `/update` deploy pulls the new code, the valor-service restart hook cycles the worker, and the fix is live.

## Agent Integration

**No agent integration required** — this is a worker-internal change. The memory extraction subsystem does not expose tools via MCP; it runs in the background after session completion. No bridge changes. No `.mcp.json` changes. No new tool wrappers.

Integration test (already listed in Test Impact): `tests/integration/test_session_finalization_decoupled.py` verifies the end-to-end session path completes under simulated extraction stall — this is the implicit "agent works" test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a subsection under "Extraction Pipeline" titled "Event-Loop Safety" that documents:
    - All Anthropic calls from async code must use `AsyncAnthropic` with explicit `asyncio.wait_for` + SDK `timeout=` kwarg.
    - Post-session extraction runs as a fire-and-forget task AFTER finalization (not before), so a hang cannot block session completion.
    - Timeouts are `_EXTRACTION_SDK_TIMEOUT` / `_EXTRACTION_HARD_TIMEOUT` constants in `agent/memory_extraction.py`.
    - Loss of extraction data on abrupt worker shutdown is acceptable (best-effort).

### Inline Documentation
- [ ] Add docstring note to each of the three call sites in `memory_extraction.py` pointing at `#1055` and the `AsyncAnthropic`/`asyncio.wait_for` invariant.
- [ ] Add docstring note to the new `_schedule_post_session_extraction` helper in `session_executor.py` explaining the ordering invariant (runs AFTER `complete_transcript`, fire-and-forget, shutdown-drained).
- [ ] Update the `_run_work` docstring in `agent/messenger.py` to note that post-session extraction is NO LONGER called from `_run_work` (moved to `session_executor`); this prevents a future maintainer from re-introducing the block.

### README / Feature Index
- [ ] No new feature — no README index update needed. This is a bug fix, not a new capability.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestExtractBigrams` — UPDATE: no change needed (bigram logic untouched).
- [ ] `tests/unit/test_memory_extraction.py::TestDetectOutcomes::test_empty_thoughts` — UPDATE: no change needed (empty-path short-circuit untouched).
- [ ] `tests/unit/test_memory_extraction.py::TestDetectOutcomes::test_acted_on_overlap` — UPDATE if this test mocks `anthropic.Anthropic`: swap the mock to `anthropic.AsyncAnthropic` and make the mock's `messages.create` an async coroutine.
- [ ] `tests/unit/test_memory_extraction.py` — ADD `TestEventLoopSafety` with three cases: hard-timeout catch, SDK timeout catch, grep-canary for sync client.
- [ ] `tests/unit/test_messenger.py::TestBackgroundTask` — UPDATE tests that verify extraction happens inside `_run_work`. Relocate assertions to check that `_run_work` now COMPLETES before extraction is scheduled.
- [ ] `tests/unit/test_messenger_callbacks.py` — no change expected (callbacks are independent of extraction).
- [ ] ADD `tests/integration/test_session_finalization_decoupled.py` — new file with a single integration test: session with hung Haiku stub reaches terminal status within 5s.
- [ ] ADD `tests/unit/test_session_executor_extraction_decoupling.py` — new file with unit test asserting: extraction task is scheduled AFTER `complete_transcript` and `_handle_dev_session_completion`; errors inside the scheduled task don't propagate.

## Success Criteria

- [ ] All three call sites in `agent/memory_extraction.py` (lines 100/105, 277/285, 412/413) use `anthropic.AsyncAnthropic`, wrap the call in `asyncio.wait_for(..., timeout=35.0)`, and pass SDK-level `timeout=30.0` to `messages.create`.
- [ ] `grep -n "anthropic\.Anthropic(" agent/ worker/` returns zero matches in production code (excluding tests, comments, and docstrings).
- [ ] `_run_work` in `agent/messenger.py` NO LONGER calls `run_post_session_extraction`. The lines 214-223 block is removed.
- [ ] `_execute_agent_session` in `agent/session_executor.py` schedules `run_post_session_extraction` as a fire-and-forget `asyncio.create_task` AFTER both the happy-path `complete_transcript` call (L1224) and the `else`-fallback `complete_transcript` call (L1250), and before the `_handle_dev_session_completion` call (L1293).
- [ ] Integration test passes: a session with a Haiku stub that `asyncio.sleep(40)`s reaches `completed`/`failed` status within 5 seconds, NOT 40+ seconds.
- [ ] Unit test passes: `asyncio.TimeoutError` in extraction does not propagate out of `_run_work` OR the fire-and-forget wrapper; session still finalizes cleanly.
- [ ] Docs note in `docs/features/subconscious-memory.md` under "Event-Loop Safety" describes the async/timeout requirement and the extraction-after-finalization ordering.
- [ ] Tests pass (`pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -x -q`).
- [ ] Format clean (`python -m ruff format .`).
- [ ] No regression: full unit suite passes (`pytest tests/unit/ -x -q`).

## Team Orchestration

### Team Members

- **Builder (memory-extraction-async)**
  - Name: `async-builder`
  - Role: Convert the three sync `anthropic.Anthropic(...)` call sites in `agent/memory_extraction.py` to `AsyncAnthropic` with double-timeout wrapping. Add module-level timeout constants. Preserve existing try/except semantics.
  - Agent Type: async-specialist
  - Resume: true

- **Builder (session-executor-decoupling)**
  - Name: `decouple-builder`
  - Role: Remove the `run_post_session_extraction` call from `agent/messenger.py::_run_work`. Add `_schedule_post_session_extraction` helper to `agent/session_executor.py`. Wire it into `_execute_agent_session` AFTER both `complete_transcript` calls and BEFORE `_handle_dev_session_completion`. Register pending tasks for graceful shutdown drain.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (tests)**
  - Name: `test-engineer`
  - Role: Write the three new `TestEventLoopSafety` cases in `tests/unit/test_memory_extraction.py`, the new `tests/integration/test_session_finalization_decoupled.py`, and the new `tests/unit/test_session_executor_extraction_decoupling.py`. Update any existing mocks that target `anthropic.Anthropic`.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `doc-writer`
  - Role: Add the "Event-Loop Safety" subsection to `docs/features/subconscious-memory.md`. Update docstrings in the three modified files.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `hotfix-validator`
  - Role: Verify the grep canary returns zero matches, run the full unit suite, confirm the integration test passes with bounded timing, confirm docs are updated, confirm no sync `anthropic.Anthropic` regressions.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using `async-specialist` for Layer 1 (domain fit: concurrency + event-loop safety), `builder` for Layer 2 (general implementation), `test-engineer` for test expansion, `documentarian` for docs, `validator` for final pass.

## Step by Step Tasks

### 1. Convert sync call sites to AsyncAnthropic (Layer 1)
- **Task ID**: build-async-extraction
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_extraction.py::TestEventLoopSafety`, `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` returns zero matches
- **Informed By**: Research findings (AsyncAnthropic signature, double-timeout rationale)
- **Assigned To**: `async-builder`
- **Agent Type**: async-specialist
- **Parallel**: true
- Add module-level constants `_EXTRACTION_SDK_TIMEOUT = 30.0` and `_EXTRACTION_HARD_TIMEOUT = 35.0` at the top of `agent/memory_extraction.py`.
- Convert `extract_observations_async` (lines ~90-120): swap `anthropic.Anthropic` → `anthropic.AsyncAnthropic(api_key=..., timeout=_EXTRACTION_SDK_TIMEOUT)`, wrap `client.messages.create(...)` in `asyncio.wait_for(..., timeout=_EXTRACTION_HARD_TIMEOUT)`, pass `timeout=_EXTRACTION_SDK_TIMEOUT` to `messages.create`, add distinct `except asyncio.TimeoutError` branch above the existing outer catch-all.
- Convert `extract_post_merge_learning` (lines ~265-290): same pattern.
- Convert `detect_outcomes_async` (lines ~405-420): same pattern.
- Add `import asyncio` if not already imported.
- Preserve all existing `try/except Exception` semantics — failures remain non-fatal.
- Add inline `# hotfix #1055` comment at each converted call site for traceability.

### 2. Decouple extraction from finalization (Layer 2)
- **Task ID**: build-decouple-extraction
- **Depends On**: build-async-extraction (timeout constants must exist)
- **Validates**: `tests/unit/test_session_executor_extraction_decoupling.py`, `tests/integration/test_session_finalization_decoupled.py`
- **Assigned To**: `decouple-builder`
- **Agent Type**: builder
- **Parallel**: false (depends on Task 1)
- Remove lines 214-223 from `agent/messenger.py::_run_work` (the `run_post_session_extraction` block). Update the function's docstring to note the relocation.
- In `agent/session_executor.py`, add a module-level `_pending_extraction_tasks: set[asyncio.Task] = set()` (using a regular set with explicit add/discard on task callbacks; weakref is not safe because asyncio.Task has no `__weakref__` slot in some stdlib builds).
- Add helper `_schedule_post_session_extraction(session_id: str, response_text: str) -> None` that creates and registers the fire-and-forget task, swallows exceptions via a wrapper, and discards the task from the set on done.
- In `_execute_agent_session`, call `_schedule_post_session_extraction(...)` AFTER the `if agent_session: ... else: ...` finalization block completes (i.e., after BOTH L1224 and L1250 paths) and BEFORE the `_handle_dev_session_completion` call at L1293.
- Add a module-level `drain_pending_extractions(timeout: float = 5.0) -> None` async helper that worker shutdown calls: `await asyncio.wait(_pending_extraction_tasks, timeout=timeout)` then cancel the rest.
- Wire `drain_pending_extractions` into `worker/__main__.py` shutdown sequence (before `loop.close()`).
- Add inline `# hotfix #1055` comments for traceability.

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-async-extraction, build-decouple-extraction
- **Validates**: self (tests must run green)
- **Assigned To**: `test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestEventLoopSafety` to `tests/unit/test_memory_extraction.py` with:
    - `test_hard_timeout_caught_and_logged_extract_observations`
    - `test_hard_timeout_caught_and_logged_detect_outcomes`
    - `test_hard_timeout_caught_and_logged_post_merge_learning`
    - `test_sdk_api_timeout_caught_and_logged`
    - `test_no_sync_anthropic_client_grep_canary` (uses subprocess.run to grep the file)
- Create `tests/integration/test_session_finalization_decoupled.py`:
    - Set up a minimal `_execute_agent_session` scenario with a stubbed Haiku client whose `messages.create` is `async def _slow(...): await asyncio.sleep(40)`.
    - Assert the session reaches `completed` or `failed` status in Redis within 5 seconds.
    - Assert `_handle_dev_session_completion` was called within 5 seconds.
    - Teardown: cancel the pending extraction task.
- Create `tests/unit/test_session_executor_extraction_decoupling.py`:
    - Patch `run_post_session_extraction` to raise `asyncio.TimeoutError` synchronously when called.
    - Run `_execute_agent_session` with a minimal session.
    - Assert `complete_transcript` was called, `_handle_dev_session_completion` was called, no exception propagated.
- Audit `tests/unit/test_memory_extraction.py` for any test that patches `anthropic.Anthropic` and UPDATE the patch target to `anthropic.AsyncAnthropic` with an async mock.
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -x -v` and confirm all pass.

### 4. Update documentation
- **Task ID**: document-hotfix
- **Depends On**: build-async-extraction, build-decouple-extraction
- **Assigned To**: `doc-writer`
- **Agent Type**: documentarian
- **Parallel**: true with Task 3
- Add an "Event-Loop Safety" subsection to `docs/features/subconscious-memory.md` under the "Extraction Pipeline" section describing: async/timeout requirement, fire-and-forget ordering (runs after `complete_transcript`), loss-of-data tolerance on abrupt shutdown, timeout constants.
- Update the `_run_work` docstring in `agent/messenger.py` noting extraction is now handled by the caller.
- Add a docstring to `_schedule_post_session_extraction` in `agent/session_executor.py` explaining the ordering invariant and shutdown-drain semantics.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-hotfix
- **Assigned To**: `hotfix-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` — must return zero matches.
- Run `grep -n "anthropic\.Anthropic(" agent/ worker/ --include="*.py"` — confirm only the already-wrapped `agent/intent_classifier.py:204` pattern remains in production code (this is OK — wrapped via `to_thread`).
- Run `pytest tests/unit/ -x -q` — full unit suite passes.
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -v` — all new and updated tests pass.
- Run `python -m ruff format --check .` — format clean.
- Confirm `docs/features/subconscious-memory.md` has the new "Event-Loop Safety" subsection.
- Confirm inline docstrings in the three modified files reference `#1055`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No sync anthropic in memory_extraction | `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` | exit code 1 (no matches) |
| Memory extraction tests pass | `pytest tests/unit/test_memory_extraction.py -x -q` | exit code 0 |
| Messenger tests pass | `pytest tests/unit/test_messenger.py -x -q` | exit code 0 |
| Decoupling unit test passes | `pytest tests/unit/test_session_executor_extraction_decoupling.py -x -q` | exit code 0 |
| Decoupling integration test passes | `pytest tests/integration/test_session_finalization_decoupled.py -x -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Docs updated | `grep -l "Event-Loop Safety" docs/features/subconscious-memory.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The issue's Solution Sketch and Acceptance Criteria are explicit and unambiguous. The user's directive (layers 1+2 only, defer 3+4) is clear. Technical choices (AsyncAnthropic vs. to_thread, fire-and-forget vs. await-with-timeout, timeout values) are all justified above with research citations. Ready for critique and build.
