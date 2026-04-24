"""Integration-style unit tests for ``cmd_create`` working_dir derivation (#1158).

Covers the governing design principle:

    > A project and a repo should not be provided separately. The local
    > machine's configuration sets the pairing and that pairing cannot be
    > broken.

Scenarios:

- ``--project-key X`` from an ``ai`` cwd produces a session whose ``working_dir``
  is rooted under project X's declared path, not ``ai``.
- The CLI has no ``--working-dir`` flag — argparse rejects ``--working-dir``
  with ``SystemExit`` + stderr mentioning unrecognized arguments.
- ``--parent <id>`` inherits ``project_key`` from the parent; ``working_dir`` is
  re-derived from the inherited key (NOT copied from ``parent.working_dir``).
- ``_push_agent_session`` receives ``project_config`` as the raw project dict
  from ``projects.json`` (bridge-parity per PR #685).
- Anti-regression grep checks: no ``--working-dir`` flag, no ``return "valor"``,
  no ``default="valor"`` left in ``tools/valor_session.py``.

All tests stub ``bridge.routing.load_config`` and/or ``_resolve_project_working_directory``
so no real ``projects.json`` is read and no real worktrees are created.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Bootstrap: ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import valor_session  # noqa: E402
from tools.valor_session import cmd_create  # noqa: E402


def _make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        command="create",
        role="pm",
        message="Run SDLC on issue #290",
        chat_id=None,
        parent=None,
        project_key=None,
        slug=None,
        model=None,
        json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Core: working_dir derives from project_key, never from cwd
# ---------------------------------------------------------------------------


class TestWorkingDirDerivesFromProjectKey:
    def test_explicit_project_key_from_unrelated_cwd(self, tmp_path, monkeypatch):
        """Setting ``--project-key cuttlefish`` from a cwd inside the ``ai``
        repo produces a session whose ``working_dir`` is rooted under the
        cuttlefish project root (via the mocked projects.json), not the ``ai``
        cwd.

        This is the scenario described in the issue: parent PM in cuttlefish
        calls ``valor-session create`` via subprocess; the child session must
        NOT inherit the ai cwd.
        """
        ai_root = tmp_path / "ai"
        cuttlefish_root = tmp_path / "cuttlefish"
        ai_root.mkdir()
        cuttlefish_root.mkdir()

        # cwd is deliberately inside the ai repo so cwd-matching would pick
        # "ai" if the CLI fell back to it. The --project-key flag must win.
        monkeypatch.chdir(ai_root)

        projects_json = {
            "ai": {"working_directory": str(ai_root)},
            "cuttlefish": {
                "working_directory": str(cuttlefish_root),
                "chat_id": 42,
            },
        }
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        # Worktree creation would call git — stub it and just return a path
        # under the mocked cuttlefish root.
        wt_path = cuttlefish_root / ".worktrees" / "sdlc-290"
        wt_path.mkdir(parents=True)

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                return_value=wt_path,
            ) as mock_wt,
            patch("agent.worktree_manager._validate_slug"),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(_make_args(project_key="cuttlefish"))

        assert rc == 0
        # working_dir MUST be rooted under cuttlefish, not ai.
        assert captured["working_dir"] == str(wt_path)
        assert "cuttlefish" in captured["working_dir"]
        assert str(ai_root) not in captured["working_dir"]
        # project_key travels as set; project_config is the raw projects dict entry.
        assert captured["project_key"] == "cuttlefish"
        assert captured["project_config"] == projects_json["cuttlefish"]
        # get_or_create_worktree was called with the PROJECT root, not cwd.
        assert mock_wt.call_args.args[0] == cuttlefish_root

    def test_no_slug_uses_repo_root_as_working_dir(self, tmp_path, monkeypatch):
        """Dev session without --slug gets the project repo_root as working_dir,
        not the cwd and not a worktree.
        """
        demo_root = tmp_path / "demo"
        demo_root.mkdir()
        monkeypatch.chdir(tmp_path)  # cwd is unrelated

        projects_json = {"demo": {"working_directory": str(demo_root)}}
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(_make_args(role="dev", project_key="demo", message="fix typo"))

        assert rc == 0
        assert captured["working_dir"] == str(demo_root)
        assert captured["slug"] is None


# ---------------------------------------------------------------------------
# --working-dir flag: argparse must reject it outright
# ---------------------------------------------------------------------------


class TestWorkingDirFlagRemoved:
    def test_argparse_rejects_working_dir_flag(self, capsys):
        """Passing ``--working-dir`` via the real CLI entry point must exit
        non-zero with an "unrecognized arguments" error.

        We drive the actual parser (not cmd_create directly) to verify the
        flag is absent at the argparse surface.
        """
        from tools.valor_session import main as valor_session_main

        test_argv = [
            "valor-session",
            "create",
            "--role",
            "pm",
            "--slug",
            "some-slug",
            "--message",
            "hi",
            "--working-dir",
            "/any/path",
        ]
        with patch("sys.argv", test_argv):
            with pytest.raises(SystemExit) as excinfo:
                valor_session_main()
        assert excinfo.value.code != 0
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err or "--working-dir" in err


# ---------------------------------------------------------------------------
# Parent inheritance: copies project_key, re-derives working_dir
# ---------------------------------------------------------------------------


class TestParentInheritance:
    def test_parent_inherits_project_key_only(self, tmp_path, monkeypatch):
        """--parent <id> without --project-key inherits ``project_key`` from the
        parent. ``working_dir`` is re-derived from the (inherited) key's
        project entry — it is NOT copied from ``parent.working_dir``.
        """
        parent_project_root = tmp_path / "parent_proj"
        parent_project_root.mkdir()

        # The parent session exists in a different on-disk path (e.g. a
        # worktree) — this must NOT propagate to the child. Only project_key
        # propagates; working_dir is re-derived.
        parent_working_dir_on_disk = parent_project_root / ".worktrees" / "parent-wt"
        parent_working_dir_on_disk.mkdir(parents=True)

        fake_parent = MagicMock()
        fake_parent.project_key = "parent_proj"
        fake_parent.agent_session_id = "parent-uuid-123"
        fake_parent.working_dir = str(parent_working_dir_on_disk)

        projects_json = {
            "parent_proj": {"working_directory": str(parent_project_root)},
        }
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        child_wt = parent_project_root / ".worktrees" / "sdlc-999"
        child_wt.mkdir(parents=True)

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch(
                "tools.valor_session._find_session",
                return_value=fake_parent,
            ),
            patch(
                "agent.worktree_manager.get_or_create_worktree",
                return_value=child_wt,
            ),
            patch("agent.worktree_manager._validate_slug"),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(
                _make_args(
                    parent="parent-uuid-123",
                    project_key=None,
                    message="Child session for issue #999",
                )
            )

        assert rc == 0
        # Inherited from parent.
        assert captured["project_key"] == "parent_proj"
        # working_dir is re-derived from projects.json — NOT copied from
        # parent.working_dir.
        assert captured["working_dir"] == str(child_wt)
        assert captured["working_dir"] != fake_parent.working_dir

    def test_parent_not_found_falls_through_to_cwd_resolution(self, tmp_path, monkeypatch):
        """A typo in --parent (session not found) must not hard-fail creation
        if cwd can still resolve to a project. Parent inheritance is advisory.
        """
        proj_root = tmp_path / "proj"
        proj_root.mkdir()
        monkeypatch.chdir(proj_root)  # cwd matches the project

        projects_json = {"proj": {"working_directory": str(proj_root)}}
        captured: dict = {}

        async def fake_push(**kwargs):
            captured.update(kwargs)

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch("tools.valor_session._find_session", return_value=None),
            patch("agent.agent_session_queue._push_agent_session", side_effect=fake_push),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(
                _make_args(
                    role="dev",  # dev so no --slug required
                    parent="nonexistent-uuid",
                    project_key=None,
                    message="whatever",
                )
            )

        assert rc == 0
        assert captured["project_key"] == "proj"


# ---------------------------------------------------------------------------
# Error surfaces: unmatched cwd / unknown key produce clear messages
# ---------------------------------------------------------------------------


class TestErrorSurfaces:
    def test_unmatched_cwd_no_flag_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """cwd matches no project and --project-key not given → exit 1, stderr
        names the cwd and lists available keys.
        """
        proj_root = tmp_path / "proj"
        proj_root.mkdir()
        unrelated_cwd = tmp_path / "unrelated"
        unrelated_cwd.mkdir()
        monkeypatch.chdir(unrelated_cwd)

        projects_json = {"proj": {"working_directory": str(proj_root)}}

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch("agent.agent_session_queue._push_agent_session"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(_make_args(role="dev", project_key=None, message="hi"))

        assert rc != 0
        err = capsys.readouterr().err
        assert str(unrelated_cwd) in err
        assert "proj" in err  # available key listed
        assert "--project-key" in err  # remediation suggested

    def test_unknown_project_key_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """--project-key naming a key not in projects.json → exit 1 with clear
        message including the available keys.
        """
        proj_root = tmp_path / "proj"
        proj_root.mkdir()

        projects_json = {"proj": {"working_directory": str(proj_root)}}

        with (
            patch(
                "bridge.routing.load_config",
                return_value={"projects": projects_json, "defaults": {}},
            ),
            patch("agent.agent_session_queue._push_agent_session"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(_make_args(role="dev", project_key="nonexistent", message="hi"))

        assert rc != 0
        err = capsys.readouterr().err
        assert "nonexistent" in err
        assert "proj" in err

    def test_load_config_failure_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """When load_config itself raises, the CLI exits 1 with a
        ProjectsConfigUnavailableError-style message.
        """
        with (
            patch(
                "bridge.routing.load_config",
                side_effect=OSError("projects.json permission denied"),
            ),
            patch("agent.agent_session_queue._push_agent_session"),
            patch("tools.valor_session._check_worker_health", return_value=(True, 1)),
        ):
            rc = cmd_create(_make_args(role="dev", project_key="anything", message="hi"))

        assert rc != 0
        err = capsys.readouterr().err
        assert "projects.json" in err
        assert "permission denied" in err


# ---------------------------------------------------------------------------
# Anti-regression grep checks on the source file
# ---------------------------------------------------------------------------


class TestAntiRegressionGreps:
    """Runs ``grep`` against the real source to catch re-introductions of the
    silent fallback or the removed flag. Uses pathlib-derived absolute paths
    so the test is portable across machines (e.g. /Users/tomcounsell vs
    /Users/valorengels).
    """

    @staticmethod
    def _valor_session_path() -> Path:
        # tests/unit/this_file.py → parents[0]=unit, [1]=tests, [2]=repo_root
        return Path(__file__).resolve().parents[2] / "tools" / "valor_session.py"

    def test_no_working_dir_flag(self):
        """``tools/valor_session.py`` must not mention ``--working-dir`` anywhere
        (comments included — the flag is gone, no legacy traces)."""
        path = self._valor_session_path()
        result = subprocess.run(
            ["grep", "-n", "--", "--working-dir", str(path)],
            capture_output=True,
            text=True,
        )
        # grep exit 1 == no match (the desired outcome).
        assert result.returncode == 1, f"--working-dir still present in {path}:\n{result.stdout}"

    def test_no_return_valor_fallback(self):
        """No ``return "valor"`` (or ``return 'valor'``) in the source.

        The silent fallback was removed; any literal ``return "valor"`` would
        indicate a regression.
        """
        path = self._valor_session_path()
        # Two patterns: double-quoted and single-quoted
        for needle in ('return "valor"', "return 'valor'"):
            result = subprocess.run(
                ["grep", "-n", needle, str(path)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, f"Found {needle!r} in {path}:\n{result.stdout}"

    def test_no_default_valor_in_resolver(self):
        """No ``default="valor"`` (or ``default='valor'``) in the source."""
        path = self._valor_session_path()
        result = subprocess.run(
            ["grep", "-nE", r'default\s*=\s*["\']valor["\']', str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, f'Found default="valor" pattern in {path}:\n{result.stdout}'
