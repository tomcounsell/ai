---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2088
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-14T08:05:24Z
---

# Worker Loop Survives ModelException When Popping a Corrupted AgentSession

## Problem

The standalone worker runs one async `_worker_loop` coroutine per *worker_key*
(project key or chat id). Each loop pops a `pending` `AgentSession` and drives it
`pending → running`. Issue #1803 closed one way an *uncaught* exception could
escape the pop and kill the whole loop task (a `StatusConflictError` race). This
issue is a **second instance of the same class**, arriving through a different
exception type the #1803 handler does not catch.

**Current behavior:** During worker startup, `_worker_loop`
(`agent/agent_session_queue.py`) crashes with an unhandled Popoto
`ModelException` while transitioning a *fully-corrupted* `AgentSession` record
(all fields `None` except `status="pending"`; `created_at` missing) from
`pending → running`. The exception propagates out of the loop coroutine and kills
that worker_key's entire loop task. Observed **5 times today** at worker
restarts (Sentry VALOR-E5 / `7609408218`, 2026-07-14 05:32–07:04 UTC — the issue
said "3 times"; Sentry recorded 5 identical events). Each time the loop
self-healed within ~5 minutes — but via a *separate* mechanism (the periodic
session-health sweep), **not** via the pop-path exception handling. The crash
still fires every time a corrupted pending record is popped; it only gets cleaned
up after the fact.

**Confirmed escaping exception (Sentry, all 5 events identical):**
`popoto.exceptions.ModelException` with the verbatim message
`"Model instance parameters invalid. Failed to save."`. It is raised
unconditionally by Popoto's `pre_save()` (`popoto/models/base.py:913`) whenever
`self.is_valid()` returns `False`; the message carries no per-field detail. The
traceback chain is
`agent_session_queue.py:1700 _worker_loop → session_pickup.py:474 _pop_agent_session → session_lifecycle.py:716 transition_status → models/agent_session.py:974 save → popoto pre_save → raise ModelException`.
The message contains the substring **"invalid"** (never "validation") — a
load-bearing fact for the reaper-predicate analysis below.

**Why it matters:** While a corrupted record sits at the head of the queue, any
co-tenant pending sessions for that worker_key are stranded until the sweep
(~5 min) or the next restart. It is currently non-blocking only because corrupted
records are rare and the sweep is prompt — the design hole (an uncaught
single-session exception can kill a whole loop) remains open, exactly as it was
before #1803.

**Desired outcome:** A corrupted-record transition failure is handled inside the
loop the same way a `StatusConflictError` is: logged, the session skipped/cleaned,
the loop continues. No exception type raised while popping/transitioning a *single*
session should ever be able to terminate the loop task.

## Freshness Check

**Baseline commit:** `2776f2abbfd253ac06a11a5976f67829703d0cc2` (2026-07-14 14:13:55 +0700)
**Issue filed at:** 2026-07-14T07:04:04Z
**Disposition:** Unchanged

**File:line references re-verified (all exact — no drift):**
- `agent/agent_session_queue.py:1700` — `session = await _pop_agent_session(worker_key, is_project_keyed)` — still the primary pop.
- `agent/agent_session_queue.py:1701` — `except StatusConflictError as e:` (the #1803 skip-and-continue handler) — confirmed.
- `agent/agent_session_queue.py:1796` — `except BaseException:` → `raise` (the re-raise the ModelException falls through to) — confirmed.
- `agent/session_health.py:4456` — `cleanup_corrupted_agent_sessions()` reaper — confirmed (issue cited line 94, which is the re-export/import in `agent_session_queue.py`; the definition lives in `session_health.py:4456`).
- `worker/__main__.py:709` — startup call to `cleanup_corrupted_agent_sessions()` — confirmed (Step 2 of recovery).

**Additional root-cause location found during re-verification (worth recording):**
- `agent/session_pickup.py:474` — `transition_status(chosen, "running", ...)` inside `_pop_agent_session`; and `:633` — the same call inside `_pop_agent_session_with_fallback`. This is where `chosen.save()` on a corrupted record raises the Popoto exception. The issue said "`_pop_agent_session` calls `transition_status(→running)` internally" — confirmed precisely.

**Cited sibling issues/PRs re-checked:**
- #1803 — CLOSED 2026-06-26. Its fix is the `except StatusConflictError` handler at `agent_session_queue.py:1701` with bounded escalation (`_conflict_counts`, `CONFLICT_ESCALATION_PRIMARY_N=3`, `CONFLICT_ESCALATION_LAST_RESORT_N=6`). This plan extends that handler pattern, not replaces it.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=<createdAt>` empty for `agent_session_queue.py`, `session_pickup.py`, `session_health.py`, `models/session_lifecycle.py`).

**Active plans in `docs/plans/` overlapping this area:**
- `popoto-descriptor-pollution-audit.md` (status: Ready, tracking #2083) — audits/removes defensive scar tissue in `models/agent_session.py` and `models/session_lifecycle.py` around Popoto 1.8.0 `save()` behavior and corrupted/descriptor-polluted records. **Overlap is adjacent, not colliding:** that plan touches the *model save/index* layer; this plan touches the *worker pop-loop exception handler*. Coordination signal: if the descriptor-pollution audit lands first and changes which exception type `save()` raises on a corrupted record, re-confirm the catch clause here still matches. Recorded as Risk 3.

**Notes:** Bug confirmed against current main — **not** merely reproducible-by-reasoning. The escaping class was read from the live Sentry tracebacks (VALOR-E5, 5 events 2026-07-14): `popoto.exceptions.ModelException`, message `"Model instance parameters invalid. Failed to save."`, raised by `pre_save()` when `is_valid()` is `False`. `ModelException` is the base of `KeyMutationError`/`SkipSaveException` and a direct subclass of `Exception` — *not* of `StatusConflictError` — so it bypasses `:1701` and hits the `:1796` re-raise. Mechanism and class confirmed from production data.

## Prior Art

- **Issue/PR #1803**: "Worker loop crashes on StatusConflictError when a queued session is killed mid-pop" — CLOSED 2026-06-26. Added the `except StatusConflictError` skip-and-continue handler + bounded escalation at the primary pop site. **This is the direct predecessor**; the current fix is the same shape, one exception type wider. The regression test `tests/unit/test_worker_persistent.py::test_status_conflict_during_pop_does_not_crash_loop` is the template for this issue's test.
- No other closed issues / merged PRs matched "worker loop ModelException corrupted pop" — this is the second known instance of the class, not a repeat of a failed fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR for #1803 | Added `except StatusConflictError` at the primary pop site (`:1701`) with bounded escalation, so a session killed mid-pop is skipped instead of crashing the loop. | It caught exactly **one** exception type. The catch-all beneath it (`except BaseException: raise`, `:1796`) still re-raises every other single-session failure — including the Popoto `ModelException` a corrupted record throws during `transition_status→save()`. The #1803 fix narrowed the hole; it did not close the *class* (any uncaught single-session exception kills the loop). |

**Root cause pattern:** The pop path enumerates the *specific* exceptions it tolerates and re-raises everything else. Because "everything else" includes single-session data-corruption failures that are just as non-fatal as a mid-pop kill, each newly-observed corruption mode reopens the same loop-death hole.

**Reconciliation of root cause vs. chosen catch altitude (critique concern C3).** The critique correctly flagged tension: the diagnosis is "each handler catches exactly one type," yet the fix adds an Nth typed clause. Two altitudes were weighed:

- **`except Exception` (catch-any single-session failure).** Guarantees loop survival regardless of exception type and moots the primary-vs-secondary pop-site question. **Rejected** because (a) it swallows genuine *logic* bugs (a `KeyError`/`AttributeError` from a real defect in the pop path would be silently skipped, hiding regressions), (b) it re-introduces the exact risk the reaper's narrow delete-predicate was built to avoid — routing an arbitrary non-corruption failure into the corrupted-record cleanup path — and (c) it violates the existing Rabbit Hole prohibiting a blanket catch that also masks non-data failures.
- **`except ModelException` (the chosen altitude).** `ModelException` is **the base class of Popoto's save/validation family** (`KeyMutationError`, `SkipSaveException` both subclass it), so this single clause closes the *whole class of Popoto save/transition failures* — not one narrow type at a time. That is the durable fix the root-cause paragraph calls for, scoped to the data-failure family rather than to *all* exceptions. The loop's survival for the corruption class no longer depends on pre-enumerating each Popoto subtype; a broad logic bug still surfaces (loud crash) instead of being masked.

The durable fix is therefore: catch the **base Popoto save/transition exception** (`ModelException`) as skip-and-continue, and — because catching is not the same as *resolving* — verify the offending record is actually removed before trusting the skip (see Technical Approach, delta-check).

## Data Flow

1. **Entry point:** Worker startup (or steady-state tick) enters `_worker_loop(worker_key, event)` in `agent/agent_session_queue.py`.
2. **Slot acquire:** `registry.acquire()` (`:1695`) reserves a concurrency slot before the pop.
3. **Pop:** `_pop_agent_session(worker_key, ...)` (`session_pickup.py:203`) runs the priority/FIFO query, wins the pop lock and the SETNX run-claim, then calls `transition_status(chosen, "running", ...)` (`:474`).
4. **Corruption surfaces:** For a corrupted record (fields `None`, `created_at` missing), `transition_status` reaches `session.save()` (`models/session_lifecycle.py:717`), which raises a Popoto `ModelException` (validation/save failure). `transition_status` does **not** swallow it — it propagates out of `_pop_agent_session`.
5. **Handler miss:** Back in `_worker_loop`, the exception is not a `StatusConflictError`, so it skips `:1701` and hits `except BaseException:` at `:1796`, which releases the slot and **re-raises** — the loop coroutine dies.
6. **Current recovery (the mask):** The periodic session-health sweep + startup `cleanup_corrupted_agent_sessions()` eventually delete the corrupted record and a fresh loop is (re)started ~5 min later.
7. **Desired output:** Step 5 instead logs, routes the corrupted record to `cleanup_corrupted_agent_sessions()` (ORM-only), releases the slot, and `continue`s — co-tenant pending sessions keep flowing immediately.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0-1 (scope confirmation on the secondary-pop-site question — see Open Questions)
- Review rounds: 1

This is a targeted exception-handler change mirroring an existing, reviewed fix. The bottleneck is deciding blast radius (primary pop only vs. all pop sites), not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies (no new services, keys, or config). It edits an existing worker code path and adds a unit test.

## Solution

### Key Elements

- **Pop-path handler catches the Popoto save/transition family** — the primary pop site in `_worker_loop` treats a Popoto `ModelException` (the confirmed escaping class, base of `KeyMutationError`/`SkipSaveException`) the same way it already treats `StatusConflictError`: log, attempt cleanup, skip, continue. The loop never dies for a single bad record.
- **Verify-then-trust: check the reaper's `corrupted` delta (critique BLOCKER)** — `cleanup_corrupted_agent_sessions()` only deletes a record when its ID length is wrong **or** its no-op `save()` raises a message containing `"invalid"`/`"validation"` (`session_health.py:4527-4539`). The observed message *does* contain "invalid", so the observed record **is** deleted — but a `ModelException` raised for a different reason (unique-index violation, unknown field) would **not** match the predicate, the reaper would return `corrupted: 0`, and the record would sit at the head of the queue and be re-popped every tick (defeating this plan's own success criterion). Therefore the handler **captures the reaper's returned `{"corrupted": int, "orphans": int}` dict and branches on the `corrupted` delta**: `>0` means progress (record gone) → reset the guard; `==0` means the reaper could not classify/delete it → increment a consecutive-zero counter, back off, and escalate to `logger.error` once the counter crosses a threshold (an undeletable *poison record* needing human attention). This closes both new failure modes the blocker identified: (1) an unclassifiable `ModelException` no longer silently spins forever, and (2) a *healthy* session hitting a transient `ModelException` is retried on the next tick (the reaper's no-op-save probe succeeds on retry, so it is never deleted) rather than skipped forever.
- **ORM-only corrupted-record routing** — cleanup goes through the existing `cleanup_corrupted_agent_sessions()` reaper (which already handles Popoto index-vs-hash-key drift via `_delete_with_stale_key_lookup`). No raw-Redis deletion is introduced.
- **Bounded, worker_key-keyed spin guard (with the #1803 keying caveat)** — the `ModelException` does **not** carry a `session_id` (unlike `StatusConflictError`, which #1803/spike-4 augmented at its raise site in `models/session_lifecycle.py`), and a fully-corrupted record may have no usable `session_id` at all. The guard is therefore keyed by **`worker_key`**, which is deliberately **coarser than #1803's session_id keying**: it counts *corrupted pops for this loop* rather than *this specific record*, so it cannot distinguish two different poison records for the same worker_key. This coarseness is by necessity, not choice — it is documented inline and called out here so a future reader does not "fix" it to session_id keying and re-introduce a `KeyError` on `session_id=None`. The guard throttles reaper cost via backoff and, on a persistently-zero `corrupted` delta, is the signal that an undeletable poison record is present.

### Flow

Worker startup → primary pop returns a corrupted record → `transition_status→save()` raises `ModelException` → **handler catches it** → log warning + invoke `cleanup_corrupted_agent_sessions()` **inside a `try/except Exception` that degrades to `{"corrupted": 0, "orphans": 0}` on reaper failure (so a reaper exception cannot escape this clause and kill the loop — the sibling `except BaseException` would NOT catch it)** and **capture its `{"corrupted", "orphans"}` return** → **`release_unbound()` the slot immediately (before branching), so no `asyncio.sleep` ever runs while the global concurrency slot is held** → if `corrupted > 0`: record deleted, reset guard, `continue` (next pop returns a healthy co-tenant session, no ~5-min sweep wait) → if `corrupted == 0`: record survived the reaper's predicate, increment the consecutive-zero guard, `asyncio.sleep(backoff)` (slot already released), and once past the escalation threshold emit a `logger.error` naming the worker_key as holding an undeletable poison record → `continue`. The loop keeps running either way.

### Technical Approach

- **Catch `ModelException` alongside `StatusConflictError`** at the primary pop site (`agent/agent_session_queue.py:1701`). Add `from popoto.exceptions import ModelException` (confirmed from Sentry: the escaping class is exactly `ModelException`; it is the base of `KeyMutationError` and `SkipSaveException`, so this one clause covers the whole Popoto save/transition family — `QueryException` is *not* a subclass and is intentionally excluded). Implement as a distinct `except ModelException as e:` clause immediately after the `except StatusConflictError` clause (so the existing #1803 escalation logic is untouched), *before* the `except BaseException` re-raise. Do **not** widen to `except Exception` — see the root-cause reconciliation in "Why Previous Fixes Failed" for why the base-Popoto-exception altitude is chosen over catch-any.
- **In the new clause — verify-then-trust (critique BLOCKER):**
  1. `logger.warning(...)` identifying the corrupted pop for `worker_key` (a corrupted record often has no usable `session_id`, so log worker_key + the exception message).
  2. **Wrap the reaper call in `try/except Exception` and degrade to the zero-delta path on failure (critique BLOCKER).** A raw `result = await offload_redis(cleanup_corrupted_agent_sessions)` inside the new `except ModelException` clause is *not* protected by the sibling `except BaseException: raise` (sibling `except` clauses do not catch each other), so a reaper exception would propagate out of the loop coroutine and kill the worker_key loop task — reintroducing the exact bug class this plan closes. Mirror the #1803 pattern, which already wraps every `offload_redis(...)` in `try/except Exception` at `agent_session_queue.py:1731-1743`: `try: result = await offload_redis(cleanup_corrupted_agent_sessions)` / `except Exception as _exc: logger.warning(... reaper failed for worker_key=%s ...); result = {"corrupted": 0, "orphans": 0}`. On success `cleanup_corrupted_agent_sessions()` returns `{"corrupted": int, "orphans": int}` (`session_health.py:4501-4504`); on failure the degraded `{"corrupted": 0, "orphans": 0}` flows to the zero-delta branch (backoff + escalation), so a reaper failure lands on the backoff path instead of crashing the loop.
  3. **Release the global concurrency slot NOW, before branching** — `if _slot_acquired: registry.release_unbound(); _slot_acquired = False`. This is the ordering the BLOCKER requires: the slot was acquired at `:1695` before the pop, and every branch that fails to resolve a session must release it via `release_unbound()` **before any `await`** (the invariant documented at `agent_session_queue.py:1686-1697` and mirrored by the `StatusConflictError` clause, which releases with no intervening await). Releasing here — not in the tail — guarantees the zero-delta branch's `asyncio.sleep(backoff)` never runs while the slot is held, so a persistently-undeletable poison record can never starve the worker's global concurrency budget across repeated backoffs.
  4. **Branch on `result.get("corrupted", 0)`** and log both deltas (`corrupted`, `orphans`) so the reaper's effect is observable (the slot is already released before either branch runs):
     - **`corrupted > 0`** — the head-of-queue poison record was deleted. Reset `_corrupted_pop_count[worker_key] = 0` (and `_corrupted_pop_escalated.discard(worker_key)`). `continue` immediately (the co-tenant sessions flow without the ~5-min sweep wait).
     - **`corrupted == 0`** — the reaper's delete predicate (ID-length wrong **or** save-error message contains `"invalid"`/`"validation"`, `session_health.py:4516-4539`) did **not** match this `ModelException`, so nothing was deleted. Increment `_corrupted_pop_count[worker_key]`; `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` to prevent a hot re-pop spin (slot already released above); and once the count reaches `CORRUPTED_POP_ESCALATE_N`, emit a **`logger.error`** stating that `worker_key=<...>` is stuck on an **undeletable poison record** (persistently-zero `corrupted` delta) that needs human attention — this is the operator signal the blocker/concern-C4 asked for. (A *healthy* session that hit a transient `ModelException` also lands here with `corrupted == 0`, but on the next tick its `save()` succeeds and it is processed normally — it is retried, not deleted and not skipped forever.)
  5. `continue`. The slot was already released in step 3, so the clause's tail is just `continue` (the release is hoisted above the branch rather than mirrored from the `StatusConflictError` clause's release-then-continue tail, precisely because this clause has an `await` in one branch).
- **Bounded, worker_key-keyed spin guard:** maintain a **loop-local** `_corrupted_pop_count: dict[str, int]` keyed by **`worker_key`**, declared inside `_worker_loop` alongside the #1803 state at `agent_session_queue.py:1675-1677` (the "Loop-local bounded-escalation state" block where `_conflict_counts` / `_conflict_escalated` / `_conflict_last_resort` live). It must be loop-local, **not** module-level: bridge-mode `_worker_loop` exits when the queue empties and is respawned, and a module-level counter would survive that task restart — a stale escalation latch would then silently suppress the one-shot `logger.error` for a genuinely new incident on the same worker_key, and would leak one entry per worker_key with no eviction. Loop-local scope resets the guard cleanly on every `_worker_loop` (re)start, exactly like `_conflict_counts`. It mirrors `_conflict_counts` but is keyed by worker_key rather than session_id because a corrupted `ModelException` carries no session_id — see the keying caveat in Key Elements and the inline comment requirement in Documentation. `.get(worker_key, 0)` tolerates first-sight without `KeyError`. Named constants (grain-of-salt/tunable per repo convention): `CORRUPTED_POP_ESCALATE_N` (consecutive-zero-delta count before the `logger.error` fires; escalate-once via a `_corrupted_pop_escalated` set, matching the `_conflict_escalated` idempotency pattern) and `CORRUPTED_POP_BACKOFF_SECONDS` (the `asyncio.sleep` backoff on a zero-delta repeat). No `session_id` is dereferenced anywhere in the clause.
- **Reset the spin guard on any successful pop (mirrors `_conflict_counts` pruning).** At the single successful-pop convergence point (`agent_session_queue.py:1951-1953`, where `_conflict_counts.pop(session.session_id, None)` / `_conflict_escalated.discard(...)` / `_conflict_last_resort.discard(...)` already prune the #1803 state), add `_corrupted_pop_count.pop(worker_key, None)` and `_corrupted_pop_escalated.discard(worker_key)`. Because the corrupted-pop guard is keyed by **`worker_key`** (not session_id), the reset key is `worker_key`, not `session.session_id`. A healthy pop for this worker_key proves the head-of-queue poison record is gone, so the consecutive-zero-delta count and the escalate-once latch must both clear — otherwise a later, unrelated corrupted pop would inherit a stale count and could escalate prematurely (or never re-escalate). This keeps the guard bounded across the worker's lifetime, exactly as the #1803 pruning does for `_conflict_counts`.
- **Do NOT broaden the reaper's delete predicate.** The reaper's narrow predicate (`session_health.py:4531`) is deliberate — the no-op `save()` is a *probe*, and deleting on *any* exception would delete a healthy record whose save failed transiently. The blocker is resolved at the *handler* (delta-check + escalation) rather than by loosening the reaper, so no healthy record is ever auto-deleted; a genuinely-undeletable record surfaces to a human instead.
- **Secondary/fallback pop sites** (`:1860`, `:1881`, `:1898`, and the exit-time fallback ~`:1920`) currently have bare `except BaseException: raise` and catch *neither* `StatusConflictError` nor `ModelException`. In the observed scenario they are unreachable (a corrupted `status="pending"` record makes the `_has_pending` idle-check truthy, so control `continue`s at the top and never reaches these branches). Whether to harden them too is the one real scope decision — see Open Questions. Baseline plan: fix the primary site (closes the observed + reasoned bug); recommended stretch: extract a shared guarded-pop helper so all pop sites share one skip-and-continue path (prevents the *next* instance of this class). Decision deferred to critique/PM.
- **No Popoto model schema change** — this edits control flow only; no migration required.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `except ModelException` block is the unit under test: assert observable behavior — a `logger.warning` fires AND the loop continues (`_pop_agent_session` called ≥2×) AND the worker is cleaned up from `_active_workers`. No silent swallow: the handler logs and routes to cleanup. Use the confirmed real exception: `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")`.
- [ ] Assert `cleanup_corrupted_agent_sessions` is invoked (mock it) when a corrupted-record `ModelException` is caught, and that a reaper failure (mock raising) does not re-crash the loop.
- [ ] **Delta-check happy path:** mock the reaper to return `{"corrupted": 1, "orphans": 0}` → assert the guard counter is reset and no `logger.error` escalation fires.
- [ ] **Undeletable-poison escalation (critique BLOCKER):** mock the reaper to return `{"corrupted": 0, "orphans": 0}` on every call while `_pop_agent_session` keeps raising `ModelException` → assert (a) the loop still survives, (b) a backoff `asyncio.sleep` is applied, and (c) after `CORRUPTED_POP_ESCALATE_N` zero-delta pops a `logger.error` naming the worker_key fires exactly once (idempotent via the escalated set).

### Empty/Invalid Input Handling
- [ ] Corrupted record with `session_id = None` / all-`None` fields is the invalid input under test — assert the bounded spin guard keys off worker_key and does not raise `KeyError` or `AttributeError` when `session_id` is absent (the handler must never dereference `session_id`).
- [ ] Assert the loop does not hot-spin: with `_pop_agent_session` raising `ModelException` repeatedly then returning `None`, the loop terminates on shutdown without exceeding a bounded reaper-invocation count, and the zero-delta path applies backoff between reaper calls.

### Error State Rendering
- [ ] No user-visible surface (internal worker loop). The observable "error state" is the log line + metric; assert the warning is emitted. State: no Telegram/UI rendering path in scope.

## Test Impact

- [ ] `tests/unit/test_worker_persistent.py` — UPDATE (additive): add `test_model_exception_during_pop_does_not_crash_loop`, mirroring the existing `test_status_conflict_during_pop_does_not_crash_loop` (patch `_pop_agent_session` to raise `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")` on first call, return `None` after; assert loop survives, pops ≥2×, worker de-registered). No existing test in this file changes behavior.
- [ ] `tests/unit/test_worker_persistent.py` — UPDATE (additive): add `test_undeletable_corrupted_pop_escalates_without_crash` — reaper mocked to return `{"corrupted": 0, "orphans": 0}`; assert bounded backoff, single `logger.error` escalation after the threshold, and loop survival (the BLOCKER regression guard).
- [ ] `tests/unit/test_worker_persistent.py::test_status_conflict_during_pop_does_not_crash_loop` — UNCHANGED: the #1803 handler is not modified; confirm it still passes (regression guard that the new clause did not disturb the existing one).

No other existing tests are affected — the change adds one `except` clause and a bounded counter to a single loop; it does not alter any function signature, return contract, or the `StatusConflictError` path that existing tests cover.

## Rabbit Holes

- **Rewriting the whole pop/transition path to be corruption-proof at the model layer.** That is the `popoto-descriptor-pollution-audit` (#2083) plan's job. Here, stay at the loop's exception boundary.
- **Catching `BaseException`/`Exception` broadly at the pop site.** Do NOT replace the typed handlers with a blanket catch that also swallows `KeyboardInterrupt`/`CancelledError`/shutdown signals — that would break clean worker shutdown. Catch `ModelException` (and keep `StatusConflictError`) specifically; leave `BaseException` as the final re-raise for genuinely fatal cases.
- **Building a new corrupted-record deletion routine.** `cleanup_corrupted_agent_sessions()` already exists and already handles the index-vs-hash-key drift that broke hand-deletion during triage. Reuse it; do not reimplement.
- **Tuning the sweep cadence or startup reaper.** The ~5-min self-heal is a *mask*, not the fix; changing its timing is out of scope and would not close the loop-death hole.

## Risks

### Risk 1: The full-scan reaper is expensive to call on every corrupted pop
**Impact:** `cleanup_corrupted_agent_sessions()` walks `AgentSession.query.all()`. The handler calls it on **every** corrupted pop (that is how the head-of-queue poison record gets deleted promptly), so in the pathological *undeletable* case — where the reaper keeps returning `corrupted: 0` and the same record is re-popped every tick — those repeated full scans could add load and risk a hot spin.
**Mitigation:** The reaper runs on every catch, but the **zero-delta backoff** bounds the cost of the pathological case: whenever `corrupted == 0`, the handler `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` (after releasing the slot) before the loop can re-pop, so an undeletable record cannot drive a tight back-to-back re-pop/re-scan spin. A `corrupted > 0` delta means the record was deleted and the next pop returns a different (healthy) session, so the reaper is not re-invoked for that record at all. The reaper is offloaded via `offload_redis` so it never blocks the event loop, and the `worker_key`-keyed spin guard escalates to `logger.error` after `CORRUPTED_POP_ESCALATE_N` consecutive zero-delta pops.

### Risk 2: `ModelException` is too broad and swallows a real transition bug
**Impact:** If a *non-corrupt* session hits a transient Popoto `ModelException` during `save()`, the loop would skip it and (via the reaper) potentially delete a recoverable record.
**Mitigation:** The reaper's own corruption checks (ID-length, no-op-save validation probe) gate deletion — it only deletes records whose no-op `save()` *re-fails* with an `"invalid"`/`"validation"` message, so a transient error on a healthy record is not deleted (the no-op save succeeds on retry → `corrupted: 0` → the handler does not delete, does not escalate, and the healthy session is re-popped and processed next tick). The handler logs every catch at `warning` and both reaper deltas, giving Sentry/log visibility if a healthy session is unexpectedly skipped. Scope the catch to `ModelException` only (not `Exception`), matching the confirmed failure type.

### Risk 4: The reaper cannot classify/delete the popped `ModelException` record (critique BLOCKER)
**Impact:** The reaper deletes only when ID-length is wrong or the no-op `save()` message contains `"invalid"`/`"validation"` (`session_health.py:4516-4539`). The *observed* message (`"Model instance parameters invalid. Failed to save."`) contains "invalid", so the observed record is deleted — but a future `ModelException` raised for a different reason (unique-index violation, unknown field) would not match, the reaper returns `corrupted: 0`, and the record sits at the head of the queue and is re-popped every tick, blocking co-tenants indefinitely (the exact failure the plan aims to prevent).
**Mitigation:** The handler does not assume the reaper succeeded. It captures the returned `corrupted` delta and, on a persistently-zero delta, backs off (`CORRUPTED_POP_BACKOFF_SECONDS`) and escalates to `logger.error` once `CORRUPTED_POP_ESCALATE_N` consecutive zero-delta pops accumulate for that worker_key — turning a silent re-pop spin into a bounded, alerting condition that names the stuck worker_key for a human. We deliberately do **not** broaden the reaper predicate (that would risk deleting healthy records); the undeletable case is surfaced, not force-deleted.

### Risk 3: The descriptor-pollution audit (#2083) changes the raised exception type
**Impact:** If #2083 lands first and Popoto 1.8.0's atomic index maintenance changes which exception a corrupted `save()` raises, the `except ModelException` clause could stop matching.
**Mitigation:** `ModelException` is Popoto's *base* model exception — narrowing away from it is unlikely. The added unit test pins the behavior; if #2083 changes the type, that test fails loudly and signals the re-confirm. Note the coupling in the PR description.

## Race Conditions

### Race 1: Corrupted record re-popped between catch and reaper deletion
**Location:** `agent/agent_session_queue.py` new `except ModelException` clause + `agent/session_health.py:4456` reaper.
**Trigger:** The handler catches the `ModelException`, calls the reaper, but another worker (co-tenant loop for the same worker_key) pops the same corrupted record in the gap before deletion completes.
**Data prerequisite:** The corrupted record must still be indexed as `status="pending"` when the second pop's query runs.
**State prerequisite:** Two loops share the worker_key. In practice the pop lock (`_acquire_pop_lock`, 5s TTL) and SETNX run-claim serialize pops for a worker_key, so simultaneous pops of the same record are already prevented; a re-pop after lock release simply hits the same handler again (idempotent — reaper delete is idempotent, bounded counter throttles re-invocation).
**Mitigation:** Reuse the existing pop-lock/run-claim serialization; make the reaper call idempotent (it already is) and bound re-invocation. No new lock needed.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. The one genuinely open decision (whether to also harden the unreachable secondary/fallback pop sites) is surfaced as an Open Question for critique/PM rather than deferred; if PM says "harden them," it is small and folds into this same plan.

## Update System

No update system changes required — this is a purely internal worker code-path fix. No new dependencies, config files, or `scripts/update/` changes. No Popoto model schema change, so no entry in `scripts/update/migrations.py`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to `_worker_loop`. No new CLI entry point, no `.mcp.json` / MCP server change, and the bridge does not need to import anything new. The worker already runs this loop; the fix only changes how it handles one exception type.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` (or the worker-loop resilience section, whichever documents the pop loop) with a note that the pop path now survives `ModelException` (corrupted-record) failures in addition to `StatusConflictError`, extending the #1803 resilience guarantee. If a dedicated worker-loop resilience doc exists, prefer that; otherwise add a short subsection.
- [ ] No new `docs/features/README.md` index entry needed — this extends an existing documented behavior rather than adding a feature.

### Inline Documentation
- [ ] Comment the new `except ModelException` clause explaining it is the #2088 sibling of the #1803 `StatusConflictError` handler, and why the catch is scoped to `ModelException` (base of the Popoto save/transition family — single-session corruption, not fatal signals or broad logic bugs).
- [ ] Comment the `corrupted`-delta branch explaining that a `0` delta means the reaper could not classify/delete the record, so the handler must escalate rather than silently re-pop (cite critique BLOCKER).
- [ ] Comment the slot release placement: `release_unbound()` runs immediately after the reaper call and **before** the `corrupted` branch, so the zero-delta branch's `asyncio.sleep` never holds the global concurrency slot (cite critique BLOCKER + the `:1686-1697` invariant).
- [ ] Comment the spin-guard keying: it is keyed by **`worker_key`, coarser by necessity** than #1803's `session_id` keying, because a `ModelException` carries no `session_id` and a fully-corrupted record may have none — do not "fix" it to session_id keying (critique NIT). Include the grain-of-salt/tunable note on the constants.

## Success Criteria

- [ ] A corrupted `AgentSession` (all fields `None` except `status="pending"`) popped by `_worker_loop` does **not** terminate the loop task.
- [ ] The corrupted record is logged and routed to `cleanup_corrupted_agent_sessions()`; co-tenant pending sessions for the same worker_key continue processing without waiting on the ~5-min sweep.
- [ ] The handler **captures the reaper's `corrupted` delta** and branches on it: `>0` resets the guard; `==0` backs off and escalates to `logger.error` after `CORRUPTED_POP_ESCALATE_N` — no silent re-pop spin when the reaper cannot delete the record (critique BLOCKER).
- [ ] A healthy session that hits a transient `ModelException` is retried (not deleted, not skipped forever): a `corrupted: 0` reaper result does not delete anything and the session is re-popped next tick.
- [ ] New regression test `test_model_exception_during_pop_does_not_crash_loop` reproduces the corrupted-record pop and asserts the loop survives and continues (mirrors the #1803 test); `test_undeletable_corrupted_pop_escalates_without_crash` covers the zero-delta escalation path.
- [ ] No raw-Redis deletion is introduced; cleanup goes through the ORM reaper only; the reaper's delete predicate is **not** broadened.
- [ ] The bounded spin guard is keyed by `worker_key` and degrades safely when the corrupted record has no `session_id` (no `KeyError`, no hot spin).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms the new handler references `cleanup_corrupted_agent_sessions` and `ModelException`.

## Team Orchestration

### Team Members

- **Builder (worker-loop-handler)**
  - Name: pop-handler-builder
  - Role: Add the `except ModelException` clause + bounded spin guard to `_worker_loop`; wire ORM-only reaper routing.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Test Engineer (regression)**
  - Name: pop-handler-tester
  - Role: Add `test_model_exception_during_pop_does_not_crash_loop` mirroring the #1803 test; assert survival, bounded reaper invocation, session_id-free safety.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: pop-handler-validator
  - Role: Verify success criteria, run the targeted tests, confirm no raw-Redis deletion and no signature changes.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the ModelException pop-path handler
- **Task ID**: build-handler
- **Depends On**: none
- **Validates**: tests/unit/test_worker_persistent.py
- **Assigned To**: pop-handler-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `from popoto.exceptions import ModelException` to `agent/agent_session_queue.py`.
- Insert `except ModelException as e:` immediately after the existing `except StatusConflictError` clause (`:1701`) and before `except BaseException:` (`:1796`): (a) log a warning (worker_key + message; do NOT reference `session_id`), (b) **call the reaper inside a `try/except Exception` that degrades to the zero-delta path on failure — `try: result = await offload_redis(cleanup_corrupted_agent_sessions)` / `except Exception as _exc: logger.warning(... reaper failed ...); result = {"corrupted": 0, "orphans": 0}` — mirroring the #1803 wrap at `:1731-1743`, so a reaper failure flows to the backoff branch instead of propagating out (the sibling `except BaseException` does NOT catch exceptions raised inside this `except ModelException` clause)**, (c) **release the slot NOW — `if _slot_acquired: registry.release_unbound(); _slot_acquired = False` — BEFORE branching on `corrupted`, so the zero-delta branch's `asyncio.sleep` never runs while the concurrency slot is held (the BLOCKER ordering; matches the invariant at `:1686-1697` and the `StatusConflictError` clause)**, then (d) branch, then (e) `continue`.
- **Branch on `result.get("corrupted", 0)`** (the BLOCKER fix), with the slot already released: `>0` → reset `_corrupted_pop_count.pop(worker_key, None)` and `_corrupted_pop_escalated.discard(worker_key)`; `==0` → increment `_corrupted_pop_count[worker_key]`, `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)`, and once it reaches `CORRUPTED_POP_ESCALATE_N` emit a one-shot `logger.error` (gated by the `_corrupted_pop_escalated` set) naming the worker_key as holding an undeletable poison record. Log both reaper deltas (`corrupted`, `orphans`).
- **Reset the guard on a successful pop:** at the successful-pop convergence point (`:1951-1953`, alongside `_conflict_counts.pop(session.session_id, None)`), add `_corrupted_pop_count.pop(worker_key, None)` and `_corrupted_pop_escalated.discard(worker_key)` (keyed by `worker_key`, not `session.session_id`) so a healthy pop clears any stale corrupted-pop count/escalation latch for this loop.
- Add the named constants `CORRUPTED_POP_ESCALATE_N` and `CORRUPTED_POP_BACKOFF_SECONDS` (grain-of-salt/tunable comments). Declare `_corrupted_pop_count: dict[str, int]` + `_corrupted_pop_escalated: set[str]` as **loop-local** state inside `_worker_loop`, alongside the #1803 `_conflict_counts` / `_conflict_escalated` / `_conflict_last_resort` at `:1675-1677` — **not** module-level, so a bridge-mode `_worker_loop` restart (queue-empty exit → respawn) resets them cleanly and cannot carry a stale escalation latch or leak per-worker_key entries. Keyed by `worker_key`. The existing reset block at `:1951-1953` already runs inside the same function body, so it needs no scope change. Never dereference `session_id` in this clause.
- Do NOT modify the `StatusConflictError` clause or its escalation logic, and do NOT broaden the reaper's delete predicate in `session_health.py`.

### 2. Add the regression test
- **Task ID**: build-test
- **Depends On**: build-handler
- **Validates**: tests/unit/test_worker_persistent.py::test_model_exception_during_pop_does_not_crash_loop
- **Assigned To**: pop-handler-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Mirror `test_status_conflict_during_pop_does_not_crash_loop`: patch `_pop_agent_session` to raise `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")` on first call, return `None` after; assert `_worker_loop` returns without raising, pops ≥2×, and the worker is removed from `_active_workers`.
- Add `test_undeletable_corrupted_pop_escalates_without_crash`: reaper mocked to return `{"corrupted": 0, "orphans": 0}` on repeated `ModelException` pops; assert bounded backoff, a single `logger.error` escalation after `CORRUPTED_POP_ESCALATE_N`, and loop survival (BLOCKER regression guard).
- Add a case asserting a reaper failure (mock raising) does not re-crash the loop, and a corrupted record with `session_id=None` does not raise `KeyError`.
- Add a happy-path assertion: reaper returning `{"corrupted": 1, "orphans": 0}` resets the guard and fires no escalation.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-handler, build-test
- **Assigned To**: pop-handler-tester (doubles as documentarian) or a documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update the worker-loop resilience note (see Documentation section) and the inline comments.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-handler, build-test, document-feature
- **Assigned To**: pop-handler-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_persistent.py -q`.
- Verify all Success Criteria; confirm no raw-Redis ops and no function-signature changes.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Handler references reaper | `grep -c "cleanup_corrupted_agent_sessions" agent/agent_session_queue.py` | output > 0 |
| ModelException caught in loop | `grep -c "except ModelException" agent/agent_session_queue.py` | output > 0 |
| ModelException imported | `grep -c "from popoto.exceptions import ModelException" agent/agent_session_queue.py` | output > 0 |
| Reaper delta is checked | `grep -c "corrupted" agent/agent_session_queue.py` | output > 0 (handler branches on the reaper's `corrupted` delta) |
| Escalation constant present | `grep -c "CORRUPTED_POP_ESCALATE_N" agent/agent_session_queue.py` | output > 0 |
| New regression test exists | `grep -c "test_model_exception_during_pop_does_not_crash_loop" tests/unit/test_worker_persistent.py` | output > 0 |
| Escalation test exists | `grep -c "test_undeletable_corrupted_pop_escalates_without_crash" tests/unit/test_worker_persistent.py` | output > 0 |
| #1803 handler untouched | `grep -c "except StatusConflictError" agent/agent_session_queue.py` | output > 0 |
| No raw-Redis delete added | `grep -nE "\.(delete|srem|zrem)\(" agent/agent_session_queue.py \| grep -iv "release_unbound\|registry"` | (manual review — anti-criterion: no new raw-Redis ops on Popoto keys) |
| Targeted tests pass | `pytest tests/unit/test_worker_persistent.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | Broad `except ModelException` mismatched to the reaper's narrow delete predicate — records the reaper can't classify (`corrupted: 0`) re-pop every tick; a healthy session on a transient `ModelException` is skipped forever. | Solution → Key Elements ("verify-then-trust"); Technical Approach step 3; Risk 4; Success Criteria. | Handler captures the reaper's `{"corrupted", "orphans"}` return and branches on the `corrupted` delta: `>0` resets the guard; `==0` backs off + escalates to `logger.error` after `CORRUPTED_POP_ESCALATE_N`. Healthy-transient case is retried (reaper returns `corrupted:0`, deletes nothing), not skipped forever. Reaper predicate deliberately NOT broadened. |
| CONCERN | war-room | Catch type never confirmed from the 3 live crash tracebacks. | Problem ("Confirmed escaping exception"); Freshness Check Notes. | Read from Sentry VALOR-E5 (5 events, not 3): class `popoto.exceptions.ModelException`, message `"Model instance parameters invalid. Failed to save."`, raised by `pre_save()` on `is_valid()==False`. Full traceback chain cited. |
| CONCERN | war-room | Root-cause diagnosis ("each handler catches one type") contradicts adding the Nth typed clause; consider `except Exception` altitude. | Why Previous Fixes Failed → "Reconciliation of root cause vs. chosen catch altitude". | `except Exception` explicitly weighed and rejected (masks logic bugs, re-introduces heal-a-healthy-record risk, violates Rabbit Hole). Chosen `ModelException` is the **base class** of the Popoto save/transition family, so one clause closes the whole class — the durable fix the diagnosis calls for, scoped to data failures. |
| CONCERN | war-room | Spin guard keyed by worker_key can't identify the poison record; no signal for an undeletable record. | Solution → Key Elements; Technical Approach step 3; Documentation (inline). | Handler logs both reaper deltas each catch; a persistently-zero `corrupted` delta while the guard fires triggers a one-shot `logger.error` naming the stuck worker_key — the undeletable-poison operator signal. |
| NIT | war-room | worker_key keying is coarser-by-necessity vs #1803's session_id keying — document it. | Solution → Key Elements ("keying caveat"); Documentation → Inline (spin-guard keying comment). | Documented that `ModelException` carries no `session_id` (unlike the spike-4-augmented `StatusConflictError`), so worker_key keying is required, not a downgrade; inline comment warns against "fixing" it to session_id. |
| BLOCKER (re-critique) | war-room | Zero-delta branch held the concurrency slot across the backoff `asyncio.sleep` — release was ordered after the sleep, violating the release-before-await invariant (`:1686-1697`) and diverging from the `StatusConflictError` clause. | Flow; Technical Approach step 3 (release before branch); Task 1; Documentation → Inline. | `release_unbound()` moved to immediately follow the reaper call and precede the `corrupted` branch, so `asyncio.sleep` never runs while the slot is held. Flow, Technical Approach, and Task 1 all made consistent on this ordering. |
| CONCERN (re-critique) | war-room | Spin-guard counter never reset on a successful pop — a stale count could inherit across unrelated corrupted pops. | Technical Approach ("Reset the spin guard on any successful pop"); Task 1 (reset at `:1951-1953`). | Added `_corrupted_pop_count.pop(worker_key, None)` + `_corrupted_pop_escalated.discard(worker_key)` at the successful-pop convergence point (`:1951-1953`), alongside the existing `_conflict_counts` pruning, keyed by `worker_key`. |
| CONCERN (re-critique) | war-room | Risk 1 wording ("once per K corrupted pops") didn't match the call-every-catch + sleep-backoff implementation. | Risk 1 (rewritten). | Rewrote Risk 1 to state the reaper runs on every catch and the zero-delta `asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` backoff (after slot release) bounds the pathological undeletable case, rather than a "once per K pops" throttle. |
| BLOCKER (3rd critique) | war-room | Reaper call never wrapped in try/except — a reaper failure raised inside the new `except ModelException` clause is not caught by the sibling `except BaseException: raise` (sibling clauses don't catch each other), so it propagates and re-kills the loop task, reintroducing the exact bug class. | Technical Approach step 3.2; Flow; Task 1. | Wrapped `offload_redis(cleanup_corrupted_agent_sessions)` in `try/except Exception` degrading to `result = {"corrupted": 0, "orphans": 0}` on failure (mirrors the #1803 wrap at `:1731-1743`), so a reaper failure flows to the backoff/escalation path instead of propagating. |
| BLOCKER (3rd critique) | war-room | Spin-guard state specified module-level while claiming to mirror the loop-local `_conflict_counts` (`:1675-1677`). Module-level state survives a bridge-mode `_worker_loop` restart (queue-empty exit → respawn), so a stale escalation latch suppresses the one-shot `logger.error` for a new incident and leaks one entry per worker_key with no eviction. | Technical Approach ("Bounded, worker_key-keyed spin guard"); Task 1. | Specified `_corrupted_pop_count` / `_corrupted_pop_escalated` as loop-local, declared alongside `_conflict_counts` at `:1675-1677`. The reset block at `:1951-1953` already runs inside the same function body, so it needs no change. |

---

## Open Questions

1. **Secondary/fallback pop-site hardening.** The observed bug is at the primary pop (`:1700`). The secondary/fallback pops (`:1860`, `:1881`, `:1898`, exit-time ~`:1920`) have bare `except BaseException: raise` and catch neither `StatusConflictError` nor `ModelException`, but are unreachable in the corrupted-record scenario (a `pending` record keeps `_has_pending` truthy, so the loop `continue`s before reaching them). Should this plan **also** extract a shared guarded-pop helper so every pop site survives single-session failures (closes the *class* for good, slightly larger diff), or fix only the primary site (minimal, closes the observed + reasoned bug)? Recommendation: shared helper if appetite allows; primary-only otherwise.
2. **Reaper invocation cadence.** *(Resolved during revision.)* The handler invokes `cleanup_corrupted_agent_sessions()` inline on each corrupted pop and **checks the returned `corrupted` delta** to decide whether progress was made — a `>0` delta means the head-of-queue record is gone and the loop resumes immediately (co-tenants unblocked without the ~5-min sweep), while a `0` delta triggers backoff + escalation instead of a hot re-pop spin. The zero-delta backoff is what bounds full-scan cost in the pathological undeletable case, so a separate "reap every K pops" throttle is unnecessary. This is the whole point of the fix, so the inline reaper stays; only the *undeletable* case defers to human attention via the `logger.error` escalation.
