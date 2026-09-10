---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3253
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-10T02:33:24Z
---

# Worker loop dies on terminal-status conflict in the session-completion `finally`

## Problem

`_worker_loop`'s per-session completion `finally` in `agent/agent_session_queue.py`
(`:2957`–`:3028`) can raise `StatusConflictError` **out of `_worker_loop` entirely**.
When it does, the worker task for that `worker_key` dies and every session queued for
that key — plus every session enqueued afterwards — is stranded until the process
restarts or the ~5-minute session-health sweep notices.

Two independent defects combine.

**Defect 1 — there is no already-terminal skip.** The completion block's only two skip
conditions are `not fresh` (`:3002`) and `fresh.status == "pending"` (`:3010`). If the
authoritative row is *already terminal* — written by `_execute_agent_session`'s own
finalize guard, by `complete_transcript`, or out-of-band by the health checker — control
falls straight through to `:3019`:

```python
await _complete_agent_session(session, failed=session_failed)
```

`_complete_agent_session` (`agent/session_completion.py:161-162`) ends in
`finalize_session(session, "failed" | "completed", ...)` with no
`reject_from_terminal=False`. `finalize_session` (`models/session_lifecycle.py:555-564`)
raises `StatusConflictError` when the row is already terminal and the caller asks for a
*different* terminal status — the kill-is-terminal invariant, documented at
`models/session_lifecycle.py:311-318`. `TERMINAL_STATUSES` (`:67`) is
`{completed, failed, killed, abandoned, cancelled}`.

**Defect 2 — the fallback at `:3028` is a bare retry of the call that just raised.** The
`try` opened at `:3000` was written to protect the nudge-guard *read* at `:3001`
(`AgentSession.query.get`), but its body also spans the `_complete_agent_session` write in
the `else` branch at `:3019`. So a **write** conflict is caught by
`except Exception as guard_err:` (`:3020`), logged as
`"Nudge guard check failed … — completing session as fallback"`, and then the identical
call is re-issued at `:3028` **outside any try**. The state that made the first call raise
is unchanged, so the second call raises the same `StatusConflictError`.

**The escape path.** That second raise leaves the inner `finally`. Per Python semantics an
exception escaping a `finally` replaces whatever was propagating and keeps unwinding
([docs.python.org](https://docs.python.org/3/library/asyncio-exceptions.html)). It passes
the `while True`, reaches `:3051 except asyncio.CancelledError` — which does not match,
because `StatusConflictError` is an ordinary `Exception` — runs the outer `finally` at
`:3053-3055`, which pops `worker_key` from `_active_workers` and `_active_events`, and
then propagates out of the coroutine. Nothing re-creates the worker for that key.

**Why it is silent.** `_ensure_worker` (`:1870-1871`) attaches no `add_done_callback`, and
the outer `finally` pops the task from `_active_workers` — dropping the only strong
reference. asyncio surfaces a task exception only when someone retrieves it, so the death
produces no traceback at all: the entire operator-visible signal is the two
`logger.warning` lines from `:3021`. This is the "dies silently" in the issue title, and it
is why the class survived two prior site-local fixes without anyone seeing a stack.

**Reachable trigger (most likely, and getting more likely).** `_execute_agent_session`
writes a terminal status on its own exit path and then something after that write raises.
`finalized_by_execute` (`:2544`) is only set at `:2748`, `:2767`, `:2832` — all on
non-exceptional or already-handled exits — so an exception raised after the executor's own
terminal write leaves `finalized_by_execute = False`, and `:2937` sets
`session_failed = True`. The completion `finally` then runs, reads a terminal `completed`
row, and tries to write `failed`. Conflict → retry → worker death.

PR #3248 (open, `session/executor-exit-finalize-guard-3209`) **hoists the executor's
finalize guard into its `finally`**, which structurally widens the window in which the
executor writes terminal and then propagates. No file overlap with this fix, but the
adjacency means shipping #3248 without this fix increases exposure.

Health-checker terminals (`killed`, `abandoned`) written out-of-band during execution reach
the same collision from the other direction.

## Freshness Check

**Baseline:** `origin/main` @ `a15c5eab7` (2026-09-10), fetched at plan time. Issue filed
2026-09-08T05:05:39Z; recon written 2026-09-09 against `d441f972e`.

**Disposition: Minor drift.** Every claim still holds; one cited file moved by
one line and one adjacent comment was added.

| Claim | Re-verified | Result |
|---|---|---|
| `agent_session_queue.py:3000` opens the nudge-guard `try` | yes | exact |
| `:3001` `AgentSession.query.get(redis_key=…)` read | yes | exact |
| `:3002` `not fresh` skip / `:3010` `fresh.status == "pending"` skip | yes | exact; still the only two skips |
| `:3019` `await _complete_agent_session(...)` inside the same `try` | yes | exact |
| `:3020` `except Exception as guard_err:` / `:3028` unguarded retry | yes | exact |
| `session_completion.py:160-162` terminal write with no `reject_from_terminal=False` | yes | drifted by one line — now `:161` (`status = …`) / `:162` (`finalize_session(...)`) |
| `session_lifecycle.py:311-318` kill-is-terminal docstring | yes | exact |
| `session_lifecycle.py:67` `TERMINAL_STATUSES` | yes | exact |
| Escape path: `:3051` `except asyncio.CancelledError` does not catch; `:3053-3055` pops the key | yes | exact |

**Commits touching the relevant files since the issue was filed:** exactly one —
`191bd42a1` ("Stop the unprompted Telegram repeat replies…", #3273). It touched only
`models/session_lifecycle.py`, adding the `#3270` live-fence WARNING inside
`finalize_session`'s **idempotency early-return** (`:488-542`). That branch fires only when
`current_status == status` (same terminal → same terminal), which returns before the
`reject_from_terminal` guard at `:555`. It therefore does **not** intercept this bug's
path (terminal → *different* terminal). The commit's own comment says so verbatim at
`models/session_lifecycle.py:513-514`: *"Observability ONLY. The idempotency semantics are
unchanged; that is #3253's territory."* This is a marker pointing at this issue, not a
partial fix of it. It is the source of the one-line drift in `session_completion.py`.

**Cited issues/PRs re-checked:** #1803 CLOSED, #2088 CLOSED (both prior members of this
family, both fixed at the pop site only). #3270 CLOSED. **#3248 still OPEN** on branch
`session/executor-exit-finalize-guard-3209`, touching `agent/session_executor.py` only —
no file overlap, behavioural adjacency as described under Problem.

**Bug still present:** confirmed by code read rather than live reproduction — the trigger
requires a terminal write landing between the executor's exit and the worker's `finally`,
which is a production timing precondition. The three structural facts that constitute the
defect (no terminal skip; the write inside the read's `try`; the bare retry at `:3028`) are
all present on the baseline SHA and are directly readable above.

**Plan overlap:** `ls -lt docs/plans/` shows no active plan touching
`agent/agent_session_queue.py`'s worker loop. The nearest neighbours —
`telegram-reply-duplication-and-room-register.md` (#3273, merged) and
`router-plan-stage-standdown-sweep.md` (SDLC router) — are in different subsystems. No
coordination required beyond the #3248 note.

## Prior Art

This is the **third** member of one family: *an exception raised while handling a single
session escapes `_worker_loop` and strands the whole `worker_key`.* Both prior fixes landed
at the **pop** site and neither swept the rest of the loop.

| Issue | Exception | Site | Fix shape |
|---|---|---|---|
| #1803 (CLOSED) | `StatusConflictError` — session killed between the pop's `status=pending` read and its `transition_status(→running)` | `_pop_agent_session` call, handler at `:2150` | typed `except`, log, bounded per-`session_id` escalation (`:2046-2245`), release slot, `continue` |
| #2088 (CLOSED) | Popoto `ModelException` on a fully-corrupted record at the same transition | same pop call, handler at `:2245` | typed `except`, log, best-effort reap with cooldown, per-`worker_key` spin guard, backoff, `continue` |
| **#3253 (this)** | `StatusConflictError` on a terminal→terminal write | completion `finally`, `:3019` / `:3028` | **unfixed** — no typed handler at all |

Both prior handlers are documented under **"Pop-Loop Exception Resilience"** in
`docs/features/agent-session-queue.md:132-150`. That section's own framing is the reason
this site was missed: it scopes the invariant to "the primary pop site
(`_pop_agent_session()`, which drives `pending → running`)" rather than to the worker loop
as a whole. The invariant it states — *"No exception raised while popping/transitioning a
**single** session may terminate the whole `_worker_loop` task"* — is correct and already
covers this site in spirit; only its stated scope is too narrow.

`docs/features/session-lifecycle.md:143-152` carries the complementary convention: the
kill-is-terminal `StatusConflictError` is the *expected, correct, defense-in-depth outcome*
of a concurrent writer, and every legitimate caller wraps `finalize_session()` in
`try/except StatusConflictError: logger.info(...)`. Six call sites are listed there
(`session_completion.py`, `session_executor.py`, `session_health.py`,
`session_transcript.py`, `telegram_bridge.py`, `_transition_parent`). The worker's
completion `finally` is a seventh such caller and is **absent from that list** — this fix
adds it.

**Searches run:** `gh issue list --state closed --search "StatusConflictError worker loop"`,
`gh pr list --state merged --search "worker loop StatusConflictError"`, plus
`grep -rn StatusConflictError agent/ models/ worker/ tests/` (48 hits across 6 modules).
No prior attempt has ever touched `:3019`/`:3028`.

## Research

**Skip rationale considered and rejected:** the work is internal, but one language
semantic is load-bearing for the escape-path claim, so one search was run.

**Query:** "Python exception raised inside finally block replaces original exception
asyncio task dies silently".

**Findings that inform the approach:**

1. **An exception escaping a `finally` replaces whatever was propagating and keeps
   unwinding.** ([Python asyncio exceptions docs](https://docs.python.org/3/library/asyncio-exceptions.html))
   — confirms the `:3028` raise leaves the inner `finally` and is not contained by the
   surrounding session-handling structure. *Informs:* the fix must guarantee the completion
   block cannot raise at all, rather than relying on any outer handler catching it.

2. **An asyncio task's exception is surfaced only when someone retrieves it** (`await`,
   `.result()`, `.exception()`, or a done-callback); otherwise it appears at best as
   "Task exception was never retrieved" on garbage collection.
   ([SuperFastPython](https://superfastpython.com/asyncio-task-exceptions/),
   [cpython#97827](https://github.com/python/cpython/issues/97827)) — `_ensure_worker`
   (`:1870-1871`) registers no done-callback and `:3054` drops the last reference, so this
   worker death is genuinely traceback-free. *Informs:* the two `logger.warning` lines are
   the only evidence a fielded incident would leave, which is why the fix's log lines must
   name the worker, the session, and both statuses.

3. **`except Exception` is the standard cancellation-safety trap; `CancelledError` was
   moved to `BaseException` precisely so broad handlers stop swallowing it.**
   ([cpython PR 13528](https://github.com/python/cpython/pull/13528)) — the inverse applies
   here: `:3051`'s `except asyncio.CancelledError` is correctly narrow, which is exactly why
   it cannot contain this `Exception`. *Informs:* the new handler is typed
   (`except StatusConflictError` first, then `except Exception`), never `except BaseException`
   — fatal signals must keep crashing loudly, matching the `ModelException` handler's stated
   design at `docs/features/agent-session-queue.md:148-150`.

Sources: [Python asyncio exceptions](https://docs.python.org/3/library/asyncio-exceptions.html),
[SuperFastPython — asyncio task exceptions](https://superfastpython.com/asyncio-task-exceptions/),
[cpython#97827](https://github.com/python/cpython/issues/97827),
[cpython PR 13528](https://github.com/python/cpython/pull/13528).

## Data Flow

The write that conflicts, end to end. Line numbers are `origin/main` @ `a15c5eab7`.

```
_ensure_worker(worker_key)                                   agent_session_queue.py:1870
  └─ asyncio.create_task(_worker_loop(...))  ← no add_done_callback
     _active_workers[worker_key] = task      ← the only strong reference    :1871

_worker_loop  (while True)                                                  :2107
  ├─ session = await _pop_agent_session(...)                                :2149
  │    └─ except StatusConflictError / ModelException → log, continue  [GUARDED #1803/#2088]
  │
  ├─ try:  exec_task = create_task(_execute_agent_session(session))
  │    ├─ await exec_task                                                   :2766
  │    │    └─ session_executor writes a TERMINAL status on its exit path
  │    │       (executor finalize guard / complete_transcript), then may raise
  │    ├─ finalized_by_execute = True   ← ONLY on non-exceptional return    :2767
  │    └─ except Exception:  session_failed = True                          :2937
  │
  └─ finally:                                                              :2938
       if not session_completed and not finalized_by_execute:              :2957
         try:                                                              :3000
           fresh = AgentSession.query.get(redis_key=session.db_key.redis_key)  :3001
           if not fresh:            → skip                                 :3002
           elif fresh.status == "pending":  → skip (nudge)                 :3010
           else:                    ← TERMINAL LANDS HERE. No third skip.  :3018
             await _complete_agent_session(session, failed=session_failed)  :3019
                └─ rows_for_session_id(session_id) → prefer "running", else newest
                   session_completion.py:161-162
                     finalize_session(row, "failed"|"completed")
                       session_lifecycle.py:472  idempotent same→same? → return
                       session_lifecycle.py:555  reject_from_terminal and
                                                 current in TERMINAL_STATUSES
                                                 → raise StatusConflictError  ◀── RAISE 1
                       session_lifecycle.py:576  CAS on-disk != in-memory
                                                 → raise StatusConflictError  ◀── RAISE 1'
         except Exception as guard_err:                                    :3020
           logger.warning("… — completing session as fallback")            :3021
           await _complete_agent_session(session, failed=session_failed)   :3028
              └─ identical call, identical state → StatusConflictError      ◀── RAISE 2
                 UNCAUGHT

           ↓ escapes the inner finally, replacing nothing (nothing else pending)
           ↓ past `while True`
           ↓ :3051 except asyncio.CancelledError  → NO MATCH
           ↓ :3053 finally: _active_workers.pop(worker_key)                :3054
           ↓        _active_events.pop(worker_key)                         :3055
           ↓ out of the coroutine → task dies, exception never retrieved
           ✗ worker_key has no worker. Every queued and future session for it strands.
```

**Two distinct raise sites inside one call.** `finalize_session` can raise from the
`reject_from_terminal` guard (`:555`, reading the *caller's* object) **or** from the CAS
re-read (`:576`, comparing the caller's snapshot against `get_authoritative_session`). The
handler must be indifferent to which fired.

**A read divergence the fix must survive.** The worker's guard reads by
**`redis_key`** (`AgentSession.query.get(redis_key=session.db_key.redis_key)`, `:3001`),
while `_complete_agent_session` re-reads by **`session_id`**
(`AgentSession.rows_for_session_id`, `session_completion.py:130`) and tie-breaks toward a
`"running"` row, falling back to the newest. When multiple rows share a `session_id` — the
exact condition #1803's escalation exists to clean up — these two reads can select
**different rows**. A terminal-status skip based on the `redis_key` read is therefore
*necessary but not sufficient*: the worker can see a non-terminal row, pass the skip, and
`_complete_agent_session` can still land on a terminal one. This is why the fix needs both
the skip **and** the typed catch, and why the catch is load-bearing rather than
belt-and-braces.

## Why Previous Fixes Failed

Neither prior fix failed at what it set out to do. Both #1803 and #2099 (#2088) are
correct, still in place, and still doing their job at `:2150` and `:2245`. What failed is
the **sweep**: each fix closed its own reported crash site and stopped.

- **#1803 (2026-06-26)** — root cause correctly identified (kill racing the pop), fix at
  the right layer (the loop, not the lifecycle), but scoped to the one call the reporter
  hit. The other lifecycle write inside the same loop — the completion `finally` — was
  never enumerated.
- **#2088 / PR #2099 (2026-07-15)** — a *sibling exception type* at the *same* call.
  Finding a second exception class at the pop site was itself the evidence that the class
  is "any exception, any per-session call site", yet the response again generalised the
  exception type while holding the site fixed.

The result is a documentation artefact that encodes the narrow scope: the invariant in
`docs/features/agent-session-queue.md:132-150` is stated for the whole worker loop but the
section is titled and framed around the pop site, so a reader auditing the file for
compliance is steered away from `:3019`/`:3028`.

**What this fix does differently:** it treats the defect as replicated-value rather than
site-local. Task 5 is a grep sweep over `_worker_loop` for *every* lifecycle write, with
the sweep output recorded in the PR; the fix is not considered done because three named
sites are handled, but because the sweep is clean. Task 4 rescopes the doc section from
"pop-loop" to "worker-loop" so the next auditor is steered correctly.

## Architectural Impact

Low. The change is confined to one `finally` block inside one function and introduces no
new module, constant, model field, index, or public signature.

**Invariants touched:**

- **Kill-is-terminal** (`docs/features/session-lifecycle.md:61`, `:135-152`) — this fix
  *strengthens* compliance. It converts the worker's completion write from an unguarded
  terminal-flip attempt into the same "first terminal write wins; later writers log and
  step aside" posture every other lifecycle caller already uses. No change to
  `finalize_session` and no new `reject_from_terminal=False` caller. Explicitly **not**
  a relaxation of the guard.
- **Worker-loop fault containment** (`docs/features/agent-session-queue.md:132-150`) — the
  invariant is unchanged; its coverage is extended from the pop site to the completion
  site, which is where it always claimed to apply.

**Ownership boundary made explicit.** Today the worker's `session_failed` flag implicitly
claims authority to classify the session's outcome. After this fix the rule is stated:
`session_failed` is the worker's *local opinion*, formed after the fact; if the
authoritative row is already terminal, the writer that got there first — executor finalize
guard, transcript completion, or health checker — owns the classification, and the worker
records its disagreement in the log rather than in Redis.

**Not touched:** `models/session_lifecycle.py`, `agent/session_completion.py`,
`agent/session_executor.py`, `agent/session_health.py`. No Popoto model, field, or index.
No new configuration key. No new thread, task, or timer.

## Appetite

**Small.** Roughly 40 lines of production change inside one `finally` block, one new test
file, **three mechanical patch-target repairs in one existing test file**
(`tests/unit/test_worker_persistent.py` — pre-existing defect, independently justified, see
`## Test Impact`), and two documentation edits. No new abstractions, no new constants, no
migration.

The fix is deliberately smaller than its two predecessors: #1803 and #2088 each needed a
bounded escalation or spin guard because the pop site **re-pops the same session every
tick**, producing a hot loop that has to be damped. The completion `finally` runs **once
per session** and the loop moves on regardless, so there is no spin to bound. Log-and-
continue is the complete fix here; anything more is cargo-culted structure (see No-Gos).

**Out of appetite:** re-architecting session-completion ownership, deduplicating the
`redis_key` vs `session_id` read divergence, or adding worker-task death observability
(all in No-Gos, one with a follow-up issue).

## Prerequisites

None blocking. All three can proceed in parallel with this work.

- **PR #3248** (`session/executor-exit-finalize-guard-3209`) is open and touches
  `agent/session_executor.py` only — no file overlap, so no merge conflict in either
  direction. It widens the window this bug fires in, which argues for landing this fix
  promptly, not for sequencing behind it. **If #3248 merges before this PR opens**, rebase
  and re-run the Verification suite; no code change is expected.
- **Lane identity** is already established: slug `worker-loop-terminal-3253`, worktree
  `.worktrees/worker-loop-terminal-3253`, branch `session/worker-loop-terminal-3253`. Do
  not create a second lane.
- **No expected-failure coverage exists.** `grep -rn 'pytest.mark.xfail\|pytest.xfail('
  tests/` returns nothing related to this path — neither a decorator nor a runtime
  `pytest.xfail()` call. There is no pre-staged test to convert; the failure-path tests in
  Task 1 are net-new and must be written RED-first.

## Solution

Three changes, all inside `_worker_loop`'s completion `finally` in
`agent/agent_session_queue.py` (`:2995`–`:3028`). Nothing outside that block changes.

### S1 — Add an already-terminal skip (third skip condition)

Insert a branch between the `pending` skip (`:3010`) and the `else` (`:3018`):

```python
elif fresh.status in TERMINAL_STATUSES:
    logger.info(
        "[worker:%s] Session %s already terminal in Redis (status=%r) — another "
        "writer owns the outcome; skipping completion (worker wanted %r)",
        worker_key,
        session.agent_session_id,
        fresh.status,
        "failed" if session_failed else "completed",
    )
```

`TERMINAL_STATUSES` is already imported at module scope (`:82`) and already used inside
this very function at `:2736` — **no new import**.

This is a correctness change, not a defensive one. `fresh` is the authoritative row; a
terminal status on it means a writer that owns the outcome already classified the session.
Under the kill-is-terminal invariant the first terminal write wins, so the worker's
after-the-fact `session_failed` opinion must not attempt to overwrite it. The log records
the disagreement (`status=%r` vs `worker wanted %r`) so an operator can still see when the
worker and the winning writer disagreed. INFO, not WARNING, per the convention at
`docs/features/session-lifecycle.md:143` — this is the expected outcome of a concurrent
writer, not an alarm.

### S2 — Narrow the `try` to the read; give the write its own typed handler; delete the retry

The `try` at `:3000` currently spans both the read and the write. Split them so the
existing handler protects only what it was written for, and the write gets a handler that
matches what it can actually raise:

```python
# Guard against nudge overwrite: re-read the session from Redis. (comment retained)
_should_complete = False
try:
    fresh = AgentSession.query.get(redis_key=session.db_key.redis_key)
except Exception as guard_err:
    # READ failure only — this is what the handler was always for. Falling
    # through to the completion write preserves the pre-existing intent
    # ("completing session as fallback") without retrying a failed write.
    logger.warning(
        "[worker:%s] Nudge guard read failed for %s: %s — completing as fallback",
        worker_key, session.agent_session_id, guard_err,
    )
    _should_complete = True
else:
    if not fresh:
        logger.info(...)          # unchanged, :3003-3009
    elif fresh.status == "pending":
        logger.info(...)          # unchanged, :3011-3017
    elif fresh.status in TERMINAL_STATUSES:
        logger.info(...)          # S1
    else:
        _should_complete = True

if _should_complete:
    try:
        await _complete_agent_session(session, failed=session_failed)
    except StatusConflictError as conflict_err:
        # Expected, correct, defense-in-depth: a concurrent writer reached a
        # terminal status first (or the CAS re-read saw a different row than
        # our redis_key lookup did). MUST NOT propagate — escaping this
        # `finally` kills _worker_loop and strands every session for this
        # worker_key (#1803, #2088, #3253).
        logger.info(
            "[worker:%s] Completion for %s lost to a concurrent terminal writer: %s",
            worker_key, session.agent_session_id, conflict_err,
        )
    except Exception as complete_err:
        logger.error(
            "[worker:%s] Completion write failed for %s (worker continues): %s",
            worker_key, session.agent_session_id, complete_err,
            exc_info=True,
        )
```

Three properties this shape guarantees:

1. **The bare retry at `:3028` is deleted, not guarded.** After the split there is exactly
   **one** `_complete_agent_session` call in the block. A retry was never the right
   recovery: the handler existed for a read failure, and the read-failure path now reaches
   the same single write site via `_should_complete = True`. Retrying the call that just
   raised, against unchanged state, could only ever raise again.
2. **`StatusConflictError` is caught first and separately**, so the expected concurrent-
   writer case logs at INFO with the exception's own `session_id`/`expected`/`actual`
   detail, and is never mistaken for a genuine fault. It is already bound in
   `_worker_loop`'s local scope by the import at `:2101` — **no new import**.
3. **The broad `except Exception` is the containment backstop**, at ERROR with
   `exc_info=True`. It is deliberately `Exception` and **not** `BaseException`:
   `CancelledError` and `KeyboardInterrupt` must keep propagating so worker shutdown still
   works — the same reasoning stated for the `ModelException` handler at
   `docs/features/agent-session-queue.md:148-150`. This closes the class rather than the
   instance: *no* exception from the completion write can strand the `worker_key`, whether
   or not anyone anticipated its type. That is precisely what #1803 and #2088 each failed to
   do (see Why Previous Fixes Failed).

### S3 — No bounded escalation here (deliberate)

Both prior fixes paired their handler with a bounded escalation (#1803, `:2046-2245`) or a
spin guard (#2088, `CORRUPTED_POP_*`). Neither is added here, and that is a design decision
rather than an omission.

Those guards exist because the pop site **re-pops the same session on every tick** — a
conflict there is a hot loop that must be damped, and after N consecutive conflicts the
remediation (delete the stale terminal duplicate, then cancel the stuck pending row) is
what breaks the spin. The completion `finally` runs **exactly once per session**; the loop
then clears the event and pops the next one. There is no repetition to count and no spin to
damp, so a counter would accumulate state that never fires and imply a remediation that has
nothing to remediate. Log-and-continue is the whole fix. Recorded in No-Gos.

### Why S1 alone is insufficient

The worker guard reads by `redis_key` (`:3001`); `_complete_agent_session` re-reads by
`session_id` and tie-breaks toward `"running"`, else newest (`session_completion.py:118-152`).
When multiple rows share a `session_id`, the two reads can select different rows — so a
row that passes S1's non-terminal check can still resolve to a terminal row inside
`finalize_session`. S1 removes the *common* trigger; S2 makes the block *unable* to kill the
worker regardless. Both ship together; neither is redundant. TC3 tests exactly this
divergence.

## Failure Path Test Strategy

All tests go in a **new file**, `tests/unit/test_worker_loop_completion_conflict.py`, driving
the real `_worker_loop` coroutine to completion and asserting on both the observable outcome
(the loop returns normally, and keeps draining) and the log record. A new file rather than an
addition to `tests/unit/test_worker_persistent.py` avoids a collision surface with concurrent
lanes and keeps the family's regression suite findable by name.

**Test-infrastructure hazard that must not be repeated.** The existing worker-loop tests
patch `asq.AgentSession.get` with `create=True` at **three** sites —
`tests/unit/test_worker_persistent.py:386`, `:477-482`, `:567-572` (see `## Test Impact`
for the per-site enumeration). `AgentSession.get` **does not
exist** — verified: `hasattr(AgentSession, "get")` is `False`, while
`hasattr(AgentSession.query, "get")` is `True`. The guard at `:3001` calls
`AgentSession.query.get(...)`, so those patches bind a phantom attribute and the guard's
real read runs unmocked. New tests **must** patch `AgentSession.query.get` (e.g.
`patch.object(asq.AgentSession.query, "get", return_value=...)`) or they will silently test
nothing. Task 1 includes a one-line assertion of the patch target's pre-existence so the
mistake cannot recur unnoticed.

| ID | Scenario | Setup | Asserts |
|----|----------|-------|---------|
| **TC1** | Terminal row → completion is skipped (S1) | `_execute_agent_session` raises after the row went `completed`; guard read returns `status="completed"` | `_complete_agent_session` **not called**; `_worker_loop` returns normally; `worker_key not in _active_workers`; an INFO record matching `already terminal` |
| **TC2** | Terminal→terminal write raises → worker survives (S2) | Guard read returns a **non-terminal** row so the skip is bypassed; `_complete_agent_session` is an `AsyncMock(side_effect=StatusConflictError(...))` | `_worker_loop` returns normally (does **not** raise); `_complete_agent_session` called **exactly once** (proves the `:3028` retry is gone); an INFO record matching `lost to a concurrent terminal writer` |
| **TC3** | Read/write row divergence (the S1-insufficient case) | Guard read returns `status="running"`; the completion write raises `StatusConflictError` as if it resolved a different, terminal row | Same as TC2 — the loop survives on the path S1 cannot cover |
| **TC4** | **The stranding regression itself** — worker keeps draining | Two sessions queued for one `worker_key`; session 1's completion write raises `StatusConflictError`; session 2 pops normally and then requests shutdown | **session 2 executes** — this is the acceptance check for the issue's stated consequence. On unfixed code the loop dies during session 1 and session 2 never runs |
| **TC5** | Guard **read** failure still completes as fallback | `AgentSession.query.get` raises `RuntimeError`; `_complete_agent_session` is a plain `AsyncMock` | `_complete_agent_session` called **exactly once**; a WARNING matching `Nudge guard read failed`; loop returns normally. Pins the pre-existing fallback intent so S2's restructure is behaviour-preserving on the path the handler was actually written for |
| **TC6** | Non-`StatusConflictError` write failure is contained | `_complete_agent_session` raises `RuntimeError("redis down")` | Loop returns normally; an ERROR record matching `Completion write failed`. Proves the class is closed, not just the one exception type |

**RED-first is mandatory.** TC2, TC3, TC4 and TC6 must be observed failing against the
pre-fix code before S1/S2 are written — TC2/TC3/TC4/TC6 by the loop raising
`StatusConflictError`/`RuntimeError` out of `_worker_loop`, TC1 by
`_complete_agent_session` being called when it should not be. A guard that has never been
seen RED against the known-bad code is not evidence. Task 1's validation command captures
the RED run; Task 3's captures the GREEN run.

**Log assertions** use `caplog` at the `agent.agent_session_queue` logger with
`caplog.set_level(logging.INFO)`, matching on the distinctive substrings above rather than
whole formatted lines, so a later copy-edit of a log message does not break the suite.

## Test Impact

**Phantom patch target — THREE sites, not two.** `tests/unit/test_worker_persistent.py`
patches the non-existent attribute `asq.AgentSession.get` with `create=True` at **three**
places. All three are UPDATE targets in T3; repairing fewer than three leaves a phantom
mock alive while the acceptance predicate reports clean (see the corrected union predicate
in T3 / SC10 / V8). Line spans re-verified against this checkout at revision time:

- [ ] `tests/unit/test_worker_persistent.py:386` —
      `TestPersistentMode::test_corrupted_pop_guard_resets_on_successful_pop` (single-line form)
      — **UPDATE**: `patch.object(asq.AgentSession, "get", return_value=mock_fresh, create=True)`
      → `patch.object(asq.AgentSession.query, "get", return_value=mock_fresh)`. Drop
      `create=True`: `AgentSession.query.get` really exists, so keeping it would mask a
      future rename. No behavioural dependency on this plan's change.
- [ ] `tests/unit/test_worker_persistent.py:477-482` —
      `TestPersistentMode::test_standalone_processes_nudge_without_exit` (multi-line form;
      the `asq.AgentSession,` argument is on `:478`)
      — **UPDATE**: same repair. The `create=True` patch binds a phantom attribute the
      production code never calls. The test passes today and will pass after the fix either
      way (its `finalized_by_execute=True` path skips the completion block entirely), so this
      is a correctness repair of the test's own premise, not a response to a behaviour
      change. Verify it still passes after the edit.
- [ ] `tests/unit/test_worker_persistent.py:567-572` —
      `TestGracefulShutdown::test_shutdown_exits_after_current_session` (multi-line form;
      the `asq.AgentSession,` argument is on `:568`)
      — **UPDATE**: same repair. This is the site the round-2 critique found missing from
      the plan; it is invisible to the old single-line grep predicate.
- [ ] `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session_queue_async.py`,
      `tests/unit/test_worker_cancel_requeue.py`, `tests/unit/test_crash_snapshot.py`
      — **NO CHANGE, but must be re-run**: all four drive `_worker_loop` and are the blast
      radius for a `finally`-block restructure. The change is additive on every path they
      exercise (a new `elif`, a narrower `try`, one deleted duplicate call), so none is
      expected to move. Any failure here is a regression in S2's restructure, not a stale
      test — do not "fix" it by editing the assertion.
- [ ] `tests/integration/test_worker_drain.py`, `tests/integration/test_worker_wedge_pending.py`
      — **NO CHANGE, run as regression**: both assert queue-drain continuity for a
      `worker_key`, which is the property TC4 unit-tests. Confirms the fix at the
      integration layer without new integration code.
- [ ] `tests/unit/test_session_lifecycle.py`, `tests/integration/test_kill_is_terminal.py`
      — **NO CHANGE**: `finalize_session` and the kill-is-terminal guard are untouched by
      this plan. Listed so the critique record shows they were considered and excluded
      deliberately, not overlooked.
- [ ] `tests/unit/test_active_workers_patch_targets.py`
      — **NO CHANGE, run as regression**: this file exists to pin `_active_workers` patch
      targets, and `:3054`'s pop is on the changed code's escape path. Cheap insurance.

No test is DELETED and none is REPLACED. **Three** patch sites in one file are UPDATEd for a
patch-target defect the new tests must not inherit.

## Rabbit Holes

- **"Fix the read divergence properly."** Making the worker guard and
  `_complete_agent_session` agree on one row (both by `session_id`, or both by `redis_key`)
  looks like the real fix and is a genuine latent problem. It is not this issue: it changes
  which row gets finalized on every session in the system, needs its own reproduction, and
  is exactly the kind of scope jump that would blow a Small appetite. S2's typed catch makes
  the divergence *survivable*, which is all #3253 requires. No-Go.
- **"Pass `reject_from_terminal=False` from the worker."** This makes the exception go away
  by letting the worker overwrite another writer's terminal classification — inverting the
  kill-is-terminal invariant to silence a symptom. It would let a late `failed` clobber a
  legitimate `completed`. Never do this. No-Go.
- **Generalising the fix into a decorator or context manager** (`@survives_session_faults`)
  over every per-session call site in `_worker_loop`. Tempting given this is the third
  recurrence, but it would rewrite two working, heavily-commented handlers (#1803, #2088)
  whose escalation logic is not uniform with this site's. The sweep in Task 5 gets the
  coverage benefit without the rewrite risk.
- **Chasing `finalized_by_execute`.** The flag's three assignment sites (`:2748`, `:2767`,
  `:2832`) are subtle and it is tempting to "fix" the trigger by setting it on the
  exception path too. That would suppress the completion block on paths where it is the
  only writer, trading a worker death for a session stuck in `running` — strictly worse,
  and it would break the crash-snapshot path at `:2969`. Leave it alone.
- **Adding worker-task death observability while in the file.** A `add_done_callback` on
  `_ensure_worker`'s task (`:1870`) would have made this incident visible years ago and is
  genuinely one line. It is still a different concern with a different blast radius (every
  worker task, every death path, including clean shutdown). Filed as a follow-up in Task 6
  rather than smuggled in. No-Go.

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | S1's skip suppresses a *legitimate* completion — a row that is terminal for an unrelated reason while this worker's write was the correct one | Low | Medium — session left with the wrong terminal classification | The kill-is-terminal invariant already declares the first terminal write authoritative, so "wrong" here is a pre-existing property of the invariant, not something S1 introduces. The INFO log names both statuses (`status=%r` vs `worker wanted %r`), so a real disagreement is greppable. Without S1 the same row raises and kills the worker, which is strictly worse. |
| R2 | The `except Exception` backstop hides a genuine bug (e.g. an `AttributeError` in the completion path) behind an ERROR line | Medium | Medium | Logged at **ERROR with `exc_info=True`**, so the full traceback reaches the log and Sentry — strictly more visible than today, where the same bug kills the worker with no traceback at all (see Research finding 2). TC6 pins this branch. |
| R3 | S2's restructure changes behaviour on the read-failure path (the handler's original purpose) | Low | High — would regress the nudge fallback | TC5 pins the read-failure path explicitly: exactly one `_complete_agent_session` call plus the WARNING. Task 3's validation runs the four existing `_worker_loop` suites unchanged. |
| R4 | `_should_complete` control-flow rewrite introduces a path where the session is never completed at all (silent `running` leak) | Low | High | Every branch either logs a skip with its reason or sets `_should_complete = True`; there is no fallthrough. TC1/TC5 assert the two ends. Reviewer check in Task 3: the block has exactly one `_complete_agent_session` call and every `elif` terminates in a log. |
| R5 | PR #3248 merges first and shifts the line numbers this plan cites | Medium | Low | #3248 touches `agent/session_executor.py` only — zero overlap. Prerequisites carry the rebase-and-re-run instruction; the anchors used are code shapes (`elif fresh.status == "pending"`, `except Exception as guard_err`), not line numbers. |
| R6 | Sweep (Task 5) finds additional unguarded lifecycle writes in `_worker_loop`, expanding scope mid-build | **Low** (downgraded from Low-Medium at round-2 critique) | Medium | The round-2 critique pre-ran T5's sweep against the checkout: the only two other lifecycle writes in `_worker_loop` are already guarded (`finalize_session` at `:2743`, covered by the exec-task `except Exception` at `:2937`; `transition_status` at `:2883`, own handler). The sweep is expected to come up clean. The decision rule stands anyway: same-shape sites get the same guard in this PR; a site needing *different* remediation is recorded in the PR body and filed as a follow-up rather than growing this one. |

## Race Conditions

The bug **is** a race, so the fix's correctness is a statement about timing, not just code
shape.

**The window.** Between the executor's terminal write (or the health checker's, or
`complete_transcript`'s) and the worker's guard read at `:3001`, plus the second window
between that read and `finalize_session`'s CAS re-read at
`models/session_lifecycle.py:573`. Three concurrent writers can land a terminal status in
either window:

| Writer | Terminal statuses it writes | Timing relative to the worker's `finally` |
|---|---|---|
| `_execute_agent_session` finalize guard / `complete_transcript` | `completed`, `failed` | Before the `finally` runs — the common trigger |
| Session health checker (`agent/session_health.py`) | `killed`, `abandoned` | Any time, including *between* the guard read and the completion write |
| Nudge re-enqueue (`_enqueue_nudge`) | `pending` (non-terminal) | Already handled by the `:3010` skip |

**TOCTOU is not eliminated, and does not need to be.** S1's read at `:3001` is a
check-then-act: a health-checker `killed` landing microseconds later still produces a
terminal row at write time. This is unavoidable without a lock, and a lock is the wrong
instrument — the repo's stated preference is observable ownership over locks. The fix's
guarantee is therefore not "the conflict never happens" but **"the conflict never kills the
worker."** S1 closes the wide, common window (cheap, and it keeps the logs quiet); S2 makes
the residual window harmless. TC3 is the test for exactly the residual case.

**Ordering guarantee relied upon:** none beyond `finalize_session`'s own CAS. This fix adds
no new shared state, no new await point between the read and the write (the two statements
are adjacent), and no new cross-task coordination. It cannot introduce a race it does not
already survive.

**Slot release is unaffected.** `registry.release(...)` at `:3034-3035` sits *after* the
completion block and outside it. Today an escaping exception skips it, leaking the global
concurrency slot on top of killing the worker; after the fix the block always falls through
and the release always runs. This is an additional, unadvertised repair — noted so a
reviewer does not read it as an unrelated behaviour change.

## No-Gos (Out of Scope)

- **Bounded escalation / spin guard at the completion site.** The completion `finally` runs
  once per session and the loop advances unconditionally, so there is no repeated conflict
  to count and no spin to damp — unlike the pop site, which re-pops the same row every tick
  and genuinely needs #1803's and #2088's counters. Adding one here would accumulate state
  that never fires and imply a remediation with nothing to remediate. See Solution S3.
- **Reconciling the `redis_key` vs `session_id` read divergence** between `:3001` and
  `session_completion.py:118-152`. Real, latent, and out of appetite: it changes which row
  is finalized for every session in the system and needs its own issue and reproduction.
  S2 makes it survivable, which is what #3253 asks for. No follow-up filed — it is a design
  question, not a defect, and belongs in a lifecycle-ownership review.
- **`reject_from_terminal=False` anywhere.** Silencing the conflict by relaxing the
  kill-is-terminal invariant would let a late `failed` overwrite a legitimate `completed`.
  The invariant is the thing protecting correctness here; the bug is that the worker fails
  to *respect* it, not that it exists.
- **Worker-task death observability** (`add_done_callback` on `_ensure_worker`'s task at
  `:1870`, or an `asyncio` exception handler). One line, and it would have surfaced this
  incident immediately — but it touches every worker task and every death path including
  clean shutdown, which is a different blast radius. **Follow-up issue filed in Task 6** so
  it is tracked rather than forgotten.
- **Refactoring `finalized_by_execute`.** Three subtle assignment sites (`:2748`, `:2767`,
  `:2832`); changing them trades a worker death for sessions stuck in `running` and breaks
  the crash-snapshot path at `:2969`. See Rabbit Holes.
- **Changing `finalize_session`, `_complete_agent_session`, or any lifecycle module.** The
  fix lives entirely in the caller. Zero lines change in `models/session_lifecycle.py`,
  `agent/session_completion.py`, `agent/session_executor.py`, or `agent/session_health.py`.

## Update System

**No update-system changes required.**

- **No Popoto schema change.** The fix adds no field, no index, and no status value to
  `AgentSession`. `TERMINAL_STATUSES` is *read* (it is already imported at `:82` and already
  used at `:2736`); nothing about its membership changes. Therefore **no migration function
  in `scripts/update/migrations.py` and no `MIGRATIONS` registration** — the per-repo
  Popoto migration requirement is inapplicable because no persisted shape moves. Stated
  explicitly rather than left blank, because `AgentSession` *is* a Popoto model and a
  reviewer is right to ask.
- **No new dependency, config file, env var, or secret.** Nothing to propagate to
  `.env.example`, `config/settings.py`, or `~/Desktop/Valor/.env`. Deliberately no new
  tunable constant (see No-Gos on escalation thresholds).
- **No `scripts/remote-update.sh` or `.claude/skills/update/` change.** The change is a
  code-only edit to a module the worker already imports.
- **Standard post-merge propagation applies:** because this changes worker code, the
  machines running the worker need `/update` and a `worker-restart` (or
  `./scripts/valor-service.sh restart`) to pick it up. That is the ordinary deploy path,
  not a modification to it. Recorded in Task 7 so the lane does not forget the restart.

## Agent Integration

**No agent integration required.**

- **No new CLI entry point.** Nothing is added to `pyproject.toml [project.scripts]`. The
  change is internal to `agent/agent_session_queue.py`, which the worker process already
  imports directly.
- **No bridge change.** `bridge/telegram_bridge.py` does not call the modified block; the
  bridge is I/O only and enqueues sessions, while `python -m worker` executes them
  (`docs/features/bridge-worker-architecture.md`). Nothing in the bridge's import surface
  moves.
- **No new agent-callable tool, and no MCP exposure.** There is no new function in `tools/`
  and therefore nothing that would be invisible to the agent for want of wiring.
- **The user-visible effect is negative-space** — sessions stop being stranded — and is
  observed through existing surfaces: the worker log (`logs/worker.log`), the dashboard at
  `localhost:8500/dashboard.json`, and Sentry for the R2 ERROR branch. No new surface is
  introduced and none is needed.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/agent-session-queue.md` — retitle the `:132` section
      **"Pop-Loop Exception Resilience" → "Worker-Loop Exception Resilience"**, and rewrite
      its scoping sentence (`:135-137`) so the invariant covers *every* per-session call
      site in `_worker_loop`, not only `_pop_agent_session()`. The narrow framing is the
      documented reason this site was missed twice (see Why Previous Fixes Failed).
- [ ] Add a third row to that section's handler table (`:141-143`) for the completion
      `finally`: exception `StatusConflictError`, trigger *"the authoritative row reached a
      terminal status before the worker's completion write (executor finalize guard,
      transcript completion, or health checker)"*, handling *"already-terminal skip; typed
      INFO catch on the write; broad ERROR backstop; no escalation — the block runs once per
      session, so there is no spin to bound (#3253)."*
- [ ] Add `agent/agent_session_queue.py` — worker completion `finally` to the
      **catch-and-log call-site list** in `docs/features/session-lifecycle.md:145-151`. That
      list currently names six callers and is the canonical audit of who wraps
      `finalize_session` in `try/except StatusConflictError`; the worker's completion write
      is a seventh and its absence is part of why the gap persisted.
- [ ] No entry needed in `docs/features/README.md` — both target docs are already indexed;
      this edits existing pages rather than adding one.

### Inline Documentation

- [ ] Comment the already-terminal skip (S1) with *why* it is correct, not what it does:
      the first terminal write wins under the kill-is-terminal invariant, and
      `session_failed` is the worker's local, after-the-fact opinion.
- [ ] Comment the `except StatusConflictError` handler (S2) with the containment rationale
      and the issue chain (`#1803`, `#2088`, `#3253`) so the next reader sees this is the
      third member of a family, not a one-off.
- [ ] Comment why the broad handler is `except Exception` and not `except BaseException` —
      `CancelledError` must keep propagating for shutdown — mirroring the existing note at
      `docs/features/agent-session-queue.md:148-150`.
- [ ] Comment, at the deleted retry's former position, that there is now exactly **one**
      `_complete_agent_session` call in the block and that the read-failure fallback reaches
      it via `_should_complete`, so nobody re-introduces the retry.

### External Documentation Site

Not applicable — this repo publishes no Sphinx/MkDocs/Read-the-Docs site. `docs/` is the
documentation surface and is covered above.

## Success Criteria

Each criterion names the task that satisfies it and the check that proves it.

| # | Criterion | Task | Proof |
|---|---|---|---|
| SC1 | A terminal-status conflict in the completion `finally` **cannot** propagate out of `_worker_loop` | T2, T3 | TC2, TC3, TC6 |
| SC2 | The worker **keeps draining** the queue for its `worker_key` after a conflicted session — the issue's stated consequence is gone | T2, T3 | TC4 |
| SC3 | The worker never attempts a terminal → different-terminal write it does not own | T2 | TC1 |
| SC4 | Exactly **one** `_complete_agent_session` call remains in the block; the bare retry at `:3028` is deleted, not guarded | T3 | TC2/TC5 call-count assertions + reviewer read |
| SC5 | The pre-existing guard-**read**-failure fallback still completes the session exactly once | T3 | TC5 |
| SC6 | The four existing `_worker_loop` suites pass unchanged (no behaviour regression from the restructure) | T3 | Verification row V3 |
| SC7 | Every failure-path test was observed **RED** against pre-fix code before the fix landed | T1 | RED run output pasted into the PR body |
| SC8 | No unguarded lifecycle write remains anywhere in `_worker_loop` — the class is closed, not the instance | T5 | Sweep output pasted into the PR body |
| SC9 | Both doc surfaces reflect the widened invariant and the seventh catch-and-log call site | T4 | Verification rows V5 **and** V5b (one row per doc surface — V5 alone leaves the `session-lifecycle.md` edit unchecked) |
| SC10 | The phantom `AgentSession.get` patch target is repaired at **all three** existing sites (`test_worker_persistent.py:386`, `:477-482`, `:567-572`) and not inherited by the new ones | T1, T3 | Union predicate, both commands returning no output: `grep -rn 'AgentSession, "get"' tests/` **and** `grep -rn 'asq\.AgentSession,$' tests/`. Observed RED pre-fix at 1 + 2 = 3 hits. No `-P` form (BSD grep false-green). |
| SC11 | Lint and format clean; no new dependency, constant, env var, or Popoto migration | T3 | Verification rows V4, V6 |

## Team Orchestration

**Single builder, sequential.** Small appetite, one production file, one new test file, two
doc files — there is no genuinely independent axis to parallelise, and the tasks form a
strict RED → fix → GREEN chain. Spawning parallel agents here would create a coordination
cost with no throughput gain and risks two agents editing the same `finally` block.

| Role | Agent | Scope |
|---|---|---|
| Build | `builder` | T1–T3, T5–T7, executed in order in the lane worktree |
| Docs | `documentarian` (optional) | T4 only, may run concurrently with T5 — disjoint files (`docs/features/*` vs `agent/`, `tests/`) |
| Review | `code-reviewer` | Post-build, against this plan's Success Criteria table |

**Lane:** slug `worker-loop-terminal-3253`, worktree
`.worktrees/worker-loop-terminal-3253`, branch `session/worker-loop-terminal-3253`. Do not
create a second worktree. Plan-document edits commit on `main`; all code commits go on the
lane branch.

**Handoff note for the builder:** T1's RED evidence is a deliverable, not a formality — a
guard never seen failing against the known-bad code proves nothing. Capture the RED output
before touching `agent/agent_session_queue.py`.

## Step by Step Tasks

Tasks are numbered; `Depends On` is the dependency graph. Each carries the command that
validates it.

### T1 — Write the failure-path tests and observe them RED

**Depends On:** none.

- Create `tests/unit/test_worker_loop_completion_conflict.py` with TC1–TC6 exactly as
  specified in Failure Path Test Strategy.
- Patch `asq.AgentSession.query.get`, **never** `asq.AgentSession.get`. Include a guard
  assertion in the module — `assert not hasattr(asq.AgentSession, "get")` and
  `assert hasattr(asq.AgentSession.query, "get")` — so the phantom-target mistake fails
  loudly instead of silently mocking nothing. Note this module-level assertion catches the
  mistake only in *this* new file; the three pre-existing sites in
  `tests/unit/test_worker_persistent.py` are repaired in T3 and pinned by the union grep
  predicate (SC10 / V8), not by this assertion.
- Drive the real `_worker_loop` coroutine; patch `_pop_agent_session`,
  `_execute_agent_session`, `_complete_agent_session`, `_check_restart_flag`, and
  `save_session_snapshot` as the existing suites do. Use `caplog` at
  `agent.agent_session_queue`, level `INFO`.
- Run against **unmodified** `agent/agent_session_queue.py` and confirm TC1, TC2, TC3, TC4
  and TC6 FAIL (TC5 is expected to pass pre-fix — it pins existing behaviour). Paste the RED
  output into the PR body.

**Validate:**
```bash
scripts/pytest-clean.sh tests/unit/test_worker_loop_completion_conflict.py -q
# EXPECTED AT THIS STEP: TC1, TC2, TC3, TC4, TC6 fail; TC5 passes. Non-zero exit is correct.
```

### T2 — Add the already-terminal skip (S1)

**Depends On:** T1.

- Insert the `elif fresh.status in TERMINAL_STATUSES:` branch between the `pending` skip
  (`:3010`) and the `else` (`:3018`), with the INFO log naming both the on-disk status and
  the status the worker wanted.
- Confirm no new import is needed (`TERMINAL_STATUSES` at `:82`, already used at `:2736`).
- Add the inline `why` comment from the Documentation section.

**Validate:**
```bash
scripts/pytest-clean.sh tests/unit/test_worker_loop_completion_conflict.py -q -k "TC1 or terminal_skip"
# EXPECTED: TC1 passes. TC2/TC3/TC4/TC6 still fail — S1 alone does not close the class.
```

### T3 — Narrow the `try`, add the typed handler, delete the retry (S2)

**Depends On:** T2.

- Restructure `:3000`–`:3028` exactly as Solution S2 specifies: read in its own `try`; the
  read-failure handler sets `_should_complete = True` instead of calling the write; the four
  skip/complete branches in the `else`; one write site guarded by
  `except StatusConflictError` (INFO) then `except Exception` (ERROR, `exc_info=True`).
- **Delete** the retry at `:3028`. Verify by count: exactly one `_complete_agent_session`
  call remains in the block.
- Add the three inline comments from the Documentation section.
- Repair **all three** phantom patch targets in `tests/unit/test_worker_persistent.py` —
  `:386` (single-line), `:477-482` (multi-line), `:567-572` (multi-line) — enumerated
  per-site in `## Test Impact`. The edit is identical at each:
  `patch.object(asq.AgentSession, "get", return_value=mock_fresh, create=True)` →
  `patch.object(asq.AgentSession.query, "get", return_value=mock_fresh)`. Drop `create=True`;
  `AgentSession.query.get` really exists, so retaining it would mask a future rename.
  **Three, not two** — the old plan text named only two, and the old single-line grep
  predicate could see only one of them.
- **Run the phantom-site predicate BEFORE the repair and confirm it is RED (3 total hits).**
  A guard never observed failing against the known-bad state is not evidence. Both commands
  must return 0 hits after the repair.

**Validate:**
```bash
scripts/pytest-clean.sh tests/unit/test_worker_loop_completion_conflict.py -q          # all GREEN
scripts/pytest-clean.sh tests/unit/test_worker_persistent.py \
                        tests/unit/test_agent_session_queue.py \
                        tests/unit/test_agent_session_queue_async.py \
                        tests/unit/test_worker_cancel_requeue.py \
                        tests/unit/test_crash_snapshot.py \
                        tests/unit/test_active_workers_patch_targets.py -q             # no regressions
grep -c "_complete_agent_session(session, failed=session_failed)" agent/agent_session_queue.py
# EXPECTED: 1

# Phantom patch target — UNION of two line-scoped greps. Neither alone is sufficient:
# the first sees only the single-line form, the second only the multi-line form.
grep -rn 'AgentSession, "get"' tests/     # PRE-FIX: 1 hit  (:386)   POST-FIX: no output
grep -rn 'asq\.AgentSession,$' tests/     # PRE-FIX: 2 hits (:478, :568)  POST-FIX: no output
# Both repaired forms read `asq.AgentSession.query, "get"` / `asq.AgentSession.query,`
# and match neither pattern.
#
# DO NOT collapse these into a single `grep -P` / `grep -z` PCRE one-liner. macOS ships
# BSD grep, which has no `-P`: the command fails with `invalid option`, and a piped
# `grep -c` then prints 0 — a false green that hides every remaining phantom site.
```

### T4 — Documentation

**Depends On:** T3. May run concurrently with T5 (disjoint files).

- `docs/features/agent-session-queue.md`: retitle `:132` to **"Worker-Loop Exception
  Resilience"**, widen the scoping sentence at `:135-137` to all per-session call sites, and
  add the third handler-table row for the completion `finally`.
- `docs/features/session-lifecycle.md`: add the worker completion `finally` to the
  catch-and-log call-site list at `:145-151`.

**Validate:**
```bash
grep -n "Worker-Loop Exception Resilience" docs/features/agent-session-queue.md          # 1 hit
grep -n "completion \`finally\`" docs/features/agent-session-queue.md                    # >=1 hit
grep -n "agent_session_queue.py" docs/features/session-lifecycle.md | grep -i "completion"  # >=1 hit
```

### T5 — Sweep `_worker_loop` for any remaining unguarded lifecycle write

**Depends On:** T3.

The defect class is replicated-value, so it closes on a clean sweep, never on an enumerated
site list (this is the third recurrence precisely because the two prior fixes closed on a
site list).

- Enumerate every lifecycle write inside `_worker_loop`'s body and confirm each is either
  inside a typed handler or provably cannot raise out of the loop.
- Record the sweep command and its output in the PR body — a reviewer must be able to
  re-run it.
- **Decision rule:** a newly found site of the *same* shape gets the same guard in this PR.
  A site needing *different* remediation is recorded in the PR body and filed as a follow-up
  issue; do not grow this PR's appetite.
- **Expected outcome: clean.** The round-2 critique ran this exact sweep command against the
  checkout and it works as written. The two *other* lifecycle writes it surfaces inside
  `_worker_loop` are **already guarded**: `finalize_session(fresh, "cancelled", …)`
  (`agent/agent_session_queue.py:2743`) sits inside the exec-task `try` whose
  `except Exception` at `:2937` catches it into `session_failed = True`, and
  `transition_status(session, "paused", …)` (`:2883`) carries its own
  `except Exception as _ts_err` handler. Still run the sweep and paste its output — the
  class closes on a clean sweep, not on this pre-verification — but do not budget for
  mid-build scope growth here.

**Validate:**
```bash
awk '/^async def _worker_loop/,/^# Revival detection functions/' agent/agent_session_queue.py \
  | grep -n "finalize_session\|transition_status\|_complete_agent_session\|\.save()"
# Review every hit: each must sit inside a typed except, or be unreachable from the loop body.
```

### T6 — File the worker-task death observability follow-up

**Depends On:** T3.

`_ensure_worker` (`:1870-1871`) registers no `add_done_callback` and `:3054` drops the
task's last reference, so *any* future escape from `_worker_loop` dies without a traceback
(Research finding 2). Out of scope here (No-Gos), but must not be lost.

- File an issue: *"Worker tasks die without a traceback — `_ensure_worker` registers no
  done-callback"*, label `bug`, referencing #3253 and citing `:1870-1871` and `:3054`.
- Link it from this PR's body.

**Validate:**
```bash
gh issue list --state open --search "worker task done-callback traceback" --json number,title
# EXPECTED: the new issue appears
```

### T7 — Land and propagate

**Depends On:** T4, T5, T6.

- Open the PR with `Closes #3253`, the RED evidence from T1, the sweep output from T5, and
  the follow-up issue link from T6.
- After merge, run `/update` on the worker machines and restart the worker
  (`./scripts/valor-service.sh restart` or `worker-restart`) so the running process picks up
  the change. Confirm with `tail -5 logs/worker.log`.

**Validate:**
```bash
python -m ruff check agent/agent_session_queue.py tests/unit/test_worker_loop_completion_conflict.py
python -m ruff format --check agent/agent_session_queue.py tests/unit/test_worker_loop_completion_conflict.py
```

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| V1 — new failure-path suite green | `scripts/pytest-clean.sh tests/unit/test_worker_loop_completion_conflict.py -q` | exit code 0 |
| V2 — worker survives and keeps draining (SC1, SC2) | `scripts/pytest-clean.sh tests/unit/test_worker_loop_completion_conflict.py -q -k "TC2 or TC3 or TC4 or TC6"` | exit code 0 |
| V3 — no regression in existing worker-loop suites (SC6) | `scripts/pytest-clean.sh tests/unit/test_worker_persistent.py tests/unit/test_agent_session_queue.py tests/unit/test_agent_session_queue_async.py tests/unit/test_worker_cancel_requeue.py tests/unit/test_crash_snapshot.py tests/unit/test_active_workers_patch_targets.py -q` | exit code 0 |
| V4 — lint clean | `python -m ruff check agent/agent_session_queue.py tests/unit/test_worker_loop_completion_conflict.py` | exit code 0 |
| V5 — queue doc updated (SC9) | `grep -c "Worker-Loop Exception Resilience" docs/features/agent-session-queue.md` | output contains 1 |
| V5b — lifecycle doc updated (SC9) | `grep -n "agent_session_queue.py" docs/features/session-lifecycle.md \| grep -i "completion"` | >=1 hit (the worker completion `finally` added as the seventh catch-and-log caller at `docs/features/session-lifecycle.md:145-151`) |
| V6 — format clean | `python -m ruff format --check agent/agent_session_queue.py tests/unit/test_worker_loop_completion_conflict.py` | exit code 0 |
| V7 — the retry is deleted, not guarded (SC4) | `grep -c "_complete_agent_session(session, failed=session_failed)" agent/agent_session_queue.py` | output contains 1 |
| V8 — phantom patch target eliminated, all three sites (SC10) | Both of: `grep -rn 'AgentSession, "get"' tests/` and `grep -rn 'asq\.AgentSession,$' tests/` | **both** produce no output. Pre-fix these return 1 hit (`:386`) and 2 hits (`:478`, `:568`) respectively — run them before the repair to prove the guard is RED. The union of the two line-scoped patterns is required: the first is blind to the multi-line `patch.object(` form. **Never** substitute a single `grep -P`/`-z` PCRE form — BSD grep on macOS rejects `-P`, and a piped `grep -c` then prints `0`, a false green. |
| V9 — integration drain continuity (SC2) | `scripts/pytest-clean.sh tests/integration/test_worker_drain.py tests/integration/test_worker_wedge_pending.py -q` | exit code 0 |
| V10 — no Popoto migration was added (SC11) | `git diff --name-only origin/main...HEAD -- scripts/update/migrations.py \| wc -l` | output contains 0 |

## Critique Results

### Round 2 — 2026-09-10 (FULL depth, independent roster: Risk & Robustness, Scope & Value, History & Consistency)

Verdict: **READY TO BUILD (with concerns)**. No blocker survived verification. Every
round-1 blocker was re-checked against the document and confirmed resolved (see Round 1
below). The plan's own code citations were re-verified line-by-line against
`origin/main` @ `b8fe729f9` and all hold.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness + Scope & Value + History & Consistency + Structural Checks (4-way independent convergence; History & Consistency rated it BLOCKER, the other two CONCERN — aggregated as CONCERN because it cannot make S1/S2 wrong or unbuildable, only false-green a secondary test-hygiene criterion) | The phantom-patch-target repair is under-enumerated **and** its verification predicate is blind to the shape it certifies. `tests/unit/test_worker_persistent.py` contains **three** phantom `patch.object(asq.AgentSession, "get", …, create=True)` sites, not two: `:386` (single-line), `:477-482` (multi-line — the plan mis-cites this as `:474-479`), and `:568-573` (multi-line — **not named anywhere in the plan**). Separately, the grep used by T3, SC10 and V8 (`AgentSession, "get"`) is line-scoped and matches ONLY `:386`; it structurally cannot see the two multi-line sites. So after repairing `:386` alone, V8 reports clean while two phantom patches survive — reproducing, in the verification layer, exactly the "silently tests nothing" failure the plan's own Failure Path Test Strategy warns against. | **Addressed — concern-closing revision 2026-09-10.** All three sites re-verified against this checkout at revision time and enumerated per-site in `## Test Impact` (`:386`, `:477-482`, `:567-572` — the third site's span is `:567-572`, one line earlier than the critique's `:568-573`; the grep-visible `asq.AgentSession,` argument lines are `:478` and `:568`). Propagated to the `## Failure Path Test Strategy` hazard note, T1's guard-assertion caveat, T3's repair bullet, SC10, and V8. The union predicate replaces the single-line-only form everywhere, with the RED-before-repair requirement (1 + 2 = 3 hits) and the explicit BSD-grep `-P` warning carried in T3, SC10 and V8. | Two-part, both mechanical. (1) **Enumeration:** add `tests/unit/test_worker_persistent.py:568-573` as a third UPDATE bullet in `## Test Impact` and a third repair target in T3, and correct the second site's citation from `:474-479` to `:477-482`. The edit is identical at all three: `patch.object(asq.AgentSession, "get", return_value=mock_fresh, create=True)` → `patch.object(asq.AgentSession.query, "get", return_value=mock_fresh)` (drop `create=True`; `AgentSession.query.get` really exists, so `create=True` would mask a future rename). (2) **Predicate:** replace the V8/SC10/T3 command with the **union of two line-scoped greps**, asserting both return empty. Run against this checkout to confirm the guard is RED before the repair: `grep -rn 'AgentSession, "get"' tests/` returns **1** hit (`:386`) and `grep -rn 'asq\.AgentSession,$' tests/` returns **2** hits (`:478`, `:568`) — 3 phantom sites total, matching the enumeration above. After all three repairs both commands return 0, because the repaired lines read `asq.AgentSession.query, "get"` / `asq.AgentSession.query,` and match neither pattern. **Do not use a single `-P`/`-z` PCRE one-liner** — macOS ships BSD grep, which has no `-P`, so that form fails with `invalid option` and `grep -c` then reports `0`, i.e. a false green. Do not ship V8 in its current single-line-only form. |
| CONCERN | Scope & Value | SC9 claims "**Both** doc surfaces reflect the widened invariant and the seventh catch-and-log call site" but cites only V5 as proof, and V5 greps `docs/features/agent-session-queue.md` alone. The `docs/features/session-lifecycle.md` edit has no row in the global `## Verification` table — the only check for it lives inside T4's own Validate block. A build that lands the first doc edit and drops the second shows a green V5 and an apparently-satisfied SC9. | **Addressed — concern-closing revision 2026-09-10.** T4's second validate line is promoted verbatim into the `## Verification` table as row **V5b** (`grep -n "agent_session_queue.py" docs/features/session-lifecycle.md \| grep -i "completion"`, expected `>=1 hit`), V5 is retitled "queue doc updated" to make the split explicit, and SC9's Proof cell now reads "V5 **and** V5b (one row per doc surface)". | Promote T4's existing second validate line into the Verification table verbatim as a new row **V5b**: `grep -n "agent_session_queue.py" docs/features/session-lifecycle.md \| grep -i "completion"` , expected `>=1 hit`; then change SC9's Proof cell from `V5` to `V5, V5b`. No new check needs inventing — T4 already runs the right command, it just is not part of the acceptance surface the reviewer reads. The target list to append to is `docs/features/session-lifecycle.md:145-151`, the six-caller catch-and-log audit; the worker completion `finally` is the seventh. |
| NIT | Scope & Value | T3 bundles the S2 production restructure with the pre-existing, independently-justified phantom-mock repair in `tests/unit/test_worker_persistent.py`, and the `## Appetite` inventory ("one `finally` block … one new test file, and two documentation edits") never itemizes that third file. The bundling is well justified elsewhere in the plan; only the appetite accounting omits it. | **Addressed — concern-closing revision 2026-09-10.** `## Appetite` now itemizes "three mechanical patch-target repairs in one existing test file (`tests/unit/test_worker_persistent.py` — pre-existing defect, independently justified)". The bundling into T3 is retained deliberately: the repair and the new tests must not diverge on the patch target, so they land together. | — |

**Also verified during this pass, and recorded so the builder does not re-investigate:** the
two *other* lifecycle writes inside `_worker_loop` that T5's sweep will surface are **already
guarded**, so T5 is expected to come up clean and R6 (mid-build scope growth) is lower than
the plan rates it. `finalize_session(fresh, "cancelled", …)` (`agent/agent_session_queue.py:2743`)
sits inside the exec-task `try` whose `except Exception` at `:2937` catches it into
`session_failed = True`; `transition_status(session, "paused", …)` (`:2883`) carries its own
`except Exception as _ts_err` handler. The T5 sweep command in the plan was executed against
this checkout and works as written.

### Round 1 — 2026-09-09 (structural, against the skeleton document)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Structural Checks | The plan document is an unwritten skeleton: all 21 section bodies (Problem, Freshness Check, Prior Art, Research, Data Flow, Why Previous Fixes Failed, Architectural Impact, Appetite, Prerequisites, Solution, Failure Path Test Strategy, Test Impact, Rabbit Holes, Risks, Race Conditions, No-Gos, Update System, Agent Integration, Documentation, Success Criteria, Team Orchestration, Step by Step Tasks, Verification, Open Questions) contain only the literal placeholder `<!-- skeleton -->`. The prior /do-plan dispatch (2026-09-09) never completed; PLAN is still `in_progress`. | **Addressed — revision pass 2026-09-10.** Every section body is now written against a fresh read of `agent/agent_session_queue.py:2938-3055`, `agent/session_completion.py:86-162`, and `models/session_lifecycle.py:67,311-318,472-592`. The Freshness Check re-verified all nine cited file:line references against `origin/main` @ `a15c5eab7` (one line of drift, recorded). | Frontmatter and H1 retained as the critique directed. Nothing carried over from the skeleton. `revision_applied` / `revision_applied_at` set in the same edit; `plan_revising` cleared so G7 no longer blocks `/do-build`. |
| BLOCKER | Structural Checks | No `## Step by Step Tasks` content exists, so there is no task numbering, no `Depends On` graph, and no per-task validation command. A build dispatch against this document would have zero executable instructions and would improvise the fix. | **Addressed.** Seven numbered tasks T1–T7, each with an explicit `Depends On` line and a runnable `Validate:` block. The graph is a strict chain T1 → T2 → T3 → {T4, T5, T6} → T7, with T4 and T5 marked concurrent-safe because their file sets are disjoint. | T1 is RED-first and its RED output is a named deliverable in the PR body, per the "prove guards red against known-bad" rule. T2's validation deliberately expects TC2/TC3/TC4/TC6 to still fail, so the builder cannot mistake a partial fix for a complete one. |
| BLOCKER | Structural Checks | The four repo-mandated sections are all placeholders: `## Documentation` (needs a checkbox task with a `docs/features/` path), `## Update System` (needs a `scripts/update/migrations.py` disposition — relevant here because `AgentSession` is a Popoto model and the fix touches its status transitions), `## Agent Integration` (MCP exposure disposition), and `## Test Impact` (per-test UPDATE/DELETE/REPLACE dispositions). | **Addressed.** `## Documentation` carries four checkbox tasks against `docs/features/agent-session-queue.md` and `docs/features/session-lifecycle.md` plus four inline-comment tasks. `## Update System` states **no migration required** and says why: the fix reads `TERMINAL_STATUSES` and writes no new field, index, or status value, so no persisted shape moves. `## Agent Integration` states **none required** with the CLI/bridge/MCP dispositions each given separately. `## Test Impact` carries six dispositions — two UPDATE, four NO CHANGE-but-run — with none deleted or replaced. | The Update System answer is given as a positive argument rather than a bare "N/A", because `AgentSession` *is* a Popoto model and a reviewer is right to ask. The two UPDATEs repair a phantom patch target (`AgentSession.get`, which does not exist) that the new tests must not inherit — SC10 and V8 pin it. |
| BLOCKER | Structural Checks | No `## Solution` and no `## Verification` content: the root cause at `agent_session_queue.py:3028` (unguarded retry on a terminal-status conflict that strands every session for the worker_key) has no stated fix, no guard condition, or acceptance check; Success Criteria are also empty, so no criterion maps to any task. | **Addressed.** `## Solution` names three changes: **S1** the already-terminal skip (`elif fresh.status in TERMINAL_STATUSES`, `TERMINAL_STATUSES` = `{completed, failed, killed, abandoned, cancelled}`); **S2** narrowing the `try` at `:3000` to the *read* only, catching `StatusConflictError` at INFO and `Exception` at ERROR around the single write, and **deleting** the `:3028` retry rather than guarding it; **S3** an explicit no-escalation decision with its rationale. `## Verification` carries ten machine-readable rows V1–V10. `## Success Criteria` carries eleven criteria SC1–SC11, each mapped to its task and its proving check. | The conflict is **logged and swallowed at INFO**, never escalated — matching the catch-and-log convention at `docs/features/session-lifecycle.md:143` and the reasoning that a concurrent terminal writer is the expected, correct outcome. The acceptance check the critique asked for is **TC4**: two sessions on one `worker_key`, the first conflicts, and the test asserts the **second still executes** — the issue's stated consequence, directly tested. TC3 covers the residual `redis_key`-vs-`session_id` read divergence that S1 alone cannot close. |

---

## Open Questions

None blocking. Three judgement calls were made rather than escalated; each is recorded here
so a reviewer can overrule cheaply.

1. **Log level for the conflict.** INFO, following the catch-and-log convention at
   `docs/features/session-lifecycle.md:143` (a concurrent terminal writer is the expected,
   correct outcome). If operators would rather see WARNING for a while to gauge real-world
   frequency, that is a one-word change in S2 — say so and it will ship as WARNING.
2. **No bounded escalation at this site** (S3 / No-Gos). Justified by the completion block
   running once per session rather than per tick. If a reviewer believes a repeated
   conflict here indicates a duplicate-row condition worth remediating the way #1803 does,
   that is a genuine disagreement about the failure mode and should be settled before build.
3. **Follow-up rather than inline** for worker-task death observability (T6). One line in
   `_ensure_worker` would close the silent-death channel for the whole family, not just this
   member. Held out for blast-radius reasons; overrule if you would rather absorb it here.

