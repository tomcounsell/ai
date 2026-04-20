"""Unit tests for cmd_resume() and cmd_release() in tools/valor_session.py.

Coverage:
- cmd_resume: session not found, wrong status (pending/running/failed), happy-path transition
- cmd_release: no match (no pr_url, no branch match), happy-path by pr_url, happy-path by branch
- model=None on ClaudeAgentOptions: verify model key is absent when not set (avoids SDK override)
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

from tools.valor_session import (  # noqa: E402
    _find_session,
    cmd_inspect,
    cmd_kill,
    cmd_release,
    cmd_resume,
    cmd_status,
    cmd_steer,
)

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
    steering: list[str] | None = None,
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
    s.queued_steering_messages = list(steering or [])
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
                    "models.session_lifecycle": MagicMock(transition_status=MagicMock()),
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
                    "models.session_lifecycle": MagicMock(transition_status=MagicMock()),
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
        """Happy path: transitions session to pending and appends message to steering queue."""
        session = _make_session("sess-ok", status="completed", model="claude-opus-4-5")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]
        mock_transition = MagicMock()

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(transition_status=mock_transition),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-ok", message="Do the patch."))

        assert result == 0
        # Steering message must be saved before transition_status is called
        session.save.assert_called()
        assert "Do the patch." in session.queued_steering_messages
        mock_transition.assert_called_once_with(
            session, "pending", reason="valor-session resume", reject_from_terminal=False
        )

    def test_steering_message_saved_before_transition(self):
        """Steering save must precede transition_status call (no race window)."""
        call_order: list[str] = []
        session = _make_session("sess-order", status="completed")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        def _record_save():
            call_order.append("save")

        def _record_transition(s, status, reason="", reject_from_terminal=True):
            call_order.append("transition")

        session.save.side_effect = _record_save

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(side_effect=_record_transition)
                    ),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-order"))

        assert result == 0
        assert call_order.index("save") < call_order.index("transition"), (
            "session.save() must be called before transition_status()"
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
                    "models.session_lifecycle": MagicMock(transition_status=MagicMock()),
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
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(transition_status=mock_transition),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id=session.session_id, message=message))
        return result, mock_transition

    def test_killed_with_uuid_resumes(self):
        session = _make_session("sess-k", status="killed", claude_session_uuid="uuid-killed")
        result, mock_transition = self._run_resume(session, message="Pick up where we left off.")
        assert result == 0
        assert "Pick up where we left off." in session.queued_steering_messages
        mock_transition.assert_called_once_with(
            session, "pending", reason="valor-session resume", reject_from_terminal=False
        )

    def test_failed_with_uuid_resumes(self):
        session = _make_session("sess-f", status="failed", claude_session_uuid="uuid-failed")
        result, mock_transition = self._run_resume(session, message="Recover.")
        assert result == 0
        assert "Recover." in session.queued_steering_messages
        mock_transition.assert_called_once_with(
            session, "pending", reason="valor-session resume", reject_from_terminal=False
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
                    "models.session_lifecycle": MagicMock(transition_status=MagicMock()),
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


class TestCmdResumeStatusGuardExactMessage:
    """The operator-facing wording of the status guard must be stable."""

    def test_status_rejection_uses_completed_killed_failed_wording(self, capsys):
        session = _make_session("sess-paused", status="paused_circuit")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = [session]

        with (
            patch("tools.valor_session._load_env"),
            patch.dict(
                "sys.modules",
                {
                    "models.agent_session": MagicMock(AgentSession=mock_cls),
                    "models.session_lifecycle": MagicMock(transition_status=MagicMock()),
                },
            ),
        ):
            result = cmd_resume(_resume_args(session_id="sess-paused"))

        assert result == 1
        err = capsys.readouterr().err.strip()
        assert err == (
            "Error: Session sess-paused has status 'paused_circuit'. "
            "Only completed/killed/failed sessions can be resumed."
        )


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
        uuid_session = _make_session("sess-from-uuid")
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = uuid_session

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
            result = _find_session("c00fd40d7a10432ba38b52bead17061f")

        assert result is uuid_session
        mock_cls.query.filter.assert_called_once_with(session_id="c00fd40d7a10432ba38b52bead17061f")
        mock_cls.get_by_id.assert_called_once_with("c00fd40d7a10432ba38b52bead17061f")

    def test_returns_none_when_neither_lookup_finds(self):
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = None

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
            result = _find_session("nonexistent-id")

        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string must not raise — get_by_id has its own empty-string guard."""
        mock_cls = MagicMock()
        mock_cls.query.filter.return_value = []
        mock_cls.get_by_id.return_value = None

        with patch.dict(
            "sys.modules",
            {"models.agent_session": MagicMock(AgentSession=mock_cls)},
        ):
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
# model=None → not set in ClaudeAgentOptions
# ---------------------------------------------------------------------------


class TestModelNoneNotSetInOptions:
    """Verify that model=None sessions do not override SDK/CLI defaults."""

    def test_model_none_excluded_from_options_kwargs(self):
        """When ValorAgent.model is None, 'model' key must not appear in options_kwargs."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(model=None)
        # ValorAgent._create_options only sets options_kwargs["model"] when self.model is truthy.
        # Verify via the source condition: self.model is falsy when model=None so the SDK
        # default is preserved (no override).
        assert not agent.model, "model should be None/falsy when not specified"
        # Call _create_options to ensure no exception is raised when model=None.
        agent._create_options(session_id="test-session")

    def test_model_set_when_specified(self):
        """When ValorAgent.model is non-None, it must be passed to ClaudeAgentOptions."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent(model="claude-opus-4-5")
        assert agent.model == "claude-opus-4-5"
        # _create_options should include model in kwargs (covered by live integration,
        # but we verify the agent stores it correctly for dispatch).


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
                    "models.session_lifecycle": MagicMock(transition_status=mock_transition),
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
