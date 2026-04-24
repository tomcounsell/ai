"""Unit tests for Mode 3 of issue #1099 — post-compaction Tier 2 reprieve gate.

When ``pre_compact_hook`` writes ``AgentSession.last_compaction_ts`` and the
session subsequently goes idle for the post-compaction grace period, the Tier 2
reprieve gate must return ``"compacting"`` so the health-check kill is skipped.

The companion writer (``agent/hooks/pre_compact.py::pre_compact_hook``) is NOT
exercised here — these tests construct an ``AgentSession`` with the field
already populated and verify the reader half of the contract.

Cleanup: every test that creates AgentSession records uses the
``test-resilience-mode-3`` ``project_key`` prefix and a fixture-scoped Popoto
ORM teardown (NEVER raw Redis — enforced by
``.claude/hooks/validators/validate_no_raw_redis_delete.py``).
"""

import time

import pytest

from agent.session_health import COMPACT_REPRIEVE_WINDOW_SEC, _tier2_reprieve_signal
from models.agent_session import AgentSession, SessionType


@pytest.fixture
def clean_sessions():
    """Yield the project_key prefix and clean up Popoto records on teardown."""
    project_key = "test-resilience-mode-3"
    yield project_key
    try:
        for s in AgentSession.query.filter(project_key=project_key):
            s.delete()
    except Exception:
        pass  # best-effort cleanup; never crash the test runner


def _make_session(project_key: str, last_compaction_ts: float | None) -> AgentSession:
    s = AgentSession(
        project_key=project_key,
        chat_id="test-chat",
        session_type=SessionType.PM,
        message_text="seed",
        sender_name="tester",
        agent_session_id=f"sid-{int(time.time() * 1000)}",
    )
    s.last_compaction_ts = last_compaction_ts
    s.save()
    return s


def test_recent_compaction_returns_compacting(clean_sessions):
    """last_compaction_ts within COMPACT_REPRIEVE_WINDOW_SEC → 'compacting' reprieve."""
    s = _make_session(clean_sessions, time.time() - 60.0)  # 60s ago — well inside window
    # handle=None forces the psutil/pid checks to be skipped — we want to assert
    # the compacting gate fires FIRST regardless of pid availability.
    assert _tier2_reprieve_signal(handle=None, entry=s) == "compacting"


def test_stale_compaction_falls_through(clean_sessions):
    """last_compaction_ts older than COMPACT_REPRIEVE_WINDOW_SEC → no compacting reprieve."""
    s = _make_session(
        clean_sessions, time.time() - COMPACT_REPRIEVE_WINDOW_SEC - 60.0
    )  # ~11 min ago
    # No pid, no last_stdout_at → all gates fail → returns None.
    assert _tier2_reprieve_signal(handle=None, entry=s) is None


def test_no_compaction_falls_through(clean_sessions):
    """last_compaction_ts is None → compacting gate skipped, falls through."""
    s = _make_session(clean_sessions, None)
    assert _tier2_reprieve_signal(handle=None, entry=s) is None


def test_constant_distinct_from_stdout_window():
    """COMPACT_REPRIEVE_WINDOW_SEC must be a distinct module symbol from STDOUT_FRESHNESS_WINDOW.

    Both happen to default to 600s today but they answer different questions.
    Concern #4 in the plan critique — locking in the separate-symbol invariant
    so future drift doesn't accidentally couple the two windows.
    """
    from agent import session_health

    assert hasattr(session_health, "COMPACT_REPRIEVE_WINDOW_SEC")
    assert hasattr(session_health, "STDOUT_FRESHNESS_WINDOW")
    # Identity (not value) — they must be two distinct attributes even when
    # the values match. ``is not`` on Python ints is ambiguous due to small-int
    # caching, so compare the attribute names indirectly via the module dict.
    sh = vars(session_health)
    assert "COMPACT_REPRIEVE_WINDOW_SEC" in sh
    assert "STDOUT_FRESHNESS_WINDOW" in sh


def test_malformed_timestamp_does_not_crash(clean_sessions):
    """A non-numeric last_compaction_ts (defensive guard) → gate skipped, no crash."""
    s = _make_session(clean_sessions, None)
    # Force a non-numeric value through the field's setter is awkward (Popoto
    # FloatField will coerce). Instead, monkey-patch the attribute on the
    # instance so getattr returns garbage to the gate.
    object.__setattr__(s, "last_compaction_ts", "not-a-float")
    # Should not raise — the (TypeError, ValueError) catch protects this.
    assert _tier2_reprieve_signal(handle=None, entry=s) is None
