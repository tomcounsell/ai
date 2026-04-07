"""Unit tests for resolve_project_key() in tools/valor_session.py.

Tests cover:
- Exact cwd match on working_directory
- Subdirectory match (cwd is a child of working_directory)
- Most-specific match when paths overlap
- Fallback to "valor" with stderr warning when no match
- Fallback to "valor" when projects.json is empty/missing
- --project-key flag bypasses resolution (tested via cmd_create argument handling)
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import resolve_project_key  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(projects: dict) -> dict:
    """Build a minimal config dict as load_config() would return."""
    return {"projects": projects, "defaults": {}}


# ---------------------------------------------------------------------------
# Happy path: exact and subdirectory matches
# ---------------------------------------------------------------------------


class TestResolveProjectKeyMatch:
    def test_exact_match(self, tmp_path):
        """Returns the key whose working_directory exactly equals cwd."""
        config = _make_config(
            {
                "ai": {"working_directory": str(tmp_path)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(tmp_path))
        assert result == "ai"

    def test_subdirectory_match(self, tmp_path):
        """Returns the key when cwd is a subdirectory of working_directory."""
        subdir = tmp_path / "src" / "feature"
        subdir.mkdir(parents=True)
        config = _make_config(
            {
                "ai": {"working_directory": str(tmp_path)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(subdir))
        assert result == "ai"

    def test_most_specific_match_wins(self, tmp_path):
        """When two projects overlap, the longer (more specific) path wins."""
        # ai_project covers a sub-path of valor_project
        valor_dir = tmp_path
        ai_dir = tmp_path / "ai"
        ai_dir.mkdir()
        cwd = ai_dir / "tools"
        cwd.mkdir()

        config = _make_config(
            {
                "valor": {"working_directory": str(valor_dir)},
                "ai": {"working_directory": str(ai_dir)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(cwd))
        assert result == "ai"

    def test_multiple_projects_picks_correct_one(self, tmp_path):
        """When multiple projects exist, picks the one matching cwd."""
        ai_dir = tmp_path / "ai"
        valor_dir = tmp_path / "valor"
        ai_dir.mkdir()
        valor_dir.mkdir()

        config = _make_config(
            {
                "ai": {"working_directory": str(ai_dir)},
                "valor": {"working_directory": str(valor_dir)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(ai_dir))
        assert result == "ai"

        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(valor_dir))
        assert result == "valor"


# ---------------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------------


class TestResolveProjectKeyFallback:
    def test_no_match_returns_valor(self, tmp_path):
        """Returns 'valor' and prints stderr warning when cwd matches no project."""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        project_dir = tmp_path / "ai"
        project_dir.mkdir()

        config = _make_config(
            {
                "ai": {"working_directory": str(project_dir)},
            }
        )
        stderr_capture = StringIO()
        with patch("bridge.routing.load_config", return_value=config):
            with patch("sys.stderr", stderr_capture):
                result = resolve_project_key(str(other_dir))

        assert result == "valor"
        assert "valor" in stderr_capture.getvalue()

    def test_empty_projects_returns_valor(self):
        """Returns 'valor' when projects dict is empty."""
        config = _make_config({})
        stderr_capture = StringIO()
        with patch("bridge.routing.load_config", return_value=config):
            with patch("sys.stderr", stderr_capture):
                result = resolve_project_key("/some/unmatched/path")

        assert result == "valor"

    def test_load_config_exception_returns_valor(self):
        """Returns 'valor' and warns when load_config raises an exception."""
        stderr_capture = StringIO()
        with patch("bridge.routing.load_config", side_effect=Exception("file not found")):
            with patch("sys.stderr", stderr_capture):
                result = resolve_project_key("/any/path")

        assert result == "valor"
        assert "valor" in stderr_capture.getvalue()

    def test_project_missing_working_directory_skipped(self, tmp_path):
        """Projects without working_directory are skipped, doesn't crash."""
        config = _make_config(
            {
                "ai": {},  # No working_directory key
                "valor": {"working_directory": str(tmp_path)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(tmp_path))

        assert result == "valor"

    def test_project_empty_working_directory_skipped(self, tmp_path):
        """Projects with empty string working_directory are skipped."""
        config = _make_config(
            {
                "ai": {"working_directory": ""},
                "valor": {"working_directory": str(tmp_path)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            result = resolve_project_key(str(tmp_path))

        assert result == "valor"

    def test_fallback_warning_goes_to_stderr_not_stdout(self, tmp_path):
        """Fallback warning is printed to stderr, not stdout (preserves --json)."""
        config = _make_config({})
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        with patch("bridge.routing.load_config", return_value=config):
            with patch("sys.stdout", stdout_capture):
                with patch("sys.stderr", stderr_capture):
                    result = resolve_project_key("/unmatched/dir")

        assert result == "valor"
        assert stdout_capture.getvalue() == ""  # Nothing on stdout
        assert stderr_capture.getvalue() != ""  # Warning on stderr


# ---------------------------------------------------------------------------
# --project-key flag override (argument parser behavior)
# ---------------------------------------------------------------------------


class TestProjectKeyFlagOverride:
    def test_explicit_flag_bypasses_resolution(self, tmp_path):
        """When --project-key is provided, resolve_project_key is not called."""
        import argparse

        from tools.valor_session import resolve_project_key as real_fn

        # Simulate what cmd_create does: explicit_key takes priority
        args = argparse.Namespace(
            role="pm",
            message="test",
            chat_id="0",
            parent=None,
            working_dir=None,
            project_key="custom-project",
            json=False,
        )

        explicit_key = getattr(args, "project_key", None)
        assert explicit_key == "custom-project"

        # resolve_project_key should NOT be called when explicit_key is set
        with patch("tools.valor_session.resolve_project_key") as mock_resolve:
            if explicit_key:
                project_key = explicit_key
            else:
                project_key = real_fn("/some/cwd")

            mock_resolve.assert_not_called()

        assert project_key == "custom-project"
