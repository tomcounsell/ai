"""Unit tests for scripts.full_suite_lock — file-based coordination lock.

Covers acquire/release lifecycle, stale-lock detection (dead PID + corrupt
JSON), PID-mismatch release guard, and recommended_workers() edge cases.
All tests use tmp_path so the production lock file is never touched.
"""

import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

# Ensure scripts/ is importable for the test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from full_suite_lock import (  # noqa: E402
    DEFAULT_LOCK_PATH,
    acquire,
    recommended_workers,
    release,
)


@pytest.fixture
def lock_path(tmp_path):
    """Return a temp lock path so tests never touch production state."""
    return str(tmp_path / "full-suite-running.lock")


def _write_lock(path, pid, host=None):
    """Write a lock file with the given PID."""
    if host is None:
        import socket

        host = socket.gethostname()
    data = {"pid": pid, "started_at": time.time(), "host": host}
    with open(path, "w") as f:
        json.dump(data, f)


class TestAcquire:
    def test_acquire_succeeds_when_no_lock(self, lock_path):
        """acquire() creates the lock and returns True when no lock exists."""
        result = acquire(lock_path=lock_path, timeout=5)
        assert result is True
        assert os.path.exists(lock_path)
        with open(lock_path) as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()

    def test_acquire_removes_stale_lock_dead_pid(self, lock_path):
        """If the lock file references a dead PID, acquire removes it and succeeds."""
        # Use a PID that is almost certainly dead (max PID on most systems, or a large number)
        dead_pid = 999999
        _write_lock(lock_path, dead_pid)
        assert os.path.exists(lock_path)

        result = acquire(lock_path=lock_path, timeout=5)
        assert result is True
        # Lock should now contain OUR pid
        with open(lock_path) as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()

    def test_acquire_treats_corrupt_json_as_stale(self, lock_path):
        """A lock file with corrupt JSON is treated as stale (removed and acquired)."""
        with open(lock_path, "w") as f:
            f.write("{not valid json!!!")

        result = acquire(lock_path=lock_path, timeout=5)
        assert result is True
        with open(lock_path) as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()

    def test_acquire_waits_for_live_process(self, lock_path):
        """acquire() waits when the lock is held by a live process, then succeeds after release."""
        holder_script = f"""
import json, os, socket, time, sys
lock_path = {lock_path!r}
data = {{"pid": os.getpid(), "started_at": time.time(), "host": socket.gethostname()}}
with open(lock_path, "w") as f:
    json.dump(data, f)
time.sleep(3)  # hold the lock for 3 seconds
os.unlink(lock_path)
sys.exit(0)
"""
        proc = subprocess.Popen([sys.executable, "-c", holder_script])
        # Wait for the subprocess to write the lock file
        deadline = time.time() + 5
        while not os.path.exists(lock_path) and time.time() < deadline:
            time.sleep(0.1)
        assert os.path.exists(lock_path), "Subprocess failed to create lock file"

        result = acquire(lock_path=lock_path, timeout=15)
        assert result is True
        proc.wait(timeout=10)

    def test_acquire_timeout_proceeds_without_lock(self, lock_path):
        """On timeout, acquire() returns False and proceeds without the lock."""
        # Hold the lock with a long-lived subprocess
        holder_script = f"""
import json, os, socket, time, sys
lock_path = {lock_path!r}
data = {{"pid": os.getpid(), "started_at": time.time(), "host": socket.gethostname()}}
with open(lock_path, "w") as f:
    json.dump(data, f)
time.sleep(30)  # hold well beyond the acquire timeout
sys.exit(0)
"""
        proc = subprocess.Popen([sys.executable, "-c", holder_script])
        # Wait for lock file
        deadline = time.time() + 5
        while not os.path.exists(lock_path) and time.time() < deadline:
            time.sleep(0.1)
        assert os.path.exists(lock_path)

        try:
            result = acquire(lock_path=lock_path, timeout=2)
            assert result is False
        finally:
            proc.kill()
            proc.wait()
            if os.path.exists(lock_path):
                # Clean up (only if our PID, but here it's the subprocess's lock)
                try:
                    os.unlink(lock_path)
                except OSError:
                    pass

    def test_acquire_skips_stale_on_different_host(self, lock_path):
        """A lock from a different host is treated as stale (remove and acquire)."""
        _write_lock(lock_path, os.getpid(), host="other-machine-12345")
        result = acquire(lock_path=lock_path, timeout=5)
        assert result is True
        with open(lock_path) as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()


class TestRelease:
    def test_release_does_not_raise_if_lock_gone(self, lock_path):
        """release() must not raise if the lock file is already gone."""
        acquire(lock_path=lock_path, timeout=5)
        os.unlink(lock_path)
        # Should not raise
        release(lock_path=lock_path)

    def test_release_only_releases_matching_pid(self, lock_path):
        """release() does not remove a lock held by a different PID."""
        other_pid = os.getpid() + 100  # unlikely to collide
        _write_lock(lock_path, other_pid)
        release(lock_path=lock_path)
        # Lock should still exist because the PID didn't match
        assert os.path.exists(lock_path)
        with open(lock_path) as f:
            data = json.load(f)
        assert data["pid"] == other_pid

    def test_release_removes_own_lock(self, lock_path):
        """release() removes the lock when the PID matches."""
        acquire(lock_path=lock_path, timeout=5)
        release(lock_path=lock_path)
        assert not os.path.exists(lock_path)

    def test_release_no_raise_on_corrupt_lock(self, lock_path):
        """release() should not raise on corrupt JSON (treat as not ours, leave it)."""
        with open(lock_path, "w") as f:
            f.write("garbage")
        # Should not raise - corrupt lock isn't ours
        release(lock_path=lock_path)


class TestRecommendedWorkers:
    def test_returns_at_least_one(self):
        """recommended_workers() must always return >= 1."""
        with patch("os.getloadavg", return_value=[100.0, 0.0, 0.0]):
            result = recommended_workers()
        assert result >= 1

    def test_high_load_returns_one(self):
        """When load average exceeds CPU count, recommended_workers() returns 1."""
        cpu = os.cpu_count() or 1
        with patch("os.getloadavg", return_value=[float(cpu + 10), 0.0, 0.0]):
            result = recommended_workers()
        assert result == 1

    def test_zero_load_returns_cpu_count(self):
        """When load average is 0, recommended_workers() returns cpu_count."""
        cpu = os.cpu_count() or 1
        with patch("os.getloadavg", return_value=[0.0, 0.0, 0.0]):
            result = recommended_workers()
        assert result == cpu

    def test_partial_load(self):
        """With moderate load, returns cpu_count minus integer load."""
        cpu = os.cpu_count() or 1
        with patch("os.getloadavg", return_value=[2.0, 0.0, 0.0]):
            result = recommended_workers()
        assert result == max(1, cpu - 2)


class TestDefaultPath:
    def test_default_lock_path_is_set(self):
        """The module exports a DEFAULT_LOCK_PATH constant."""
        assert DEFAULT_LOCK_PATH is not None
        assert "full-suite-running.lock" in DEFAULT_LOCK_PATH
