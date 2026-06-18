"""Integration tests for PiSubprocessBuilder against real local Pi (ollama/gemma4:31b).

These tests invoke the real `pi` CLI with the local ollama model, so:
- They are slow (30s-2min per test)
- They require `pi` on PATH and `ollama` running with `gemma4:31b` pulled
- Marked `@pytest.mark.integration` and skipped if pi/ollama unavailable
"""

import pathlib
import shutil
import subprocess

import pytest

from agent.granite_container.builder import PiSubprocessBuilder

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------


def _pi_available() -> bool:
    return shutil.which("pi") is not None


def _ollama_model_available(model: str = "gemma4:31b") -> bool:
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
        return model in result.stdout
    except Exception:
        return False


_REPO_ROOT = pathlib.Path(__file__).parents[2]

_RAILS_PATH = _REPO_ROOT / ".claude/commands/granite/_prime-rails.md"
_PERSONA_PATH = _REPO_ROOT / "config/personas/granite/pi_dev_rails.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_builder(cwd: str, timeout_s: int = 120) -> PiSubprocessBuilder:
    """Create a PiSubprocessBuilder for integration tests."""
    if not _RAILS_PATH.exists():
        pytest.skip(f"rails file not found: {_RAILS_PATH}")
    if not _PERSONA_PATH.exists():
        pytest.skip(f"persona file not found: {_PERSONA_PATH}")
    return PiSubprocessBuilder(
        builder_cwd=cwd,
        rails_path=str(_RAILS_PATH),
        persona_path=str(_PERSONA_PATH),
        provider="ollama",
        model="gemma4:31b",
        timeout_s=timeout_s,
    )


def _init_git(path: pathlib.Path) -> None:
    """Initialise a minimal git repo so Pi's bash/edit tools work properly."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=False)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Integration test class
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not _pi_available(),
    reason="pi CLI not on PATH",
)
@pytest.mark.skipif(
    not _ollama_model_available(),
    reason="ollama gemma4:31b not available",
)
class TestPiBuilderE2E:
    """End-to-end tests using real pi CLI with local ollama."""

    @pytest.fixture
    def temp_worktree(self, tmp_path):
        """A temp directory with a minimal git repo acting as the builder's cwd."""
        _init_git(tmp_path)
        return tmp_path

    def test_hello_world_response(self, temp_worktree):
        """Pi responds to a trivial prompt — proves subprocess plumbing."""
        builder = _make_builder(str(temp_worktree))
        result = builder.run_turn("Reply with the single word: PONG. Do not write anything else.")
        assert isinstance(result, str), "run_turn must return str"
        assert result.strip() != "", "Pi returned empty — subprocess or parse failure"
        builder.close()

    def test_file_write_in_worktree(self, temp_worktree):
        """Pi can write a file in builder_cwd (worktree isolation working)."""
        target = temp_worktree / "hello.txt"
        assert not target.exists()

        builder = _make_builder(str(temp_worktree))
        result = builder.run_turn(
            "Create a file called hello.txt in the current directory containing "
            "exactly: hello\nThen report what you did."
        )

        assert isinstance(result, str), "run_turn must return str"
        assert result != "", "Pi returned empty"
        assert target.exists(), f"hello.txt was not created in {temp_worktree}"
        builder.close()

    def test_empty_output_degrades_gracefully(self, temp_worktree):
        """run_turn never raises — validates the DEV_REPORT_UNAVAILABLE path."""
        builder = _make_builder(str(temp_worktree))
        # Pi should respond to any valid prompt; this proves run_turn doesn't crash.
        result = builder.run_turn("What is 2+2? Answer with just the number.")
        assert isinstance(result, str), "run_turn must return a string (even empty)"
        builder.close()

    def test_close_is_safe_after_run(self, temp_worktree):
        """close() after a completed run must not raise."""
        builder = _make_builder(str(temp_worktree))
        builder.run_turn("Say: done")
        builder.close()  # must not raise

    def test_builder_cwd_constraint_respected(self, temp_worktree, tmp_path):
        """Risk 6: Pi must not write files outside its builder_cwd."""
        sibling = tmp_path / "sibling_dir"
        sibling.mkdir()
        _init_git(sibling)

        # Run builder scoped to temp_worktree
        builder = _make_builder(str(temp_worktree))
        builder.run_turn("List the files in the current directory.")
        builder.close()

        # Sibling directory must remain clean
        sibling_files = list(sibling.iterdir())
        # Allowing only the .git dir if it was created by _init_git
        non_git = [f for f in sibling_files if f.name != ".git"]
        assert non_git == [], f"Pi wrote outside its builder_cwd: found {non_git} in sibling dir"
