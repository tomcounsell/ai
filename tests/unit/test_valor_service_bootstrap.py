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
        # Env-gated (webui `restart` tests, #2123): report loaded so restart_bridge
        # and restart_worker take the fast kickstart -k path. Existing tests never
        # set LAUNCHCTL_LIST_LOADED, so their behavior is unchanged.
        if [ -n "${LAUNCHCTL_LIST_LOADED:-}" ]; then
            exit 0
        fi
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
# Env-gated (webui `restart` tests, #2123): report the bridge process as running
# so restart_bridge's post-kickstart is_running probe succeeds. Existing tests
# never set PGREP_BRIDGE_FOUND, so their behavior is unchanged.
if [ -n "${PGREP_BRIDGE_FOUND:-}" ]; then
    for a in "$@"; do
        case "$a" in
            *telegram_bridge*)
                echo 88888
                exit 0
                ;;
        esac
    done
fi
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


# === restart: restart_webui verify-new-PID + serving-port posture (#2123) ===
#
# These tests drive the REAL `restart` arm end-to-end. restart_bridge and
# restart_worker are steered down their fast kickstart paths via the env-gated
# LAUNCHCTL_LIST_LOADED / PGREP_BRIDGE_FOUND stub branches above, then
# restart_webui runs against the stubs below.
#
# NOTE on `kill`: bash's builtin `kill` preempts any PATH stub, so the KILL stub
# below is never actually invoked (it exists to keep the sandbox hermetic if the
# script ever switches to `command kill` / an external kill). Tests therefore do
# NOT assert on KILL log lines; the fake PIDs (4000000+) sit above the macOS
# pid_max so the builtin's kill -9 safely fails with ESRCH and is caught by the
# script's `|| true`. Port-state transitions are modeled inside the lsof stub
# instead: its FIRST call returns LSOF_PIDS_BEFORE (the pre-kill listener set),
# later calls return nothing until the python stub spawning `ui.app` drops a
# "spawned" marker, after which calls return LSOF_PIDS_AFTER.

FAKE_OLD_PID = "4000000"
FAKE_NEW_PID = "4000001"

LSOF_STUB = """#!/bin/bash
echo "LSOF $*" >> "$CALL_LOG"
count_file="$WEBUI_STATE_DIR/lsof_count"
n=$(cat "$count_file" 2>/dev/null || echo 0)
echo $((n + 1)) > "$count_file"
if [ -f "$WEBUI_STATE_DIR/spawned" ]; then
    if [ -n "${LSOF_PIDS_AFTER:-}" ]; then
        printf '%s\\n' $LSOF_PIDS_AFTER
        exit 0
    fi
    exit 1
fi
if [ "$n" -eq 0 ] && [ -n "${LSOF_PIDS_BEFORE:-}" ]; then
    printf '%s\\n' $LSOF_PIDS_BEFORE
    exit 0
fi
exit 1
"""

KILL_STUB = """#!/bin/bash
# Never reached from valor-service.sh (bash builtin kill preempts PATH lookup);
# present only so nothing in the sandbox falls through to a real /bin/kill.
echo "KILL $*" >> "$CALL_LOG"
exit 0
"""

CURL_STUB = """#!/bin/bash
echo "CURL $*" >> "$CALL_LOG"
if [ -n "${CURL_FAIL:-}" ]; then
    exit 7
fi
exit 0
"""

# Compresses the script's fixed sleeps (restart_bridge's 2s, restart_worker's 5s)
# and the webui poll intervals so the restart tests stay fast. Only installed by
# the webui fixture — existing tests keep real sleep timing.
SLEEP_STUB = """#!/bin/bash
echo "SLEEP $*" >> "$CALL_LOG"
exec /bin/sleep 0.05
"""

# Installed at the sandbox project's EXACT venv path ($PROJECT_DIR/.venv/bin/python):
# restart_webui spawns via the absolute "$VENV/bin/python", which bypasses PATH, so
# a PATH-level python stub would never be invoked. Spawning `-m ui.app` drops the
# "spawned" marker that flips the lsof stub into its post-spawn phase; the `-c`
# invocation from set_worker_restart_suppress_marker just exits 0.
PYTHON_VENV_STUB = """#!/bin/bash
echo "PYTHON $*" >> "$CALL_LOG"
for a in "$@"; do
    if [ "$a" = "ui.app" ]; then
        touch "$WEBUI_STATE_DIR/spawned"
        exit 0
    fi
done
exit 0
"""


class WebuiHarness(Harness):
    """Harness extended with the stubs the `restart` (webui) path needs."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.webui_state = tmp_path / "webui_state"
        self.webui_state.mkdir()
        (self.proj / "logs").mkdir()

        for name, body in (
            ("lsof", LSOF_STUB),
            ("kill", KILL_STUB),
            ("curl", CURL_STUB),
            ("sleep", SLEEP_STUB),
        ):
            stub = self.stub_bin / name
            stub.write_text(body)
            stub.chmod(0o755)

        venv_bin = self.proj / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        python_stub = venv_bin / "python"
        python_stub.write_text(PYTHON_VENV_STUB)
        python_stub.chmod(0o755)

    def run_restart(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {
            "WEBUI_STATE_DIR": str(self.webui_state),
            # Steer restart_bridge/restart_worker down their kickstart paths.
            "LAUNCHCTL_LIST_LOADED": "1",
            "PGREP_BRIDGE_FOUND": "1",
            # Tiny env-overridable verify windows so failure scenarios stay fast.
            "WEBUI_PORT_FREE_RETRIES": "3",
            "WEBUI_SERVE_RETRIES": "10",
            "WEBUI_CURL_TIMEOUT": "1",
        }
        if extra_env:
            env.update(extra_env)
        return self.run("restart", extra_env=env)


@pytest.fixture
def webui_harness(tmp_path: Path) -> WebuiHarness:
    return WebuiHarness(tmp_path)


def test_restart_webui_success_new_pid_serving(webui_harness):
    result = webui_harness.run_restart(
        extra_env={"LSOF_PIDS_BEFORE": FAKE_OLD_PID, "LSOF_PIDS_AFTER": FAKE_NEW_PID}
    )
    calls = webui_harness.calls()
    # Bridge and worker restarts completed before the webui portion ran.
    assert "Bridge restarted (PID: 88888)" in result.stdout
    assert "Worker restarted (PID: 99999)" in result.stdout
    # /health was actually probed — serving is the primary success signal.
    assert any("/health" in line for line in calls.splitlines() if line.startswith("CURL ")), calls
    assert f"Web UI restarted (PID: {FAKE_NEW_PID})" in result.stdout
    assert "ADVISORY" not in result.stderr, result.stderr
    assert "WARNING: Web UI restart failed" not in result.stderr, result.stderr
    assert result.returncode == 0


def test_restart_webui_pid_reuse_advisory_still_succeeds(webui_harness):
    result = webui_harness.run_restart(
        extra_env={"LSOF_PIDS_BEFORE": FAKE_OLD_PID, "LSOF_PIDS_AFTER": FAKE_OLD_PID}
    )
    # Serving PID is a member of the pre-kill set: advisory warning, NOT failure.
    assert "possible PID reuse" in result.stderr, result.stderr
    assert f"Web UI restarted (PID: {FAKE_OLD_PID})" in result.stdout
    assert "WARNING: Web UI restart failed" not in result.stderr, result.stderr
    assert result.returncode == 0


def test_restart_webui_not_serving_warns_and_exits_nonzero(webui_harness):
    result = webui_harness.run_restart(
        extra_env={
            "LSOF_PIDS_BEFORE": FAKE_OLD_PID,
            "LSOF_PIDS_AFTER": FAKE_NEW_PID,
            "CURL_FAIL": "1",
        }
    )
    # A PID binds but /health never answers: loud WARNING, never the success line.
    assert "WARNING: Web UI restart failed" in result.stderr, result.stderr
    assert "Web UI restarted" not in result.stdout, result.stdout
    # The restart) guard let bridge/worker restarts complete first...
    assert "Bridge restarted (PID: 88888)" in result.stdout
    assert "Worker restarted (PID: 99999)" in result.stdout
    # ...then `restart` exits non-zero for the webui failure.
    assert result.returncode != 0


def test_restart_webui_cold_start_succeeds(webui_harness):
    # No prior listener on the port: no "must differ from old" failure, plain success.
    result = webui_harness.run_restart(extra_env={"LSOF_PIDS_AFTER": FAKE_NEW_PID})
    assert f"Web UI restarted (PID: {FAKE_NEW_PID})" in result.stdout
    assert "ADVISORY" not in result.stderr, result.stderr
    assert "WARNING: Web UI restart failed" not in result.stderr, result.stderr
    assert result.returncode == 0
