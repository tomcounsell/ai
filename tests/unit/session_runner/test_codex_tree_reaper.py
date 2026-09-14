"""Unit tests for the Codex process-tree reaper (plan #2001 Task 3).

``kill_codex_tree`` runs under the enumerate-kill-wait contract: capture
``(pid, create_time)`` for the whole tree, SIGKILL only on
``create_time`` match (a recycled PID is never signalled), wait up to 5s,
return survivor dicts. These tests run against real ``sleep`` processes —
no Codex binary, no network — so the crash-cleanup path is proven on live
PIDs, including a grandchild (sandboxed descendants, not just the direct
child) and an unrelated bystander that must survive.
"""

from __future__ import annotations

import subprocess
import sys
import time

import psutil

from agent.session_runner.harness.codex import kill_codex_tree


def _sleep_tree():
    """Spawn a Python root with a real sleep grandchild.

    The root outlives its child (unlike ``sh -c 'sleep & wait'``, which
    exits once the child dies), so both deaths are attributable to the
    reaper under test.
    """
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, time; subprocess.Popen(['sleep', '120']); time.sleep(120)",
        ]
    )
    # Wait for the grandchild to exist before returning.
    for _ in range(100):
        try:
            kids = psutil.Process(proc.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            kids = []
        if kids:
            return proc
        time.sleep(0.05)
    raise AssertionError("sleep grandchild never appeared")


def _alive(pid: int) -> bool:
    return psutil.pid_exists(pid)


def test_tree_reaper_kills_root_and_grandchild():
    root = _sleep_tree()
    try:
        kids = psutil.Process(root.pid).children(recursive=True)
        assert kids, "expected at least a sleep grandchild"
        grandchild_pid = kids[0].pid

        survivors = kill_codex_tree(root.pid)

        assert survivors == []
        # Roots we spawned ourselves linger as zombies until reaped.
        root.wait(timeout=10)
        assert root.returncode == -9  # SIGKILL
        for _ in range(100):
            if not _alive(grandchild_pid):
                break
            time.sleep(0.05)
        assert not _alive(grandchild_pid)
    finally:
        for pid in (root.pid,):
            try:
                if _alive(pid):
                    root.kill()
            except Exception:  # noqa: BLE001 -- best-effort cleanup
                pass


def test_tree_reaper_spares_unrelated_bystander():
    root = _sleep_tree()
    bystander = subprocess.Popen(["sleep", "120"])
    try:
        kill_codex_tree(root.pid)
        assert _alive(bystander.pid), "reaper killed a process outside the tree"
    finally:
        bystander.kill()
        bystander.wait()
        try:
            if _alive(root.pid):
                root.kill()
        except Exception:  # noqa: BLE001
            pass


def test_tree_reaper_is_fail_quiet_on_dead_pid():
    assert kill_codex_tree(2**31 - 1) == []


def test_tree_reaper_never_signals_recycled_pid(monkeypatch):
    """A PID whose create_time changed since enumeration is skipped.

    Forces the mismatch by shrinking the capture window: patch
    ``psutil.Process.create_time`` so the re-check disagrees with the
    captured value, then assert the live process was never signalled.
    """
    root = _sleep_tree()
    try:
        kid = psutil.Process(root.pid).children(recursive=True)[0]

        calls = {"n": 0}
        orig = psutil.Process.create_time

        def _flaky(self):
            # Capture calls are odd (real value); re-checks are even
            # (disagree) — every child looks recycled, so none is signalled.
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                return orig(self) + 1000.0
            return orig(self)

        monkeypatch.setattr(psutil.Process, "create_time", _flaky)
        kill_codex_tree(root.pid)
        assert _alive(kid.pid), "recycled-PID stand-in must never be signalled"
    finally:
        monkeypatch.undo()
        root.kill()
        root.wait()
