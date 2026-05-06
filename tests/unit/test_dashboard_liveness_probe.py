"""Tests for the dashboard liveness probe helper (issue #1269).

`ui/data/sdlc._check_process_alive(pid)` answers "is this PID a live process?"
via a non-blocking ``os.kill(pid, 0)`` syscall. Three return values:

  * ``True``  — process exists in the OS process table.
  * ``False`` — ProcessLookupError raised; the PID is not a live process.
  * ``None``  — uncertain (PID None, PID <= 0, or PermissionError/OSError).

The PID <= 0 guard is critical: ``kill(0, ...)`` and ``kill(-pid, ...)`` have
process-group semantics on Linux/macOS, so we refuse to probe rather than risk
a wrong answer.

Ghost-branch test pattern (POSIX-portable, zero flake risk):
    proc = subprocess.Popen(["true"])
    pid = proc.pid
    proc.wait()                # subprocess has terminated
    assert _check_process_alive(pid) is False
This avoids the flaky ``time.sleep`` patterns that plagued earlier liveness
tests — capture the PID *before* wait(), then assert after the OS reaps it.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from ui.data.sdlc import _check_process_alive


class TestNoneAndNonPositivePids:
    """Helper rejects None and non-positive PIDs without probing."""

    def test_none_returns_none(self):
        assert _check_process_alive(None) is None

    def test_zero_returns_none(self):
        # kill(0, sig) targets the current process group — refuse to probe.
        assert _check_process_alive(0) is None

    def test_negative_returns_none(self):
        # kill(-pid, sig) targets a process group — refuse to probe.
        assert _check_process_alive(-1) is None
        assert _check_process_alive(-12345) is None


class TestAliveBranch:
    """A live PID returns True."""

    def test_own_pid_is_alive(self):
        # The pytest process itself is alive; probing its own PID returns True.
        assert _check_process_alive(os.getpid()) is True


class TestGhostBranch:
    """A dead PID (ProcessLookupError) returns False.

    POSIX-portable pattern: spawn a short-lived process, capture its PID,
    wait for it to exit, then probe. ``true`` exits immediately with code 0.
    Future contributors: do NOT replace this with a sleep-based test —
    sleep timings are flaky in CI; capturing the PID before wait() is
    deterministic.
    """

    def test_dead_pid_returns_false(self):
        proc = subprocess.Popen(["true"])
        pid = proc.pid
        proc.wait()  # OS reaps the subprocess
        # On macOS/Linux the PID can briefly remain a zombie after wait();
        # `os.kill(pid, 0)` raises ProcessLookupError once the entry is gone.
        # In practice wait() in Python's subprocess waits for the OS reap,
        # so the assertion below is reliable.
        assert _check_process_alive(pid) is False


class TestErrorBranches:
    """PermissionError and generic OSError return None (uncertain)."""

    def test_permission_error_returns_none(self, monkeypatch):
        def _raise_perm(_pid, _sig):
            raise PermissionError("not your process")

        monkeypatch.setattr(os, "kill", _raise_perm)
        assert _check_process_alive(12345) is None

    def test_generic_os_error_returns_none(self, monkeypatch):
        def _raise_oserr(_pid, _sig):
            raise OSError("kernel weather")

        monkeypatch.setattr(os, "kill", _raise_oserr)
        assert _check_process_alive(12345) is None

    def test_other_exceptions_propagate(self, monkeypatch):
        """Non-OSError exceptions are unexpected — they should propagate so
        we don't silently swallow real bugs."""

        def _raise_runtime(_pid, _sig):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(os, "kill", _raise_runtime)
        with pytest.raises(RuntimeError):
            _check_process_alive(12345)
