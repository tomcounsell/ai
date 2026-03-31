"""Tests for SDLC pipeline integrity fixes.

Covers:
A. Session continuation fallback metadata preservation
B. Deterministic URL construction in Observer
C. Merge guard hook blocking
"""

import json
import os
import subprocess
import sys

from utils.github_patterns import construct_canonical_url as _construct_canonical_url


class TestCanonicalUrlConstruction:
    """Test deterministic URL construction from worker-provided URLs."""

    def test_correct_repo_pr_url_preserved(self):
        url = "https://github.com/tomcounsell/ai/pull/42"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/42"

    def test_correct_repo_issue_url_preserved(self):
        url = "https://github.com/tomcounsell/ai/issues/17"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/17"

    def test_wrong_repo_url_corrected(self):
        """Worker provided wrong repo — number extracted, correct repo used."""
        url = "https://github.com/wrong-org/wrong-repo/pull/99"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/99"

    def test_wrong_repo_issue_url_corrected(self):
        url = "https://github.com/other-org/other-repo/issues/5"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/5"

    def test_none_url_returns_none(self):
        assert _construct_canonical_url(None, "tomcounsell/ai") is None

    def test_empty_string_returns_none(self):
        assert _construct_canonical_url("", "tomcounsell/ai") is None

    def test_whitespace_only_returns_none(self):
        assert _construct_canonical_url("   ", "tomcounsell/ai") is None

    def test_non_github_url_returns_none(self):
        """Non-GitHub URLs have no extractable number."""
        result = _construct_canonical_url("https://example.com/page", "tomcounsell/ai")
        assert result is None

    def test_github_url_without_number_returns_none(self):
        result = _construct_canonical_url("https://github.com/tomcounsell/ai", "tomcounsell/ai")
        assert result is None

    def test_no_gh_repo_returns_none(self):
        url = "https://github.com/tomcounsell/ai/pull/42"
        assert _construct_canonical_url(url, None) is None
        assert _construct_canonical_url(url, "") is None

    def test_pr_url_takes_priority_over_issue_path(self):
        """URLs with /pull/ should produce PR URLs."""
        url = "https://github.com/org/repo/pull/100"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/100"

    def test_url_with_trailing_whitespace(self):
        url = "  https://github.com/org/repo/issues/7  "
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/7"


class TestMergeGuardHook:
    """Test the merge guard PreToolUse hook."""

    HOOK_PATH = ".claude/hooks/validators/validate_merge_guard.py"

    def _run_hook(self, tool_name: str, command: str) -> dict | None:
        """Run the hook with given input and return parsed output or None."""
        hook_input = json.dumps(
            {
                "tool_name": tool_name,
                "tool_input": {"command": command},
            }
        )
        result = subprocess.run(
            [sys.executable, self.HOOK_PATH],
            input=hook_input,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        assert result.returncode == 0, f"Hook failed: {result.stderr}"
        if result.stdout.strip():
            return json.loads(result.stdout.strip())
        return None

    def test_blocks_gh_pr_merge(self):
        result = self._run_hook("Bash", "gh pr merge 42")
        assert result is not None
        assert result["decision"] == "block"
        assert "human authorization" in result["reason"]

    def test_blocks_gh_pr_merge_with_flags(self):
        result = self._run_hook("Bash", "gh pr merge 42 --squash")
        assert result is not None
        assert result["decision"] == "block"

    def test_allows_gh_pr_merge_help(self):
        result = self._run_hook("Bash", "gh pr merge --help")
        assert result is None

    def test_allows_echo_containing_merge(self):
        result = self._run_hook("Bash", 'echo "gh pr merge"')
        assert result is None

    def test_allows_non_bash_tool(self):
        result = self._run_hook("Read", "gh pr merge 42")
        assert result is None

    def test_allows_unrelated_command(self):
        result = self._run_hook("Bash", "git status")
        assert result is None

    def test_allows_gh_pr_list(self):
        result = self._run_hook("Bash", "gh pr list")
        assert result is None

    def test_blocks_merge_in_pipeline(self):
        result = self._run_hook("Bash", "cd repo && gh pr merge 10 --merge")
        assert result is not None
        assert result["decision"] == "block"

    def test_allows_authorized_merge(self, tmp_path, monkeypatch):
        """Merge is allowed when authorization file exists."""
        # Create the data dir and auth file in the project root
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        auth_file = os.path.join(project_root, "data", "merge_authorized_42")
        os.makedirs(os.path.dirname(auth_file), exist_ok=True)
        try:
            with open(auth_file, "w") as f:
                f.write("")
            result = self._run_hook("Bash", "gh pr merge 42 --squash")
            assert result is None  # Allowed
        finally:
            os.unlink(auth_file)

    def test_blocks_unauthorized_merge(self):
        """Merge is blocked when no authorization file exists."""
        # Ensure no auth file exists
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        auth_file = os.path.join(project_root, "data", "merge_authorized_999")
        if os.path.exists(auth_file):
            os.unlink(auth_file)
        result = self._run_hook("Bash", "gh pr merge 999 --squash")
        assert result is not None
        assert result["decision"] == "block"

    def test_blocks_merge_without_pr_number(self):
        """Merge without a PR number is blocked (can't check authorization)."""
        result = self._run_hook("Bash", "gh pr merge --squash")
        assert result is not None
        assert result["decision"] == "block"


class TestEnqueueContinuationFallback:
    """Test that the fallback path preserves session metadata."""

    def test_extract_agent_session_fields_includes_metadata(self):
        """Verify _AGENT_SESSION_FIELDS includes all critical session metadata."""
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        critical_fields = [
            "context_summary",
            "expectations",
            "issue_url",
            "pr_url",
            "history",
            "correlation_id",
            "classification_type",
            "work_item_slug",
        ]
        for field in critical_fields:
            assert field in _AGENT_SESSION_FIELDS, (
                f"Critical field {field!r} missing from _AGENT_SESSION_FIELDS"
            )

    def test_diagnose_missing_session_returns_dict(self):
        """Verify _diagnose_missing_session returns diagnostic info."""
        from agent.agent_session_queue import _diagnose_missing_session

        result = _diagnose_missing_session("nonexistent-session-id-12345")
        assert isinstance(result, dict)
        # Should either have matching_keys or error (if Redis not available)
        assert "matching_keys" in result or "error" in result


class TestMergeStageTracking:
    """Test MERGE stage is properly tracked across modules."""

    def test_merge_in_display_stages(self):
        from bridge.pipeline_graph import DISPLAY_STAGES

        assert "MERGE" in DISPLAY_STAGES

    def test_merge_in_stage_to_skill(self):
        from bridge.pipeline_graph import STAGE_TO_SKILL

        assert "MERGE" in STAGE_TO_SKILL
        assert STAGE_TO_SKILL["MERGE"] == "/do-merge"

    def test_merge_in_stage_constants(self):
        from models.agent_session import SDLC_STAGES

        assert "MERGE" in SDLC_STAGES

    def test_docs_routes_to_merge(self):
        from bridge.pipeline_graph import get_next_stage

        result = get_next_stage("DOCS", "success")
        assert result == ("MERGE", "/do-merge")

    def test_merge_skill_mapped(self):
        from bridge.pipeline_graph import STAGE_TO_SKILL

        assert STAGE_TO_SKILL["MERGE"] == "/do-merge"
