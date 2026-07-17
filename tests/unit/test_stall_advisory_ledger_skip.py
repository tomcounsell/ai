"""Regression: stall-advisory must skip `is_ledger` sessions.

`sdlc-local-{N}` SDLC pipeline anchors are created with `is_ledger=True` and by
design never spawn an SDK subprocess, so they never emit a start/turn event. The
stall classifier therefore returns ``never_started`` for them — an *actionable*
reason. Before this guard, `run_stall_advisory` classified (and could kill) those
ledgers, orphaning the issue lock and deadlocking the SDLC router
(`ISSUE_LOCKED / orphaned_lock`). This was hit live on 2026-07-15 for
`sdlc-local-2101` and `sdlc-local-2086`.

The health loop already skips ledgers (#2042); this pins the stall-path half of
that guard: a ledger must never reach `classify_session_stall`.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from reflections.stall_advisory import run_stall_advisory


def _mk_session(session_id: str, *, is_ledger: bool):
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        status="running",
        is_ledger=is_ledger,
    )


def test_ledger_session_excluded_from_stall_classification():
    ledger = _mk_session("sdlc-local-9999", is_ledger=True)
    normal = _mk_session("0_normalsession", is_ledger=False)

    classified: list[str] = []

    def _spy_classify(events, session=None):
        classified.append(getattr(session, "session_id", "?"))
        # Healthy verdict — no finding, no recovery side effects.
        return SimpleNamespace(level="ok", reason="healthy_recent_turn", signals={})

    mock_query = MagicMock()
    mock_query.filter.return_value = [ledger, normal]

    with (
        patch("models.agent_session.AgentSession.query", mock_query),
        patch("agent.session_stall_classifier.classify_session_stall", _spy_classify),
        patch("agent.session_telemetry.read_session_timeline", lambda sid: []),
        # Force recovery context unavailable so the test never touches prod Redis.
        patch("reflections.stall_advisory._get_redis", side_effect=RuntimeError("no redis")),
    ):
        result = run_stall_advisory()

    # The ledger must never be classified; the normal session must be.
    assert "sdlc-local-9999" not in classified, "ledger was classified — guard missing"
    assert "0_normalsession" in classified
    # Only the non-ledger session is counted as a running session.
    assert result["summary"].startswith("1 running session(s)")


def test_all_ledgers_yields_no_classification():
    ledgers = [_mk_session(f"sdlc-local-{n}", is_ledger=True) for n in (1, 2, 3)]
    classified: list[str] = []

    def _spy_classify(events, session=None):
        classified.append(getattr(session, "session_id", "?"))
        return SimpleNamespace(level="ok", reason="healthy", signals={})

    mock_query = MagicMock()
    mock_query.filter.return_value = ledgers

    with (
        patch("models.agent_session.AgentSession.query", mock_query),
        patch("agent.session_stall_classifier.classify_session_stall", _spy_classify),
        patch("agent.session_telemetry.read_session_timeline", lambda sid: []),
        patch("reflections.stall_advisory._get_redis", side_effect=RuntimeError("no redis")),
    ):
        result = run_stall_advisory()

    assert classified == [], "no ledger should ever be classified"
    assert result["summary"].startswith("0 running session(s)")
    assert result["status"] == "ok"
