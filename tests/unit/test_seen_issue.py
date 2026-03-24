"""Unit tests for models/seen_issue.py SeenIssue model.

Tests the Popoto model that tracks which GitHub issues have been processed
by the issue poller. Covers get_or_create, mark, and is_seen operations.
"""

from models.seen_issue import SeenIssue


class TestSeenIssue:
    """Tests for the SeenIssue Popoto model."""

    def setup_method(self):
        """Clean up test records before each test."""
        for record in SeenIssue.query.all():
            if "test_seen/" in str(record.repo_key):
                record.delete()

    def teardown_method(self):
        """Clean up test records after each test."""
        for record in SeenIssue.query.all():
            if "test_seen/" in str(record.repo_key):
                record.delete()

    def test_get_or_create_new(self):
        """get_or_create returns a new record for unseen repo."""
        record = SeenIssue.get_or_create("test_seen", "new_repo")
        assert record.repo_key == "test_seen/new_repo"
        assert record.issue_numbers == set()

    def test_get_or_create_existing(self):
        """get_or_create returns existing record for known repo."""
        SeenIssue.create(repo_key="test_seen/existing", issue_numbers={"10", "20"})
        record = SeenIssue.get_or_create("test_seen", "existing")
        assert "10" in record.issue_numbers
        assert "20" in record.issue_numbers

    def test_mark_adds_issue(self):
        """mark adds an issue number and persists it."""
        record = SeenIssue.get_or_create("test_seen", "mark_repo")
        record.mark(42)
        # Reload to verify persistence
        reloaded = SeenIssue.get_or_create("test_seen", "mark_repo")
        assert "42" in reloaded.issue_numbers

    def test_mark_multiple_issues(self):
        """mark can add multiple issues to the same repo."""
        record = SeenIssue.get_or_create("test_seen", "multi_repo")
        record.mark(1)
        record.mark(2)
        record.mark(3)
        reloaded = SeenIssue.get_or_create("test_seen", "multi_repo")
        assert reloaded.is_seen(1)
        assert reloaded.is_seen(2)
        assert reloaded.is_seen(3)

    def test_is_seen_true(self):
        """is_seen returns True for a marked issue."""
        record = SeenIssue.get_or_create("test_seen", "seen_repo")
        record.mark(99)
        assert record.is_seen(99) is True

    def test_is_seen_false(self):
        """is_seen returns False for an unmarked issue."""
        record = SeenIssue.get_or_create("test_seen", "unseen_repo")
        assert record.is_seen(999) is False

    def test_issue_numbers_stored_as_strings(self):
        """Issue numbers are stored as strings internally."""
        record = SeenIssue.get_or_create("test_seen", "str_repo")
        record.mark(42)
        assert "42" in record.issue_numbers

    def test_no_ttl(self):
        """SeenIssue should not have a TTL (persistent tracking)."""
        assert not hasattr(SeenIssue._meta, "ttl") or SeenIssue._meta.ttl is None

    def test_separate_repos_independent(self):
        """Different repos have independent issue tracking."""
        record_a = SeenIssue.get_or_create("test_seen", "repo_a")
        SeenIssue.get_or_create("test_seen", "repo_b")
        record_a.mark(1)
        # repo_b should not see repo_a's issues
        reloaded_b = SeenIssue.get_or_create("test_seen", "repo_b")
        assert reloaded_b.is_seen(1) is False
