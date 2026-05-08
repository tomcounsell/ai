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

    cfg = {"interval": 60, "description": "x", "execution_type": "function"}
    entry = _build_entry(name, cfg, r, time.time())
    assert entry["failure_count_consecutive"] == 3
    assert entry["paused_until"] > time.time()
    assert abs(entry["cost_usd_total"] - 0.42) < 1e-9
    assert entry["output_sink"] == "memory:7.0"


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
