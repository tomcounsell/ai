"""Unit tests for opt-in Codex CLI provisioning (plan #2001 Task 4b).

The lane is off by default: with ``CODEX__INSTALL_ENABLED`` unset the
provisioner touches nothing (no npm, no ``codex --version``). Enabled
machines install when absent, upgrade below the floor, and skip at/above
it. Every failure degrades to a ``failed`` result — never a raise — so
the update run stays green.
"""

from __future__ import annotations

import subprocess

import scripts.update.codex_cli as codex_cli
from scripts.update.codex_cli import CodexCliResult


def _cfg(enabled=True, package="@openai/codex", min_version="0.144.3"):
    return (enabled, package, min_version)


def test_disabled_by_default_touches_nothing(monkeypatch):
    seen = []
    monkeypatch.setattr(codex_cli, "_read_config", lambda: (False, "@openai/codex", "0.144.3"))
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: seen.append(name) or "/usr/bin/x")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    result = codex_cli.install_or_update()
    assert isinstance(result, CodexCliResult)
    assert result.success is True and result.action == "disabled"
    # Disabled still reports the installed version (read-only PATH probe);
    # what must never happen is a subprocess (npm or `codex --version`).
    assert seen == ["codex"]


def test_disabled_reports_current_version(monkeypatch):
    monkeypatch.setattr(codex_cli, "_read_config", lambda: (False, "@openai/codex", "0.144.3"))
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: "0.154.0")
    assert codex_cli.install_or_update().version == "0.154.0"


def test_enabled_absent_binary_installs(monkeypatch):
    calls = []
    monkeypatch.setattr(codex_cli, "_read_config", lambda: _cfg())
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: None)
    monkeypatch.setattr(
        codex_cli, "_npm_install", lambda pkg: calls.append(pkg) or (True, "0.154.0", None)
    )
    result = codex_cli.install_or_update()
    assert result.success is True and result.action == "installed"
    assert result.version == "0.154.0" and calls == ["@openai/codex"]


def test_enabled_old_version_upgrades(monkeypatch):
    monkeypatch.setattr(codex_cli, "_read_config", lambda: _cfg())
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: "0.140.0")
    monkeypatch.setattr(codex_cli, "_npm_install", lambda pkg: (True, "0.154.0", None))
    result = codex_cli.install_or_update()
    assert result.success is True and result.action == "updated"


def test_enabled_current_version_skips_without_install(monkeypatch):
    monkeypatch.setattr(codex_cli, "_read_config", lambda: _cfg())
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: "0.154.0")
    monkeypatch.setattr(
        codex_cli,
        "_npm_install",
        lambda pkg: (_ for _ in ()).throw(AssertionError("must not install")),
    )
    result = codex_cli.install_or_update()
    assert result.success is True and result.action == "skipped"
    assert result.version == "0.154.0"


def test_enabled_at_floor_skips():
    assert codex_cli._version_tuple("0.144.3") >= codex_cli._version_tuple("0.144.3")
    assert codex_cli._version_tuple("0.144.30") >= codex_cli._version_tuple("0.144.3")
    assert not codex_cli._version_tuple("0.144.2") >= codex_cli._version_tuple("0.144.3")


def test_npm_missing_is_failed_not_raised(monkeypatch):
    monkeypatch.setattr(codex_cli, "_read_config", lambda: _cfg())
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: None)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: None)
    result = codex_cli.install_or_update()
    assert result.success is False and result.action == "failed"
    assert "npm" in (result.error or "").lower()


def test_install_failure_bounds_error_text(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(codex_cli, "_read_config", lambda: _cfg())
    monkeypatch.setattr(codex_cli, "_installed_version", lambda: None)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(
        codex_cli.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="E" * 500),
    )
    result = codex_cli.install_or_update()
    assert result.success is False and result.action == "failed"
    assert len(result.error or "") <= 200


def test_unexpected_exception_is_failed(monkeypatch):
    monkeypatch.setattr(
        codex_cli, "_read_config", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    result = codex_cli.install_or_update()
    assert result.success is False and result.action == "failed"


def test_parse_version_shapes():
    assert codex_cli._parse_version("codex-cli 0.154.0") == "0.154.0"
    assert codex_cli._parse_version("0.144.3\n") == "0.144.3"
    assert codex_cli._parse_version("no version here") is None


def test_installed_version_none_when_binary_missing(monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: None)
    assert codex_cli._installed_version() is None


def test_installed_version_none_on_timeout(monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda name: "/opt/homebrew/bin/codex")

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="codex", timeout=30)

    monkeypatch.setattr(codex_cli.subprocess, "run", _boom)
    assert codex_cli._installed_version() is None
