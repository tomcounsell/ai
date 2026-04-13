"""ReflectionRun model - per-day reflection execution state with resumability.

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    DictField,
    Field,
    KeyField,
    ListField,
    Model,
    SortedField,
    UniqueKeyField,
)


class ReflectionRun(Model):
    """Persisted state for a single reflection run (one per day).

    The date field is a UniqueKeyField so only one run exists per date.
    State is checkpointed after each step for resumability.
    project_key scopes runs to a specific project.
    """

    date = UniqueKeyField()  # YYYY-MM-DD, one run per day
    project_key = KeyField(null=True)
    completed_steps = ListField(null=True)
    output_path = Field(null=True)  # Path to JSON file with large payloads
    session_observations = ListField(null=True)  # list of observation dicts
    auto_fix_attempts = ListField(null=True)
    step_progress = DictField(null=True)  # {step_name: {metric: value}}
    started_at = SortedField(type=float)
    dry_run = Field(type=bool, default=False)

    @classmethod
    def load_or_create(cls, date: str, dry_run: bool = False) -> "ReflectionRun":
        """Load existing run for date, or create a new one."""
        existing = cls.query.filter(date=date)
        if existing:
            return existing[0]
        return cls.create(
            date=date,
            completed_steps=[],
            output_path=None,
            session_observations=[],
            auto_fix_attempts=[],
            step_progress={},
            started_at=time.time(),
            dry_run=dry_run,
        )

    def save_checkpoint(self) -> None:
        """Save current state. Delete and recreate for KeyField safety."""
        data = {
            "date": self.date,
            "project_key": self.project_key,
            "completed_steps": self.completed_steps or [],
            "output_path": self.output_path,
            "session_observations": self.session_observations or [],
            "auto_fix_attempts": self.auto_fix_attempts or [],
            "step_progress": self.step_progress or {},
            "started_at": self.started_at,
            "dry_run": self.dry_run,
        }
        self.delete()
        type(self).create(**data)

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 30) -> int:
        """Delete run records older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        all_runs = cls.query.all()
        deleted = 0
        for run in all_runs:
            if run.started_at and run.started_at < cutoff:
                run.delete()
                deleted += 1
        return deleted
