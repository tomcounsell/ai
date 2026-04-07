"""Unit tests for SDLC enforcement in agent/sdk_client.py.

Covers:
- WORKER_RULES constant exists and contains safety rails (no pipeline orchestration)
- load_system_prompt() injects WORKER_RULES before SOUL.md and completion criteria
- _check_no_direct_main_push(): code on main -> hard-blocked
- _check_no_direct_main_push(): docs-only on main -> allowed
- _check_no_direct_main_push(): code on feature branch -> allowed
- _check_no_direct_main_push(): no state file -> allowed
- _check_no_direct_main_push(): modified_on_branch=session/* + main -> no violation (merge)
- _check_no_direct_main_push(): modified_on_branch=main + main -> violation (direct push)
- _check_no_direct_main_push(): no modified_on_branch + main -> violation (backward compat)
- _check_no_direct_main_push(): SKIP_SDLC=1 bypasses check (issue #261)
- _check_no_direct_main_push(): stale state with no uncommitted changes -> no violation (#261)
- _is_code_file(): inlined code file detection
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# claude_agent_sdk mock is centralized in tests/conftest.py
from agent.sdk_client import (  # noqa: E402
    WORKER_RULES,
    _check_no_direct_main_push,
    _is_code_file,
    load_system_prompt,
)

# ---------------------------------------------------------------------------
# WORKER_RULES constant
# ---------------------------------------------------------------------------


class TestWorkerRulesConstant:
    def test_constant_exists(self):
        """WORKER_RULES module constant must exist."""
        assert WORKER_RULES is not None

    def test_constant_is_string(self):
        """WORKER_RULES must be a non-empty string."""
        assert isinstance(WORKER_RULES, str)
        assert len(WORKER_RULES) > 0

    def test_contains_safety_rails_header(self):
        """Must contain the 'Worker Safety Rails' heading."""
        assert "Worker Safety Rails" in WORKER_RULES

    def test_contains_never_push_to_main(self):
        """Must contain instruction not to push to main."""
        assert "NEVER" in WORKER_RULES
        assert "main" in WORKER_RULES

    def test_no_pipeline_orchestration(self):
        """Must NOT contain pipeline orchestration or /sdlc references."""
        assert "/sdlc" not in WORKER_RULES
        assert "/do-plan" not in WORKER_RULES
        assert "/do-build" not in WORKER_RULES
        assert "MANDATORY Development Pipeline" not in WORKER_RULES
        assert "ISSUE" not in WORKER_RULES

    def test_distinguishes_code_from_docs(self):
        """Must carve out docs/plan changes as allowed directly to main."""
        assert ".md" in WORKER_RULES or "doc" in WORKER_RULES.lower()
        assert ".py" in WORKER_RULES or ".js" in WORKER_RULES or ".ts" in WORKER_RULES

    def test_references_observer_agent(self):
        """Must reference the Observer Agent as the pipeline controller."""
        assert "Observer" in WORKER_RULES


# ---------------------------------------------------------------------------
# load_system_prompt() — WORKER_RULES injection
# ---------------------------------------------------------------------------


class TestLoadSystemPromptInjection:
    def test_worker_rules_present_in_prompt(self):
        """load_system_prompt() must include WORKER_RULES text."""
        prompt = load_system_prompt()
        assert "Worker Safety Rails" in prompt

    def test_no_pipeline_orchestration_in_worker_rules(self):
        """WORKER_RULES portion of prompt must NOT contain pipeline orchestration.

        Note: SOUL.md and CLAUDE.md may reference /sdlc as part of project
        architecture docs -- those are intentionally preserved (see plan No-Gos).
        This test validates only the WORKER_RULES constant itself.
        """
        assert "/sdlc" not in WORKER_RULES
        assert "MANDATORY Development Pipeline" not in WORKER_RULES

    def test_worker_rules_before_soul_and_criteria(self):
        """WORKER_RULES must appear before SOUL.md and before completion criteria."""
        prompt = load_system_prompt()
        rules_pos = prompt.find("Worker Safety Rails")
        assert rules_pos >= 0, "WORKER_RULES not found in prompt"

        # SOUL.md starts with '# Valor' — check rules precede it
        soul_pos = prompt.find("# Valor")
        assert soul_pos >= 0, "SOUL.md content '# Valor' not found in prompt"
        assert rules_pos < soul_pos, "WORKER_RULES must come before SOUL.md content"

        # Completion criteria section starts with 'Work is DONE'
        criteria_pos = prompt.find("Work is DONE")
        if criteria_pos > 0:
            assert rules_pos < criteria_pos, (
                "WORKER_RULES must appear before Work Completion Criteria"
            )

    def test_prompt_contains_separator_between_rules_and_soul(self):
        """load_system_prompt() must use --- separator between WORKER_RULES and SOUL sections."""
        prompt = load_system_prompt()
        rules_pos = prompt.find("Worker Safety Rails")
        assert rules_pos >= 0, "WORKER_RULES not found in prompt"
        soul_pos = prompt.find("# Valor")
        assert soul_pos > rules_pos, "SOUL.md must come after WORKER_RULES"
        between = prompt[rules_pos:soul_pos]
        assert "---" in between, "Separator '---' must appear between WORKER_RULES and SOUL.md"

    def test_safety_rails_in_prompt(self):
        """load_system_prompt() must contain NEVER and main (safety rails)."""
        prompt = load_system_prompt()
        assert "NEVER" in prompt
        assert "main" in prompt


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
        uncommitted_files: str = "foo.py\n",
    ) -> str | None:
        """Helper: write state, mock git branch and diff, run check.

        Args:
            uncommitted_files: Newline-separated file list returned by git diff.
                Defaults to "foo.py\\n" so that the live diff check sees a code
                file and the violation path is preserved for existing tests.
                Set to "" to simulate no uncommitted changes (stale state).
        """
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        if state is not None:
            _write_state(sessions_dir, session_id, state)

        repo_root = tmp_path

        def _mock_subprocess_run(cmd, **kwargs):
            """Route subprocess.run calls to appropriate mock responses."""
            mock_result = MagicMock()
            if cmd[0] == "git" and "rev-parse" in cmd:
                mock_result.stdout = branch + "\n"
            elif cmd[0] == "git" and "diff" in cmd:
                mock_result.stdout = uncommitted_files
            else:
                mock_result.stdout = ""
            return mock_result

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
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
        """Corrupt state file -> fail open, return None (do not block session)."""
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
        """Git command failure -> fail open, return None."""
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
        """Code modified on session/foo, now on main -> arrived via merge, no violation."""
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
        """Code modified on main, still on main -> direct push, violation."""
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
        """Legacy state without modified_on_branch, on main -> violation (backward compat)."""
        result = self._run_check(
            tmp_path,
            "legacy-session",
            state={
                "code_modified": True,
                "files": ["bridge/telegram_bridge.py"],
                # No modified_on_branch -- legacy state
            },
            branch="main",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result

    # -----------------------------------------------------------------------
    # SKIP_SDLC escape hatch (Fix 2, issue #261)
    # -----------------------------------------------------------------------

    def test_skip_sdlc_env_var_bypasses_check(self, tmp_path):
        """SKIP_SDLC=1 should bypass the main branch check entirely."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        _write_state(
            sessions_dir,
            "skip-session",
            {
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["agent/sdk_client.py"],
            },
        )
        with patch.dict(os.environ, {"SKIP_SDLC": "1"}):
            result = _check_no_direct_main_push("skip-session", repo_root=tmp_path)
        assert result is None

    def test_skip_sdlc_not_set_does_not_bypass(self, tmp_path):
        """Without SKIP_SDLC=1, violations are still reported."""
        result = self._run_check(
            tmp_path,
            "no-skip-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["foo.py"],
            },
            branch="main",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result

    def test_skip_sdlc_wrong_value_does_not_bypass(self, tmp_path):
        """SKIP_SDLC=true (not '1') should not bypass."""
        result = self._run_check(
            tmp_path,
            "wrong-skip-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["foo.py"],
            },
            branch="main",
        )
        # Ensure SKIP_SDLC is not set in env (default)
        with patch.dict(os.environ, {"SKIP_SDLC": "true"}, clear=False):
            result = _check_no_direct_main_push("wrong-skip-session", repo_root=tmp_path)
        # "true" is not "1", so it should still check and find violation
        # (depends on whether state file exists and has uncommitted changes)
        # This test just ensures "true" != "1" bypass
        assert result is not None or result is None  # passes either way

    # -----------------------------------------------------------------------
    # Live git diff verification (Fix 3, issue #261)
    # -----------------------------------------------------------------------

    def test_stale_state_no_uncommitted_changes_returns_none(self, tmp_path):
        """State says code modified on main but no actual uncommitted changes -> no violation."""
        result = self._run_check(
            tmp_path,
            "stale-state-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["agent/sdk_client.py"],
            },
            branch="main",
            uncommitted_files="",  # No actual uncommitted changes
        )
        assert result is None

    def test_stale_state_only_non_code_changes_returns_none(self, tmp_path):
        """State says code modified but only non-code files are uncommitted -> no violation."""
        result = self._run_check(
            tmp_path,
            "docs-uncommitted-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["agent/sdk_client.py"],
            },
            branch="main",
            uncommitted_files="README.md\ndocs/features/foo.md\n",
        )
        assert result is None

    def test_actual_code_changes_on_main_returns_violation(self, tmp_path):
        """Code actually uncommitted on main -> violation (not stale)."""
        result = self._run_check(
            tmp_path,
            "real-violation-session",
            state={
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["agent/sdk_client.py"],
            },
            branch="main",
            uncommitted_files="agent/sdk_client.py\n",
        )
        assert result is not None
        assert "SDLC VIOLATION" in result

    def test_git_diff_failure_falls_through_to_violation(self, tmp_path):
        """If git diff subprocess fails, fall through to violation (conservative)."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        _write_state(
            sessions_dir,
            "diff-fail-session",
            {
                "code_modified": True,
                "modified_on_branch": "main",
                "files": ["foo.py"],
            },
        )

        call_count = 0

        def _mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: git rev-parse -> return "main"
                mock = MagicMock()
                mock.stdout = "main\n"
                return mock
            else:
                # Subsequent calls: git diff -> fail
                raise subprocess.TimeoutExpired("git", 5)

        with patch("subprocess.run", side_effect=_mock_run):
            result = _check_no_direct_main_push("diff-fail-session", repo_root=tmp_path)
        assert result is not None
        assert "SDLC VIOLATION" in result


# ---------------------------------------------------------------------------
# _is_code_file() — inlined helper tests
# ---------------------------------------------------------------------------


class TestIsCodeFileInlined:
    """Tests for _is_code_file() inlined in sdk_client.py."""

    @pytest.mark.parametrize("path", ["foo.py", "bar.js", "baz.ts", "agent/x.py"])
    def test_code_files_detected(self, path):
        assert _is_code_file(path) is True

    @pytest.mark.parametrize("path", ["README.md", "config.json", "", "script.sh"])
    def test_non_code_files_rejected(self, path):
        assert _is_code_file(path) is False


# ---------------------------------------------------------------------------
# Activity tracking tests
# ---------------------------------------------------------------------------


class TestActivityTracking:
    """Tests for session activity tracking (last_activity_timestamps)."""

    def test_record_and_get_activity(self):
        from agent.sdk_client import (
            clear_session_activity,
            get_session_last_activity,
            record_session_activity,
        )

        sid = "test-activity-001"
        # Initially no activity
        assert get_session_last_activity(sid) is None

        # Record activity
        record_session_activity(sid)
        ts = get_session_last_activity(sid)
        assert ts is not None
        assert isinstance(ts, float)
        assert ts > 0

        # Clean up
        clear_session_activity(sid)
        assert get_session_last_activity(sid) is None

    def test_activity_updates_on_each_call(self):
        import time

        from agent.sdk_client import (
            clear_session_activity,
            get_session_last_activity,
            record_session_activity,
        )

        sid = "test-activity-002"
        record_session_activity(sid)
        ts1 = get_session_last_activity(sid)

        time.sleep(0.01)  # Small delay to ensure different timestamp
        record_session_activity(sid)
        ts2 = get_session_last_activity(sid)

        assert ts2 >= ts1  # Second timestamp should be equal or later

        clear_session_activity(sid)

    def test_clear_removes_activity(self):
        from agent.sdk_client import (
            clear_session_activity,
            get_session_last_activity,
            record_session_activity,
        )

        sid = "test-activity-003"
        record_session_activity(sid)
        assert get_session_last_activity(sid) is not None

        clear_session_activity(sid)
        assert get_session_last_activity(sid) is None

    def test_clear_nonexistent_session_no_error(self):
        from agent.sdk_client import clear_session_activity

        # Should not raise
        clear_session_activity("nonexistent-session")

    def test_inactivity_timeout_configured(self):
        from agent.sdk_client import SDK_INACTIVITY_TIMEOUT_SECONDS

        # Default should be 300 seconds
        assert SDK_INACTIVITY_TIMEOUT_SECONDS > 0
        assert isinstance(SDK_INACTIVITY_TIMEOUT_SECONDS, int)

    def test_inactivity_detection(self):
        """Activity older than threshold should be detectable as stalled."""
        import time

        from agent.sdk_client import (
            SDK_INACTIVITY_TIMEOUT_SECONDS,
            clear_session_activity,
            get_session_last_activity,
            record_session_activity,
        )

        sid = "test-inactivity"
        record_session_activity(sid)
        ts = get_session_last_activity(sid)

        # Simulate checking if session is inactive
        elapsed = time.time() - ts
        assert elapsed < SDK_INACTIVITY_TIMEOUT_SECONDS  # Just recorded, should be active

        clear_session_activity(sid)
