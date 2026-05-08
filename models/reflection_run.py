"""ReflectionRun model - one row per reflection execution.

History rows for the unified reflection scheduler. Each completed run
(success or error) writes one ``ReflectionRun`` keyed by ``(name, timestamp)``
via ``get_or_create_for``. TTL is 30 days, matching ``tools/analytics.py``'s
``--days 30`` rollup horizon (see Q8 cycle-4 fix in the unify-recurring-tasks
plan).
"""

import time

import popoto


class ReflectionRun(popoto.Model):
    """A single execution record for a reflection."""

    run_id = popoto.AutoKeyField()
    name = popoto.KeyField()  # FK to Reflection.name
    timestamp = popoto.Field(type=float, default=0.0)
    status = popoto.Field(default="success")  # success | error | stale_running
    duration_ms = popoto.IntField(default=0)
    cost_usd = popoto.Field(type=float, default=0.0)
    tokens_input = popoto.IntField(default=0)
    tokens_output = popoto.IntField(default=0)
    error = popoto.Field(null=True)
    output_summary = popoto.Field(null=True)
    delivery_error = popoto.Field(null=True)
    projects = popoto.ListField(default=[])

    class Meta:
        ttl = 86400 * 30  # 30 days — matches tools/analytics.py --days 30 rollup horizon

    @classmethod
    def get_or_create_for(cls, name: str, timestamp: float) -> "ReflectionRun":
        """Composite-key idempotent lookup-or-create on (name, timestamp).

        Popoto provides no composite-key ``get_or_create``, so this classmethod
        is the canonical entry point for the migration script and scheduler.
        """
        existing = cls.query.filter(name=name, timestamp=timestamp)
        if existing:
            first = existing[0] if not hasattr(existing, "first") else existing.first()
            if first is not None:
                return first
        run = cls(name=name, timestamp=timestamp)
        run.save()
        return run

    @classmethod
    def recent_for(cls, name: str, limit: int = 50) -> list["ReflectionRun"]:
        """Return the most-recent runs for a reflection name (newest first)."""
        rows = list(cls.query.filter(name=name))
        rows.sort(key=lambda r: r.timestamp or 0.0, reverse=True)
        return rows[:limit]

    @classmethod
    def cleanup_older_than(cls, max_age_days: int = 30) -> int:
        """Best-effort sweep for rows older than ``max_age_days``.

        Defense-in-depth — Popoto's ``Meta.ttl`` is the primary expiration
        mechanism, but a periodic reflection can call this to verify orphan
        rows are not accumulating.
        """
        cutoff = time.time() - (max_age_days * 86400)
        deleted = 0
        for row in list(cls.query.all()):
            if (row.timestamp or 0.0) < cutoff:
                row.delete()
                deleted += 1
        return deleted
