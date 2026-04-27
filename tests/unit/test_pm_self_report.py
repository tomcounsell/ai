"""PM mid-work self-report behavior (issue #1172, Phase 1).

Goldilocks goal: a PM session running real work emits exactly ONE short
status message via ``valor-telegram send`` between the first dev-child
completion and the final delivery, then goes silent until completion. The
``self_report_sent_at`` field on AgentSession enforces the once-per-session
cap.

These tests exercise ``agent.session_completion._emit_pm_self_report``
directly, monkey-patching the subprocess invocation. Real subprocess
testing belongs in the integration suite.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent import session_completion
from models.agent_session import AgentSession, SessionType


def _make_pm(suffix: str, **overrides) -> AgentSession:
    defaults = dict(
        project_key="test-pm-self-report",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="Working on issue #1172 — PM session liveness",
        sender_name="x",
        session_id=f"pm-self-report-{suffix}-{time.time_ns()}",
        working_dir="/tmp",
        status="running",
    )
    defaults.update(overrides)
    s = AgentSession.create(**defaults)
    return s


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    try:
        for s in AgentSession.query.all():
            if (
                isinstance(getattr(s, "project_key", None), str)
                and s.project_key == "test-pm-self-report"
            ):
                try:
                    s.delete()
                except Exception:
                    pass
    except Exception:
        pass


def test_self_report_sends_and_marks_timestamp(monkeypatch):
    pm = _make_pm("send-once")

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    sent = session_completion._emit_pm_self_report(pm, project_name="Valor")
    assert sent is True

    # The valor-telegram subprocess was invoked with the PM channel.
    args, _ = fake_run.call_args
    cmd = args[0]
    assert cmd[0] == "valor-telegram"
    assert cmd[1] == "send"
    chat_idx = cmd.index("--chat") + 1
    assert cmd[chat_idx] == "PM: Valor"

    # Frequency cap state is set.
    pm.refresh_from_db() if hasattr(pm, "refresh_from_db") else None
    refreshed = AgentSession.query.filter(session_id=pm.session_id)[0]
    assert refreshed.self_report_sent_at is not None


def test_self_report_skips_when_already_sent(monkeypatch):
    """Frequency cap: a second invocation must NOT send."""
    pm = _make_pm("cap")
    from datetime import UTC, datetime

    pm.self_report_sent_at = datetime.now(tz=UTC)
    pm.save(update_fields=["self_report_sent_at"])

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    sent = session_completion._emit_pm_self_report(pm, project_name="Valor")
    assert sent is False
    fake_run.assert_not_called()


def test_self_report_skips_when_project_name_missing(monkeypatch):
    """Without a project_name, no fallback channel is attempted."""
    pm = _make_pm("no-project")

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    sent = session_completion._emit_pm_self_report(pm, project_name=None)
    assert sent is False
    fake_run.assert_not_called()


def test_self_report_subprocess_failure_keeps_state_unset(monkeypatch):
    pm = _make_pm("subprocess-fail")

    fake_run = MagicMock(return_value=MagicMock(returncode=1, stderr="boom"))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    sent = session_completion._emit_pm_self_report(pm, project_name="Valor")
    assert sent is False

    # Frequency cap state must remain None — retry on next dev completion.
    refreshed = AgentSession.query.filter(session_id=pm.session_id)[0]
    assert refreshed.self_report_sent_at is None


def test_self_report_subprocess_exception_does_not_propagate(monkeypatch):
    pm = _make_pm("subprocess-raise")

    def _boom(*_a, **_kw):
        raise OSError("simulated PATH lookup failure")

    monkeypatch.setattr(session_completion.subprocess, "run", _boom)

    # Must not raise.
    sent = session_completion._emit_pm_self_report(pm, project_name="Valor")
    assert sent is False


def test_self_report_skips_non_pm_sessions(monkeypatch):
    pm = _make_pm("dev-instead", session_type=SessionType.DEV)

    fake_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
    monkeypatch.setattr(session_completion.subprocess, "run", fake_run)

    sent = session_completion._emit_pm_self_report(pm, project_name="Valor")
    assert sent is False
    fake_run.assert_not_called()
