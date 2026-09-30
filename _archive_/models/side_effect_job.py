"""SideEffectJob — a durable row for work a session leaves behind.

Post-session memory extraction used to be an ``asyncio.create_task`` the
worker held in memory: a failure vanished, a worker restart lost every
in-flight extraction, and nothing could be retried. This row replaces that.
It is created when the session's execution finishes and drained minutes
later by the ``side-effect-drain`` reflection, in a different process.

The row is LIVE state, not a record of failure. It carries ``attempts`` and
``next_attempt_at``, it is deleted on success, and at the attempt cap it is
converted into a terminal ``models.dead_letter.DeadLetter`` — two models
with two jobs, per this feature's Decision 1.

``payload_json`` is what makes the row self-sufficient: a handler's
arguments travel in the row rather than in the enqueuing process's memory,
so ``agent.side_effects.run_due`` can call the handler with its exact
production signature and no handler is rewritten to fit the queue.
"""

from datetime import datetime

from popoto import AutoKeyField, Field, IndexedField, IntField, KeyField, Model, SortedField


class SideEffectJob(Model):
    """One unit of post-session work, retried on a backoff until it lands.

    Fields:
        job_id: Generated row key.
        kind: Which handler in ``agent.side_effects.HANDLERS`` runs this job.
            Indexed so the drain can select a kind.
        session_id: The session the work belongs to. Passed positionally to
            the handler.
        project_key: Owning project, available to handlers that need it.
        payload_json: JSON object of the handler's keyword arguments.
        attempts: Handler invocations spent so far.
        next_attempt_at: When the drain may next run this job.
        status: ``pending`` | ``running`` | ``done``.

    ``next_attempt_at`` is a ``SortedField``, not a ``DatetimeField``, and
    that is load-bearing. The drain selects with
    ``filter(status="pending", next_attempt_at__lte=now)``; Popoto's plain
    fields contribute no range predicates, so a ``DatetimeField`` here makes
    that query raise — and the tempting fix (drop the predicate) would run
    *every* pending job on every 60s tick, erasing the backoff while every
    assertion about the stored value stayed green. ``SortedField`` forbids
    null, which ``enqueue`` already satisfies by always writing a value.
    """

    job_id = AutoKeyField()
    session_id = KeyField()
    project_key = KeyField(null=True)
    kind = IndexedField(default="memory_extraction")
    payload_json = Field(null=True)
    attempts = IntField(default=0)
    next_attempt_at = SortedField(type=datetime)
    status = IndexedField(default="pending")

    class Meta:
        # A job nobody drained in a week is not going to be drained. The TTL
        # matches the `sideeffect:idem:*` single-winner guard's expiry so the
        # row and its dedup key age out together.
        ttl = 7 * 24 * 60 * 60
