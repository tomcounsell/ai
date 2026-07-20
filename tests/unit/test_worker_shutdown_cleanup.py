"""Worker shutdown harness-child cleanup (issue #2141).

`terminate_harness_children` must SIGTERM (then SIGKILL) every `claude`
harness descendant on worker shutdown instead of orphaning it into the next
boot's reaper — and must never raise into the shutdown path.
"""

from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from worker import shutdown_cleanup

# ---------------------------------------------------------------------------
# _is_claude_harness
# ---------------------------------------------------------------------------


def _proc(name="python", cmdline=None, pid=123):
    p = MagicMock()
    p.pid = pid
    p.name.return_value = name
    p.cmdline.return_value = cmdline if cmdline is not None else [name]
    return p


def test_matches_claude_by_name():
    assert shutdown_cleanup._is_claude_harness(_proc(name="claude")) is True


def test_matches_claude_by_argv0_basename():
    p = _proc(name="2.1.205", cmdline=["/Users/x/.local/bin/claude", "-p", "--verbose"])
    assert shutdown_cleanup._is_claude_harness(p) is True


def test_non_claude_process_not_matched():
    p = _proc(name="python", cmdline=["/usr/bin/python", "-m", "worker"])
    assert shutdown_cleanup._is_claude_harness(p) is False


def test_matcher_never_raises():
    p = MagicMock()
    p.name.side_effect = RuntimeError("gone")
    assert shutdown_cleanup._is_claude_harness(p) is False


# ---------------------------------------------------------------------------
# terminate_harness_children
# ---------------------------------------------------------------------------


def test_terminates_matching_children_and_kills_survivors():
    harness = _proc(name="claude", pid=101)
    other = _proc(name="python", cmdline=["python", "-m", "x"], pid=102)
    me = MagicMock()
    me.children.return_value = [harness, other]

    fake_psutil = SimpleNamespace(
        Process=MagicMock(return_value=me),
        wait_procs=MagicMock(return_value=([], [harness])),  # harness survives TERM
    )
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        n = shutdown_cleanup.terminate_harness_children(term_grace_s=0.01)

    assert n == 1
    harness.terminate.assert_called_once()
    harness.kill.assert_called_once()
    other.terminate.assert_not_called()


def test_no_children_is_noop():
    me = MagicMock()
    me.children.return_value = []
    fake_psutil = SimpleNamespace(Process=MagicMock(return_value=me), wait_procs=MagicMock())
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        assert shutdown_cleanup.terminate_harness_children() == 0
    fake_psutil.wait_procs.assert_not_called()


def test_enumeration_failure_never_raises():
    fake_psutil = SimpleNamespace(Process=MagicMock(side_effect=RuntimeError("no procfs")))
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        assert shutdown_cleanup.terminate_harness_children() == 0


def test_terminate_failure_still_counts_and_continues():
    bad = _proc(name="claude", pid=201)
    bad.terminate.side_effect = RuntimeError("gone already")
    good = _proc(name="claude", pid=202)
    me = MagicMock()
    me.children.return_value = [bad, good]
    fake_psutil = SimpleNamespace(
        Process=MagicMock(return_value=me),
        wait_procs=MagicMock(return_value=([bad, good], [])),
    )
    with patch.dict(sys.modules, {"psutil": fake_psutil}):
        assert shutdown_cleanup.terminate_harness_children(term_grace_s=0.01) == 2
    good.terminate.assert_called_once()


def test_real_subprocess_child_is_not_matched():
    """Sanity: a real non-claude child (sleep) is never touched."""
    child = subprocess.Popen(["sleep", "5"])
    try:
        time.sleep(0.1)
        n = shutdown_cleanup.terminate_harness_children(term_grace_s=0.1)
        assert child.poll() is None  # still alive — not collateral damage
        assert isinstance(n, int)
    finally:
        child.terminate()
        child.wait(timeout=5)
