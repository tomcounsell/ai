"""Unit coverage for the live-worker signal guard (#2147).

Proves the guard logic: a process whose command line looks like
``python -m worker`` (a fabricated worker-argv proc) is refused, while an
ordinary non-worker process passes. This is the AC#2 guard coverage — it
demonstrates no audited kill path can target the launchd worker, independent
of whether the historical 2026-07-17 SIGTERM is reproduced.
"""

import os
import subprocess
import sys
import time

import pytest

from tests._worker_guard import (
    LiveWorkerSignalError,
    _looks_like_worker_cmdline,
    assert_not_live_worker,
)


class TestLooksLikeWorkerCmdline:
    def test_worker_cmdline_matches(self):
        assert _looks_like_worker_cmdline("/usr/bin/python3.11 -m worker")

    def test_pytest_cmdline_does_not_match(self):
        # The test runner itself must never be mistaken for a worker.
        assert not _looks_like_worker_cmdline("/usr/bin/python3.11 -m pytest -x")

    def test_non_python_worker_token_does_not_match(self):
        # `-m worker` without a python executable is not our worker.
        assert not _looks_like_worker_cmdline("/bin/sh -m worker")

    def test_empty_cmdline_does_not_match(self):
        assert not _looks_like_worker_cmdline("")


class TestAssertNotLiveWorker:
    def test_non_worker_pid_passes(self):
        """The current (pytest) process is not a worker → must not raise."""
        assert_not_live_worker(os.getpid())

    def test_fabricated_worker_argv_pid_raises(self):
        """A benign proc whose command line carries the `-m worker` token raises.

        The interpreter runs the ``-c`` body (a harmless sleep); the trailing
        ``-m worker`` tokens are inert argv but appear in the process command
        line that ``ps`` reports, so the guard treats the pid as a worker.
        """
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", "-m", "worker"]
        )
        try:
            time.sleep(0.3)  # let ps observe the process
            with pytest.raises(LiveWorkerSignalError):
                assert_not_live_worker(proc.pid)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
