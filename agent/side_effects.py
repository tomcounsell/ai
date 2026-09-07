"""Enqueue and drain the durable side effects a session leaves behind.

One handler registry and one drain loop. A second kind of side effect is an
entry in :data:`HANDLERS`, not a framework.

    enqueue("memory_extraction", session_id, project_key, {...})

writes a ``SideEffectJob`` row, and the ``side-effect-drain`` reflection
calls :func:`run_due` every 60s. A handler that raises -- or a claim that
never gets to run at all, including an ``asyncio.CancelledError`` from a
worker shutdown mid-drain -- is retried on ``min(30 * 2**attempts, 900)``
seconds of backoff up to :data:`MAX_JOB_ATTEMPTS`, after which the job
becomes a ``DeadLetter(stage="extraction")`` and the row is deleted. A
cancellation releases the claim without charging an attempt.

**Create is single-winner per ``(kind, session_id)``.** Two enqueues for one
pair — ``_execute_agent_session`` running twice for a session, the archive
restore racing the executor, the fleet-wide ``/update`` back-enqueue running
on every machine against one shared Redis — must produce one row. A composite
``KeyField`` cannot provide that (``job_id`` is an ``AutoKeyField``, so
Popoto composes a distinct key per caller), so ``enqueue`` takes a
``SET ... NX`` on ``sideeffect:idem:{kind}:{session_id}`` through
``utils.redis_client.text_redis()`` first and returns the bound ``job_id``
on a lost race. This is the same single-winner idiom as
``agent/enqueue_idempotency.py``, deliberately not a second mechanism.

``enqueue`` RAISES when that guard's Redis is unreachable rather than
creating a possibly-duplicate row: the caller's failure has to be visible.
The one hot-path caller (``agent/session_executor.py``, at the end of
``_execute_agent_session``) wraps it in its own ``try``, because a raise
there would skip the session teardown that follows it.

Handlers are registered by dotted path so importing this module costs
nothing: the worker imports it on every session teardown and must not pay
for the memory/Anthropic stack unless a job actually runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from models.side_effect_job import SideEffectJob
from utils.utc import utc_now

logger = logging.getLogger(__name__)

# kind -> dotted path of the callable that runs it. The callable keeps its
# exact production signature: `run_due` passes `session_id` positionally and
# everything in `payload_json` as keyword arguments.
HANDLERS: dict[str, str] = {
    "memory_extraction": "agent.memory_extraction.run_post_session_extraction",
}

# Handler invocations a job gets before it becomes a dead letter.
MAX_JOB_ATTEMPTS = 4

# Backoff: 30s, 60s, 120s, ... capped at 15 minutes.
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 900

# Lifetime of the single-winner create guard. Matches SideEffectJob.Meta.ttl
# so the guard key and the row it protects age out together.
_IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60


def resolve_handler(kind: str) -> Callable[..., Any] | None:
    """Import and return the callable registered for ``kind``."""
    dotted = HANDLERS.get(kind)
    if not dotted:
        return None
    import importlib

    module_path, _, attr = dotted.rpartition(".")
    return getattr(importlib.import_module(module_path), attr)


def _idempotency_key(kind: str, session_id: str) -> str:
    return f"sideeffect:idem:{kind}:{session_id}"


def _backoff_seconds(attempts: int) -> int:
    return min(_BACKOFF_BASE_SECONDS * (2**attempts), _BACKOFF_CAP_SECONDS)


def _paused() -> bool:
    """True when side-effect handlers are administratively paused.

    The kill switch is a disposition on the job rather than a gate in front
    of the enqueue, so a paused period leaves rows to drain when it ends.
    """
    try:
        from config.settings import settings

        return bool(settings.features.side_effects_paused)
    except Exception:  # noqa: BLE001 -- an unreadable flag means "not paused"
        return False


def enqueue(
    kind: str,
    session_id: str,
    project_key: str | None = None,
    payload: dict | None = None,
) -> str:
    """Create one durable side-effect job and return its ``job_id``.

    Single-winner per ``(kind, session_id)``: a lost race returns the
    already-bound ``job_id`` without creating a second row.

    Raises on a Redis failure in the guard rather than creating a row that
    might be a duplicate.
    """
    if not kind:
        raise ValueError("enqueue() requires a kind")
    if not session_id:
        raise ValueError("enqueue() requires a session_id")

    import uuid

    from utils.redis_client import text_redis

    client = text_redis()
    key = _idempotency_key(kind, session_id)

    # Mint the id first so it can be bound into the guard before the row
    # exists: the loser of the race reads this exact value back.
    job_id = uuid.uuid4().hex

    if not client.set(key, job_id, nx=True, ex=_IDEMPOTENCY_TTL_SECONDS):
        bound = client.get(key)
        if bound:
            bound_id = str(bound)
            logger.info(
                "SideEffectJob enqueue lost the race for %s/%s; binding to %s",
                kind,
                session_id,
                bound_id,
            )
            _merge_payload_into_pending(bound_id, payload)
            return bound_id
        # The key expired between the SET NX and the read. Take it under our
        # own id and create the row.
        client.set(key, job_id, ex=_IDEMPOTENCY_TTL_SECONDS)

    SideEffectJob.create(
        job_id=job_id,
        kind=kind,
        session_id=session_id,
        project_key=project_key or None,
        payload_json=json.dumps(payload) if payload is not None else None,
        attempts=0,
        next_attempt_at=utc_now(),
        status="pending",
    )
    logger.info("SideEffectJob enqueued: kind=%s session=%s job=%s", kind, session_id, job_id)
    return job_id


def _merge_payload_into_pending(job_id: str, payload: dict | None) -> None:
    """Fold a lost-race caller's payload into the row it bound to.

    The single-winner guard keeps one row per ``(kind, session_id)``, but a
    ``session_id`` is shared across every turn of a conversation
    (``models/agent_session.py:1248``). Without this, a second turn's own
    ``enqueue`` call binds to the first turn's row and silently discards its
    ``response_text`` -- the drain then runs the FIRST turn's stale content,
    or nothing at all, instead of the most recent one. Overwriting the
    payload here means the drain always sees the latest turn's data, closing
    the gap between the guard's lifetime (60s drain cadence plus up to ~7min
    of retry backoff) and the seconds-long window the in-memory dedup this
    guard replaced actually covered.

    Only a still-``pending`` row is touched. A ``running`` row's handler has
    already read its ``kwargs`` before this function could possibly run, so
    updating it here cannot change what that invocation does, and the row is
    deleted on completion moments later regardless. That narrow window --
    the handler's own execution time, not the full drain cadence -- is the
    residual loss this fix cannot close without reworking the row's
    lifecycle beyond what this blocker asks for.
    """
    if payload is None:
        return
    try:
        existing = SideEffectJob.query.get(job_id=job_id)
        if existing is None or existing.status != "pending":
            return
        existing.payload_json = json.dumps(payload)
        existing.save()
    except Exception as e:  # noqa: BLE001 -- best-effort refresh, never blocks the caller
        logger.debug("SideEffectJob payload merge failed for %s: %s", job_id, e)


def release_idempotency(kind: str, session_id: str) -> None:
    """Drop the create guard so the same work can be enqueued again."""
    try:
        from utils.redis_client import text_redis

        text_redis().delete(_idempotency_key(kind, session_id))
    except Exception as e:  # noqa: BLE001 -- the key ages out on its own
        logger.debug("SideEffectJob guard release failed for %s/%s: %s", kind, session_id, e)


def _dead_letter(job: SideEffectJob, error: str) -> bool:
    """Convert an exhausted job into a terminal dead letter.

    Returns ``True`` on success. A dead-letter write can itself fail (Redis
    unreachable), and the caller must not delete the row or release the
    idempotency guard on that failure -- doing so would strand the job with
    no record anywhere. The caller falls back to a pending retry instead.
    """
    from bridge import dead_letters

    try:
        payload = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    payload["_kind"] = job.kind
    payload["_session_id"] = job.session_id
    try:
        dead_letters.record(
            "extraction",
            payload,
            f"side-effect handler failed {job.attempts} time(s): {error}",
            replayable=True,
            project_key=job.project_key,
        )
    except Exception as e:  # noqa: BLE001 -- a failed dead-letter write must not strand the row
        logger.warning("SideEffectJob %s dead-letter write failed: %s", job.job_id, e)
        return False
    return True


def _requeue(job: SideEffectJob, now, attempts: int) -> None:
    """Release a claimed row back to ``pending`` with backoff applied."""
    job.status = "pending"
    job.next_attempt_at = now + timedelta(seconds=_backoff_seconds(attempts))
    job.save()


async def run_due(limit: int = 25) -> dict:
    """Run every pending job whose ``next_attempt_at`` has passed.

    Returns a summary dict (``ran`` / ``failed`` / ``dead_lettered`` /
    ``skipped``). One bad job never stops the batch: a handler that raises is
    caught, counted against the job's ``attempts``, and backed off; at the cap
    the job becomes a dead letter and the row is deleted.
    """
    summary = {"ran": 0, "failed": 0, "dead_lettered": 0, "skipped": 0}
    if limit <= 0:
        return summary
    if _paused():
        logger.info("side-effect drain paused by settings.features.side_effects_paused")
        summary["skipped"] = 1
        return summary

    now = utc_now()
    due = list(SideEffectJob.query.filter(status="pending", next_attempt_at__lte=now))[:limit]

    for job in due:
        # Re-read and claim the row so an overlapping tick skips it (Race 1).
        fresh = SideEffectJob.query.get(job_id=job.job_id)
        if fresh is None or fresh.status != "pending":
            summary["skipped"] += 1
            continue
        fresh.status = "running"
        fresh.save()

        # Everything from here to the successful-completion block below runs
        # inside one `try`/`except BaseException`. Before this fix, the claim
        # above committed and then `resolve_handler()`, the payload decode,
        # and the handler call all ran OUTSIDE any protection that could
        # release it: a worker-shutdown `asyncio.CancelledError` (a
        # `BaseException`, not caught by `except Exception`), an import
        # failure inside `resolve_handler()`, or any other unforeseen raise
        # left the row claimed forever -- nothing anywhere else in the repo
        # ever moves a row out of "running". That stranded the row past every
        # future drain, aged it out silently under the 7-day TTL with no
        # DeadLetter, and kept its idempotency guard bound to a dead job_id
        # for those same 7 days. See `_requeue()` / the `BaseException`
        # branch below for the recovery path.
        try:
            handler = resolve_handler(fresh.kind)
            if handler is None:
                fresh.attempts = (fresh.attempts or 0) + 1
                kind, session_id = fresh.kind, fresh.session_id
                if _dead_letter(fresh, f"no handler registered for kind {kind!r}"):
                    fresh.delete()
                    release_idempotency(kind, session_id)
                    summary["dead_lettered"] += 1
                else:
                    _requeue(fresh, now, fresh.attempts)
                    summary["failed"] += 1
                continue

            try:
                kwargs = json.loads(fresh.payload_json or "{}")
            except (TypeError, ValueError) as e:
                logger.warning("SideEffectJob %s has unreadable payload_json: %s", fresh.job_id, e)
                kwargs = {}

            result = handler(fresh.session_id, **kwargs)
            if hasattr(result, "__await__"):
                await result
        except BaseException as e:  # noqa: BLE001 -- see comment above; must release the claim
            job_id, kind, session_id = fresh.job_id, fresh.kind, fresh.session_id
            if not isinstance(e, Exception):
                # Not an ordinary handler failure: `asyncio.CancelledError`
                # from a worker shutdown, or a `KeyboardInterrupt` /
                # `SystemExit`. None of these are the job's fault -- release
                # the claim without charging an attempt so the next tick
                # retries it, then let the interrupt keep propagating.
                # Swallowing it here (the way `except Exception` used to
                # make impossible, but a bare `except BaseException` alone
                # would not) would turn a shutdown signal into a caught,
                # logged, and discarded event.
                logger.warning(
                    "SideEffectJob %s (%s) interrupted mid-run (%s); releasing claim",
                    job_id,
                    kind,
                    type(e).__name__,
                )
                fresh.status = "pending"
                fresh.save()
                raise
            fresh.attempts = (fresh.attempts or 0) + 1
            attempts = fresh.attempts
            logger.warning(
                "SideEffectJob %s (%s) failed on attempt %s: %s", job_id, kind, attempts, e
            )
            if attempts >= MAX_JOB_ATTEMPTS:
                if _dead_letter(fresh, str(e)):
                    release_idempotency(kind, session_id)
                    fresh.delete()
                    summary["dead_lettered"] += 1
                else:
                    _requeue(fresh, now, attempts)
                    summary["failed"] += 1
            else:
                _requeue(fresh, now, attempts)
                summary["failed"] += 1
            continue

        kind, session_id = fresh.kind, fresh.session_id
        fresh.delete()
        release_idempotency(kind, session_id)
        summary["ran"] += 1

    return summary
