"""doctor's environment-hygiene checks: stale artifacts that fail silently.

Two checks, one shape — machine state that no longer matches the repo's pins, and
that degrades quietly rather than erroring.

`stale_bytecode` (#2883): bytecode caches are namespaced per interpreter
(`module.cpython-314.pyc`), so bumping `.python-version` orphans the previous
interpreter's caches rather than replacing them. CPython never stats, validates,
or deletes a cache whose magic tag is not its own, so nothing invalidated them,
nothing swept them, and nothing warned a pin bump created them. Not merely
untidy: a stale pre-fix `.pyc` under `tools/__pycache__` already failed a clean
source tree once (#2807/#2809), because a guard read the filesystem rather than
tracked content.

`shadowed_toolchain` (#2780): a stale user-site `uv` ahead of the real one on
PATH does not fail loudly — it succeeds and rewrites `uv.lock` in its own older
format, so the damage lands in a tracked file and reads as an ordinary diff.
`_check_console_scripts_resolve` cannot cover it, because `uv` is not one of this
repo's `[project.scripts]`.
"""

from __future__ import annotations

from pathlib import Path

from tools.doctor import (
    _check_stale_bytecode,
    _interpreter_tag_for_pin,
    _scan_off_pin_bytecode,
)


def _cache(root: Path, rel: str, name: str) -> Path:
    d = root / rel / "__pycache__"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\x00")
    return p


class TestInterpreterTag:
    def test_pin_maps_to_cpython_tag(self):
        assert _interpreter_tag_for_pin("3.14") == "cpython-314"

    def test_two_digit_minor(self):
        assert _interpreter_tag_for_pin("3.9") == "cpython-39"

    def test_patch_level_pin_uses_major_minor_only(self):
        assert _interpreter_tag_for_pin("3.14.3").startswith("cpython-314")


class TestScanFires:
    """The check must be able to fail — one nobody proved could fire is not a
    guard (#2658)."""

    def test_off_pin_cache_is_reported(self, tmp_path):
        stale = _cache(tmp_path, "tools", "mod.cpython-312.pyc")
        assert _scan_off_pin_bytecode(tmp_path, "3.14") == [stale]

    def test_multiple_off_pin_tags_all_reported(self, tmp_path):
        a = _cache(tmp_path, "tools", "a.cpython-312.pyc")
        b = _cache(tmp_path, "agent", "b.cpython-313.pyc")
        assert set(_scan_off_pin_bytecode(tmp_path, "3.14")) == {a, b}

    def test_check_reports_a_failure_with_the_count(self, tmp_path, monkeypatch):
        _cache(tmp_path, "tools", "a.cpython-312.pyc")
        _cache(tmp_path, "tools", "b.cpython-312.pyc")
        monkeypatch.setattr("tools.doctor.PROJECT_DIR", tmp_path)
        monkeypatch.setattr("agent.worktree_manager.repo_interpreter_pin", lambda _p: "3.14")

        result = _check_stale_bytecode()
        assert result.passed is False
        assert "2 off-pin" in result.message
        assert "cpython-312" in result.message
        assert result.fix and "rm -f" in result.fix


class TestScanDoesNotOverreach:
    def test_on_pin_cache_is_left_alone(self, tmp_path):
        _cache(tmp_path, "tools", "mod.cpython-314.pyc")
        assert _scan_off_pin_bytecode(tmp_path, "3.14") == []

    def test_venv_is_skipped(self, tmp_path):
        """A venv is replaced wholesale by `rm -rf .venv && uv sync`, and
        `_check_worktree_interpreters` already owns drift there."""
        _cache(tmp_path, ".venv/lib/site-packages/x", "mod.cpython-312.pyc")
        assert _scan_off_pin_bytecode(tmp_path, "3.14") == []

    def test_worktrees_are_skipped(self, tmp_path):
        _cache(tmp_path, ".worktrees/lane/tools", "mod.cpython-312.pyc")
        assert _scan_off_pin_bytecode(tmp_path, "3.14") == []

    def test_untagged_pyc_is_left_alone(self, tmp_path):
        """A name with no interpreter tag is not attributable to one."""
        d = tmp_path / "tools"
        d.mkdir(parents=True)
        (d / "legacy.pyc").write_bytes(b"\x00")
        assert _scan_off_pin_bytecode(tmp_path, "3.14") == []

    def test_clean_tree_passes(self, tmp_path, monkeypatch):
        _cache(tmp_path, "tools", "mod.cpython-314.pyc")
        monkeypatch.setattr("tools.doctor.PROJECT_DIR", tmp_path)
        monkeypatch.setattr("agent.worktree_manager.repo_interpreter_pin", lambda _p: "3.14")

        result = _check_stale_bytecode()
        assert result.passed is True
        assert "no off-pin bytecode" in result.message


class TestDegradesGracefully:
    def test_missing_pin_does_not_fail_the_check(self, tmp_path, monkeypatch):
        """No pin means no reference to compare against, not a broken machine."""
        monkeypatch.setattr("tools.doctor.PROJECT_DIR", tmp_path)
        monkeypatch.setattr("agent.worktree_manager.repo_interpreter_pin", lambda _p: None)

        result = _check_stale_bytecode()
        assert result.passed is True
        assert "skipping" in result.message


class TestRegisteredWithDoctor:
    def test_check_runs_as_part_of_doctor(self):
        """A check nothing invokes reports nothing."""
        import inspect

        import tools.doctor as doctor

        source = inspect.getsource(doctor)
        assert "_check_stale_bytecode," in source, (
            "check must be registered in doctor's check list, not merely defined"
        )


class TestShadowedToolchain:
    """A stale user-site `uv` must be reported (#2780).

    `_check_console_scripts_resolve` covers this repo's own `[project.scripts]`,
    which cannot include `uv` — and `uv` is the one that matters most, because an
    old `uv` does not fail loudly. It succeeds and rewrites `uv.lock` in its own
    older format, so the damage lands in a tracked file and reads as an ordinary
    diff.

    Measured live on 2026-08-31: `~/Library/Python/3.12/bin/uv` was v0.6.10
    (built 2025-03-25) and won PATH resolution over Homebrew's v0.11.3.
    """

    def _shimmed(self, tmp_path, monkeypatch, tool="uv"):
        shim_dir = tmp_path / "Library" / "Python" / "3.12" / "bin"
        shim_dir.mkdir(parents=True)
        shim = shim_dir / tool
        shim.write_text("#!/bin/sh\n")
        shim.chmod(0o755)
        monkeypatch.setattr("tools.doctor.Path.home", staticmethod(lambda: tmp_path))
        return shim

    def test_fires_when_a_tool_resolves_into_user_site(self, tmp_path, monkeypatch):
        from tools.doctor import _check_shadowed_toolchain

        shim = self._shimmed(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "shutil.which", lambda t: str(shim) if t == "uv" else "/opt/homebrew/bin/uvx"
        )

        result = _check_shadowed_toolchain()
        assert result.passed is False
        assert "uv" in result.message
        assert str(shim) in result.message
        assert result.fix

    def test_message_explains_why_an_old_uv_is_dangerous(self, tmp_path, monkeypatch):
        """An operator who thinks this is cosmetic will not act on it."""
        from tools.doctor import _check_shadowed_toolchain

        shim = self._shimmed(tmp_path, monkeypatch)
        monkeypatch.setattr("shutil.which", lambda t: str(shim) if t == "uv" else None)

        assert "uv.lock" in _check_shadowed_toolchain().message

    def test_passes_when_tools_resolve_outside_user_site(self, tmp_path, monkeypatch):
        from tools.doctor import _check_shadowed_toolchain

        self._shimmed(tmp_path, monkeypatch)
        monkeypatch.setattr("shutil.which", lambda _t: "/opt/homebrew/bin/uv")

        assert _check_shadowed_toolchain().passed is True

    def test_absent_tool_is_not_reported_as_shadowed(self, tmp_path, monkeypatch):
        from tools.doctor import _check_shadowed_toolchain

        self._shimmed(tmp_path, monkeypatch)
        monkeypatch.setattr("shutil.which", lambda _t: None)

        assert _check_shadowed_toolchain().passed is True

    def test_no_user_site_dir_passes(self, tmp_path, monkeypatch):
        from tools.doctor import _check_shadowed_toolchain

        monkeypatch.setattr("tools.doctor.Path.home", staticmethod(lambda: tmp_path))
        assert _check_shadowed_toolchain().passed is True

    def test_check_is_registered_with_doctor(self):
        import inspect

        import tools.doctor as doctor

        assert "_check_shadowed_toolchain," in inspect.getsource(doctor)
