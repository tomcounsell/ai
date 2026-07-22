"""Unit tests for tools.sdlc_session_ensure.

Tests cover:
- Creates session when none exists
- Returns existing session (idempotent)
- Handles Redis errors gracefully
- CLI output format
- Invalid input handling
- Env-var short-circuit for bridge-initiated sessions
- --kill-orphans zombie cleanup
- Issue-level ownership lock wiring at all five return points (#1954)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEnsureSession:
    """Tests for the ensure_session function."""

    def test_returns_existing_session_by_issue(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_session = MagicMock()
        mock_session.session_id = "sdlc-local-941"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]  # post-save readback

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=941)

        assert result["session_id"] == "sdlc-local-941"
        assert result["created"] is False
        # ensure_session mints and emits the run identity (#2003), mirrored
        # to the session record.
        assert result["run_id"]
        assert mock_session.active_run_id == result["run_id"]

    def test_creates_new_session(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-942"

        mock_as = MagicMock()
        # First filter call: idempotent existing-by-id check (none). Second:
        # the post-save run_id readback (the just-created session).
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(
                issue_number=942,
                issue_url="https://github.com/tomcounsell/ai/issues/942",
            )

        assert result["session_id"] == "sdlc-local-942"
        assert result["created"] is True
        assert result["run_id"]
        assert mock_new_session.active_run_id == result["run_id"]
        mock_as.create_local.assert_called_once()

    def test_creates_new_session_with_is_ledger_true_at_create_call(self):
        """Non-executable ledger flag (#2042): is_ledger=True must be present
        in the SAME kwargs dict passed to create_local(), not added by a
        follow-up write. This closes the race where a worker could observe
        the row before a separate is_ledger=True write landed."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-947"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=947)

        assert result["created"] is True
        mock_as.create_local.assert_called_once()
        # is_ledger=True must be a kwarg of the create_local() call itself --
        # present on the very first persisted row, not a later save().
        _call_args, call_kwargs = mock_as.create_local.call_args
        assert call_kwargs.get("is_ledger") is True

    def test_idempotent_by_session_id(self):
        """If a session with sdlc-local-{N} already exists, return it."""
        from tools.sdlc_session_ensure import ensure_session

        mock_existing = MagicMock()
        mock_existing.session_id = "sdlc-local-943"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_existing]

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=943)

        assert result["session_id"] == "sdlc-local-943"
        assert result["created"] is False
        assert result["run_id"]

    def test_returns_empty_for_invalid_issue_number(self):
        from tools.sdlc_session_ensure import ensure_session

        assert ensure_session(issue_number=0) == {}
        assert ensure_session(issue_number=-1) == {}

    def test_handles_redis_error_gracefully(self):
        from tools.sdlc_session_ensure import ensure_session

        with patch(
            "tools._sdlc_utils.find_session_by_issue",
            side_effect=ConnectionError("Redis down"),
        ):
            result = ensure_session(issue_number=941)

        assert result == {}

    def test_transition_status_failure_still_returns_session(self):
        """Session is usable even if transition_status fails."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-944"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.transition_status",
                side_effect=RuntimeError("transition failed"),
            ),
        ):
            result = ensure_session(issue_number=944)

        assert result["session_id"] == "sdlc-local-944"
        assert result["created"] is True
        assert result["run_id"]

    def test_project_key_resolution_error_returns_empty(self):
        """#1158: on ProjectKeyResolutionError, ensure_session returns {} and
        does NOT create an AgentSession with a coerced/wrong project_key.

        The plan's governing principle: if the project→repo pairing can't be
        resolved, refuse to create a session rather than silently misroute.
        """
        from tools.sdlc_session_ensure import ensure_session
        from tools.valor_session import ProjectKeyResolutionError

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectKeyResolutionError(
                    cwd="/tmp/unknown", available_keys=["valor", "ai"]
                ),
            ),
        ):
            result = ensure_session(issue_number=945)

        # Empty dict → no session created.
        assert result == {}
        # AgentSession.create_local was NEVER called — no coercion to a wrong
        # project happened.
        mock_as.create_local.assert_not_called()

    def test_projects_config_unavailable_error_returns_empty(self):
        """#1158: on ProjectsConfigUnavailableError (e.g., projects.json load
        failure), ensure_session returns {} with no session created.
        """
        from tools.sdlc_session_ensure import ensure_session
        from tools.valor_session import ProjectsConfigUnavailableError

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectsConfigUnavailableError(
                    "could not load projects.json: permission denied"
                ),
            ),
        ):
            result = ensure_session(issue_number=946)

        assert result == {}
        mock_as.create_local.assert_not_called()


class TestCLI:
    """Tests for CLI invocation."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--issue-number" in result.stdout
        assert "--issue-url" in result.stdout

    def test_missing_required_arg(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode != 0


class TestBridgeShortCircuit:
    """Tests for the VALOR_SESSION_ID / AGENT_SESSION_ID env short-circuit."""

    def test_short_circuit_returns_env_session_when_live_eng(self, monkeypatch):
        """Env var set + live PM session that OWNS the issue returns it without
        creating anything and without an issue lookup (C1, #1671).

        The mock bridge session is given a REAL str issue_url ending
        /issues/1140 — the reconciliation now reads issue_url, so a MagicMock
        default would truthily match and mask the assertion. With an owning
        issue_url, the short-circuit keeps the env session and never calls
        find_session_by_issue.
        """
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_valor_-1003449100931_691")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        bridge_session = MagicMock()
        bridge_session.session_id = "tg_valor_-1003449100931_691"
        bridge_session.session_type = "eng"
        bridge_session.status = "running"
        # REAL str issue_url — the env session OWNS issue 1140.
        bridge_session.issue_url = "https://github.com/tomcounsell/ai/issues/1140"

        # find_session_by_issue must NOT be called on the owning short-circuit path.
        fsbi = MagicMock()

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge_session]  # post-save readback

        with (
            patch("tools._sdlc_utils.find_session", return_value=bridge_session),
            patch("tools._sdlc_utils.find_session_by_issue", fsbi),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=1140)

        assert result["session_id"] == "tg_valor_-1003449100931_691"
        assert result["created"] is False
        assert result["run_id"]
        fsbi.assert_not_called()

    def test_non_owning_env_session_prefers_existing_issue_session(self, monkeypatch):
        """C1 (#1671): env var points at a live PM session that does NOT own the
        issue, AND an sdlc-local-N session exists → ensure_session returns the
        issue-scoped session, not the divergent env session. No duplicate."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-other-issue")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        # Env session is a live PM but owns a DIFFERENT issue.
        env_session = MagicMock()
        env_session.session_id = "parent-pm-other-issue"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        # The issue-scoped session that actually owns issue 1171.
        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-1171"

        mock_as = MagicMock()  # create_local must NOT be called (no duplicate).
        mock_as.query.filter.return_value = [issue_session]  # post-save readback

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=1171)

        assert result["session_id"] == "sdlc-local-1171"
        assert result["created"] is False
        assert result["run_id"]
        # No new session fabricated when an issue-scoped one already exists.
        mock_as.create_local.assert_not_called()

    def test_non_owning_env_session_creates_when_no_issue_session(self, monkeypatch):
        """C1 (#1671): env session does NOT own the issue and NO issue-scoped
        session exists yet → fall through to create sdlc-local-N (never return
        the divergent env session)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-other-issue")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock()
        env_session.session_id = "parent-pm-other-issue"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1172"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            # find_session_by_issue returns None in BOTH the reconciliation
            # call and the legacy existing-session lookup.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1172)

        # Created the issue-scoped session; did NOT return the env session.
        assert result["session_id"] == "sdlc-local-1172"
        assert result["created"] is True
        assert result["run_id"]

    def test_short_circuit_falls_through_when_env_session_missing(self, monkeypatch):
        """Env var set but no live session — fall through to legacy create path."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "stale_session_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1141"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=None),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1141)

        assert result["session_id"] == "sdlc-local-1141"
        assert result["created"] is True
        assert result["run_id"]

    def test_short_circuit_falls_through_when_only_agent_session_id_stale(self, monkeypatch):
        """Twin of the above (#2190 Test Impact): a stale AGENT_SESSION_ID with
        VALOR_SESSION_ID unset -- the pre-B2 fallback identifier -- must also
        degrade to the legacy create path, not just a stale VALOR_SESSION_ID."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.setenv("AGENT_SESSION_ID", "stale_agent_session_hex")

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-11411"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=None),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=11411)

        assert result["session_id"] == "sdlc-local-11411"
        assert result["created"] is True
        assert result["run_id"]

    def test_empty_env_var_does_not_short_circuit(self, monkeypatch):
        """Empty-string env var behaves identically to unset."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1142"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        find_session_mock = MagicMock()
        with (
            patch("tools._sdlc_utils.find_session", find_session_mock),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1142)

        assert result["session_id"] == "sdlc-local-1142"
        assert result["created"] is True
        assert result["run_id"]
        # find_session should NOT be called when env vars are empty
        find_session_mock.assert_not_called()

    def test_short_circuit_falls_through_for_non_owning_session(self, monkeypatch):
        """Env var points at an Eng session that does not own the issue — must NOT activate (C2)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "dev_session_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        dev_session = MagicMock()
        dev_session.session_id = "dev_session_id"
        dev_session.session_type = "eng"
        dev_session.status = "running"
        # REAL str issue_url pointing at a DIFFERENT issue — required since the
        # C1 reconciliation (commit 0d04f4ac, #1671/#1672) reads issue_url via
        # ``.endswith()``; a bare MagicMock attribute would truthily match and
        # falsely keep the env session.
        dev_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1143"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=dev_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1143)

        # Must NOT return the dev session id; must fall through to create.
        assert result["session_id"] == "sdlc-local-1143"
        assert result["created"] is True
        assert result["run_id"]

    def test_short_circuit_falls_through_for_terminal_status_eng_session(self, monkeypatch):
        """Env points at a terminal-status PM session (AD1) — fall through."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "completed_pm_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        for offset, terminal_status in enumerate(
            (
                "completed",
                "failed",
                "killed",
                "abandoned",
                "cancelled",
            )
        ):
            # Distinct issue number per iteration: each successful
            # ensure_session acquires a LIVE issue lock in the test Redis db
            # (#2003), so re-running the same issue within one test would hit
            # the deliberate no-adopt ISSUE_LOCKED path.
            issue_number = 11440 + offset
            local_id = f"sdlc-local-{issue_number}"

            terminal_session = MagicMock()
            terminal_session.session_id = "completed_pm_id"
            terminal_session.session_type = "eng"
            terminal_session.status = terminal_status

            mock_new_session = MagicMock()
            mock_new_session.session_id = local_id

            mock_as = MagicMock()
            mock_as.query.filter.side_effect = [[], [mock_new_session]]
            mock_as.create_local.return_value = mock_new_session

            with (
                patch("tools._sdlc_utils.find_session", return_value=terminal_session),
                patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
                patch("models.agent_session.AgentSession", mock_as),
                patch("models.session_lifecycle.transition_status"),
            ):
                result = ensure_session(issue_number=issue_number)

            # Must fall through and create a fresh session; not reuse the
            # terminal-status bridge session.
            assert result["session_id"] == local_id, (
                f"failed for terminal status {terminal_status!r}"
            )
            assert result["created"] is True
            assert result["run_id"]

    def test_short_circuit_degrades_on_find_session_error(self, monkeypatch):
        """Redis error during env lookup falls through without crashing."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "some_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1145"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch(
                "tools._sdlc_utils.find_session",
                side_effect=ConnectionError("Redis down"),
            ),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1145)

        # Degrades gracefully to the create path.
        assert result["session_id"] == "sdlc-local-1145"
        assert result["created"] is True
        assert result["run_id"]


class TestEnvShortCircuitIdentifierMismatch:
    """Issue #2190: the resolver's env short-circuit reads
    ``VALOR_SESSION_ID or AGENT_SESSION_ID`` and resolves it via
    ``find_session(session_id=...)`` -- a filter on the ``session_id`` field.
    The headless runner historically injected ONLY ``AGENT_SESSION_ID =
    session.agent_session_id`` (the per-run hex, Popoto AutoKey ``id``),
    which is NOT the same namespace as ``session_id`` (``tg_valor_...``,
    ``sdlc-local-...``). For a bridge PM session the two differ, so the
    lookup always misses and WS-F's ownerless-adopt branch never fires.

    These tests exercise the REAL (unmocked) ``find_session`` resolution
    against a live Popoto-backed AgentSession -- mocking ``find_session``
    directly (as ``TestBridgeShortCircuit`` does) would mask this bug,
    since a mock returns its configured value regardless of which env var
    or identifier shape was actually passed in.
    """

    PROJECT_KEY = "test-2190-resolver-mismatch"

    @pytest.fixture
    def cleanup_test_sessions(self):
        from models.agent_session import AgentSession

        def _cleanup():
            try:
                stale = [
                    s
                    for s in AgentSession.query.all()
                    if getattr(s, "project_key", None) == self.PROJECT_KEY
                ]
            except Exception:
                return
            for s in stale:
                try:
                    s.delete()
                except Exception:
                    pass

        _cleanup()
        yield
        _cleanup()

    def _make_ownerless_bridge_session(self, issue_number: int):
        """Live, ownerless bridge PM session: session_id=tg_valor_..., a
        distinct hex agent_session_id (Popoto AutoKey), is_ledger=False,
        no issue_url -- the exact WS-F adoption target."""
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status

        session_id = f"tg_valor_test2190_{issue_number}"
        session = AgentSession.create_eng(
            session_id=session_id,
            project_key=self.PROJECT_KEY,
            working_dir="/tmp",
            chat_id=f"chat_2190_{issue_number}",
            telegram_message_id=1,
            message_text=f"SDLC {issue_number}",
            sender_name="Test2190",
        )
        try:
            transition_status(session, "running", "test setup")
        except Exception:
            pass

        # Fixture sanity: ownerless, non-ledger, and the two identifier
        # namespaces are (as required by Risk 5) disjoint for this record.
        assert not (session.issue_url or "").strip()
        assert session.is_ledger in (False, "False", None)
        assert session.agent_session_id
        assert session.session_id != session.agent_session_id
        return session

    def test_red_state_agent_session_id_only_mints_duplicate(
        self, monkeypatch, cleanup_test_sessions
    ):
        """Documents the bug -- PASSES on current main. With only
        AGENT_SESSION_ID=<hex> injected (pre-B2 production shape) and
        VALOR_SESSION_ID unset, the resolver's session_id-field filter
        misses the live ownerless PM session and mints a duplicate
        sdlc-local-<N>."""
        from models.agent_session import AgentSession
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 209001
        bridge_session = self._make_ownerless_bridge_session(issue_number)
        dup_session_id = f"sdlc-local-{issue_number}"

        monkeypatch.setenv("AGENT_SESSION_ID", bridge_session.agent_session_id)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        try:
            result = ensure_session(issue_number=issue_number)

            assert result["session_id"] == dup_session_id
            assert result["created"] is True
            dup_rows = list(AgentSession.query.filter(session_id=dup_session_id))
            assert len(dup_rows) == 1, "expected the bug to mint exactly one duplicate"
        finally:
            for s in AgentSession.query.filter(session_id=dup_session_id):
                s.delete()

    def test_green_state_valor_session_id_adopts_ownerless_session(
        self, monkeypatch, cleanup_test_sessions
    ):
        """The B2 shape: VALOR_SESSION_ID=<session_id> AND
        AGENT_SESSION_ID=<hex> -- exactly what
        ``agent/session_executor.py``'s ``_harness_env`` produces once the
        seam is wired. Must ADOPT the ownerless bridge session (same
        session_id returned, created=False, run_id bound) and mint NO
        sdlc-local-<N>."""
        from models.agent_session import AgentSession
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 209002
        bridge_session = self._make_ownerless_bridge_session(issue_number)
        dup_session_id = f"sdlc-local-{issue_number}"

        monkeypatch.setenv("VALOR_SESSION_ID", bridge_session.session_id)
        monkeypatch.setenv("AGENT_SESSION_ID", bridge_session.agent_session_id)

        try:
            result = ensure_session(issue_number=issue_number)

            assert result["session_id"] == bridge_session.session_id
            assert result["created"] is False
            assert result["run_id"]
            dup_rows = list(AgentSession.query.filter(session_id=dup_session_id))
            assert dup_rows == [], "adoption must not also mint sdlc-local-<N>"
        finally:
            for s in AgentSession.query.filter(session_id=dup_session_id):
                s.delete()

    def test_namespace_disjointness_fixture_invariant(self, monkeypatch, cleanup_test_sessions):
        """Risk 5: B2's VALOR_SESSION_ID-first resolution is only safe because
        a session_id value (tg_valor_..., sdlc-local-..., local-...) can never
        collide with an agent_session_id value (a 32-char Popoto AutoKey hex).
        Pin that invariant directly against a live fixture record: the two
        identifiers differ in both value AND shape (session_id is prefixed /
        non-hex; agent_session_id is exactly 32 lowercase hex characters)."""
        import re

        issue_number = 209003
        bridge_session = self._make_ownerless_bridge_session(issue_number)

        session_id = bridge_session.session_id
        agent_session_id = bridge_session.agent_session_id

        assert session_id != agent_session_id
        # agent_session_id is a 32-char hex AutoKey.
        assert re.fullmatch(r"[0-9a-f]{32}", agent_session_id), (
            f"agent_session_id must be 32-char hex; got {agent_session_id!r}"
        )
        # session_id carries a human-readable prefix and is never bare hex.
        assert session_id.startswith("tg_valor_")
        assert not re.fullmatch(r"[0-9a-f]{32}", session_id), (
            "session_id must never collide with the hex agent_session_id shape"
        )

    def test_behavioral_equivalence_live_self_owned_session_returns_same_no_remint(
        self, monkeypatch, cleanup_test_sessions
    ):
        """Risk 4(a): a LIVE self-owned eng session that already owns issue N
        resolves via the VALOR_SESSION_ID short-circuit and returns the SAME
        session with no re-bind/re-stamp/mint -- outcome identical to the
        pre-B2 issue-based (find_session_by_issue) path."""
        from models.agent_session import AgentSession
        from models.session_lifecycle import transition_status
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 209004
        session_id = f"tg_valor_test2190_{issue_number}"
        issue_url = f"https://github.com/tomcounsell/ai/issues/{issue_number}"

        session = AgentSession.create_eng(
            session_id=session_id,
            project_key=self.PROJECT_KEY,
            working_dir="/tmp",
            chat_id=f"chat_2190_{issue_number}",
            telegram_message_id=1,
            message_text=f"SDLC {issue_number}",
            sender_name="Test2190",
            issue_url=issue_url,
        )
        try:
            transition_status(session, "running", "test setup")
        except Exception:
            pass

        dup_session_id = f"sdlc-local-{issue_number}"
        monkeypatch.setenv("VALOR_SESSION_ID", session_id)
        monkeypatch.setenv("AGENT_SESSION_ID", session.agent_session_id)

        try:
            result = ensure_session(issue_number=issue_number)

            assert result["session_id"] == session_id
            assert result["created"] is False
            assert result["run_id"]
            # No competing sdlc-local-<N> minted for an already-owning session.
            dup_rows = list(AgentSession.query.filter(session_id=dup_session_id))
            assert dup_rows == []
        finally:
            for s in AgentSession.query.filter(session_id=dup_session_id):
                s.delete()

    def test_behavioral_equivalence_terminal_status_session_not_adopted(
        self, monkeypatch, cleanup_test_sessions
    ):
        """Risk 4(b): a TERMINAL-status (completed/killed/failed) self-owned
        session for issue N must NOT be adopted/resurrected via the
        VALOR_SESSION_ID short-circuit -- it falls through to WS-F's
        liveness/ownership guard exactly as before B2, and a fresh
        sdlc-local-<N> is minted instead."""
        from models.agent_session import AgentSession
        from models.session_lifecycle import finalize_session, transition_status
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 209005
        session_id = f"tg_valor_test2190_{issue_number}"

        session = AgentSession.create_eng(
            session_id=session_id,
            project_key=self.PROJECT_KEY,
            working_dir="/tmp",
            chat_id=f"chat_2190_{issue_number}",
            telegram_message_id=1,
            message_text=f"SDLC {issue_number}",
            sender_name="Test2190",
        )
        try:
            transition_status(session, "running", "test setup")
        except Exception:
            pass
        try:
            finalize_session(session, "completed", "test: mark terminal before adoption attempt")
        except Exception:
            session.status = "completed"
            session.save()

        dup_session_id = f"sdlc-local-{issue_number}"
        monkeypatch.setenv("VALOR_SESSION_ID", session_id)
        monkeypatch.setenv("AGENT_SESSION_ID", session.agent_session_id)

        try:
            result = ensure_session(issue_number=issue_number)

            # Must NOT resurrect the terminal session -- a fresh sdlc-local-<N>
            # is created instead.
            assert result["session_id"] == dup_session_id
            assert result["created"] is True
        finally:
            for s in AgentSession.query.filter(session_id=dup_session_id):
                s.delete()


class TestOwnerlessAdoption:
    """WS-F (#2026): adopt an ownerless bridge PM eng session instead of minting
    a competing ``sdlc-local-{N}``.

    A bridge PM session built from raw message text (``"SDLC 1312"``) never gets
    ``issue_url`` stamped, so #1147's ownership check missed and a duplicate
    top-level session was minted. The env short-circuit now adopts the ownerless
    env session (bind run_id + write signal, then stamp ``issue_url`` last).
    """

    def test_ownerless_env_session_is_adopted(self, monkeypatch):
        """Env eng session with issue_url=None → adopt (created False), stamp
        issue_url from the explicit arg, mint nothing, no issue-lookup detour."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "tg_valor_-1003449100931_1192")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "tg_valor_-1003449100931_1192"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None  # the observed ownerless bridge case

        fsbi = MagicMock()  # divergent-owner detour must NOT run
        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", fsbi),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run_abc123", None),
            ),
        ):
            result = ensure_session(
                issue_number=1312,
                issue_url="https://github.com/tomcounsell/ai/issues/1312",
            )

        assert result["session_id"] == "tg_valor_-1003449100931_1192"
        assert result["created"] is False
        assert result["run_id"] == "run_abc123"
        # issue_url stamped LAST (after a successful bind) and persisted.
        assert pm_session.issue_url == "https://github.com/tomcounsell/ai/issues/1312"
        pm_session.save.assert_called_once()
        # No competitor minted; no divergent-owner detour.
        mock_as.create_local.assert_not_called()
        fsbi.assert_not_called()

    def test_ownerless_variants_none_empty_whitespace_all_adopted(self, monkeypatch):
        """None, "", and whitespace-only issue_url are ALL ownerless → adopt."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        for offset, ownerless in enumerate((None, "", "   ")):
            issue_number = 13120 + offset
            monkeypatch.setenv("AGENT_SESSION_ID", f"pm-{issue_number}")

            pm_session = MagicMock()
            pm_session.session_id = f"pm-{issue_number}"
            pm_session.session_type = "eng"
            pm_session.status = "running"
            pm_session.issue_url = ownerless

            mock_as = MagicMock()

            with (
                patch("tools._sdlc_utils.find_session", return_value=pm_session),
                patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
                patch("models.agent_session.AgentSession", mock_as),
                patch(
                    "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                    return_value=(f"run-{issue_number}", None),
                ),
            ):
                result = ensure_session(
                    issue_number=issue_number,
                    issue_url=f"https://github.com/tomcounsell/ai/issues/{issue_number}",
                )

            assert result["session_id"] == f"pm-{issue_number}", (
                f"failed to adopt for ownerless issue_url={ownerless!r}"
            )
            assert result["created"] is False
            mock_as.create_local.assert_not_called()

    def test_ownerless_adoption_builds_issue_url_when_arg_absent(self, monkeypatch):
        """No --issue-url arg → stamp is built from the resolved repo slug."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-13199")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-13199"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch("tools._sdlc_utils._resolve_target_repo", return_value="tomcounsell/ai"),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run-13199", None),
            ),
        ):
            result = ensure_session(issue_number=13199)  # no issue_url

        assert result["created"] is False
        assert pm_session.issue_url == "https://github.com/tomcounsell/ai/issues/13199"

    def test_ownerless_adoption_bind_failure_returns_error_no_mint(self, monkeypatch):
        """Bind fails (foreign ISSUE_LOCKED) → return the error dict verbatim,
        NEVER fall through to a mint, and leave issue_url untouched (no stamp).

        Falling through under a held foreign lock would mint the exact
        ``sdlc-local-{N}`` orphan WS-F prevents (critique blocker #2)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-1313")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-1313"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None

        error_dict = {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_run_id": "foreign_run",
            "owner_session_id": "foreign_sess",
            "orphaned_lock": False,
        }

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=(None, error_dict),
            ),
        ):
            result = ensure_session(issue_number=1313)

        assert result == error_dict
        # No stamp on a bind failure (catches a stamp-first regression).
        assert pm_session.issue_url is None
        pm_session.save.assert_not_called()
        # No competitor minted.
        mock_as.create_local.assert_not_called()

    def test_ownerless_adoption_stamp_failure_returns_adopted_no_mint(self, monkeypatch):
        """Bind succeeds but the issue_url save raises → return the adopted
        session (run already owned); NEVER fall through to a mint."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-1314")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        pm_session = MagicMock()
        pm_session.session_id = "pm-1314"
        pm_session.session_type = "eng"
        pm_session.status = "running"
        pm_session.issue_url = None
        pm_session.save.side_effect = ConnectionError("Redis down during stamp")

        mock_as = MagicMock()

        with (
            patch("tools._sdlc_utils.find_session", return_value=pm_session),
            patch("tools._sdlc_utils.find_session_by_issue", MagicMock()),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.sdlc_session_ensure._acquire_run_lock_and_bind",
                return_value=("run-1314", None),
            ),
        ):
            result = ensure_session(
                issue_number=1314,
                issue_url="https://github.com/tomcounsell/ai/issues/1314",
            )

        assert result["session_id"] == "pm-1314"
        assert result["created"] is False
        assert result["run_id"] == "run-1314"
        # Stamp failure did not fall through to a mint under a held lock.
        mock_as.create_local.assert_not_called()

    def test_divergent_owner_not_adopted(self, monkeypatch):
        """Env session owning a DIFFERENT issue is NOT adopted (its issue_url is
        untouched) → existing divergent-owner fall-through preserved (#1671)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("AGENT_SESSION_ID", "pm-other")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        env_session = MagicMock()
        env_session.session_id = "pm-other"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-1315"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [issue_session]  # post-save readback

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=1315)

        # Divergent env session preferred the issue-scoped session; not adopted.
        assert result["session_id"] == "sdlc-local-1315"
        assert result["created"] is False
        # The divergent env session's issue_url was NOT overwritten.
        assert env_session.issue_url == "https://github.com/tomcounsell/ai/issues/9999"
        env_session.save.assert_not_called()


def _make_orphan_session(
    session_id,
    age_seconds,
    heartbeat=None,
    session_type="eng",
    last_activity_seconds=None,
):
    """Build a MagicMock AgentSession with orphan-relevant fields.

    ``age_seconds`` sets ``created_at`` (creation age). By default the session's
    last-activity timestamps (``updated_at``/``started_at``) mirror
    ``created_at`` — i.e. a session that was created and never advanced a stage,
    which is the genuinely-dead-orphan shape.

    Pass ``last_activity_seconds`` to model a LIVE pipeline that was created long
    ago but recently refreshed ``updated_at`` via a stage_states write (#1676):
    ``created_at`` stays at ``age_seconds`` while ``updated_at`` is set to the
    fresher ``last_activity_seconds``.
    """
    s = MagicMock()
    s.session_id = session_id
    s.session_type = session_type
    s.status = "running"
    s.last_heartbeat_at = heartbeat
    s.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    activity_age = age_seconds if last_activity_seconds is None else last_activity_seconds
    s.updated_at = datetime.now(UTC) - timedelta(seconds=activity_age)
    s.started_at = s.updated_at
    s.issue_url = None
    return s


class TestKillOrphans:
    """Tests for the --kill-orphans zombie-cleanup CLI path."""

    def test_dry_run_lists_without_modifying(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9991", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["killed"] is False
        assert result["orphans"][0]["session_id"] == "sdlc-local-9991"

    def test_real_run_finalizes_orphans_via_finalize_session(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9992", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]

        finalize_mock = MagicMock()
        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.finalize_session", finalize_mock),
        ):
            result = _kill_orphans(dry_run=False)

        assert result["count"] == 1
        assert result["killed"] is True
        assert result["failures"] == 0
        assert result["results"][0] == {
            "session_id": "sdlc-local-9992",
            "result": "killed",
        }
        # Verify finalize_session was called with correct args (not transition_status).
        finalize_mock.assert_called_once()
        _args, kwargs = finalize_mock.call_args
        assert kwargs["reason"] == "zombie sdlc-local session cleanup"
        assert kwargs["skip_auto_tag"] is True
        assert kwargs["skip_checkpoint"] is True
        assert kwargs["skip_parent"] is True

    def test_finalize_session_failure_does_not_crash(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        orphan = _make_orphan_session("sdlc-local-9993", ORPHAN_AGE_SECONDS + 60)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [orphan]

        with (
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.finalize_session",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = _kill_orphans(dry_run=False)

        assert result["count"] == 1
        assert result["failures"] == 1
        assert result["results"][0]["result"] == "failed"
        assert "boom" in result["results"][0]["error"]

    def test_newer_than_threshold_not_listed(self):
        from tools.sdlc_session_ensure import _kill_orphans

        fresh = _make_orphan_session("sdlc-local-9994", 60)  # 1 minute old
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [fresh]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0
        assert result["orphans"] == []

    def test_session_with_heartbeat_never_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        old_but_alive = _make_orphan_session(
            "sdlc-local-9995",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=datetime.now(UTC),
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [old_but_alive]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_boundary_at_threshold_is_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        at_boundary = _make_orphan_session("sdlc-local-9996", ORPHAN_AGE_SECONDS)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [at_boundary]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        # At-threshold means age >= threshold is True.
        assert result["count"] == 1

    def test_boundary_one_second_under_not_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        under = _make_orphan_session("sdlc-local-9997", ORPHAN_AGE_SECONDS - 1)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [under]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_boundary_one_second_over_is_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        over = _make_orphan_session("sdlc-local-9998", ORPHAN_AGE_SECONDS + 1)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [over]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1

    def test_non_sdlc_local_session_never_listed(self):
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # A bridge session matching all other zombie criteria must be skipped.
        bridge = _make_orphan_session("tg_valor_-1003449100931_691", ORPHAN_AGE_SECONDS + 3600)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_live_local_pipeline_with_fresh_updated_at_not_listed(self):
        """#1676: a worker-less sdlc-local-N PM session with last_heartbeat_at=None
        but a FRESH updated_at (advanced a stage recently) must NOT be reaped.

        This is the core defect: on a skills-only machine no worker writes a
        heartbeat, so a live /do-sdlc pipeline matched the old zombie criteria
        after 10 minutes and --kill-orphans destroyed its stage_states mid-run.
        The fix exempts it because every stage_states write refreshes updated_at.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # Created 1 hour ago (well past threshold), but advanced a stage 30s ago.
        live = _make_orphan_session(
            "sdlc-local-1676",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
            last_activity_seconds=30,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [live]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0
        assert result["orphans"] == []

    def test_stale_local_pipeline_no_heartbeat_still_listed(self):
        """#1676: a worker-less sdlc-local-N PM session with last_heartbeat_at=None
        AND a stale updated_at (no stage advanced for the full window) is still a
        genuine zombie and MUST be reaped — preserving original dead-orphan
        behavior for sessions that truly stalled.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        # Created AND last-active well past the threshold.
        stale = _make_orphan_session(
            "sdlc-local-1677",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
            last_activity_seconds=ORPHAN_AGE_SECONDS + 600,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [stale]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["orphans"][0]["session_id"] == "sdlc-local-1677"

    def test_fresh_updated_at_exempts_even_at_creation_boundary(self):
        """#1676: updated_at just under the threshold exempts a session whose
        created_at is exactly at the threshold — last activity, not creation, is
        the liveness clock.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1678",
            age_seconds=ORPHAN_AGE_SECONDS,
            heartbeat=None,
            last_activity_seconds=ORPHAN_AGE_SECONDS - 1,
        )
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_falls_back_to_started_at_when_updated_at_missing(self):
        """#1676: when updated_at is None, _last_activity_at falls back to
        started_at. A fresh started_at exempts an old-created_at session.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1679",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
        )
        s.updated_at = None
        s.started_at = datetime.now(UTC) - timedelta(seconds=30)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 0

    def test_falls_back_to_created_at_when_no_activity_timestamps(self):
        """#1676: when both updated_at and started_at are None, the reaper falls
        back to created_at — an old, never-advanced session is still a zombie.
        """
        from tools.sdlc_session_ensure import ORPHAN_AGE_SECONDS, _kill_orphans

        s = _make_orphan_session(
            "sdlc-local-1680",
            age_seconds=ORPHAN_AGE_SECONDS + 3600,
            heartbeat=None,
        )
        s.updated_at = None
        s.started_at = None
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _kill_orphans(dry_run=True)

        assert result["count"] == 1
        assert result["orphans"][0]["session_id"] == "sdlc-local-1680"

    def test_cli_dry_run_exits_zero_with_valid_json(self):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_session_ensure",
                "--kill-orphans",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
        )
        assert result.returncode == 0
        # stdout must be parseable JSON
        payload = json.loads(result.stdout)
        assert "count" in payload
        assert payload["killed"] is False

    def test_cli_rejects_issue_number_with_kill_orphans(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_session_ensure",
                "--kill-orphans",
                "--issue-number",
                "1",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        # argparse .error() exits 2
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr.lower()


class TestCreateLocalMessageText:
    """Fix A (#1741): create_local receives a non-empty, issue-anchored message_text.

    Without Fix A, ``message_text`` was not passed to ``create_local``, so the
    AgentSession was created with ``message_text=None``. The executor then built
    the PTY container's first turn as "MESSAGE: None", which primed the granite
    PM with a phantom task and triggered a silent [/complete] no-op.

    These tests assert that ``create_local`` is always called with:
    - ``message_text`` kwarg present and non-empty
    - the text references the issue number (issue-anchored)
    - when ``issue_url`` is supplied, it is also embedded in the text
    """

    def test_create_local_receives_message_text(self):
        """create_local is called with a non-empty message_text kwarg."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1741"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1741)

        assert result["session_id"] == "sdlc-local-1741"
        assert result["created"] is True
        mock_as.create_local.assert_called_once()
        _, kwargs = mock_as.create_local.call_args
        assert "message_text" in kwargs, "create_local was not called with message_text kwarg"
        assert kwargs["message_text"], "message_text must be non-empty"

    def test_message_text_is_issue_anchored(self):
        """message_text references the issue number so the PM has a real goal anchor."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1742"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1742)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs["message_text"]
        # Must reference the issue number so the PM can find the work to do.
        assert "1742" in msg, f"message_text must reference issue number 1742; got: {msg!r}"

    def test_message_text_embeds_issue_url_when_provided(self):
        """When issue_url is supplied, it is embedded in message_text."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1743"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        issue_url = "https://github.com/tomcounsell/ai/issues/1743"

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1743, issue_url=issue_url)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs["message_text"]
        assert issue_url in msg, (
            f"message_text must embed the issue_url when supplied; got: {msg!r}"
        )

    def test_message_text_present_without_issue_url(self):
        """message_text is non-empty even when no issue_url is supplied."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1744"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1744)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs.get("message_text", "")
        assert msg and msg.strip(), "message_text must be non-empty even without issue_url"
        assert "1744" in msg


class TestIssueLockWiring:
    """Issues #1954/#2003: every return point of ensure_session() -- the 4
    early-return branches (env-owns-issue, env-diverges-but-issue-owned,
    find_session_by_issue match, idempotent existing_by_id match) plus the
    final create-and-claim path -- goes through one shared helper that mints
    a FRESH run_id candidate, contests the issue lock, and binds the winner
    to the session record. No branch can skip the contest, and there is NO
    adopt-from-record branch.
    """

    @staticmethod
    def _lock_result(acquired: bool, owner_session_id=None, owner_run_id=None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(
            acquired=acquired,
            owner_session_id=owner_session_id,
            owner_run_id=owner_run_id,
        )

    @staticmethod
    def _readback_as(session):
        """Mock AgentSession whose readback query returns the bound session."""
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        return mock_as

    def test_mint_on_env_owns_issue_return(self, monkeypatch):
        """Return point 1: env session owns the issue (true no-op short-circuit)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_valor_-100_691")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        bridge_session = MagicMock()
        bridge_session.session_id = "tg_valor_-100_691"
        bridge_session.session_type = "eng"
        bridge_session.status = "running"
        bridge_session.issue_url = "https://github.com/tomcounsell/ai/issues/2001"

        lock_mock = MagicMock(return_value=self._lock_result(True, "tg_valor_-100_691"))

        with (
            patch("tools._sdlc_utils.find_session", return_value=bridge_session),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(bridge_session)),
        ):
            result = ensure_session(issue_number=2001)

        assert result["session_id"] == "tg_valor_-100_691"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2001
        # A FRESH uuid-hex candidate is minted per top-level call and emitted.
        assert isinstance(args[1], str) and len(args[1]) == 32
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "tg_valor_-100_691"
        assert bridge_session.active_run_id == args[1]

    def test_mint_on_env_diverges_but_issue_owned_return(self, monkeypatch):
        """Return point 2: env session diverges; an existing issue-scoped
        session is preferred (C1, #1671)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-other-issue")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock()
        env_session.session_id = "parent-pm-other-issue"
        env_session.session_type = "eng"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-2002"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2002"))

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(issue_session)),
        ):
            result = ensure_session(issue_number=2002)

        assert result["session_id"] == "sdlc-local-2002"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2002
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "sdlc-local-2002"

    def test_mint_on_find_session_by_issue_match_return(self):
        """Return point 3: the main issue-based lookup (no env var)."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2003"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2003"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("models.agent_session.AgentSession", self._readback_as(existing)),
        ):
            result = ensure_session(issue_number=2003)

        assert result["session_id"] == "sdlc-local-2003"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, kwargs = lock_mock.call_args
        assert args[0] == 2003
        assert result["run_id"] == args[1]
        assert kwargs.get("session_id") == "sdlc-local-2003"

    def test_mint_on_idempotent_existing_by_id_return(self):
        """Return point 4: a session with sdlc-local-{N} already exists (no
        find_session_by_issue hit, matched by deterministic id instead)."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2004"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [existing]

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2004"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2004)

        assert result["session_id"] == "sdlc-local-2004"
        assert result["created"] is False
        lock_mock.assert_called_once()
        args, _ = lock_mock.call_args
        assert args[0] == 2004
        assert result["run_id"] == args[1]

    def test_mint_on_create_and_claim_return(self):
        """Return point 5: the final create-and-claim path (cold start)."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2005"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2005"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2005)

        assert result["session_id"] == "sdlc-local-2005"
        assert result["created"] is True
        lock_mock.assert_called_once()
        args, _ = lock_mock.call_args
        assert args[0] == 2005
        assert result["run_id"] == args[1]
        # issue_number is written ONCE, only on this creation path.
        _, kwargs = mock_as.create_local.call_args
        assert kwargs.get("issue_number") == 2005

    def test_acquire_run_lock_and_bind_pins_target_repo_from_resolver(self):
        """Issue #2012: target_repo is resolved exactly once, in
        _acquire_run_lock_and_bind, and passed through to every
        touch_issue_lock call -- never re-resolved per write downstream."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2006"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2006"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools._sdlc_utils._resolve_target_repo", return_value="tomcounsell/ai"),
        ):
            ensure_session(issue_number=2006)

        lock_mock.assert_called_once()
        _args, kwargs = lock_mock.call_args
        assert kwargs.get("target_repo") == "tomcounsell/ai"

    def test_acquire_run_lock_and_bind_passes_through_none_target_repo(self):
        """A resolver miss (None) must not block lock acquisition -- it is
        passed through as-is; downstream degradation is the ledger writer's
        job, not this function's."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2007"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2007"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
            patch("tools._sdlc_utils._resolve_target_repo", return_value=None),
        ):
            result = ensure_session(issue_number=2007)

        assert result["session_id"] == "sdlc-local-2007"
        lock_mock.assert_called_once()
        _args, kwargs = lock_mock.call_args
        assert kwargs.get("target_repo") is None

    def test_issue_number_not_rewritten_on_continuing_session_returns(self):
        """The 4 early-return (continuing-session) branches must NEVER write
        issue_number -- it is a write-once mirror field set only at creation."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2006"

        mock_as = self._readback_as(existing)  # create_local must never be called.

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(True, "sdlc-local-2006"),
            ),
        ):
            result = ensure_session(issue_number=2006)

        assert result["session_id"] == "sdlc-local-2006"
        assert result["created"] is False
        mock_as.create_local.assert_not_called()

    def test_blocked_shape_includes_owning_run_id(self):
        """When touch_issue_lock() reports a foreign live holder,
        ensure_session() propagates ISSUE_LOCKED with BOTH the owning run_id
        and session_id -- never silently returning the session."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2007"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(
                    False,
                    owner_session_id="sdlc-local-2007-other-owner",
                    owner_run_id="foreign-run-hex",
                ),
            ),
        ):
            result = ensure_session(issue_number=2007)

        assert result == {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_run_id": "foreign-run-hex",
            "owner_session_id": "sdlc-local-2007-other-owner",
            # Cycle-3 nit: the refusal carries the orphan signal (from the
            # follow-up peek; the mocked lock reports not-orphaned).
            "orphaned_lock": False,
        }

    def test_second_bare_ensure_under_live_signal_inherits_run_id(self):
        """WS1 (#2026) supersedes the old #2003 ISSUE_LOCKED-on-second-bare-
        ensure behavior: Call A mints run_id_a, acquires the lease, AND writes
        the supervised-run signal. A second BARE ensure now finds the LIVE
        signal and returns the named SUPERVISED_RUN_ACTIVE refusal carrying
        run_id_a to inherit -- it mints NOTHING (no fresh candidate, no
        adoption from the record). Exercises the REAL touch_issue_lock() and
        the real signal against the test Redis db."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2050
        local_session_id = f"sdlc-local-{issue_number}"

        session = MagicMock()
        session.session_id = local_session_id
        session.working_dir = None  # anchor session: no slug worktree file

        # Call A: fresh key, must acquire and bind its run_id + write the signal.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)

        assert result_a["created"] is False
        run_id_a = result_a["run_id"]
        assert run_id_a
        assert session.active_run_id == run_id_a

        # Call B: a bare ensure under the live supervised-run signal inherits
        # run_id_a via SUPERVISED_RUN_ACTIVE and mints nothing.
        session_b = MagicMock()
        session_b.session_id = local_session_id
        session_b.active_run_id = None
        session_b.working_dir = None
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session_b),
            patch("models.agent_session.AgentSession", self._readback_as(session_b)),
        ):
            result_b = ensure_session(issue_number=issue_number)

        assert result_b["blocked"] is True
        assert result_b["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert result_b["run_id"] == run_id_a
        assert result_b["owner_run_id"] == run_id_a
        # No fresh mint and no adoption onto the second session's record.
        assert result_b.get("created") is None
        assert session_b.active_run_id is None

    def test_save_failure_releases_lock_next_caller_acquires_immediately(self):
        """Race 3 (cycle-2 CONCERN 2): a save failure after lock acquire
        releases the lock via COMPARE-AND-DELETE -- the next caller acquires
        immediately instead of waiting out the 300s TTL. Real Redis."""
        import popoto.redis_db as rdb

        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2051

        broken = MagicMock()
        broken.session_id = f"sdlc-local-{issue_number}"
        broken.save.side_effect = RuntimeError("redis save exploded")

        with patch("tools._sdlc_utils.find_session_by_issue", return_value=broken):
            result = ensure_session(issue_number=issue_number)

        assert result.get("error") == "RUN_BIND_FAILED"
        # Lock released: key gone from the test Redis db.
        assert rdb.POPOTO_REDIS_DB.get(f"session:issuelock:{issue_number}") is None

        # Next caller acquires immediately -- no 300s wedge.
        healthy = MagicMock()
        healthy.session_id = f"sdlc-local-{issue_number}"
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=healthy),
            patch("models.agent_session.AgentSession", self._readback_as(healthy)),
        ):
            result2 = ensure_session(issue_number=issue_number)

        assert result2["run_id"]
        assert result2["session_id"] == f"sdlc-local-{issue_number}"

    def test_readback_mismatch_releases_lock(self):
        """Post-save readback mismatch (the record does not carry the lock's
        run_id) releases the lock and surfaces the error. Real Redis."""
        import popoto.redis_db as rdb

        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2052

        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"

        stale = MagicMock()
        stale.session_id = f"sdlc-local-{issue_number}"
        stale.active_run_id = "some-other-run-entirely"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [stale]  # readback sees a stale value

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=issue_number)

        assert result.get("error") == "RUN_BIND_FAILED"
        assert rdb.POPOTO_REDIS_DB.get(f"session:issuelock:{issue_number}") is None

    def test_orphaned_lock_flagged_on_peek(self):
        """A lock whose run_id matches no live session's active_run_id is
        reported orphaned_lock=True by the peek path. Real Redis lock; the
        live-session scan sees an empty test db."""
        from models.session_lifecycle import touch_issue_lock

        issue_number = 2053
        acquired = touch_issue_lock(issue_number, "ghost-run", session_id="sdlc-local-2053")
        assert acquired.acquired is True

        peek = touch_issue_lock(issue_number, None, session_id="sdlc-local-2053", peek=True)
        assert peek.acquired is False
        assert peek.owner_run_id == "ghost-run"
        assert peek.orphaned_lock is True

    def test_legacy_record_without_active_run_id_never_crashes(self):
        """A legacy session record with no active_run_id (pre-#2003 rows)
        contests the lock normally -- reads never crash on the missing
        field, and the fresh mint binds onto it."""
        from tools.sdlc_session_ensure import ensure_session

        legacy = MagicMock()
        legacy.session_id = "sdlc-local-2054"
        legacy.active_run_id = None  # legacy row: field absent/None

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=legacy),
            patch("models.agent_session.AgentSession", self._readback_as(legacy)),
        ):
            result = ensure_session(issue_number=2054)

        assert result["session_id"] == "sdlc-local-2054"
        assert result["run_id"]
        assert legacy.active_run_id == result["run_id"]


class TestVerifiedRunIdReuse:
    """#2003 cycle-3 BLOCKER 1: the per-stage /sdlc router re-runs
    session-ensure at every stage boundary while its OWN prior stage's lock
    is still live (the stage's completion marker renews it to the full TTL).
    A bare re-ensure mints a fresh candidate, loses SET NX to itself, and
    self-wedges the pipeline. --reuse-run-id is the escape: a claim the
    caller already carries is verified against the live lock (owner match)
    or, on a free lock, against the record mirror -- and only then honored.
    No-adopt stays intact for foreign/stale claims.
    """

    @staticmethod
    def _readback_as(session):
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        return mock_as

    def test_consecutive_stage_reuse_survives_own_live_lock(self):
        """The judge-mandated regression: ensure -> stage-completion renewal
        -> second ensure WITHIN the TTL. With --reuse-run-id the second
        ensure returns the SAME run_id instead of wedging on ISSUE_LOCKED.
        Real Redis lock throughout."""
        from tools._sdlc_utils import renew_issue_lock_for_session
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2060
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.issue_number = issue_number

        # Stage N: first ensure mints run_id A.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)
        run_id_a = result_a["run_id"]
        assert run_id_a

        # Stage N's final `stage-marker --status completed` renews the lock
        # to the full TTL (the exact write_marker side effect).
        renew_issue_lock_for_session(session, run_id=run_id_a)

        # Stage N+1: the router re-ensures seconds later, carrying the
        # conversation's run_id. Must NOT wedge; must return the same id.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_b = ensure_session(issue_number=issue_number, reuse_run_id=run_id_a)

        assert result_b.get("blocked") is None, result_b
        assert result_b["run_id"] == run_id_a
        assert result_b["session_id"] == f"sdlc-local-{issue_number}"

    def test_reuse_with_wrong_id_against_live_lock_still_blocked(self):
        """An unverifiable claim while a foreign lock is live falls through
        to the fresh-mint contest and stays ISSUE_LOCKED (no adopt)."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2061
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)
        run_id_a = result_a["run_id"]

        intruder = MagicMock()
        intruder.session_id = f"sdlc-local-{issue_number}"
        intruder.active_run_id = None
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=intruder),
            patch("models.agent_session.AgentSession", self._readback_as(intruder)),
        ):
            result_b = ensure_session(issue_number=issue_number, reuse_run_id="bogus-claim")

        assert result_b["blocked"] is True
        assert result_b["reason"] == "ISSUE_LOCKED"
        assert result_b["owner_run_id"] == run_id_a
        assert "orphaned_lock" in result_b

    def test_reuse_on_free_lock_with_record_match_reacquires_same_id(self):
        """TTL lapsed but the record mirror corroborates the claim: the
        ensure re-acquires under the SAME run_id (lossless recovery)."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2062
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.active_run_id = "aabbccdd" * 4  # prior mint, mirrored on the record

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result = ensure_session(issue_number=issue_number, reuse_run_id="aabbccdd" * 4)

        assert result["run_id"] == "aabbccdd" * 4

    def test_reuse_on_free_lock_with_record_mismatch_mints_fresh(self):
        """A claim the record does NOT corroborate is ignored on a free
        lock: fresh mint, never claim-echo."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2063
        session = MagicMock()
        session.session_id = f"sdlc-local-{issue_number}"
        session.active_run_id = "11112222" * 4

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result = ensure_session(issue_number=issue_number, reuse_run_id="deadbeef" * 4)

        assert result["run_id"] != "deadbeef" * 4
        assert len(result["run_id"]) == 32


class TestSupervisedRunSignal:
    """WS1 (#2026): the supervised-run signal drives fork inheritance.

    A bare ``session-ensure`` under a LIVE supervised-run signal returns the
    named ``SUPERVISED_RUN_ACTIVE`` refusal (carrying the supervisor's run_id)
    and mints NOTHING. A stale/expired signal falls back to normal standalone
    mint semantics. Enforcement lives in the tool, not prose (Risk 3).
    """

    @staticmethod
    def _readback_as(session):
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [session]
        return mock_as

    def test_bare_ensure_under_live_signal_refuses_and_mints_nothing(self):
        """A live signal short-circuits the bare ensure to SUPERVISED_RUN_ACTIVE
        before any lock contest or mint."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2070"
        session.working_dir = None

        live = SupervisedRunStatus(True, "supervisor-run-abc", "sdlc-local-2070")

        lock_mock = MagicMock()
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=live),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2070)

        assert result["blocked"] is True
        assert result["reason"] == "SUPERVISED_RUN_ACTIVE"
        assert result["run_id"] == "supervisor-run-abc"
        assert result["owner_run_id"] == "supervisor-run-abc"
        assert result.get("created") is None
        # Mints nothing: the lock is never contested.
        lock_mock.assert_not_called()
        # No run_id bound onto the record.
        assert getattr(session, "active_run_id", None) in (None,) or not isinstance(
            session.active_run_id, str
        )

    def test_bare_ensure_under_stale_signal_falls_back_to_standalone(self):
        """A stale/expired signal (not live) never refuses -- the bare ensure
        mints fresh via the normal lock contest."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2071"
        session.working_dir = None

        stale = SupervisedRunStatus(False, "dead-run", None)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", return_value=stale),
        ):
            result = ensure_session(issue_number=2071)

        assert result.get("blocked") is None
        assert result["run_id"]
        assert len(result["run_id"]) == 32
        assert session.active_run_id == result["run_id"]

    def test_reuse_ensure_is_exempt_from_signal_refusal(self):
        """A --reuse-run-id ensure is the supervisor's own consecutive-stage
        re-ensure: it skips the signal refusal entirely (verified against the
        live lock further down instead)."""
        from agent.supervised_run import SupervisedRunStatus
        from tools.sdlc_session_ensure import ensure_session

        session = MagicMock()
        session.session_id = "sdlc-local-2072"
        session.working_dir = None
        session.active_run_id = "aa11bb22" * 4

        live = SupervisedRunStatus(True, "aa11bb22" * 4, "sdlc-local-2072")
        status_mock = MagicMock(return_value=live)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
            patch("agent.supervised_run.supervised_run_status", status_mock),
        ):
            result = ensure_session(issue_number=2072, reuse_run_id="aa11bb22" * 4)

        # The reuse path never consults the supervised-run signal.
        status_mock.assert_not_called()
        assert result.get("reason") != "SUPERVISED_RUN_ACTIVE"


class TestSupervisedRunModule:
    """Direct tests of agent.supervised_run against the test Redis db."""

    def test_status_live_when_lock_held_by_signal_run_id(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import touch_issue_lock

        issue_number = 2080
        run_id = "runsig-2080"
        # Supervisor holds the lease under run_id, then publishes the signal.
        assert touch_issue_lock(issue_number, run_id, session_id="s").acquired is True
        write_supervised_run_signal(issue_number, run_id, session_id="s")

        status = supervised_run_status(issue_number)
        assert status.live is True
        assert status.run_id == run_id

    def test_status_stale_when_lock_released(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import release_issue_lock, touch_issue_lock

        issue_number = 2081
        run_id = "runsig-2081"
        touch_issue_lock(issue_number, run_id, session_id="s")
        write_supervised_run_signal(issue_number, run_id, session_id="s")
        # Supervisor releases the lease at run end: the signal goes stale even
        # though its key may still exist until its own TTL lapses.
        release_issue_lock(issue_number, run_id)

        status = supervised_run_status(issue_number)
        assert status.live is False

    def test_status_stale_when_lock_owned_by_different_run(self):
        from agent.supervised_run import (
            supervised_run_status,
            write_supervised_run_signal,
        )
        from models.session_lifecycle import touch_issue_lock

        issue_number = 2082
        # A stale signal names run A, but the lock is now held by run B.
        write_supervised_run_signal(issue_number, "old-run-A", session_id="s")
        touch_issue_lock(issue_number, "new-run-B", session_id="s")

        status = supervised_run_status(issue_number)
        assert status.live is False

    def test_no_signal_returns_not_live(self):
        from agent.supervised_run import supervised_run_status

        status = supervised_run_status(2083)
        assert status.live is False
        assert status.run_id is None

    def test_clear_signal_is_compare_and_delete(self):
        from agent.supervised_run import (
            clear_supervised_run_signal,
            read_supervised_run_signal,
            write_supervised_run_signal,
        )

        issue_number = 2084
        write_supervised_run_signal(issue_number, "owner-run", session_id="s")
        # A foreign run_id must not clear the signal.
        clear_supervised_run_signal(issue_number, "foreign-run")
        assert read_supervised_run_signal(issue_number) is not None
        # The owner clears it.
        clear_supervised_run_signal(issue_number, "owner-run")
        assert read_supervised_run_signal(issue_number) is None

    def test_operations_fail_open_on_redis_error(self):
        """Every op degrades to a safe default (never raises) on Redis error."""
        from agent.supervised_run import (
            read_supervised_run_signal,
            supervised_run_status,
            write_supervised_run_signal,
        )

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.get.side_effect = RuntimeError("redis down")
            mock_redis.set.side_effect = RuntimeError("redis down")
            # None of these raise.
            write_supervised_run_signal(2085, "run", session_id="s")
            assert read_supervised_run_signal(2085) is None
            assert supervised_run_status(2085).live is False
