# Side-Effect Jobs

## Overview

Work a session leaves behind — today that is post-session memory extraction —
runs as a durable `SideEffectJob` row rather than as a task the worker holds in
memory. The row is written when the session's execution finishes and drained by
the `side-effect-drain` reflection, in a different process, on a 60s cadence.

The row is live state. It carries `attempts` and `next_attempt_at`, it is
deleted on success, and at the attempt cap it becomes a terminal
[`DeadLetter`](pipeline-dead-letters.md) a human can see.

## Why the row exists

Extraction was an `asyncio.create_task` registered in a module-level dict
(hotfix #1055). That gave the property it needed — the nudge fires without
waiting for extraction — and three it did not:

- A worker restart cancelled every in-flight extraction.
- A failure was swallowed into a debug log, so nobody could answer "what was
  lost yesterday?".
- Nothing retried.

The row keeps the first property (an enqueue is one Redis write) and fixes the
other three.

## The model

`models/side_effect_job.py`:

| Field | Purpose |
|---|---|
| `job_id` | Generated row key |
| `kind` | Which handler runs it (`IndexedField`) |
| `session_id` | Passed positionally to the handler |
| `project_key` | Owning project |
| `payload_json` | JSON object of the handler's keyword arguments |
| `attempts` | Handler invocations spent |
| `next_attempt_at` | When the drain may next run it (`SortedField`) |
| `status` | `pending` \| `running` \| `done` |

`Meta.ttl` is 7 days, matching the create guard's expiry so the row and its
dedup key age out together.

**`next_attempt_at` must be a `SortedField`, not a `DatetimeField`.** The drain
selects with `filter(status="pending", next_attempt_at__lte=now)`. Popoto's
plain fields contribute no range predicates, so a `DatetimeField` makes that
query raise — and the tempting fix (drop the predicate) would run *every*
pending job on every tick, erasing the backoff while every assertion about the
stored value stayed green.

## `payload_json` is what makes the row self-sufficient

A handler's arguments travel in the row, not in the enqueuing process's memory.
`run_due` calls `HANDLERS[kind](session_id, **json.loads(payload_json))`, so
every handler keeps its exact production signature and none is rewritten to fit
the queue.

For extraction this is load-bearing beyond convenience. `turn_count` and
`is_conversational` are the #1822 trivial-session gate, and they are captured at
the seam *before* teardown clears the in-memory turn tracker. Re-deriving either
inside the drain minutes later would silently restore the bug those captures
were added to fix.

## Single-winner create

Two enqueues for one `(kind, session_id)` must produce one row. This happens
more often than it sounds: `_execute_agent_session` runs twice for a session
(health-check revival, retry, manual resume), the archive restore races the
executor, and the fleet-wide `/update` back-enqueue runs on every machine
against one shared Redis.

`enqueue` takes `SET sideeffect:idem:{kind}:{session_id} {job_id} NX EX 604800`
through `utils.redis_client.text_redis()` before creating the row, and returns
the bound `job_id` on a lost race. A composite `KeyField` cannot provide this:
`job_id` is an `AutoKeyField`, so Popoto composes a distinct key per caller and
both rows would survive.

`enqueue` **raises** when that guard's Redis is unreachable rather than creating
a possibly-duplicate row. The one hot-path caller
(`agent/session_executor.py`, at the end of `_execute_agent_session`) wraps it in
its own `try` — a raise there would skip the error-case snapshot, the
steering-queue rescue, and the reaction/nudge path to the end of the function,
so a Redis error must cost an extraction, never a teardown.

A lost race is not silent: `enqueue` takes a keyword-only `merge_on_lost_race`
(default `True`) that decides what happens to the row it binds to. A
`session_id` is shared across every turn of a conversation, so the same
`(kind, session_id)` pair is enqueued repeatedly as a session resumes. With the
default on, the losing caller's payload overwrites the still-`pending` row so
the drain always runs the latest turn's data rather than a stale earlier one;
a `running` row is left untouched since its handler has already read its
arguments. `scripts/update/migrations.py`'s fleet-wide back-enqueue passes
`merge_on_lost_race=False`: it mints a synthetic placeholder payload only to
satisfy the handler's signature and must never overwrite a live pending job's
real payload with it. The per-turn caller and the dead-letter replay caller
(`bridge/dead_letters.py::_replay_side_effect`) both keep the default.

## Retry and give-up

`min(30 * 2**attempts, 900)` seconds of backoff, up to `MAX_JOB_ATTEMPTS` (4).
At the cap the job becomes `DeadLetter(stage="extraction")` and the row is
deleted; the dead letter is replayable, so a human can re-enqueue it from the
dashboard once the underlying cause is fixed. When the dead-letter write
itself fails, the row is requeued pending with backoff instead of being
deleted, so a failing handler is never silently discarded for want of a place
to record it.

A tick cancelled mid-handler — an `asyncio.CancelledError` from a worker
shutdown, or any other `BaseException` that is not an ordinary handler
failure — releases the claimed row back to `pending` with its attempt count
unchanged and re-raises, so the next tick retries it without spending one of
its four attempts on a shutdown that was never the job's fault.

One bad job never stops the batch. A handler that raises is caught per job, and
a `kind` with no registered handler is dead-lettered immediately rather than
retried four times.

## Adding a kind

Add one entry to `HANDLERS` in `agent/side_effects.py`:

```python
HANDLERS: dict[str, str] = {
    "memory_extraction": "agent.memory_extraction.run_post_session_extraction",
    "your_kind": "your.module.your_callable",
}
```

The value is a dotted path, resolved at call time, so importing
`agent.side_effects` stays free — the worker imports it on every session
teardown and must not pay for a handler's dependency tree unless a job runs.

The callable takes `session_id` positionally and everything else as keyword
arguments; async callables are awaited. That is the whole registration. This is
a handler dict with one drain loop, not a retry framework.

## Pausing

`settings.features.side_effects_paused` (`FEATURES__SIDE_EFFECTS_PAUSED`) makes
the drain report a skip and run nothing. Jobs stay pending and drain when the
pause lifts, so pausing loses no work. The switch is a disposition on the drain
rather than a gate in front of the enqueue, for exactly that reason.

## Files

| File | Role |
|---|---|
| `models/side_effect_job.py` | The row |
| `agent/side_effects.py` | `enqueue`, `run_due`, the handler registry |
| `reflections/housekeeping/side_effect_drain.py` | The 60s drain |
| `agent/session_executor.py` | The one production enqueue site |
| `scripts/update/migrations.py` | `side_effect_job_model` back-enqueue |
| `scripts/update/reflection_register.py` | `register_side_effect_drain` |

## Related

- [Pipeline Dead Letters](pipeline-dead-letters.md) — where an exhausted job goes
- [Subconscious Memory](subconscious-memory.md) — what the extraction handler does
- [Reflections](reflections.md) — the drain's scheduler
