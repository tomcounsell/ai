"""``scripts/classification-eval-uncontended.sh`` (#3421, Risk 8).

The wrapper is the one sanctioned way to run a comparison on a host that
serves live traffic: it unloads exactly the loaded contended labels, runs
the comparison, and restores them on every exit path. Every external
command it runs is read through an environment variable, so these tests
point them at fake executables in a temp dir that append their argv to a
log and never touch launchd, the bridge, or a network.
"""

from __future__ import annotations

import os
import signal
import subprocess
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "classification-eval-uncontended.sh"
ALL_LOADED = (
    "-\t0\tcom.valor.bridge\n123\t0\tcom.valor.worker\n456\t0\tcom.valor.reflection-worker\n"
)
CONNECTED = "Connected to Telegram"


class Harness:
    """Fake executables plus the env that points the wrapper at them."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        launchctl_list: str,
        connect_on_restart: bool,
        failing_service_commands: tuple[str, ...] = (),
    ):
        self.dir = tmp_path
        self.log = tmp_path / "calls.log"
        self.bridge_log = tmp_path / "bridge.log"
        self.runs_dir = tmp_path / "data"
        self.runs_dir.mkdir()
        self.started = tmp_path / "runner.started"
        connect = f"echo '{CONNECTED}' >> \"$BRIDGE_LOG\"" if connect_on_restart else ":"
        failing = " ".join(failing_service_commands)
        self._fake(
            "valor-service.sh",
            f"""
            echo "valor-service $*" >> "{self.log}"
            for failing in {failing}; do
                if [ "$1" = "$failing" ]; then echo "fake $1 failed" >&2; exit 1; fi
            done
            if [ "$1" = start ]; then {connect}; fi
            """,
        )
        self._fake("install_reflection_worker.sh", f'echo "install-reflection $*" >> "{self.log}"')
        self._fake(
            "launchctl",
            f"""
            if [ "$1" = list ]; then printf '%b' '{launchctl_list}'; exit 0; fi
            echo "launchctl $*" >> "{self.log}"
            """,
        )
        self._fake(
            "runner",
            f"""
            echo "runner $*" >> "{self.log}"
            # The sleep child exists before the marker appears, so a SIGINT sent
            # the instant the marker lands always finds a runner in `wait`. An
            # asynchronous child ignores SIGINT, so the trap ends it explicitly.
            sleeper=
            trap 'kill "$sleeper" 2>/dev/null; exit 130' INT
            trap 'kill "$sleeper" 2>/dev/null; exit 143' TERM
            if [ "${{RUNNER_SLEEP:-0}}" != 0 ]; then sleep "$RUNNER_SLEEP" & sleeper=$!; fi
            touch "{self.started}"
            wait
            exit "${{RUNNER_EXIT:-0}}"
            """,
        )

    def _fake(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text("#!/bin/bash\n" + textwrap.dedent(body).strip() + "\n")
        path.chmod(0o755)
        return path

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self.dir)),
            "VALOR_SERVICE_SH": str(self.dir / "valor-service.sh"),
            "INSTALL_REFLECTION_SH": str(self.dir / "install_reflection_worker.sh"),
            "LAUNCHCTL": str(self.dir / "launchctl"),
            "CLASSIFICATION_EVAL_CMD": str(self.dir / "runner"),
            "BRIDGE_LOG": str(self.bridge_log),
            "RUNS_DIR": str(self.runs_dir),
            "BRIDGE_WAIT_S": "2",
        }
        env.update(extra)
        return env

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def counter(self) -> Path:
        return self.runs_dir / f"classification_eval_runs.{datetime.now(UTC):%Y-%m-%d}"

    def lock(self) -> Path:
        return self.runs_dir / ".classification_eval_uncontended.lock"

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path, launchctl_list=ALL_LOADED, connect_on_restart=True)


RESTORE_ALL = ["valor-service start", "valor-service worker-start", "install-reflection "]


def _restore_index(calls: list[str]) -> int:
    return next(i for i, line in enumerate(calls) if line == "valor-service start")


@pytest.mark.parametrize("runner_exit", ["0", "2"])
def test_restores_every_loaded_label_after_the_runner_exits(harness, runner_exit):
    """Exit 0 and a shortfall exit 2 alike: unload exactly the loaded
    labels, run the comparison, restore in order, and propagate the
    runner's exit code."""
    result = harness.run("--site", "x.y", "--candidate", "decisions", RUNNER_EXIT=runner_exit)
    calls = harness.calls()
    assert result.returncode == int(runner_exit), (result.stdout, result.stderr)
    assert calls == [
        "valor-service stop",
        "valor-service worker-disable",
        f"launchctl bootout gui/{os.getuid()}/com.valor.reflection-worker",
        "runner --site x.y --candidate decisions",
        *RESTORE_ALL,
    ]
    assert harness.counter().read_text().strip() == "1"
    assert not harness.lock().exists()


def test_restores_after_the_runner_is_interrupted(harness):
    """Ctrl-C reaches the wrapper and the runner together (one process
    group); the runner dies, the wrapper's EXIT trap still restores."""
    proc = subprocess.Popen(
        [str(SCRIPT), "--site", "x.y"],
        env=harness.env(RUNNER_SLEEP="30"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        # An xdist worker ignores SIGINT and a child inherits that; a terminal
        # hands the wrapper the default disposition, which is what Ctrl-C tests.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    deadline = time.monotonic() + 10
    while not harness.started.exists():
        assert time.monotonic() < deadline, "the fake runner never started"
        time.sleep(0.05)
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    proc.communicate(timeout=20)
    calls = harness.calls()
    assert proc.returncode != 0
    assert "valor-service stop" in calls and "runner --site x.y" in calls
    assert calls[_restore_index(calls) :] == RESTORE_ALL
    assert not harness.lock().exists()


@pytest.mark.parametrize(
    ("loaded", "expected_unload", "expected_restore"),
    [
        ("", [], []),
        ("-\t0\tcom.valor.bridge\n", ["valor-service stop"], ["valor-service start"]),
        # A worker-only host (a machine that deliberately runs no bridge)
        # gets its worker back and never a bridge started.
        (
            "9\t0\tcom.valor.worker\n",
            ["valor-service worker-disable"],
            ["valor-service worker-start"],
        ),
        (
            "9\t0\tcom.valor.reflection-worker\n",
            [f"launchctl bootout gui/{os.getuid()}/com.valor.reflection-worker"],
            ["install-reflection "],
        ),
        (
            "1\t0\tcom.valor.bridge\n9\t0\tcom.valor.reflection-worker\n5\t0\tcom.apple.Finder\n",
            [
                "valor-service stop",
                f"launchctl bootout gui/{os.getuid()}/com.valor.reflection-worker",
            ],
            ["valor-service start", "install-reflection "],
        ),
    ],
)
def test_unloads_and_restores_only_the_labels_launchctl_reported_loaded(
    tmp_path, loaded, expected_unload, expected_restore
):
    harness = Harness(tmp_path, launchctl_list=loaded, connect_on_restart=True)
    result = harness.run("--site", "x.y")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.calls() == [*expected_unload, "runner --site x.y", *expected_restore]


def test_fails_naming_the_bridge_when_it_never_reconnects(tmp_path):
    harness = Harness(tmp_path, launchctl_list=ALL_LOADED, connect_on_restart=False)
    result = harness.run("--site", "x.y", BRIDGE_WAIT_S="1")
    assert result.returncode != 0
    assert "bridge" in result.stderr and CONNECTED in result.stderr
    assert "failed to return:" in result.stderr
    assert harness.calls()[-3:] == RESTORE_ALL


def test_a_marker_logged_before_the_stop_does_not_count_as_a_reconnect(tmp_path):
    """The bridge logs the marker once per connect and the log rotates only
    when oversized, so on a quiet bridge the previous run's marker is still
    in the tail; a restore whose bridge never comes up must still fail."""
    harness = Harness(tmp_path, launchctl_list=ALL_LOADED, connect_on_restart=False)
    harness.bridge_log.write_text(f"boot\n{CONNECTED}\nSIGTERM received\n")
    result = harness.run("--site", "x.y", BRIDGE_WAIT_S="1")
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "bridge" in result.stderr and CONNECTED in result.stderr
    assert "bridge reconnected" not in result.stdout

    (tmp_path / "b").mkdir()
    connecting = Harness(tmp_path / "b", launchctl_list=ALL_LOADED, connect_on_restart=True)
    connecting.bridge_log.write_text(f"boot\n{CONNECTED}\n")
    result = connecting.run("--site", "x.y", BRIDGE_WAIT_S="2")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "bridge reconnected" in result.stdout


def test_a_failed_bridge_start_never_skips_the_worker_and_names_the_bridge(tmp_path):
    """Each label is restored by its own command: when the bridge's start
    fails, the worker's sticky launchctl disable is still undone, and the
    exit names the service that failed to return."""
    harness = Harness(
        tmp_path,
        launchctl_list=ALL_LOADED,
        connect_on_restart=False,
        failing_service_commands=("start",),
    )
    result = harness.run("--site", "x.y", BRIDGE_WAIT_S="1")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.calls()[-3:] == RESTORE_ALL
    assert "failed to return: bridge" in result.stderr


def test_a_runner_over_run_timeout_is_terminated_and_the_services_restored(harness):
    result = harness.run("--site", "x.y", RUNNER_SLEEP="30", RUN_TIMEOUT_S="1")
    assert result.returncode == 124, (result.stdout, result.stderr)
    assert "RUN_TIMEOUT_S=1" in result.stderr
    calls = harness.calls()
    assert "runner --site x.y" in calls
    assert calls[_restore_index(calls) :] == RESTORE_ALL
    assert not harness.lock().exists()


def test_a_second_invocation_is_refused_while_the_lock_is_held(harness):
    harness.lock().mkdir()
    result = harness.run("--site", "x.y")
    assert result.returncode == 4
    assert "another uncontended run" in result.stderr and "Nothing was stopped" in result.stderr
    assert harness.calls() == []
    assert not harness.counter().exists()
    harness.lock().rmdir()
    result = harness.run("--site", "x.y")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_bridge_check_is_skipped_when_the_bridge_was_not_loaded(tmp_path):
    harness = Harness(tmp_path, launchctl_list="9\t0\tcom.valor.worker\n", connect_on_restart=False)
    result = harness.run("--site", "x.y", BRIDGE_WAIT_S="1")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "valor-service start" not in harness.calls()


def test_refuses_a_thirteenth_run_in_one_utc_day_without_stopping_anything(harness):
    harness.counter().write_text("12\n")
    result = harness.run("--site", "x.y")
    assert result.returncode != 0
    assert "MAX_UNCONTENDED_RUNS_PER_DAY" in result.stderr and "12" in result.stderr
    assert harness.calls() == []
    assert harness.counter().read_text().strip() == "12"

    harness.counter().write_text("11\n")
    result = harness.run("--site", "x.y")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.counter().read_text().strip() == "12"
    assert "runner --site x.y" in harness.calls()


def test_the_script_traps_exit_and_names_the_bound_literally():
    """The Verification row greps these two lines; the bound is one token so
    the plan's ``MAX_UNCONTENDED_RUNS_PER_DAY=12`` matches it verbatim."""
    lines = SCRIPT.read_text().splitlines()
    assert "MAX_UNCONTENDED_RUNS_PER_DAY=12" in lines
    assert os.access(SCRIPT, os.X_OK)
    trap_line = next(
        i for i, line in enumerate(lines) if line.startswith("trap ") and "EXIT" in line
    )
    runner_line = next(i for i, line in enumerate(lines) if '"${runner_cmd[@]}" "$@"' in line)
    assert trap_line < runner_line, "the EXIT trap must be installed before the runner starts"
