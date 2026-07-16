"""Integration test for scripts/refresh_baseline_detached.sh (issue #2066).

The wrapper launches ``refresh_test_baseline.py`` detached so the ~30-min refresh
survives the agent's 10-min foreground bash-tool cap. This test proves:

1. The wrapper RETURNS PROMPTLY (well under the 10-min cap) with a PID + log path,
   rather than blocking for the whole refresh.
2. The detached child ACTUALLY RAN — the log gains a terminal ``EXIT=`` line — so a
   failed/degraded refresh is observable rather than silent.
3. The child NEVER touches the real machine-local ``data/main_test_baseline.json``.

To satisfy (3) without a 30-min real refresh, the wrapper is invoked with ``--dry-run``
(writes classification to stdout, never to the baseline file, and skips the machine-global
suite lock so this test cannot deadlock against a concurrent full-suite run) and ``--runs 2``
(≥2 usable runs so the child is not ``degraded`` and exits 0) against a tiny, fast, reliably
passing target.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from scripts.refresh_test_baseline import DEFAULT_BASELINE_PATH

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "refresh_baseline_detached.sh"

# A tiny, dependency-light unit module that reliably passes — cheap to collect twice.
FAST_TARGET = "tests/unit/reflections/test_test_baseline_refresh_check.py"


def _snapshot(path: Path) -> tuple[float, int] | None:
    """Return (mtime, size) for an existing file, or None if absent."""
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_mtime, st.st_size)


@pytest.mark.timeout(300)
def test_wrapper_launches_detached_and_preserves_exit_code(tmp_path: Path) -> None:
    assert WRAPPER.exists(), f"wrapper missing: {WRAPPER}"

    # (3) Guard: the real baseline must be byte-for-byte unchanged after the run.
    before = _snapshot(DEFAULT_BASELINE_PATH)

    # Isolate this test's log + pidfile under tmp_path so it never collides with a
    # real in-flight refresh (or a leftover child from a prior test run) via the
    # shared logs/baseline_refresh.pid concurrency guard.
    env = {**os.environ, "BASELINE_REFRESH_LOG_DIR": str(tmp_path)}

    # (1) The launch itself must return promptly — give it a generous 120s ceiling
    # (the whole point is that it does NOT block for the ~30-min refresh).
    start = time.monotonic()
    proc = subprocess.run(
        [
            str(WRAPPER),
            "--dry-run",
            "--runs",
            "2",
            FAST_TARGET,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    launch_elapsed = time.monotonic() - start

    assert proc.returncode == 0, f"wrapper exited {proc.returncode}: {proc.stderr}"
    assert launch_elapsed < 120, "wrapper blocked instead of detaching"
    assert "PID" in proc.stdout
    assert "log :" in proc.stdout

    # Extract the log path the wrapper printed.
    log_path: Path | None = None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("log :"):
            log_path = Path(stripped.split("log :", 1)[1].strip())
            break
    assert log_path is not None and log_path.exists(), f"log path not found in:\n{proc.stdout}"

    # (2) Poll the log for the terminal EXIT= line the wrapper appends after the
    # detached child completes. This proves the child ran to completion.
    deadline = time.monotonic() + 240
    exit_line: str | None = None
    while time.monotonic() < deadline:
        text = log_path.read_text(errors="replace")
        for line in text.splitlines():
            if line.startswith("EXIT="):
                exit_line = line
                break
        if exit_line is not None:
            break
        time.sleep(2)

    assert exit_line is not None, (
        f"detached child never wrote EXIT= line; log:\n{log_path.read_text()}"
    )
    # 2 usable dry-runs on a passing target => not degraded => exit 0.
    assert exit_line.startswith("EXIT=0"), f"unexpected child exit: {exit_line}"

    # (3) Confirm the real machine-local baseline was untouched by the dry-run child.
    after = _snapshot(DEFAULT_BASELINE_PATH)
    assert after == before, "dry-run refresh must not modify data/main_test_baseline.json"
