"""Unit tests for ui/data/reflections.py reading from ReflectionRun rows."""

from __future__ import annotations

import time

from models.reflection import Reflection
from models.reflection_run import ReflectionRun
from ui.data.reflections import _build_entry, get_run_detail, get_run_history


def test_build_entry_surfaces_new_fields():
    name = f"ui-build-{int(time.time() * 1e6)}"
    r = Reflection.create(
        name=name,
        output_sink="memory:7.0",
    )
    r.failure_count_consecutive = 3
    r.paused_until = time.time() + 100
    r.cost_usd_total = 0.42
    r.save()

    cfg = {"schedule": "every:60s", "description": "x", "execution_type": "function"}
    entry = _build_entry(name, cfg, r, time.time())
    assert entry["failure_count_consecutive"] == 3
    assert entry["paused_until"] > time.time()
    assert abs(entry["cost_usd_total"] - 0.42) < 1e-9
    assert entry["output_sink"] == "memory:7.0"


def test_build_entry_computes_next_due_from_schedule():
    """Regression for #1273 PR review blocker: dashboard must compute next_due
    from the fazm-grammar `schedule` key, not the removed legacy `interval` int.

    Without this, every reflection shows due_in_seconds=None and the dashboard
    'due in N min' indicator silently breaks post-migration.
    """
    name = f"ui-sched-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name)
    ran_at = time.time() - 10  # 10 seconds ago
    r.ran_at = ran_at
    r.save()

    cfg = {"schedule": "every:60s", "description": "x", "execution_type": "function"}
    entry = _build_entry(name, cfg, r, time.time())

    # Cadence label still derives from the schedule.
    assert entry["interval"] == 60
    # next_due = ran_at + 60s; due_in_seconds ≈ 50 (60s cadence - 10s elapsed).
    assert entry["next_due"] is not None
    assert abs(entry["next_due"] - (ran_at + 60)) < 0.5
    assert entry["due_in_seconds"] is not None
    assert 45 <= entry["due_in_seconds"] <= 55
    assert entry["overdue"] is False


def test_build_entry_overdue_when_schedule_elapsed():
    """When ran_at + schedule interval is in the past, overdue=True."""
    name = f"ui-overdue-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name)
    r.ran_at = time.time() - 3600  # one hour ago, schedule is 60s
    r.save()

    cfg = {"schedule": "every:60s", "description": "x", "execution_type": "function"}
    entry = _build_entry(name, cfg, r, time.time())

    assert entry["next_due"] is not None
    assert entry["due_in_seconds"] < 0
    assert entry["overdue"] is True


def test_get_run_history_reads_from_reflection_run():
    name = f"ui-runs-{int(time.time() * 1e6)}"
    Reflection.create(name=name)
    base = time.time()
    for i in range(3):
        run = ReflectionRun.get_or_create_for(name=name, timestamp=base - i * 10)
        run.status = "success"
        run.duration_ms = 1000
        run.save()

    out = get_run_history(name, page=1)
    assert out["total_runs"] == 3
    assert out["total_pages"] == 1
    assert len(out["runs"]) == 3
    # newest first
    timestamps = [r["timestamp"] for r in out["runs"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_get_run_history_empty():
    out = get_run_history(f"missing-{int(time.time() * 1e6)}", page=1)
    assert out == {"runs": [], "total_pages": 1, "total_runs": 0}


def test_get_run_detail_reads_full_row():
    name = f"ui-detail-{int(time.time() * 1e6)}"
    Reflection.create(name=name)
    base = time.time()
    run = ReflectionRun.get_or_create_for(name=name, timestamp=base)
    run.status = "success"
    run.duration_ms = 1234
    run.cost_usd = 0.05
    run.tokens_input = 7
    run.tokens_output = 11
    run.output_summary = "hi"
    run.save()

    detail = get_run_detail(name, 0)
    assert detail is not None
    assert detail["status"] == "success"
    assert abs(detail["duration"] - 1.234) < 1e-9
    assert detail["output_summary"] == "hi"
    assert detail["cost_usd"] == 0.05
    assert detail["tokens_input"] == 7
    assert detail["tokens_output"] == 11
