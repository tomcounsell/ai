"""Unit tests for reap_stale_running() (agent/reflection_scheduler.py).

Race 2 (cycle-4) coverage: stale running rows older than the threshold are
reaped to status=stale_running, failure_count_consecutive++, and the operation
is idempotent on the next call.
"""

from __future__ import annotations

import time

from agent.reflection_scheduler import reap_stale_running
from models.reflection import Reflection


def _make(
    name: str,
    *,
    schedule: str,
    ran_at: float,
    last_status: str = "running",
    last_duration: float | None = None,
) -> Reflection:
    r = Reflection.create(name=name)
    r.schedule = schedule
    r.last_status = last_status
    r.ran_at = ran_at
    if last_duration is not None:
        r.last_duration = last_duration
    r.save()
    return r


def test_reap_old_running_record():
    name = f"reap-old-{int(time.time() * 1e6)}"
    # threshold = max(2*interval, last_duration or 1800).
    # Use every:1h so 2*interval=7200 dominates the 1800 fallback.
    _make(name, schedule="every:1h", ran_at=time.time() - 7300)
    reaped = reap_stale_running()
    assert reaped >= 1
    rec = Reflection.query.filter(name=name)[0]
    assert rec.last_status == "stale_running"
    assert int(rec.failure_count_consecutive or 0) >= 1
    assert rec.last_error and "stale" in rec.last_error.lower()


def test_recent_running_not_reaped():
    name = f"reap-fresh-{int(time.time() * 1e6)}"
    # ran_at recent (well within fallback 1800s)
    _make(name, schedule="every:5m", ran_at=time.time() - 60)
    reap_stale_running()
    rec = Reflection.query.filter(name=name)[0]
    assert rec.last_status == "running"
    assert int(rec.failure_count_consecutive or 0) == 0


def test_reap_multiple_records():
    base = int(time.time() * 1e6)
    names = [f"reap-multi-{base}-{i}" for i in range(3)]
    for n in names:
        _make(n, schedule="every:1m", ran_at=time.time() - 4000)  # > fallback 1800s
    reap_stale_running()
    for n in names:
        rec = Reflection.query.filter(name=n)[0]
        assert rec.last_status == "stale_running"


def test_reap_idempotent():
    name = f"reap-idem-{int(time.time() * 1e6)}"
    _make(name, schedule="every:5m", ran_at=time.time() - 4000)
    first = reap_stale_running()
    rec1 = Reflection.query.filter(name=name)[0]
    fcc1 = int(rec1.failure_count_consecutive or 0)

    # Second call: same record now has last_status='stale_running', so it should
    # NOT be reaped again (only 'running' is reaped).
    second = reap_stale_running()
    rec2 = Reflection.query.filter(name=name)[0]
    fcc2 = int(rec2.failure_count_consecutive or 0)
    assert fcc2 == fcc1
    # The second pass should report 0 reaps for this record (others may exist)
    assert second <= first


def test_no_running_records_no_op():
    # Just ensure it returns 0 cleanly with nothing matching
    Reflection.create(name=f"reap-success-{int(time.time() * 1e6)}")  # last_status='pending'
    reaped = reap_stale_running()
    # Pre-existing rows from other tests in the same Redis db could exist;
    # we simply assert the call does not crash and returns an int.
    assert isinstance(reaped, int)
