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

    def test_no_adopt_from_record_second_call_blocked(self):
        """The #2003 cycle-1 BLOCKER regression: a second top-level
        ensure_session() for the SAME issue while the incumbent's lock is
        LIVE must be ISSUE_LOCKED -- even though the shared session record
        already carries the incumbent's active_run_id. There is NO
        adopt-from-record branch. Exercises the REAL touch_issue_lock()
        against the test Redis db (no mocking of the lock itself)."""
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2050
        local_session_id = f"sdlc-local-{issue_number}"

        session = MagicMock()
        session.session_id = local_session_id

        # Call A: fresh key, must acquire and bind its run_id to the record.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_a = ensure_session(issue_number=issue_number)

        assert result_a["created"] is False
        run_id_a = result_a["run_id"]
        assert run_id_a
        assert session.active_run_id == run_id_a

        # Call B: same record (active_run_id == run_id_a is VISIBLE on it),
        # but the live lock decides -- fresh candidate loses, no adoption.
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=session),
            patch("models.agent_session.AgentSession", self._readback_as(session)),
        ):
            result_b = ensure_session(issue_number=issue_number)

        assert result_b["blocked"] is True
        assert result_b["reason"] == "ISSUE_LOCKED"
        assert result_b["owner_run_id"] == run_id_a
        assert result_b["owner_session_id"] == local_session_id

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
