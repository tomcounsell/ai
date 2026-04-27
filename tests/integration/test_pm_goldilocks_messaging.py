"""Acceptance criterion (issue #1172): a PM session that completes work
emits ≤1 mid-work status message + 1 final "done" message — not the
spam-mode cadence and not silent.

The integration test exercises ``_emit_pm_self_report`` end-to-end with a
real (mocked-subprocess) trigger sequence:

1. PM session is mid-work; first dev-child completion fires.
   → exactly ONE valor-telegram subprocess call to ``PM: <project>``.
2. Second dev-child completion fires while the first self-report is still
   on record.
   → ZERO additional valor-telegram calls (frequency cap).

Total mid-work messages: 1. Final delivery is the runner's job (not exercised
here — see test_pm_final_delivery.py).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent import session_completion
from models.agent_session import AgentSession, SessionType


@pytest.fixture
def goldilocks_pm():
    s = AgentSession.create(
        project_key="test-pm-goldilocks",
        chat_id="goldilocks-chat",
        session_type=SessionType.PM,
        message_text="Run the build for issue #1172",
        sender_name="Test",
        session_id=f"pm-goldilocks-{time.time_ns()}",
        working_dir="/tmp/goldilocks",
        status="running",
        project_config={"name": "Valor"},
    )
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _project_name_from(parent: AgentSession) -> str | None:
    pc = getattr(parent, "project_config", None) or {}
    if isinstance(pc, dict):
        return pc.get("name") or pc.get("display_name")
    return None


def test_goldilocks_at_most_one_mid_work_message(goldilocks_pm, monkeypatch):
    """Two dev-child completions → exactly ONE valor-telegram send."""
    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    project_name = _project_name_from(goldilocks_pm)

    # First dev-child completion: should send.
    sent_a = session_completion._emit_pm_self_report(goldilocks_pm, project_name=project_name)
    assert sent_a is True
    assert fake_run.call_count == 1

    # Refresh from Redis (simulate the post-save state being read by the
    # next handler call).
    refreshed = AgentSession.query.filter(session_id=goldilocks_pm.session_id)[0]

    # Second dev-child completion: cap holds, no additional send.
    sent_b = session_completion._emit_pm_self_report(refreshed, project_name=project_name)
    assert sent_b is False
    assert fake_run.call_count == 1, "Frequency cap broken: a second send slipped through."


def test_self_report_is_addressed_to_pm_channel(goldilocks_pm, monkeypatch):
    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    session_completion._emit_pm_self_report(
        goldilocks_pm, project_name=_project_name_from(goldilocks_pm)
    )

    cmd = fake_run.call_args[0][0]
    assert cmd[0] == "valor-telegram"
    assert cmd[1] == "send"
    chat_idx = cmd.index("--chat") + 1
    assert cmd[chat_idx] == "PM: Valor"
    body = cmd[-1]
    # The body is short and templated.
    assert isinstance(body, str)
    assert 5 < len(body) < 300


def test_no_self_report_for_dev_session(monkeypatch):
    """Dev sessions never emit a PM self-report, regardless of state.

    Built fresh (not derived from the goldilocks fixture) because session_type
    is a Popoto KeyField — its value forms the Redis identity and cannot be
    mutated in-place.
    """
    dev = AgentSession.create(
        project_key="test-pm-goldilocks",
        chat_id="dev-chat",
        session_type=SessionType.DEV,
        message_text="Dev work",
        sender_name="Test",
        session_id=f"dev-no-self-report-{time.time_ns()}",
        working_dir="/tmp/dev",
        status="running",
    )

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    try:
        sent = session_completion._emit_pm_self_report(dev, project_name="Valor")
        assert sent is False
        fake_run.assert_not_called()
    finally:
        try:
            dev.delete()
        except Exception:
            pass
