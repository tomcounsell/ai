"""Unit tests for tools/venv_health.py (issue #2050)."""

import subprocess
import sys

from tools import venv_health


class TestCheckModules:
    def test_no_missing_modules_on_healthy_env(self):
        """pytest and xdist are dev deps of this repo; both should be importable
        in the environment running this test."""
        missing = venv_health.check_modules()
        assert missing == []

    def test_records_missing_module_without_aborting_on_first(self, monkeypatch):
        """A module missing partway through the list must not short-circuit --
        every missing module is reported."""
        monkeypatch.setattr(
            venv_health,
            "_REQUIRED_MODULES",
            ("pytest", "definitely_not_a_real_module_xyz", "xdist"),
        )
        missing = venv_health.check_modules()
        assert missing == ["definitely_not_a_real_module_xyz"]


class TestCheckBinaries:
    def test_no_missing_binaries_on_healthy_env(self):
        missing = venv_health.check_binaries()
        assert missing == []

    def test_records_missing_binary(self, monkeypatch):
        monkeypatch.setattr(
            venv_health, "_REQUIRED_BINARIES", ("definitely-not-a-real-binary-xyz",)
        )
        missing = venv_health.check_binaries()
        assert missing == ["definitely-not-a-real-binary-xyz"]


class TestCheckHealth:
    def test_healthy_env_reports_nothing_missing(self):
        assert venv_health.check_health() == []

    def test_combines_module_and_binary_misses(self, monkeypatch):
        monkeypatch.setattr(venv_health, "_REQUIRED_MODULES", ("not_a_real_module_a",))
        monkeypatch.setattr(venv_health, "_REQUIRED_BINARIES", ("not-a-real-binary-b",))
        missing = venv_health.check_health()
        assert missing == ["not_a_real_module_a", "not-a-real-binary-b"]


class TestMainExitCode:
    def test_main_returns_zero_on_healthy_env(self):
        assert venv_health.main() == 0

    def test_main_returns_one_when_something_missing(self, monkeypatch):
        monkeypatch.setattr(venv_health, "_REQUIRED_MODULES", ("not_a_real_module_a",))
        monkeypatch.setattr(venv_health, "_REQUIRED_BINARIES", ())
        assert venv_health.main() == 1


class TestCliInvocation:
    """Drive `python -m tools.venv_health` end-to-end as the CLI entry point."""

    def test_cli_exits_zero_on_healthy_env(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.venv_health"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "OK" in result.stdout
