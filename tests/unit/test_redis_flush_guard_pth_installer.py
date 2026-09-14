"""Unit tests for scripts/update/redis_flush_guard_pth.py (#2645, Task 2).

Uses `tmp_path` FAKE venvs only -- never writes into a real venv. A "fake
venv" here is a directory with a `pyvenv.cfg` marker file and a
`lib/pythonX.Y/site-packages` directory, which is exactly what
`install_into` looks for.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys

import pytest

from scripts.update.redis_flush_guard_pth import (
    _PIN_PTH_CONTENT,
    _PIN_PTH_FILENAME,
    _PIN_SHIM_FILENAME,
    _PIN_SOURCE_PATH,
    _PTH_CONTENT,
    _PTH_FILENAME,
    _SHIM_CONTENT,
    _SHIM_FILENAME,
    install_into,
)


def _make_fake_venv(tmp_path, name="venv"):
    """Build a minimal fake venv: pyvenv.cfg + lib/python3.14/site-packages."""
    venv_dir = tmp_path / name
    venv_dir.mkdir()
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")
    site_packages = venv_dir / "lib" / "python3.14" / "site-packages"
    site_packages.mkdir(parents=True)
    return venv_dir, site_packages


class TestFreshInstall:
    def test_writes_all_four_files_with_correct_content(self, tmp_path):
        venv_dir, site_packages = _make_fake_venv(tmp_path)

        result = install_into(venv_dir)

        assert result["status"] == "installed"
        assert result["reason"] is None
        assert (site_packages / _SHIM_FILENAME).read_text() == _SHIM_CONTENT
        assert (site_packages / _PTH_FILENAME).read_text() == _PTH_CONTENT
        # The checkout-pin shim is a byte copy of its single source (#3141).
        assert (site_packages / _PIN_SHIM_FILENAME).read_text() == _PIN_SOURCE_PATH.read_text()
        assert (site_packages / _PIN_PTH_FILENAME).read_text() == _PIN_PTH_CONTENT

    def test_shim_written_before_pth(self, tmp_path, monkeypatch):
        """Ordering assertion (Race 1): the .pth must never reference a
        missing module, so the shim must exist on disk before the .pth
        does. Verified by recording write order via _atomic_write."""
        venv_dir, site_packages = _make_fake_venv(tmp_path)

        write_order = []
        import scripts.update.redis_flush_guard_pth as mod

        original_atomic_write = mod._atomic_write

        def _tracking_atomic_write(path, content):
            write_order.append(path.name)
            original_atomic_write(path, content)

        monkeypatch.setattr(mod, "_atomic_write", _tracking_atomic_write)

        result = mod.install_into(venv_dir)

        assert result["status"] == "installed"
        assert write_order == [
            _PIN_SHIM_FILENAME,
            _PIN_PTH_FILENAME,
            _SHIM_FILENAME,
            _PTH_FILENAME,
        ]


class TestIdempotence:
    def test_rerun_with_identical_content_reports_unchanged(self, tmp_path):
        venv_dir, _ = _make_fake_venv(tmp_path)

        first = install_into(venv_dir)
        second = install_into(venv_dir)

        assert first["status"] == "installed"
        assert second["status"] == "unchanged"
        assert second["reason"] is None


class TestSkipPaths:
    def test_not_a_venv_missing_path(self, tmp_path):
        result = install_into(tmp_path / "does-not-exist")
        assert result["status"] == "skipped"
        assert "not a venv" in result["reason"]

    def test_not_a_venv_no_pyvenv_cfg(self, tmp_path):
        plain_dir = tmp_path / "not-a-venv"
        plain_dir.mkdir()
        result = install_into(plain_dir)
        assert result["status"] == "skipped"
        assert "not a venv" in result["reason"]

    def test_no_site_packages(self, tmp_path):
        venv_dir = tmp_path / "venv-no-sitepkgs"
        venv_dir.mkdir()
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")
        result = install_into(venv_dir)
        assert result["status"] == "skipped"
        assert "site-packages" in result["reason"]

    def test_read_only_site_packages_skips_without_partial_write_or_crash(self, tmp_path):
        venv_dir, site_packages = _make_fake_venv(tmp_path)
        original_mode = site_packages.stat().st_mode
        os.chmod(site_packages, stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = install_into(venv_dir)
        finally:
            os.chmod(site_packages, original_mode)

        assert result["status"] == "skipped"
        assert "read-only" in result["reason"]
        # No partial write: neither target file exists.
        assert not (site_packages / _SHIM_FILENAME).exists()
        assert not (site_packages / _PTH_FILENAME).exists()


class TestAtomicReplace:
    def test_no_tmp_residue_after_install(self, tmp_path):
        venv_dir, site_packages = _make_fake_venv(tmp_path)

        install_into(venv_dir)

        leftovers = [p.name for p in site_packages.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestGeneratedShimContract:
    def test_shim_calls_arm_not_install(self, tmp_path):
        venv_dir, site_packages = _make_fake_venv(tmp_path)
        install_into(venv_dir)
        content = (site_packages / _SHIM_FILENAME).read_text()
        assert "tools.redis_flush_guard.arm()" in content
        assert "install()" not in content

    def test_shim_contains_no_disable_kill_switch(self, tmp_path):
        venv_dir, site_packages = _make_fake_venv(tmp_path)
        install_into(venv_dir)
        content = (site_packages / _SHIM_FILENAME).read_text()
        assert "REDIS_FLUSH_GUARD_DISABLE" not in content


class TestPthOrdering:
    def test_pth_filename_sorts_after_editable_impl_pth(self):
        # `_` (0x5F) < `z` (0x7A), so any `_`-prefixed .pth (like the repo's
        # real `_editable_impl_valor_bridge.pth`) sorts before this one.
        assert sorted(["_editable_impl_valor_bridge.pth", _PTH_FILENAME]) == [
            "_editable_impl_valor_bridge.pth",
            _PTH_FILENAME,
        ]

    def test_checkout_pin_pth_sorts_before_flush_guard_pth(self):
        """The flush-guard shim imports ``tools`` at site time; the checkout
        pin must already be at the front of sys.path by then (#3141)."""
        assert sorted([_PTH_FILENAME, _PIN_PTH_FILENAME]) == [_PIN_PTH_FILENAME, _PTH_FILENAME]


class TestFilePathLoadability:
    def test_loadable_by_file_path_without_importing_package(self):
        """The self-heal path in tools/redis_flush_guard.py loads this
        module via importlib.util.spec_from_file_location, bypassing
        `scripts.update`'s package __init__ (which eagerly imports ~30
        submodules and mutates sys.path[0]). Confirm that loading this way
        leaves both untouched."""
        module_path = (
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            / "scripts"
            / "update"
            / "redis_flush_guard_pth.py"
        )
        assert module_path.is_file()

        sys_path_0_before = sys.path[0]
        scripts_update_run_present_before = "scripts.update.run" in sys.modules

        spec = importlib.util.spec_from_file_location("_test_rfg_pth_installer", module_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "install_into")
        assert sys.path[0] == sys_path_0_before
        assert ("scripts.update.run" in sys.modules) == scripts_update_run_present_before


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
