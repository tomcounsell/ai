"""``any_worker_alive()``: the improvement scheduler adapter's liveness gate (#3215, Task 6)."""

from __future__ import annotations

import time

import pytest
from popoto.redis_db import POPOTO_REDIS_DB as _R

from agent.session_health import (
    _ORPHAN_REAP_HOSTNAME,
    _WORKER_PID_HEARTBEAT_TS_KEY_PREFIX,
    WORKER_REGISTERED_PID_KEY_PREFIX,
    any_worker_alive,
)


def _register(pid: int, *, heartbeat_age_seconds: float | None) -> None:
    key = f"{WORKER_REGISTERED_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}"
    _R.set(key, str(pid), ex=120)
    if heartbeat_age_seconds is not None:
        ts_key = f"{_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}"
        _R.set(ts_key, str(time.time() - heartbeat_age_seconds), ex=120)


def _cleanup(pid: int) -> None:
    _R.delete(f"{WORKER_REGISTERED_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}")
    _R.delete(f"{_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}")


@pytest.fixture(autouse=True)
def _clear_registered_pids():
    """This claimed test db is process-scoped, but scan/isolate anyway: a
    stray registered pid from another test in the same worker db would make
    the "no live worker" assertions flaky."""
    for key in _R.scan_iter(f"{WORKER_REGISTERED_PID_KEY_PREFIX}*"):
        _R.delete(key)
    yield
    for key in _R.scan_iter(f"{WORKER_REGISTERED_PID_KEY_PREFIX}*"):
        _R.delete(key)


class TestAnyWorkerAlive:
    def test_true_when_a_registered_pid_has_a_fresh_heartbeat(self):
        pid = 900001
        _register(pid, heartbeat_age_seconds=1.0)
        try:
            assert any_worker_alive() is True
        finally:
            _cleanup(pid)

    def test_false_when_the_only_registered_pid_has_a_stale_heartbeat(self):
        pid = 900002
        _register(pid, heartbeat_age_seconds=99999.0)
        try:
            assert any_worker_alive() is False
        finally:
            _cleanup(pid)

    def test_false_when_a_registered_pid_has_no_heartbeat_at_all(self):
        pid = 900003
        _register(pid, heartbeat_age_seconds=None)
        try:
            assert any_worker_alive() is False
        finally:
            _cleanup(pid)
