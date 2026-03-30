"""Reflections Popoto models - Redis-backed state for the reflections maintenance system.

Three models:
- ReflectionRun: per-run state with resumability (one per day)
- ReflectionIgnore: auto-fix suppression with TTL-based auto-expiry
- PRReviewAudit: deduplication tracker for PR review audit findings

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
    project_key scopes runs to a specific project.
    """

    date = UniqueKeyField()  # YYYY-MM-DD, one run per day
    project_key = KeyField(null=True)
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
            "project_key": self.project_key,
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


class PRReviewAudit(Model):
    """Deduplication tracker for PR review audit findings.

    Keyed by a composite of repo:pr_number:comment_id:finding_index to prevent
    re-filing GitHub issues for already-audited review findings. Each record
    stores the severity classification and the URL of the filed issue (if any).

    A single review comment may contain multiple structured findings. The
    finding_index ensures each finding is tracked independently, preventing
    the case where auditing one finding silently marks others as audited.

    Fields:
        audit_id: Auto-generated unique key
        repo: GitHub repo slug (e.g. "tomcounsell/ai")
        pr_number: Pull request number
        comment_id: Composite dedup key "{repo}:{pr_number}:{comment_id}:{finding_index}"
        severity: Classified severity (critical, standard, trivial)
        filed_issue_url: URL of the filed GitHub issue, if any
        audited_at: Timestamp when this comment was audited (for TTL cleanup)
    """

    audit_id = AutoKeyField()
    repo = KeyField()
    pr_number = IntField()
    comment_id = UniqueKeyField()  # Dedup key: "{repo}:{pr_number}:{comment_id}:{finding_index}"
    severity = Field(null=True)
    filed_issue_url = Field(null=True)
    audited_at = SortedField(type=float)

    @classmethod
    def is_audited(cls, comment_key: str) -> bool:
        """Check if a review finding has already been audited.

        Args:
            comment_key: Composite key in format "{repo}:{pr_number}:{comment_id}:{finding_index}"
        """
        existing = cls.query.filter(comment_id=comment_key)
        return bool(existing)

    @classmethod
    def mark_audited(
        cls,
        comment_key: str,
        repo: str,
        pr_number: int,
        severity: str,
        issue_url: str | None = None,
    ) -> "PRReviewAudit":
        """Record a review finding as audited.

        Args:
            comment_key: Composite key in format "{repo}:{pr_number}:{comment_id}:{finding_index}"
            repo: GitHub repo slug
            pr_number: Pull request number
            severity: Classified severity (critical, standard, trivial)
            issue_url: URL of the filed GitHub issue, if any
        """
        return cls.create(
            repo=repo,
            pr_number=pr_number,
            comment_id=comment_key,
            severity=severity,
            filed_issue_url=issue_url,
            audited_at=time.time(),
        )

    @classmethod
    def last_successful_run(cls) -> float | None:
        """Return the most recent audited_at timestamp, or None if no audits exist.

        Used to determine the PR time window for the next audit run.
        """
        all_audits = cls.query.all()
        if not all_audits:
            return None
        latest = max(
            (a.audited_at for a in all_audits if a.audited_at),
            default=None,
        )
        return latest

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete audit records older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        all_audits = cls.query.all()
        deleted = 0
        for audit in all_audits:
            if audit.audited_at and audit.audited_at < cutoff:
                audit.delete()
                deleted += 1
        return deleted
