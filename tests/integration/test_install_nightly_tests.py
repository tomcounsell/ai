"""Installer contract for the nightly regression detector (issue #2823).

Covers scripts/install_nightly_tests.sh:
  - The worktree refusal above the role gate — the plist is machine-global and
    hardcodes an absolute PROJECT_DIR, so installing from a lane worktree would
    aim the fleet's detector at a directory merge cleanup deletes.
  - has_worker_role() install/skip matrix — running the test suite requires a
    checkout and a worker, not a Telegram bridge (the #1379 over-narrow-gating
    class this replaces has_bridge_role() to avoid). A machine owning a
    NON-Telegram project still installs; a machine owning no project skips AND
    removes any stale plist; an unreadable or unparseable config fails CLOSED
    (does not install, and leaves any existing plist alone).
  - The installer's stable success-line marker, pinned so
    scripts/update/service.py::install_nightly_tests can classify "installed"
    vs "skipped" by matching the *success* text (fails closed on any other
    early exit).

Mixed: some structural assertions on the shipped script + plist, plus
behavioural cases that run the real installer against a sandboxed project
dir and fake $HOME. No real launchd service is bootstrapped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
    refusal_idx = installer_src.index("gitdir:.*/.git/worktrees/")
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


def _run_installer(proj: Path, home: Path, config: Path | None, **extra_env):
    env = {**os.environ, "HOME": str(home), "SERVICE_LABEL_PREFIX": "com.valor", **extra_env}
    if config is not None:
        env["PROJECTS_CONFIG_PATH"] = str(config)
    return subprocess.run(
        ["/bin/bash", str(proj / "scripts" / "install_nightly_tests.sh")],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _home_with_plist(tmp_path: Path, name: str = "home") -> tuple[Path, Path]:
    home = tmp_path / name
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    plist = home / "Library" / "LaunchAgents" / "com.valor.nightly-tests.plist"
    plist.write_text("<plist>existing</plist>")
    return home, plist


@pytest.mark.parametrize(
    "config_content, label",
    [(None, "absent"), ("{ this is not json", "corrupt")],
)
def test_undeterminable_role_never_removes_the_plist(tmp_path: Path, config_content, label):
    """Behavioural, not a source grep.

    An unreadable or unparseable projects.json means the check did not answer.
    "Did not answer" must never take the removal path — projects.json lives in
    the iCloud-synced vault that remote-update.sh already warns is
    intermittently unreadable, and the installer re-runs every 30 minutes, so
    conflating it with "this host owns nothing" would uninstall a healthy
    detector on any sync hiccup.

    Asserted on observable outcomes (plist bytes survive, no success marker),
    so reverting the guard fails this test even if its comments are left in
    place. The prior source-grep form stayed green against exactly that.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    if config_content is None:
        config = tmp_path / "does-not-exist.json"
    else:
        config = tmp_path / "bad.json"
        config.write_text(config_content)

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert _SUCCESS_MARKER not in result.stdout
    assert plist.read_text() == "<plist>existing</plist>", f"{label} config removed the plist"
    assert "Stale nightly-tests plist removed" not in result.stdout


def test_host_owning_nothing_does_remove_the_plist(tmp_path: Path):
    """The counterpart that keeps the fail-closed guard honest.

    A *parsed* config that genuinely assigns this host no project is a real
    answer, and must still take the removal path. Without this, making the
    installer refuse everything unconditionally would pass the test above.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    config = tmp_path / "other.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": "Some Other Machine"}}}))

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert "Stale nightly-tests plist removed" in result.stdout
    assert not plist.exists()


def test_broken_interpreter_does_not_uninstall_a_qualifying_host(tmp_path: Path):
    """A venv python that exists but cannot run must fail closed.

    The `-x` test passes straight through a half-finished `uv sync` or an
    off-pin interpreter. If such a python dies with any code other than 0/1,
    treating that as "owns nothing" uninstalls the detector from a host that
    actually qualifies.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    host = subprocess.run(
        ["scutil", "--get", "ComputerName"], capture_output=True, text=True
    ).stdout.strip()
    config = tmp_path / "mine.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": host}}}))

    # Executable, but dies with a code that is neither 0 nor 1.
    # unlink() first: _fake_checkout leaves a SYMLINK to the real interpreter
    # here, and write_text() on a symlink writes through to its target — which
    # would clobber the system python rather than the fixture.
    broken = proj / ".venv" / "bin" / "python"
    broken.unlink()
    broken.write_text("#!/bin/bash\necho 'dyld: symbol not found' >&2\nexit 133\n")
    broken.chmod(0o755)

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert plist.read_text() == "<plist>existing</plist>", "broken interpreter uninstalled it"
    assert "Stale nightly-tests plist removed" not in result.stdout


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


def _fake_checkout(tmp_path: Path) -> Path:
    """A non-worktree project dir carrying a copy of the installer.

    Ships a WORKING venv python. Without one the installer exits at the venv
    fail-closed guard, which would make the corrupt-config case below pass for
    the wrong reason — never reaching the JSON parse whose exit-2 path it is
    supposed to be exercising.
    """
    proj = tmp_path / "fake-main-checkout"
    scripts = proj / "scripts"
    scripts.mkdir(parents=True)
    (proj / ".git").mkdir()  # a directory, so the worktree refusal does NOT fire
    lib_dir = scripts / "lib"
    lib_dir.mkdir()
    (lib_dir / "launchctl.sh").write_text("launchctl_bootstrap_fail_soft() { return 0; }\n")
    installer_copy = scripts / "install_nightly_tests.sh"
    installer_copy.write_text(_INSTALLER.read_text())
    installer_copy.chmod(0o755)
    venv_bin = proj / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    return proj


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
