"""Unit tests for Mode 2 of issue #1099 — context-usage warning log.

When per-turn ``input_tokens / context_window`` exceeds 0.75, the harness
emits a single WARNING log so operators have an early-warning signal before
the session degrades. Pure observability: no state change, no behavior change.
"""

import logging

import pytest

from agent.sdk_client import _log_context_usage_if_risky


def test_warning_emitted_above_threshold(caplog):
    """input_tokens / context_window > 0.75 → one WARNING with 'context_usage'."""
    # opus has a 200_000-token context window; 160_000 = 0.80 → above threshold.
    usage = {"input_tokens": 160_000}
    with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
        _log_context_usage_if_risky(session_id="sess-1", model="opus", usage=usage)

    matches = [r for r in caplog.records if "context_usage" in r.getMessage()]
    assert len(matches) == 1, f"Expected 1 'context_usage' WARNING, got {len(matches)}"
    msg = matches[0].getMessage()
    assert "session_id=sess-1" in msg
    assert "model=opus" in msg
    assert "input_tokens=160000" in msg


def test_no_warning_below_threshold(caplog):
    """input_tokens / context_window <= 0.75 → no WARNING emitted."""
    # 50_000 / 200_000 = 0.25 — well below threshold.
    usage = {"input_tokens": 50_000}
    with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
        _log_context_usage_if_risky(session_id="sess-2", model="opus", usage=usage)

    matches = [r for r in caplog.records if "context_usage" in r.getMessage()]
    assert matches == [], f"Expected no 'context_usage' WARNING, got {len(matches)}"


def test_none_usage_no_crash(caplog):
    """usage=None is the typical harness-error path → no warning, no exception."""
    # Must not raise.
    _log_context_usage_if_risky(session_id="sess-3", model="opus", usage=None)
    # And no warning should have been emitted.
    matches = [r for r in caplog.records if "context_usage" in r.getMessage()]
    assert matches == []


def test_unknown_model_logs_skip_warning(caplog):
    """Unknown model name → skip pct calc + emit a single 'unknown model' WARNING.

    Concern #2 in the plan critique. The harness must NOT crash if a session
    is configured with a model not registered in ``config/models.py`` — instead
    it logs a flagging WARNING so operators can fix the registration.
    """
    usage = {"input_tokens": 160_000}
    with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
        _log_context_usage_if_risky(
            session_id="sess-4",
            model="not-a-registered-model",
            usage=usage,
        )

    # Should emit the unknown-model WARNING but NOT the context_usage WARNING.
    unknown = [r for r in caplog.records if "unknown model" in r.getMessage()]
    assert len(unknown) == 1, f"Expected 1 'unknown model' WARNING, got {len(unknown)}"

    pct_msgs = [r for r in caplog.records if "context_usage pct=" in r.getMessage()]
    assert pct_msgs == [], "Should not emit pct warning when model is unknown"


def test_zero_input_tokens_no_warning(caplog):
    """input_tokens=0 → early-return path, no warning."""
    usage = {"input_tokens": 0}
    with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
        _log_context_usage_if_risky(session_id="sess-5", model="opus", usage=usage)

    matches = [r for r in caplog.records if "context_usage" in r.getMessage()]
    assert matches == []


def test_helper_never_raises_on_malformed_usage():
    """Malformed usage dict must not crash the turn — observability is non-fatal."""
    # Missing key, garbage type, negative numbers — none should raise.
    _log_context_usage_if_risky(session_id="s", model="opus", usage={})
    _log_context_usage_if_risky(session_id="s", model="opus", usage={"input_tokens": "garbage"})
    _log_context_usage_if_risky(session_id="s", model="opus", usage={"input_tokens": -1})


@pytest.mark.parametrize(
    "alias,full_id",
    [
        ("opus", "claude-opus-4-5-20251101"),
        ("sonnet", "claude-sonnet-4-5-20250929"),
        ("haiku", "claude-haiku-4-5-20251001"),
    ],
)
def test_alias_and_full_id_both_resolve(alias, full_id, caplog):
    """Alias and full model id must both resolve to the same context window."""
    from config.models import get_model_context_window

    assert get_model_context_window(alias) == get_model_context_window(full_id)
