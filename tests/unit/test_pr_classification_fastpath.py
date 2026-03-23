"""Tests for PR/issue reference fast-path SDLC classification.

Verifies that messages containing PR or issue references are synchronously
classified as SDLC work before enqueue, preventing the race condition where
the async classifier loses the race and defaults to "question".

See: PR #478 postmortem — "Complete PR 478" was classified as "question"
because the async classifier hadn't finished before the job was picked up.
"""

import re

# The fast-path regex used in both bridge/telegram_bridge.py and agent/sdk_client.py
PR_ISSUE_PATTERN = r"(?:issue|pr|pull request)\s+#?\d+"
BARE_ISSUE_PATTERN = r"^#\d+$"


def _is_sdlc_fast_path(text: str) -> bool:
    """Reproduce the fast-path check from bridge and sdk_client."""
    lower = text.strip().lower()
    return bool(re.search(PR_ISSUE_PATTERN, lower) or re.match(BARE_ISSUE_PATTERN, lower))


class TestPrIssueFastPath:
    """Messages with PR/issue references must always classify as SDLC."""

    def test_complete_pr_number(self):
        assert _is_sdlc_fast_path("Complete PR 478")

    def test_complete_pr_hash(self):
        assert _is_sdlc_fast_path("Complete PR #478")

    def test_merge_pr(self):
        assert _is_sdlc_fast_path("merge PR 123")

    def test_review_pr(self):
        assert _is_sdlc_fast_path("review PR #99")

    def test_fix_issue(self):
        assert _is_sdlc_fast_path("fix issue 463")

    def test_issue_hash(self):
        assert _is_sdlc_fast_path("issue #463")

    def test_continue_issue(self):
        assert _is_sdlc_fast_path("continue issue 463")

    def test_pull_request_full(self):
        assert _is_sdlc_fast_path("check pull request #200")

    def test_bare_issue_number(self):
        assert _is_sdlc_fast_path("#471")

    def test_bare_issue_with_whitespace(self):
        assert _is_sdlc_fast_path("  #471  ")

    def test_plain_question_not_sdlc(self):
        assert not _is_sdlc_fast_path("how does the bridge work?")

    def test_plain_command_not_sdlc(self):
        assert not _is_sdlc_fast_path("restart the bridge")

    def test_number_without_prefix_not_sdlc(self):
        assert not _is_sdlc_fast_path("478")

    def test_pr_without_number_not_sdlc(self):
        assert not _is_sdlc_fast_path("check the PR")

    def test_case_insensitive(self):
        assert _is_sdlc_fast_path("COMPLETE PR 478")
        assert _is_sdlc_fast_path("Fix Issue #100")


class TestClassifyWorkRequestIntegration:
    """Verify classify_work_request from routing.py handles PR/issue references."""

    def test_pr_reference_returns_sdlc(self):
        from bridge.routing import classify_work_request

        assert classify_work_request("Complete PR 478") == "sdlc"

    def test_issue_reference_returns_sdlc(self):
        from bridge.routing import classify_work_request

        assert classify_work_request("fix issue #463") == "sdlc"

    def test_bare_issue_returns_sdlc(self):
        from bridge.routing import classify_work_request

        assert classify_work_request("#471") == "sdlc"

    def test_plain_question_not_sdlc(self):
        from bridge.routing import classify_work_request

        result = classify_work_request("what time is it?")
        assert result in ("question", "passthrough")
