"""Shell-level tests for the install_*.sh launchd bootstrap call sites (#2013).

Parametrized over the five ``install_*.sh`` helpers hardened by #2013
(``install_worker.sh``, ``install_reflection_worker.sh``, ``install_nightly_tests.sh``,
``install_email_bridge.sh``, ``install_sdlc_reflection.sh``). Runs each REAL script
inside a sandboxed fake project: the real script + ``scripts/lib/launchctl.sh``, the
real ``com.valor.*.plist`` template it renders, a minimal ``.env``, a stub
``.venv/bin/python`` that special-cases each script's own precondition dry-run check
(``-m worker --dry-run``, ``-m pytest --json-report --help``, ``-m reflections
--dry-run``, ``-m tools.reflection_machine_filter ...``) and otherwise delegates
heredoc-piped scripts (env-var-injection into the rendered plist) to the REAL
interpreter running this test suite, a stub ``launchctl`` mimicking the errno-5 EIO
race with a configurable ``kickstart -k`` recovery, a stub ``pgrep`` that always
reports "not found" (satisfying ``install_email_bridge.sh``'s foreground-process
pre-check), and an overridden ``$HOME``. No real launchd services are touched.

Each of these installers runs under `set -euo pipefail` and is invoked one-per-service
(no "abort a batch" concern), so — unlike ``valor-service.sh`` — a genuine
bootstrap+kickstart double-failure legitimately exits non-zero for that one script
(``launchctl_bootstrap_fail_soft ... || exit 1``). The fail-soft contract for these
scripts is narrower: a *transient* errno-5 (bootstrap fails, kickstart recovers) must
NOT abort the install, and a genuine double-failure must surface the distinct WARNING
line before it exits — not swallow it silently.

``install_worker.sh`` has two call sites (main L174, watchdog L211) chained by `|| exit
1`: on a genuine double-failure the first `exit 1` fires before the watchdog call site
is ever reached, so its double-failure test only asserts on the first label.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).parent.parent.parent
REPO_SCRIPTS = REPO_ROOT / "scripts"
REAL_LAUNCHCTL_LIB = REPO_SCRIPTS / "lib" / "launchctl.sh"

# Mirrors LAUNCHCTL_BOOTSTRAP_RETRIES (default) — the base env sets it to 3 so
# count assertions on the bootstrap-retry loop (loop A) stay deterministic.
RETRIES = 3

# Per-LABEL live-PID-probe expectation (loop B, `verify-pid` 4th arg). The opt-in is
# per call site, NOT per script: only truly RESIDENT labels (RunAtLoad + KeepAlive)
# pass verify-pid. Both watchdogs are `StartInterval` one-shots with no persistent
# PID, so they must NOT get a print probe (a spurious WARNING at install_worker.sh's
# watchdog site would hit `|| exit 1` and abort a real install). Maps each script to
# the set of its labels that SHOULD emit a `launchctl print` probe.
PROBE_LABELS: dict[str, set[str]] = {
    "install_worker.sh": {"com.valor.worker"},  # NOT com.valor.worker-watchdog (StartInterval)
    "install_reflection_worker.sh": {"com.valor.reflection-worker"},
    "install_email_bridge.sh": {"com.valor.email-bridge"},
    "install_nightly_tests.sh": set(),
    "install_sdlc_reflection.sh": set(),
}

# script_name -> (plist template filenames to stage in PROJECT_DIR, ordered labels
# hit by launchctl_bootstrap_fail_soft calls in execution order).
INSTALL_SCRIPTS: dict[str, dict[str, list[str]]] = {
    "install_worker.sh": {
        "plists": ["com.valor.worker.plist"],
        "labels": ["com.valor.worker", "com.valor.worker-watchdog"],
    },
    "install_reflection_worker.sh": {
        "plists": ["com.valor.reflection-worker.plist"],
        "labels": ["com.valor.reflection-worker"],
    },
    "install_nightly_tests.sh": {
        "plists": ["com.valor.nightly-tests.plist"],
        "labels": ["com.valor.nightly-tests"],
    },
    "install_email_bridge.sh": {
        "plists": ["com.valor.email-bridge.plist"],
        "labels": ["com.valor.email-bridge"],
    },
    "install_sdlc_reflection.sh": {
        "plists": ["com.valor.sdlc-reflection.plist"],
        "labels": ["com.valor.sdlc-reflection"],
    },
}

LAUNCHCTL_STUB = """#!/bin/bash
echo "LAUNCHCTL $*" >> "$CALL_LOG"
cmd="${1:-}"
case "$cmd" in
    bootstrap)
        if [ -n "${BOOTSTRAP_FAIL:-}" ]; then
            echo "Bootstrap failed: 5: Input/output error" >&2
            exit 1
        fi
        if [ -n "${BOOTSTRAP_FAIL_TIMES:-}" ]; then
            prior=$(grep -c "^LAUNCHCTL bootstrap" "$CALL_LOG" 2>/dev/null || echo 0)
            if [ "$prior" -le "$BOOTSTRAP_FAIL_TIMES" ]; then
                echo "Bootstrap failed: 5: Input/output error" >&2
                exit 1
            fi
        fi
        exit 0
        ;;
    kickstart)
        if [ -n "${KICKSTART_FAIL:-}" ]; then
            exit 1
        fi
        exit 0
        ;;
    bootout)
        exit 0
        ;;
    print)
        if [ -n "${PRINT_NO_PID:-}" ]; then
            exit 0
        fi
        echo "    pid = 4242"
        exit 0
        ;;
    list)
        # Bare `launchctl list` (piped to grep -q) — nothing loaded.
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""

# install_email_bridge.sh refuses to install over a foreground (non-launchd) email
# bridge process; always report "not found" so that pre-check never blocks the install.
PGREP_STUB = """#!/bin/bash
echo "PGREP $*" >> "$CALL_LOG"
exit 1
"""

# A "smart" python stub: special-cases each install script's own precondition
# dry-run/help check (these would otherwise require a fully functional application
# environment to actually run), and delegates any heredoc-piped script (identified by
# a bare "-" first argument, e.g. the env-var-injection-into-plist steps) to the REAL
# interpreter running this test suite — which has python-dotenv and plistlib
# available — so those steps exercise real parsing/plist logic end to end.
PYTHON_STUB = """#!/bin/bash
echo "PY $*" >> "$CALL_LOG"
case "$*" in
    "-m worker --dry-run")
        exit "${WORKER_DRYRUN_RC:-0}"
        ;;
    "-m pytest --json-report --help")
        exit "${PYTEST_HELP_RC:-0}"
        ;;
    "-m reflections --dry-run")
        exit "${REFLECTIONS_DRYRUN_RC:-0}"
        ;;
    "-m tools.reflection_machine_filter"*)
        exit "${REFLECTION_FILTER_RC:-0}"
        ;;
esac
if [ "${1:-}" = "-" ]; then
    shift
    exec "$REAL_PYTHON" - "$@"
fi
exit 0
"""


class InstallHarness:
    """A sandboxed fake project one real install_*.sh script runs against."""

    def __init__(self, tmp_path: Path, script_name: str):
        self.script_name = script_name
        self.labels = INSTALL_SCRIPTS[script_name]["labels"]
        self.proj = tmp_path / "proj"
        self.home = tmp_path / "home"
        self.stub_bin = tmp_path / "bin"
        self.call_log = tmp_path / "calls.log"

        scripts_dir = self.proj / "scripts"
        (scripts_dir / "lib").mkdir(parents=True)
        (scripts_dir / script_name).write_text((REPO_SCRIPTS / script_name).read_text())
        (scripts_dir / "lib" / "launchctl.sh").write_text(REAL_LAUNCHCTL_LIB.read_text())

        for plist_name in INSTALL_SCRIPTS[script_name]["plists"]:
            (self.proj / plist_name).write_text((REPO_ROOT / plist_name).read_text())

        (self.proj / ".env").write_text("FOO=bar\n")

        venv_bin = self.proj / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python_stub = venv_bin / "python"
        python_stub.write_text(PYTHON_STUB)
        python_stub.chmod(0o755)

        self.stub_bin.mkdir()
        for name, content in (("launchctl", LAUNCHCTL_STUB), ("pgrep", PGREP_STUB)):
            stub = self.stub_bin / name
            stub.write_text(content)
            stub.chmod(0o755)

        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)

    def run(self, extra_env: dict | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CALL_LOG": str(self.call_log),
            "REAL_PYTHON": sys.executable,
            "LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP": "0",
            "LAUNCHCTL_BOOTSTRAP_RETRIES": str(RETRIES),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.proj / "scripts" / self.script_name)],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.proj,
            timeout=timeout,
        )

    def calls(self) -> str:
        return self.call_log.read_text() if self.call_log.exists() else ""

    def bootstrap_calls(self) -> list[str]:
        return [
            line for line in self.calls().splitlines() if line.startswith("LAUNCHCTL bootstrap")
        ]

    def kickstart_calls(self) -> list[str]:
        return [
            line for line in self.calls().splitlines() if line.startswith("LAUNCHCTL kickstart")
        ]

    def print_calls(self) -> list[str]:
        return [line for line in self.calls().splitlines() if line.startswith("LAUNCHCTL print")]


SCRIPT_NAMES = sorted(INSTALL_SCRIPTS)


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_happy_path_no_kickstart(tmp_path, script_name):
    """Bootstrap succeeds first try: no kickstart call, no WARNING, clean exit.

    Loop B (`launchctl print` live-PID probe) runs per RESIDENT label only; the
    watchdog (StartInterval) and every scheduled script emit no `print` call.
    """
    harness = InstallHarness(tmp_path, script_name)
    result = harness.run()
    assert not harness.kickstart_calls(), harness.calls()
    assert "WARNING: launchctl bootstrap+kickstart failed" not in result.stderr, result.stderr
    assert len(harness.bootstrap_calls()) == len(harness.labels), harness.calls()
    probe_labels = PROBE_LABELS[script_name]
    # Exactly one probe per resident label; the stub reports pid on the first probe.
    assert len(harness.print_calls()) == len(probe_labels), harness.calls()
    for label in harness.labels:
        got_probe = any(label in line for line in harness.print_calls())
        assert got_probe == (label in probe_labels), (label, harness.calls())
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_recover_via_kickstart(tmp_path, script_name):
    """Transient errno-5 bootstrap failure recovers via kickstart -k; no abort.

    With a permanent EIO, each label burns all RETRIES bootstrap attempts (loop A)
    before falling back to a single kickstart -k, so the bootstrap count is
    ``len(labels) * RETRIES``. Resident scripts then pass loop B (print → pid).
    """
    harness = InstallHarness(tmp_path, script_name)
    result = harness.run(extra_env={"BOOTSTRAP_FAIL": "1"})
    assert len(harness.bootstrap_calls()) == len(harness.labels) * RETRIES, harness.calls()
    assert len(harness.kickstart_calls()) == len(harness.labels), harness.calls()
    assert "WARNING: launchctl bootstrap+kickstart failed" not in result.stderr, result.stderr
    # The script proceeded through every call site rather than aborting under
    # `set -euo pipefail` at the first transient bootstrap failure.
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_double_failure_warns_before_nonzero_exit(tmp_path, script_name):
    """Genuine double-failure surfaces the distinct WARNING before exiting non-zero.

    These installers legitimately exit non-zero on a genuine double-failure (they are
    invoked one-per-service; there is no "abort a batch" concern) — but the failure
    must be surfaced via the distinct WARNING line, not silently swallowed.
    """
    harness = InstallHarness(tmp_path, script_name)
    result = harness.run(extra_env={"BOOTSTRAP_FAIL": "1", "KICKSTART_FAIL": "1"})
    first_label = harness.labels[0]
    assert f"WARNING: launchctl bootstrap+kickstart failed for {first_label}" in result.stderr, (
        result.stderr
    )
    assert result.returncode != 0


def test_retry_then_succeed(tmp_path):
    """Loop A: a transient errno-5 that clears on attempt 2 — no kickstart, no WARNING.

    Uses a resident single-label script so we also confirm loop B ran (print probe).
    """
    harness = InstallHarness(tmp_path, "install_reflection_worker.sh")
    result = harness.run(extra_env={"BOOTSTRAP_FAIL_TIMES": "1"})
    assert len(harness.bootstrap_calls()) == 2, harness.calls()
    assert not harness.kickstart_calls(), harness.calls()
    assert "WARNING: launchctl bootstrap+kickstart failed" not in result.stderr, result.stderr
    assert harness.print_calls(), harness.calls()
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_pid_verification_failure_warns(tmp_path):
    """Loop B: bootstrap succeeds but the label never shows a live PID → WARNING.

    A resident script whose `launchctl print` never emits a `pid =` line exhausts
    the RETRIES-bounded probe loop and surfaces the distinct WARNING + non-zero exit.
    """
    harness = InstallHarness(tmp_path, "install_reflection_worker.sh")
    label = harness.labels[0]
    result = harness.run(extra_env={"PRINT_NO_PID": "1"})
    assert not harness.kickstart_calls(), harness.calls()
    assert len(harness.print_calls()) == RETRIES, harness.calls()
    assert f"WARNING: launchctl bootstrap+kickstart failed for {label}" in result.stderr, (
        result.stderr
    )
    assert result.returncode != 0
