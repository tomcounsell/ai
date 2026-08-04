"""valor-session enumerates through the shared seam (issue #2519).

The motivating incident: 22 well-formed ``status="pending"`` AgentSessions sat
in Redis while ``query.filter(status="pending")`` returned nothing. Against that
state ``valor-session kill --all`` reported success having skipped every one of
them, and ``valor-session list --status pending`` showed one row.

These tests reproduce that shape — the index answers empty, the record scan
answers fully — and assert the CLI now sees what the dashboard sees.
"""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import cmd_kill, cmd_list  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.sessions]

TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})


def _stranded(n: int = 22) -> list[MagicMock]:
    """Sessions the record scan sees and the status index does not."""
    sessions = []
    for i in range(n):
        s = MagicMock()
        s.session_id = f"stranded-{i}"
        s.status = "pending"
        s.session_type = "teammate"
        s.created_at = None
        s.message_text = "watchdog crash-storm alert"
        s.auto_continue_count = 0
        sessions.append(s)
    return sessions


def _blind_index_query(sessions):
    """A query double where ``all()`` is complete and ``filter()`` is empty."""
    query = MagicMock()
    query.all.return_value = sessions
    query.filter.return_value = []
    query.count.return_value = 0
    return query


class TestKillAllReachesIndexInvisibleSessions:
    def test_finalizes_every_stranded_session(self):
        sessions = _stranded()
        finalize = MagicMock()
        agent_session = MagicMock(query=_blind_index_query(sessions))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=agent_session),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=finalize,
                    ),
                },
            ),
        ):
            result = cmd_kill(argparse.Namespace(**{"all": True}, id=None, json=False))

        assert result == 0
        assert finalize.call_count == 22

    def test_terminal_sessions_are_left_alone(self):
        """kill --all still targets only pending/running/active."""
        sessions = _stranded(3)
        done = MagicMock()
        done.session_id = "already-done"
        done.status = "completed"
        finalize = MagicMock()
        agent_session = MagicMock(query=_blind_index_query([*sessions, done]))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=agent_session),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=finalize,
                    ),
                },
            ),
        ):
            cmd_kill(argparse.Namespace(**{"all": True}, id=None, json=False))

        killed = {call.args[0].session_id for call in finalize.call_args_list}
        assert killed == {"stranded-0", "stranded-1", "stranded-2"}


class TestListReachesIndexInvisibleSessions:
    def test_status_filter_surfaces_stranded_sessions(self, capsys):
        agent_session = MagicMock(query=_blind_index_query(_stranded()))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules", {"models.agent_session": MagicMock(AgentSession=agent_session)}
            ),
        ):
            result = cmd_list(argparse.Namespace(status="pending", role=None, limit=50, json=True))

        assert result == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 22
        assert all(r["status"] == "pending" for r in rows)

    def test_role_filter_still_applies_client_side(self, capsys):
        sessions = _stranded(3)
        sessions[0].session_type = "eng"
        agent_session = MagicMock(query=_blind_index_query(sessions))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules", {"models.agent_session": MagicMock(AgentSession=agent_session)}
            ),
        ):
            cmd_list(argparse.Namespace(status="pending", role="teammate", limit=50, json=True))

        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 2
