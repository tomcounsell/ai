"""ReflectionRun Popoto model — per-run history rows for the unified Reflection system.

Replaces the embedded ``Reflection.run_history`` list (capped at 200) with
unbounded, indexable rows. Rows auto-expire after 30 days via Redis TTL,
matching the ``tools/analytics.py --days 30`` rollup horizon (issue #1273
Q1 cycle-4 fix).

Each row records exactly one execution of a registered reflection. The
scheduler writes one row per completion; the dashboard reader and the
analytics rollup read by ``name`` (indexed).

Composite-key idempotency for the migration backfill is provided by
``get_or_create_for(name, timestamp)`` (Q3 cycle-4 fix). Popoto does not
ship a composite-key ``get_or_create``, so this module supplies the
``query.filter(...).first()`` + construct/save fallback explicitly.

See ``docs/features/reflections.md`` and
``docs/plans/unify-recurring-tasks-into-reflections.md`` for the full design.
"""

from __future__ import annotations

from popoto import (
    AutoKeyField,
    Field,
    FloatField,
    IntField,
    KeyField,
    Model,
)


class ReflectionRun(Model):
    """A single execution of a registered reflection.

    Fields:
        run_id: Auto-generated Popoto primary key.
        name: Reflection name; indexed for ``filter(name=...)`` reads.
        timestamp: Unix timestamp (seconds) when the run completed.
        status: ``"success"`` | ``"error"`` | ``"skipped"`` | ``"stale_running"``.
        duration_ms: Run duration in milliseconds (float for sub-ms precision).
        cost_usd: Anthropic API spend for the run, 0.0 for function-type.
        tokens_input: Input tokens billed (0 for function-type).
        tokens_output: Output tokens billed (0 for function-type).
        error: Truncated error message (max 500 chars), None on success.
        output_summary: Optional one-liner about what the run produced.
        delivery_error: Set when the run succeeded but output delivery failed
            (e.g. ``telegram_resolve_failed: <chat>`` per Q5 cycle-4 fix).

    The TTL is declared at the Model level so Popoto applies Redis EXPIRE on
    every save (NOT a runtime-only ``r.expire()`` call, which would violate
    the no-raw-Redis-on-Popoto-keys invariant).
    """

    run_id = AutoKeyField()
    name = KeyField()
    timestamp = FloatField(default=0.0)
    status = Field(default="success")
    duration_ms = FloatField(default=0.0)
    cost_usd = FloatField(default=0.0)
    tokens_input = IntField(default=0)
    tokens_output = IntField(default=0)
    error = Field(null=True)
    output_summary = Field(null=True)
    delivery_error = Field(null=True)

    class Meta:
        # 30 days. Matches `tools/analytics.py --days` default (Q1 cycle-4 fix).
        ttl = 86400 * 30

    @classmethod
    def get_or_create_for(cls, name: str, timestamp: float) -> ReflectionRun:
        """Composite-key idempotency for the migration backfill.

        Popoto provides a single-key ``get_or_create`` only; the migration
        script needs ``(name, timestamp)`` idempotency to remain reentrant.

        Args:
            name: Reflection name.
            timestamp: Unix timestamp the run completed at.

        Returns:
            The existing row if one already matches both fields, otherwise
            a freshly-created row that has been saved.
        """
        existing = list(cls.query.filter(name=name, timestamp=timestamp))
        if existing:
            return existing[0]
        run = cls.create(name=name, timestamp=timestamp)
        return run
