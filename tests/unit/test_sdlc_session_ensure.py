"""Unit tests for tools.sdlc_session_ensure.

Tests cover:
- Creates session when none exists
- Returns existing session (idempotent)
- Handles Redis errors gracefully
- CLI output format
- Invalid input handling
- Env-var short-circuit for bridge-initiated sessions
- --kill-orphans zombie cleanup
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

        with patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session):
            result = ensure_session(issue_number=941)

        assert result == {"session_id": "sdlc-local-941", "created": False}

    def test_creates_new_session(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-942"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
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

        assert result == {"session_id": "sdlc-local-942", "created": True}
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

        assert result == {"session_id": "sdlc-local-943", "created": False}

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
        mock_as.query.filter.return_value = []
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

        assert result == {"session_id": "sdlc-local-944", "created": True}

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

    def test_short_circuit_returns_env_session_when_live_pm(self, monkeypatch):
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

        with (
            patch("tools._sdlc_utils.find_session", return_value=bridge_session),
            patch("tools._sdlc_utils.find_session_by_issue", fsbi),
        ):
            result = ensure_session(issue_number=1140)

        assert result == {
            "session_id": "tg_valor_-1003449100931_691",
            "created": False,
        }
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
        env_session.session_type = "pm"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        # The issue-scoped session that actually owns issue 1171.
        issue_session = MagicMock()
        issue_session.session_id = "sdlc-local-1171"

        mock_as = MagicMock()  # create_local must NOT be called (no duplicate).

        with (
            patch("tools._sdlc_utils.find_session", return_value=env_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=1171)

        assert result == {"session_id": "sdlc-local-1171", "created": False}
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
        env_session.session_type = "pm"
        env_session.status = "running"
        env_session.issue_url = "https://github.com/tomcounsell/ai/issues/9999"

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1172"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
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
        assert result == {"session_id": "sdlc-local-1172", "created": True}

    def test_short_circuit_falls_through_when_env_session_missing(self, monkeypatch):
        """Env var set but no live session — fall through to legacy create path."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "stale_session_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1141"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=None),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1141)

        assert result == {"session_id": "sdlc-local-1141", "created": True}

    def test_empty_env_var_does_not_short_circuit(self, monkeypatch):
        """Empty-string env var behaves identically to unset."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1142"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        find_session_mock = MagicMock()
        with (
            patch("tools._sdlc_utils.find_session", find_session_mock),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1142)

        assert result == {"session_id": "sdlc-local-1142", "created": True}
        # find_session should NOT be called when env vars are empty
        find_session_mock.assert_not_called()

    def test_short_circuit_falls_through_for_non_pm_session(self, monkeypatch):
        """Env var points at a Dev session — short-circuit must NOT activate (C2)."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "dev_session_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        dev_session = MagicMock()
        dev_session.session_id = "dev_session_id"
        dev_session.session_type = "dev"
        dev_session.status = "running"

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1143"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session", return_value=dev_session),
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1143)

        # Must NOT return the dev session id; must fall through to create.
        assert result == {"session_id": "sdlc-local-1143", "created": True}

    def test_short_circuit_falls_through_for_terminal_status_pm_session(self, monkeypatch):
        """Env points at a terminal-status PM session (AD1) — fall through."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "completed_pm_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        for terminal_status in (
            "completed",
            "failed",
            "killed",
            "abandoned",
            "cancelled",
        ):
            terminal_session = MagicMock()
            terminal_session.session_id = "completed_pm_id"
            terminal_session.session_type = "pm"
            terminal_session.status = terminal_status

            mock_new_session = MagicMock()
            mock_new_session.session_id = "sdlc-local-1144"

            mock_as = MagicMock()
            mock_as.query.filter.return_value = []
            mock_as.create_local.return_value = mock_new_session

            with (
                patch("tools._sdlc_utils.find_session", return_value=terminal_session),
                patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
                patch("models.agent_session.AgentSession", mock_as),
                patch("models.session_lifecycle.transition_status"),
            ):
                result = ensure_session(issue_number=1144)

            # Must fall through and create a fresh session; not reuse the
            # terminal-status bridge session.
            assert result == {
                "session_id": "sdlc-local-1144",
                "created": True,
            }, f"failed for terminal status {terminal_status!r}"

    def test_short_circuit_degrades_on_find_session_error(self, monkeypatch):
        """Redis error during env lookup falls through without crashing."""
        from tools.sdlc_session_ensure import ensure_session

        monkeypatch.setenv("VALOR_SESSION_ID", "some_id")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1145"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
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
        assert result == {"session_id": "sdlc-local-1145", "created": True}


def _make_orphan_session(
    session_id,
    age_seconds,
    heartbeat=None,
    session_type="pm",
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
