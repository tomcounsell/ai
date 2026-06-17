"""Unit tests for bridge/liveness.py — stale-update-stream detector signals.

All Redis calls are mocked; no real Redis needed.
"""

import logging
from unittest.mock import MagicMock

import pytest

from bridge.liveness import (
    _PROBE_KEY,
    _TTL_SECONDS,
    _UPDATE_KEY,
    get_last_probe_ok,
    get_last_update_received,
    record_probe_ok,
    record_update_received,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_redis(get_return=None):
    """Return a MagicMock Redis client with get() pre-configured."""
    r = MagicMock()
    r.get.return_value = get_return
    return r


# ---------------------------------------------------------------------------
# record_update_received
# ---------------------------------------------------------------------------


def test_record_update_received_writes_correct_key():
    r = _mock_redis()
    record_update_received(redis_client=r)
    r.set.assert_called_once()
    args, kwargs = r.set.call_args
    assert args[0] == _UPDATE_KEY
    # Value should be a float-parseable string
    float(args[1])
    assert kwargs.get("ex") == _TTL_SECONDS


def test_record_update_received_does_not_raise_on_redis_failure(caplog):
    r = _mock_redis()
    r.set.side_effect = Exception("connection refused")
    with caplog.at_level(logging.WARNING, logger="bridge.liveness"):
        record_update_received(redis_client=r)  # must not raise
    assert any("record_update_received" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# get_last_update_received
# ---------------------------------------------------------------------------


def test_get_last_update_received_returns_none_when_key_missing():
    r = _mock_redis(get_return=None)
    assert get_last_update_received(redis_client=r) is None


def test_get_last_update_received_returns_none_on_corrupt_value():
    r = _mock_redis(get_return="not-a-number")
    assert get_last_update_received(redis_client=r) is None


def test_get_last_update_received_returns_float_on_valid_value():
    r = _mock_redis(get_return="1718000000.123")
    result = get_last_update_received(redis_client=r)
    assert isinstance(result, float)
    assert result == pytest.approx(1718000000.123)


def test_get_last_update_received_returns_none_on_redis_failure(caplog):
    r = _mock_redis()
    r.get.side_effect = Exception("timeout")
    with caplog.at_level(logging.WARNING, logger="bridge.liveness"):
        result = get_last_update_received(redis_client=r)
    assert result is None
    assert any("get_last_update_received" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# record_probe_ok
# ---------------------------------------------------------------------------


def test_record_probe_ok_writes_correct_key():
    r = _mock_redis()
    record_probe_ok(redis_client=r)
    r.set.assert_called_once()
    args, kwargs = r.set.call_args
    assert args[0] == _PROBE_KEY
    float(args[1])
    assert kwargs.get("ex") == _TTL_SECONDS


def test_record_probe_ok_does_not_raise_on_redis_failure(caplog):
    r = _mock_redis()
    r.set.side_effect = Exception("connection refused")
    with caplog.at_level(logging.WARNING, logger="bridge.liveness"):
        record_probe_ok(redis_client=r)  # must not raise
    assert any("record_probe_ok" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# get_last_probe_ok
# ---------------------------------------------------------------------------


def test_get_last_probe_ok_returns_none_when_key_missing():
    r = _mock_redis(get_return=None)
    assert get_last_probe_ok(redis_client=r) is None


def test_get_last_probe_ok_returns_none_on_corrupt_value():
    r = _mock_redis(get_return="bogus")
    assert get_last_probe_ok(redis_client=r) is None


def test_get_last_probe_ok_returns_float_on_valid_value():
    r = _mock_redis(get_return="1718000042.0")
    result = get_last_probe_ok(redis_client=r)
    assert isinstance(result, float)
    assert result == pytest.approx(1718000042.0)


def test_get_last_probe_ok_returns_none_on_redis_failure(caplog):
    r = _mock_redis()
    r.get.side_effect = Exception("timeout")
    with caplog.at_level(logging.WARNING, logger="bridge.liveness"):
        result = get_last_probe_ok(redis_client=r)
    assert result is None
    assert any("get_last_probe_ok" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# Regression guard B2: reconciler must NOT call record_update_received
# ---------------------------------------------------------------------------


def test_reconciler_does_not_call_record_update_received():
    """Ensure reconciler.py never stamps the update-received key.

    If it did, the detector could not distinguish a live update loop from a
    reconciler-only probe.
    """
    reconciler_path = __file__.split("tests/")[0] + "bridge/reconciler.py"
    with open(reconciler_path) as f:
        source = f.read()
    assert "record_update_received" not in source, (
        "bridge/reconciler.py must NOT call record_update_received — "
        "doing so defeats the stale-stream detector (regression guard B2)"
    )


# ---------------------------------------------------------------------------
# Regression guard C-required-1: record_update_received precedes is_duplicate_message
# ---------------------------------------------------------------------------


def test_record_update_received_before_is_duplicate_message_in_bridge():
    """record_update_received must appear BEFORE is_duplicate_message in the
    NewMessage handler so liveness is stamped even for duplicate messages.
    """
    bridge_path = __file__.split("tests/")[0] + "bridge/telegram_bridge.py"
    with open(bridge_path) as f:
        lines = f.readlines()

    update_line = next(
        (i for i, line in enumerate(lines) if "record_update_received" in line), None
    )
    dedup_line = next((i for i, line in enumerate(lines) if "is_duplicate_message" in line), None)

    assert update_line is not None, "record_update_received not found in telegram_bridge.py"
    assert dedup_line is not None, "is_duplicate_message not found in telegram_bridge.py"
    assert update_line < dedup_line, (
        f"record_update_received (line {update_line + 1}) must come BEFORE "
        f"is_duplicate_message (line {dedup_line + 1}) in telegram_bridge.py"
    )
