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

import pytest

from tools.doctor import _check_console_scripts_resolve

SCRIPTS = ("critique-roster-check", "critique-resume-probe", "sdlc-push-guard")


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_checkout(
    tmp_path: Path,
    names: tuple[str, ...] = SCRIPTS,
    *,
    venv_version: str | None = "3.14",
    pin_version: str | None = "3.14",
    write_pyvenv_cfg: bool = True,
    broken_interpreter: bool = False,
    python3_symlink_target: Path | None = None,
    shebang_body: str | None = None,
) -> Path:
    """A checkout with a `[project.scripts]` table and a populated `.venv/bin`.

    Builds a realistic venv so the interpreter-verification guard this file
    tests is actually reachable: a real `.venv/bin/python3`, a `pyvenv.cfg`
    naming its version, and a repo-root `.python-version` pin. Every generated
    shim's shebang points at that `python3` by default.

    - `venv_version`: the `version_info` written to `.venv/pyvenv.cfg`. `None`
      omits the `version_info` line (an unresolvable venv version, case 17).
    - `write_pyvenv_cfg`: `False` omits `.venv/pyvenv.cfg` entirely.
    - `pin_version`: the repo-root `.python-version` content. `None` omits the
      file (no pin, case 8).
    - `broken_interpreter`: makes `.venv/bin/python3` a dangling symlink
      (case 3, missing).
    - `python3_symlink_target`: makes `.venv/bin/python3` a symlink to this
      existing path instead of a plain file — the live shape, where the venv
      python is itself a symlink into a base interpreter outside every repo
      venv (case 7, the realpath guard).
    - `shebang_body`: overrides every generated shim's shebang line/body.
      Defaults to `#!{python3}\\n`, i.e. the fixture's own interpreter.
    """
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    table = "\n".join(f'{n} = "tools.{n.replace("-", "_")}:main"' for n in names)
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\n\n[project.scripts]\n{table}\n')

    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    python3 = venv_bin / "python3"
    if broken_interpreter:
        python3.symlink_to(venv_bin / "does-not-exist")
    elif python3_symlink_target is not None:
        python3.symlink_to(python3_symlink_target)
    else:
        _executable(python3)

    if write_pyvenv_cfg:
        cfg_lines = ["home = /usr/bin", "implementation = CPython"]
        if venv_version is not None:
            cfg_lines.append(f"version_info = {venv_version}")
        cfg_lines.append("include-system-site-packages = false")
        (root / ".venv" / "pyvenv.cfg").write_text("\n".join(cfg_lines) + "\n")

    if pin_version is not None:
        (root / ".python-version").write_text(f"{pin_version}\n")

    body = shebang_body if shebang_body is not None else f"#!{python3}\n"
    for name in names:
        _executable(root / ".venv" / "bin" / name, body)
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
        """A hardlinked copy of the venv file is not automatically the wrong copy.

        `_same_file` is general hardlink tolerance, not an artifact of any
        particular tool -- `/update` itself hardlinks exactly one script
        (`scripts/sdlc-tool`), which is not a `[project.scripts]` name.
        """
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


class TestWinningScriptInterpreter:
    """The interpreter-identity half of the check (#2748).

    Resolving into the right *directory* (#2665, above) says nothing about
    whether the winning file's shebang binds to a real interpreter. These
    cases build shims whose shebang points at a nonexistent, off-pin, or
    non-repo interpreter and assert the check reads and classifies it.
    """

    # --- Case 1 -----------------------------------------------------------

    def test_case1_control_matching_pin_passes(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "3 of 3 interpreter-verified" in result.message

    # --- Case 2: off-pin ----------------------------------------------------

    def test_case2_off_pin_interpreter_fails_naming_target_and_both_versions(
        self, tmp_path, monkeypatch
    ):
        root = _fake_checkout(tmp_path, venv_version="3.12", pin_version="3.14")
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        assert str(root / ".venv" / "bin" / "python3") in result.message
        assert "3.12" in result.message
        assert "3.14" in result.message

    # --- Case 3: missing (ordering guard for spike-3) -----------------------

    def test_case3_missing_interpreter_fails_naming_dangling_target(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path, broken_interpreter=True)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        python3 = root / ".venv" / "bin" / "python3"
        dangling = os.path.realpath(python3)
        assert str(python3) in result.message
        assert dangling in result.message

    # --- Case 4: outside -----------------------------------------------------

    def test_case4_outside_repo_venv_fails(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path, shebang_body="#!/usr/bin/python3\n")
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        assert "outside every repo venv" in result.message
        assert "/usr/bin/python3" in result.message

    # --- Case 5: hardlink, os.link, trailer present, no /update -------------

    def test_case5_hardlinked_copy_flagged_with_rebuild_and_trailer_no_update(
        self, tmp_path, monkeypatch
    ):
        root = _fake_checkout(
            tmp_path, names=("sdlc-push-guard",), venv_version="3.12", pin_version="3.14"
        )
        local_bin = tmp_path / "local" / "bin"
        local_bin.mkdir(parents=True)
        os.link(root / ".venv" / "bin" / "sdlc-push-guard", local_bin / "sdlc-push-guard")
        result = _run(root, [local_bin, root / ".venv" / "bin"], monkeypatch)
        assert result.passed is False
        assert result.fix
        assert "rm -rf .venv && uv sync --all-extras" in result.fix
        assert "/update" not in result.fix
        assert "Also remove the stale hardlinked copy at" in result.fix
        assert str(local_bin / "sdlc-push-guard") in result.fix

    # --- Case 6: unclassified forms, parameterized ---------------------------

    @pytest.mark.parametrize(
        "shebang_body",
        [
            "#!/bin/sh\n'''exec' /usr/bin/python3 \"$0\" \"$@\"\n'''\n",
            # uv --relocatable's dirname $0 variant: line 1 is the same
            # /bin/sh polyglot marker, only the exec target differs.
            '#!/bin/sh\n\'\'\'exec\' "$(dirname "$0")"/python3 "$0" "$@"\n\'\'\'\n',
            "#!/usr/bin/env python3\n",
            "",
            "xx/usr/bin/python3\n",
        ],
        ids=[
            "sh_polyglot",
            "relocatable_dirname",
            "env_indirection",
            "no_shebang",
            "malformed_shebang_prefix",
        ],
    )
    def test_case6_unclassified_shebang_forms_produce_no_finding(
        self, tmp_path, monkeypatch, shebang_body
    ):
        names = ("critique-roster-check", "critique-resume-probe", "sdlc-push-guard")
        root = _fake_checkout(tmp_path, names=names)
        _executable(root / ".venv" / "bin" / "target-script", shebang_body)
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'target-script = "tools.target_script:main"\n'
        )
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "3 of 4 interpreter-verified" in result.message

    # --- Case 7: realpath guard -----------------------------------------------

    def test_case7_symlinked_venv_python_is_not_realpathed(self, tmp_path, monkeypatch):
        external = tmp_path / "external" / "python3.14"
        external.parent.mkdir(parents=True)
        _executable(external)
        root = _fake_checkout(tmp_path, python3_symlink_target=external)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "outside" not in result.message

    # --- Case 8: no pin --------------------------------------------------------

    def test_case8_no_pin_disables_off_pin_but_missing_still_fails_with_disclosure(
        self, tmp_path, monkeypatch
    ):
        root = _fake_checkout(tmp_path, broken_interpreter=True, pin_version=None)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        assert "pin unresolvable; off-pin comparison skipped" in result.message

    def test_case8_no_pin_pass_path_discloses_skip(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path, pin_version=None)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "pin unresolvable; off-pin comparison skipped" in result.message

    # --- Case 9: grouping --------------------------------------------------------

    def test_case9_grouping_collapses_shared_bad_target_to_one_line(self, tmp_path, monkeypatch):
        names = ("critique-roster-check", "critique-resume-probe", "sdlc-push-guard")
        root = _fake_checkout(tmp_path, names=names, venv_version="3.12", pin_version="3.14")
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        target = str(root / ".venv" / "bin" / "python3")
        assert result.message.count(target) == 1
        for n in names:
            assert n in result.message

    # --- Case 10: mixed --------------------------------------------------------

    def test_case10_mixed_resolution_and_interpreter_failures_both_reported(
        self, tmp_path, monkeypatch
    ):
        names = ("critique-roster-check", "critique-resume-probe")
        root = _fake_checkout(tmp_path, names=names, venv_version="3.12", pin_version="3.14")
        (root / "pyproject.toml").write_text(
            (root / "pyproject.toml").read_text() + 'never-installed = "tools.never:main"\n'
        )
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False
        assert "not installed" in result.message
        assert "3.12" in result.message and "3.14" in result.message
        assert result.fix
        assert "uv sync" in result.fix
        assert "rm -rf .venv && uv sync --all-extras" in result.fix

    # --- Case 11: distinct diagnostic prefixes, shared command -----------------

    def test_case11_three_reasons_have_distinct_prefixes_and_shared_command(
        self, tmp_path, monkeypatch
    ):
        root_m = _fake_checkout(tmp_path / "m", names=("sdlc-push-guard",), broken_interpreter=True)
        r_m = _run(root_m, [root_m / ".venv" / "bin"], monkeypatch)

        root_o = _fake_checkout(
            tmp_path / "o", names=("sdlc-push-guard",), venv_version="3.12", pin_version="3.14"
        )
        r_o = _run(root_o, [root_o / ".venv" / "bin"], monkeypatch)

        root_x = _fake_checkout(
            tmp_path / "x", names=("sdlc-push-guard",), shebang_body="#!/usr/bin/python3\n"
        )
        r_x = _run(root_x, [root_x / ".venv" / "bin"], monkeypatch)

        fixes = [r_m.fix, r_o.fix, r_x.fix]
        assert all(f for f in fixes)
        assert all("rm -rf .venv && uv sync --all-extras" in f for f in fixes)
        diags = [f.split("In ", 1)[0].strip() for f in fixes]
        # Pairwise inequality alone is too weak here: each root lives in a
        # different tmp_path, so the three diagnostic prefixes would differ
        # by target *path* even if the reason-specific wording collapsed to
        # one shared sentence. Pin the reason-specific keyword each prefix
        # must carry, so a collapse to one generic sentence is caught even
        # when it happens to still mention a distinct target path each time.
        assert "does not exist" in diags[0]
        assert "Python 3.12" in diags[1] and "pin is 3.14" in diags[1]
        assert "outside every repo venv" in diags[2]
        assert len(set(diags)) == 3, diags

    # --- Case 12: pass-message contract -----------------------------------------

    def test_case12_pass_message_keeps_prefix_and_appends_no_venv_path(self, tmp_path, monkeypatch):
        names = ("critique-roster-check", "critique-resume-probe", "sdlc-push-guard")
        root = _fake_checkout(tmp_path, names=names)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert result.message.startswith(f"3 console scripts resolve into {root / '.venv' / 'bin'}")
        assert result.message.endswith("3 of 3 interpreter-verified")

    # --- Case 13: degenerate extractor inputs -----------------------------------

    def test_case13_degenerate_shebang_inputs(self, tmp_path):
        from tools.doctor import _shebang_interpreter

        zero_byte = tmp_path / "zero"
        zero_byte.write_bytes(b"")
        assert _shebang_interpreter(zero_byte) is None

        bare = tmp_path / "bare"
        bare.write_text("#!\n")
        assert _shebang_interpreter(bare) is None

        whitespace_only = tmp_path / "ws"
        whitespace_only.write_text("#!   \n")
        assert _shebang_interpreter(whitespace_only) is None

        with_flags = tmp_path / "flags"
        with_flags.write_text("#!/path/python -E -s\n")
        assert _shebang_interpreter(with_flags) == "/path/python"

    # --- Case 14: every resolved script is read, not just the first ------------

    def test_case14_default_fixture_reads_all_three(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path, names=("a", "b", "c"))
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "3 of 3 interpreter-verified" in result.message

    def test_case14_only_second_script_alphabetically_has_bad_shebang(self, tmp_path, monkeypatch):
        root = _fake_checkout(tmp_path, names=("a", "b", "c"))
        missing_target = root / ".venv" / "bin" / "does-not-exist-python"
        (root / ".venv" / "bin" / "b").write_text(f"#!{missing_target}\n")
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is False, (
            "under the dedup-guard bug, only the first script sighted per bin dir "
            "is ever read and this stays green"
        )
        assert "b" in result.message
        assert str(missing_target) in result.message

    # --- Case 15: two-entry venv_bins (the worktree topology) -------------------

    def test_case15_each_shim_checked_against_the_venv_its_own_shebang_names(
        self, tmp_path, monkeypatch
    ):
        main_root = _fake_checkout(tmp_path, names=(), venv_version="3.14", pin_version="3.14")
        worktree = main_root / ".worktrees" / "lane-a"
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {main_root / '.git' / 'worktrees' / 'lane-a'}\n")
        (worktree / "pyproject.toml").write_text(
            '[project]\nname = "x"\n\n[project.scripts]\n'
            'main-shim = "tools.main_shim:main"\n'
            'wt-shim = "tools.wt_shim:main"\n'
        )
        wt_venv_bin = worktree / ".venv" / "bin"
        wt_python3 = wt_venv_bin / "python3"
        _executable(wt_python3)
        (worktree / ".venv" / "pyvenv.cfg").write_text("version_info = 3.13\n")

        main_python3 = main_root / ".venv" / "bin" / "python3"
        _executable(wt_venv_bin / "main-shim", f"#!{main_python3}\n")
        _executable(wt_venv_bin / "wt-shim", f"#!{wt_python3}\n")

        result = _run(worktree, [wt_venv_bin], monkeypatch)
        assert result.passed is False, result.message
        # main-shim's target is the main venv, on the pin -> no finding.
        assert "main-shim" not in result.message
        # wt-shim's target is the worktree venv, off the pin -> flagged,
        # naming the *worktree's* version (3.13), not the main venv's (3.14).
        assert "wt-shim" in result.message
        assert "3.13" in result.message
        assert "pin is 3.14" in result.message
        assert str(wt_python3) in result.message

    # --- Case 16: divergent PATH spelling (hardlink-trailer guard) --------------

    def test_case16_divergent_path_spelling_gets_no_hardlink_trailer(self, tmp_path, monkeypatch):
        root = _fake_checkout(
            tmp_path, names=("sdlc-push-guard",), venv_version="3.12", pin_version="3.14"
        )
        symlinked_root = tmp_path / "repo-symlink"
        symlinked_root.symlink_to(root)
        result = _run(root, [symlinked_root / ".venv" / "bin"], monkeypatch)
        assert result.passed is False
        assert result.fix
        assert "Also remove the stale hardlinked copy at" not in result.fix

    # --- Case 17: unresolvable venv version --------------------------------------

    def test_case17_unresolvable_venv_version_excluded_from_ratio_no_none_leak(
        self, tmp_path, monkeypatch
    ):
        root = _fake_checkout(tmp_path, names=("a", "b", "c"), write_pyvenv_cfg=False)
        result = _run(root, [root / ".venv" / "bin", Path("/usr/bin")], monkeypatch)
        assert result.passed is True, result.message
        assert "is Python None" not in result.message
        assert ", 0 of 3 interpreter-verified" in result.message

    # --- Case 18: symlinked on-PATH copy gets no trailer -------------------------

    def test_case18_symlinked_on_path_copy_gets_no_trailer(self, tmp_path, monkeypatch):
        root = _fake_checkout(
            tmp_path, names=("sdlc-push-guard",), venv_version="3.12", pin_version="3.14"
        )
        local_bin = tmp_path / "local" / "bin"
        local_bin.mkdir(parents=True)
        os.symlink(root / ".venv" / "bin" / "sdlc-push-guard", local_bin / "sdlc-push-guard")
        result = _run(root, [local_bin, root / ".venv" / "bin"], monkeypatch)
        assert result.passed is False
        assert result.fix
        assert "Also remove the stale hardlinked copy at" not in result.fix

    # --- Case 19: grouped trailer names every hardlinked path --------------------

    def test_case19_grouped_trailer_names_every_hardlinked_path(self, tmp_path, monkeypatch):
        names = ("critique-roster-check", "critique-resume-probe")
        root = _fake_checkout(tmp_path, names=names, venv_version="3.12", pin_version="3.14")
        local_bin = tmp_path / "local" / "bin"
        local_bin.mkdir(parents=True)
        for n in names:
            os.link(root / ".venv" / "bin" / n, local_bin / n)
        result = _run(root, [local_bin, root / ".venv" / "bin"], monkeypatch)
        assert result.passed is False
        assert result.fix
        assert result.fix.count("Also remove the stale hardlinked cop") == 1
        assert str(local_bin / "critique-roster-check") in result.fix
        assert str(local_bin / "critique-resume-probe") in result.fix
