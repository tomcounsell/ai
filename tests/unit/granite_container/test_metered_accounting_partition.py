"""Metered-accounting partition tests (plan #1842, blockers 1 + 2 / Race 1).

Proves the disjoint-field design: the transcript tailer's ABSOLUTE writes to
``total_*`` and the headless leg's ADDITIVE writes to ``metered_*`` target
non-overlapping fields, so a mixed-transport session cannot lose an update, and
headless cost is counted exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.sdk_client import accumulate_session_tokens
from models.agent_session import AgentSession


def _make_session(session_id: str) -> AgentSession:
    return AgentSession.create(
        session_id=session_id,
        session_type="eng",
        project_key="test-metered",
        status="running",
        created_at=datetime.now(tz=UTC),
        total_input_tokens=0,
        total_output_tokens=0,
        total_cache_read_tokens=0,
        total_cost_usd=0.0,
    )


def _reload(session_id: str) -> AgentSession:
    rows = list(AgentSession.query.filter(session_id=session_id))
    assert rows, f"no AgentSession for {session_id}"
    rows.sort(key=lambda s: s.created_at or 0, reverse=True)
    return rows[0]


@pytest.fixture(autouse=True)
def _cleanup(redis_test_db):
    yield
    for s in list(AgentSession.query.filter(project_key="test-metered")):
        s.delete()


class TestDisjointFields:
    def test_metered_writes_only_metered_fields(self, redis_test_db):
        sid = "metered-only-1"
        _make_session(sid)
        accumulate_session_tokens(sid, 100, 50, 10, 0.25, metered=True, role="dev")
        s = _reload(sid)
        # metered_* populated
        assert s.metered_input_tokens == 100
        assert s.metered_output_tokens == 50
        assert s.metered_cache_read_tokens == 10
        assert s.metered_cost_usd == pytest.approx(0.25)
        # total_* untouched — headless tokens are NEVER folded into total_*
        assert (s.total_input_tokens or 0) == 0
        assert (s.total_output_tokens or 0) == 0
        assert (s.total_cost_usd or 0.0) == 0.0

    def test_default_writes_only_total_fields(self, redis_test_db):
        sid = "total-only-1"
        _make_session(sid)
        accumulate_session_tokens(sid, 200, 80, 20, 0.5)  # metered defaults False
        s = _reload(sid)
        assert s.total_input_tokens == 200
        assert s.total_output_tokens == 80
        assert s.total_cost_usd == pytest.approx(0.5)
        assert (s.metered_input_tokens or 0) == 0
        assert (s.metered_cost_usd or 0.0) == 0.0

    def test_tailer_absolute_write_and_headless_additive_never_clobber(self, redis_test_db):
        """Simulate the mixed-transport interleave: the tailer sets total_*
        ABSOLUTELY (as bridge_adapter._tailer_tick does) while the headless leg
        adds to metered_*. Neither clobbers the other (Race 1)."""
        sid = "mixed-1"
        s = _make_session(sid)

        # Headless (Dev) turn 1 → metered_* += ...
        accumulate_session_tokens(sid, 100, 40, 5, 0.10, metered=True, role="dev")

        # Tailer tick for the PTY (PM) role → ABSOLUTE set on total_* (mirrors
        # _tailer_tick assigning merged PTY totals).
        s = _reload(sid)
        s.total_input_tokens = 1000
        s.total_output_tokens = 400
        s.total_cache_read_tokens = 50
        s.save(
            update_fields=[
                "total_input_tokens",
                "total_output_tokens",
                "total_cache_read_tokens",
            ]
        )

        # Headless (Dev) turn 2 → metered_* += ... AFTER the tailer's absolute write.
        accumulate_session_tokens(sid, 100, 40, 5, 0.10, metered=True, role="dev")

        # Another tailer tick (absolute) — must not touch metered_*.
        s = _reload(sid)
        s.total_input_tokens = 2000
        s.save(update_fields=["total_input_tokens"])

        final = _reload(sid)
        # total_* reflects the tailer's LAST absolute write (PTY only).
        assert final.total_input_tokens == 2000
        # metered_* reflects the SUM of both headless turns (additive, unclobbered).
        assert final.metered_input_tokens == 200
        assert final.metered_output_tokens == 80
        assert final.metered_cost_usd == pytest.approx(0.20)
        # Combined grand total = total + metered (computed at read time).
        combined = float(final.total_cost_usd or 0.0) + float(final.metered_cost_usd or 0.0)
        assert combined == pytest.approx(0.20)


class TestCountedExactlyOnce:
    def test_headless_cost_counted_once_per_turn(self, redis_test_db):
        """Each headless turn accumulates exactly once (no double count)."""
        sid = "once-1"
        _make_session(sid)
        accumulate_session_tokens(sid, 10, 10, 0, 0.05, metered=True, role="pm")
        accumulate_session_tokens(sid, 10, 10, 0, 0.05, metered=True, role="pm")
        s = _reload(sid)
        # Two turns → exactly 2x, never 4x.
        assert s.metered_cost_usd == pytest.approx(0.10)
        assert s.metered_input_tokens == 20


class TestMeteredMetricEmission:
    def test_metered_metric_emitted_from_accumulation(self, redis_test_db, monkeypatch):
        """The session.metered_cost_usd ledger metric is emitted from the single
        metered-accumulation branch with role + project dimensions."""
        recorded = []

        import analytics.collector as collector

        monkeypatch.setattr(
            collector,
            "record_metric",
            lambda name, value, dims=None: recorded.append((name, value, dims)),
        )
        sid = "metric-1"
        _make_session(sid)
        accumulate_session_tokens(sid, 10, 5, 0, 0.30, metered=True, role="dev")
        assert ("session.metered_cost_usd", 0.30, {"role": "dev", "project": "test-metered"}) in [
            (n, pytest.approx(v), d) for (n, v, d) in recorded
        ]

    def test_default_path_emits_no_metered_metric(self, redis_test_db, monkeypatch):
        recorded = []

        import analytics.collector as collector

        monkeypatch.setattr(
            collector,
            "record_metric",
            lambda name, value, dims=None: recorded.append(name),
        )
        sid = "metric-2"
        _make_session(sid)
        accumulate_session_tokens(sid, 10, 5, 0, 0.30)  # not metered
        assert "session.metered_cost_usd" not in recorded
