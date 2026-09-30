"""Reflection model — Redis-backed state for the unified reflection scheduler (issue #1273).

Tracks per-reflection definition + last-run summary. Per-run history rows are
stored separately in ``ReflectionRun`` (``models/reflection_run.py``) so the
size of a Reflection record is bounded; 200-cap embedded ``run_history`` lists
are gone.

The class exposes the legacy ``mark_started`` / ``mark_completed`` / ``mark_skipped``
API surface so existing callers (``agent/reflection_scheduler.py``,
``reflections/pm_briefings/__init__.py``) keep working — but ``mark_completed``
now writes a ``ReflectionRun`` row in addition to updating ``last_run_summary``,
``failure_count_consecutive`` (with dead-letter escalation), and the rolling
cost / token totals.

See:

- ``docs/features/reflections.md`` (single source of truth)
- ``docs/plans/unify-recurring-tasks-into-reflections.md`` (Q1, Q6, Q8)
"""

from __future__ import annotations

import logging
import time

from popoto import (
    AutoKeyField,
    DictField,
    Field,
    FloatField,
    IntField,
    KeyField,
    Model,
)

logger = logging.getLogger(__name__)

# Default retry policy applied when a reflection has no explicit override.
DEFAULT_RETRY_POLICY: dict[str, int] = {
    "max_retries": 3,
    "backoff_seconds": 60,
    "max_consecutive_failures_before_pause": 5,
}

# Auto-pause window after the dead-letter threshold is hit (24h).
PAUSE_AFTER_DEAD_LETTER_SECONDS = 86400


class Reflection(Model):
    """Persistent state for a single registered reflection.

    Each reflection declared in ``reflections.yaml`` (or created via the
    MCP server) gets one Reflection record in Redis, keyed by name.

    Schedule grammar:
        ``schedule`` carries one of ``every: <dur>``, ``cron: <expr>[; tz=<zone>]``,
        ``at: <iso>``. Parsed by ``agent.reflection_schedule.compute_next_due()``.

    Output:
        ``output_sink`` is one of ``log_only`` (default), ``dashboard_only``,
        ``memory:<importance>``, ``telegram:<chat>``. See ``agent/reflection_output.py``.

    Failure tracking:
        On error, ``failure_count_consecutive`` is bumped. At the
        ``max_consecutive_failures_before_pause`` threshold (default 5),
        ``paused_until`` is set 24h in the future and ``dead_letter_escalated``
        flips to True; a Memory record at importance 7.0 is written exactly once
        per escalation cluster.

    Cost accounting:
        ``cost_usd_total``, ``tokens_input_total``, ``tokens_output_total`` are
        running totals. The analytics rollup reflection reads ``ReflectionRun``
        rows directly for per-day breakdowns; these totals are for fast
        dashboard display only.

    Authorship:
        ``created_by_session_id`` is set when the reflection is created via the
        MCP server, and ``None`` for registry-loaded reflections (those declared
        in ``reflections.yaml``). MCP auth uses this distinction.

    Lifecycle:
        ``auto_delete_after_run`` is True for ``at:`` (one-shot) reflections;
        the scheduler deletes the record after a successful run, but preserves
        failed one-shots for operator diagnosis.
    """

    reflection_id = AutoKeyField()
    name = KeyField()

    # Schedule + output
    schedule = Field(default="")  # unified grammar (every:/cron:/at:)
    output_sink = Field(default="log_only")
    auto_delete_after_run = Field(type=bool, default=False)
    enabled = Field(type=bool, default=True)

    # Last-run summary (small dict for fast dashboard reads — full history
    # lives in ReflectionRun rows). Shape:
    # {"timestamp": float, "status": str, "duration": float, "error": str|None}
    last_run_summary = DictField(default=dict)

    # Legacy compat — kept on the read side so older Popoto records still load.
    # New writes always update last_run_summary instead. Scalar reads of these
    # are still answered for the dashboard's older code paths until those are
    # repointed (the same PR that lands this also updates ui/data/reflections.py).
    ran_at = FloatField(null=True)
    run_count = IntField(default=0)
    last_status = Field(
        default="pending"
    )  # pending | running | success | error | skipped | stale_running
    last_error = Field(null=True)
    last_duration = FloatField(null=True)

    # Failure tracking
    failure_count_consecutive = IntField(default=0)
    retry_policy = DictField(default=dict)
    paused_until = FloatField(default=0.0)
    dead_letter_escalated = Field(type=bool, default=False)

    # Cost accounting
    cost_usd_total = FloatField(default=0.0)
    tokens_input_total = IntField(default=0)
    tokens_output_total = IntField(default=0)

    # Authorship
    created_by_session_id = Field(null=True)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    def effective_retry_policy(self) -> dict:
        """Merge persisted ``retry_policy`` over the module defaults."""
        merged = dict(DEFAULT_RETRY_POLICY)
        if isinstance(self.retry_policy, dict):
            merged.update(self.retry_policy)
        return merged

    def is_paused(self, *, now: float | None = None) -> bool:
        """True iff ``paused_until`` is in the future."""
        if now is None:
            now = time.time()
        try:
            return float(self.paused_until or 0.0) > now
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(cls, name: str, **defaults) -> Reflection:
        """Get-or-create by name.

        Extra keyword args set initial values on a NEW record only (existing
        records are returned unchanged so concurrent callers don't clobber each
        other's writes).
        """
        existing = list(cls.query.filter(name=name))
        if existing:
            return existing[0]
        defaults.setdefault("schedule", "")
        defaults.setdefault("output_sink", "log_only")
        defaults.setdefault("last_status", "pending")
        return cls.create(name=name, **defaults)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_started(self) -> None:
        """Mark this reflection as currently running."""
        self.last_status = "running"
        self.ran_at = time.time()
        self.save()

    def mark_completed(
        self,
        duration: float,
        error: str | None = None,
        projects: list[dict] | None = None,
        *,
        cost_usd: float = 0.0,
        tokens_input: int = 0,
        tokens_output: int = 0,
        output_summary: str | None = None,
    ) -> None:
        """Mark this reflection as completed (success or error).

        Writes a ``ReflectionRun`` row with cost/token data, updates the
        ``last_run_summary`` dict for fast dashboard reads, and runs the
        failure-tracking state machine.

        Args:
            duration: How long the run took in seconds.
            error: Error message if the run failed, None for success.
            projects: optional per-project breakdown (legacy compat — preserved
                in ``last_run_summary`` so per-project audits keep working).
            cost_usd: Anthropic API spend; 0.0 for function-type reflections.
            tokens_input / tokens_output: Token counts for this run.
            output_summary: One-liner about what the run produced; surfaced
                on the dashboard.
        """
        self.last_duration = duration
        self.run_count = (self.run_count or 0) + 1
        status = "error" if error else "success"
        if error:
            self.last_status = "error"
            self.last_error = error[:1000]
        else:
            self.last_status = "success"
            self.last_error = None

        ts = time.time()
        self.last_run_summary = {
            "timestamp": ts,
            "status": status,
            "duration": duration,
            "error": (error[:500] if error else None),
            "projects": projects or [],
            "output_summary": output_summary,
        }

        # Cost rollup (Q8: function-type reflections write 0 cost)
        if cost_usd:
            self.cost_usd_total = (self.cost_usd_total or 0.0) + float(cost_usd)
        if tokens_input:
            self.tokens_input_total = (self.tokens_input_total or 0) + int(tokens_input)
        if tokens_output:
            self.tokens_output_total = (self.tokens_output_total or 0) + int(tokens_output)

        # Failure tracking (Q6 cycle-4 fix: dead-letter escalation flag)
        if error:
            self.record_failure(error=error, save=False)
        else:
            self.record_success(save=False)

        self.save()

        # Write a ReflectionRun row (post-save so we don't cascade-fail on
        # ReflectionRun import errors during early bring-up of the new model).
        try:
            from models.reflection_run import ReflectionRun

            ReflectionRun.create(
                name=self.name,
                timestamp=ts,
                status=status,
                duration_ms=duration * 1000.0,
                cost_usd=float(cost_usd or 0.0),
                tokens_input=int(tokens_input or 0),
                tokens_output=int(tokens_output or 0),
                error=(error[:500] if error else None),
                output_summary=output_summary,
            )
        except Exception as e:
            # Non-fatal: ReflectionRun is for history; the live dashboard already
            # has last_run_summary. Log so operators see drift, never crash the run.
            logger.warning(
                "[reflection] Failed to persist ReflectionRun for %s: %s",
                self.name,
                e,
            )

    def mark_skipped(self, reason: str = "already running") -> None:
        """Mark this reflection as skipped (e.g., paused or already running)."""
        self.last_status = "skipped"
        self.last_error = reason
        self.save()

    # ------------------------------------------------------------------
    # Failure-tracking state machine (Q6 cycle-4 fix)
    # ------------------------------------------------------------------

    def record_failure(self, error: str | None = None, *, save: bool = True) -> None:
        """Bump ``failure_count_consecutive`` and escalate to dead-letter if threshold hit.

        Idempotent within an escalation cluster: subsequent failures past the
        threshold bump the counter and refresh ``paused_until`` but do NOT
        re-write the dead-letter Memory record.
        """
        self.failure_count_consecutive = (self.failure_count_consecutive or 0) + 1
        threshold = self.effective_retry_policy()["max_consecutive_failures_before_pause"]

        if self.failure_count_consecutive >= threshold:
            self.paused_until = time.time() + PAUSE_AFTER_DEAD_LETTER_SECONDS
            if not bool(self.dead_letter_escalated):
                # First crossing — write the Memory record and flip the flag.
                self.dead_letter_escalated = True
                self._write_dead_letter_memory(error=error)

        if save:
            self.save()

    def record_success(self, *, save: bool = True) -> None:
        """Reset failure counters on a successful run."""
        self.failure_count_consecutive = 0
        self.dead_letter_escalated = False
        if save:
            self.save()

    def resume(self) -> None:
        """Operator-driven recovery: clear paused_until + escalation flag."""
        self.paused_until = 0.0
        self.failure_count_consecutive = 0
        self.dead_letter_escalated = False
        self.save()

    def _write_dead_letter_memory(self, *, error: str | None) -> None:
        """Persist a Memory record at importance 7.0 (project-level learning)."""
        try:
            from models.memory import Memory

            content = (
                f"Reflection {self.name} disabled: "
                f"{self.failure_count_consecutive} consecutive failures, "
                f"last error: {error or '(no error captured)'}"
            )
            Memory.create(
                content=content,
                importance=7.0,
                category="correction",
                source_agent="reflection-scheduler",
            )
        except Exception as e:
            # Memory failure must not crash the reflection.
            logger.warning(
                "[reflection] Failed to write dead-letter Memory for %s: %s",
                self.name,
                e,
            )

    # ------------------------------------------------------------------
    # Bulk read helpers (kept for backward compat with /queue-status etc.)
    # ------------------------------------------------------------------

    @classmethod
    def get_all_states(cls) -> list[Reflection]:
        """Return all reflection state records."""
        return list(cls.query.all())

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete reflection state records not run in max_age_days.

        Note: with ReflectionRun's 30-day TTL, the per-run history is
        independently bounded. This sweep only affects the Reflection
        definition record (e.g. an `at:` reflection that fired and was
        never auto-deleted because the run errored).
        """
        cutoff = time.time() - (max_age_days * 86400)
        all_records = cls.query.all()
        deleted = 0
        for record in all_records:
            ran_at = record.ran_at if isinstance(record.ran_at, (int, float)) else None
            if ran_at and ran_at < cutoff:
                record.delete()
                deleted += 1
        return deleted
