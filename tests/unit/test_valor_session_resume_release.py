"""Unit tests for cmd_resume() and cmd_release() in tools/valor_session.py.

Coverage:
- cmd_resume: session not found, wrong status (pending/running/failed), happy-path transition
- cmd_resume: abandoned status now resumable (issue #1539)
- cmd_resume: cancelled still rejected
- resume_session: shared core function directly
- cmd_release: no match (no pr_url, no branch match), happy-path by pr_url, happy-path by branch
- model=None on ClaudeAgentOptions: verify model key is absent when not set (avoids SDK override)
"""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import (  # noqa: E402
    ResumeResult,
    _find_session,
    cmd_inspect,
    cmd_kill,
    cmd_release,
    cmd_resume,
    cmd_status,
    cmd_steer,
    resume_session,
)

# Module-level constants needed for mocking session_lifecycle
_RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str,
    status: str = "completed",
    retain: bool = False,
    pr_url: str = "",
    slug: str = "",
    claude_session_uuid: str | None = "uuid-default",
    model: str | None = None,
) -> MagicMock:
    s = MagicMock()
    s.session_id = session_id
    s.status = status
    s.retain_for_resume = retain
    s.pr_url = pr_url
    s.slug = slug
    # Default to a non-null UUID so existing happy-path tests continue to
    # exercise the status-guard path without tripping the null-UUID guard
    # added in issue #1061. Tests that want to exercise the null-UUID path
    # must pass ``claude_session_uuid=None`` explicitly.
    s.claude_session_uuid = claude_session_uuid
    s.model = model
    s.created_at = 0
    return s


def _resume_args(
    session_id: str = "sess-1",
    message: str = "Fix the bug.",
    as_json: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(id=session_id, message=message, json=as_json)


def _release_args(pr: str = "42", as_json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(pr=pr, json=as_json)


# ---------------------------------------------------------------------------
# cmd_resume
# ---------------------------------------------------------------------------


class TestCmdResumeNotFound:
    def test_session_not_found_returns_1(self, capsys):
        """cmd_resume with unknown ID returns 1 and prints error to stderr."""
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        # _find_session falls back to get_by_id when filter is empty (#1061).
        mock_cls.get_by_id.return_value = None

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="missing"))

        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower()


class TestCmdResumeWrongStatus:
    def _run_with_status(self, status: str, capsys) -> tuple[int, str]:
        session = _make_session("sess-1", status=status)
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args())

        return result, capsys.readouterr().err

    def test_pending_returns_1(self, capsys):
        result, err = self._run_with_status("pending", capsys)
        assert result == 1
        assert "pending" in err.lower()

    def test_running_returns_1(self, capsys):
        result, err = self._run_with_status("running", capsys)
        assert result == 1
        assert "running" in err.lower()

    def test_dormant_returns_1(self, capsys):
        """Dormant sessions are NOT operator-revival targets — they resume themselves."""
        result, err = self._run_with_status("dormant", capsys)
        assert result == 1
        # The error message must name the current status
        assert "dormant" in err


class TestCmdResumeHappyPath:
    def test_transitions_to_pending_and_appends_steering(self):
        """Happy path: transitions session to pending and pushes the steering message."""
        session = _make_session("sess-ok", status="completed", model="claude-opus-4-5")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        mock_transition = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message") as mock_push,
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=mock_transition,
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-ok", message="Do the patch."))

        assert result == 0
        # Steering message must be pushed to Redis before transition_status is called
        mock_push.assert_called_once_with("sess-ok", "Do the patch.", "resume:valor-session resume")
        mock_transition.assert_called_once_with(
            session, "pending", reason="resume (valor-session resume)", reject_from_terminal=False
        )

    def test_steering_push_before_transition(self):
        """Steering push must precede transition_status call (no race window)."""
        call_order: list[str] = []
        session = _make_session("sess-order", status="completed")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        def _record_push(*_a, **_kw):
            call_order.append("push")

        def _record_transition(s, status, reason="", reject_from_terminal=True):
            call_order.append("transition")

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message", side_effect=_record_push),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(side_effect=_record_transition),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-order"))

        assert result == 0
        assert call_order.index("push") < call_order.index("transition"), (
            "push_steering_message() must be called before transition_status()"
        )

    def test_json_output(self, capsys):
        session = _make_session(
            "sess-j",
            status="completed",
            claude_session_uuid="uuid-abc",
            model="claude-sonnet-4-5",
        )
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-j", as_json=True))

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert data["session_id"] == "sess-j"
        assert data["status"] == "resumed"
        assert data["claude_session_uuid"] == "uuid-abc"


# ---------------------------------------------------------------------------
# cmd_resume: killed / failed support (#1061)
# ---------------------------------------------------------------------------


class TestCmdResumeKilledFailedSupport:
    """Killed and failed sessions may be resumed when a claude_session_uuid is stored.

    See issue #1061.
    """

    def _run_resume(self, session, message="Try again."):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        mock_transition = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message") as mock_push,
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=mock_transition,
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id=session.session_id, message=message))
        return result, mock_transition, mock_push

    def test_killed_with_uuid_resumes(self):
        session = _make_session("sess-k", status="killed", claude_session_uuid="uuid-killed")
        result, mock_transition, mock_push = self._run_resume(
            session, message="Pick up where we left off."
        )
        assert result == 0
        mock_push.assert_called_once_with(
            "sess-k", "Pick up where we left off.", "resume:valor-session resume"
        )
        mock_transition.assert_called_once_with(
            session, "pending", reason="resume (valor-session resume)", reject_from_terminal=False
        )

    def test_failed_with_uuid_resumes(self):
        session = _make_session("sess-f", status="failed", claude_session_uuid="uuid-failed")
        result, mock_transition, mock_push = self._run_resume(session, message="Recover.")
        assert result == 0
        mock_push.assert_called_once_with("sess-f", "Recover.", "resume:valor-session resume")
        mock_transition.assert_called_once_with(
            session, "pending", reason="resume (valor-session resume)", reject_from_terminal=False
        )


class TestCmdResumeNullUuidGuard:
    """A killed session without a stored claude_session_uuid cannot be resumed."""

    def _run_resume_and_capture(self, session, capsys):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id=session.session_id))
        return result, capsys.readouterr().err

    def test_killed_with_null_uuid_exits_1_with_exact_message(self, capsys):
        """Exact error string is part of the operator-facing contract — see #1061."""
        session = _make_session("sess-knone", status="killed", claude_session_uuid=None)
        result, err = self._run_resume_and_capture(session, capsys)
        assert result == 1
        assert err.strip() == (
            "Error: cannot resume: no transcript UUID stored "
            "(session was killed before first turn completed)"
        )

    def test_failed_with_null_uuid_exits_1(self, capsys):
        session = _make_session("sess-fnone", status="failed", claude_session_uuid=None)
        result, err = self._run_resume_and_capture(session, capsys)
        assert result == 1
        assert "no transcript UUID stored" in err


class TestResumeNoReentryWarning:
    """Post-cutover (#1924): the #1836 Part C / #1721 re-entry warning is gone.

    Four-scalar resume (claude_session_uuid + dev_agent_id + runner_cwd +
    claude_version, consumed by the SessionRunner) IS real transcript re-entry,
    so a successful gate-pass no longer needs an honest-limitation caveat.
    ``resume_handles`` — the field the warning keyed on — was deleted from the
    model; no resume attaches a warning anymore.
    """

    def _resume(self, session):
        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message"),
            patch.dict(
                "sys.modules",
                {
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            return resume_session(session, "continue", source="test")

    def test_resume_has_no_reentry_warning(self):
        """A successful resume carries warning=None — the re-entry caveat died
        with resume_handles."""
        session = _make_session(
            "sess-runner",
            status="completed",
            claude_session_uuid="pm-uuid-abc",
        )
        result = self._resume(session)
        assert result.success is True
        assert result.warning is None


class TestCmdResumeStatusGuardExactMessage:
    """The operator-facing wording of the status guard must be stable."""

    def test_status_rejection_uses_completed_killed_failed_abandoned_wording(self, capsys):
        session = _make_session("sess-paused", status="paused_circuit")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        resumable = frozenset({"completed", "killed", "failed", "abandoned"})

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=resumable,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-paused"))

        assert result == 1
        err = capsys.readouterr().err.strip()
        assert err == (
            "Error: Session sess-paused has status 'paused_circuit'. "
            "Only completed/killed/failed/abandoned sessions can be resumed."
        )


# ---------------------------------------------------------------------------
# cmd_resume: abandoned support (issue #1539)
# ---------------------------------------------------------------------------


class TestCmdResumeAbandonedSupport:
    """Abandoned sessions are now resumable (issue #1539)."""

    def _run_resume(self, session, message="Continue."):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        mock_transition = MagicMock()
        resumable = frozenset({"completed", "killed", "failed", "abandoned"})

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message") as mock_push,
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=mock_transition,
                        RESUMABLE_STATUSES=resumable,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id=session.session_id, message=message))
        return result, mock_transition, mock_push

    def test_abandoned_with_uuid_resumes(self):
        session = _make_session("sess-a", status="abandoned", claude_session_uuid="uuid-abandoned")
        result, mock_transition, mock_push = self._run_resume(
            session, message="Pick up where we left off."
        )
        assert result == 0
        mock_push.assert_called_once_with(
            "sess-a", "Pick up where we left off.", "resume:valor-session resume"
        )
        mock_transition.assert_called_once()
        _, kwargs = mock_transition.call_args
        assert kwargs.get("reject_from_terminal") is False

    def test_cancelled_still_rejected(self, capsys):
        """Cancelled is an intentional human stop — must never be resumable."""
        session = _make_session("sess-c", status="cancelled", claude_session_uuid="uuid-c")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        resumable = frozenset({"completed", "killed", "failed", "abandoned"})

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(),
                        RESUMABLE_STATUSES=resumable,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-c"))

        assert result == 1
        err = capsys.readouterr().err
        assert "cancelled" in err


# ---------------------------------------------------------------------------
# resume_session: shared core function (issue #1539)
# ---------------------------------------------------------------------------


class TestResumeSessionCore:
    """Tests for the shared resume_session() programmatic core."""

    def _make_mock_session(
        self,
        session_id="core-sess",
        status="failed",
        uuid="uuid-core",
    ):
        s = MagicMock()
        s.session_id = session_id
        s.status = status
        s.claude_session_uuid = uuid
        s.model = "claude-opus-4-5"
        return s

    def _patch_lifecycle(self, mock_transition=None, resumable=None):
        if resumable is None:
            resumable = frozenset({"completed", "killed", "failed", "abandoned"})
        if mock_transition is None:
            mock_transition = MagicMock()
        return patch.dict(
            "sys.modules",
            {
                "models.session_lifecycle": MagicMock(
                    transition_status=mock_transition,
                    RESUMABLE_STATUSES=resumable,
                ),
            },
        ), mock_transition

    def test_success_returns_resumeresult_with_success_true(self):
        session = self._make_mock_session(status="failed")
        patch_ctx, mock_transition = self._patch_lifecycle()

        with (
            patch("tools.valor_session._load_env"),
            patch_ctx,
        ):
            result = resume_session(session, "fix it", source="test")

        assert isinstance(result, ResumeResult)
        assert result.success is True
        assert result.session_id == "core-sess"
        assert result.model == "claude-opus-4-5"
        assert result.claude_session_uuid == "uuid-core"
        assert result.error is None
        mock_transition.assert_called_once()

    def test_pending_returns_failure(self):
        session = self._make_mock_session(status="pending")
        patch_ctx, _ = self._patch_lifecycle()

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "msg")

        assert result.success is False
        assert "already pending" in result.error

    def test_running_returns_failure(self):
        session = self._make_mock_session(status="running")
        patch_ctx, _ = self._patch_lifecycle()

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "msg")

        assert result.success is False
        assert "currently running" in result.error

    def test_cancelled_returns_failure(self):
        session = self._make_mock_session(status="cancelled")
        resumable = frozenset({"completed", "killed", "failed", "abandoned"})
        patch_ctx, _ = self._patch_lifecycle(resumable=resumable)

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "msg")

        assert result.success is False
        assert "cancelled" in result.error

    def test_null_uuid_returns_failure(self):
        session = self._make_mock_session(status="failed", uuid=None)
        patch_ctx, _ = self._patch_lifecycle()

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "msg")

        assert result.success is False
        assert "no transcript UUID" in result.error

    def test_abandoned_resumes_successfully(self):
        session = self._make_mock_session(status="abandoned")
        patch_ctx, mock_transition = self._patch_lifecycle()

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "recover", source="auto-resume")

        assert result.success is True
        mock_transition.assert_called_once()
        _, kwargs = mock_transition.call_args
        assert kwargs.get("reject_from_terminal") is False

    def test_steering_push_before_transition(self):
        """push_steering_message() must precede transition_status() (no race window)."""
        call_order: list[str] = []
        session = self._make_mock_session(status="completed")

        def _record_push(*_a, **_kw):
            call_order.append("push")

        def _record_transition(s, status, reason="", reject_from_terminal=True):
            call_order.append("transition")

        mock_transition = MagicMock(side_effect=_record_transition)
        patch_ctx, _ = self._patch_lifecycle(mock_transition=mock_transition)

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message", side_effect=_record_push) as mock_push,
            patch_ctx,
        ):
            result = resume_session(session, "continue")

        assert result.success is True
        assert call_order.index("push") < call_order.index("transition")
        mock_push.assert_called_once_with("core-sess", "continue", "resume:cli")

    def test_transition_error_returns_failure(self):
        session = self._make_mock_session(status="failed")
        mock_transition = MagicMock(side_effect=RuntimeError("Redis down"))
        patch_ctx, _ = self._patch_lifecycle(mock_transition=mock_transition)

        with patch("tools.valor_session._load_env"), patch_ctx:
            result = resume_session(session, "msg")

        assert result.success is False
        assert "Could not transition" in result.error

    def test_gate_pass_resume_has_no_warning(self):
        """Post-cutover (#1924): a gate-pass resume attaches NO warning.

        The #1836 Part C / #1721 re-entry caveat keyed on the deleted
        ``resume_handles`` field; four-scalar resume re-enters the prior
        transcript for real, so ``success=True`` needs no qualifier."""
        session = self._make_mock_session(status="completed", uuid="pm-uuid-runner")
        patch_ctx, mock_transition = self._patch_lifecycle()

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message"),
            patch_ctx,
        ):
            result = resume_session(session, "continue", source="test")

        assert result.success is True
        assert result.warning is None
        mock_transition.assert_called_once()


# ---------------------------------------------------------------------------
# _find_session: dual-id lookup (#1061)
# ---------------------------------------------------------------------------


class TestFindSessionByPrimarySessionId:
    """_find_session returns the newest matching record by session_id."""

    def test_single_match_returned(self):
        session = _make_session("sess-1")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
            result = _find_session("sess-1")

        assert result is session
        mock_cls.query.filter.assert_called_once_with(session_id="sess-1")
        mock_cls.get_by_id.assert_not_called()

    def test_multiple_matches_returns_newest_by_created_at(self):
        old_session = _make_session("sess-1")
        old_session.created_at = 100
        new_session = _make_session("sess-1")
        new_session.created_at = 500
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [old_session, new_session]

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
            result = _find_session("sess-1")

        assert result is new_session


class TestFindSessionFallbackToAgentSessionId:
    """When session_id filter is empty, fall back to AgentSession.get_by_id()."""

    def test_uuid_fallback_when_session_id_empty(self):
        # Issue #1720: _find_session now retries _CLASS_SET_RETRY_ATTEMPTS times
        # before falling through to get_by_id.  The filter is called N times
        # (once per retry attempt) when the class-set is empty, then get_by_id.
        import tools.valor_session as vs

        uuid_session = _make_session("sess-from-uuid")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = uuid_session

        with (
            patch.dict(
                "sys.modules",
                {"models.agent_session": MagicMock(AgentSession=mock_cls)},
            ),
            patch("tools.valor_session.time.sleep"),
        ):  # skip backoff in unit tests
            result = _find_session("c00fd40d7a10432ba38b52bead17061f")

        assert result is uuid_session
        # filter is called _CLASS_SET_RETRY_ATTEMPTS times (bounded retry exhaust)
        assert mock_cls.query.filter.call_count == vs._CLASS_SET_RETRY_ATTEMPTS
        mock_cls.get_by_id.assert_called_once_with("c00fd40d7a10432ba38b52bead17061f")

    def test_returns_none_when_neither_lookup_finds(self):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = None

        with (
            patch.dict(
                "sys.modules",
                {"models.agent_session": MagicMock(AgentSession=mock_cls)},
            ),
            patch("tools.valor_session.time.sleep"),
        ):  # skip retry backoff
            result = _find_session("nonexistent-id")

        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string must not raise — get_by_id has its own empty-string guard."""
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = None

        with (
            patch.dict(
                "sys.modules",
                {"models.agent_session": MagicMock(AgentSession=mock_cls)},
            ),
            patch("tools.valor_session.time.sleep"),
        ):  # skip retry backoff
            result = _find_session("")

        assert result is None


# ---------------------------------------------------------------------------
# Dual-id: cmd_status / cmd_inspect / cmd_kill / cmd_steer (#1061)
# ---------------------------------------------------------------------------


class TestDualIdLookupAcrossSubcommands:
    """Each of cmd_status, cmd_inspect, cmd_kill, cmd_steer must resolve UUIDs."""

    def _patch_agent_session(self, mock_cls):
        return patch.dict(
            "sys.modules",
            {
                "models.agent_session": MagicMock(AgentSession=mock_cls),
                "models.session_lifecycle": MagicMock(
                    TERMINAL_STATUSES={"completed", "killed", "failed"},
                    finalize_session=MagicMock(),
                ),
            },
        )

    def test_cmd_status_resolves_by_agent_session_id(self, capsys):
        session = _make_session("sess-status", status="completed")
        mock_cls = MagicMock()
        # session_id filter returns empty → UUID fallback path
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = session

        args = argparse.Namespace(
            id="c00fd40d7a10432ba38b52bead17061f", json=True, full_message=False
        )

        with (
            patch("tools.valor_session._load_env"),
            patch("tools.valor_session._check_worker_health", return_value=(True, None)),
            self._patch_agent_session(mock_cls),
        ):
            result = cmd_status(args)

        assert result == 0
        mock_cls.get_by_id.assert_called_once_with("c00fd40d7a10432ba38b52bead17061f")

    def test_cmd_inspect_resolves_by_agent_session_id(self, capsys):
        session = _make_session("sess-inspect", status="completed")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = session

        args = argparse.Namespace(id="c00fd40d7a10432ba38b52bead17061f", json=True)

        with (
            patch("tools.valor_session._load_env"),
            self._patch_agent_session(mock_cls),
        ):
            result = cmd_inspect(args)

        assert result == 0
        mock_cls.get_by_id.assert_called_once_with("c00fd40d7a10432ba38b52bead17061f")

    def test_cmd_kill_resolves_by_agent_session_id(self, capsys):
        session = _make_session("sess-kill", status="running")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = session
        mock_finalize = MagicMock()

        args = argparse.Namespace(id="c00fd40d7a10432ba38b52bead17061f", json=True, all=False)

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        TERMINAL_STATUSES={"completed", "killed", "failed"},
                        finalize_session=mock_finalize,
                    ),
                },
            ),
        ):
            result = cmd_kill(args)

        assert result == 0
        mock_cls.get_by_id.assert_called_once_with("c00fd40d7a10432ba38b52bead17061f")
        mock_finalize.assert_called_once()
        # finalize_session must be called with the canonical session, and the
        # returned killed id must be the session.session_id (not the UUID arg)
        finalize_call = mock_finalize.call_args
        assert finalize_call.args[0] is session

    def test_cmd_steer_resolves_by_agent_session_id_and_delegates_with_session_id(self):
        """cmd_steer resolves UUID → session, then calls steer_session with canonical session_id."""
        session = _make_session("sess-steer", status="running")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = session
        mock_steer = MagicMock(return_value={"success": True})

        args = argparse.Namespace(
            id="c00fd40d7a10432ba38b52bead17061f",
            message="Hold up.",
            json=False,
        )

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "agent.agent_session_queue": MagicMock(steer_session=mock_steer),
                },
            ),
        ):
            result = cmd_steer(args)

        assert result == 0
        # steer_session must be called with the canonical session_id, not the UUID
        mock_steer.assert_called_once_with("sess-steer", "Hold up.")

    def test_cmd_steer_not_found_returns_1(self, capsys):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = None
        mock_steer = MagicMock()

        args = argparse.Namespace(id="nope", message="x", json=False)

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "agent.agent_session_queue": MagicMock(steer_session=mock_steer),
                },
            ),
        ):
            result = cmd_steer(args)

        assert result == 1
        mock_steer.assert_not_called()
        assert "not found" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# cmd_release
# ---------------------------------------------------------------------------


class TestCmdReleaseNoMatch:
    def test_no_sessions_prints_warning_returns_0(self, capsys):
        """No retained sessions → returns 0 with informational message, no crash."""
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []

        with (
            patch("tools.valor_session._load_env"),
            patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_cls)}),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = cmd_release(_release_args(pr="99"))

        assert result == 0
        out = capsys.readouterr().out
        assert "99" in out  # PR number mentioned

    def test_session_without_pr_url_and_no_branch_not_released(self, capsys):
        """Session with retain=True but no pr_url and gh returns empty branch → not released."""
        session = _make_session("sess-x", retain=True, pr_url="", slug="my-feature")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_cls)}),
            patch("subprocess.run") as mock_run,
        ):
            # gh fails to return a branch name
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = cmd_release(_release_args(pr="42"))

        assert result == 0
        session.save.assert_not_called()


class TestCmdReleaseHappyPath:
    def test_release_by_pr_url(self, capsys):
        """Session matched by pr_url containing PR number is released."""
        session = _make_session(
            "sess-pr",
            retain=True,
            pr_url="https://github.com/org/repo/pull/42",
            slug="some-feature",
        )
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_cls)}),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="session/some-feature\n")
            result = cmd_release(_release_args(pr="42"))

        assert result == 0
        assert session.retain_for_resume is False
        session.save.assert_called_once()

    def test_release_by_branch_slug(self, capsys):
        """Session matched via slug in PR branch name is released even without pr_url."""
        session = _make_session(
            "sess-branch",
            retain=True,
            pr_url="",  # no pr_url set
            slug="my-feature",
        )
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_cls)}),
            patch("subprocess.run") as mock_run,
        ):
            # gh returns a branch containing the slug
            mock_run.return_value = MagicMock(returncode=0, stdout="session/my-feature\n")
            result = cmd_release(_release_args(pr="55"))

        assert result == 0
        assert session.retain_for_resume is False
        session.save.assert_called_once()

    def test_json_output_lists_released_ids(self, capsys):
        session = _make_session(
            "sess-j2",
            retain=True,
            pr_url="https://github.com/org/repo/pull/7",
        )
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_cls)}),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = cmd_release(_release_args(pr="7", as_json=True))

        assert result == 0
        data = json.loads(capsys.readouterr().out)
        assert data["pr"] == "7"
        assert "sess-j2" in data["released"]
        assert data["count"] == 1


# ---------------------------------------------------------------------------
# model=None → not set in the harness argv (plan #2000 Task 2.2: repointed
# off ValorAgent's ClaudeAgentOptions -- dead, no production caller -- onto
# get_response_via_harness's model kwarg, the live CLI-harness equivalent)
# ---------------------------------------------------------------------------


class _EmptyStdoutLines:
    """Async iterator yielding nothing -- for tests that only assert argv."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _empty_stdout_lines() -> _EmptyStdoutLines:
    return _EmptyStdoutLines()


class TestModelNoneNotSetInOptions:
    """Verify that model=None sessions do not override the CLI's own default."""

    @pytest.mark.asyncio
    async def test_model_none_excluded_from_argv(self):
        """When model=None, --model must not appear in the harness argv."""
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.stdout = _empty_stdout_lines()
            proc.stderr = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.pid = 1
            mock_exec.return_value = proc

            await get_response_via_harness(message="hi", working_dir="/tmp", model=None)

        assert "--model" not in mock_exec.call_args.args

    @pytest.mark.asyncio
    async def test_model_set_when_specified(self):
        """When model is non-None, --model <value> is injected into the argv."""
        from unittest.mock import AsyncMock, patch

        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.stdout = _empty_stdout_lines()
            proc.stderr = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            proc.pid = 1
            mock_exec.return_value = proc

            await get_response_via_harness(
                message="hi", working_dir="/tmp", model="claude-opus-4-5"
            )

        argv = mock_exec.call_args.args
        assert "--model" in argv
        assert argv[argv.index("--model") + 1] == "claude-opus-4-5"


# ---------------------------------------------------------------------------
# retain_for_resume: stage name case invariant
# ---------------------------------------------------------------------------


class TestRetainForResumeStageCase:
    """Guard against case-mismatch bugs in the retain_for_resume harness guard.

    pipeline_state.PipelineStateMachine.current_stage() returns uppercase stage
    names from ALL_STAGES (e.g. "BUILD", "TEST"). The harness guard must compare
    against the same uppercase literal.
    """

    def test_all_stages_are_uppercase(self):
        """ALL_STAGES must consist entirely of uppercase strings."""
        from agent.pipeline_state import ALL_STAGES

        for stage in ALL_STAGES:
            assert stage == stage.upper(), (
                f"Stage {stage!r} is not uppercase — harness comparisons would break"
            )

    def test_build_stage_literal_is_uppercase(self):
        """The BUILD literal used in the retain_for_resume guard must match ALL_STAGES."""
        from agent.pipeline_state import ALL_STAGES

        assert "BUILD" in ALL_STAGES, "BUILD must be present in ALL_STAGES"
        # Confirm the lowercase variant is NOT in ALL_STAGES (the historical bug)
        assert "build" not in ALL_STAGES, (
            "lowercase 'build' must not be in ALL_STAGES — harness guard uses 'BUILD'"
        )

    def test_transition_status_resume_passes_reject_from_terminal_false(self):
        """cmd_resume must pass reject_from_terminal=False to transition_status.

        Without this flag, transitioning a completed session back to pending raises
        ValueError because 'completed' is in TERMINAL_STATUSES.
        """
        session = _make_session("sess-terminal", status="completed")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        mock_transition = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=mock_transition,
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-terminal"))

        assert result == 0
        # Verify reject_from_terminal=False is explicitly passed
        _, kwargs = mock_transition.call_args
        assert kwargs.get("reject_from_terminal") is False, (
            "transition_status must be called with reject_from_terminal=False "
            "so completed→pending promotion is allowed"
        )


# ---------------------------------------------------------------------------
# resume_session: goal re-injection (issue #2136)
# ---------------------------------------------------------------------------


class TestResumeGoalReinjection:
    """A resumed session's first turn input must carry the session's goal.

    The resume path pushes only the caller's ``--message`` onto the steering
    list; the worker drains that as the first turn input. If the transcript's
    goal was compacted and the message is generic ("continue"), the resumed
    session is goalless. ``resume_session`` now folds the record's goal
    (``context_summary`` → ``message_text`` → latest ``summary`` event) into the
    pushed message as ``[Prior session context: <goal>]\\n\\n<message>``, mirroring
    ``agent/session_executor.py:2262-2269``.
    """

    def _goal_session(
        self,
        *,
        session_id: str = "sess-goal",
        status: str = "completed",
        claude_session_uuid: str | None = "uuid-goal",
        context_summary=None,
        message_text=None,
        summary=None,
    ) -> MagicMock:
        """Build a session with explicit (string or None) goal fields.

        Unlike ``_make_session``, the three goal-bearing attributes are set
        explicitly so they are real strings / None rather than truthy
        MagicMock children (which the ``isinstance(str)`` guard must skip).
        """
        s = MagicMock()
        s.session_id = session_id
        s.status = status
        s.claude_session_uuid = claude_session_uuid
        s.model = None
        s.context_summary = context_summary
        s.message_text = message_text
        s.summary = summary
        return s

    def _run(self, session, message="continue"):
        """Call resume_session directly; return (result, pushed_text)."""
        mock_transition = MagicMock()
        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message") as mock_push,
            patch.dict(
                "sys.modules",
                {
                    "models.session_lifecycle": MagicMock(
                        transition_status=mock_transition,
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = resume_session(session, message, source="cli")
        assert result.success, f"resume_session failed: {result.error}"
        mock_push.assert_called_once()
        pushed_text = mock_push.call_args[0][1]
        return result, pushed_text

    def test_context_summary_folded_into_message(self):
        """(a) context_summary set → goal prefixes the generic message."""
        session = self._goal_session(context_summary="Fix the auth token refresh bug")
        _, pushed = self._run(session, message="continue")
        # (h) exact string the executor would pop as steering_msgs[0]["text"].
        assert pushed == "[Prior session context: Fix the auth token refresh bug]\n\ncontinue"

    def test_falls_back_to_message_text(self):
        """(b) empty context_summary → falls through to message_text."""
        session = self._goal_session(
            context_summary="", message_text="Original task: migrate the queue"
        )
        _, pushed = self._run(session, message="continue")
        assert pushed == "[Prior session context: Original task: migrate the queue]\n\ncontinue"

    def test_falls_back_to_summary_event(self):
        """(c) context_summary + message_text empty → uses latest summary."""
        session = self._goal_session(
            context_summary=None, message_text=None, summary="Progress: wired the resolver"
        )
        _, pushed = self._run(session, message="continue")
        assert pushed == "[Prior session context: Progress: wired the resolver]\n\ncontinue"

    def test_no_goal_pushes_raw_message(self):
        """(d) all goal fields empty/None → raw message pushed, no prefix."""
        session = self._goal_session(context_summary=None, message_text=None, summary=None)
        _, pushed = self._run(session, message="continue")
        assert pushed == "continue"
        assert "Prior session context" not in pushed

    def test_whitespace_only_context_summary_falls_through(self):
        """(e) whitespace-only context_summary is treated as empty."""
        session = self._goal_session(context_summary="   \n  ", message_text="Real task anchor")
        _, pushed = self._run(session, message="continue")
        assert pushed == "[Prior session context: Real task anchor]\n\ncontinue"

    def test_long_goal_truncated(self):
        """(f) an over-long goal is truncated at the cap with an ellipsis."""
        from tools.valor_session import _RESUME_GOAL_MAX_CHARS

        long_goal = "x" * (_RESUME_GOAL_MAX_CHARS + 500)
        session = self._goal_session(context_summary=long_goal)
        _, pushed = self._run(session, message="continue")
        assert pushed.startswith("[Prior session context: ")
        assert pushed.endswith("\n\ncontinue")
        # The embedded goal is capped (allowing for a short ellipsis marker).
        inner = pushed[len("[Prior session context: ") : -len("]\n\ncontinue")]
        assert len(inner) <= _RESUME_GOAL_MAX_CHARS
        assert inner.endswith("…") or inner.endswith("...")

    def test_already_prefixed_message_not_double_wrapped(self):
        """(g) an operator-supplied already-prefixed message is not re-wrapped."""
        session = self._goal_session(context_summary="Some goal")
        operator_msg = "[Prior session context: hand-written]\n\ndo the thing"
        _, pushed = self._run(session, message=operator_msg)
        assert pushed == operator_msg
        assert pushed.count("[Prior session context:") == 1

    def test_magicmock_goal_fields_skipped(self):
        """A default MagicMock session (non-string goal attrs) pushes raw message."""
        # _make_session leaves context_summary/message_text/summary as MagicMock
        # children — truthy but not str. The isinstance guard must skip them.
        session = _make_session("sess-mm", status="completed")
        _, pushed = self._run(session, message="continue")
        assert pushed == "continue"
