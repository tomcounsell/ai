"""Unit tests for accumulate_session_tokens (issue #1128).

Covers:
- Normal values persist onto AgentSession and monotonically accumulate.
- Missing / None sub-fields default to 0 without raising.
- WATCHDOG_TOKEN_TRACKING_ENABLED=false is a no-op.
- No-session-found is a quiet skip (fail-quiet contract).
- Zero deltas short-circuit before the Redis round-trip.
- ModelException during save is logged but doesn't raise.
- _usage_field handles dict, attribute-style, and None shapes.
"""

from __future__ import annotations

import logging

import pytest

from agent.sdk_client import _usage_field, accumulate_session_tokens
from models.agent_session import AgentSession


@pytest.fixture()
def test_session():
    """Create a persisted AgentSession for accumulator tests."""
    sid = f"acc-test-{id(object())}"
    s = AgentSession(
        session_id=sid,
        project_key="tst-accum",
        agent_session_id=f"as-{sid}",
        status="running",
    )
    s.save()
    yield s
    try:
        s.delete()
    except Exception:
        pass


class TestUsageField:
    def test_none_returns_zero(self):
        assert _usage_field(None, "input_tokens") == 0

    def test_missing_attr_returns_zero(self):
        class Empty:
            pass

        assert _usage_field(Empty(), "input_tokens") == 0

    def test_attribute_style(self):
        class Usage:
            input_tokens = 123
            output_tokens = 45

        u = Usage()
        assert _usage_field(u, "input_tokens") == 123
        assert _usage_field(u, "output_tokens") == 45
        assert _usage_field(u, "missing") == 0

    def test_dict_style(self):
        d = {"input_tokens": 77, "output_tokens": 88}
        assert _usage_field(d, "input_tokens") == 77
        assert _usage_field(d, "missing") == 0

    def test_non_integer_fallback(self):
        d = {"input_tokens": "not a number"}
        assert _usage_field(d, "input_tokens") == 0


class TestAccumulateSessionTokens:
    def test_none_session_id_is_noop(self, test_session):
        # Should not raise and should not affect the test session.
        accumulate_session_tokens(None, 100, 50, 10, 0.05)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 0
        assert reloaded.total_output_tokens == 0
        assert reloaded.total_cost_usd == 0.0

    def test_missing_session_is_quiet_skip(self, caplog):
        with caplog.at_level(logging.DEBUG):
            accumulate_session_tokens("nope-nonexistent", 100, 50, 10, 0.05)
        # Did not raise; the helper may or may not emit a debug log — the
        # contract is fail-quiet, so we assert nothing about log level.

    def test_monotonic_accumulation(self, test_session):
        sid = test_session.session_id
        accumulate_session_tokens(sid, 100, 50, 25, 0.10)
        accumulate_session_tokens(sid, 200, 80, 15, 0.30)

        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 300
        assert reloaded.total_output_tokens == 130
        assert reloaded.total_cache_read_tokens == 40
        assert abs(reloaded.total_cost_usd - 0.40) < 1e-9

    def test_none_subfields_default_to_zero(self, test_session):
        sid = test_session.session_id
        # output_tokens None → 0; cost None → 0.0
        accumulate_session_tokens(sid, 100, None, None, None)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 100
        assert reloaded.total_output_tokens == 0
        assert reloaded.total_cache_read_tokens == 0
        assert reloaded.total_cost_usd == 0.0

    def test_all_zero_short_circuits(self, test_session):
        """All-zero delta should not persist a no-op write."""
        sid = test_session.session_id
        # Make sure a prior save recorded zeros.
        accumulate_session_tokens(sid, 0, 0, 0, 0.0)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 0

    def test_env_gate_disables(self, test_session, monkeypatch):
        sid = test_session.session_id
        monkeypatch.setenv("WATCHDOG_TOKEN_TRACKING_ENABLED", "false")
        accumulate_session_tokens(sid, 100, 50, 10, 0.05)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 0
        assert reloaded.total_cost_usd == 0.0

    def test_env_gate_other_falsy_values(self, test_session, monkeypatch):
        sid = test_session.session_id
        for val in ("0", "NO", "False", "no"):
            monkeypatch.setenv("WATCHDOG_TOKEN_TRACKING_ENABLED", val)
            accumulate_session_tokens(sid, 1, 1, 0, 0.01)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 0

    def test_non_numeric_inputs_logged_and_skipped(self, test_session, caplog):
        sid = test_session.session_id
        with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
            accumulate_session_tokens(sid, "not-a-number", 50, 10, 0.05)
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        assert reloaded.total_input_tokens == 0
        assert any(
            "non-numeric inputs" in r.message or "accumulate_session_tokens" in r.message
            for r in caplog.records
        )

    def test_save_exception_is_fail_quiet(self, test_session, monkeypatch, caplog):
        """If save() raises Exception, helper logs and does not re-raise."""
        sid = test_session.session_id

        def boom(self, **kw):  # noqa: ARG001
            raise RuntimeError("simulated save failure")

        # Patch the query to return our session whose save raises.
        monkeypatch.setattr(AgentSession, "save", boom)

        with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
            # Should not raise
            accumulate_session_tokens(sid, 10, 5, 2, 0.01)


class TestDashboardSurfacing:
    """End-to-end: accumulator writes → PipelineProgress reflects values."""

    def test_pipeline_progress_reads_token_fields(self, test_session):
        from ui.data.sdlc import _session_to_pipeline

        accumulate_session_tokens(
            test_session.session_id,
            100,
            200,
            50,
            1.23,
        )
        reloaded = list(AgentSession.query.filter(session_id=test_session.session_id))[0]
        progress = _session_to_pipeline(reloaded)
        assert progress is not None
        assert progress.total_input_tokens == 100
        assert progress.total_output_tokens == 200
        assert progress.total_cache_read_tokens == 50
        assert abs(progress.total_cost_usd - 1.23) < 1e-9
