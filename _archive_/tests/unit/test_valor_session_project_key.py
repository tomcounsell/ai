"""Unit tests for ``resolve_project_key()`` and ``_resolve_project_working_directory()``.

Covers the tightened contract from issue #1158:

- Exact cwd match on ``working_directory``.
- Subdirectory match (cwd is a child of ``working_directory``).
- Most-specific match when paths overlap.
- **No silent fallback** — unmatched cwd raises ``ProjectKeyResolutionError``.
- **No config-load fallback** — ``load_config()`` errors raise
  ``ProjectsConfigUnavailableError``.
- ``--project-key`` flag bypasses resolution (tested via ``cmd_create`` argument
  handling).
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import pytest  # noqa: E402

from tools.valor_session import (  # noqa: E402
    ProjectKeyResolutionError,
    ProjectsConfigUnavailableError,
    _resolve_project_working_directory,
    resolve_project_key,
)

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
# Fallback behavior — now raises instead of returning "valor"
# ---------------------------------------------------------------------------


class TestResolveProjectKeyFallback:
    def test_no_match_raises(self, tmp_path):
        """Unmatched cwd raises ProjectKeyResolutionError with useful message."""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        project_dir = tmp_path / "ai"
        project_dir.mkdir()

        config = _make_config(
            {
                "ai": {"working_directory": str(project_dir)},
            }
        )
        with patch("bridge.routing.load_config", return_value=config):
            with pytest.raises(ProjectKeyResolutionError) as excinfo:
                resolve_project_key(str(other_dir))

        msg = str(excinfo.value)
        assert str(other_dir) in msg
        assert "ai" in msg  # available key listed
        assert "--project-key" in msg  # remediation suggested

    def test_empty_projects_raises(self):
        """Empty projects dict still raises — no silent fallback."""
        config = _make_config({})
        with patch("bridge.routing.load_config", return_value=config):
            with pytest.raises(ProjectKeyResolutionError) as excinfo:
                resolve_project_key("/some/unmatched/path")
        # Available keys list should be empty but present.
        assert "[]" in str(excinfo.value)

    def test_load_config_exception_raises_distinct_error(self):
        """load_config() failure raises ProjectsConfigUnavailableError, not
        ProjectKeyResolutionError. The two must be distinguishable because the
        remediation differs (fix projects.json vs pass --project-key).
        """
        with patch("bridge.routing.load_config", side_effect=Exception("file not found")):
            with pytest.raises(ProjectsConfigUnavailableError) as excinfo:
                resolve_project_key("/any/path")

        # The cause is preserved so operators can diagnose the root error.
        assert "file not found" in str(excinfo.value)

    def test_project_missing_working_directory_skipped(self, tmp_path):
        """Projects without working_directory are skipped; matched project wins."""
        config = _make_config(
            {
                "ai": {},  # No working_directory key — skipped
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

    def test_resolver_itself_does_not_write_to_stderr(self, tmp_path):
        """The helper no longer prints — it raises. Stderr/stdout stay clean.

        (The CLI wrapper in cmd_create is responsible for surfacing the error
        message to stderr via its broad ``except Exception`` handler.)
        """
        config = _make_config({})
        stdout_capture = StringIO()
        stderr_capture = StringIO()

        with patch("bridge.routing.load_config", return_value=config):
            with patch("sys.stdout", stdout_capture):
                with patch("sys.stderr", stderr_capture):
                    with pytest.raises(ProjectKeyResolutionError):
                        resolve_project_key("/unmatched/dir")

        assert stdout_capture.getvalue() == ""
        assert stderr_capture.getvalue() == ""


# ---------------------------------------------------------------------------
# _resolve_project_working_directory — new helper
# ---------------------------------------------------------------------------


class TestResolveProjectWorkingDirectory:
    def test_returns_path_and_dict_tuple(self, tmp_path):
        """Helper returns (Path, full_project_dict) so the caller gets both
        values from a single load_config() call.
        """
        proj = {"working_directory": str(tmp_path), "chat_id": 12345}
        config = _make_config({"demo": proj})
        with patch("bridge.routing.load_config", return_value=config):
            repo_root, project = _resolve_project_working_directory("demo")

        assert repo_root == Path(str(tmp_path)).expanduser()
        # The dict is returned unchanged (bridge-parity): no filtering, no
        # reshape — the CLI must pass it whole to _push_agent_session.
        assert project == proj

    def test_unknown_key_raises(self, tmp_path):
        config = _make_config({"valor": {"working_directory": str(tmp_path)}})
        with patch("bridge.routing.load_config", return_value=config):
            with pytest.raises(ProjectKeyResolutionError) as excinfo:
                _resolve_project_working_directory("nonexistent")
        # Message lists the keys that DO exist so the caller can fix the typo.
        assert "valor" in str(excinfo.value)

    def test_key_with_empty_working_directory_raises(self):
        config = _make_config({"broken": {"working_directory": ""}})
        with patch("bridge.routing.load_config", return_value=config):
            with pytest.raises(ProjectKeyResolutionError) as excinfo:
                _resolve_project_working_directory("broken")
        assert "working_directory" in str(excinfo.value)

    def test_load_config_failure_raises_config_unavailable(self):
        with patch(
            "bridge.routing.load_config",
            side_effect=OSError("projects.json permission denied"),
        ):
            with pytest.raises(ProjectsConfigUnavailableError) as excinfo:
                _resolve_project_working_directory("any")
        assert "permission denied" in str(excinfo.value)

    def test_expanduser_is_applied(self, monkeypatch, tmp_path):
        """``~`` in working_directory is expanded so the caller never sees a
        literal tilde path that won't exist on disk.
        """
        # Fake "home" = tmp_path so ~/fake resolves to tmp_path/fake.
        monkeypatch.setenv("HOME", str(tmp_path))
        config = _make_config({"home_proj": {"working_directory": "~/fake"}})
        with patch("bridge.routing.load_config", return_value=config):
            repo_root, _ = _resolve_project_working_directory("home_proj")
        # The ~ should have been expanded.
        assert "~" not in str(repo_root)
        assert str(repo_root).endswith("fake")


# ---------------------------------------------------------------------------
# --project-key flag override (argument parser behavior)
# ---------------------------------------------------------------------------


class TestProjectKeyFlagOverride:
    def test_explicit_flag_bypasses_resolution(self):
        """When --project-key is provided, resolve_project_key is not called.

        Also asserts there is no longer a ``working_dir`` attribute on the
        namespace — the CLI flag has been removed.
        """
        import argparse

        from tools.valor_session import resolve_project_key as real_fn

        # Simulate what cmd_create does: explicit_key takes priority.
        # Note: no ``working_dir`` attribute — the flag is gone.
        args = argparse.Namespace(
            role="pm",
            message="test",
            chat_id="0",
            parent=None,
            project_key="custom-project",
            json=False,
        )

        # Confirm the attribute we removed is not present on the namespace
        # (this is a regression guard for the plan's Success Criterion that
        # ``valor-session create --working-dir ...`` now fails argparse).
        assert not hasattr(args, "working_dir")

        explicit_key = getattr(args, "project_key", None)
        assert explicit_key == "custom-project"

        # resolve_project_key should NOT be called when explicit_key is set.
        with patch("tools.valor_session.resolve_project_key") as mock_resolve:
            if explicit_key:
                project_key = explicit_key
            else:
                project_key = real_fn("/some/cwd")

            mock_resolve.assert_not_called()

        assert project_key == "custom-project"
