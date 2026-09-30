"""PRReviewAudit model - deduplication tracker for PR review audit findings.

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    AutoKeyField,
    Field,
    IntField,
    KeyField,
    Model,
    SortedField,
    UniqueKeyField,
)


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
