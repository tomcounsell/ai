"""Reflection-scheduler health surface on the dashboard (issue #1828).

`_get_reflection_scheduler_health()` reads two data/ files written by
`python -m reflections`:
  - `last_reflection_tick`      (mtime)  -> status + tick_age_s
  - `reflection_worker_starts`  (JSON)   -> restart_count + last_start_age_s

The four fields are flattened into the `/dashboard.json` and `/health` `health`
dicts. `status` is derived PURELY from tick freshness; `last_start_age_s` staying
near-zero (even with a fresh tick) is the crash-loop indicator; `restart_count` is
informational-only and a single benign deploy bump must NOT flip status.

The health helper is a closure inside `create_app()`, so these tests drive the real
endpoints via TestClient while controlling the real data/ files (snapshot + restore).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.webui]

_DATA = Path(__file__).resolve().parents[2] / "data"
_TICK = _DATA / "last_reflection_tick"
_STARTS = _DATA / "reflection_worker_starts"


@pytest.fixture
def data_files_snapshot():
    """Snapshot the two reflection data files, yield, then restore them.

    Keeps the shared repo data/ dir clean even though the closure reads it directly.
    """
    _DATA.mkdir(exist_ok=True)
    saved = {}
    for f in (_TICK, _STARTS):
        saved[f] = f.read_bytes() if f.exists() else None
    try:
        yield
    finally:
        for f, content in saved.items():
            if content is None:
                if f.exists():
                    f.unlink()
            else:
                f.write_bytes(content)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from ui.app import create_app

    return TestClient(create_app())


def _write_tick(age_s: float) -> None:
    _TICK.write_text(str(time.time() - age_s))
    mtime = time.time() - age_s
    os.utime(_TICK, (mtime, mtime))


def _write_starts(count: int, start_age_s: float) -> None:
    _STARTS.write_text(json.dumps({"count": count, "last_start_ts": time.time() - start_age_s}))


def _health(client) -> dict:
    return client.get("/dashboard.json").json()["health"]


def test_flattened_fields_present(client, data_files_snapshot):
    """All four reflection_scheduler_* fields are present in the health dict."""
    _write_tick(5)
    _write_starts(count=1, start_age_s=5)
    h = _health(client)
    assert "reflection_scheduler_status" in h
    assert "reflection_scheduler_tick_age_s" in h
    assert "reflection_scheduler_restart_count" in h
    assert "reflection_scheduler_last_start_age_s" in h


def test_fresh_tick_reads_ok(client, data_files_snapshot):
    _write_tick(3)
    _write_starts(count=1, start_age_s=3)
    h = _health(client)
    assert h["reflection_scheduler_status"] == "ok"
    assert h["reflection_scheduler_tick_age_s"] < 150


def test_stale_tick_reads_error(client, data_files_snapshot):
    _write_tick(10_000)  # well past the stale threshold
    _write_starts(count=1, start_age_s=10_000)
    h = _health(client)
    assert h["reflection_scheduler_status"] == "error"


def test_absent_tick_reads_error(client, data_files_snapshot):
    if _TICK.exists():
        _TICK.unlink()
    if _STARTS.exists():
        _STARTS.unlink()
    h = _health(client)
    assert h["reflection_scheduler_status"] == "error"
    assert h["reflection_scheduler_tick_age_s"] is None


def test_crash_loop_visible_despite_fresh_tick(client, data_files_snapshot):
    """A crash loop: fresh tick (each short-lived process writes one) BUT a near-zero
    last_start_age_s (launchd keeps respawning). status stays freshness-derived; the
    loop is visible via last_start_age_s."""
    _write_tick(2)  # fresh — looks healthy on tick alone
    _write_starts(count=57, start_age_s=1)  # just (re)started a beat ago
    h = _health(client)
    assert h["reflection_scheduler_status"] == "ok"  # status is tick-derived
    assert h["reflection_scheduler_last_start_age_s"] <= 3  # the crash-loop signal
    assert h["reflection_scheduler_restart_count"] == 57


def test_healthy_long_lived_has_climbing_start_age(client, data_files_snapshot):
    """A healthy scheduler booted once long ago: fresh tick + a large last_start_age_s."""
    _write_tick(4)
    _write_starts(count=2, start_age_s=86_400)  # booted a day ago, still ticking
    h = _health(client)
    assert h["reflection_scheduler_status"] == "ok"
    assert h["reflection_scheduler_last_start_age_s"] >= 80_000


def test_benign_deploy_bump_does_not_flip_status(client, data_files_snapshot):
    """A single deploy bump (restart_count increments, start-age climbs) must NOT read
    as a crash loop — status stays ok and the start-age is not near-zero."""
    _write_tick(5)
    _write_starts(count=3, start_age_s=600)  # deployed 10 min ago, healthy since
    h = _health(client)
    assert h["reflection_scheduler_status"] == "ok"
    assert h["reflection_scheduler_last_start_age_s"] > 60


def test_health_route_also_carries_fields(client, data_files_snapshot):
    """The /health route surfaces the same reflection_scheduler fields."""
    _write_tick(5)
    _write_starts(count=1, start_age_s=5)
    h = client.get("/health").json()
    assert h["reflection_scheduler"] == "ok"
    assert "reflection_scheduler_last_start_age_s" in h
