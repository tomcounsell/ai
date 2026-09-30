"""Unit tests for the Fix #5 (#1821) worker loop-beacon publish.

The off-loop heartbeat thread translates the per-process monotonic loop age into
a cross-process WALL-CLOCK beacon the bridge can read. Risk 1 (the #1 design
risk) is publishing a raw ``monotonic()`` value: the bridge's ``now - beacon``
math would then mix two unrelated clocks. These tests pin the beacon shape:

- ``wall_ts`` is a wall-clock ``time.time()`` value (≈ now, NOT a small
  monotonic uptime), with the correct TTL.
- ``loop_beacon_age_s`` is advisory; ``armed`` reflects whether the loop ticked.
- A Redis error is swallowed (fail-quiet) and the disk heartbeat still writes.
"""

from __future__ import annotations

import json
import socket
import time
from unittest.mock import MagicMock, patch

import agent.session_health as sh
import agent.session_state as session_state

_HOST = socket.gethostname()
_BEACON_KEY = f"worker:loop_beacon:{_HOST}"


def _read_beacon():
    from popoto.redis_db import POPOTO_REDIS_DB

    raw = POPOTO_REDIS_DB.get(_BEACON_KEY)
    assert raw is not None, "beacon key was not published"
    return json.loads(raw)


def test_beacon_wall_ts_is_wall_clock_not_monotonic():
    """wall_ts must be a time.time() value, never a small monotonic uptime (Risk 1)."""
    session_state.bump_loop_tick()  # arm the loop beacon
    before = time.time()
    sh._publish_loop_beacon()
    after = time.time()

    beacon = _read_beacon()

    # A wall-clock epoch value is always > 1e9 (year 2001+); a monotonic uptime
    # on a normal machine is far smaller. This is the load-bearing Risk-1 assert.
    assert beacon["wall_ts"] > 1_000_000_000
    assert before - 1 <= beacon["wall_ts"] <= after + 1
    assert beacon["armed"] is True
    # Advisory monotonic age is a small non-negative float.
    assert beacon["loop_beacon_age_s"] is not None
    assert 0 <= beacon["loop_beacon_age_s"] < 60


def test_beacon_ttl_is_three_heartbeat_intervals():
    """The beacon must carry a TTL of 3 × WORKER_HEARTBEAT_INTERVAL."""
    session_state.bump_loop_tick()
    sh._publish_loop_beacon()

    from popoto.redis_db import POPOTO_REDIS_DB

    ttl = POPOTO_REDIS_DB.ttl(_BEACON_KEY)
    assert 0 < ttl <= sh.WORKER_LOOP_BEACON_TTL_SECONDS
    assert sh.WORKER_LOOP_BEACON_TTL_SECONDS == 3 * sh.WORKER_HEARTBEAT_INTERVAL


def test_beacon_unarmed_when_loop_never_ticked():
    """A None loop tick publishes armed=False / age None (never treated as wedged)."""
    original = session_state.last_loop_tick
    session_state.last_loop_tick = None
    try:
        sh._publish_loop_beacon()
        beacon = _read_beacon()
        assert beacon["armed"] is False
        assert beacon["loop_beacon_age_s"] is None
        # wall_ts is still a real wall-clock timestamp even when unarmed.
        assert beacon["wall_ts"] > 1_000_000_000
    finally:
        session_state.last_loop_tick = original


def test_beacon_publish_swallows_redis_error():
    """A Redis error in the beacon publish must never raise (fail-quiet)."""
    session_state.bump_loop_tick()
    failing = MagicMock()
    failing.set.side_effect = RuntimeError("redis down")
    with patch("popoto.redis_db.POPOTO_REDIS_DB", failing):
        # Must not raise.
        sh._publish_loop_beacon()
    assert failing.set.called


def test_heartbeat_disk_write_survives_beacon_redis_error(tmp_path):
    """_write_worker_heartbeat writes the disk heartbeat even if the beacon publish
    hits a Redis error — the disk write precedes and is independent of the beacon."""
    heartbeat_file = sh.Path(sh.__file__).parent.parent / "data" / "last_worker_connected"
    heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    # Baseline mtime marker: remove so we can assert it was (re)written.
    if heartbeat_file.exists():
        heartbeat_file.unlink()

    session_state.bump_loop_tick()
    failing = MagicMock()
    failing.set.side_effect = RuntimeError("redis down")
    with patch("popoto.redis_db.POPOTO_REDIS_DB", failing):
        # Must not raise despite every Redis .set failing.
        sh._write_worker_heartbeat()

    assert heartbeat_file.exists(), "disk heartbeat must be written despite beacon Redis error"
