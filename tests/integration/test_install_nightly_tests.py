"""Installer contract for the nightly regression detector (issue #2823).

Covers scripts/install_nightly_tests.sh:
  - The worktree refusal above the role gate — the plist is machine-global and
    hardcodes an absolute PROJECT_DIR, so installing from a lane worktree would
    aim the fleet's detector at a directory merge cleanup deletes.
  - has_worker_role() install/skip matrix — running the test suite requires a
    checkout and a worker, not a Telegram bridge (the #1379 over-narrow-gating
    class this replaces has_bridge_role() to avoid). A machine owning a
    NON-Telegram project still installs; a machine owning no project skips AND
    removes any stale plist; an unreadable config fails OPEN (installs).
  - The installer's stable success-line marker, pinned so
    scripts/update/service.py::install_nightly_tests can classify "installed"
    vs "skipped" by matching the *success* text (fails closed on any other
    early exit).

These are static/structural assertions on the shipped script + plist — they do
not bootstrap a real launchd service.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

_REPO = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO / "scripts" / "install_nightly_tests.sh"
_PLIST = _REPO / "com.valor.nightly-tests.plist"
_SUCCESS_MARKER = "Nightly regression test service installed successfully."


@pytest.fixture(scope="module")
def installer_src() -> str:
    return _INSTALLER.read_text()


@pytest.fixture(scope="module")
def plist_src() -> str:
    return _PLIST.read_text()


def test_installer_exists_and_executable():
    assert _INSTALLER.exists()
    assert _INSTALLER.stat().st_mode & 0o111, "installer must be executable"


def test_worktree_refusal_precedes_role_gate(installer_src):
    """The worktree refusal must appear before has_worker_role() is defined
    or invoked, so a lane worktree never reaches the role gate at all."""
    refusal_idx = installer_src.index("gitdir:.*/\\.git/worktrees/")
    role_gate_idx = installer_src.index("has_worker_role()")
    assert refusal_idx < role_gate_idx


def test_worktree_refusal_matches_gitdir_shape(installer_src):
    assert "gitdir:" in installer_src
    assert ".git/worktrees/" in installer_src
    assert "grep -qE" in installer_src


def test_gate_is_worker_role_not_bridge(installer_src):
    assert "has_worker_role" in installer_src
    # The function must no longer be DEFINED or INVOKED — only an explanatory
    # comment may still name the old identifier for context.
    assert "has_bridge_role() {" not in installer_src
    assert "if ! has_bridge_role" not in installer_src
    # The bridge-role Telegram clause must be dropped — nightly tests run
    # wherever the worker runs, regardless of Telegram config.
    assert 'proj.get("telegram")' not in installer_src
    assert 'get("telegram")' not in installer_src


def test_gate_qualifies_on_any_machine_match(installer_src):
    """The Python snippet exits 0 (qualify) as soon as a project's machine matches host."""
    assert 'proj.get("machine")' in installer_src
    assert "sys.exit(0)" in installer_src


def test_self_skip_and_stale_plist_removal(installer_src):
    assert "Skipping nightly-tests install" in installer_src
    assert "launchctl bootout" in installer_src
    assert "rm -f" in installer_src


def test_fails_open_on_unreadable_config(installer_src):
    # has_worker_role returns 0 (install) when config/venv/scutil are unavailable.
    assert "Fail open" in installer_src


def test_success_marker_is_pinned(installer_src):
    """scripts/update/service.py classifies "installed" vs "skipped" by
    matching this exact line in stdout — it must not drift."""
    assert _SUCCESS_MARKER in installer_src


def test_service_py_pins_the_same_marker():
    service_src = (_REPO / "scripts" / "update" / "service.py").read_text()
    assert _SUCCESS_MARKER in service_src


def test_json_report_prerequisite_check(installer_src):
    assert "pytest-json-report" in installer_src


def test_plist_is_calendar_scheduled_not_keepalive(plist_src):
    assert "StartCalendarInterval" in plist_src
    assert "KeepAlive" not in plist_src


def test_plist_lints_clean():
    """plutil -lint accepts the plist after path substitution (macOS only)."""
    import shutil

    if not shutil.which("plutil"):
        pytest.skip("plutil not available (non-macOS)")

    raw = _PLIST.read_text()
    substituted = (
        raw.replace("__PROJECT_DIR__", str(_REPO))
        .replace("__HOME_DIR__", str(Path.home()))
        .replace("__SERVICE_LABEL__", "com.valor.nightly-tests")
    )
    with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as tf:
        tf.write(substituted)
        tf_path = tf.name
    try:
        result = subprocess.run(["plutil", "-lint", tf_path], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        Path(tf_path).unlink(missing_ok=True)


def test_worktree_refusal_fires_in_a_real_worktree_checkout(tmp_path: Path):
    """Behavioral check: a fabricated worktree-shaped `.git` file trips the
    refusal and exits 0 without ever reaching the role gate."""
    fake_project = tmp_path / "fake-worktree"
    fake_scripts = fake_project / "scripts"
    fake_scripts.mkdir(parents=True)
    (fake_project / ".git").write_text(f"gitdir: {tmp_path}/main-repo/.git/worktrees/some-slug\n")
    lib_dir = fake_scripts / "lib"
    lib_dir.mkdir()
    (lib_dir / "launchctl.sh").write_text("launchctl_bootstrap_fail_soft() { return 0; }\n")
    installer_copy = fake_scripts / "install_nightly_tests.sh"
    installer_copy.write_text(_INSTALLER.read_text())
    installer_copy.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(installer_copy)],
        cwd=fake_project,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "worktree checkout" in result.stdout
    # The role gate's own messaging must not appear — the worktree refusal
    # short-circuits before has_worker_role() runs.
    assert "Skipping nightly-tests install (no projects" not in result.stdout
