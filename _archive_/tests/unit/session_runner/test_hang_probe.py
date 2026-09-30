"""Unit tests for the short-term subprocess-hang probe (2026-07-13).

Covers ``agent.session_runner.liveness.subprocess_hang_verdict`` — the
evidence-based classifier that distinguishes a working cold start
(progressing) from a genuine hang without waiting on model output. The probe
reads the subprocess tree via psutil; these tests inject a fake process so the
verdict logic is exercised deterministically with no real subprocess.
"""

from __future__ import annotations

from unittest.mock import patch

import psutil
import pytest

from agent.session_runner import liveness as lv


class _FakeConn:
    def __init__(self, port: int | None, status: str = psutil.CONN_ESTABLISHED):
        self.status = status
        self.raddr = type("Addr", (), {"port": port})() if port is not None else None


class _FakeProc:
    """Minimal psutil.Process stand-in driven by explicit fields."""

    def __init__(
        self,
        pid: int = 111,
        status: str = psutil.STATUS_RUNNING,
        cpu: float = 1.0,
        children: list | None = None,
        conns: list | None = None,
        conns_raise: type[BaseException] | None = None,
    ):
        self.pid = pid
        self._status = status
        self._cpu = cpu
        self._children = children if children is not None else []
        self._conns = conns
        self._conns_raise = conns_raise

    def status(self):
        return self._status

    def cpu_times(self):
        # (user, system, children_user, children_system)
        return (self._cpu, 0.0, 0.0, 0.0)

    def children(self, recursive: bool = False):
        return list(self._children)

    def net_connections(self, kind: str = "inet"):
        if self._conns_raise is not None:
            raise self._conns_raise()
        if self._conns is None:
            # Simulate "no connection API data" as an empty readable list.
            return []
        return list(self._conns)


@pytest.fixture(autouse=True)
def _clear_state():
    lv._hang_samples.clear()
    yield
    lv._hang_samples.clear()


def _verdict(proc):
    with patch("psutil.Process", return_value=proc):
        return lv.subprocess_hang_verdict(proc.pid, "sess")


def test_no_pid_is_unknown():
    assert lv.subprocess_hang_verdict(None, "sess") == ("unknown", None)


def test_dead_status_is_hung():
    assert _verdict(_FakeProc(status=psutil.STATUS_ZOMBIE)) == ("hung", "dead")


def test_gone_process_is_hung():
    # pid no longer exists → NoSuchProcess → a death, not a slow start: the
    # caller must recover, not reprieve (regression: it once returned "alive").
    with patch("psutil.Process", side_effect=psutil.NoSuchProcess(pid=123)):
        assert lv.subprocess_hang_verdict(123, "sess") == ("hung", "gone")


def test_access_denied_is_unknown():
    with patch("psutil.Process", side_effect=psutil.AccessDenied(pid=123)):
        assert lv.subprocess_hang_verdict(123, "sess") == ("unknown", None)


def test_live_children_is_progressing():
    proc = _FakeProc(children=[object()])
    assert _verdict(proc) == ("progressing", "children")


def test_first_sample_is_progressing_baseline():
    assert _verdict(_FakeProc(cpu=5.0)) == ("progressing", "cpu_baseline")


def test_cpu_advancing_is_progressing():
    proc = _FakeProc(pid=222, cpu=1.0)
    _verdict(proc)  # baseline at 1.0
    proc._cpu = 3.0  # advanced
    assert _verdict(proc) == ("progressing", "cpu")


def test_flat_cpu_with_api_socket_is_progressing():
    # Baseline, then flat CPU but an established HTTPS connection in flight.
    proc = _FakeProc(pid=333, cpu=2.0, conns=[_FakeConn(443)])
    _verdict(proc)  # baseline
    assert _verdict(proc) == ("progressing", "api")


def test_flat_cpu_no_api_confirms_hung_after_samples():
    # No children, flat CPU, sockets readable with no HTTPS conn → hung after
    # HANG_CONFIRM_SAMPLES consecutive flat polls.
    proc = _FakeProc(pid=444, cpu=2.0, conns=[_FakeConn(22)])  # ssh, not API
    assert _verdict(proc) == ("progressing", "cpu_baseline")  # sample 1 (baseline)
    # Each subsequent flat poll accrues one flat count; grace before confirm.
    for _ in range(lv.HANG_CONFIRM_SAMPLES - 1):
        assert _verdict(proc) == ("progressing", "cpu_flat_grace")
    assert _verdict(proc) == ("hung", "flat_cpu_no_api")


def test_flat_cpu_unreadable_sockets_is_unknown():
    # Sockets raise AccessDenied → cannot disprove a network wait → unknown,
    # never a false hang.
    proc = _FakeProc(pid=555, cpu=2.0, conns_raise=psutil.AccessDenied)
    _verdict(proc)  # baseline
    assert _verdict(proc) == ("unknown", None)


def test_pid_change_rebaselines():
    # A session that recovers and respawns a new subprocess (new pid) must
    # re-baseline rather than compare CPU across two unrelated processes.
    proc_a = _FakeProc(pid=666, cpu=100.0)
    _verdict(proc_a)  # baseline at pid 666, cpu 100
    proc_b = _FakeProc(pid=777, cpu=0.5)  # new subprocess, low CPU
    # Without the pid guard this would read as a huge negative delta → flat →
    # risk a false hang. With the guard it re-baselines → progressing.
    assert _verdict(proc_b) == ("progressing", "cpu_baseline")


def test_clear_hang_state_drops_baseline():
    proc = _FakeProc(pid=888, cpu=1.0)
    _verdict(proc)
    assert ("sess", "") in lv._hang_samples
    lv.clear_hang_state("sess")
    assert ("sess", "") not in lv._hang_samples


def test_clear_hang_state_drops_all_callers():
    # clear_hang_state must drop every (session_key, caller) variant, not just
    # one — both probers key off the same session id.
    proc = _FakeProc(pid=890, cpu=1.0)
    with patch("psutil.Process", return_value=proc):
        lv.subprocess_hang_verdict(890, "sess", caller="fix3")
        lv.subprocess_hang_verdict(890, "sess", caller="health")
    assert ("sess", "fix3") in lv._hang_samples
    assert ("sess", "health") in lv._hang_samples
    lv.clear_hang_state("sess")
    assert not any(k[0] == "sess" for k in lv._hang_samples)


def test_callers_have_independent_flat_counts():
    # Two probers on the same session must not perturb each other's flat-count:
    # the fix3 poller confirming a hang must not depend on health-loop calls.
    proc = _FakeProc(pid=891, cpu=2.0, conns=[_FakeConn(22)])  # flat, no API
    with patch("psutil.Process", return_value=proc):
        # Interleave: health poll should not advance fix3's flat counter.
        assert lv.subprocess_hang_verdict(891, "s", caller="fix3")[0] == "progressing"
        for _ in range(3):
            lv.subprocess_hang_verdict(891, "s", caller="health")
        # fix3 has only seen one (baseline) poll; needs HANG_CONFIRM_SAMPLES more
        # flat polls of its OWN to confirm — the health calls did not count.
        for _ in range(lv.HANG_CONFIRM_SAMPLES - 1):
            assert lv.subprocess_hang_verdict(891, "s", caller="fix3")[0] == "progressing"
        assert lv.subprocess_hang_verdict(891, "s", caller="fix3") == ("hung", "flat_cpu_no_api")
