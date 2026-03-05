"""Unit tests for SDLC enforcement in agent/sdk_client.py.

Covers:
- SDLC_WORKFLOW constant exists and contains mandatory pipeline text
- load_system_prompt() injects SDLC_WORKFLOW between SOUL.md and completion criteria
- _check_no_direct_main_push(): code on main → hard-blocked
- _check_no_direct_main_push(): docs-only on main → allowed
- _check_no_direct_main_push(): code on feature branch → allowed
- _check_no_direct_main_push(): no state file → allowed
- _check_no_direct_main_push(): modified_on_branch=session/* + main → no violation (merge)
- _check_no_direct_main_push(): modified_on_branch=main + main → violation (direct push)
- _check_no_direct_main_push(): no modified_on_branch + main → violation (backward compat)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

# Mock claude_agent_sdk before importing agent.sdk_client to avoid
# dependency issues (mcp.types.ToolAnnotations import error).
if "claude_agent_sdk" not in sys.modules:

    class _MockSDK(ModuleType):
        """Auto-mock module: returns a MagicMock for any attribute access."""

        def __getattr__(self, name):
            return MagicMock()

    sys.modules["claude_agent_sdk"] = _MockSDK("claude_agent_sdk")

from agent.sdk_client import (  # noqa: E402
    SDLC_WORKFLOW,
    _check_no_direct_main_push,
    load_system_prompt,
)

# ---------------------------------------------------------------------------
# SDLC_WORKFLOW constant
# ---------------------------------------------------------------------------


class TestSdlcWorkflowConstant:
    def test_constant_exists(self):
        """SDLC_WORKFLOW module constant must exist."""
        assert SDLC_WORKFLOW is not None

    def test_constant_is_string(self):
        """SDLC_WORKFLOW must be a non-empty string."""
        assert isinstance(SDLC_WORKFLOW, str)
        assert len(SDLC_WORKFLOW) > 0

    def test_contains_mandatory_pipeline_header(self):
        """Must contain the 'Mandatory Development Pipeline' heading."""
        assert "MANDATORY Development Pipeline" in SDLC_WORKFLOW

    def test_contains_never_push_to_main(self):
        """Must contain instruction not to push to main."""
        assert "NEVER" in SDLC_WORKFLOW
        assert "main" in SDLC_WORKFLOW

    def test_contains_do_plan_and_do_build(self):
        """Must reference /do-plan and /do-build skills."""
        assert "/do-plan" in SDLC_WORKFLOW
        assert "/do-build" in SDLC_WORKFLOW

    def test_contains_issue_step(self):
        """Must mandate a GitHub issue step."""
        assert "ISSUE" in SDLC_WORKFLOW or "issue" in SDLC_WORKFLOW.lower()

    def test_distinguishes_code_from_docs(self):
        """Must carve out docs/plan changes as allowed directly to main."""
        assert ".md" in SDLC_WORKFLOW or "doc" in SDLC_WORKFLOW.lower()
        assert ".py" in SDLC_WORKFLOW


# ---------------------------------------------------------------------------
# load_system_prompt() — SDLC_WORKFLOW injection
# ---------------------------------------------------------------------------


class TestLoadSystemPromptInjection:
    def test_sdlc_workflow_present_in_prompt(self):
        """load_system_prompt() must include SDLC_WORKFLOW text."""
        prompt = load_system_prompt()
        assert "MANDATORY Development Pipeline" in prompt

    def test_sdlc_workflow_is_between_soul_and_criteria(self):
        """SDLC_WORKFLOW must appear after SOUL.md and before completion criteria."""
        prompt = load_system_prompt()
        sdlc_pos = prompt.find("MANDATORY Development Pipeline")
        assert sdlc_pos > 0, "SDLC_WORKFLOW not found in prompt"

        # SOUL.md starts with '# Valor' — check it precedes SDLC
        soul_pos = prompt.find("# Valor")
        assert soul_pos >= 0, "SOUL.md content '# Valor' not found in prompt"
        assert soul_pos < sdlc_pos, "SOUL.md content must come before SDLC_WORKFLOW"

        # Completion criteria section starts with 'Work is DONE'
        criteria_pos = prompt.find("Work is DONE")
        if criteria_pos > 0:
            assert sdlc_pos < criteria_pos, (
                "SDLC_WORKFLOW must appear before Work Completion Criteria"
            )

    def test_prompt_contains_separator_before_sdlc(self):
        """load_system_prompt() must use --- separator before SDLC section."""
        prompt = load_system_prompt()
        # Find position of SDLC content and verify --- appears just before it
        sdlc_pos = prompt.find("MANDATORY Development Pipeline")
        assert sdlc_pos > 0
        preceding = prompt[max(0, sdlc_pos - 50) : sdlc_pos]
        assert "---" in preceding, "Separator '---' must precede SDLC_WORKFLOW block"


# ---------------------------------------------------------------------------
# _check_no_direct_main_push() behavioural tests
# ---------------------------------------------------------------------------


def _write_state(sessions_dir: Path, session_id: str, state: dict) -> None:
    """Write a sdlc_state.json for the given session_id."""
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "sdlc_state.json").write_text(json.dumps(state))


class TestCheckNoDirectMainPush:
    """Behavioural tests for _check_no_direct_main_push()."""

    def _run_check(
        self,
        tmp_path: Path,
        session_id: str,
        state: dict | None,
        branch: str,
    ) -> str | None:
        """Helper: write state, mock git branch, run check."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        if state is not None:
            _write_state(sessions_dir, session_id, state)

        repo_root = tmp_path

        # Mock git rev-parse to return the desired branch
        mock_result = MagicMock()
        mock_result.stdout = branch + "\n"

        with patch("subprocess.run", return_value=mock_result):
            return _check_no_direct_main_push(session_id, repo_root=repo_root)

    def test_no_state_file_returns_none(self, tmp_path):
        """Non-code session: no sdlc_state.json → always passes (None)."""
        result = self._run_check(tmp_path, "ghost-session", state=None, branch="main")
        assert result is None

    def test_code_on_main_returns_error(self, tmp_path):
        """Code modified on main branch → hard-block error message."""
        result = self._run_check(
            tmp_path,
            "bad-session",
            state={
                "code_modified": True,
                "files": ["bridge/telegram_bridge.py"],
            },
            branch="main",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result
        assert "main" in result

    def test_code_on_main_error_contains_remediation(self, tmp_path):
        """Error message must tell the developer how to fix the violation."""
        result = self._run_check(
            tmp_path,
            "bad-session",
            state={"code_modified": True, "files": ["foo.py"]},
            branch="main",
        )
        assert result is not None
        assert "session/" in result or "branch" in result.lower()
        assert "pr" in result.lower() or "PR" in result

    def test_code_on_feature_branch_returns_none(self, tmp_path):
        """Code on session/{slug} branch → not blocked (inside /do-build)."""
        result = self._run_check(
            tmp_path,
            "good-session",
            state={"code_modified": True, "files": ["agent/sdk_client.py"]},
            branch="session/my-feature",
        )
        assert result is None

    def test_docs_only_on_main_returns_none(self, tmp_path):
        """Docs-only session on main → allowed (code_modified=False)."""
        result = self._run_check(
            tmp_path,
            "docs-session",
            state={
                "code_modified": False,
                "files": ["docs/features/something.md"],
            },
            branch="main",
        )
        assert result is None

    def test_modified_files_listed_in_error(self, tmp_path):
        """Error message must list the modified files."""
        result = self._run_check(
            tmp_path,
            "bad-session",
            state={
                "code_modified": True,
                "files": ["bridge/telegram_bridge.py", "agent/sdk_client.py"],
            },
            branch="main",
        )
        assert result is not None
        assert "telegram_bridge.py" in result
        assert "sdk_client.py" in result

    def test_corrupt_state_file_returns_none(self, tmp_path):
        """Corrupt state file → fail open, return None (do not block session)."""
        sessions_dir = tmp_path / "data" / "sessions"
        session_dir = sessions_dir / "corrupt-session"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "sdlc_state.json").write_text("not valid json {{{{")

        mock_result = MagicMock()
        mock_result.stdout = "main\n"
        with patch("subprocess.run", return_value=mock_result):
            result = _check_no_direct_main_push("corrupt-session", repo_root=tmp_path)
        assert result is None

    def test_git_command_failure_returns_none(self, tmp_path):
        """Git command failure → fail open, return None."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        _write_state(
            sessions_dir,
            "git-fail-session",
            {"code_modified": True, "files": ["foo.py"]},
        )
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = _check_no_direct_main_push("git-fail-session", repo_root=tmp_path)
        assert result is None

    # -----------------------------------------------------------------------
    # modified_on_branch: merge scenario vs. direct push
    # -----------------------------------------------------------------------

    def test_modified_on_session_branch_now_on_main_returns_none(self, tmp_path):
        """Code modified on session/foo, now on main → arrived via merge, no violation."""
        result = self._run_check(
            tmp_path,
            "merged-session",
            state={
                "code_modified": True,
                "modified_on_branch": "session/stop-hook-fix",
                "files": ["agent/sdk_client.py"],
            },
            branch="main",
        )
        assert result is None

    def test_modified_on_main_now_on_main_returns_violation(self, tmp_path):
        """Code modified on main, still on main → direct push, violation."""
        result = self._run_check(
            tmp_path,
            "direct-push-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["agent/sdk_client.py"],
            },
            branch="main",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result

    def test_no_modified_on_branch_legacy_returns_violation(self, tmp_path):
        """Legacy state without modified_on_branch, on main → violation (backward compat)."""
        result = self._run_check(
            tmp_path,
            "legacy-session",
            state={
                "code_modified": True,
                "files": ["bridge/telegram_bridge.py"],
                # No modified_on_branch — legacy state
            },
            branch="main",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result
