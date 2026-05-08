"""Unit tests for ReflectionRun (models/reflection_run.py)."""

from __future__ import annotations

import time

from models.reflection_run import ReflectionRun


def test_meta_ttl_30_days():
    # Popoto moves user-defined Meta attrs into _meta after class registration
    assert getattr(ReflectionRun._meta, "ttl", None) == 86400 * 30


def test_get_or_create_for_idempotent():
    name = f"run-test-{int(time.time() * 1e6)}"
    ts = time.time()
    a = ReflectionRun.get_or_create_for(name=name, timestamp=ts)
    b = ReflectionRun.get_or_create_for(name=name, timestamp=ts)
    assert a.name == name
    assert b.name == name
    # Same composite key should produce one row
    rows = list(ReflectionRun.query.filter(name=name))
    assert len(rows) == 1


def test_fields_round_trip():
    name = f"run-fields-{int(time.time() * 1e6)}"
    ts = time.time()
    run = ReflectionRun.get_or_create_for(name=name, timestamp=ts)
    run.status = "success"
    run.duration_ms = 1234
    run.cost_usd = 0.42
    run.tokens_input = 100
    run.tokens_output = 50
    run.error = None
    run.output_summary = "hello"
    run.delivery_error = None
    run.projects = [{"k": "v"}]
    run.save()

    rows = list(ReflectionRun.query.filter(name=name))
    assert len(rows) == 1
    r = rows[0]
    assert r.status == "success"
    assert int(r.duration_ms) == 1234
    assert float(r.cost_usd) == 0.42
    assert int(r.tokens_input) == 100
    assert int(r.tokens_output) == 50
    assert r.output_summary == "hello"


def test_recent_for_orders_newest_first():
    name = f"run-order-{int(time.time() * 1e6)}"
    t1 = time.time() - 100
    t2 = time.time() - 50
    t3 = time.time()
    for ts in (t1, t2, t3):
        ReflectionRun.get_or_create_for(name=name, timestamp=ts)
    recent = ReflectionRun.recent_for(name, limit=10)
    assert len(recent) == 3
    timestamps = [r.timestamp for r in recent]
    assert timestamps == sorted(timestamps, reverse=True)
