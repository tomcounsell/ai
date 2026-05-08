"""Integration tests for ui/data/reflections.py dashboard surface."""

from __future__ import annotations

import time

from models.reflection import Reflection
from models.reflection_run import ReflectionRun


def test_reflection_dashboard_exposes_new_fields():
    """The dashboard data layer surfaces failure_count_consecutive, paused_until,
    cost_usd_total, output_sink for any Reflection."""
    from ui.data.reflections import _build_entry

    name = f"dash-{int(time.time() * 1e6)}"
    r = Reflection.create(name=name, output_sink="memory:8.0")
    r.failure_count_consecutive = 4
    r.paused_until = time.time() + 100
    r.cost_usd_total = 0.99
    r.save()

    cfg = {"interval": 60, "description": "x", "execution_type": "function"}
    entry = _build_entry(name, cfg, r, time.time())

    for k in ("failure_count_consecutive", "paused_until", "cost_usd_total", "output_sink"):
        assert k in entry, f"dashboard missing key: {k}"
    assert entry["failure_count_consecutive"] == 4
    assert entry["cost_usd_total"] == 0.99
    assert entry["output_sink"] == "memory:8.0"


def test_get_run_history_reads_from_reflection_run_rows():
    from ui.data.reflections import get_run_detail, get_run_history

    name = f"dash-runs-{int(time.time() * 1e6)}"
    Reflection.create(name=name)
    base = time.time()
    for i in range(5):
        run = ReflectionRun.get_or_create_for(name=name, timestamp=base - i * 60)
        run.status = "success"
        run.duration_ms = 500 + i
        run.cost_usd = 0.01 * (i + 1)
        run.save()

    history = get_run_history(name)
    assert history["total_runs"] == 5
    assert len(history["runs"]) == 5

    detail = get_run_detail(name, 0)  # oldest forward index
    assert detail is not None
    assert detail["name"] == name
