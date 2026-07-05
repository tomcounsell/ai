"""The conftest xdist reaper only kills workers it owns (or true orphans).

On a shared machine two pytest controllers can run concurrently; the old
machine-wide pgrep reap in tests/conftest.py killed the other run's live
workers on any controller exit ("node down: Not properly terminated").
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conftest import _ours_or_orphan  # noqa: E402


class TestOursOrOrphan:
    def test_own_child_is_ours(self):
        proc = subprocess.Popen(["sleep", "30"])
        try:
            assert _ours_or_orphan(proc.pid) is True
        finally:
            proc.kill()
            proc.wait()

    def test_own_grandchild_is_ours(self):
        # Worker processes are grandchildren when spawned through a shell.
        proc = subprocess.Popen(["bash", "-c", "sleep 30 & wait"])
        try:
            out = ""
            for _ in range(50):  # the fork of `sleep` races this pgrep
                out = subprocess.run(
                    ["pgrep", "-P", str(proc.pid)], capture_output=True, text=True, timeout=5
                ).stdout.strip()
                if out:
                    break
                time.sleep(0.1)
            grandchild = int(out.splitlines()[0])
            assert _ours_or_orphan(grandchild) is True
        finally:
            proc.terminate()
            proc.wait()

    def test_foreign_ancestor_chain_is_not_ours(self):
        # Our own parent's chain reaches init without passing through us.
        parent = os.getppid()
        if parent <= 1:
            return  # already init-parented (container edge case); nothing to assert
        assert _ours_or_orphan(parent) is False

    def test_dead_pid_is_not_ours(self):
        proc = subprocess.Popen(["sleep", "0.01"])
        proc.wait()
        assert _ours_or_orphan(proc.pid) is False
