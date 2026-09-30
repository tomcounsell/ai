"""Unit tests for the #1312 worker-liveness ingestion signal.

Two units under test:

- ``agent.session_health.worker_loop_beacon_fresh`` — the single, fail-closed
  freshness definition. Freshness is keyed ONLY on the wall-clock ``wall_ts``
  (Risk 1); an unarmed-but-fresh beacon is still alive (startup grace); a
  missing/malformed/expired key or ANY Redis error returns ``False``.
- ``bridge.response.react_if_worker_down`` — applies ⚠ + records for the #2178
  clear path when the worker is down, is a no-op when the worker is alive, and
  is fully fail-quiet (a raising ``set_reaction`` never propagates).

All tests patch the Redis client / helpers — no production Redis is touched.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.session_health as sh
from bridge.response import REACTION_WORKER_DOWN, react_if_worker_down

# ---------------------------------------------------------------------------
# worker_loop_beacon_fresh — freshness matrix
# ---------------------------------------------------------------------------


def _fake_redis(raw):
    """A Redis stub whose .get() returns ``raw`` (or raises if ``raw`` is an Exception)."""
    r = MagicMock()
    if isinstance(raw, Exception):
        r.get.side_effect = raw
    else:
        r.get.return_value = raw
    return r


def _patch_redis(raw):
    return patch("popoto.redis_db.POPOTO_REDIS_DB", _fake_redis(raw))


def _beacon(wall_ts, *, armed=True, age=0.0):
    return json.dumps({"wall_ts": wall_ts, "loop_beacon_age_s": age, "armed": armed})


def test_fresh_beacon_is_alive():
    with _patch_redis(_beacon(time.time())):
        assert sh.worker_loop_beacon_fresh() is True


def test_stale_wall_ts_is_down():
    old = time.time() - (sh.BRIDGE_WORKER_BEACON_STALE_S + 30)
    with _patch_redis(_beacon(old)):
        assert sh.worker_loop_beacon_fresh() is False


def test_missing_key_is_down():
    with _patch_redis(None):
        assert sh.worker_loop_beacon_fresh() is False


def test_malformed_json_is_down():
    with _patch_redis("not-json{"):
        assert sh.worker_loop_beacon_fresh() is False


def test_non_numeric_wall_ts_is_down():
    with _patch_redis(json.dumps({"wall_ts": "abc", "armed": True})):
        assert sh.worker_loop_beacon_fresh() is False


def test_missing_wall_ts_field_is_down():
    with _patch_redis(json.dumps({"armed": True})):
        assert sh.worker_loop_beacon_fresh() is False


def test_redis_error_is_down_fail_closed():
    with _patch_redis(RuntimeError("redis down")):
        assert sh.worker_loop_beacon_fresh() is False


def test_unarmed_but_fresh_is_alive_startup_grace():
    """Worker process up, loop not yet ticked → alive (freshness ignores armed)."""
    with _patch_redis(_beacon(time.time(), armed=False, age=None)):
        assert sh.worker_loop_beacon_fresh() is True


def test_bytes_payload_is_decoded():
    with _patch_redis(_beacon(time.time()).encode("utf-8")):
        assert sh.worker_loop_beacon_fresh() is True


def test_host_override_is_used():
    r = _fake_redis(_beacon(time.time()))
    with patch("popoto.redis_db.POPOTO_REDIS_DB", r):
        sh.worker_loop_beacon_fresh(host="other-host")
    r.get.assert_called_once_with("worker:loop_beacon:other-host")


# ---------------------------------------------------------------------------
# react_if_worker_down — reaction behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reacts_and_records_when_worker_down():
    client = MagicMock()
    with (
        patch("agent.session_health.worker_loop_beacon_fresh", return_value=False),
        patch("bridge.response.set_reaction", new=AsyncMock(return_value=True)) as set_rx,
        patch("agent.worker_down_reactions.record_worker_down_reaction") as record,
    ):
        await react_if_worker_down(client, 123, 456, "sess-abc")

    set_rx.assert_awaited_once_with(client, 123, 456, REACTION_WORKER_DOWN)
    record.assert_called_once_with("sess-abc", 123, 456)


@pytest.mark.asyncio
async def test_no_reaction_when_worker_alive():
    client = MagicMock()
    with (
        patch("agent.session_health.worker_loop_beacon_fresh", return_value=True),
        patch("bridge.response.set_reaction", new=AsyncMock()) as set_rx,
        patch("agent.worker_down_reactions.record_worker_down_reaction") as record,
    ):
        await react_if_worker_down(client, 123, 456, "sess-abc")

    set_rx.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
async def test_fail_quiet_when_set_reaction_raises():
    """A raising set_reaction must NOT propagate into the handler (enqueue must proceed)."""
    client = MagicMock()
    with (
        patch("agent.session_health.worker_loop_beacon_fresh", return_value=False),
        patch(
            "bridge.response.set_reaction",
            new=AsyncMock(side_effect=RuntimeError("telegram boom")),
        ),
        patch("agent.worker_down_reactions.record_worker_down_reaction") as record,
    ):
        # Must not raise.
        await react_if_worker_down(client, 123, 456, "sess-abc")

    # set_reaction raised before the record call — recording never happened, but
    # the handler survived (no exception propagated).
    record.assert_not_called()
