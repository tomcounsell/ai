"""Unit tests for validate_sdlc_on_stop.py Stop hook SDLC quality gate."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Hook scripts live in .claude/hooks/validators/ — add parent to sys.path
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
VALIDATORS_DIR = HOOKS_DIR / "validators"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import validate_sdlc_on_stop module."""
    import validate_sdlc_on_stop

    return validate_sdlc_on_stop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_skip_sdlc_env():
    """Ensure SKIP_SDLC is not set between tests."""
    env = os.environ.copy()
    os.environ.pop("SKIP_SDLC", None)
    yield
    # Restore original state
    os.environ.clear()
    os.environ.update(env)


@pytest.fixture()
def sessions_dir(tmp_path):
    """Return a temporary sessions directory."""
    d = tmp_path / "sessions"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def patch_sessions_dir(sessions_dir):
    """Patch get_data_sessions_dir in validate_sdlc_on_stop to use tmp dir."""
    with patch("validate_sdlc_on_stop.get_data_sessions_dir", return_value=sessions_dir):
        yield sessions_dir


def write_state(sessions_dir: Path, session_id: str, state: dict) -> None:
    """Write a sdlc_state.json for the given session."""
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "sdlc_state.json").write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# check_sdlc_quality_gate — the core logic function
# ---------------------------------------------------------------------------


class TestCheckSdlcQualityGate:
    """Tests for the check_sdlc_quality_gate(session_id) function."""

    def test_no_state_file_returns_pass(self, patch_sessions_dir):
        """Non-code session: no sdlc_state.json → pass (exit 0)."""
        mod = import_validator()
        result = mod.check_sdlc_quality_gate("no-such-session")
        assert result is None  # None means pass (exit 0)

    def test_all_quality_commands_true_returns_pass(self, patch_sessions_dir):
        """All three quality gates passed → exit 0."""
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-all-pass",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": True, "ruff": True, "ruff-format": True},
            },
        )
        result = mod.check_sdlc_quality_gate("session-all-pass")
        assert result is None

    def test_code_modified_false_returns_pass(self, patch_sessions_dir):
        """State file exists but code_modified is False → pass (no enforcement needed)."""
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-no-code",
            {
                "code_modified": False,
                "files": [],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        result = mod.check_sdlc_quality_gate("session-no-code")
        assert result is None

    @pytest.mark.parametrize(
        "quality_commands,expected_missing",
        [
            (
                {"pytest": False, "ruff": False, "ruff-format": False},
                ["pytest", "ruff", "ruff-format"],
            ),
            (
                {"pytest": True, "ruff": False, "ruff-format": False},
                ["ruff", "ruff-format"],
            ),
            (
                {"pytest": True, "ruff": True, "ruff-format": False},
                ["ruff-format"],
            ),
            (
                {"pytest": False, "ruff": True, "ruff-format": True},
                ["pytest"],
            ),
        ],
    )
    def test_missing_quality_commands_returns_error_message(
        self, patch_sessions_dir, quality_commands, expected_missing
    ):
        """Any missing quality gate → returns error message listing missing checks."""
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-partial",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": quality_commands,
            },
        )
        result = mod.check_sdlc_quality_gate("session-partial")
        assert result is not None
        for missing in expected_missing:
            assert missing in result

    def test_error_message_includes_run_commands(self, patch_sessions_dir):
        """Error message should include example run commands for missing checks."""
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-hints",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        result = mod.check_sdlc_quality_gate("session-hints")
        assert result is not None
        assert "pytest tests/" in result
        assert "python -m ruff check ." in result

    def test_error_message_mentions_skip_sdlc(self, patch_sessions_dir):
        """Error message should mention SKIP_SDLC escape hatch."""
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-escape",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": True, "ruff-format": True},
            },
        )
        result = mod.check_sdlc_quality_gate("session-escape")
        assert result is not None
        assert "SKIP_SDLC" in result


# ---------------------------------------------------------------------------
# SKIP_SDLC escape hatch
# ---------------------------------------------------------------------------


class TestSkipSdlcEscapeHatch:
    def test_skip_sdlc_set_returns_none_even_when_gates_missing(self, patch_sessions_dir, capsys):
        """SKIP_SDLC=1 bypasses enforcement even if quality gates are incomplete."""
        os.environ["SKIP_SDLC"] = "1"
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-skip",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        result = mod.check_sdlc_quality_gate("session-skip")
        assert result is None

    def test_skip_sdlc_logs_warning(self, patch_sessions_dir, capsys):
        """SKIP_SDLC=1 should emit a warning to stderr."""
        os.environ["SKIP_SDLC"] = "1"
        mod = import_validator()
        write_state(
            patch_sessions_dir,
            "session-warn",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        mod.check_sdlc_quality_gate("session-warn")
        captured = capsys.readouterr()
        assert "SKIP_SDLC" in captured.err or "SKIP_SDLC" in captured.out


# ---------------------------------------------------------------------------
# main() integration — exit codes via subprocess
# ---------------------------------------------------------------------------


class TestMainExitCodes:
    """End-to-end tests invoking main() to verify correct exit codes."""

    def _run_main(self, sessions_dir: Path, session_id: str, env_extra=None):
        """Run the validator script via subprocess and return exit code + stderr."""
        import subprocess

        script_path = VALIDATORS_DIR / "validate_sdlc_on_stop.py"
        stdin_data = json.dumps({"session_id": session_id})
        # sessions_dir is <tmp_path>/data/sessions; CLAUDE_PROJECT_DIR must be
        # <tmp_path> so get_data_sessions_dir() can resolve data/sessions correctly.
        env = {
            **os.environ,
            "CLAUDE_PROJECT_DIR": str(sessions_dir.parent.parent),
        }
        if env_extra:
            env.update(env_extra)
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
        )
        return result.returncode, result.stderr, result.stdout

    def test_no_state_file_exits_0(self, tmp_path):
        """No sdlc_state.json → exit 0."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True)
        code, _, _ = self._run_main(sessions_dir, "ghost-session")
        assert code == 0

    def test_all_gates_passed_exits_0(self, tmp_path):
        """All quality gates passed → exit 0."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True)
        write_state(
            sessions_dir,
            "session-green",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": True, "ruff": True, "ruff-format": True},
            },
        )
        code, _, _ = self._run_main(sessions_dir, "session-green")
        assert code == 0

    def test_missing_gates_exits_2(self, tmp_path):
        """Missing quality gates → exit 2."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True)
        write_state(
            sessions_dir,
            "session-red",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": True, "ruff-format": False},
            },
        )
        code, stderr, _ = self._run_main(sessions_dir, "session-red")
        assert code == 2
        assert "pytest" in stderr
        assert "ruff-format" in stderr

    def test_skip_sdlc_exits_0_even_when_gates_missing(self, tmp_path):
        """SKIP_SDLC=1 forces exit 0 regardless of gate status."""
        sessions_dir = tmp_path / "data" / "sessions"
        sessions_dir.mkdir(parents=True)
        write_state(
            sessions_dir,
            "session-bypass",
            {
                "code_modified": True,
                "files": ["foo.py"],
                "quality_commands": {"pytest": False, "ruff": False, "ruff-format": False},
            },
        )
        code, _, _ = self._run_main(sessions_dir, "session-bypass", env_extra={"SKIP_SDLC": "1"})
        assert code == 0
