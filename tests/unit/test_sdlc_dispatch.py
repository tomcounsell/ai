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
- ``record_dispatch_for_session()`` calls ``touch_issue_lock()`` DIRECTLY,
  deriving ``issue_number`` from ``session.issue_url`` -- it must not assume
  ``ensure_session()`` ran first (#1954).
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


class TestParseIssueNumberFromUrl:
    """_parse_issue_number_from_url mirrors find_session_by_issue's
    /issues/{N} suffix convention, in the reverse direction (url -> number)."""

    def test_extracts_issue_number(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url("https://github.com/tomcounsell/ai/issues/1954") == 1954

    def test_returns_none_for_missing_url(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url(None) is None
        assert _parse_issue_number_from_url("") is None

    def test_returns_none_for_url_without_issue_segment(self):
        from tools.sdlc_dispatch import _parse_issue_number_from_url

        assert _parse_issue_number_from_url("https://github.com/tomcounsell/ai/pull/42") is None


class TestRecordDispatchIssueLock:
    """Issue #1954: record_dispatch_for_session() calls touch_issue_lock()
    DIRECTLY (not via ensure_session()) before writing the dispatch event,
    deriving issue_number by parsing session.issue_url. This must hold for
    the continuing-session path too, where find_session(ensure=True)'s Step-2
    short-circuit never calls ensure_session()."""

    def _lock_result(self, acquired: bool, owner_session_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(acquired=acquired, owner_session_id=owner_session_id)

    def test_refuses_and_returns_false_when_lock_held_by_different_session(self):
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3001"
        session.session_id = "sdlc-local-3001"

        lock_mock = MagicMock(return_value=self._lock_result(False, "other-live-session"))

        with patch("models.session_lifecycle.touch_issue_lock", lock_mock):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is False
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 3001
        assert args[1] == "sdlc-local-3001"

    def test_continuing_session_derives_issue_number_from_issue_url(self):
        """The continuing-session path: no prior ensure_session() call, session
        resolved via find_session_by_issue -- issue_number must be derived
        from session.issue_url and the lock still enforced (acquired)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = "https://github.com/tomcounsell/ai/issues/3002"
        session.session_id = "sdlc-local-3002"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-3002"))

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is True
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 3002
        assert args[1] == "sdlc-local-3002"

    def test_no_lock_call_when_session_has_no_issue_url(self):
        """A session with no parseable issue number must not attempt a lock
        check -- the write proceeds unguarded (unchanged no-issue-context
        behavior)."""
        from tools.sdlc_dispatch import record_dispatch_for_session

        session = MagicMock()
        session.issue_url = None
        session.session_id = "some-session"

        lock_mock = MagicMock()

        with (
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            ok = record_dispatch_for_session(session, skill="/do-build")

        assert ok is True
        lock_mock.assert_not_called()

    def test_cli_record_renews_lock_via_record_dispatch_for_session(self):
        """Issue #1954 Task 4: the `dispatch record` CLI subcommand's existing
        Task-3 wiring (record_dispatch_for_session() calling touch_issue_lock()
        directly) already satisfies "dispatch record renews the lock" -- no
        separate/additional CLI-layer call is added. This exercises the CLI
        entry point (_cli_record) end-to-end through the unmocked
        record_dispatch_for_session() to confirm the renewal fires as a side
        effect of the subcommand, not just of the lower-level helper in
        isolation."""
        from tools import sdlc_dispatch

        session = MagicMock(name="issue_session")
        session.issue_url = "https://github.com/tomcounsell/ai/issues/1954"
        session.session_id = "sdlc-local-1954"

        find_mock = MagicMock(return_value=session)
        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-1954"))

        args = SimpleNamespace(
            session_id=None, issue_number=1954, skill="/do-build", pr_number=None
        )

        with (
            patch.object(sdlc_dispatch, "_find_session", find_mock),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools.stage_states_helpers.update_stage_states", return_value=True),
        ):
            result = sdlc_dispatch._cli_record(args)

        assert result["ok"] is True
        lock_mock.assert_called_once()
        args_called = lock_mock.call_args.args
        assert args_called[0] == 1954
        assert args_called[1] == "sdlc-local-1954"

    def test_two_sessions_same_issue_second_refused(self, monkeypatch):
        """End-to-end (real Redis, no mocking of touch_issue_lock): two
        record_dispatch_for_session() calls for the same issue from distinct
        simulated processes -- the second is refused."""
        import models.session_lifecycle as session_lifecycle
        from tools.sdlc_dispatch import record_dispatch_for_session

        session_a = MagicMock()
        session_a.issue_url = "https://github.com/tomcounsell/ai/issues/3050"
        session_a.session_id = "sdlc-local-3050"

        session_b = MagicMock()
        session_b.issue_url = "https://github.com/tomcounsell/ai/issues/3050"
        session_b.session_id = "sdlc-local-3050"

        monkeypatch.setattr(session_lifecycle, "_process_holder_token", lambda: "proc-A")
        with patch("tools.stage_states_helpers.update_stage_states", return_value=True):
            ok_a = record_dispatch_for_session(session_a, skill="/do-build")
        assert ok_a is True

        monkeypatch.setattr(session_lifecycle, "_process_holder_token", lambda: "proc-B")
        with patch("tools.stage_states_helpers.update_stage_states", return_value=True):
            ok_b = record_dispatch_for_session(session_b, skill="/do-build")
        assert ok_b is False
