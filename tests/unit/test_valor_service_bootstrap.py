"""Shell-level tests for scripts/valor-service.sh's launchd bootstrap call sites (#2013).

Runs the REAL script inside a sandboxed fake project: a tmp project dir carrying the
real ``valor-service.sh`` + ``scripts/lib/launchctl.sh``, a stub ``launchctl`` on PATH
that mimics the errno-5 EIO race with a configurable ``kickstart -k`` recovery, a stub
``pgrep`` that reports a process found only for worker-pattern queries, and an
overridden ``$HOME``. No real launchd services are touched.

Covers, for each of the three bare-bootstrap call sites hardened by #2013
(``install_bridge_components`` L548, ``bootstrap_plist_idempotent`` L392 — hit once
for the update-cron label and once for the bridge-watchdog label — and ``start_worker``
L724):

- happy path (bootstrap succeeds first try): no ``kickstart`` call, no WARNING —
  identical to today's observable behavior.
- recover path (bootstrap fails, ``kickstart -k`` recovers): kickstart is called, no
  WARNING is printed, and the script proceeds past the bootstrap call site instead of
  aborting under ``set -e``.
- genuine double-failure (both bootstrap and kickstart fail): the distinct
  ``WARNING: launchctl bootstrap+kickstart failed for <label>`` line appears on stderr
  AND the script still proceeds past the call site rather than hard-aborting — the
  whole point of the #2013 fail-soft fix.

``install``'s bridge-role branch exercises all three ``valor-service.sh`` call sites in
one run (bridge install L548 + ``bootstrap_plist_idempotent`` L392 for both the
update-cron and watchdog labels — bridge role defaults to true when no
``projects.json`` is present, so the sandbox never needs to fabricate one). Its
trailing ``status_bridge`` call independently returns non-zero in this sandbox (no real
bridge process is ever spawned — see the pgrep stub below) which aborts the script
under ``set -e`` AFTER all three bootstrap call sites have already run. That is an
unrelated environmental artifact of the sandbox, not a bootstrap failure, so the
``install`` tests assert on CALL_LOG/stdout/stderr markers (proving every stage was
reached) rather than on the final return code alone. ``worker-start`` gives a cleaner
signal — rc 0 in every scenario, including genuine double-failure — since nothing else
in that path fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_DIR = Path(__file__).parent.parent.parent
REAL_SCRIPT = PROJECT_DIR / "scripts" / "valor-service.sh"
REAL_LAUNCHCTL_LIB = PROJECT_DIR / "scripts" / "lib" / "launchctl.sh"

# Mirrors LAUNCHCTL_BOOTSTRAP_RETRIES (default) — the base env sets it to 3 so
# count assertions on the bootstrap-retry loop (loop A) stay deterministic.
RETRIES = 3

LAUNCHCTL_STUB = """#!/bin/bash
echo "LAUNCHCTL $*" >> "$CALL_LOG"
cmd="${1:-}"
case "$cmd" in
    bootstrap)
        if [ -n "${BOOTSTRAP_FAIL_NONEIO:-}" ]; then
            # Genuine (non-transient) plist error — NOT the errno-5 EIO shape.
            # Loop A must NOT retry this; it breaks straight to the kickstart fallback.
            echo "Bootstrap failed: 112: Could not find specified service" >&2
            exit 1
        fi
        if [ -n "${BOOTSTRAP_FAIL:-}" ]; then
            # Mimic the real launchd EIO message on the classic stale-label failure.
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
    enable)
        exit 0
        ;;
    list)
        shift
        if [ $# -eq 0 ]; then
            # Bare `launchctl list` (piped to grep -q / awk) — nothing loaded.
            exit 0
        fi
        # `launchctl list <label>` single-arg loaded probe — not loaded by default,
        # forcing every call site down the bootstrap (not kickstart-only) branch.
        exit 1
        ;;
    *)
        exit 0
        ;;
esac
"""

# Only "worker"-pattern pgrep queries report a process found. This keeps
# `stop_bridge`'s retry loop from spinning (bridge queries report "not found" so
# `stop_bridge` short-circuits immediately) while `is_worker_running` reports success
# right away for worker-start.
PGREP_STUB = """#!/bin/bash
echo "PGREP $*" >> "$CALL_LOG"
for a in "$@"; do
    case "$a" in
        *worker*)
            echo 99999
            exit 0
            ;;
    esac
done
exit 1
"""


class Harness:
    """A sandboxed fake project the real valor-service.sh runs against."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.proj = tmp_path / "proj"
        self.home = tmp_path / "home"
        self.stub_bin = tmp_path / "bin"
        self.call_log = tmp_path / "calls.log"

        scripts_dir = self.proj / "scripts"
        (scripts_dir / "lib").mkdir(parents=True)
        (scripts_dir / "valor-service.sh").write_text(REAL_SCRIPT.read_text())
        (scripts_dir / "lib" / "launchctl.sh").write_text(REAL_LAUNCHCTL_LIB.read_text())

        self.stub_bin.mkdir()
        launchctl = self.stub_bin / "launchctl"
        launchctl.write_text(LAUNCHCTL_STUB)
        launchctl.chmod(0o755)
        pgrep = self.stub_bin / "pgrep"
        pgrep.write_text(PGREP_STUB)
        pgrep.chmod(0o755)

        self.agents_dir = self.home / "Library" / "LaunchAgents"
        self.agents_dir.mkdir(parents=True)

    def run(
        self, command: str, extra_env: dict | None = None, timeout: int = 60
    ) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CALL_LOG": str(self.call_log),
            "LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP": "0",
            "LAUNCHCTL_BOOTSTRAP_RETRIES": str(RETRIES),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.proj / "scripts" / "valor-service.sh"), command],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.proj,
            timeout=timeout,
        )

    def calls(self) -> str:
        return self.call_log.read_text() if self.call_log.exists() else ""

    def launchctl_calls(self) -> list[str]:
        return [line for line in self.calls().splitlines() if line.startswith("LAUNCHCTL ")]

    def print_calls(self) -> list[str]:
        return [line for line in self.calls().splitlines() if line.startswith("LAUNCHCTL print")]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _seed_worker_plist(harness: Harness) -> None:
    (harness.agents_dir / "com.valor.worker.plist").write_text("<plist/>\n")


# === install: bridge install (L548) + bootstrap_plist_idempotent (L392, hit twice) ===


def test_install_happy_path_no_kickstart(harness):
    result = harness.run("install")
    calls = harness.calls()
    assert not any("kickstart" in line for line in calls.splitlines()), calls
    assert "WARNING" not in result.stderr, result.stderr
    assert "Bridge service installed and started" in result.stdout
    assert "Installing update polling..." in result.stdout
    assert "Installing bridge watchdog..." in result.stdout
    bootstrap_lines = [line for line in harness.launchctl_calls() if "bootstrap " in line]
    assert len(bootstrap_lines) == 3, calls
    # Only the resident bridge label runs loop B (`launchctl print` live-PID probe).
    # bridge-watchdog (StartInterval 60) and update-cron (StartInterval) are scheduled
    # one-shots with no persistent PID → they must NOT be probed. (endswith avoids the
    # `com.valor.bridge` prefix matching a hypothetical bridge-watchdog probe line.)
    print_lines = harness.print_calls()
    assert any(line.rstrip().endswith("com.valor.bridge") for line in print_lines), calls
    assert not any("com.valor.bridge-watchdog" in line for line in print_lines), calls
    assert not any("com.valor.update" in line for line in print_lines), calls
    # Reaches the trailing status_bridge tail (proving nothing aborted earlier), which
    # itself returns non-zero in this sandbox (no real bridge process is spawned) and
    # trips `set -e` — an environmental artifact unrelated to bootstrap behavior.
    assert "Bridge Status: STOPPED" in result.stdout
    assert result.returncode == 1


def test_install_recover_via_kickstart(harness):
    result = harness.run("install", extra_env={"BOOTSTRAP_FAIL": "1"})
    calls = harness.calls()
    bootstrap_lines = [line for line in harness.launchctl_calls() if "bootstrap " in line]
    kickstart_lines = [line for line in harness.launchctl_calls() if "kickstart " in line]
    # 3 bootstrap SITES, each burning RETRIES attempts (loop A) under permanent EIO
    # before its single kickstart -k fallback.
    assert len(bootstrap_lines) == 3 * RETRIES, calls
    assert len(kickstart_lines) == 3, calls
    assert "WARNING" not in result.stderr, result.stderr
    # The script proceeded past every bootstrap call site rather than aborting.
    assert "Bridge service installed and started" in result.stdout
    assert "Installing update polling..." in result.stdout
    assert "Installing bridge watchdog..." in result.stdout
    assert "Bridge Status: STOPPED" in result.stdout
    assert result.returncode == 1


def test_install_double_failure_warns_and_continues(harness):
    result = harness.run("install", extra_env={"BOOTSTRAP_FAIL": "1", "KICKSTART_FAIL": "1"})
    for label in ("com.valor.bridge", "com.valor.update", "com.valor.bridge-watchdog"):
        assert f"WARNING: launchctl bootstrap+kickstart failed for {label}" in result.stderr, (
            result.stderr
        )
    # Fail-soft: the script proceeds through every stage instead of hard-aborting at
    # the first bootstrap+kickstart failure — the whole point of the #2013 fix.
    assert "Bridge service installed and started" in result.stdout
    assert "Installing update polling..." in result.stdout
    assert "Installing bridge watchdog..." in result.stdout
    assert "Bridge Status: STOPPED" in result.stdout
    assert result.returncode == 1


# === worker-start (L724): the cleanest fail-soft signal — rc 0 in every scenario ===


def test_worker_start_happy_path_no_kickstart(harness):
    _seed_worker_plist(harness)
    result = harness.run("worker-start")
    calls = harness.calls()
    assert not any("kickstart" in line for line in calls.splitlines()), calls
    assert "WARNING" not in result.stderr, result.stderr
    # worker-start passes verify-pid → loop B probes the com.valor.worker label.
    assert any("com.valor.worker" in line for line in harness.print_calls()), calls
    assert "Worker started (PID: 99999)" in result.stdout
    assert result.returncode == 0


def test_worker_start_recover_via_kickstart(harness):
    _seed_worker_plist(harness)
    result = harness.run("worker-start", extra_env={"BOOTSTRAP_FAIL": "1"})
    calls = harness.calls()
    assert any(line.startswith("LAUNCHCTL bootstrap") for line in calls.splitlines()), calls
    assert any(line.startswith("LAUNCHCTL kickstart") for line in calls.splitlines()), calls
    assert "WARNING" not in result.stderr, result.stderr
    # Recovery via kickstart -k means worker-start still reports success.
    assert "Worker started (PID: 99999)" in result.stdout
    assert result.returncode == 0


def test_worker_start_non_eio_failure_skips_retry(harness):
    """Loop A gate: a genuine (non-errno-5) bootstrap failure must NOT be retried.

    Locks in the Risk 1 mitigation — a real plist error short-circuits to exactly ONE
    bootstrap attempt for com.valor.worker, then straight to the single kickstart -k
    fallback (which recovers), rather than burning RETRIES sleeps. A regression that
    widened the gate to retry all failures would make the bootstrap count == RETRIES.
    """
    _seed_worker_plist(harness)
    result = harness.run("worker-start", extra_env={"BOOTSTRAP_FAIL_NONEIO": "1"})
    calls = harness.calls()
    bootstrap_lines = [
        line for line in harness.launchctl_calls() if line.startswith("LAUNCHCTL bootstrap")
    ]
    # Exactly one bootstrap attempt — no loop-A retry on a non-EIO failure.
    assert len(bootstrap_lines) == 1, calls
    assert any(line.startswith("LAUNCHCTL kickstart") for line in calls.splitlines()), calls
    assert "WARNING" not in result.stderr, result.stderr
    assert "Worker started (PID: 99999)" in result.stdout
    assert result.returncode == 0


def test_worker_start_double_failure_warns_and_continues(harness):
    _seed_worker_plist(harness)
    result = harness.run("worker-start", extra_env={"BOOTSTRAP_FAIL": "1", "KICKSTART_FAIL": "1"})
    assert "WARNING: launchctl bootstrap+kickstart failed for com.valor.worker" in result.stderr, (
        result.stderr
    )
    # Fail-soft: worker-start still reports success — the genuine double-failure is
    # surfaced via the WARNING, not by aborting the whole invocation under `set -e`.
    assert "Worker started (PID: 99999)" in result.stdout
    assert result.returncode == 0


def test_worker_start_retry_then_succeed(harness):
    """Loop A: a transient errno-5 that clears on attempt 2 — no kickstart, no WARNING."""
    _seed_worker_plist(harness)
    result = harness.run("worker-start", extra_env={"BOOTSTRAP_FAIL_TIMES": "1"})
    calls = harness.calls()
    bootstrap_lines = [
        line for line in harness.launchctl_calls() if line.startswith("LAUNCHCTL bootstrap")
    ]
    assert len(bootstrap_lines) == 2, calls
    assert not any("kickstart" in line for line in calls.splitlines()), calls
    assert "WARNING" not in result.stderr, result.stderr
    assert any("com.valor.worker" in line for line in harness.print_calls()), calls
    assert "Worker started (PID: 99999)" in result.stdout
    assert result.returncode == 0


def test_worker_start_pid_verification_failure_warns(harness):
    """Loop B: bootstrap succeeds but the label never shows a live PID → WARNING.

    worker-start uses ``|| echo WARNING ... continuing``, so rc stays 0 — but the
    distinct loop-B WARNING for com.valor.worker must reach stderr.
    """
    _seed_worker_plist(harness)
    result = harness.run("worker-start", extra_env={"PRINT_NO_PID": "1"})
    calls = harness.calls()
    assert not any("kickstart" in line for line in calls.splitlines()), calls
    assert "WARNING: launchctl bootstrap+kickstart failed for com.valor.worker" in result.stderr, (
        result.stderr
    )
    assert result.returncode == 0
