"""SeenIssue model - tracks which GitHub issues have been processed by the poller.

Replaces the raw Redis sets in scripts/issue_poller.py with a Popoto model.
Each org/repo combination gets its own SeenIssue record storing the set of
processed issue numbers. No TTL - seen issues are tracked persistently.
"""

from popoto import KeyField, Model, SetField


class SeenIssue(Model):
    """Tracks processed GitHub issue numbers per repository.

    Used by scripts/issue_poller.py to avoid re-processing issues
    that have already been seen. Each org/repo pair gets one record.

    Fields:
        repo_key: Repository identifier in "org/repo" format
        issue_numbers: Set of issue number strings that have been processed
    """

    repo_key = KeyField()
    issue_numbers = SetField(default=set)

    @classmethod
    def get_or_create(cls, org: str, repo: str) -> "SeenIssue":
        """Get existing record for a repo, or create a new one."""
        key = f"{org}/{repo}"
        existing = cls.query.filter(repo_key=key)
        if existing:
            return existing[0]
        return cls.create(repo_key=key, issue_numbers=set())

    def mark(self, issue_number: int) -> None:
        """Mark an issue as seen (processed)."""
        self.issue_numbers.add(str(issue_number))
        self.save()

    def is_seen(self, issue_number: int) -> bool:
        """Check if an issue has already been processed."""
        return str(issue_number) in self.issue_numbers
