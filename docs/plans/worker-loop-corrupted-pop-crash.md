---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2088
last_comment_id:
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
that worker_key's entire loop task. Observed **3 times today** at worker
restarts. Each time the loop self-healed within ~5 minutes — but via a *separate*
mechanism (the periodic session-health sweep), **not** via the pop-path exception
handling. The crash still fires every time a corrupted pending record is popped;
it only gets cleaned up after the fact.

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

**Notes:** Bug still reproducible-by-reasoning against current main: `save()` on a record missing required fields raises a Popoto `ModelException` (base of `KeyMutationError`/`SkipSaveException`), which is a direct subclass of `Exception` — *not* of `StatusConflictError` — so it bypasses `:1701` and hits the `:1796` re-raise. Confirmed the mechanism is intact.

## Prior Art

- **Issue/PR #1803**: "Worker loop crashes on StatusConflictError when a queued session is killed mid-pop" — CLOSED 2026-06-26. Added the `except StatusConflictError` skip-and-continue handler + bounded escalation at the primary pop site. **This is the direct predecessor**; the current fix is the same shape, one exception type wider. The regression test `tests/unit/test_worker_persistent.py::test_status_conflict_during_pop_does_not_crash_loop` is the template for this issue's test.
- No other closed issues / merged PRs matched "worker loop ModelException corrupted pop" — this is the second known instance of the class, not a repeat of a failed fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR for #1803 | Added `except StatusConflictError` at the primary pop site (`:1701`) with bounded escalation, so a session killed mid-pop is skipped instead of crashing the loop. | It caught exactly **one** exception type. The catch-all beneath it (`except BaseException: raise`, `:1796`) still re-raises every other single-session failure — including the Popoto `ModelException` a corrupted record throws during `transition_status→save()`. The #1803 fix narrowed the hole; it did not close the *class* (any uncaught single-session exception kills the loop). |

**Root cause pattern:** The pop path enumerates the *specific* exceptions it tolerates and re-raises everything else. Because "everything else" includes single-session data-corruption failures that are just as non-fatal as a mid-pop kill, each newly-observed corruption mode reopens the same loop-death hole. The durable fix is to make the pop-path handler treat *any single-session pop/transition failure* as skip-and-continue, so the loop's survival no longer depends on having pre-enumerated the exception type.

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

- **Broadened pop-path handler** — the primary pop site in `_worker_loop` treats a Popoto `ModelException` (corrupted/unsaveable single session) the same way it already treats `StatusConflictError`: log, clean up, skip, continue. The loop never dies for a single bad record.
- **ORM-only corrupted-record routing** — on catching a corrupted-record failure, route it to the existing `cleanup_corrupted_agent_sessions()` reaper (which already handles Popoto index-vs-hash-key drift via `_delete_with_stale_key_lookup`) rather than hand-deleting Redis keys. No raw-Redis deletion is introduced.
- **Bounded, session_id-free spin guard** — because a corrupted record may have no usable `session_id`, the handler must not reuse the `StatusConflictError`-keyed `_conflict_counts` map blindly. It uses a small, bounded corrupted-pop counter (throttling how often the full-scan reaper is invoked and yielding to avoid a hot re-pop spin) that degrades safely when `session_id` is absent.

### Flow

Worker startup → primary pop returns a corrupted record → `transition_status→save()` raises `ModelException` → **handler catches it** → log warning + invoke `cleanup_corrupted_agent_sessions()` (bounded) + `release_unbound()` the slot → `continue` → next pop returns a healthy co-tenant session → loop keeps running (no ~5-min sweep wait).

### Technical Approach

- **Catch `ModelException` alongside `StatusConflictError`** at the primary pop site (`agent/agent_session_queue.py:1701`). Add `from popoto.exceptions import ModelException` (verified: `ModelException` is the base of `KeyMutationError` and `SkipSaveException`, the save/validation failures a corrupted record raises; `QueryException` is *not* a subclass, and the issue confirmed the escaping type is `ModelException`, so this catch is correctly scoped). Implement as a distinct `except ModelException as e:` clause immediately after the `except StatusConflictError` clause (so the existing #1803 escalation logic is untouched), *before* the `except BaseException` re-raise.
- **In the new clause:** `logger.warning(...)` identifying the corrupted pop; call `await offload_redis(cleanup_corrupted_agent_sessions)` (ORM-only reaper, already offloaded elsewhere) to delete the record so it is not re-popped at the head of the queue; `registry.release_unbound()` if `_slot_acquired`; `continue`. This mirrors the `StatusConflictError` clause's slot-release-and-continue tail exactly.
- **Bounded spin guard:** maintain a per-loop `_corrupted_pop_count` (module-level dict keyed by worker_key, or a simple local counter, resolved in build). Invoke the full-scan reaper at most once per K corrupted pops (K a named constant, e.g. `CORRUPTED_POP_REAP_EVERY_N`, grain-of-salt/tunable per repo convention) and `await asyncio.sleep(small_backoff)` if corrupted pops repeat, so a record the reaper cannot delete (returns False) cannot spin the loop hot. Because there is no reliable `session_id` on a corrupted record, the guard is keyed by worker_key, not session_id — no `KeyError`, no infinite spin.
- **Secondary/fallback pop sites** (`:1860`, `:1881`, `:1898`, and the exit-time fallback ~`:1920`) currently have bare `except BaseException: raise` and catch *neither* `StatusConflictError` nor `ModelException`. In the observed scenario they are unreachable (a corrupted `status="pending"` record makes the `_has_pending` idle-check truthy, so control `continue`s at the top and never reaches these branches). Whether to harden them too is the one real scope decision — see Open Questions. Baseline plan: fix the primary site (closes the observed + reasoned bug); recommended stretch: extract a shared guarded-pop helper so all pop sites share one skip-and-continue path (prevents the *next* instance of this class). Decision deferred to critique/PM.
- **No Popoto model schema change** — this edits control flow only; no migration required.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `except ModelException` block is the unit under test: assert observable behavior — a `logger.warning` fires AND the loop continues (`_pop_agent_session` called ≥2×) AND the worker is cleaned up from `_active_workers`. No silent swallow: the handler logs and routes to cleanup.
- [ ] Assert `cleanup_corrupted_agent_sessions` is invoked (mock it) when a corrupted-record `ModelException` is caught, and that a reaper failure (mock raising) does not re-crash the loop.

### Empty/Invalid Input Handling
- [ ] Corrupted record with `session_id = None` / all-`None` fields is the invalid input under test — assert the bounded spin guard keys off worker_key and does not raise `KeyError` or `AttributeError` when `session_id` is absent.
- [ ] Assert the loop does not hot-spin: with `_pop_agent_session` raising `ModelException` repeatedly then returning `None`, the loop terminates on shutdown without exceeding a bounded reaper-invocation count.

### Error State Rendering
- [ ] No user-visible surface (internal worker loop). The observable "error state" is the log line + metric; assert the warning is emitted. State: no Telegram/UI rendering path in scope.

## Test Impact

- [ ] `tests/unit/test_worker_persistent.py` — UPDATE (additive): add `test_model_exception_during_pop_does_not_crash_loop`, mirroring the existing `test_status_conflict_during_pop_does_not_crash_loop` (patch `_pop_agent_session` to raise `popoto.exceptions.ModelException` on first call, return `None` after; assert loop survives, pops ≥2×, worker de-registered). No existing test in this file changes behavior.
- [ ] `tests/unit/test_worker_persistent.py::test_status_conflict_during_pop_does_not_crash_loop` — UNCHANGED: the #1803 handler is not modified; confirm it still passes (regression guard that the new clause did not disturb the existing one).

No other existing tests are affected — the change adds one `except` clause and a bounded counter to a single loop; it does not alter any function signature, return contract, or the `StatusConflictError` path that existing tests cover.

## Rabbit Holes

- **Rewriting the whole pop/transition path to be corruption-proof at the model layer.** That is the `popoto-descriptor-pollution-audit` (#2083) plan's job. Here, stay at the loop's exception boundary.
- **Catching `BaseException`/`Exception` broadly at the pop site.** Do NOT replace the typed handlers with a blanket catch that also swallows `KeyboardInterrupt`/`CancelledError`/shutdown signals — that would break clean worker shutdown. Catch `ModelException` (and keep `StatusConflictError`) specifically; leave `BaseException` as the final re-raise for genuinely fatal cases.
- **Building a new corrupted-record deletion routine.** `cleanup_corrupted_agent_sessions()` already exists and already handles the index-vs-hash-key drift that broke hand-deletion during triage. Reuse it; do not reimplement.
- **Tuning the sweep cadence or startup reaper.** The ~5-min self-heal is a *mask*, not the fix; changing its timing is out of scope and would not close the loop-death hole.

## Risks

### Risk 1: The full-scan reaper is expensive to call on every corrupted pop
**Impact:** `cleanup_corrupted_agent_sessions()` walks `AgentSession.query.all()`. Calling it on every corrupted pop in a tight loop could add load, and if the record cannot be deleted (reaper returns False), the same record is re-popped, risking a hot spin.
**Mitigation:** Bounded spin guard — invoke the reaper at most once per K corrupted pops per worker_key and `await asyncio.sleep(small_backoff)` between repeats. Offload the reaper via `offload_redis` so it never blocks the event loop.

### Risk 2: `ModelException` is too broad and swallows a real transition bug
**Impact:** If a *non-corrupt* session hits a transient Popoto `ModelException` during `save()`, the loop would skip it and (via the reaper) potentially delete a recoverable record.
**Mitigation:** The reaper's own corruption checks (ID-length, no-op-save validation probe) gate deletion — it only deletes records that genuinely fail validation, so a transient error on a healthy record is not deleted (the no-op save succeeds on retry). The handler logs every catch at `warning`, giving Sentry/log visibility if a healthy session is unexpectedly skipped. Scope the catch to `ModelException` only (not `Exception`), matching the confirmed failure type.

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
- [ ] Comment the new `except ModelException` clause explaining it is the #2088 sibling of the #1803 `StatusConflictError` handler, and why the catch is scoped to `ModelException` (single-session corruption, not fatal signals).
- [ ] Comment the bounded spin guard constant (grain-of-salt/tunable per repo convention).

## Success Criteria

- [ ] A corrupted `AgentSession` (all fields `None` except `status="pending"`) popped by `_worker_loop` does **not** terminate the loop task.
- [ ] The corrupted record is logged and routed to `cleanup_corrupted_agent_sessions()`; co-tenant pending sessions for the same worker_key continue processing without waiting on the ~5-min sweep.
- [ ] New regression test `test_model_exception_during_pop_does_not_crash_loop` reproduces the corrupted-record pop and asserts the loop survives and continues (mirrors the #1803 test).
- [ ] No raw-Redis deletion is introduced; cleanup goes through the ORM reaper only.
- [ ] The bounded spin guard degrades safely when the corrupted record has no `session_id` (no `KeyError`, no hot spin).
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
- Insert `except ModelException as e:` immediately after the existing `except StatusConflictError` clause (`:1701`) and before `except BaseException:` (`:1796`): log a warning, invoke `await offload_redis(cleanup_corrupted_agent_sessions)`, `release_unbound()` the slot if acquired, `continue`.
- Add a bounded spin guard (named constant, e.g. `CORRUPTED_POP_REAP_EVERY_N`, with a grain-of-salt/tunable comment) keyed by worker_key; throttle reaper invocation and add a small `asyncio.sleep` backoff on repeat corrupted pops. Ensure safety when `session_id` is `None`.
- Do NOT modify the `StatusConflictError` clause or its escalation logic.

### 2. Add the regression test
- **Task ID**: build-test
- **Depends On**: build-handler
- **Validates**: tests/unit/test_worker_persistent.py::test_model_exception_during_pop_does_not_crash_loop
- **Assigned To**: pop-handler-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Mirror `test_status_conflict_during_pop_does_not_crash_loop`: patch `_pop_agent_session` to raise `popoto.exceptions.ModelException` on first call, return `None` after; assert `_worker_loop` returns without raising, pops ≥2×, and the worker is removed from `_active_workers`.
- Add a case asserting a reaper failure (mock raising) does not re-crash the loop, and a corrupted record with `session_id=None` does not raise `KeyError`.

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
| New regression test exists | `grep -c "test_model_exception_during_pop_does_not_crash_loop" tests/unit/test_worker_persistent.py` | output > 0 |
| #1803 handler untouched | `grep -c "except StatusConflictError" agent/agent_session_queue.py` | output > 0 |
| No raw-Redis delete added | `grep -nE "\.(delete|srem|zrem)\(" agent/agent_session_queue.py \| grep -iv "release_unbound\|registry"` | (manual review — anti-criterion: no new raw-Redis ops on Popoto keys) |
| Targeted tests pass | `pytest tests/unit/test_worker_persistent.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_worker_persistent.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Secondary/fallback pop-site hardening.** The observed bug is at the primary pop (`:1700`). The secondary/fallback pops (`:1860`, `:1881`, `:1898`, exit-time ~`:1920`) have bare `except BaseException: raise` and catch neither `StatusConflictError` nor `ModelException`, but are unreachable in the corrupted-record scenario (a `pending` record keeps `_has_pending` truthy, so the loop `continue`s before reaching them). Should this plan **also** extract a shared guarded-pop helper so every pop site survives single-session failures (closes the *class* for good, slightly larger diff), or fix only the primary site (minimal, closes the observed + reasoned bug)? Recommendation: shared helper if appetite allows; primary-only otherwise.
2. **Reaper invocation cadence.** Is invoking the full-scan `cleanup_corrupted_agent_sessions()` inline (bounded per K pops) acceptable, or should the handler instead just skip + `continue` and rely entirely on the existing startup + periodic sweep to delete the record (accepting that the same corrupted record is re-popped-and-skipped each tick until the sweep runs)? Recommendation: bounded inline reaper — it removes the head-of-queue record promptly so co-tenants are not blocked, which is the whole point of the fix.
