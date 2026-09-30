"""Unit tests for `_cleanup_duplicate_sessions` dedup narrowing (#1877 defect #4).

Only a `completed` session means a message was actually handled. A prior
`failed` / `killed` / `abandoned` attempt did NOT handle the message, so a
legitimate `pending` retry must survive.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update.run import _cleanup_duplicate_sessions


def _session(status: str, chat_id: str, msg_id: int, sid: str) -> MagicMock:
    s = MagicMock()
    s.status = status
    s.chat_id = chat_id
    s.telegram_message_id = msg_id
    s.agent_session_id = sid
    return s


class _FakeQuery:
    """Stand-in for AgentSession.query supporting `.filter(status=...)`."""

    def __init__(self, by_status: dict[str, list]):
        self._by_status = by_status

    def filter(self, status: str):
        return list(self._by_status.get(status, []))


def _run_with_sessions(by_status: dict[str, list]):
    """Invoke the cleanup with a fake AgentSession model + captured finalize calls."""
    finalized: list[tuple[str, str]] = []

    fake_model = MagicMock()
    fake_model.query = _FakeQuery(by_status)

    def _fake_finalize(session, status, **kwargs):
        finalized.append((session.agent_session_id, status))

    with (
        patch.dict(
            "sys.modules",
            {
                "models.agent_session": MagicMock(AgentSession=fake_model),
                "models.session_lifecycle": MagicMock(finalize_session=_fake_finalize),
            },
        ),
    ):
        killed = _cleanup_duplicate_sessions(Path("/tmp"))
    return killed, finalized


def test_failed_terminal_does_not_kill_pending_retry():
    """A `failed` attempt + matching `pending` retry → pending survives."""
    pending = _session("pending", "-100", 42, "retry-1")
    failed = _session("failed", "-100", 42, "failed-0")
    killed, finalized = _run_with_sessions({"pending": [pending], "failed": [failed]})
    assert killed == 0
    assert finalized == []


def test_killed_and_abandoned_do_not_kill_pending_retry():
    """`killed`/`abandoned` attempts also leave a matching `pending` retry alive."""
    pending = _session("pending", "-100", 7, "retry-1")
    killed_s = _session("killed", "-100", 7, "killed-0")
    abandoned = _session("abandoned", "-100", 7, "abandoned-0")
    killed, finalized = _run_with_sessions(
        {"pending": [pending], "killed": [killed_s], "abandoned": [abandoned]}
    )
    assert killed == 0
    assert finalized == []


def test_completed_terminal_kills_pending_duplicate():
    """A `completed` session + matching `pending` → the pending duplicate is killed."""
    pending = _session("pending", "-100", 99, "dup-1")
    completed = _session("completed", "-100", 99, "done-0")
    killed, finalized = _run_with_sessions({"pending": [pending], "completed": [completed]})
    assert killed == 1
    assert finalized == [("dup-1", "killed")]
