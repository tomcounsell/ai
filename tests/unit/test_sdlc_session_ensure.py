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
        env_session.session_type = "eng"
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
        env_session.session_type = "eng"
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

    def test_short_circuit_falls_through_for_terminal_status_eng_session(self, monkeypatch):
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
            terminal_session.session_type = "eng"
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
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1741)

        assert result == {"session_id": "sdlc-local-1741", "created": True}
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
    """Issue #1954: touch_issue_lock() must be called from ALL FIVE return
    points of ensure_session() -- the 4 early-return branches (env-owns-issue,
    env-diverges-but-issue-owned, find_session_by_issue match, idempotent
    existing_by_id match) plus the final create-and-claim path -- via one
    shared local helper, so no branch can skip it.
    """

    @staticmethod
    def _lock_result(acquired: bool, owner_session_id: str | None = None):
        from models.session_lifecycle import IssueLockResult

        return IssueLockResult(acquired=acquired, owner_session_id=owner_session_id)

    def test_touch_lock_on_env_owns_issue_return(self, monkeypatch):
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
        ):
            result = ensure_session(issue_number=2001)

        assert result == {"session_id": "tg_valor_-100_691", "created": False}
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 2001
        assert args[1] == "tg_valor_-100_691"

    def test_touch_lock_on_env_diverges_but_issue_owned_return(self, monkeypatch):
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
        ):
            result = ensure_session(issue_number=2002)

        assert result == {"session_id": "sdlc-local-2002", "created": False}
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 2002
        assert args[1] == "sdlc-local-2002"

    def test_touch_lock_on_find_session_by_issue_match_return(self):
        """Return point 3: the main issue-based lookup (no env var)."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2003"

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2003"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2003)

        assert result == {"session_id": "sdlc-local-2003", "created": False}
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 2003
        assert args[1] == "sdlc-local-2003"

    def test_touch_lock_on_idempotent_existing_by_id_return(self):
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

        assert result == {"session_id": "sdlc-local-2004", "created": False}
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 2004
        assert args[1] == "sdlc-local-2004"

    def test_touch_lock_on_create_and_claim_return(self):
        """Return point 5: the final create-and-claim path (cold start)."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-2005"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        mock_as.create_local.return_value = mock_new_session

        lock_mock = MagicMock(return_value=self._lock_result(True, "sdlc-local-2005"))

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
            patch("models.session_lifecycle.touch_issue_lock", lock_mock),
        ):
            result = ensure_session(issue_number=2005)

        assert result == {"session_id": "sdlc-local-2005", "created": True}
        lock_mock.assert_called_once()
        args = lock_mock.call_args.args
        assert args[0] == 2005
        assert args[1] == "sdlc-local-2005"
        # issue_number is written ONCE, only on this creation path.
        _, kwargs = mock_as.create_local.call_args
        assert kwargs.get("issue_number") == 2005

    def test_issue_number_not_rewritten_on_continuing_session_returns(self):
        """The 4 early-return (continuing-session) branches must NEVER write
        issue_number -- it is a write-once mirror field set only at creation."""
        from tools.sdlc_session_ensure import ensure_session

        existing = MagicMock()
        existing.session_id = "sdlc-local-2006"

        mock_as = MagicMock()  # create_local must never be called on this path.

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=existing),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.touch_issue_lock",
                return_value=self._lock_result(True, "sdlc-local-2006"),
            ),
        ):
            result = ensure_session(issue_number=2006)

        assert result == {"session_id": "sdlc-local-2006", "created": False}
        mock_as.create_local.assert_not_called()

    def test_blocked_shape_returned_when_lock_held_by_different_session(self):
        """When touch_issue_lock() reports contention, ensure_session() must
        propagate the blocked signal rather than silently returning the
        session as if nothing happened."""
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
                return_value=self._lock_result(False, "sdlc-local-2007-other-owner"),
            ),
        ):
            result = ensure_session(issue_number=2007)

        assert result == {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_session_id": "sdlc-local-2007-other-owner",
        }

    def test_two_processes_distinct_holder_tokens_detect_contention(self, monkeypatch):
        """The round-2 critique regression case: two independently-resolved
        ensure_session() calls for the SAME issue -- simulating two different
        OS processes via distinct holder_tokens -- both resolve the identical
        deterministic sdlc-local-{N} session_id. Ownership must be compared by
        holder_token, not session_id, so the second call is correctly blocked
        rather than both succeeding. Exercises the REAL touch_issue_lock()
        against the test Redis db (no mocking of the lock itself).
        """
        import models.session_lifecycle as session_lifecycle
        from tools.sdlc_session_ensure import ensure_session

        issue_number = 2050
        local_session_id = f"sdlc-local-{issue_number}"

        mock_new_session = MagicMock()
        mock_new_session.session_id = local_session_id

        # Simulated Process A: fresh key, must acquire.
        monkeypatch.setattr(session_lifecycle, "_process_holder_token", lambda: "process-A-token")
        mock_as_a = MagicMock()
        mock_as_a.query.filter.return_value = []
        mock_as_a.create_local.return_value = mock_new_session
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as_a),
            patch("models.session_lifecycle.transition_status"),
        ):
            result_a = ensure_session(issue_number=issue_number)

        assert result_a == {"session_id": local_session_id, "created": True}

        # Simulated Process B: distinct holder_token, same deterministic
        # session_id -- must be blocked, not silently succeed.
        monkeypatch.setattr(session_lifecycle, "_process_holder_token", lambda: "process-B-token")
        mock_as_b = MagicMock()
        mock_as_b.query.filter.return_value = []
        mock_as_b.create_local.return_value = mock_new_session
        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as_b),
            patch("models.session_lifecycle.transition_status"),
        ):
            result_b = ensure_session(issue_number=issue_number)

        assert result_b == {
            "blocked": True,
            "reason": "ISSUE_LOCKED",
            "owner_session_id": local_session_id,
        }
