"""Reflection model - Redis-backed state for the unified reflection scheduler.

Tracks per-reflection execution state: when it last ran, when it's next due,
run count, and last status/error. Used by agent/reflection_scheduler.py to
decide which reflections are due and to record outcomes.

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    AutoKeyField,
    Field,
    IntField,
    KeyField,
    ListField,
    Model,
)


class Reflection(Model):
    """Persistent state for a single registered reflection.

    Each reflection declared in config/reflections.yaml gets one Reflection
    record in Redis, keyed by name. The scheduler reads/updates these records
    on every tick.

    Fields:
        name: Unique identifier matching the registry entry
        last_run: Unix timestamp of last successful execution start
        next_due: Unix timestamp when the reflection should next run
        run_count: Total number of times this reflection has executed
        last_status: Result of last run: 'success', 'error', 'skipped', or 'running'
        last_error: Error message from last failed run (None if last run succeeded)
        last_duration: Duration of last run in seconds
        run_history: Append-only list of recent run dicts (capped at 200).
            Each dict: {timestamp, status, duration, error, log_path}
    """

    reflection_id = AutoKeyField()
    name = KeyField()
    last_run = Field(type=float, null=True)
    next_due = Field(type=float, null=True)
    run_count = IntField(default=0)
    last_status = Field(default="pending")  # pending | running | success | error | skipped
    last_error = Field(null=True, max_length=1000)
    last_duration = Field(type=float, null=True)
    run_history = ListField(default=[])  # List of run dicts, capped at 200

    _RUN_HISTORY_CAP = 200

    @classmethod
    def get_or_create(cls, name: str) -> "Reflection":
        """Get existing reflection state by name, or create a new record."""
        existing = cls.query.filter(name=name)
        if existing:
            return existing[0]
        return cls.create(
            name=name,
            last_run=None,
            next_due=None,
            run_count=0,
            last_status="pending",
            last_error=None,
            last_duration=None,
            run_history=[],
        )

    def mark_started(self) -> None:
        """Mark this reflection as currently running."""
        self.last_status = "running"
        self.last_run = time.time()
        self.save()

    def mark_completed(self, duration: float, error: str | None = None) -> None:
        """Mark this reflection as completed (success or error).

        Internally appends a run record to run_history (capped at 200 entries).
        The method signature is unchanged -- existing callers in
        agent/reflection_scheduler.py require no modifications.

        Args:
            duration: How long the run took in seconds
            error: Error message if the run failed, None for success
        """
        self.last_duration = duration
        self.run_count = (self.run_count or 0) + 1
        status = "error" if error else "success"
        if error:
            self.last_status = "error"
            self.last_error = error[:1000] if error else None
        else:
            self.last_status = "success"
            self.last_error = None

        # Append to run_history (capped at RUN_HISTORY_CAP)
        run_record = {
            "timestamp": time.time(),
            "status": status,
            "duration": duration,
            "error": error[:500] if error else None,
        }
        history = self.run_history if isinstance(self.run_history, list) else []
        history.append(run_record)
        if len(history) > self._RUN_HISTORY_CAP:
            history = history[-self._RUN_HISTORY_CAP :]
        self.run_history = history

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
            if record.last_run and record.last_run < cutoff:
                record.delete()
                deleted += 1
        return deleted
