"""Reflections Popoto models - Redis-backed state for the reflections maintenance system.

Three models:
- ReflectionRun: per-run state with resumability (one per day)
- ReflectionIgnore: auto-fix suppression with TTL-based auto-expiry
- LessonLearned: queryable institutional memory from LLM reflection

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    AutoKeyField,
    DictField,
    Field,
    IntField,
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
    """

    date = UniqueKeyField()  # YYYY-MM-DD, one run per day
    current_step = IntField(default=1)
    completed_steps = ListField(null=True)
    daily_report = ListField(null=True)
    findings = DictField(null=True)  # {category: [finding_strings]}
    session_analysis = DictField(null=True)
    reflections = ListField(null=True)  # list of reflection dicts
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
            current_step=1,
            completed_steps=[],
            daily_report=[],
            findings={},
            session_analysis={},
            reflections=[],
            auto_fix_attempts=[],
            step_progress={},
            started_at=time.time(),
            dry_run=dry_run,
        )

    def save_checkpoint(self) -> None:
        """Save current state. Delete and recreate for KeyField safety."""
        data = {
            "date": self.date,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps or [],
            "daily_report": self.daily_report or [],
            "findings": self.findings or {},
            "session_analysis": self.session_analysis or {},
            "reflections": self.reflections or [],
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


class ReflectionIgnore(Model):
    """An ignored bug pattern with automatic TTL-based expiry.

    The expires_at SortedField enables efficient range queries to
    find/prune expired entries.
    """

    ignore_id = AutoKeyField()
    pattern = KeyField()  # pattern string to match against
    reason = Field(null=True, max_length=500)
    created_at = SortedField(type=float)
    expires_at = SortedField(type=float)  # Unix timestamp when this expires

    @classmethod
    def add_ignore(cls, pattern: str, reason: str = "", days: int = 14) -> "ReflectionIgnore":
        """Add a new ignore entry that expires after `days` days."""
        now = time.time()
        return cls.create(
            pattern=pattern,
            reason=reason,
            created_at=now,
            expires_at=now + (days * 86400),
        )

    @classmethod
    def get_active(cls) -> list["ReflectionIgnore"]:
        """Return all non-expired ignore entries."""
        now = time.time()
        all_entries = cls.query.all()
        return [e for e in all_entries if e.expires_at and e.expires_at > now]

    @classmethod
    def cleanup_expired(cls) -> int:
        """Delete expired ignore entries. Returns count deleted."""
        now = time.time()
        all_entries = cls.query.all()
        deleted = 0
        for entry in all_entries:
            if entry.expires_at and entry.expires_at <= now:
                entry.delete()
                deleted += 1
        return deleted

    @classmethod
    def is_ignored(cls, pattern: str) -> bool:
        """Check if a pattern matches any active ignore entry (case-insensitive)."""
        pattern_lower = pattern.lower()
        for entry in cls.get_active():
            entry_pattern = (entry.pattern or "").lower()
            if entry_pattern and (entry_pattern in pattern_lower or pattern_lower in entry_pattern):
                return True
        return False


class LessonLearned(Model):
    """A learned lesson from session reflection.

    Replaces data/lessons_learned.jsonl with a queryable Redis model.
    Keyed by category for filtering. Deduplication by pattern field.
    """

    lesson_id = AutoKeyField()
    date = KeyField()  # YYYY-MM-DD when the lesson was recorded
    category = KeyField()  # misunderstanding, code_bug, poor_planning, etc.
    summary = Field(max_length=2000)
    pattern = Field(max_length=2000)  # the recurring pattern
    prevention = Field(null=True, max_length=2000)
    source_session = Field(null=True, max_length=200)
    validated = IntField(default=0)  # 0 = unvalidated, 1+ = validated N times
    created_at = SortedField(type=float)

    @classmethod
    def add_lesson(
        cls,
        date: str,
        category: str,
        summary: str,
        pattern: str,
        prevention: str = "",
        source_session: str = "",
    ) -> "LessonLearned | None":
        """Add a lesson, skipping if the pattern already exists.

        Returns the created LessonLearned or None if it was a duplicate.
        """
        # Deduplicate by exact pattern match
        existing = cls.query.all()
        for lesson in existing:
            if lesson.pattern == pattern:
                return None

        return cls.create(
            date=date,
            category=category,
            summary=summary,
            pattern=pattern,
            prevention=prevention,
            source_session=source_session,
            validated=0,
            created_at=time.time(),
        )

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete lessons older than max_age_days. Returns count deleted."""
        cutoff = time.time() - (max_age_days * 86400)
        all_lessons = cls.query.all()
        deleted = 0
        for lesson in all_lessons:
            if lesson.created_at and lesson.created_at < cutoff:
                lesson.delete()
                deleted += 1
        return deleted

    @classmethod
    def get_recent(cls, days: int = 90) -> list["LessonLearned"]:
        """Get lessons from the last N days."""
        cutoff = time.time() - (days * 86400)
        all_lessons = cls.query.all()
        return [
            lesson for lesson in all_lessons if lesson.created_at and lesson.created_at > cutoff
        ]
