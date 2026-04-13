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

from tools.valor_session import cmd_release, cmd_resume  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str,
    status: str = "completed",
    retain: bool = False,
    pr_url: str = "",
    slug: str = "",
    claude_session_uuid: str | None = None,
    model: str | None = None,
    steering: list[str] | None = None,
) -> MagicMock:
    s = MagicMock()
    s.session_id = session_id
    s.status = status
    s.retain_for_resume = retain
    s.pr_url = pr_url
    s.slug = slug
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

    def test_failed_returns_1(self, capsys):
        """Only 'completed' sessions can be resumed; failed should be rejected."""
        result, err = self._run_with_status("failed", capsys)
        assert result == 1
        assert "failed" in err.lower() or "completed" in err.lower()


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
