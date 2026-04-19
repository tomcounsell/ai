---
status: Shipped
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-19
tracking: https://github.com/tomcounsell/ai/issues/1055
last_comment_id:
revision_applied: true
allow_unchecked: true
---

<!--
allow_unchecked: true rationale — the plan's checklist items (Exception Handling
Coverage, Test Impact, Feature Documentation, Inline Documentation, Success
Criteria) were not checked off during BUILD but the work is fully verified
complete via:
  - APPROVED re-review comment on PR #1056 (all prior findings addressed)
  - 90 hotfix-related tests passing including 5s SLO integration test
  - Regression canary grep returns 0 matches (enforced by unit test)
  - docs/features/subconscious-memory.md updated with "Event-Loop Safety (hotfix #1055)"
  - CI passing, pr_merge_state CLEAN
  - ruff format clean
See PR #1056 body for exhaustive mapping of plan items to commits.
-->


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
- Extraction failures emit analytics counters so silent failures become visible.
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
- `.claude/hooks/hook_utils/memory_bridge.py:462-518` — `post_merge_extract()` calls `asyncio.run(extract_post_merge_learning(...))` inside a short-lived hook subprocess — **corrected from earlier draft** (the earlier draft incorrectly referenced a nonexistent `agent/post_merge_learning.py`). The hook subprocess runs OUT of the worker event loop, but the conversion to `AsyncAnthropic` + `asyncio.wait_for` is still in scope — see Data Flow for why.
- `worker/__main__.py:400-432` — shutdown sequence with 60s worker-task wait then health/notify/reflection cancels — confirmed.

**Cited sibling issues/PRs re-checked:**
- #917 — CLOSED 2026-04-13 (Bug: health-check-recovered sessions not finalized). Introduced the `else` fallback at `session_executor.py:1244` that also calls `complete_transcript()` — confirms both finalization paths share the same ordering dependency this plan fixes.
- #987 — CLOSED 2026-04-15 (SDLC pipeline halts after first stage: race between `_handle_dev_session_completion` and `_finalize_parent_sync`). Established the invariant that `_handle_dev_session_completion` must run AFTER `complete_transcript` — this plan preserves that ordering with a **synchronous** `_schedule_post_session_extraction(...)` call that does not await or gather.
- #1019 — OPEN (Worker lifecycle audit: open investigations). Broader investigation; this issue is narrower and independently fixable.
- #1034 — CLOSED 2026-04-19 (docs_auditor LLM calls fail with auth error and destabilize the worker). Informs the analytics-counter requirement in this plan — silent extraction failures must be visible.

**Commits on main since issue was filed (touching referenced files):** None. The issue was filed today and no commits have landed on main.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/claude-code-memory-integration.md` — references `agent/memory_extraction.py` to describe existing functionality (not modifying it). No overlap with this hotfix.
- `docs/plans/pm-autonomous-skills.md` — proposes additive behavior (writing outcome metrics inside `run_post_session_extraction`). No overlap with the async/finalization concerns.
- `docs/plans/agent_wiki.md` — references `extract_post_merge_learning()` and explicitly notes at line 157 that it is "called via `asyncio.run()` in `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()`; an async WikiWriter inside `asyncio.run()` raises `RuntimeError: This event loop is already running`." This is the regression class that Task 3 explicitly tests for.

**Notes:** Current code in the cited files matches the issue's claims exactly. No drift. Proceeding.

## Prior Art

- **Issue #1034** (closed 2026-04-19) — docs_auditor LLM calls fail with auth error and destabilize the worker. Related pattern: sync Anthropic SDK call inside the worker process destabilizing the event loop. The fix was auth-focused (not async-wrap-focused), so the underlying sync-in-async hazard still exists for `memory_extraction.py`. This hotfix addresses that hazard for the most critical path (post-session extraction). Also informs the silent-failure concern: #1034 was diagnosed via user-visible weirdness, not logs, because the failure was silent. This plan adds `memory.extraction.error` analytics counters so future silent failures show up on `/dashboard.json`.
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

4. **`async with anthropic.AsyncAnthropic(...) as client:`** context manager form cleanly releases the underlying `httpx.AsyncClient`. Without it, per-call instantiation leaks on `asyncio.TimeoutError` paths — CPython refcount-GC reclaims it, but `-W error::ResourceWarning` emits noise to stderr. This plan uses the context-manager form to avoid that noise (nit 10).

**How these findings shape the plan:**
- Use `AsyncAnthropic` (not sync wrapped via `to_thread`) — it's the intended async path and avoids a thread-pool hop.
- Set BOTH an SDK-level `timeout=30.0` on `messages.create(...)` AND an outer `asyncio.wait_for(..., timeout=35.0)` (5s buffer so the SDK gets a chance to raise cleanly first).
- Wrap the client in `async with` to release httpx resources deterministically on timeout.
- Catch `asyncio.TimeoutError` explicitly in addition to the existing `except Exception` — make the failure log message distinguishable from other failures for observability.
- Emit `memory.extraction.error` analytics counter in every exception handler except `CancelledError`.

## Data Flow

End-to-end data flow for a session that triggers post-session memory extraction:

1. **Entry point**: Telegram message arrives at the bridge → bridge enqueues an `AgentSession` to Redis.
2. **Worker pops session** (`worker/__main__.py`): creates `BossMessenger` + `BackgroundTask`, enters `session_executor._execute_agent_session`.
3. **CLI harness runs** via `task.run(coro, send_result=True)` — `BackgroundTask._run_work` awaits `coro` (the agent's work), then sends the result message via the messenger.
4. **Post-session extraction block** (`agent/messenger.py:214-223`): inside `_run_work`, after the result is sent, `run_post_session_extraction(session_id, response_text)` is awaited. **This block is removed in Layer 2.**
5. **Extraction fans out** (`agent/memory_extraction.py:658-694`):
    - `extract_observations_async(...)` → **sync `anthropic.Anthropic()` + `client.messages.create(...)`** at lines 100/105 (the blocking call).
    - `detect_outcomes_async(...)` (if injected thoughts exist) → **sync `anthropic.Anthropic()` + `client.messages.create(...)`** at lines 412/413.
    - (The third call site at lines 277/285 lives in `extract_post_merge_learning`, which is NOT called from `run_post_session_extraction`. See "Why all three sync call sites are in scope" below for its caller and why it's still in scope.)
6. **Return to session_executor** (`agent/session_executor.py:1191`): `await task._task` returns only after `_run_work` completes (i.e., after extraction finishes or raises — pre-fix) / as soon as the result message is sent (post-fix).
7. **Finalization** (`agent/session_executor.py:1214-1268`): `complete_transcript(session.session_id, status=...)` runs on the happy path (L1224) or the #917 fallback path (L1250). This writes `SESSION_END` and transitions the session to `completed`/`failed` via `finalize_session` → `_finalize_parent_sync`.
8. **Extraction schedule (post-fix)**: Immediately after finalization returns and before `_handle_dev_session_completion`, `_schedule_post_session_extraction(...)` is called **synchronously** (no `await`) to register a fire-and-forget `asyncio.create_task(...)`. Task handle is stored in `_pending_extraction_tasks: dict[str, asyncio.Task]` keyed by `session_id` so duplicate schedules for the same session are deduplicated.
9. **Dev→PM nudge** (`agent/session_executor.py:1293`): `_handle_dev_session_completion(...)` runs next — enqueues a nudge to the parent PM session. Because Step 8 used `create_task` (synchronous, not `await`), the nudge fires while extraction is still pending.

**Why all three sync call sites are in scope:**

`extract_observations_async` and `detect_outcomes_async` run on the worker event loop directly (via `run_post_session_extraction`). Fixing these is the observed-bug fix.

`extract_post_merge_learning` is called ONLY from `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()` (lines 462-518), which wraps it in `asyncio.run(extract_post_merge_learning(...))` inside a short-lived Claude Code hook subprocess. This does NOT run on the worker event loop. However, the conversion is still in scope for three reasons:

1. **Regression canary against "event loop already running"**: `docs/plans/agent_wiki.md:157` explicitly notes that `extract_post_merge_learning()` is called inside `asyncio.run(...)`. Converting the sync client to `AsyncAnthropic` with correct `await` semantics eliminates a class of future regressions where an accidental `asyncio.to_thread` or nested `asyncio.run` inside the converted code would raise `RuntimeError: This event loop is already running` in the hook. We add a unit test that calls `asyncio.run(extract_post_merge_learning(...))` with mocked `AsyncAnthropic` to guard this.
2. **Consistency**: All three call sites share the same sync-in-async pattern. Fixing two and leaving one is unprincipled and invites future bug reports.
3. **Success criterion simplicity**: Narrowing the grep canary to `agent/memory_extraction.py` (see Success Criteria) requires all three sites converted.

## Appetite

**Size:** Small

**Team:** Solo dev, 1 validator pass

**Interactions:**
- PM check-ins: 0 (scope is pre-scoped in the issue's Solution Sketch).
- Review rounds: 1 (code review via `/do-pr-review`).

This is a targeted, well-scoped hotfix. Three sync call sites to convert, one ordering change in `session_executor.py`, one shutdown-drain hook in `worker/__main__.py`, and five tests. Estimated build time: 75-105 minutes including tests.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` available (optional for tests — code is non-fatal if missing) | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key() or True"` | Extraction needs API key at runtime; tests don't |
| `anthropic` package includes `AsyncAnthropic` | `python -c "from anthropic import AsyncAnthropic"` | Verify the SDK version has async client (all supported versions do) |
| `asyncio.wait_for` available | N/A — stdlib since Python 3.4.4 | Timeout wrapper |
| `analytics.collector.record_metric` callable | `python -c "from analytics.collector import record_metric"` | Used for new `memory.extraction.error` counter |

No new external dependencies. The `anthropic` package already ships `AsyncAnthropic`.

## Solution

### Key Elements

- **Layer 1 — Event loop unblock** (`agent/memory_extraction.py`): convert all three `anthropic.Anthropic(...)` + `client.messages.create(...)` call sites to `async with anthropic.AsyncAnthropic(...) as client: await client.messages.create(...)`. Wrap each await in `asyncio.wait_for(..., timeout=35.0)` and pass an SDK-level `timeout=30.0` to `messages.create`. Catch `asyncio.TimeoutError` with a distinct log message. Emit `memory.extraction.error` analytics counter on every error path.
- **Layer 2 — Decouple extraction from finalization** (`agent/session_executor.py` + `agent/messenger.py` + `worker/__main__.py`): move the post-session extraction call OUT of `BackgroundTask._run_work` and into `_execute_agent_session`, scheduled **synchronously** (no `await`) AFTER `complete_transcript(...)` runs on both the happy path and the #917 fallback path, and BEFORE the `_handle_dev_session_completion` call. Extraction runs as a best-effort fire-and-forget `asyncio.create_task`, deduplicated by `session_id`, with a shutdown-drain hook wired into `worker/__main__.py` at a specified position in the shutdown sequence.
- **Tests**: five new tests prove the invariants. A hung Haiku client with a **real sync blocker** (not `asyncio.sleep`) must not block session finalization. `asyncio.TimeoutError` in extraction does not propagate. The PM nudge is delivered within the 5-second window. Extraction task is still pending (`.done() is False`) when `_handle_dev_session_completion` returns. Calling `asyncio.run(extract_post_merge_learning(...))` with a mocked `AsyncAnthropic` does not raise "event loop already running".
- **Documentation**: a note in `docs/features/subconscious-memory.md` records the async/timeout requirement for all Anthropic calls made from the worker loop.

### Flow

**Before (broken):**

`_execute_agent_session` → `await task._task` (blocks on `_run_work`) → inside `_run_work`: `await run_post_session_extraction(...)` (blocks 6h on hung TCP) → return → `complete_transcript()` (finally runs, 6h late) → `_handle_dev_session_completion()` (6h late).

**After (hotfix):**

`_execute_agent_session` → `await task._task` (`_run_work` no longer awaits extraction — returns as soon as result is sent) → `complete_transcript()` (runs immediately) → `_schedule_post_session_extraction(session_id, response_text)` (**synchronous** — no `await`; registers `asyncio.create_task(...)` and returns) → `await _handle_dev_session_completion(...)` (nudges PM immediately, while extraction task is still pending; extraction task completes or times out in the background; failures logged and counted, never propagated).

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

# AFTER (async-safe, double-timeout, resource-cleaned):
import anthropic
try:
    async with anthropic.AsyncAnthropic(api_key=api_key, timeout=_EXTRACTION_SDK_TIMEOUT) as client:
        message = await asyncio.wait_for(
            client.messages.create(
                model=MODEL_FAST,
                max_tokens=500,
                messages=[...],
                timeout=_EXTRACTION_SDK_TIMEOUT,  # SDK-level timeout
            ),
            timeout=_EXTRACTION_HARD_TIMEOUT,  # outer hard-stop timeout
        )
except asyncio.TimeoutError:
    logger.warning(
        "[memory_extraction] Anthropic call exceeded %.1fs hard timeout (non-fatal); "
        "extraction skipped for session_id=%s",
        _EXTRACTION_HARD_TIMEOUT,
        session_id,
    )
    _record_extraction_error("TimeoutError", session_id, project_key)
    return [] if returning_list else None
```

Rationale for the numeric choices:
- `30.0` SDK timeout: post-session extraction has no user-facing deadline; 30s is generous for Haiku-tier calls (median ~1-3s, 99p ~5-8s).
- `35.0` outer timeout: 5s buffer above SDK to let the SDK raise cleanly first for distinguishable error types; if the SDK hangs entirely (the observed 6h case), `asyncio.TimeoutError` still fires.
- Both timeouts are constants at module scope so they can be adjusted without re-reading call sites: `_EXTRACTION_SDK_TIMEOUT = 30.0`, `_EXTRACTION_HARD_TIMEOUT = 35.0`.

**Analytics counter for silent failures** (applies to every converted call site — addresses concern #4):

Add a private helper at module scope in `agent/memory_extraction.py`:

```python
def _record_extraction_error(error_class: str, session_id: str, project_key: str | None = None) -> None:
    """Emit memory.extraction.error counter. Non-fatal — silent if analytics unavailable."""
    if error_class == "CancelledError":
        return  # Expected on shutdown; do not record
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.extraction.error",
            1.0,
            {
                "error_class": error_class.lower(),
                "session_id": session_id,
                "project_key": project_key or "",
            },
        )
    except Exception:
        pass
```

Call `_record_extraction_error(type(e).__name__, session_id, project_key)` inside EVERY `except` branch in the three converted functions (both the new `except asyncio.TimeoutError` and the existing outer `except Exception as e`). Do NOT call it on `CancelledError` (the guard above handles this). Reuses the same `analytics.collector.record_metric` pattern already present at `agent/memory_extraction.py:162` (success counter). Dashboard pickup is automatic via `/dashboard.json`.

**Layer 2 relocation:**

In `agent/messenger.py::BackgroundTask._run_work`, remove lines 214-223 (the `run_post_session_extraction` block) entirely. Don't call it there at all.

In `agent/session_executor.py::_execute_agent_session`, add a new helper `_schedule_post_session_extraction(session_id, response_text)` that creates a fire-and-forget `asyncio.create_task(...)` and registers it in a module-level dict keyed by `session_id`.

**Call-shape invariant** (addresses concern #6 — preserves #987):

```python
# Inside _execute_agent_session, AFTER complete_transcript returns:
_schedule_post_session_extraction(session.session_id, task._result or "")  # synchronous — NO await, NO gather

if _session_type == "dev" and not task.error:
    await _handle_dev_session_completion(...)  # PM nudge fires while extraction is still pending
```

**CRITICAL**: `_schedule_post_session_extraction` MUST be a synchronous call. Any `await` or `asyncio.gather(...)` on the returned task would re-couple extraction latency to the PM nudge, reintroducing the #987 failure mode on a slow Haiku call. The unit test in `tests/unit/test_session_executor_extraction_decoupling.py` asserts that at the moment `_handle_dev_session_completion` returns, the scheduled extraction task's `.done()` is `False` — proving the nudge ran ahead of extraction completion.

**Duplicate-schedule guard** (addresses concern #7):

`_pending_extraction_tasks` is a `dict[str, asyncio.Task]` keyed by `session_id`, not a `set`. This prevents duplicate extractions when `_execute_agent_session` runs twice for the same session (health-check revival, retry, manual resume), which would otherwise save duplicate observations at importance 4.0 and race on the `clear_session(session_id)` cleanup. On schedule, if `session_id` already has a pending non-done task, log and skip:

```python
# Module-level state in agent/session_executor.py:
_pending_extraction_tasks: dict[str, asyncio.Task] = {}

def _schedule_post_session_extraction(session_id: str, response_text: str) -> None:
    """Fire-and-forget post-session memory extraction.

    Synchronous — creates and registers an asyncio.create_task; does NOT await it.
    Preserves #987 ordering invariant: _handle_dev_session_completion must be able
    to run before this task completes. Deduplicates by session_id to avoid races
    when _execute_agent_session runs twice (health-check revival, retry).

    See hotfix #1055.
    """
    existing = _pending_extraction_tasks.get(session_id)
    if existing is not None and not existing.done():
        logger.info(
            "[memory_extraction] Extraction already in-flight for %s, skipping duplicate",
            session_id,
        )
        return

    async def _wrapper() -> None:
        try:
            from agent.memory_extraction import run_post_session_extraction

            await run_post_session_extraction(session_id, response_text)
        except asyncio.CancelledError:
            raise  # preserve cancellation semantics for shutdown drain
        except Exception as e:
            logger.debug(
                "[memory_extraction] Background extraction failed for %s (non-fatal): %s",
                session_id,
                e,
            )

    task = asyncio.create_task(_wrapper(), name=f"post_session_extraction:{session_id}")
    _pending_extraction_tasks[session_id] = task
    task.add_done_callback(lambda t: _pending_extraction_tasks.pop(session_id, None))
```

**Shutdown drain ordering** (addresses concern #5):

`drain_pending_extractions(timeout: float = 5.0) -> None` runs AFTER the worker-task wait (`worker/__main__.py:408` — `await asyncio.gather(*pending, return_exceptions=True)`) and BEFORE the health/notify/reflection cancels. At that point:
- All worker loops have drained → every extraction that will be scheduled has been scheduled.
- The event loop is still running → pending extractions can complete or be cancelled cleanly.
- Health/notify/reflection tasks are still live → but we are ordered before their cancellation.

Exact insertion point in `worker/__main__.py`, after line 408:

```python
# Drain in-flight post-session extractions (hotfix #1055)
try:
    from agent.session_executor import drain_pending_extractions

    await drain_pending_extractions(timeout=5.0)
except Exception as e:
    logger.warning(f"Extraction drain failed: {e}")

# Cancel health monitor  ← existing line 410 continues unchanged
```

`drain_pending_extractions` MUST no-op when `_pending_extraction_tasks` is empty (first-deploy case / worker that never ran a session):

```python
async def drain_pending_extractions(timeout: float = 5.0) -> None:
    """Wait up to `timeout` seconds for pending extraction tasks, then cancel the rest.

    No-op if _pending_extraction_tasks is empty. See hotfix #1055.
    """
    if not _pending_extraction_tasks:
        return  # First-deploy case — nothing to drain

    pending = list(_pending_extraction_tasks.values())
    logger.info("[memory_extraction] Draining %d pending extraction task(s)", len(pending))
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        task.cancel()
    if still_pending:
        logger.warning(
            "[memory_extraction] Cancelled %d extraction task(s) that did not complete within %.1fs",
            len(still_pending),
            timeout,
        )
```

5s drain adds to the existing 60s worker-wait → ~65s graceful-shutdown ceiling. Risk 1 below documents that the drain exists (rather than cancelling immediately) to preserve the common-case extraction that is already mid-flight — the data loss tolerance covers only the stall case, not normal completion.

**Why reorder to "extraction after finalization" rather than keep awaiting (with a timeout) in `_run_work`?**

Even with a 35-second hard timeout on extraction, blocking `_run_work` for up to 35 seconds blocks finalization for up to 35 seconds. That is 35 seconds during which the worker event loop heartbeat tasks could stall from perspectives of other watchdogs, and the user-visible `completed` state is delayed. Fire-and-forget makes the pipeline latency independent of extraction latency — the correct async pattern.

**Why not a separate thread for extraction?**

`AsyncAnthropic` is already async-native; spawning a thread just to hold the HTTP client adds no resilience over `asyncio.create_task` with `asyncio.wait_for`. Threads also escape the event loop's shutdown coordination, which makes the "cancel pending extractions on worker shutdown" requirement messier.

**What about the reference patterns in `agent/intent_classifier.py:204` and `bridge/media.py:349`?**

Those wrap sync `anthropic.Anthropic` via `asyncio.to_thread` / `run_in_executor`. That pattern is ALSO correct and keeps the API calls off the event loop. I'm not using that pattern here because:
- `AsyncAnthropic` is a cleaner fit — it avoids the thread-pool hop.
- The existing sync wrappers in those files don't have explicit timeouts; migrating them is NOT in scope for this hotfix (they live outside `agent/memory_extraction.py`). They remain untouched per No-Gos. The Layer 3 validator hook follow-up will codify the "wrapped-sync is OK" exemption for those sites.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/memory_extraction.py` — three functions with outer `try/except Exception` that currently swallow SDK failures silently. Test: stub the AsyncAnthropic client to raise, assert `logger.warning(...)` with a distinguishable message is emitted AND `memory.extraction.error` counter increments with the right `error_class` tag.
- [ ] `agent/memory_extraction.py` — NEW `except asyncio.TimeoutError` branches. Test: stub the client's `messages.create` to be an async coroutine that internally calls `time.sleep(40)` (real sync block, NOT `asyncio.sleep` — see concern #2). Assert `logger.warning` with the "hard timeout" message is emitted within ~35s, the function returns the empty/None result, and `memory.extraction.error` counter increments with `error_class="timeouterror"`.
- [ ] `agent/session_executor.py` — extraction scheduling block. Test: stub `run_post_session_extraction` to raise `RuntimeError`, assert the exception is caught inside the fire-and-forget task wrapper and does not propagate out; assert `logger.debug` was called.
- [ ] `agent/session_executor.py` — CancelledError path. Test: schedule an extraction, cancel it, assert no analytics counter fires (CancelledError is expected on shutdown) and no stray warnings.

### Empty/Invalid Input Handling

- [ ] `extract_observations_async` already short-circuits on empty `response_text` (L86). This plan preserves that guard; no new test needed.
- [ ] `run_post_session_extraction` already wraps in try/except and cleans up session state in `finally` (L671-691). No new test needed.
- [ ] Verify: if `response_text` is None/empty, NO AsyncAnthropic client is instantiated (guard fires first). Test exists in `tests/unit/test_memory_extraction.py::TestDetectOutcomes::test_empty_thoughts` and `test_empty_response`; extend to confirm no client instantiation occurs in that path.

### Error State Rendering

- [ ] The feature has no user-visible output — extraction is background-only. Failure is logged only, and surfaced via the `memory.extraction.error` analytics counter on `/dashboard.json`. Log messages must be distinct per failure class (timeout vs. auth vs. other) so operators can diagnose from logs alone.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` — ADD new test class `TestEventLoopSafety` with **5 cases**:
    - `test_hard_timeout_caught_and_logged_extract_observations`: Patches `anthropic.AsyncAnthropic` to return a client whose `messages.create` is an async coroutine that executes `time.sleep(40)` internally (real sync block that models the observed sync-socket stall; see concern #2). Asserts `extract_observations_async` returns `[]` within ~35s, emits WARNING matching "hard timeout", and increments `memory.extraction.error` counter with `error_class="timeouterror"`.
    - `test_hard_timeout_caught_and_logged_detect_outcomes`: Same pattern for `detect_outcomes_async`.
    - `test_hard_timeout_caught_and_logged_post_merge_learning`: Same pattern for `extract_post_merge_learning`.
    - `test_sdk_api_timeout_caught_and_logged`: Stubs `anthropic.AsyncAnthropic` to raise `anthropic.APITimeoutError` directly; asserts graceful handling and counter increment with `error_class="apitimeouterror"`.
    - `test_no_sync_anthropic_client_grep_canary`: Uses `subprocess.run` to grep `agent/memory_extraction.py` for `anthropic\.Anthropic(` — asserts zero matches (regression canary).
- [ ] `tests/unit/test_memory_extraction.py` — ADD test `test_extract_post_merge_learning_runs_inside_asyncio_run`: Calls `asyncio.run(extract_post_merge_learning(...))` with a mocked `AsyncAnthropic` whose `messages.create` returns a minimal valid response. Asserts no `RuntimeError: This event loop is already running`. Guards against the regression class documented in `docs/plans/agent_wiki.md:157` and the hook subprocess call path in `.claude/hooks/hook_utils/memory_bridge.py:505-509`.
- [ ] `tests/unit/test_memory_extraction.py` — UPDATE existing tests that mock `anthropic.Anthropic`: audit and swap the patch target to `anthropic.AsyncAnthropic`. Make the mock's `messages.create` an async coroutine (`AsyncMock` with `return_value=...`). Specifically: `TestExtractObservations`, `TestDetectOutcomes::test_acted_on_overlap`, `TestExtractPostMergeLearning` classes.
- [ ] `tests/unit/test_messenger.py::TestBackgroundTask` — UPDATE tests that currently exercise `_run_work` to remove any assumption that extraction runs synchronously inside `_run_work`. Specifically verify the new invariant: `_run_work` returns BEFORE extraction would complete (by checking extraction was NOT called by the time `_run_work` returned — relocated to `_execute_agent_session`).
- [ ] NEW `tests/integration/test_session_finalization_decoupled.py` — Integration test: spawn a minimal session via the session executor with a stubbed Haiku client whose `messages.create` is:
    ```python
    class HungClient:
        async def create(self, *a, **kw):
            import time
            time.sleep(40)  # Real sync block — models the observed failure mode
    ```
    Assertions (within a 5-second bounded window):
    1. Dev session reaches `completed` or `failed` status in Redis.
    2. `_handle_dev_session_completion` was called.
    3. The parent PM session's `queued_steering_messages` grew by exactly 1, with the new entry referencing the dev session's `task._result` (verifies user-visible symptom from concern #8).
    4. The scheduled extraction task's `.done()` is `False` (still pending — proves decoupling).
    Teardown: cancel the pending extraction task via `task.cancel()`; assert no `CancelledError` propagates past the wrapper's `try/except Exception` (concern #2 second assertion).
- [ ] NEW `tests/unit/test_session_executor_extraction_decoupling.py` — Unit test suite:
    - `test_extraction_error_does_not_propagate`: Patches `run_post_session_extraction` to raise `asyncio.TimeoutError` inside the task. Runs `_execute_agent_session` with a minimal session. Asserts `complete_transcript` was called, `_handle_dev_session_completion` was called, no exception propagated out of `_execute_agent_session`.
    - `test_pm_nudge_fires_while_extraction_pending`: Stubs `run_post_session_extraction` to `await asyncio.sleep(10)` (cooperative suspension is fine here — we want the task to be pending, not to block the loop). Asserts `mock_handle_dev_session_completion.called is True` within a 1s window, AND the scheduled extraction task's `.done() is False` at the moment `_handle_dev_session_completion` returns.
    - `test_duplicate_schedule_is_deduplicated`: Calls `_schedule_post_session_extraction(session_id="s1", ...)` twice in a row. Asserts only ONE task exists in `_pending_extraction_tasks["s1"]` after both calls, and a log message matches "already in-flight for s1".
    - `test_drain_pending_extractions_noop_when_empty`: With `_pending_extraction_tasks = {}`, `await drain_pending_extractions(timeout=5.0)` returns within ~0s (not 5s) and logs nothing at WARNING. Guards against first-deploy regressions.

## Rabbit Holes

- **Migrating `bridge/routing.py:606/646`, `tools/classifier.py:84/322`, `agent/intent_classifier.py:204`, `bridge/media.py:349`**: All sync-in-async, some already wrapped, some not. OUT OF SCOPE for this hotfix. Will be covered by Layer 3's validator hook and cleanup PR. Touching them here balloons the review surface and risks destabilizing bridge routing (high-traffic code).
- **Rewriting `run_post_session_extraction` entirely**: tempting to refactor the function shape since we're in it. DON'T. Keep the surgical discipline — the three sync calls and one orchestration change are the only things changing.
- **Adding a configurable timeout**: don't pull `_EXTRACTION_HARD_TIMEOUT` into a settings file yet. Module-level constant is fine; if operators need to tune it, that's a separate issue.
- **Watchdog auto-recovery** (Layer 4): NOT in this plan. The issue's Downstream section explicitly separates layers 3+4 into a follow-up.
- **Adding a semaphore or rate limiter for concurrent extractions**: fire-and-forget with no concurrency limit is fine — post-session extraction runs once per session completion, and a 35s hard timeout caps unbounded growth. Adding a semaphore now would be premature optimization.
- **Extracting a shared `AsyncAnthropic` singleton**: the sync version has a singleton in `bridge/routing.py`. Don't propagate that pattern here — the overhead of constructing `AsyncAnthropic` per-call is negligible, and a shared client across the worker loop introduces shutdown-ordering complexity we don't need.
- **Adding per-error-class alerts** on `memory.extraction.error`: counters are emitted, dashboard surfaces them; no alert routing needed for a hotfix.

## Risks

### Risk 1: Fire-and-forget extraction starvation on shutdown
**Impact:** On graceful worker shutdown, in-flight extraction tasks may be cancelled mid-call, losing the observation/outcome data. Ungraceful shutdown (SIGKILL) loses them for sure.
**Why a 5s drain exists** (resolving nit 11): The common case is that extraction is already mid-flight and near completion when shutdown arrives (extraction takes 1-5s typically; shutdown is SIGTERM-initiated, not crash). A 5s drain window lets these common-case extractions complete — NOT the stall case. The stall case (extraction wedged for >5s) is the one we DO accept losing on shutdown; the 35s hard timeout inside the task already caps its worst-case latency, so it will either complete in <5s or be abandoned. The drain is cheap (5s) and saves the typical case. Loss tolerance covers only the stall path, not normal completion.
**Mitigation:** Extraction is already best-effort (non-fatal try/except). Shutdown-drain helper awaits pending extractions with `asyncio.wait(timeout=5.0)` — completes most in time, cancels the rest. Document this in `docs/features/subconscious-memory.md`.

### Risk 2: Race between extraction task and session deletion
**Impact:** If a session is deleted from Redis while its fire-and-forget extraction task is still running, the extraction may write to stale state (e.g., `clear_session(session_id)` in the `finally` block of `run_post_session_extraction`). This is benign today (`clear_session` is idempotent and operates on an in-memory dict) but worth flagging.
**Mitigation:** No code change required — the existing `clear_session` implementation is safe against this. Add a note in `docs/features/subconscious-memory.md` that extraction tasks are orphan-safe.

### Risk 3: `asyncio.TimeoutError` vs `anthropic.APITimeoutError` handling confusion
**Impact:** The Anthropic SDK raises its own `APITimeoutError` subclass of `anthropic.APIError`. The outer `asyncio.wait_for` raises `asyncio.TimeoutError`. If we only catch one, the other propagates and crashes the non-fatal guarantee.
**Mitigation:** Catch `asyncio.TimeoutError` explicitly with a distinct log message, then let the existing outer `except Exception` catch the SDK's `APITimeoutError` and everything else. Both branches record to the `memory.extraction.error` counter with distinguishable `error_class` tags. The outer handler already preserves non-fatality. Explicit test cases for both in `TestEventLoopSafety`.

### Risk 4: Timeout values too aggressive for slow regions
**Impact:** 30s SDK + 35s outer may be too tight for agents running in high-latency regions, causing benign timeouts to log warnings.
**Mitigation:** These are post-session EXTRACTION calls on a FAST model (Haiku). Empirically Haiku responses on `messages.create(max_tokens=500)` complete in 1-5 seconds. 30s covers 6-10x typical latency. If operators observe widespread timeouts in practice, bump the constants — simple change, no structural impact. The new `memory.extraction.error` counter on `/dashboard.json` will surface this fast.

### Risk 5: Breaking existing tests that patch `anthropic.Anthropic`
**Impact:** Test suite fails after the swap to `AsyncAnthropic` because mocks target the wrong symbol.
**Mitigation:** Audit and update mocks in `tests/unit/test_memory_extraction.py` as part of the build. Listed in Test Impact. The `/do-build` skill will run the test suite after changes and flag any broken mocks for patch.

### Risk 6: Orphan httpx.AsyncClient on timeout
**Impact:** Without `async with`, per-call instantiation leaks the underlying `httpx.AsyncClient` on `asyncio.TimeoutError` paths. CPython refcount-GC reclaims it, but under `-W error::ResourceWarning` emits noise to stderr.
**Mitigation:** All three converted call sites use `async with anthropic.AsyncAnthropic(...) as client:` — context manager form releases httpx resources deterministically even on timeout cancellation. (Addresses nit 10.)

## Race Conditions

### Race 1: Fire-and-forget extraction vs. worker shutdown
**Location:** `agent/session_executor.py` (new extraction scheduling block) and `worker/__main__.py` (shutdown sequence, after line 408).
**Trigger:** Worker receives SIGTERM while an extraction task is mid-flight on the event loop.
**Data prerequisite:** The extraction task expects `session_id` to still exist in `agent/memory_hook._INJECTED_THOUGHTS` for `detect_outcomes_async`. The cleanup in `run_post_session_extraction`'s `finally` block calls `clear_session(session_id)`, which removes this.
**State prerequisite:** The AsyncAnthropic client's httpx connection pool is released via `async with` on normal exit, timeout cancellation, and task cancellation alike.
**Mitigation:** Worker's graceful shutdown path awaits pending extractions with a bounded `asyncio.wait(timeout=5.0)` at the precise ordering specified in Technical Approach (after worker-task drain, before health/notify/reflection cancels). Tasks exceeding this are cancelled — the wrapper's `except Exception` + re-raise of `CancelledError` preserves cancellation semantics while swallowing non-cancel errors.

### Race 2: Duplicate extraction for the same session_id
**Location:** `agent/session_executor.py::_schedule_post_session_extraction`.
**Trigger:** `_execute_agent_session` runs twice for the same `session_id` — common paths: health-check revival of a session marked `running` (see #917 fallback at L1250), operator-triggered retry, manual resume via `valor_session resume`.
**Data prerequisite:** Before the fix, both invocations would call `Memory.safe_save` → duplicate observations at importance 4.0; both would call `clear_session(session_id)` in the `finally` block, racing on the in-memory dict.
**State prerequisite:** `_pending_extraction_tasks` must be keyed by `session_id` (not a `set[Task]`) so duplicates are detectable before scheduling.
**Mitigation:** `_schedule_post_session_extraction` checks `_pending_extraction_tasks.get(session_id)`; if present and not done, logs `"Extraction already in-flight for {session_id}, skipping duplicate"` and returns. Covered by `test_duplicate_schedule_is_deduplicated`.

### Race 3: Finalization running before result is fully persisted to Redis
**Location:** `agent/session_executor.py:1214-1268`.
**Trigger:** Fast finalization path under the new ordering could theoretically run before the `await self.messenger.send(...)` at `agent/messenger.py:212` has persisted the result message to Redis (via the TelegramRelayOutputHandler).
**Data prerequisite:** `messenger.send(...)` must complete BEFORE `complete_transcript` so that the SESSION_END transcript marker is written after the last message.
**State prerequisite:** The ordering in `_run_work` is: `await self._result = await coro` → `await messenger.send(result)` → return. No change. Finalization still runs AFTER `_run_work` returns.
**Mitigation:** This race already existed and is already handled by the pre-existing ordering (`messenger.send` is awaited inside `_run_work`; `_run_work` returns only after send completes). The plan doesn't change this sequence. Only the extraction block is moved OUT of `_run_work`.

### Race 4: Extraction task completing after session record is deleted
**Location:** `agent/memory_extraction.py:686-691` (`clear_session(session_id)` in finally block).
**Trigger:** A user or operator deletes the session's Popoto record while the orphaned extraction task is still running.
**Data prerequisite:** Memory records saved by `extract_observations_async` use `project_key` (not session_id) for persistence, so they survive session deletion fine.
**State prerequisite:** `clear_session(session_id)` only touches an in-memory dict keyed by session_id — safe to call with a stale ID (it just no-ops).
**Mitigation:** No code change required. Existing behavior is orphan-safe.

### Race 5: PM nudge vs. extraction task scheduling order
**Location:** `agent/session_executor.py::_execute_agent_session` — the two lines that call `_schedule_post_session_extraction(...)` and `await _handle_dev_session_completion(...)`.
**Trigger:** A naive refactor that accidentally inserts `await` before `_schedule_post_session_extraction(...)` or gathers its returned task — would re-couple extraction latency to the PM nudge, regressing #987.
**Data prerequisite:** PM nudge must arrive at the PM's steering inbox BEFORE extraction completes (extraction latency can be 30+ seconds on hang; nudge is sub-millisecond).
**State prerequisite:** `_schedule_post_session_extraction(...)` must remain a synchronous call returning `None`, not a coroutine returning a task handle.
**Mitigation:** `_schedule_post_session_extraction` is declared `def` (not `async def`) and returns `None`. Unit test `test_pm_nudge_fires_while_extraction_pending` asserts `.done() is False` at the moment `_handle_dev_session_completion` returns. A review-time checklist item: "no `await` on `_schedule_post_session_extraction`".

## No-Gos (Out of Scope)

- **Layer 3 — Validator hook** in `.claude/hooks/validators/` that rejects new `anthropic.Anthropic(` usage in async-reachable modules. Separate follow-up issue (to be filed after this PR merges).
- **Layer 4 — Watchdog auto-recovery** in `monitoring/session_watchdog.py::check_stalled_sessions()`. The watchdog currently logs `LIFECYCLE_STALL` but doesn't act; this plan does NOT change that. Separate follow-up issue.
- **Migrating other sync Anthropic call sites** in `bridge/routing.py`, `tools/classifier.py`, `agent/intent_classifier.py`, `bridge/media.py`. Already-wrapped sites remain untouched in this hotfix. Specifically: `agent/intent_classifier.py:204` (wrapped via `asyncio.to_thread`) and `bridge/media.py:349` (wrapped via `run_in_executor`) are already safe — they do NOT block the event loop. Leaving them as-is is deliberate; Layer 3's validator hook will codify the "wrapped-sync is OK" exemption for these sites. `bridge/routing.py:606/646` and `tools/classifier.py:84/322` are unwrapped but live outside the observed failure path (bridge routing, not worker extraction) — separate concerns handled in the Layer 3 follow-up.
- **Refactoring the reflection scheduler** or the docs_auditor sync path from #1034. #1034 was auth-focused, not async-focused. Separate concern.
- **Adding observability metrics** beyond the `memory.extraction.error` counter (e.g., latency histograms, per-extraction-type breakdown). Worth having, but not a hotfix requirement. Can be added later without blocking this PR.
- **Configurable timeouts via settings**. Module-level constants are sufficient for now.
- **Closing the `_run_work` docstring gap**: Task 4 updates the docstring as a small cosmetic improvement (resolving nit 12). It is low-cost and prevents a future maintainer from re-introducing the extraction block in `_run_work`. Keep.

## Update System

**No update system changes required** — this is a pure code change in `agent/memory_extraction.py`, `agent/messenger.py`, `agent/session_executor.py`, and `worker/__main__.py`. No new dependencies, no new config files, no migration steps. The next `/update` deploy pulls the new code, the valor-service restart hook cycles the worker, and the fix is live.

## Agent Integration

**No agent integration required** — this is a worker-internal change. The memory extraction subsystem does not expose tools via MCP; it runs in the background after session completion. No bridge changes. No `.mcp.json` changes. No new tool wrappers.

Integration test (already listed in Test Impact): `tests/integration/test_session_finalization_decoupled.py` verifies the end-to-end session path completes under simulated extraction stall — this is the implicit "agent works" test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — add a subsection under "Extraction Pipeline" titled "Event-Loop Safety" that documents:
    - All Anthropic calls from async code must use `AsyncAnthropic` with explicit `asyncio.wait_for` + SDK `timeout=` kwarg, inside `async with` for resource cleanup.
    - Post-session extraction runs as a fire-and-forget task AFTER finalization (not before), so a hang cannot block session completion.
    - Timeouts are `_EXTRACTION_SDK_TIMEOUT` / `_EXTRACTION_HARD_TIMEOUT` constants in `agent/memory_extraction.py`.
    - Loss of extraction data on abrupt worker shutdown is acceptable (best-effort). Graceful shutdown drains up to 5s.
    - Failures emit `memory.extraction.error` analytics counter, visible on `/dashboard.json`.
    - Extraction tasks are orphan-safe: session deletion does not corrupt them (`clear_session` is idempotent).

### Inline Documentation
- [ ] Add docstring note to each of the three call sites in `memory_extraction.py` pointing at `#1055` and the `AsyncAnthropic`/`asyncio.wait_for`/`async with` invariant.
- [ ] Add docstring to the new `_schedule_post_session_extraction` helper in `session_executor.py` explaining the ordering invariant (runs AFTER `complete_transcript`, fire-and-forget, shutdown-drained, synchronous — preserves #987).
- [ ] Add docstring to the new `drain_pending_extractions` helper explaining the shutdown ordering requirement and the no-op-on-empty case.
- [ ] Update the `_run_work` docstring in `agent/messenger.py` to note that post-session extraction is NO LONGER called from `_run_work` (moved to `session_executor`); this prevents a future maintainer from re-introducing the block. (Resolves nit 12.)

### README / Feature Index
- [ ] No new feature — no README index update needed. This is a bug fix, not a new capability.

## Success Criteria

- [ ] All three call sites in `agent/memory_extraction.py` (lines 100/105, 277/285, 412/413) use `async with anthropic.AsyncAnthropic(...) as client:`, wrap the call in `asyncio.wait_for(..., timeout=_EXTRACTION_HARD_TIMEOUT)`, and pass SDK-level `timeout=_EXTRACTION_SDK_TIMEOUT` to `messages.create`.
- [ ] `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` returns zero matches. (Narrowed to just this file; see No-Gos for why other wrapped sites are out of scope.)
- [ ] Every `except` branch in the three converted functions (both `except asyncio.TimeoutError` and outer `except Exception as e`) calls `_record_extraction_error(type(e).__name__, session_id, project_key)`. `CancelledError` is NOT recorded (guard in the helper).
- [ ] `_run_work` in `agent/messenger.py` NO LONGER calls `run_post_session_extraction`. The lines 214-223 block is removed. Docstring updated to note the relocation.
- [ ] `_execute_agent_session` in `agent/session_executor.py` schedules `run_post_session_extraction` via a **synchronous** `_schedule_post_session_extraction(...)` call (no `await`) AFTER both the happy-path `complete_transcript` call (L1224) and the `else`-fallback `complete_transcript` call (L1250), and before the `await _handle_dev_session_completion(...)` call (L1293).
- [ ] `_pending_extraction_tasks` is a `dict[str, asyncio.Task]` keyed by `session_id` (not `set`). Duplicate schedules for the same `session_id` are deduplicated with a log.
- [ ] `drain_pending_extractions(timeout=5.0)` is wired into `worker/__main__.py` shutdown sequence AFTER line 408 (worker-task wait completes) and BEFORE line 411 (health task cancel). Is a no-op when `_pending_extraction_tasks` is empty.
- [ ] Integration test passes: a session with a Haiku stub that internally runs `time.sleep(40)` reaches `completed`/`failed` status within 5 seconds, `_handle_dev_session_completion` is called within 5 seconds, PM `queued_steering_messages` grows by 1, and the scheduled extraction task is still `done() is False` when finalization returns.
- [ ] Unit test passes: `asyncio.TimeoutError` in extraction does not propagate out of the fire-and-forget wrapper; `CancelledError` does not propagate past the wrapper's `try/except`; session still finalizes cleanly.
- [ ] Unit test passes: `asyncio.run(extract_post_merge_learning(...))` with mocked `AsyncAnthropic` does not raise `RuntimeError: This event loop is already running` (guards the hook subprocess call path).
- [ ] Docs note in `docs/features/subconscious-memory.md` under "Event-Loop Safety" describes the async/timeout requirement, extraction-after-finalization ordering, and `memory.extraction.error` counter.
- [ ] Tests pass (`pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -x -q`).
- [ ] Format clean (`python -m ruff format .`).
- [ ] No regression: full unit suite passes (`pytest tests/unit/ -x -q`).

## Team Orchestration

### Team Members

- **Builder (memory-extraction-async)**
  - Name: `async-builder`
  - Role: Convert the three sync `anthropic.Anthropic(...)` call sites in `agent/memory_extraction.py` to `async with anthropic.AsyncAnthropic(...) as client:` with double-timeout wrapping. Add module-level timeout constants and `_record_extraction_error` helper. Preserve existing try/except semantics, add `except asyncio.TimeoutError` branches, emit analytics counter on every error path.
  - Agent Type: async-specialist
  - Resume: true

- **Builder (session-executor-decoupling)**
  - Name: `decouple-builder`
  - Role: Remove the `run_post_session_extraction` call from `agent/messenger.py::_run_work`. Add `_schedule_post_session_extraction` and `drain_pending_extractions` helpers to `agent/session_executor.py`, with `_pending_extraction_tasks` as a `dict[str, asyncio.Task]`. Wire the scheduler into `_execute_agent_session` synchronously after both `complete_transcript` calls and before `_handle_dev_session_completion`. Wire the drain into `worker/__main__.py` after line 408.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (tests)**
  - Name: `test-engineer`
  - Role: Write the five `TestEventLoopSafety` cases in `tests/unit/test_memory_extraction.py`, the `test_extract_post_merge_learning_runs_inside_asyncio_run` guard, the new `tests/integration/test_session_finalization_decoupled.py` (with real sync `time.sleep(40)` stub and PM nudge assertions), and the new `tests/unit/test_session_executor_extraction_decoupling.py` (four cases). Update existing mocks that target `anthropic.Anthropic`.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `doc-writer`
  - Role: Add the "Event-Loop Safety" subsection to `docs/features/subconscious-memory.md`. Update docstrings in the four modified files (`memory_extraction.py`, `messenger.py`, `session_executor.py`, `__main__.py`).
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `hotfix-validator`
  - Role: Verify the grep canary returns zero matches in `agent/memory_extraction.py`, run the full unit suite, confirm the integration test passes with bounded timing, confirm docs are updated, confirm no sync `anthropic.Anthropic` regressions in `agent/memory_extraction.py`.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using `async-specialist` for Layer 1 (domain fit: concurrency + event-loop safety), `builder` for Layer 2 (general implementation), `test-engineer` for test expansion, `documentarian` for docs, `validator` for final pass.

## Step by Step Tasks

### 1. Convert sync call sites to AsyncAnthropic (Layer 1)
- **Task ID**: build-async-extraction
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_extraction.py::TestEventLoopSafety`, `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` returns zero matches
- **Informed By**: Research findings (AsyncAnthropic signature, double-timeout rationale, async-with resource cleanup)
- **Assigned To**: `async-builder`
- **Agent Type**: async-specialist
- **Parallel**: true
- Add module-level constants `_EXTRACTION_SDK_TIMEOUT = 30.0` and `_EXTRACTION_HARD_TIMEOUT = 35.0` at the top of `agent/memory_extraction.py`.
- Add module-level helper `_record_extraction_error(error_class: str, session_id: str, project_key: str | None = None) -> None` that emits the `memory.extraction.error` analytics counter, skipping `CancelledError` (see Technical Approach for exact body).
- Convert `extract_observations_async` (lines ~90-120):
  - Swap `anthropic.Anthropic` → `anthropic.AsyncAnthropic` inside `async with ... as client:`.
  - Wrap `client.messages.create(...)` in `asyncio.wait_for(..., timeout=_EXTRACTION_HARD_TIMEOUT)`.
  - Pass `timeout=_EXTRACTION_SDK_TIMEOUT` to `messages.create`.
  - Add distinct `except asyncio.TimeoutError` branch ABOVE the existing outer catch-all; log distinguishable WARNING; call `_record_extraction_error("TimeoutError", ...)`; return empty/None.
  - Inside existing outer `except Exception as e`, add `_record_extraction_error(type(e).__name__, ...)` call (preserve existing log line).
- Convert `extract_post_merge_learning` (lines ~265-290): same pattern. Note the hook subprocess call path — correctness here is guarded by a unit test in Task 3.
- Convert `detect_outcomes_async` (lines ~405-420): same pattern.
- Add `import asyncio` and `from anthropic import AsyncAnthropic` if not already imported (keep existing `import anthropic` too).
- Preserve all existing `try/except Exception` semantics — failures remain non-fatal.
- Add inline `# hotfix #1055` comment at each converted call site for traceability.

### 2. Decouple extraction from finalization (Layer 2)
- **Task ID**: build-decouple-extraction
- **Depends On**: build-async-extraction (timeout constants and error helper must exist)
- **Validates**: `tests/unit/test_session_executor_extraction_decoupling.py`, `tests/integration/test_session_finalization_decoupled.py`
- **Assigned To**: `decouple-builder`
- **Agent Type**: builder
- **Parallel**: false (depends on Task 1)
- Remove lines 214-223 from `agent/messenger.py::_run_work` (the `run_post_session_extraction` block). Update the function's docstring to note extraction is now handled by the caller (`_execute_agent_session`) — prevents future re-introduction (resolves nit 12).
- In `agent/session_executor.py`, add a module-level `_pending_extraction_tasks: dict[str, asyncio.Task] = {}` keyed by `session_id` (NOT a `set` — dedup requirement).
- Add helper `_schedule_post_session_extraction(session_id: str, response_text: str) -> None` as a **synchronous** `def` (NOT `async def`):
  - Checks `_pending_extraction_tasks.get(session_id)`; if present and not done, logs `"Extraction already in-flight for {session_id}, skipping duplicate"` and returns.
  - Creates inner `async def _wrapper()` that awaits `run_post_session_extraction(...)` inside `try/except`, re-raises `CancelledError`, swallows other exceptions with debug log.
  - `asyncio.create_task(_wrapper(), name=f"post_session_extraction:{session_id}")`, registers in dict, adds done-callback that pops the key.
- Add async helper `drain_pending_extractions(timeout: float = 5.0) -> None`:
  - No-op returns immediately if `_pending_extraction_tasks` is empty.
  - `asyncio.wait(list(_pending_extraction_tasks.values()), timeout=timeout)`; cancel still-pending; log at INFO/WARNING.
- In `_execute_agent_session`, call `_schedule_post_session_extraction(session.session_id, task._result or "")` **synchronously (NO `await`)** immediately AFTER the `if agent_session: ... else: ...` finalization block completes (i.e., after BOTH L1224 and L1250 paths return) and BEFORE the `await _handle_dev_session_completion(...)` call at L1293. **CRITICAL**: no `await`, no `asyncio.gather` — any awaiting on the scheduler regresses #987.
- Wire `drain_pending_extractions(timeout=5.0)` into `worker/__main__.py` AFTER line 408 (`await asyncio.gather(*pending, return_exceptions=True)`) and BEFORE line 410 (`# Cancel health monitor` → `health_task.cancel()`). Use the exact try/except skeleton from Technical Approach.
- Add inline `# hotfix #1055` comments for traceability.

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-async-extraction, build-decouple-extraction
- **Validates**: self (tests must run green)
- **Assigned To**: `test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestEventLoopSafety` class to `tests/unit/test_memory_extraction.py` with **5 cases**:
    - `test_hard_timeout_caught_and_logged_extract_observations` — use a stub `AsyncAnthropic` whose `messages.create` is `async def create(...): import time; time.sleep(40)`. Assert return value, WARNING log, and `memory.extraction.error` counter with `error_class="timeouterror"`.
    - `test_hard_timeout_caught_and_logged_detect_outcomes` — same pattern for `detect_outcomes_async`.
    - `test_hard_timeout_caught_and_logged_post_merge_learning` — same pattern for `extract_post_merge_learning`.
    - `test_sdk_api_timeout_caught_and_logged` — stub `AsyncAnthropic` to raise `anthropic.APITimeoutError`. Assert graceful handling + counter with `error_class="apitimeouterror"`.
    - `test_no_sync_anthropic_client_grep_canary` — `subprocess.run(["grep", "-n", "anthropic\\.Anthropic(", "agent/memory_extraction.py"])` returns exit code 1 (no matches).
- Add standalone test `test_extract_post_merge_learning_runs_inside_asyncio_run` to `tests/unit/test_memory_extraction.py`:
    ```python
    def test_extract_post_merge_learning_runs_inside_asyncio_run(monkeypatch):
        """Guards against 'event loop is already running' in hook subprocess path.

        Hook at .claude/hooks/hook_utils/memory_bridge.py:505-509 calls
        asyncio.run(extract_post_merge_learning(...)). See docs/plans/agent_wiki.md:157.
        """
        # ... patch AsyncAnthropic with a minimal async-mocked messages.create ...
        result = asyncio.run(extract_post_merge_learning("title", "body", "diff"))
        # assert result is a Memory-like object or None; MUST NOT raise RuntimeError
    ```
- Audit `tests/unit/test_memory_extraction.py` for any test that patches `anthropic.Anthropic`. UPDATE the patch target to `anthropic.AsyncAnthropic` with `AsyncMock` for `messages.create`. Specifically: `TestExtractObservations`, `TestDetectOutcomes::test_acted_on_overlap`, `TestExtractPostMergeLearning`.
- Create `tests/integration/test_session_finalization_decoupled.py`:
    ```python
    class HungClient:
        class messages:
            @staticmethod
            async def create(*a, **kw):
                import time
                time.sleep(40)  # Real sync block — models the observed failure mode
    ```
    - Set up a minimal `_execute_agent_session` scenario with the stubbed client monkeypatched in.
    - Within a 5-second bounded window, assert:
        1. Dev session reaches `completed` or `failed` status in Redis (re-read via `AgentSession.query.filter(...)`).
        2. `_handle_dev_session_completion` was called.
        3. Parent PM session's `queued_steering_messages` grew by exactly 1, new entry references the dev session's `task._result` (covers user-visible symptom).
        4. The scheduled extraction task's `.done()` is `False` at the moment finalization returns.
    - Teardown: `task.cancel()`; assert no `CancelledError` propagates past the wrapper's `try/except Exception`.
- Create `tests/unit/test_session_executor_extraction_decoupling.py` with 4 cases:
    - `test_extraction_error_does_not_propagate` — patch `run_post_session_extraction` to raise `asyncio.TimeoutError`; assert `complete_transcript` and `_handle_dev_session_completion` still called, no exception out.
    - `test_pm_nudge_fires_while_extraction_pending` — stub `run_post_session_extraction` to `await asyncio.sleep(10)`. Within 1s, assert `mock_handle_dev_session_completion.called is True` AND scheduled task `.done() is False` at the moment `_handle_dev_session_completion` returns.
    - `test_duplicate_schedule_is_deduplicated` — call `_schedule_post_session_extraction(session_id="s1", ...)` twice; assert only ONE task in `_pending_extraction_tasks["s1"]` and INFO log with "already in-flight for s1".
    - `test_drain_pending_extractions_noop_when_empty` — with `_pending_extraction_tasks = {}`, assert `await drain_pending_extractions(timeout=5.0)` completes within 0.1s (no 5s block) and emits no WARNING.
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -x -v` and confirm all pass.

### 4. Update documentation
- **Task ID**: document-hotfix
- **Depends On**: build-async-extraction, build-decouple-extraction
- **Assigned To**: `doc-writer`
- **Agent Type**: documentarian
- **Parallel**: true with Task 3
- Add an "Event-Loop Safety" subsection to `docs/features/subconscious-memory.md` under the "Extraction Pipeline" section describing: async/timeout requirement, `async with` resource cleanup, fire-and-forget ordering (runs after `complete_transcript`, synchronous scheduler to preserve #987), loss-of-data tolerance on abrupt shutdown (5s graceful drain), timeout constants, `memory.extraction.error` counter visibility.
- Update the `_run_work` docstring in `agent/messenger.py` noting extraction is now handled by the caller (`_execute_agent_session`) — resolves nit 12, prevents re-introduction.
- Add a docstring to `_schedule_post_session_extraction` in `agent/session_executor.py` explaining the synchronous-call invariant (preserves #987), dedup-by-session_id, and fire-and-forget semantics.
- Add a docstring to `drain_pending_extractions` explaining the post-worker-drain / pre-health-cancel ordering and the no-op-on-empty case.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-hotfix
- **Assigned To**: `hotfix-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -n "anthropic\.Anthropic(" agent/memory_extraction.py` — must return zero matches (exit 1).
- Run `pytest tests/unit/ -x -q` — full unit suite passes.
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_messenger.py tests/unit/test_session_executor_extraction_decoupling.py tests/integration/test_session_finalization_decoupled.py -v` — all new and updated tests pass.
- Run `python -m ruff format --check .` — format clean.
- Confirm `docs/features/subconscious-memory.md` has the new "Event-Loop Safety" subsection with all four required bullets (async/timeout, fire-and-forget ordering, loss tolerance, counter).
- Confirm inline docstrings in the four modified files (`memory_extraction.py`, `messenger.py`, `session_executor.py`, `__main__.py`) reference `#1055`.
- Confirm `_schedule_post_session_extraction` is declared with `def` (NOT `async def`) and is called without `await` in `_execute_agent_session` — review-time invariant for #987.

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
| Scheduler is synchronous | `grep -n "def _schedule_post_session_extraction" agent/session_executor.py` | matches `def ` (not `async def`) |
| No await on scheduler | `grep -n "await _schedule_post_session_extraction" agent/session_executor.py` | exit code 1 (no matches) |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Consistency Auditor | Duplicate `## Test Impact` sections with conflicting content | Revision pass: consolidated into single section after `## Failure Path Test Strategy` and before `## Rabbit Holes` | Kept the richer 8-bullet version; dropped the 5-bullet duplicate; reconciled `TestEventLoopSafety` at 5 cases |
| CONCERN | Skeptic | Integration test stub uses `asyncio.sleep(40)` (cooperative) instead of modeling sync-socket stall | Revision pass: Test Impact now specifies `time.sleep(40)` inside async `create` stub | See `HungClient` shape in Test Impact and Step 3; added teardown assertion that no `CancelledError` propagates |
| CONCERN | Skeptic/Archaeologist/Simplifier | Factually wrong reference to nonexistent `agent/post_merge_learning.py`; actual caller is `.claude/hooks/hook_utils/memory_bridge.py` | Revision pass: Data Flow and Freshness Check corrected; added unit test `test_extract_post_merge_learning_runs_inside_asyncio_run` to guard "event loop already running" regression | Success Criteria grep narrowed to `agent/memory_extraction.py`; kept all three sites in scope with updated rationale |
| CONCERN | Operator | No visibility on silent extraction failures after decoupling | Revision pass: added `_record_extraction_error` helper and `memory.extraction.error` counter in every `except` branch (except `CancelledError`) | See Technical Approach for helper body; Dashboard pickup automatic via `/dashboard.json` |
| CONCERN | Operator | Shutdown drain ordering not specified vs. existing worker/task cancellations | Revision pass: Technical Approach now specifies AFTER line 408 and BEFORE line 410 in `worker/__main__.py`, with exact insertion snippet | `drain_pending_extractions` is a no-op when `_pending_extraction_tasks` is empty (first-deploy case) |
| CONCERN | Archaeologist | Plan does not verify the #987 invariant end-to-end | Revision pass: Technical Approach marks scheduler as `def` (not `async def`); Task 2 explicitly flags "NO `await`, NO `asyncio.gather`"; test `test_pm_nudge_fires_while_extraction_pending` added; verification table has synchronous-scheduler grep checks | Race 5 added for naive-refactor regression |
| CONCERN | Adversary | Duplicate extraction for same session_id not guarded | Revision pass: `_pending_extraction_tasks` is `dict[str, asyncio.Task]` keyed by session_id; schedule dedupes; Race 2 added; `test_duplicate_schedule_is_deduplicated` added | See exact guard snippet in Technical Approach |
| CONCERN | User | Integration test asserts finalization but not user-visible symptom (PM nudge) | Revision pass: Test Impact integration test now has 4 assertions including `queued_steering_messages` grew by 1; unit test `test_pm_nudge_fires_while_extraction_pending` added | Two tests cover the nudge — one unit (mocked), one integration (real Popoto) |
| CONCERN | Consistency Auditor | Success Criterion #2 grep scope contradicts Task 5 validation | Revision pass: Success Criterion narrowed to `agent/memory_extraction.py` only; Task 5 validation grep matches; No-Gos explicitly cites `agent/intent_classifier.py:204` (`asyncio.to_thread`) and `bridge/media.py:349` (`run_in_executor`) as already-safe | Wrapped-sync sites remain untouched in this hotfix; Layer 3 follow-up codifies the exemption |
| NIT | Adversary | AsyncAnthropic client not closed on timeout | Revision pass: all three call sites use `async with anthropic.AsyncAnthropic(...) as client:` for deterministic httpx cleanup | Risk 6 added |
| NIT | Simplifier | 5-second drain contradicts "loss is acceptable" framing | Revision pass: Risk 1 now explicitly distinguishes common-case (near-complete extractions drain in <5s) from stall-case (the hard-timeout-capped task that we DO accept losing) | Kept the 5s drain; rationale preserved |
| NIT | Simplifier | `_run_work` docstring update is cosmetic | Revision pass: kept the update in Task 4 — it prevents a future maintainer from re-introducing the extraction block; small cost, regression protection benefit | Noted in No-Gos ("Closing the `_run_work` docstring gap") |

Verdict recorded: `artifact_hash=sha256:9fc79e36b86bbcaa9b694870166da3180a00dd13c8f860a5f96f9130d5b86092` (pre-revision).

---

## Open Questions

None. All critique findings have been addressed in the plan text above. Blocker (duplicate `## Test Impact`) is resolved — there is now exactly one section after `## Failure Path Test Strategy` and before `## Rabbit Holes`. `revision_applied: true` is set in frontmatter so the next SDLC dispatch advances to `/do-build` (Row 4c).
