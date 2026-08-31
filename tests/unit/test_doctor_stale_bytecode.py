"""doctor must report source-tree bytecode orphaned by an interpreter pin bump (#2883).

Bytecode caches are namespaced per interpreter (`module.cpython-314.pyc`), so
bumping `.python-version` orphans the previous interpreter's caches rather than
replacing them. CPython never stats, validates, or deletes a cache whose magic
tag is not its own, so nothing invalidates them, nothing swept them, and nothing
warned that a pin bump created them.

They are not merely untidy: a stale pre-fix `.pyc` under `tools/__pycache__`
already failed a clean source tree once (#2807/#2809), because a guard read the
filesystem rather than tracked content.
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
