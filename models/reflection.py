"""Reflection model - Redis-backed state for the unified reflection scheduler.

Tracks per-reflection execution state: schedule, last run summary, failure
tracking, and rolling cost totals. Run history is stored separately as
``ReflectionRun`` rows (see ``models/reflection_run.py``); this record only
keeps a compact ``last_run_summary`` for fast dashboard reads.

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    AutoKeyField,
    Field,
    IntField,
    KeyField,
    Model,
)


class Reflection(Model):
    """Persistent state for a single registered reflection.

    Each reflection declared in config/reflections.yaml gets one Reflection
    record in Redis, keyed by name. The scheduler reads/updates these records
    on every tick.
    """

    reflection_id = AutoKeyField()
    name = KeyField()

    # Schedule (fazm-style triplet: cron:, every:, at:)
    schedule = Field(default="")

    # Execution type: "function" (in-process callable) or "agent" (spawn AgentSession).
    # Registry-loaded reflections leave these blank and use the YAML ReflectionEntry;
    # ad-hoc Reflections (e.g. `agent_session_scheduler --after`) carry the command
    # inline so the scheduler can drive them without a YAML registration.
    execution_type = Field(default="")
    command = Field(default="", null=True)

    # Output sink (log_only | dashboard_only | memory:<importance> | telegram:<chat>)
    output_sink = Field(default="log_only")

    # Last-run state
    ran_at = Field(type=float, null=True)
    run_count = IntField(default=0)
    last_status = Field(
        default="pending"
    )  # pending | running | success | error | skipped | stale_running
    last_error = Field(null=True)
    last_duration = Field(type=float, null=True)
    last_run_summary = Field(type=dict, default={})  # {ran_at, status, duration, error}

    # Failure tracking
    failure_count_consecutive = IntField(default=0)
    retry_policy = Field(type=dict, default={})
    paused_until = Field(type=float, default=0.0)
    dead_letter_escalated = Field(type=bool, default=False)

    # Cost / token accounting (rolling totals)
    cost_usd_total = Field(type=float, default=0.0)
    tokens_input_total = IntField(default=0)
    tokens_output_total = IntField(default=0)

    # Provenance / lifecycle
    created_by_session_id = Field(null=True)
    auto_delete_after_run = Field(type=bool, default=False)

    # Threshold for the dead-letter Memory escalation (Q6 cycle-4 spec)
    _DEAD_LETTER_THRESHOLD = 5
    _DEAD_LETTER_PAUSE_SECONDS = 86400

    @classmethod
    def get_or_create(cls, name: str) -> "Reflection":
        """Get existing reflection state by name, or create a new record."""
        existing = cls.query.filter(name=name)
        if existing:
            return existing[0]
        return cls.create(name=name)

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
        cost_usd: float = 0.0,
        tokens_input: int = 0,
        tokens_output: int = 0,
        output: str | None = None,
    ) -> None:
        """Mark this reflection as completed (success or error).

        Creates a ``ReflectionRun`` row for history, updates the compact
        ``last_run_summary`` on this record, increments rolling cost/token
        totals, and applies failure-tracking + dead-letter Memory escalation
        per Q6 cycle-4.
        """
        # Lazy import to avoid circular imports during model registration.
        from models.reflection_run import ReflectionRun

        now = time.time()
        status = "error" if error else "success"
        truncated_error = error[:1000] if error else None

        self.last_duration = duration
        self.run_count = (self.run_count or 0) + 1
        self.last_status = status
        self.last_error = truncated_error
        self.last_run_summary = {
            "ran_at": now,
            "status": status,
            "duration": duration,
            "error": error[:500] if error else None,
        }

        # Rolling totals
        self.cost_usd_total = (self.cost_usd_total or 0.0) + (cost_usd or 0.0)
        self.tokens_input_total = (self.tokens_input_total or 0) + (tokens_input or 0)
        self.tokens_output_total = (self.tokens_output_total or 0) + (tokens_output or 0)

        # Failure tracking + dead-letter Memory escalation (Q6 cycle-4)
        if error:
            self.failure_count_consecutive = (self.failure_count_consecutive or 0) + 1
            if (
                self.failure_count_consecutive >= self._DEAD_LETTER_THRESHOLD
                and not self.dead_letter_escalated
            ):
                # Transition <5 -> >=5: write the Memory record once
                try:
                    from models.memory import Memory

                    Memory.create(
                        content=(
                            f"Reflection {self.name} disabled: "
                            f"{self.failure_count_consecutive} consecutive failures, "
                            f"last error: {error}"
                        ),
                        importance=7.0,
                        category="correction",
                    )
                except Exception:
                    # Memory write must never crash the scheduler.
                    pass
                self.dead_letter_escalated = True
            if self.failure_count_consecutive >= self._DEAD_LETTER_THRESHOLD:
                # Threshold (or above) — extend pause window each failure.
                self.paused_until = now + self._DEAD_LETTER_PAUSE_SECONDS
        else:
            # First success after escalation resets both fields.
            self.failure_count_consecutive = 0
            self.dead_letter_escalated = False

        # Persist a ReflectionRun row for history.
        try:
            run = ReflectionRun.get_or_create_for(name=self.name, timestamp=now)
            run.status = status
            run.duration_ms = int(duration * 1000) if duration else 0
            run.cost_usd = cost_usd or 0.0
            run.tokens_input = tokens_input or 0
            run.tokens_output = tokens_output or 0
            run.error = truncated_error
            run.output_summary = output[:1000] if output else None
            run.projects = projects or []
            run.save()
        except Exception:
            # History write must never crash the scheduler.
            pass

        self.save()

    def mark_skipped(self, reason: str = "already running") -> None:
        """Mark this reflection as skipped (e.g., already running)."""
        self.last_status = "skipped"
        self.last_error = reason
        self.save()

    @classmethod
    def get_all_states(cls) -> list["Reflection"]:
        """Return all reflection state records."""
        return list(cls.query.all())

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete reflection state records not run in max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        all_records = cls.query.all()
        deleted = 0
        for record in all_records:
            if record.ran_at and record.ran_at < cutoff:
                record.delete()
                deleted += 1
        return deleted
