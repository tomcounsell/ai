"""Unit tests for cmd_kill() in tools/valor_session.py.

Tests cover:
- kill --all kills all non-terminal sessions via finalize_session
- kill --id <ID> kills a specific session via finalize_session
- kill --id <ID> on an already-terminal session returns 0 with a warning
- kill --id <ID> with a nonexistent session returns 1 with an error
- kill --all with no non-terminal sessions returns 0 with empty killed list
- kill --all with partial failure captures errors without propagating
- --json output for success and partial-failure cases
- No ValueError raised from transition_status (regression guard)
"""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import cmd_kill  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})


def _make_session(session_id: str, status: str = "running") -> MagicMock:
    """Build a minimal mock AgentSession."""
    s = MagicMock()
    s.session_id = session_id
    s.status = status
    return s


def _kill_args(
    *,
    kill_all: bool = False,
    session_id: str | None = None,
    as_json: bool = False,
) -> argparse.Namespace:
    """Build a Namespace matching the kill subcommand args."""
    return argparse.Namespace(
        **{"all": kill_all},
        id=session_id,
        json=as_json,
    )


# ---------------------------------------------------------------------------
# kill --all
# ---------------------------------------------------------------------------


class TestCmdKillAll:
    def test_kills_all_non_terminal_sessions(self, capsys):
        """kill --all calls finalize_session for each non-terminal session."""
        session_a = _make_session("sess-a", "running")
        session_b = _make_session("sess-b", "pending")

        def _fake_filter(status):
            if status == "running":
                return [session_a]
            if status == "pending":
                return [session_b]
            return []

        mock_query = MagicMock()
        mock_query.filter.side_effect = lambda **kw: _fake_filter(kw.get("status", ""))

        with (
            patch("tools.valor_session._load_env"),
            patch("models.agent_session.AgentSession") as mock_cls,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
            patch("models.session_lifecycle.TERMINAL_STATUSES", TERMINAL_STATUSES),
        ):
            mock_cls.query = mock_query
            # Patch imports inside cmd_kill
            with patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                    ),
                },
            ):
                result = cmd_kill(_kill_args(kill_all=True))

        assert result == 0

    def test_no_sessions_returns_zero(self, capsys):
        """kill --all with no sessions returns 0 and empty killed list."""
        mock_query = MagicMock()
        mock_query.filter.return_value = []

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=MagicMock(query=mock_query)),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=MagicMock(),
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(kill_all=True))

        assert result == 0

    def test_partial_failure_captured_not_propagated(self, capsys):
        """kill --all captures per-session errors in the errors list."""
        bad_session = _make_session("sess-bad", "running")
        good_session = _make_session("sess-good", "running")

        call_count = 0

        def _finalize_side_effect(s, status, reason=""):
            nonlocal call_count
            call_count += 1
            if s.session_id == "sess-bad":
                raise RuntimeError("Redis timeout")

        def _fake_filter(status):
            if status == "running":
                return [bad_session, good_session]
            return []

        mock_query = MagicMock()
        mock_query.filter.side_effect = lambda **kw: _fake_filter(kw.get("status", ""))

        mock_finalize = MagicMock(side_effect=_finalize_side_effect)
        mock_cls = MagicMock()
        mock_cls.query = mock_query

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(kill_all=True))

        # good session killed, bad session errored → returns 1
        assert result == 1
        assert call_count == 2  # finalize was attempted for both

    def test_json_output_all(self, capsys):
        """kill --all --json outputs valid JSON with killed and errors keys."""
        session_a = _make_session("sess-a", "running")

        def _fake_filter(status):
            if status == "running":
                return [session_a]
            return []

        mock_query = MagicMock()
        mock_query.filter.side_effect = lambda **kw: _fake_filter(kw.get("status", ""))

        mock_cls = MagicMock()
        mock_cls.query = mock_query

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=MagicMock(),
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(kill_all=True, as_json=True))

        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "killed" in data
        assert "errors" in data
        assert "sess-a" in data["killed"]
        assert data["errors"] == []


# ---------------------------------------------------------------------------
# kill --id
# ---------------------------------------------------------------------------


class TestCmdKillById:
    def test_kills_specific_session(self, capsys):
        """kill --id <ID> calls finalize_session for the matched session."""
        session = _make_session("sess-xyz", "running")

        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        mock_finalize = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(session_id="sess-xyz"))

        assert result == 0
        mock_finalize.assert_called_once_with(session, "killed", reason="valor-session kill")

    def test_nonexistent_session_returns_1(self, capsys):
        """kill --id with unknown ID returns 1 and prints stderr."""
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=MagicMock(),
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(session_id="does-not-exist"))

        assert result == 1

    def test_already_terminal_returns_0_no_finalize(self, capsys):
        """kill --id on a session already in terminal status returns 0 without killing."""
        session = _make_session("sess-done", "killed")

        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        mock_finalize = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(session_id="sess-done"))

        assert result == 0
        mock_finalize.assert_not_called()

    def test_json_output_success(self, capsys):
        """kill --id --json outputs valid JSON on success."""
        session = _make_session("sess-xyz", "running")

        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=MagicMock(),
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(session_id="sess-xyz", as_json=True))

        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "killed" in data
        assert "sess-xyz" in data["killed"]


# ---------------------------------------------------------------------------
# Regression guard: no ValueError from transition_status
# ---------------------------------------------------------------------------


class TestNoValueErrorRegression:
    def test_kill_all_does_not_call_transition_status(self):
        """Regression: cmd_kill must NOT call transition_status (rejects terminal statuses)."""
        session = _make_session("sess-a", "running")

        mock_query = MagicMock()
        mock_query.filter.side_effect = lambda **kw: (
            [session] if kw.get("status") == "running" else []
        )

        mock_cls = MagicMock()
        mock_cls.query = mock_query

        mock_finalize = MagicMock()
        mock_transition = MagicMock(side_effect=ValueError("must not be called"))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                        transition_status=mock_transition,
                    ),
                },
            ),
        ):
            # Should complete without ValueError
            result = cmd_kill(_kill_args(kill_all=True))

        assert result == 0
        mock_transition.assert_not_called()
        mock_finalize.assert_called()

    def test_kill_by_id_does_not_call_transition_status(self):
        """Regression: cmd_kill --id must NOT call transition_status."""
        session = _make_session("sess-xyz", "running")

        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        mock_finalize = MagicMock()
        mock_transition = MagicMock(side_effect=ValueError("must not be called"))

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES=TERMINAL_STATUSES,
                        finalize_session=mock_finalize,
                        transition_status=mock_transition,
                    ),
                },
            ),
        ):
            result = cmd_kill(_kill_args(session_id="sess-xyz"))

        assert result == 0
        mock_transition.assert_not_called()
        mock_finalize.assert_called_once()
