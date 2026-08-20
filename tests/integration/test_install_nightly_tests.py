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
    """The snippet qualifies as soon as any project's machine matches the host.

    The verdict is carried by a printed token, not an exit code — see
    test_role_answer_requires_a_positive_token for why.
    """
    assert 'proj.get("machine")' in installer_src
    assert 'print("ROLE:OWNS" if owns else "ROLE:NONE")' in installer_src


def test_self_skip_and_stale_plist_removal(installer_src):
    assert "Skipping nightly-tests install" in installer_src
    assert "launchctl bootout" in installer_src
    assert "rm -f" in installer_src


# A per-process label prefix, so nothing this file does can name a REAL service.
#
# $HOME is sandboxed by tmp_path, but the launchd domain is NOT: the installer's
# skip path runs a genuine `launchctl bootout gui/<uid>/<LABEL>`. With the
# production prefix that unloads the live nightly detector until the next
# /update — on precisely the machines this PR deploys to. Inert here only
# because nothing is currently loaded, which is luck, not isolation.
_TEST_LABEL_PREFIX = f"com.valortest{os.getpid()}"
_TEST_PLIST_NAME = f"{_TEST_LABEL_PREFIX}.nightly-tests.plist"


def _run_installer(proj: Path, home: Path, config: Path | None, **extra_env):
    env = {
        **os.environ,
        "HOME": str(home),
        "SERVICE_LABEL_PREFIX": _TEST_LABEL_PREFIX,
        **extra_env,
    }
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
    plist = home / "Library" / "LaunchAgents" / _TEST_PLIST_NAME
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


@pytest.mark.parametrize(
    "stub, label",
    [
        # The realistic half-`uv sync` state. CPython orphaned from its stdlib
        # exits 1 — the SAME code that would otherwise mean "parsed cleanly,
        # this host owns nothing" and authorise an uninstall. This case is the
        # reason the gate keys on a stdout token rather than an exit code:
        # enumerating "bad" exit codes cannot separate them.
        ("echo 'Fatal Python error: Failed to import encodings module' >&2\nexit 1\n", "exit-1"),
        ("echo 'dyld: symbol not found' >&2\nexit 133\n", "exit-133"),
    ],
)
def test_broken_interpreter_does_not_uninstall_a_qualifying_host(tmp_path: Path, stub, label):
    """A venv python that exists but cannot run must fail closed.

    `-x` passes straight through a half-finished `uv sync` or an off-pin
    interpreter, so this is a live condition. On a host that genuinely
    qualifies, a broken interpreter must never be read as "owns nothing".
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    host = subprocess.run(
        ["scutil", "--get", "ComputerName"], capture_output=True, text=True
    ).stdout.strip()
    config = tmp_path / "mine.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": host}}}))

    # Safe to overwrite: _fake_checkout ships a plain stub file here, not a
    # symlink to the shared uv interpreter. See its docstring for why that
    # distinction matters well beyond this test.
    broken = proj / ".venv" / "bin" / "python"
    broken.write_text(f"#!/bin/bash\n{stub}")
    broken.chmod(0o755)

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert plist.read_text() == "<plist>existing</plist>", f"{label} interpreter uninstalled it"
    assert "Stale nightly-tests plist removed" not in result.stdout


def test_heredoc_exception_with_a_real_interpreter_fails_closed(tmp_path: Path):
    """The other exit-1 death: a working python, an exception in the heredoc.

    Uses the REAL interpreter — no stub — so it exercises the genuine path
    rather than simulating it. A config that is valid JSON but not an object
    makes `cfg.get(...)` raise outside the guarded block, so python exits 1
    having printed no token. Exit-code keying could not tell that apart from
    "parsed cleanly, this host owns nothing", which authorises removal; the
    token requirement does, without needing to anticipate this case.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    config = tmp_path / "list.json"
    config.write_text(json.dumps(["not", "an", "object"]))

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert plist.read_text() == "<plist>existing</plist>"
    assert "Stale nightly-tests plist removed" not in result.stdout
    assert "could not determine host role" in result.stdout


@pytest.mark.parametrize(
    "stub, expect_removed, label",
    [
        # Only stdout can carry an answer, so a token on stderr is not one.
        ('echo "ROLE:NONE" >&2\nexit 0\n', False, "token-on-stderr"),
        # A truncated write matches neither token.
        ('echo "ROLE:NON"\nexit 0\n', False, "truncated-token"),
        # Contradictory output means something other than the heredoc wrote to
        # stdout (a sitecustomize, a .pth, a wrapper). Refused explicitly, not
        # left to the case order.
        ('echo "ROLE:NONE"\necho "ROLE:OWNS"\nexit 0\n', False, "both-tokens"),
        # Silence is not an answer.
        ("exit 0\n", False, "no-token"),
        # Unrelated stdout noise must NOT hide a legitimate verdict.
        ('echo "some .pth banner"\necho "ROLE:NONE"\nexit 0\n', True, "noise-then-NONE"),
    ],
)
def test_token_cannot_lie(tmp_path: Path, stub, expect_removed, label):
    """The removal path is reachable only by a genuine, unambiguous ROLE:NONE.

    The gate was wrong twice by enumerating exit codes (`-eq 2`, then
    `-ne 0 && -ne 1`), each enumeration missing a real death mode. The token is
    a different shape, so the question becomes whether the token itself can be
    produced, suppressed, or corrupted by anything other than the code that
    means it. These are those vectors.

    Note the last case is the only one that may remove: noise around a real
    verdict must not suppress it, or a legitimate skip would silently start
    installing everywhere.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    host = subprocess.run(
        ["scutil", "--get", "ComputerName"], capture_output=True, text=True
    ).stdout.strip()
    config = tmp_path / "mine.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": host}}}))

    py = proj / ".venv" / "bin" / "python"
    py.write_text(f"#!/bin/bash\n{stub}")
    py.chmod(0o755)

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert plist.exists() is not expect_removed, f"{label}: wrong removal decision"


def _stub_scutil(tmp_path: Path, body: str) -> Path:
    """A PATH shim whose `scutil` behaves as `body` dictates."""
    binp = tmp_path / "stubbin"
    binp.mkdir(exist_ok=True)
    sc = binp / "scutil"
    sc.write_text(f"#!/bin/bash\n{body}")
    sc.chmod(0o755)
    return binp


def test_empty_computer_name_is_not_an_answer(tmp_path: Path):
    """`scutil` exiting 0 with no output means "I don't know who I am".

    An empty host matches no project, so the heredoc would emit a confident
    ROLE:NONE and authorise uninstalling the detector from a machine that
    genuinely qualifies. Reading an input is not the same as understanding it:
    this is the indeterminate-input-becomes-confident-negative failure the
    token was introduced to eliminate, reappearing one layer further in.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    host = subprocess.run(
        ["scutil", "--get", "ComputerName"], capture_output=True, text=True
    ).stdout.strip()
    config = tmp_path / "mine.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": host}}}))

    binp = _stub_scutil(tmp_path, 'if [ "$1" = "--get" ]; then echo ""; exit 0; fi\nexit 0\n')
    result = _run_installer(proj, home, config, PATH=f"{binp}:{os.environ['PATH']}")

    assert result.returncode == 0, result.stderr
    assert plist.exists(), "empty ComputerName uninstalled a qualifying host"
    assert "Stale nightly-tests plist removed" not in result.stdout


def test_verdict_from_an_incomplete_run_is_not_a_verdict(tmp_path: Path):
    """ROLE:NONE followed by a crash must not authorise removal.

    "An exit code cannot carry the answer" does not mean it carries nothing:
    rc==0 still means the process completed normally. A run that printed a
    verdict and then died produced it under unknown conditions. The DESTRUCTIVE
    branch therefore requires token AND rc==0; the install branch deliberately
    does not, because installing wrongly is recoverable and uninstalling is not.
    """
    proj = _fake_checkout(tmp_path)
    home, plist = _home_with_plist(tmp_path)
    host = subprocess.run(
        ["scutil", "--get", "ComputerName"], capture_output=True, text=True
    ).stdout.strip()
    config = tmp_path / "mine.json"
    config.write_text(json.dumps({"projects": {"x": {"machine": host}}}))

    py = proj / ".venv" / "bin" / "python"
    py.write_text('#!/bin/bash\necho "ROLE:NONE"\nexit 9\n')
    py.chmod(0o755)

    result = _run_installer(proj, home, config)

    assert result.returncode == 0, result.stderr
    assert plist.exists(), "a verdict from a crashed run authorised removal"
    assert "did not complete" in result.stdout


def test_role_answer_requires_a_positive_token(installer_src):
    """The heredoc emits an explicit token rather than encoding its verdict in
    an exit code.

    Documentation-grade only. This does NOT catch a revert to exit-code
    keying: reverting the bash while leaving both `print()` calls in place
    keeps this green (measured). `test_token_cannot_lie` is what actually
    catches that, via the five behavioural vectors. Kept because it names the
    contract at the point someone editing the heredoc would look.
    """
    assert "ROLE:NONE" in installer_src
    assert "ROLE:OWNS" in installer_src


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

    Ships a working *interpreter* at `.venv/bin/python` — not a real venv
    (there is no `pyvenv.cfg`, so no project site-packages). The role-gate
    heredoc only needs the stdlib, so that is sufficient. Without it the
    installer exits at the venv fail-closed guard, which would make the
    corrupt-config case below pass for the wrong reason, never reaching the
    JSON parse it is meant to exercise.

    It is a STUB that delegates to `$REAL_PYTHON`, deliberately not a symlink.
    A symlink here is a live hazard: tests that install a broken interpreter
    write to this path, and `write_text()` through a symlink writes to its
    target. `sys.executable` is itself a symlink into
    `~/.local/share/uv/python/cpython-3.14.6-.../bin/python3.14`, the shared
    uv-managed interpreter behind **every venv on this machine** — 24 worktree
    venvs at last count. Clobbering it would not break one fixture; it would
    require re-downloading through uv and re-syncing all of them. A plain file
    makes that mistake harmless instead of merely discouraged. Mirrors the
    `$REAL_PYTHON` delegation InstallHarness.PYTHON_STUB already uses.
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
    stub = venv_bin / "python"
    stub.write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
    stub.chmod(0o755)
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
