"""Shell-level tests for scripts/remote-update.sh (issue #1898).

Runs the REAL script inside a sandboxed fake project: a tmp git clone with an
upstream to pull from, a stub ``launchctl`` on PATH, a stub ``.venv/bin/python``,
and an overridden ``$HOME``. No real services are touched.

Covers the #1898 bridge-restart tail sequence:

- bridge ``kickstart -k`` fires on a bridge-relevant diff + installed plist;
  NOT on an irrelevant diff; NOT without the plist.
- the update lock is released BEFORE the bridge kickstart (EXIT traps never
  fire on SIGKILL), so the next invocation is never green-skipped.
- a worker kickstart failure exits non-zero even when the terminal verify
  passes (the OR regression guard — a green verify must never mask a failed
  restart).
- the terminal ``verify_release`` step runs on EVERY cycle including no-op
  cron cycles, with the correct scope flag (``--skip-bridge`` only when a
  bridge restart is queued) and ``--since`` (restart moment when a worker
  kickstart happened, 0 otherwise).
- ``data/update-pending-report`` is staged pre-kickstart when the Telegram
  chat context is exported; the pure-cron path stages nothing.
- the lock-collision branch prints a distinct "bridge restart in progress"
  notice when the planned-restart marker is fresh.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_DIR = Path(__file__).parent.parent.parent
REAL_SCRIPT = PROJECT_DIR / "scripts" / "remote-update.sh"

LAUNCHCTL_STUB = """#!/bin/bash
LOCKSTATE=released
[ -d "$PROJ_DIR/data/update.lock" ] && LOCKSTATE=held
echo "LAUNCHCTL $* lock=$LOCKSTATE" >> "$CALL_LOG"
cmd="${1:-}"
if [ "$cmd" = "list" ]; then
    # WORKER_NOT_LISTED simulates the false-negative grep: the worker label is
    # in fact still registered in the domain, but `launchctl list` momentarily
    # doesn't report it (e.g. a stale worker process mid-transition). This
    # drives the "not loaded → bootstrap" branch of remote-update.sh.
    if [ -n "${WORKER_NOT_LISTED:-}" ]; then
        printf 'com.valor.bridge\\n'
    else
        printf 'com.valor.worker\\ncom.valor.bridge\\n'
    fi
    exit 0
fi
if [ "$cmd" = "kickstart" ]; then
    case "$*" in
        *.worker*) if [ -n "${WORKER_KICKSTART_FAIL:-}" ]; then exit 1; fi ;;
        *.bridge*) if [ -n "${BRIDGE_KICKSTART_FAIL:-}" ]; then exit 1; fi ;;
    esac
    exit 0
fi
if [ "$cmd" = "bootstrap" ]; then
    if [ -n "${BOOTSTRAP_FAIL:-}" ]; then
        # Mimic the real launchd EIO message on the classic stale-label failure.
        echo "Bootstrap failed: 5: Input/output error" >&2
        exit 1
    fi
    exit 0
fi
exit 0
"""

PYTHON_STUB = """#!/bin/bash
echo "PY $*" >> "$CALL_LOG"
if [ "${1:-}" = "-" ]; then cat > /dev/null; exit 0; fi
for a in "$@"; do
    if [ "$a" = "scripts.update.verify_release" ]; then
        echo "VERIFY $*" >> "$CALL_LOG"
        exit "${VERIFY_RC:-0}"
    fi
done
exit 0
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(repo: Path, relpath: str, msg: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {msg}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg, "--no-verify")


class Harness:
    """A sandboxed fake project the real remote-update.sh runs against."""

    def __init__(self, tmp_path: Path, bridge_plist: bool = True):
        self.root = tmp_path
        self.upstream = tmp_path / "upstream"
        self.proj = tmp_path / "proj"
        self.home = tmp_path / "home"
        self.stub_bin = tmp_path / "bin"
        self.call_log = tmp_path / "calls.log"

        # Upstream repo carrying the REAL script under test.
        self.upstream.mkdir()
        _git(self.upstream, "init", "-q")
        _git(self.upstream, "config", "user.email", "test@example.com")
        _git(self.upstream, "config", "user.name", "Test")
        _git(self.upstream, "config", "commit.gpgsign", "false")
        script_dst = self.upstream / "scripts" / "remote-update.sh"
        script_dst.parent.mkdir(parents=True)
        script_dst.write_text(REAL_SCRIPT.read_text())
        _commit(self.upstream, "bridge/mod.py", "initial")

        # Clone → the fake project dir the script executes in.
        subprocess.run(
            ["git", "clone", "-q", str(self.upstream), str(self.proj)],
            capture_output=True,
            check=True,
        )
        _git(self.proj, "config", "user.email", "test@example.com")
        _git(self.proj, "config", "user.name", "Test")

        # Stub venv python + launchctl.
        venv_python = self.proj / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text(PYTHON_STUB)
        venv_python.chmod(0o755)
        self.stub_bin.mkdir()
        launchctl = self.stub_bin / "launchctl"
        launchctl.write_text(LAUNCHCTL_STUB)
        launchctl.chmod(0o755)

        # Worker plist template + installed copy; bridge plist optional.
        (self.proj / "com.valor.worker.plist").write_text("<plist>__PROJECT_DIR__</plist>")
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.valor.worker.plist").write_text("<plist/>")
        if bridge_plist:
            (agents / "com.valor.bridge.plist").write_text("<plist/>")

    def push_upstream_commit(self, relpath: str, msg: str) -> None:
        """Land a commit in upstream so the next run's pull fast-forwards."""
        _commit(self.upstream, relpath, msg)

    def run(self, extra_env: dict | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CALL_LOG": str(self.call_log),
            "PROJ_DIR": str(self.proj),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.proj / "scripts" / "remote-update.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    def calls(self) -> str:
        return self.call_log.read_text() if self.call_log.exists() else ""

    def verify_lines(self) -> list[str]:
        return [line for line in self.calls().splitlines() if line.startswith("VERIFY ")]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_bridge_kickstart_on_relevant_diff_with_plist(harness):
    harness.push_upstream_commit("bridge/new_handler.py", "bridge-relevant")
    result = harness.run(
        extra_env={"UPDATE_REPORT_CHAT_ID": "12345", "UPDATE_REPORT_REPLY_TO": "678"}
    )
    calls = harness.calls()

    kick_lines = [
        line for line in calls.splitlines() if "kickstart" in line and "com.valor.bridge" in line
    ]
    assert kick_lines, f"bridge kickstart not invoked; calls:\n{calls}\nstdout:\n{result.stdout}"
    # Lock released BEFORE the kickstart fires (EXIT trap never runs on SIGKILL).
    assert "lock=released" in kick_lines[0]
    # Planned-restart marker written pre-kickstart.
    assert (harness.proj / "data" / "update-restart-in-progress").exists()
    # Pending report staged with the exported chat context + HEAD short SHA.
    report = json.loads((harness.proj / "data" / "update-pending-report").read_text())
    assert report["chat_id"] == "12345"
    assert report["reply_to"] == "678"
    assert report["sha"]
    assert report["worker_state"] == "worker restarted"
    assert float(report["staged_ts"]) > 0
    # Verify ran worker-scoped (--skip-bridge) with the restart moment.
    verify = harness.verify_lines()
    assert len(verify) == 1
    assert "--skip-bridge" in verify[0]
    assert "--since 0 " not in verify[0] + " "
    # Positive stdout marker gating handle_update_command's beacon poll.
    assert "Worker restarted" in result.stdout
    assert result.returncode == 0


def test_no_bridge_kickstart_on_irrelevant_diff(harness):
    harness.push_upstream_commit("docs/notes.md", "docs-only")
    result = harness.run()
    calls = harness.calls()
    assert not any(
        "kickstart" in line and "com.valor.bridge" in line for line in calls.splitlines()
    )
    # Both processes verified (no scope flag), nothing restarted (--since 0).
    verify = harness.verify_lines()
    assert len(verify) == 1
    assert "--skip-bridge" not in verify[0]
    assert "--since 0" in verify[0]
    assert result.returncode == 0


def test_no_bridge_kickstart_without_plist(tmp_path):
    harness = Harness(tmp_path, bridge_plist=False)
    harness.push_upstream_commit("bridge/new_handler.py", "bridge-relevant")
    result = harness.run()
    calls = harness.calls()
    assert not any(
        "kickstart" in line and "com.valor.bridge" in line for line in calls.splitlines()
    )
    verify = harness.verify_lines()
    assert len(verify) == 1
    assert "--skip-bridge" not in verify[0]
    assert result.returncode == 0


def test_worker_kickstart_failure_exits_nonzero_even_with_passing_verify(tmp_path):
    """OR regression guard: RESTART_FAILED=1 + verify exit 0 → STILL non-zero."""
    harness = Harness(tmp_path, bridge_plist=False)
    harness.push_upstream_commit("worker/mod.py", "worker-relevant")
    result = harness.run(
        extra_env={"WORKER_KICKSTART_FAIL": "1", "BOOTSTRAP_FAIL": "1", "VERIFY_RC": "0"}
    )
    assert result.returncode != 0
    assert "RESTART FAILED: worker" in result.stdout
    # The verify still ran and passed — and did not mask the failure.
    assert len(harness.verify_lines()) == 1


def test_worker_bootstrap_eio_recovers_via_kickstart(tmp_path):
    """Not-loaded branch: a false-negative `launchctl list` sends the script down
    the bootstrap path, but the label is actually still registered so bootstrap
    hits EIO (errno 5). The script must recover with `kickstart -k` rather than
    reporting a spurious RESTART FAILED. Regression for the stale-worker
    bootstrap EIO seen on "Valor the Bald"."""
    harness = Harness(tmp_path, bridge_plist=False)
    harness.push_upstream_commit("worker/mod.py", "worker-relevant")
    result = harness.run(
        extra_env={"WORKER_NOT_LISTED": "1", "BOOTSTRAP_FAIL": "1", "VERIFY_RC": "0"}
    )
    # bootstrap was attempted and failed; kickstart -k recovered it.
    calls = harness.calls()
    assert any("bootstrap" in line for line in calls.splitlines())
    assert any("kickstart" in line and "com.valor.worker" in line for line in calls.splitlines()), (
        f"kickstart recovery not invoked; calls:\n{calls}"
    )
    # No spurious failure line, clean exit, worker reported restarted.
    assert "RESTART FAILED" not in result.stdout
    assert result.returncode == 0
    assert "Worker restarted" in result.stdout
    # The recovery sets VERIFY_SINCE=$RESTART_TS just like the loaded branch, so
    # the #1898 release verify still runs exactly once against the restart moment.
    assert len(harness.verify_lines()) == 1


def test_worker_bootstrap_and_kickstart_both_fail_reports_failure(tmp_path):
    """Not-loaded branch: when BOTH the bootstrap and the kickstart fallback
    fail, the script surfaces a distinct scannable failure line and exits
    non-zero — the EIO recovery must not mask a genuinely dead worker."""
    harness = Harness(tmp_path, bridge_plist=False)
    harness.push_upstream_commit("worker/mod.py", "worker-relevant")
    result = harness.run(
        extra_env={
            "WORKER_NOT_LISTED": "1",
            "BOOTSTRAP_FAIL": "1",
            "WORKER_KICKSTART_FAIL": "1",
            "VERIFY_RC": "0",
        }
    )
    assert result.returncode != 0
    assert "RESTART FAILED: worker bootstrap/kickstart failed" in result.stdout
    # The raw launchd errno/message is surfaced for diagnosability, not swallowed.
    assert "Bootstrap failed: 5: Input/output error" in result.stdout


def test_bridge_kickstart_failure_exits_nonzero_and_withdraws_marker(harness):
    harness.push_upstream_commit("bridge/new_handler.py", "bridge-relevant")
    result = harness.run(
        extra_env={
            "BRIDGE_KICKSTART_FAIL": "1",
            "UPDATE_REPORT_CHAT_ID": "12345",
            "UPDATE_REPORT_REPLY_TO": "678",
        }
    )
    assert result.returncode != 0
    assert "RESTART FAILED: bridge" in result.stdout
    # No restart happened: marker + staged report withdrawn so the watchdog is
    # not suppressed and the still-alive bridge reports inline.
    assert not (harness.proj / "data" / "update-restart-in-progress").exists()
    assert not (harness.proj / "data" / "update-pending-report").exists()


def test_noop_cron_cycle_still_runs_verify_and_fails_on_stale(harness):
    """No new commits → the terminal verify still runs; positive staleness fails."""
    result = harness.run(extra_env={"VERIFY_RC": "1"})
    verify = harness.verify_lines()
    assert len(verify) == 1
    assert "--since 0" in verify[0]
    assert result.returncode == 1


def test_pure_cron_stages_no_pending_report(harness):
    """Bridge restart without a Telegram chat context stages nothing."""
    harness.push_upstream_commit("bridge/new_handler.py", "bridge-relevant")
    result = harness.run()  # no UPDATE_REPORT_* env vars
    assert not (harness.proj / "data" / "update-pending-report").exists()
    calls = harness.calls()
    assert any("kickstart" in line and "com.valor.bridge" in line for line in calls.splitlines())
    assert result.returncode == 0


def test_lock_released_before_self_kill_second_run_not_skipped(harness):
    """After a bridge-relevant run reaches the kickstart, the lock is gone —
    an immediate second invocation must not green-skip."""
    harness.push_upstream_commit("bridge/new_handler.py", "bridge-relevant")
    first = harness.run()
    assert first.returncode == 0
    second = harness.run()
    assert "Another update is already running" not in second.stdout
    assert "Pulling latest changes" in second.stdout


def test_lock_collision_with_fresh_marker_prints_distinct_notice(harness):
    (harness.proj / "data").mkdir(exist_ok=True)
    (harness.proj / "data" / "update.lock").mkdir()
    (harness.proj / "data" / "update-restart-in-progress").write_text("1234567890\n")
    result = harness.run()
    assert result.returncode == 0
    assert "bridge restart in progress" in result.stdout
    assert "Another update is already running" not in result.stdout


def test_lock_collision_without_marker_prints_generic_skip(harness):
    (harness.proj / "data").mkdir(exist_ok=True)
    (harness.proj / "data" / "update.lock").mkdir()
    result = harness.run()
    assert result.returncode == 0
    assert "Another update is already running" in result.stdout


def _seed_lock(harness, *, pid: str | None, age_s: int) -> Path:
    """Seed a pre-existing data/update.lock dir with an optional pid file and a
    controlled mtime (age_s seconds in the past)."""
    lock = harness.proj / "data" / "update.lock"
    lock.parent.mkdir(exist_ok=True)
    lock.mkdir()
    if pid is not None:
        (lock / "pid").write_text(f"{pid}\n")
    when = int(time.time()) - age_s
    os.utime(lock, (when, when))
    return lock


def test_lock_collision_young_dead_pid_is_reclaimed(harness):
    """Young lock whose recorded PID is dead → crashed run → reclaim immediately
    (the #2169 fix). The run must proceed rather than green-skip."""
    _seed_lock(harness, pid="999999", age_s=5)  # 999999 is not a live process
    result = harness.run()
    assert result.returncode == 0
    assert "Crashed update detected" in result.stdout
    assert "Another update is already running" not in result.stdout
    assert "Pulling latest changes" in result.stdout


def test_lock_collision_young_live_pid_is_skipped(harness):
    """Young lock whose recorded PID is alive → genuine concurrent run → skip."""
    _seed_lock(harness, pid=str(os.getpid()), age_s=5)  # pytest process is alive
    result = harness.run()
    assert result.returncode == 0
    assert "Another update is already running" in result.stdout
    assert "Pulling latest changes" not in result.stdout


def test_lock_collision_old_live_pid_reclaimed_age_backstop_wins(harness):
    """Lock older than the 600s TTL is reclaimed regardless of PID liveness —
    the age backstop is the ultimate authority (covers wedged-but-alive holders
    and PID reuse)."""
    _seed_lock(harness, pid=str(os.getpid()), age_s=700)  # alive pid, but stale
    result = harness.run()
    assert result.returncode == 0
    assert "Stale lock detected" in result.stdout
    assert "Another update is already running" not in result.stdout
    assert "Pulling latest changes" in result.stdout


def test_lock_collision_young_unknown_pid_is_skipped(harness):
    """Young lock with no pid file (legacy lock or mid-claim) → skip
    conservatively; the age backstop clears it later."""
    _seed_lock(harness, pid=None, age_s=5)
    result = harness.run()
    assert result.returncode == 0
    assert "Another update is already running" in result.stdout
    assert "Pulling latest changes" not in result.stdout


def test_marker_freshness_literal_matches_python_ttl():
    """The shell's hardcoded marker-freshness window must equal the shared
    Python constant (Decision 26 formula) — drift fails this test."""
    import re

    from scripts.update.service import UPDATE_RESTART_MARKER_TTL_SECONDS

    script = REAL_SCRIPT.read_text()
    match = re.search(r'\[ "\$marker_age" -lt (\d+) \]', script)
    assert match, "marker freshness check not found in remote-update.sh"
    assert int(match.group(1)) == UPDATE_RESTART_MARKER_TTL_SECONDS
