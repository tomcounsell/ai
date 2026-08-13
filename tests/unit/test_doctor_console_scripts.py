"""Tests for doctor's `[project.scripts]` PATH-resolution check (#2566).

The defect these guard is pure host state: `.venv/bin` missing from PATH while
a stale `~/Library/Python/3.12/bin` sits on it holding shims for a system
interpreter with no editable install of this repo. Two of the three SDLC entry
points resolved to those shims and died with `ModuleNotFoundError`; the third
had no shim and failed as `command not found`. Both shapes are simulated here
with real files and a real PATH, because a check that only ever sees a healthy
machine proves nothing.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

from tools.doctor import _check_console_scripts_resolve

SCRIPTS = ("critique-roster-check", "critique-resume-probe", "sdlc-push-guard")


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_checkout(tmp_path: Path, names: tuple[str, ...] = SCRIPTS) -> Path:
    """A checkout with a `[project.scripts]` table and a populated `.venv/bin`."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    table = "\n".join(f'{n} = "tools.{n.replace("-", "_")}:main"' for n in names)
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\n\n[project.scripts]\n{table}\n')
    for name in names:
        _executable(root / ".venv" / "bin" / name)
    return root


def _stale_shim_dir(tmp_path: Path, names: tuple[str, ...]) -> Path:
    """Stand-in for `~/Library/Python/3.12/bin` — shims for a foreign interpreter."""
    shim_dir = tmp_path / "Library" / "Python" / "3.12" / "bin"
    for name in names:
        _executable(shim_dir / name, "#!/usr/bin/python3\nraise SystemExit(1)\n")
    return shim_dir


def _run(root: Path, path_entries: list[Path], monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(str(p) for p in path_entries))
    with patch("tools.doctor.PROJECT_DIR", root):
        return _check_console_scripts_resolve()


class TestConsoleScriptsResolve:
    def test_passes_when_venv_bin_leads_path(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "3 console scripts" in result.message
        assert str(root / ".venv" / "bin") in result.message

    def test_stale_shim_ahead_of_venv_is_caught(self, tmp_path, monkeypatch):
        """The `valorengels` shape: venv on PATH, but shadowed for two names."""
        root = _fake_checkout(tmp_path)
        shims = _stale_shim_dir(tmp_path, ("critique-roster-check", "critique-resume-probe"))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)
        assert result.passed is False
        assert "2/3" in result.message
        assert "critique-roster-check" in result.message
        assert str(shims) in result.message
        assert "shadowed" in result.message
        assert result.fix and str(root / ".venv" / "bin") in result.fix

    def test_venv_absent_from_path_reports_both_failure_shapes(self, tmp_path, monkeypatch):
        """The `MacBookPro.local` shape: shims for two names, nothing for the third."""
        root = _fake_checkout(tmp_path)
        shims = _stale_shim_dir(tmp_path, ("critique-roster-check", "critique-resume-probe"))
        result = _run(root, [shims, tmp_path / "empty"], monkeypatch)
        assert result.passed is False
        assert "3/3" in result.message
        assert "not on PATH" in result.message
        # ModuleNotFoundError shape and command-not-found shape, both named.
        assert f"critique-roster-check -> {shims / 'critique-roster-check'}" in result.message
        assert "sdlc-push-guard -> not found" in result.message

    def test_nothing_on_path_at_all_fails(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path)
        result = _run(root, [tmp_path / "empty"], monkeypatch)
        assert result.passed is False
        assert "not found" in result.message

    def test_hardlinked_copy_outside_the_venv_is_accepted(self, tmp_path, monkeypatch):
        """`/update` hardlinks entry points into ~/.local/bin — not a wrong copy."""
        root = _fake_checkout(tmp_path, names=("sdlc-push-guard",))
        local_bin = tmp_path / "local" / "bin"
        local_bin.mkdir(parents=True)
        os.link(root / ".venv" / "bin" / "sdlc-push-guard", local_bin / "sdlc-push-guard")
        result = _run(root, [local_bin, root / ".venv" / "bin"], monkeypatch)
        assert result.passed is True, result.message

    def test_main_checkout_venv_accepted_from_a_worktree(self, tmp_path, monkeypatch):
        """Doctor often runs from a worktree whose lane uses the main venv."""
        root = _fake_checkout(tmp_path)
        worktree = root / ".worktrees" / "lane-a"
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {root / '.git' / 'worktrees' / 'lane-a'}\n")
        (worktree / "pyproject.toml").write_text((root / "pyproject.toml").read_text())
        result = _run(worktree, [root / ".venv" / "bin"], monkeypatch)
        assert result.passed is True, result.message
        # The pass message names where they actually resolved (the main venv),
        # not the worktree's own bin that nothing on PATH points at.
        assert str(root / ".venv" / "bin") in result.message
        assert str(worktree / ".venv" / "bin") not in result.message

    def test_truncates_a_long_failure_list(self, tmp_path, monkeypatch):
        names = tuple(f"valor-tool-{i}" for i in range(9))
        root = _fake_checkout(tmp_path, names=names)
        result = _run(root, [tmp_path / "empty"], monkeypatch)
        assert result.passed is False
        assert "+4 more" in result.message

    def test_unreadable_pyproject_fails(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()
        result = _run(root, [tmp_path / "empty"], monkeypatch)
        assert result.passed is False
        assert "could not read" in result.message

    def test_empty_scripts_table_fails(self, tmp_path, monkeypatch):
        root = tmp_path / "repo"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
        result = _run(root, [tmp_path / "empty"], monkeypatch)
        assert result.passed is False
        assert "no [project.scripts]" in result.message


class TestDeclaredButNotInstalled:
    """The third state: venv on PATH, nothing shadowing, name simply absent.

    This is the routine one — a teammate pulls a new `[project.scripts]` entry
    and has not re-synced — and it used to be reported as "shadowed" with a
    remediation (reorder PATH) that was already satisfied.
    """

    def test_absent_name_is_not_called_shadowed(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path)
        # Declared in pyproject but never built into .venv/bin.
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'never-installed = "tools.never:main"\n'
        )
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)

        assert result.passed is False
        assert "1/4" in result.message
        assert "not installed" in result.message
        assert "shadowed" not in result.message, "PATH is already correct; nothing shadows it"
        assert "never-installed" in result.message

    def test_fix_points_at_uv_sync_not_at_path(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path)
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'never-installed = "tools.never:main"\n'
        )
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)

        assert result.fix
        assert "uv sync" in result.fix
        assert "export PATH" not in result.fix, "advising a PATH reorder that is already done"
        assert "never-installed" in result.fix

    def test_mixed_shadowed_and_absent_reports_both_remedies(self, tmp_path, monkeypatch):
        """A host can have both problems; the fix must not name only one."""
        root = _fake_checkout(tmp_path)
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'never-installed = "tools.never:main"\n'
        )
        shims = _stale_shim_dir(tmp_path, ("critique-roster-check",))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)

        assert result.passed is False
        assert "2/4" in result.message
        assert "shadowed" in result.message
        assert "not installed" in result.message
        assert result.fix
        assert "export PATH" in result.fix and "uv sync" in result.fix

    def test_a_shadowed_name_still_reads_as_shadowed(self, tmp_path, monkeypatch):
        """Control: the not-installed branch must not swallow the real thing."""
        root = _fake_checkout(tmp_path)
        shims = _stale_shim_dir(tmp_path, ("critique-roster-check",))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)

        assert "shadowed" in result.message
        assert "not installed" not in result.message
        assert result.fix and "uv sync" not in result.fix


class TestShimmedAndNeverInstalled:
    """The sub-state where both problems land on the same name.

    Every other fixture shims a name that `_fake_checkout` also installs, so
    "resolves to a stale shim AND was never built into the venv" was never
    constructed — and the not-installed tagging on that branch could be
    stripped with the suite still green.

    It is not exotic: pull a branch that adds a `[project.scripts]` entry, skip
    the sync, and if a stale user-site directory happens to carry that name you
    are in exactly this state. Telling the operator only to reorder PATH would
    leave them with a name that still does not exist.
    """

    def _checkout_with_a_declared_but_unbuilt_name(self, tmp_path):
        root = _fake_checkout(tmp_path)
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'never-installed = "tools.never:main"\n'
        )
        return root

    def test_shimmed_absent_name_is_tagged_not_installed(self, tmp_path, monkeypatch):
        root = self._checkout_with_a_declared_but_unbuilt_name(tmp_path)
        # The shim carries the one name the venv does NOT have.
        shims = _stale_shim_dir(tmp_path, ("never-installed",))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)

        assert result.passed is False
        assert "1/4" in result.message
        assert "never-installed" in result.message
        assert "not installed in the repo venv" in result.message, (
            "resolving to a shim does not make the name present in the venv"
        )
        assert str(shims) in result.message, "the operator still needs to know what won"

    def test_fix_names_uv_sync_for_a_shimmed_absent_name(self, tmp_path, monkeypatch):
        root = self._checkout_with_a_declared_but_unbuilt_name(tmp_path)
        shims = _stale_shim_dir(tmp_path, ("never-installed",))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)

        assert result.fix
        assert "uv sync" in result.fix, "PATH advice alone leaves the name non-existent"
        assert "never-installed" in result.fix

    def test_mixed_host_aggregate_suffix_counts_the_absent_names(self, tmp_path, monkeypatch):
        """One genuinely shadowed name plus one shimmed-and-absent name."""
        root = self._checkout_with_a_declared_but_unbuilt_name(tmp_path)
        shims = _stale_shim_dir(tmp_path, ("critique-roster-check", "never-installed"))
        result = _run(root, [shims, root / ".venv" / "bin"], monkeypatch)

        assert "2/4" in result.message
        assert "shadowed" in result.message
        assert "1 also not installed in the venv" in result.message, (
            "the aggregate suffix must count the absent names, not just mention them"
        )
        assert result.fix and "export PATH" in result.fix and "uv sync" in result.fix

    def test_fix_signals_omission_when_more_than_five_names_are_not_installed(
        self, tmp_path, monkeypatch
    ):
        """The `fix` string truncates the not-installed list at 5; it must say so.

        `message` already appends `(+N more)` on truncation (see
        `test_truncates_a_long_failure_list`); the `fix` string's own
        `uv sync` remediation list truncated silently, so an operator who
        installed exactly the five named entries and re-ran would still have
        unnamed broken scripts.
        """
        names = tuple(f"valor-tool-{i}" for i in range(9))
        root = _fake_checkout(tmp_path, names=())
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text()
            + "\n".join(f'{n} = "tools.{n.replace("-", "_")}:main"' for n in names)
            + "\n"
        )
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)

        assert result.passed is False
        assert result.fix
        assert "uv sync" in result.fix
        assert "(+4 more)" in result.fix, (
            "9 not-installed names, 5 shown -- the remaining 4 must be signaled, "
            "not silently dropped"
        )


class TestRegisteredInDoctor:
    def test_check_is_registered_before_system_tools(self):
        """`_check_system_tools` imports verify.py, which prepends the stale dir."""
        from tools.doctor import _check_console_scripts_resolve as check
        from tools.doctor import _check_system_tools, get_checks

        checks = get_checks()
        assert check in checks
        assert checks.index(check) < checks.index(_check_system_tools)
