"""Installer contract for the reflection-scheduler subprocess (issue #1828).

Covers scripts/install_reflection_worker.sh:
  - has_worker_role() install/skip matrix — a machine owning a NON-Telegram project
    still installs (the #1379-avoidance case); a machine owning no project skips AND
    removes any stale plist; an unreadable config fails OPEN (installs).
  - The gate drops the `if proj.get("telegram")` clause (Telegram config is irrelevant).
  - The verify probe runs `python -m reflections --dry-run` with VALOR_LAUNCHD=1 after
    sourcing .env (env parity with the launchd runtime).
  - The moved config-copy (reflections.yaml + reflection_machine_filter) lives here, and
    the plist uses the KeepAlive long-lived lifecycle (not StartInterval cron).

These are static/structural assertions on the shipped script + plist — they do not
bootstrap a real launchd service.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_REPO = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO / "scripts" / "install_reflection_worker.sh"
_PLIST = _REPO / "com.valor.reflection-worker.plist"
_WORKER_INSTALLER = _REPO / "scripts" / "install_worker.sh"


@pytest.fixture(scope="module")
def installer_src() -> str:
    return _INSTALLER.read_text()


@pytest.fixture(scope="module")
def plist_src() -> str:
    return _PLIST.read_text()


def test_installer_exists_and_executable():
    assert _INSTALLER.exists()
    assert _INSTALLER.stat().st_mode & 0o111, "installer must be executable"


def test_gate_is_worker_role_not_bridge(installer_src):
    assert "has_worker_role" in installer_src
    # The bridge-role Telegram clause must be dropped — reflections run wherever the
    # worker runs, regardless of Telegram config (#1379 over-narrow-gating avoidance).
    assert 'proj.get("telegram")' not in installer_src
    assert 'get("telegram")' not in installer_src


def test_gate_qualifies_on_any_machine_match(installer_src):
    """The Python snippet exits 0 (qualify) as soon as a project's machine matches host."""
    assert 'proj.get("machine")' in installer_src
    assert "sys.exit(0)" in installer_src


def test_self_skip_and_stale_plist_removal(installer_src):
    assert "Skipping reflection-worker install" in installer_src
    assert "launchctl bootout" in installer_src
    assert "rm -f" in installer_src


def test_fails_open_on_unreadable_config(installer_src):
    # has_worker_role returns 0 (install) when config/venv/scutil are unavailable.
    assert "Fail open" in installer_src


def test_dry_run_env_parity(installer_src):
    """The verify probe carries VALOR_LAUNCHD=1 and runs -m reflections --dry-run, after
    .env is sourced — the same env resolution the plist runtime uses."""
    assert re.search(r"VALOR_LAUNCHD=1.*-m reflections --dry-run", installer_src)
    assert "source" in installer_src and ".env" in installer_src


def test_config_prep_moved_into_this_installer(installer_src):
    assert "reflection_machine_filter" in installer_src
    assert "reflections.yaml" in installer_src


def test_config_prep_removed_from_worker_installer():
    worker_src = _WORKER_INSTALLER.read_text()
    assert "reflection_machine_filter" not in worker_src
    # The worker installer no longer copies reflections.yaml (single owner).
    assert "Valor/reflections.yaml" not in worker_src


def test_plist_is_long_lived_not_cron(plist_src):
    assert "KeepAlive" in plist_src
    assert "ThrottleInterval" in plist_src
    assert "StartInterval" not in plist_src


def test_plist_sets_launchd_and_sources_env(plist_src):
    assert "VALOR_LAUNCHD" in plist_src
    assert "-m reflections" in plist_src
    # .env sourced in ProgramArguments via the /bin/bash -c sdlc-reflection idiom.
    assert "/bin/bash" in plist_src
    assert "source" in plist_src and ".env" in plist_src


def test_plist_lints_clean():
    """plutil -lint accepts the plist after path substitution (macOS only)."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("plutil"):
        pytest.skip("plutil not available (non-macOS)")

    raw = _PLIST.read_text()
    substituted = (
        raw.replace("__PROJECT_DIR__", str(_REPO))
        .replace("__HOME_DIR__", str(Path.home()))
        .replace("__SERVICE_LABEL__", "com.valor.reflection-worker")
    )
    with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as tf:
        tf.write(substituted)
        tf_path = tf.name
    try:
        result = subprocess.run(["plutil", "-lint", tf_path], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        Path(tf_path).unlink(missing_ok=True)
