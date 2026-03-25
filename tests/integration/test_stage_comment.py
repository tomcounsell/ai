"""Integration test for SDLC stage comment posting and reading.

Posts a comment to a real GitHub issue, fetches it back, and verifies
the format is correct. Uses the actual `gh` CLI.
"""

import pytest

from utils.issue_comments import (
    STAGE_COMMENT_MARKER,
    fetch_stage_comments,
    format_prior_context,
    format_stage_comment,
    post_stage_comment,
)

# Use the tracking issue for this feature as the test target
TEST_ISSUE_NUMBER = 520
TEST_REPO = "tomcounsell/ai"


@pytest.mark.integration
class TestStageCommentIntegration:
    """Integration tests that post and read real GitHub comments."""

    def test_post_and_fetch_stage_comment(self):
        """Post a stage comment and verify it can be fetched back."""
        # Post a test comment
        success = post_stage_comment(
            issue_number=TEST_ISSUE_NUMBER,
            stage="INTEGRATION_TEST",
            outcome="Automated test verification",
            findings=["Comment posting works", "Comment fetching works"],
            files=["utils/issue_comments.py"],
            notes="This comment was posted by an automated integration test.",
            repo=TEST_REPO,
        )
        assert success, "Failed to post stage comment"

        # Fetch it back
        comments = fetch_stage_comments(TEST_ISSUE_NUMBER, repo=TEST_REPO)
        assert len(comments) > 0, "No stage comments found after posting"

        # Find our test comment
        test_comments = [c for c in comments if c["stage"] == "INTEGRATION_TEST"]
        assert len(test_comments) > 0, "Test comment not found in fetched comments"

        latest = test_comments[-1]
        assert latest["stage"] == "INTEGRATION_TEST"
        assert latest["outcome"] == "Automated test verification"

    def test_format_prior_context_with_real_comments(self):
        """Verify format_prior_context produces readable output."""
        comments = fetch_stage_comments(TEST_ISSUE_NUMBER, repo=TEST_REPO)
        if not comments:
            pytest.skip("No stage comments on issue to test with")

        context = format_prior_context(comments)
        assert "Prior Stage Findings" in context
        assert len(context) > 0

    def test_comment_contains_marker(self):
        """Verify posted comments contain the machine-readable marker."""
        comments = fetch_stage_comments(TEST_ISSUE_NUMBER, repo=TEST_REPO)
        if not comments:
            pytest.skip("No stage comments on issue")

        for c in comments:
            assert STAGE_COMMENT_MARKER in c["body"]

    def test_format_stage_comment_is_valid_markdown(self):
        """Verify the comment format is valid markdown with proper structure."""
        body = format_stage_comment(
            stage="BUILD",
            outcome="PR #123 opened",
            findings=["Fixed auth middleware", "Added retry logic"],
            files=["agent/hooks/subagent_stop.py", "utils/issue_comments.py"],
            notes="Review should check error handling paths.",
        )

        # Verify structure
        assert body.startswith(STAGE_COMMENT_MARKER)
        assert "## Stage: BUILD" in body
        assert "**Outcome:** PR #123 opened" in body
        assert "### Key Findings" in body
        assert "### Files Modified" in body
        assert "### Notes for Next Stage" in body
