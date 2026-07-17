---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2088
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-14T08:27:45Z
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

The durable fix is therefore: catch the **base Popoto save/transition exception** (`ModelException`) as skip-and-continue — log it, opportunistically route the record to the existing ORM reaper, back off, and `continue`. Permanently *resolving* the corrupted record is the periodic session-health sweep's job (the production backstop that already self-heals these records every ~5 min today); the loop's sole new responsibility is to stop *crashing* in the meantime. The handler deliberately does **not** interpret the reaper's return value — see the Technical Approach for why that interpretation was the source of four failed critique rounds and was removed at the root.

## Data Flow

1. **Entry point:** Worker startup (or steady-state tick) enters `_worker_loop(worker_key, event)` in `agent/agent_session_queue.py`.
2. **Slot acquire:** `registry.acquire()` (`:1695`) reserves a concurrency slot before the pop.
3. **Pop:** `_pop_agent_session(worker_key, ...)` (`session_pickup.py:203`) runs the priority/FIFO query, wins the pop lock and the SETNX run-claim, then calls `transition_status(chosen, "running", ...)` (`:474`).
4. **Corruption surfaces:** For a corrupted record (fields `None`, `created_at` missing), `transition_status` reaches `session.save()` (`models/session_lifecycle.py:717`), which raises a Popoto `ModelException` (validation/save failure). `transition_status` does **not** swallow it — it propagates out of `_pop_agent_session`.
5. **Handler miss:** Back in `_worker_loop`, the exception is not a `StatusConflictError`, so it skips `:1701` and hits `except BaseException:` at `:1796`, which releases the slot and **re-raises** — the loop coroutine dies.
6. **Current recovery (the mask):** The periodic session-health sweep + startup `cleanup_corrupted_agent_sessions()` eventually delete the corrupted record and a fresh loop is (re)started ~5 min later.
7. **Desired output:** Step 5 instead logs a warning, opportunistically routes the corrupted record to `cleanup_corrupted_agent_sessions()` (ORM-only, best-effort), releases the slot, backs off briefly, and `continue`s — co-tenant pending sessions keep flowing immediately on the common path (the reaper clears the head-of-queue record), and even if the reaper cannot delete it this tick the ~5-min session-health sweep remains the authoritative backstop, exactly as in production today.

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

### Design decision: simplify the escalation machinery away (ends the 4-round churn)

Every one of the four prior critique rounds found a *new* correctness defect in one specific piece of added machinery: a "verify-then-trust" branch that captured the reaper's `{"corrupted": int, ...}` return and interpreted a `corrupted == 0` delta as an "undeletable poison record," firing a bespoke escalation. R1 (reaper predicate too narrow), R2 (slot held across the sleep), R3 (reaper call unguarded), and R4 (the reaper's `_filter_hydrated_sessions` pre-scan at `session_health.py:4508` *silently drops* phantom/orphan records before the `cleaned` counter increments, and phantom clearing is folded into a *separate* `repair_indexes()`/`phantoms_cleared` path that never lands in the returned `corrupted` count — so a genuinely self-healed phantom, which is exactly the Problem's "all fields None except status=pending" shape, reports `corrupted: 0` and gets **misclassified** as poison) are all defects in **interpreting the reaper's return value**. That interpretation is the churn.

**It is also unnecessary.** Production already self-heals corrupted records via the periodic session-health sweep (~5 min); the sweep is the authoritative undeletable-record backstop today and stays so. The *only* bug this issue must fix is the loop **crashing** in the meantime. So the plan removes the entire return-value-interpretation branch at the root: the reaper is called **opportunistically** (best-effort, return value ignored), and the handler never branches program logic on the reaper's `corrupted` delta. This deletes the whole R1/R4 class of blockers in one move while still satisfying all four acceptance criteria.

### Key Elements

- **Pop-path handler catches the Popoto save/transition family** — the primary pop site in `_worker_loop` treats a Popoto `ModelException` (the confirmed escaping class, base of `KeyMutationError`/`SkipSaveException`) the same way it already treats `StatusConflictError`: log, best-effort cleanup, skip, continue. The loop never dies for a single bad record.
- **Opportunistic ORM-only reaper call — return value ignored** — the handler invokes `cleanup_corrupted_agent_sessions()` (the existing reaper, which already handles Popoto index-vs-hash-key drift) once per corrupted pop, wrapped in `try/except Exception` so a reaper failure degrades to a no-op and can never escape the clause. It does **not** capture or branch on the reaper's `{"corrupted", "orphans"}` return. On the common path the reaper clears the head-of-queue record, so the next pop returns a healthy co-tenant session without waiting on the sweep; if the reaper cannot delete it this tick, the periodic session-health sweep is the authoritative backstop — the same mechanism that self-heals these records in production today. No raw-Redis deletion is introduced.
- **Minimal bounded, worker_key-keyed spin guard** — to avoid a hot re-pop spin against a record stuck at the queue head, the handler backs off (`asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)`) after each corrupted pop and, after `CORRUPTED_POP_ESCALATE_N` **consecutive** corrupted pops for a worker_key, emits a one-shot `logger.error` naming the stuck worker_key so an operator can investigate. This guard is a **plain consecutive-corrupted-pop counter** — it depends on nothing from the reaper's return value, so it carries none of the R1/R4 interpretation risk. It is keyed by **`worker_key`**, deliberately **coarser than #1803's session_id keying**: a corrupted `ModelException` carries no `session_id` (unlike `StatusConflictError`, which #1803/spike-4 augmented at its raise site), and a fully-corrupted record may have no usable `session_id` at all. This coarseness is by necessity, not choice — documented inline so a future reader does not "fix" it to session_id keying and re-introduce a `KeyError` on `session_id=None`. The counter is reset on any successful pop (mirroring `_conflict_counts`) so it stays bounded across the worker's lifetime.

### Flow

Worker startup → primary pop returns a corrupted record → `transition_status→save()` raises `ModelException` → **handler catches it** → `logger.warning` (worker_key + exception message; no `session_id`) → invoke `cleanup_corrupted_agent_sessions()` **inside a `try/except Exception` that degrades to a no-op on reaper failure (so a reaper exception cannot escape this clause and kill the loop — the sibling `except BaseException` would NOT catch it); the return value is ignored** → **`release_unbound()` the slot (before the backoff `await`), so no `asyncio.sleep` ever runs while the global concurrency slot is held** → increment the loop-local `_corrupted_pop_count[worker_key]`; once it reaches `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops, emit a one-shot `logger.error` naming the stuck worker_key → `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` → `continue`. On the common path the reaper deleted the head-of-queue record, so the next pop returns a healthy co-tenant session (and the successful-pop reset clears the guard); if the record was undeletable this tick, the ~5-min session-health sweep clears it, exactly as in production today. The loop keeps running either way, and the handler never inspects the reaper's return value.

### Technical Approach

- **Catch `ModelException` alongside `StatusConflictError`** at the primary pop site (`agent/agent_session_queue.py:1701`). Add `from popoto.exceptions import ModelException` (confirmed from Sentry: the escaping class is exactly `ModelException`; it is the base of `KeyMutationError` and `SkipSaveException`, so this one clause covers the whole Popoto save/transition family — `QueryException` is *not* a subclass and is intentionally excluded). Implement as a distinct `except ModelException as e:` clause immediately after the `except StatusConflictError` clause (so the existing #1803 escalation logic is untouched), *before* the `except BaseException` re-raise. Do **not** widen to `except Exception` — see the root-cause reconciliation in "Why Previous Fixes Failed" for why the base-Popoto-exception altitude is chosen over catch-any.
- **In the new clause — log, best-effort reap, back off, continue (no return-value interpretation):**
  1. `logger.warning(...)` identifying the corrupted pop for `worker_key` (a corrupted record often has no usable `session_id`, so log worker_key + the exception message).
  2. **Wrap the opportunistic reaper call in `try/except Exception` and degrade to a no-op on failure.** A raw `await offload_redis(cleanup_corrupted_agent_sessions)` inside the new `except ModelException` clause is *not* protected by the sibling `except BaseException: raise` (sibling `except` clauses do not catch each other), so a reaper exception would propagate out of the loop coroutine and kill the worker_key loop task — reintroducing the exact bug class this plan closes. Mirror the #1803 pattern, which already wraps every `offload_redis(...)` in `try/except Exception` at `agent_session_queue.py:1731-1743`: `try: await offload_redis(cleanup_corrupted_agent_sessions)` / `except Exception as _exc: logger.warning(... reaper failed for worker_key=%s (session-health sweep remains the backstop) ...)`. **The return value is intentionally not captured or branched on** — the reaper is a best-effort head-of-queue cleanup, and the periodic session-health sweep is the authoritative backstop for anything it cannot delete this tick. This is the core simplification: nothing in the handler interprets `{"corrupted", "orphans"}`, so the R1/R4 class of "reaper-delta misinterpretation" blockers cannot exist.
  3. **Release the global concurrency slot before the backoff `await`** — `if _slot_acquired: registry.release_unbound(); _slot_acquired = False`. The slot was acquired at `:1695` before the pop, and every branch that fails to resolve a session must release it via `release_unbound()` **before any `await`** (the invariant documented at `agent_session_queue.py:1686-1697` and mirrored by the `StatusConflictError` clause). Releasing here — before the `asyncio.sleep` below — guarantees the backoff never runs while the slot is held, so a record stuck at the queue head can never starve the worker's global concurrency budget across repeated backoffs. (Place the release after the reaper call so the reaper still runs under the acquired slot, matching the `StatusConflictError` clause's ordering.)
  4. **Bounded spin guard + backoff (plain counter, no reaper dependence).** Increment `_corrupted_pop_count[worker_key]` (`.get(worker_key, 0) + 1`). If it reaches `CORRUPTED_POP_ESCALATE_N` and `worker_key not in _corrupted_pop_escalated`, emit a one-shot `logger.error` stating that `worker_key=<...>` has hit `ModelException` on pop `N` times consecutively — a corrupted record appears stuck at the queue head that the session-health sweep has not yet cleared, needing operator attention — then `_corrupted_pop_escalated.add(worker_key)`. Then `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` (slot already released) to prevent a hot re-pop spin. The escalation fires purely on the consecutive-corrupted-pop count; it never reads the reaper's return value.
  5. `continue`. On the common single-corrupted-record path the reaper already deleted the record, so the next pop returns a healthy co-tenant session and the successful-pop reset (below) clears the guard; the small one-time backoff on a rare corrupted pop is negligible (corrupted records occur a handful of times per day).
- **Bounded, worker_key-keyed spin guard:** maintain a **loop-local** `_corrupted_pop_count: dict[str, int]` and `_corrupted_pop_escalated: set[str]` keyed by **`worker_key`**, declared inside `_worker_loop` alongside the #1803 state at `agent_session_queue.py:1675-1677` (the "Loop-local bounded-escalation state" block where `_conflict_counts` / `_conflict_escalated` / `_conflict_last_resort` live). It must be loop-local, **not** module-level: bridge-mode `_worker_loop` exits when the queue empties and is respawned, and a module-level counter would survive that task restart — a stale escalation latch would then silently suppress the one-shot `logger.error` for a genuinely new incident on the same worker_key, and would leak one entry per worker_key with no eviction. Loop-local scope resets the guard cleanly on every `_worker_loop` (re)start, exactly like `_conflict_counts`. It mirrors `_conflict_counts` but is keyed by worker_key rather than session_id because a corrupted `ModelException` carries no session_id — see the keying caveat in Key Elements and the inline comment requirement in Documentation. `.get(worker_key, 0)` tolerates first-sight without `KeyError`. Named constants (grain-of-salt/tunable per repo convention): `CORRUPTED_POP_ESCALATE_N` (consecutive-corrupted-pop count before the `logger.error` fires; escalate-once via the `_corrupted_pop_escalated` set, matching the `_conflict_escalated` idempotency pattern) and `CORRUPTED_POP_BACKOFF_SECONDS` (the `asyncio.sleep` backoff after each corrupted pop). No `session_id` is dereferenced anywhere in the clause.
- **Reset the spin guard on any successful pop (mirrors `_conflict_counts` pruning).** At the single successful-pop convergence point (`agent_session_queue.py:1951-1953`, where `_conflict_counts.pop(session.session_id, None)` / `_conflict_escalated.discard(...)` / `_conflict_last_resort.discard(...)` already prune the #1803 state), add `_corrupted_pop_count.pop(worker_key, None)` and `_corrupted_pop_escalated.discard(worker_key)`. Because the corrupted-pop guard is keyed by **`worker_key`** (not session_id), the reset key is `worker_key`, not `session.session_id`. A healthy pop for this worker_key proves the head-of-queue corrupted record is gone, so the consecutive-corrupted-pop count and the escalate-once latch must both clear — otherwise a later, unrelated corrupted pop would inherit a stale count and could escalate prematurely (or never re-escalate). This keeps the guard bounded across the worker's lifetime, exactly as the #1803 pruning does for `_conflict_counts`.
- **Do NOT broaden the reaper's delete predicate.** The reaper's narrow predicate (`session_health.py:4531`) is deliberate — the no-op `save()` is a *probe*, and deleting on *any* exception would delete a healthy record whose save failed transiently. This plan does not touch the reaper at all; it calls it best-effort and lets the existing sweep own permanent resolution, so no healthy record is ever auto-deleted by anything this change adds.
- **Secondary/fallback pop sites** (`:1860`, `:1881`, `:1898`, and the exit-time fallback ~`:1920`) currently have bare `except BaseException: raise` and catch *neither* `StatusConflictError` nor `ModelException`. In the observed scenario they are unreachable (a corrupted `status="pending"` record makes the `_has_pending` idle-check truthy, so control `continue`s at the top and never reaches these branches). Whether to harden them too is the one real scope decision — see Open Questions. Baseline plan: fix the primary site (closes the observed + reasoned bug); recommended stretch: extract a shared guarded-pop helper so all pop sites share one skip-and-continue path (prevents the *next* instance of this class). Decision deferred to critique/PM.
- **No Popoto model schema change** — this edits control flow only; no migration required.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `except ModelException` block is the unit under test: assert observable behavior — a `logger.warning` fires AND the loop continues (`_pop_agent_session` called ≥2×) AND the worker is cleaned up from `_active_workers`. No silent swallow: the handler logs and routes to cleanup. Use the confirmed real exception: `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")`.
- [ ] Assert `cleanup_corrupted_agent_sessions` is invoked (mock it) when a corrupted-record `ModelException` is caught, and that a reaper failure (mock raising `Exception`) does **not** re-crash the loop — the handler swallows it (best-effort) and the loop still survives and continues.
- [ ] **Bounded escalation:** with `_pop_agent_session` raising `ModelException` on every call (record stuck at the head) and the reaper mocked to a no-op, assert (a) the loop still survives, (b) a backoff `asyncio.sleep` is applied between corrupted pops, and (c) after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops a `logger.error` naming the worker_key fires **exactly once** (idempotent via `_corrupted_pop_escalated`). The escalation must not read any reaper return value.
- [ ] **Guard reset on recovery:** `_pop_agent_session` raises `ModelException` once, then returns a healthy session — assert the guard counter is cleared at the successful-pop convergence point and no escalation fires.

### Empty/Invalid Input Handling
- [ ] Corrupted record with `session_id = None` / all-`None` fields is the invalid input under test — assert the bounded spin guard keys off worker_key and does not raise `KeyError` or `AttributeError` when `session_id` is absent (the handler must never dereference `session_id`).
- [ ] Assert the loop does not hot-spin: with `_pop_agent_session` raising `ModelException` repeatedly then returning `None`, the loop terminates on shutdown and applies the `CORRUPTED_POP_BACKOFF_SECONDS` backoff between corrupted pops rather than spinning back-to-back.

### Error State Rendering
- [ ] No user-visible surface (internal worker loop). The observable "error state" is the log line + metric; assert the warning is emitted. State: no Telegram/UI rendering path in scope.

## Test Impact

- [ ] `tests/unit/test_worker_persistent.py` — UPDATE (additive): add `test_model_exception_during_pop_does_not_crash_loop`, mirroring the existing `test_status_conflict_during_pop_does_not_crash_loop` (patch `_pop_agent_session` to raise `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")` on first call, return `None` after; assert loop survives, pops ≥2×, worker de-registered). No existing test in this file changes behavior.
- [ ] `tests/unit/test_worker_persistent.py` — UPDATE (additive): add `test_repeated_corrupted_pop_escalates_without_crash` — `_pop_agent_session` raises `ModelException` on every call, reaper mocked to a no-op; assert bounded backoff between pops, a single `logger.error` escalation after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops, and loop survival. The test must not assert anything about the reaper's return value (the handler ignores it).
- [ ] `tests/unit/test_worker_persistent.py::test_status_conflict_during_pop_does_not_crash_loop` — UNCHANGED: the #1803 handler is not modified; confirm it still passes (regression guard that the new clause did not disturb the existing one).

No other existing tests are affected — the change adds one `except` clause and a bounded counter to a single loop; it does not alter any function signature, return contract, or the `StatusConflictError` path that existing tests cover.

## Rabbit Holes

- **Rewriting the whole pop/transition path to be corruption-proof at the model layer.** That is the `popoto-descriptor-pollution-audit` (#2083) plan's job. Here, stay at the loop's exception boundary.
- **Catching `BaseException`/`Exception` broadly at the pop site.** Do NOT replace the typed handlers with a blanket catch that also swallows `KeyboardInterrupt`/`CancelledError`/shutdown signals — that would break clean worker shutdown. Catch `ModelException` (and keep `StatusConflictError`) specifically; leave `BaseException` as the final re-raise for genuinely fatal cases.
- **Building a new corrupted-record deletion routine.** `cleanup_corrupted_agent_sessions()` already exists and already handles the index-vs-hash-key drift that broke hand-deletion during triage. Reuse it; do not reimplement.
- **Tuning the sweep cadence or startup reaper.** The ~5-min self-heal is a *mask*, not the fix; changing its timing is out of scope and would not close the loop-death hole.

## Risks

### Risk 1: The full-scan reaper is expensive to call on every corrupted pop
**Impact:** `cleanup_corrupted_agent_sessions()` walks `AgentSession.query.all()`. The handler calls it best-effort on **every** corrupted pop, so in the pathological *undeletable* case — where the same record survives the reaper and is re-popped every tick — those repeated full scans could add load and risk a hot spin.
**Mitigation:** The `CORRUPTED_POP_BACKOFF_SECONDS` backoff runs after every corrupted pop (after the slot is released), so an undeletable record cannot drive a tight back-to-back re-pop/re-scan spin. On the common path the reaper deletes the record and the next pop returns a healthy session, so the reaper is not re-invoked for that record. The reaper is offloaded via `offload_redis` so it never blocks the event loop, and the `worker_key`-keyed spin guard escalates to `logger.error` after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops — turning a stuck record into a bounded, alerting condition. This risk is deliberately accepted as small: corrupted records occur a handful of times per day, and the periodic session-health sweep (~5 min) is the authoritative backstop that will clear any record the opportunistic reaper cannot.

### Risk 2: `ModelException` is too broad and swallows a real transition bug
**Impact:** If a *non-corrupt* session hits a transient Popoto `ModelException` during `save()`, the loop would skip it and (via the best-effort reaper) potentially delete a recoverable record.
**Mitigation:** The reaper's own corruption checks (ID-length, no-op-save validation probe) gate deletion — it only deletes records whose no-op `save()` *re-fails* with an `"invalid"`/`"validation"` message, so a transient error on a healthy record is not deleted (the no-op save succeeds on retry). The handler skips the session this tick and re-pops it next tick; because a subsequent successful pop resets the spin guard, a one-off transient error never escalates. The handler logs every catch at `warning`, giving Sentry/log visibility if a healthy session is unexpectedly skipped. Scope the catch to `ModelException` only (not `Exception`), matching the confirmed failure type — a genuine logic bug still crashes loudly rather than being masked.

### Risk 4: A corrupted record survives the opportunistic reaper this tick
**Impact:** The reaper deletes only when ID-length is wrong or the no-op `save()` message contains `"invalid"`/`"validation"` (`session_health.py:4516-4539`). The *observed* message (`"Model instance parameters invalid. Failed to save."`) contains "invalid", so the observed record is deleted on the first opportunistic call. But a future `ModelException` raised for a different reason (unique-index violation, unknown field) might not match, so the record survives the tick and is re-popped.
**Mitigation:** This is explicitly **not** a blocker in the simplified design, because the handler never interprets whether the reaper deleted the record. It logs, backs off (`CORRUPTED_POP_BACKOFF_SECONDS`), escalates to `logger.error` after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops (naming the stuck worker_key for a human), and `continue`s — the loop survives regardless. The authoritative resolution of any record the opportunistic reaper cannot delete is the periodic session-health sweep, exactly as in production today. We deliberately do **not** broaden the reaper predicate (that would risk deleting healthy records) and deliberately do **not** branch handler logic on the reaper's return value (that return-value interpretation was the source of four failed critique rounds — see the Design decision in Solution).

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
- [ ] Comment the opportunistic reaper call: its return value is **deliberately ignored** — the reaper is best-effort head-of-queue cleanup, and the periodic session-health sweep is the authoritative backstop for anything it cannot delete this tick. Note that interpreting the reaper's return value was the source of four failed critique rounds (R1/R4) and was removed at the root.
- [ ] Comment the slot release placement: `release_unbound()` runs after the reaper call and **before** the backoff `asyncio.sleep`, so the sleep never holds the global concurrency slot (cite the `:1686-1697` invariant).
- [ ] Comment the spin-guard keying: it is a plain consecutive-corrupted-pop counter keyed by **`worker_key`, coarser by necessity** than #1803's `session_id` keying, because a `ModelException` carries no `session_id` and a fully-corrupted record may have none — do not "fix" it to session_id keying. Include the grain-of-salt/tunable note on the constants.

## Success Criteria

- [ ] A corrupted `AgentSession` (all fields `None` except `status="pending"`) popped by `_worker_loop` does **not** terminate the loop task.
- [ ] The corrupted record is logged and routed best-effort to `cleanup_corrupted_agent_sessions()`; co-tenant pending sessions for the same worker_key continue processing on the common path without waiting on the ~5-min sweep.
- [ ] The handler does **not** capture or branch on the reaper's return value; a reaper failure is swallowed (best-effort) and does not crash the loop. Permanent resolution of any record the reaper cannot delete this tick is left to the periodic session-health sweep, as in production today.
- [ ] A healthy session that hits a transient `ModelException` is retried (not deleted, not skipped forever): the reaper does not delete a record whose no-op save succeeds, the session is re-popped next tick, and a subsequent successful pop resets the spin guard so no escalation fires.
- [ ] The bounded spin guard is a plain consecutive-corrupted-pop counter keyed by `worker_key`: it applies `CORRUPTED_POP_BACKOFF_SECONDS` backoff after each corrupted pop and emits a one-shot `logger.error` after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops — with no dependence on any reaper return value.
- [ ] New regression test `test_model_exception_during_pop_does_not_crash_loop` reproduces the corrupted-record pop and asserts the loop survives and continues (mirrors the #1803 test); `test_repeated_corrupted_pop_escalates_without_crash` covers the bounded backoff + one-shot escalation path.
- [ ] No raw-Redis deletion is introduced; cleanup goes through the ORM reaper only; the reaper is **not** modified.
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
- Insert `except ModelException as e:` immediately after the existing `except StatusConflictError` clause (`:1701`) and before `except BaseException:` (`:1796`), in this exact order:
  1. **Log a warning** (worker_key + exception message; do NOT reference `session_id`).
  2. **Call the reaper best-effort, ignoring its return value** — `try: await offload_redis(cleanup_corrupted_agent_sessions)` / `except Exception as _exc: logger.warning("[worker:%s] reaper failed (session-health sweep remains the backstop): %s", worker_key, _exc)`. Mirror the #1803 wrap at `:1731-1743`. Do **not** capture or branch on the return value — this is the core simplification that removes the R1/R4 blocker class at the root.
  3. **Release the slot** — `if _slot_acquired: registry.release_unbound(); _slot_acquired = False` — BEFORE the backoff `await`, so `asyncio.sleep` never runs while the concurrency slot is held (matches the invariant at `:1686-1697` and the `StatusConflictError` clause). Place it after the reaper call so the reaper runs under the acquired slot.
  4. **Bounded spin guard + backoff:** `_corrupted_pop_count[worker_key] = _corrupted_pop_count.get(worker_key, 0) + 1`; if it `>= CORRUPTED_POP_ESCALATE_N` and `worker_key not in _corrupted_pop_escalated`, emit a one-shot `logger.error` naming the worker_key as stuck on a corrupted record the sweep has not cleared, then `_corrupted_pop_escalated.add(worker_key)`; then `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)`.
  5. `continue`.
- **Reset the guard on a successful pop:** at the successful-pop convergence point (`:1951-1953`, alongside `_conflict_counts.pop(session.session_id, None)`), add `_corrupted_pop_count.pop(worker_key, None)` and `_corrupted_pop_escalated.discard(worker_key)` (keyed by `worker_key`, not `session.session_id`) so a healthy pop clears any stale corrupted-pop count/escalation latch for this loop.
- Add the named constants `CORRUPTED_POP_ESCALATE_N` and `CORRUPTED_POP_BACKOFF_SECONDS` (grain-of-salt/tunable comments). Declare `_corrupted_pop_count: dict[str, int]` + `_corrupted_pop_escalated: set[str]` as **loop-local** state inside `_worker_loop`, alongside the #1803 `_conflict_counts` / `_conflict_escalated` / `_conflict_last_resort` at `:1675-1677` — **not** module-level, so a bridge-mode `_worker_loop` restart (queue-empty exit → respawn) resets them cleanly and cannot carry a stale escalation latch or leak per-worker_key entries. Keyed by `worker_key`. The existing reset block at `:1951-1953` already runs inside the same function body, so it needs no scope change. Never dereference `session_id` in this clause.
- Do NOT modify the `StatusConflictError` clause or its escalation logic, and do NOT modify the reaper in `session_health.py`.

### 2. Add the regression test
- **Task ID**: build-test
- **Depends On**: build-handler
- **Validates**: tests/unit/test_worker_persistent.py::test_model_exception_during_pop_does_not_crash_loop
- **Assigned To**: pop-handler-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Mirror `test_status_conflict_during_pop_does_not_crash_loop`: patch `_pop_agent_session` to raise `popoto.exceptions.ModelException("Model instance parameters invalid. Failed to save.")` on first call, return `None` after; assert `_worker_loop` returns without raising, pops ≥2×, and the worker is removed from `_active_workers`.
- Add `test_repeated_corrupted_pop_escalates_without_crash`: `_pop_agent_session` raises `ModelException` on every call, reaper mocked to a no-op; assert bounded backoff between pops, a single `logger.error` escalation after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops, and loop survival. Do not assert anything about the reaper's return value (the handler ignores it).
- Add a case asserting a reaper failure (mock raising `Exception`) does not re-crash the loop, and a corrupted record with `session_id=None` does not raise `KeyError`.
- Add a guard-reset assertion: `ModelException` once then a healthy session pop clears the guard counter and fires no escalation.

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
| Reaper return value NOT interpreted | `grep -n "cleanup_corrupted_agent_sessions" agent/agent_session_queue.py` | manual review — the new clause must call the reaper but must NOT assign/branch on its `{"corrupted", ...}` return |
| Backoff/escalation constants present | `grep -c "CORRUPTED_POP_ESCALATE_N\|CORRUPTED_POP_BACKOFF_SECONDS" agent/agent_session_queue.py` | output > 0 |
| New regression test exists | `grep -c "test_model_exception_during_pop_does_not_crash_loop" tests/unit/test_worker_persistent.py` | output > 0 |
| Escalation test exists | `grep -c "test_repeated_corrupted_pop_escalates_without_crash" tests/unit/test_worker_persistent.py` | output > 0 |
| #1803 handler untouched | `grep -c "except StatusConflictError" agent/agent_session_queue.py` | output > 0 |
| No raw-Redis delete added | `grep -nE "\.(delete|srem|zrem)\(" agent/agent_session_queue.py \| grep -iv "release_unbound\|registry"` | (manual review — anti-criterion: no new raw-Redis ops on Popoto keys) |
| Targeted tests pass | `pytest tests/unit/test_worker_persistent.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | war-room | Broad `except ModelException` mismatched to the reaper's narrow delete predicate — records the reaper can't classify re-pop every tick; a healthy session on a transient `ModelException` is skipped forever. | **Superseded by the 4th-critique simplification (see final row).** | Originally addressed by a "verify-then-trust" delta branch. That branch was the churn source and was **removed** in the 4th revision: the handler no longer interprets the reaper's return value at all. The concern is now met differently — the loop always survives (log + best-effort reap + backoff + continue), and the periodic session-health sweep is the authoritative resolver for records the opportunistic reaper cannot delete. The healthy-transient case is retried (reaper deletes nothing; successful pop resets the guard). |
| CONCERN | war-room | Catch type never confirmed from the 3 live crash tracebacks. | Problem ("Confirmed escaping exception"); Freshness Check Notes. | Read from Sentry VALOR-E5 (5 events, not 3): class `popoto.exceptions.ModelException`, message `"Model instance parameters invalid. Failed to save."`, raised by `pre_save()` on `is_valid()==False`. Full traceback chain cited. |
| CONCERN | war-room | Root-cause diagnosis ("each handler catches one type") contradicts adding the Nth typed clause; consider `except Exception` altitude. | Why Previous Fixes Failed → "Reconciliation of root cause vs. chosen catch altitude". | `except Exception` explicitly weighed and rejected (masks logic bugs, re-introduces heal-a-healthy-record risk, violates Rabbit Hole). Chosen `ModelException` is the **base class** of the Popoto save/transition family, so one clause closes the whole class — the durable fix the diagnosis calls for, scoped to data failures. |
| CONCERN | war-room | Spin guard keyed by worker_key can't identify the poison record; no signal for an undeletable record. | Solution → Key Elements; Technical Approach step 4; Documentation (inline). | The handler logs every corrupted pop at `warning`, and after `CORRUPTED_POP_ESCALATE_N` **consecutive** corrupted pops for a worker_key it emits a one-shot `logger.error` naming the stuck worker_key — the operator signal that a record is stuck at the queue head. In the simplified design this escalation is count-based (no reaper-return dependence). |
| NIT | war-room | worker_key keying is coarser-by-necessity vs #1803's session_id keying — document it. | Solution → Key Elements ("keying caveat"); Documentation → Inline (spin-guard keying comment). | Documented that `ModelException` carries no `session_id` (unlike the spike-4-augmented `StatusConflictError`), so worker_key keying is required, not a downgrade; inline comment warns against "fixing" it to session_id. |
| BLOCKER (re-critique) | war-room | Zero-delta branch held the concurrency slot across the backoff `asyncio.sleep` — release was ordered after the sleep, violating the release-before-await invariant (`:1686-1697`). | Flow; Technical Approach step 3; Task 1; Documentation → Inline. | `release_unbound()` runs after the reaper call and **before** the backoff `asyncio.sleep`, so the sleep never runs while the slot is held. Preserved verbatim in the simplified design. |
| CONCERN (re-critique) | war-room | Spin-guard counter never reset on a successful pop — a stale count could inherit across unrelated corrupted pops. | Technical Approach ("Reset the spin guard on any successful pop"); Task 1 (reset at `:1951-1953`). | Added `_corrupted_pop_count.pop(worker_key, None)` + `_corrupted_pop_escalated.discard(worker_key)` at the successful-pop convergence point (`:1951-1953`), alongside the existing `_conflict_counts` pruning, keyed by `worker_key`. Preserved in the simplified design. |
| CONCERN (re-critique) | war-room | Risk 1 wording didn't match the call-every-catch + sleep-backoff implementation. | Risk 1 (rewritten). | Rewrote Risk 1: the reaper runs best-effort on every catch and the `asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)` backoff (after slot release) bounds the pathological undeletable case. |
| BLOCKER (3rd critique) | war-room | Reaper call never wrapped in try/except — a reaper failure raised inside the new `except ModelException` clause is not caught by the sibling `except BaseException: raise`, so it propagates and re-kills the loop task. | Technical Approach step 2; Flow; Task 1. | Wrapped the opportunistic `offload_redis(cleanup_corrupted_agent_sessions)` in `try/except Exception` (mirrors the #1803 wrap at `:1731-1743`); on failure it logs and degrades to a no-op, so a reaper failure never propagates. |
| BLOCKER (3rd critique) | war-room | Spin-guard state specified module-level while claiming to mirror the loop-local `_conflict_counts` (`:1675-1677`). Module-level state survives a bridge-mode `_worker_loop` restart, so a stale escalation latch suppresses the one-shot `logger.error` for a new incident and leaks entries. | Technical Approach ("Bounded, worker_key-keyed spin guard"); Task 1. | Specified `_corrupted_pop_count` / `_corrupted_pop_escalated` as loop-local, declared alongside `_conflict_counts` at `:1675-1677`. Preserved in the simplified design. |
| BLOCKER (4th critique) | war-room | The delta-check machinery misclassified a self-healed phantom as an undeletable poison record: `cleanup_corrupted_agent_sessions()` runs records through `_filter_hydrated_sessions()` (`session_health.py:4508`) which **silently drops** phantom/orphan-index records before the `cleaned` counter increments, and phantoms are cleared via a **separate** `repair_indexes()`/`phantoms_cleared` path never folded into the returned `corrupted` count. The Problem's motivating record ("all fields None except status=pending") matches the phantom shape → a genuinely self-healed phantom reports `corrupted: 0` → spurious "undeletable poison" escalation. | **Design pivot: simplified the escalation machinery away** — Solution → "Design decision"; Flow; Technical Approach step 2; Risk 4; Success Criteria; Task 1. Every one of R1–R4 was a defect in *interpreting the reaper's return value*. That interpretation is deleted at the root. | The handler now calls the reaper **best-effort and ignores its return value** — it never inspects `corrupted`, so the phantom-vs-`cleaned` accounting mismatch cannot misclassify anything. The loop survives via log + best-effort reap + backoff + `continue`; the periodic session-health sweep (the production backstop today) owns permanent resolution of any record the opportunistic reaper cannot delete. The spin guard is a plain consecutive-corrupted-pop counter with no reaper dependence. This removes the whole R1/R4 blocker class while still satisfying all four acceptance criteria (loop survives, session logged + routed to ORM cleanup, co-tenants continue, no raw-Redis deletion). |

---

## Open Questions

1. **Secondary/fallback pop-site hardening.** The observed bug is at the primary pop (`:1700`). The secondary/fallback pops (`:1860`, `:1881`, `:1898`, exit-time ~`:1920`) have bare `except BaseException: raise` and catch neither `StatusConflictError` nor `ModelException`, but are unreachable in the corrupted-record scenario (a `pending` record keeps `_has_pending` truthy, so the loop `continue`s before reaching them). Should this plan **also** extract a shared guarded-pop helper so every pop site survives single-session failures (closes the *class* for good, slightly larger diff), or fix only the primary site (minimal, closes the observed + reasoned bug)? Recommendation: shared helper if appetite allows; primary-only otherwise.
2. **Reaper invocation cadence.** *(Resolved during revision — simplified.)* The handler invokes `cleanup_corrupted_agent_sessions()` best-effort on each corrupted pop but **does not interpret its return value**. On the common path the reaper clears the head-of-queue record and the next pop returns a healthy co-tenant session; the per-pop `CORRUPTED_POP_BACKOFF_SECONDS` backoff bounds full-scan cost in the pathological undeletable case, so a separate "reap every K pops" throttle is unnecessary. Any record the opportunistic reaper cannot delete is resolved by the periodic session-health sweep (~5 min), the same production backstop that self-heals these records today; the `logger.error` escalation (after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops) surfaces a stuck record to an operator. Branching on the reaper's `{"corrupted", ...}` delta was tried across four critique rounds and removed as the root cause of the churn.
