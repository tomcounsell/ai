"""Unit tests for tools.sdlc_dispatch session resolution (#1671).

The #1671 skew explicitly named "dispatch-history entries to the wrong session"
as a symptom, but this module had zero coverage. These tests pin the corrected
behavior:

- ``record`` resolves with ``ensure=True`` so a cold-start
  ``dispatch record --issue-number N`` creates/uses ``sdlc-local-N`` rather than
  env-resolving to a divergent inherited session or silently no-opping.
- ``record`` under a divergent ``VALOR_SESSION_ID`` lands the dispatch entry on
  the issue-scoped session (the direct #1671 regression for the dispatch writer).
- ``get`` and ``reset`` stay non-ensuring — they must not fabricate a session.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class TestDispatchRecordEnsures:
    """The record path passes ensure=True (B1, #1671)."""

    def test_record_resolves_with_ensure_true(self):
        """_cli_record calls find_session with ensure=True so the dispatch write
        has an issue-scoped home on a cold start."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        find_mock = MagicMock(return_value=session)

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", return_value=True),
            patch.object(
                sdlc_dispatch, "get_dispatch_history", return_value=[{"skill": "/do-build"}]
            ),
        ):
            result = sdlc_dispatch._cli_record(args)

        # The critical assertion: record resolves with ensure=True.
        find_mock.assert_called_once_with(session_id=None, issue_number=1671, ensure=True)
        assert result == {"ok": True, "history_length": 1}

    def test_record_lands_on_issue_session_under_divergent_env(self, monkeypatch):
        """#1671 regression: with VALOR_SESSION_ID pointing at a DIFFERENT
        session, a `dispatch record --issue-number N` resolves the issue-scoped
        session (sdlc-local-N), not the divergent env session.

        Exercises the real find_session resolver end-to-end (not mocked) to
        prove the precedence fix routes the dispatch write correctly.
        """
        from tools import sdlc_dispatch

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = MagicMock(name="issue_session")
        issue_session.session_type = "eng"
        issue_session.session_id = "sdlc-local-1671"

        captured = {}

        def _capture(session, skill, pr_number=None):
            captured["session"] = session
            captured["skill"] = skill
            return True

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None
        )

        with (
            # The real find_session is used; its issue-first pass hits this.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", side_effect=_capture),
            patch.object(sdlc_dispatch, "get_dispatch_history", return_value=[{}]),
        ):
            result = sdlc_dispatch._cli_record(args)

        # The dispatch write landed on the issue-scoped session, not the env one.
        assert captured["session"] is issue_session
        assert result == {"ok": True, "history_length": 1}

    def test_record_cold_start_creates_via_ensure(self, monkeypatch):
        """Cold start (no pre-existing session, no env) → ensure creates
        sdlc-local-N and the dispatch write lands there."""
        from tools import sdlc_dispatch

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        created = MagicMock(name="created_session")
        created.session_type = "eng"

        captured = {}

        def _capture(session, skill, pr_number=None):
            captured["session"] = session
            return True

        args = SimpleNamespace(
            session_id=None, issue_number=1671, skill="/do-build", pr_number=None
        )

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [created]

        with (
            # No existing issue session on the first lookup.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            # ensure_session creates sdlc-local-1671; the re-resolve returns it.
            patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"session_id": "sdlc-local-1671", "created": True},
            ) as ensure_mock,
            patch("tools._sdlc_utils.AgentSession", mock_as),
            patch.object(sdlc_dispatch, "record_dispatch_for_session", side_effect=_capture),
            patch.object(sdlc_dispatch, "get_dispatch_history", return_value=[{}]),
        ):
            result = sdlc_dispatch._cli_record(args)

        ensure_mock.assert_called_once_with(1671)
        assert captured["session"] is created
        assert result == {"ok": True, "history_length": 1}


class TestDispatchGetResetNonEnsuring:
    """get/reset must not fabricate a session (no ensure=True)."""

    def test_get_does_not_ensure(self):
        from tools import sdlc_dispatch

        find_mock = MagicMock(return_value=None)
        args = SimpleNamespace(session_id=None, issue_number=1671)

        with patch.object(sdlc_dispatch, "_find_session", find_mock):
            result = sdlc_dispatch._cli_get(args)

        # No ensure kwarg → defaults to ensure=False (no session created).
        find_mock.assert_called_once_with(session_id=None, issue_number=1671)
        _, kwargs = find_mock.call_args
        assert "ensure" not in kwargs or kwargs["ensure"] is False
        assert result == []

    def test_reset_does_not_ensure(self):
        from tools import sdlc_dispatch

        find_mock = MagicMock(return_value=None)
        args = SimpleNamespace(session_id=None, issue_number=1671)

        with patch.object(sdlc_dispatch, "_find_session", find_mock):
            result = sdlc_dispatch._cli_reset(args)

        find_mock.assert_called_once_with(session_id=None, issue_number=1671)
        _, kwargs = find_mock.call_args
        assert "ensure" not in kwargs or kwargs["ensure"] is False
        assert result == {"ok": False, "history_length": 0}
