"""Unit tests for the token-threshold alert in session_watchdog (issue #1128).

The watchdog reads `AgentSession.total_input_tokens + total_output_tokens`
and steers when the sum crosses `TOKEN_ALERT_THRESHOLD` on a `running`
session. The watchdog is READ-ONLY for these fields — writes happen in
the worker process via `accumulate_session_tokens`.

Validates:
- Crossing the threshold on a `running` session triggers one steer.
- Below-threshold sessions do NOT trigger.
- Non-running sessions do NOT trigger (token alert is for live sessions).
- Cooldown key is reason-scoped (`token_alert`) and holds for
  `TOKEN_ALERT_COOLDOWN` seconds.
- The message carries both the dollar cost and the token count verbatim.
- Watchdog never writes the token fields (read-only contract).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.steering import clear_steering_queue, pop_all_steering_messages
from monitoring.session_watchdog import (
    TOKEN_ALERT_THRESHOLD,
    assess_session_health,
)


def _db():
    import popoto.redis_db as _rdb

    return _rdb.POPOTO_REDIS_DB


@pytest.fixture(autouse=True)
def _clear_cooldown_keys():
    """Clear cooldown keys between tests."""
    db = _db()
    for key in db.scan_iter("watchdog:steer_cooldown:*"):
        db.delete(key)
    yield
    db = _db()
    for key in db.scan_iter("watchdog:steer_cooldown:*"):
        db.delete(key)


def _session(
    session_id: str,
    status: str,
    in_toks: int,
    out_toks: int,
    cost: float = 1.23,
    updated_at=None,
    started_at=None,
):
    """Build a lightweight session-like object for assess_session_health."""
    import time

    now = time.time()
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=f"as-{session_id}",
        status=status,
        project_key="tst-tok",
        chat_id="test-chat",
        updated_at=updated_at or now,
        started_at=started_at or (now - 100),
        total_input_tokens=in_toks,
        total_output_tokens=out_toks,
        total_cost_usd=cost,
        tool_call_count=0,
    )


class TestTokenThresholdTriggers:
    def test_crossing_threshold_on_running_triggers_steer(self):
        sid = "tok-alert-1"
        clear_steering_queue(sid)
        s = _session(sid, "running", TOKEN_ALERT_THRESHOLD // 2, TOKEN_ALERT_THRESHOLD // 2 + 10)
        # With read_recent_tool_calls returning [], repetition and cascade
        # cannot fire, so only the token branch is exercised.
        with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
            result = assess_session_health(s)
        assert any("Token budget" in issue for issue in result["issues"])
        msgs = pop_all_steering_messages(sid)
        assert len(msgs) == 1
        assert "Token budget exceeded" in msgs[0]["text"]
        assert msgs[0]["sender"] == "watchdog"

    def test_below_threshold_does_not_trigger(self):
        sid = "tok-alert-2"
        clear_steering_queue(sid)
        s = _session(sid, "running", 1000, 500)  # way below threshold
        with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
            result = assess_session_health(s)
        assert not any("Token budget" in issue for issue in result["issues"])
        msgs = pop_all_steering_messages(sid)
        assert msgs == []

    def test_non_running_status_does_not_trigger(self):
        """Dormant / paused / completed should NOT get token-alert steers."""
        for status in ("dormant", "paused", "completed", "failed"):
            sid = f"tok-alert-{status}"
            clear_steering_queue(sid)
            s = _session(sid, status, TOKEN_ALERT_THRESHOLD, TOKEN_ALERT_THRESHOLD)
            with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
                assess_session_health(s)
            msgs = pop_all_steering_messages(sid)
            assert msgs == [], f"status={status} should not steer"


class TestTokenAlertCooldown:
    def test_duplicate_fire_suppressed(self):
        sid = "tok-alert-dup"
        clear_steering_queue(sid)
        s = _session(sid, "running", TOKEN_ALERT_THRESHOLD, 100)
        with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
            assess_session_health(s)
            assess_session_health(s)  # second tick within cooldown
        msgs = pop_all_steering_messages(sid)
        assert len(msgs) == 1  # cooldown absorbed the second call


class TestReadOnlyContract:
    def test_watchdog_does_not_mutate_token_fields(self):
        sid = "tok-alert-readonly"
        clear_steering_queue(sid)
        s = _session(sid, "running", TOKEN_ALERT_THRESHOLD, 100, cost=2.0)
        before = (
            s.total_input_tokens,
            s.total_output_tokens,
            s.total_cost_usd,
        )
        with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
            assess_session_health(s)
        after = (
            s.total_input_tokens,
            s.total_output_tokens,
            s.total_cost_usd,
        )
        assert before == after, "watchdog must not mutate token fields"


class TestMessageFormatting:
    def test_message_carries_cost_and_tokens(self):
        sid = "tok-alert-msg"
        clear_steering_queue(sid)
        s = _session(sid, "running", TOKEN_ALERT_THRESHOLD, 0, cost=9.99)
        with patch("monitoring.session_watchdog.read_recent_tool_calls", return_value=[]):
            assess_session_health(s)
        msgs = pop_all_steering_messages(sid)
        assert len(msgs) == 1
        text = msgs[0]["text"]
        assert "$9.99" in text
        # Tokens are formatted with a thousands separator
        assert f"{TOKEN_ALERT_THRESHOLD:,}" in text
