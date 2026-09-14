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


_GATE_START = "# >>> registry-probe-gate"
_GATE_END = "# <<< registry-probe-gate"


def _gate_fragment(installer_src: str) -> str:
    """Slice the live registry-probe gate out of the installer.

    Executing the shipped fragment, rather than asserting substrings over it, is
    the only way to prove a three-way gate branches in all three directions:
    flipping `-eq 2` to `-ne 2` or dropping the `elif` leaves every substring
    assertion in this file green. `remote-update.sh`'s latch is tested the same
    way, on the same reasoning.
    """
    assert _GATE_START in installer_src and _GATE_END in installer_src, (
        "the registry-probe-gate markers are load-bearing; tests slice the fragment "
        "out of the shipped installer by them"
    )
    return installer_src.split(_GATE_START, 1)[1].split(_GATE_END, 1)[0]


def _run_gate(installer_src: str, tmp_path: Path, probe_exit: int) -> tuple[int, str]:
    """Run the gate against a stub interpreter that exits with `probe_exit`.

    The generated script sets `-euo pipefail` because the installer does. The
    whole reason to execute the fragment instead of asserting substrings over it
    is fidelity to the shipped context, and the shell mode is part of that
    context: under a default shell, a future edit inside the markers that trips
    `errexit` or `nounset` would abort the real install while these tests stayed
    green. Matches `_run_latch` in `tests/unit/test_update_reflections_callables.py`.
    """
    import subprocess

    project = tmp_path / "project"
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "verify_registry_without_shim.py").write_text("")
    stub = project / ".venv" / "bin" / "python"
    stub.write_text(f"#!/bin/sh\nexit {probe_exit}\n")
    stub.chmod(0o755)

    script = tmp_path / "gate.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'PROJECT_DIR="{project}"\n'
        f"{_gate_fragment(installer_src)}\n"
        "echo REACHED_END\n"
    )
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_gate_admits_a_resolvable_registry(installer_src, tmp_path):
    """Green control: exit 0 falls through to the rest of the install."""
    rc, out = _run_gate(installer_src, tmp_path, 0)
    assert rc == 0
    assert "REACHED_END" in out


def test_gate_aborts_the_install_when_callables_do_not_resolve(installer_src, tmp_path):
    rc, out = _run_gate(installer_src, tmp_path, 1)
    assert rc == 1
    assert "REACHED_END" not in out
    assert "did not resolve" in out


def test_gate_aborts_the_install_when_nothing_was_probed(installer_src, tmp_path):
    """The deliberate asymmetry with Step 4.65: exit 2 is a hard error here.

    `/update` treats the vacuous verdict as a warning and proceeds, because
    blocking a periodic restart on it wedges the cycle. Installing a scheduler
    with no registry to schedule has no such cost, so this caller refuses — and
    with its own message, since the two failures need different operator action.
    """
    rc, out = _run_gate(installer_src, tmp_path, 2)
    assert rc == 1
    assert "REACHED_END" not in out
    assert "no reflections registry found" in out
    assert "did not resolve" not in out, "the two failures must not collapse into one message"


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
