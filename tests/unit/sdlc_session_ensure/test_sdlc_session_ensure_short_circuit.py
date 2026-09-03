"""Unit tests for tools.sdlc_session_ensure: env-var session short-circuit (#2879)."""

from unittest.mock import MagicMock, patch

import pytest


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
        mock_as.query.get.return_value = bridge_session  # post-save readback (primary-key lookup)

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
        mock_as.query.get.return_value = issue_session  # post-save readback (primary-key lookup)

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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
            mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
            # post-save readback (primary-key lookup)
            mock_as.query.get.return_value = mock_new_session
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
        mock_as.query.filter.side_effect = [[]]  # existing_by_id lookup (none)
        mock_as.query.get.return_value = mock_new_session  # post-save readback (primary-key lookup)
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
