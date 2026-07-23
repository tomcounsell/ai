"""Unit tests for pipeline stage wiring in agent/hooks/pre_tool_use.py.

Tests _extract_stage_from_prompt(), _start_pipeline_stage(), and the
integration of start_stage() into _handle_skill_tool_start().

Phase 5 update: Removed tests for _maybe_register_dev_session (Agent tool
dev-session interception removed). Updated _handle_skill_tool_start tests
to use AGENT_SESSION_ID env var instead of session_registry.resolve().
"""

import json
import logging
import uuid
from unittest.mock import MagicMock, patch

from agent.hooks.pre_tool_use import (
    _SKILL_TO_STAGE,
    _extract_stage_from_prompt,
    _handle_skill_tool_start,
    _start_pipeline_stage,
)
from agent.pipeline_ledger import PipelineLedger
from models.agent_session import AgentSession, SessionType
from models.session_lifecycle import release_issue_lock, touch_issue_lock


class TestExtractStageFromPrompt:
    """Test _extract_stage_from_prompt helper."""

    def test_extracts_stage_colon_format(self):
        assert _extract_stage_from_prompt("Stage: BUILD") == "BUILD"

    def test_extracts_stage_to_execute_dash_format(self):
        assert _extract_stage_from_prompt("Stage to execute -- PLAN") == "PLAN"

    def test_extracts_stage_to_execute_colon(self):
        assert _extract_stage_from_prompt("Stage to execute: TEST") == "TEST"

    def test_extracts_stage_case_insensitive_prefix(self):
        assert _extract_stage_from_prompt("stage: BUILD") == "BUILD"

    def test_extracts_from_longer_prompt(self):
        prompt = (
            "You are a Developer agent.\n\n"
            "Stage: BUILD\n"
            "Issue: https://github.com/example/repo/issues/42\n"
            "Plan: docs/plans/some-plan.md"
        )
        assert _extract_stage_from_prompt(prompt) == "BUILD"

    def test_returns_none_for_empty_prompt(self):
        assert _extract_stage_from_prompt("") is None

    def test_returns_none_for_none_prompt(self):
        assert _extract_stage_from_prompt(None) is None

    def test_returns_none_for_no_stage(self):
        assert _extract_stage_from_prompt("Just do some work please") is None

    def test_returns_none_for_stage_keyword_without_valid_name(self):
        assert _extract_stage_from_prompt("This is a stage of development") is None

    def test_extracts_first_stage_when_multiple_present(self):
        prompt = "Stage: BUILD\nAfter BUILD, run TEST"
        assert _extract_stage_from_prompt(prompt) == "BUILD"

    def test_all_stage_names(self):
        for stage in [
            "ISSUE",
            "PLAN",
            "CRITIQUE",
            "BUILD",
            "TEST",
            "PATCH",
            "REVIEW",
            "DOCS",
            "MERGE",
        ]:
            assert _extract_stage_from_prompt(f"Stage: {stage}") == stage

    def test_fallback_to_keyword_scan(self):
        prompt = "Execute the REVIEW stage for this PR"
        assert _extract_stage_from_prompt(prompt) == "REVIEW"

    def test_fallback_needs_stage_keyword(self):
        assert _extract_stage_from_prompt("Run the BUILD job now") is None


class TestStartPipelineStage:
    """Test _start_pipeline_stage helper.

    _start_pipeline_stage() resolves its state machine via
    ``agent.pipeline_state.resolve_pipeline_state_machine()`` (issue #2012
    follow-up), which returns a ``(state_machine, used_ledger, detail)``
    3-tuple. The mocked ``agent.pipeline_state`` module below must therefore
    stub ``resolve_pipeline_state_machine`` (not ``PipelineStateMachine``
    directly) to exercise the pre-existing session-keyed fallback behavior
    these tests cover -- the mock session has no real issue_number/
    active_run_id, so explicitly setting both to ``None`` (MagicMock
    auto-vivifies any attribute access as a truthy Mock otherwise) matches
    the "fallback" outcome the real helper would resolve to.
    """

    def _make_mocks(self):
        """Create mock AgentSession and PipelineStateMachine modules."""
        mock_session = MagicMock()
        mock_session.stage_states = None
        mock_session.session_id = "parent-1"
        mock_session.issue_number = None
        mock_session.active_run_id = None

        mock_sm_instance = MagicMock()

        mock_psm_module = MagicMock()
        mock_psm_module.PipelineStateMachine.return_value = mock_sm_instance
        mock_psm_module.resolve_pipeline_state_machine.return_value = (
            mock_sm_instance,
            False,
            "session fallback (missing issue_number/active_run_id)",
        )

        mock_as_module = MagicMock()
        mock_as_module.AgentSession.query.filter.return_value = [mock_session]

        return mock_session, mock_sm_instance, mock_psm_module, mock_as_module

    def test_starts_stage_on_parent_session(self, caplog):
        mock_session, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks()

        with (
            patch.dict(
                "sys.modules",
                {
                    "agent.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.INFO),
        ):
            _start_pipeline_stage("parent-1", "BUILD")

        mock_psm_mod.resolve_pipeline_state_machine.assert_called_once_with(mock_session)
        mock_sm.start_stage.assert_called_once_with("BUILD")
        assert "Started pipeline stage BUILD" in caplog.text

    def test_logs_warning_when_parent_not_found(self, caplog):
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.query.filter.return_value = []
        mock_psm_mod = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "agent.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            _start_pipeline_stage("nonexistent", "BUILD")

        assert "Parent session nonexistent not found" in caplog.text

    def test_catches_start_stage_value_error(self, caplog):
        mock_session, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks()
        mock_sm.start_stage.side_effect = ValueError("Cannot start BUILD: no predecessor completed")

        with (
            patch.dict(
                "sys.modules",
                {
                    "agent.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            _start_pipeline_stage("parent-1", "BUILD")

        assert "Failed to start pipeline stage BUILD" in caplog.text
        assert "no predecessor completed" in caplog.text

    def test_catches_redis_error(self, caplog):
        mock_as_mod = MagicMock()
        mock_as_mod.AgentSession.query.filter.side_effect = RuntimeError("Redis down")
        mock_psm_mod = MagicMock()

        with (
            patch.dict(
                "sys.modules",
                {
                    "agent.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.WARNING),
        ):
            _start_pipeline_stage("parent-4", "BUILD")

        assert "Failed to start pipeline stage BUILD" in caplog.text


class TestStartPipelineStageLedgerCutover:
    """Real Popoto/Redis integration (no mocks): _start_pipeline_stage()
    prefers the issue-keyed PipelineLedger when the parent session's
    per-issue run_id lease is live and pinned to a target_repo (issue #2012
    follow-up). This closes the split-brain for the LIVE hook path -- the
    offline sdlc-tool CLI writers already write through the ledger; this
    hook fires on every real Skill-tool invocation inside a live Eng
    session, so it must resolve the SAME ledger record.
    """

    _REPO = "test-owner/hook-cutover-repo"
    _ISSUE = 900301

    def _cleanup_ledger(self):
        for record in PipelineLedger.query.filter(ledger_key=f"{self._REPO}:{self._ISSUE}"):
            record.delete()

    def setup_method(self):
        self._cleanup_ledger()
        self._run_id = None

    def teardown_method(self):
        self._cleanup_ledger()
        if self._run_id:
            release_issue_lock(self._ISSUE, self._run_id)

    def test_writes_through_ledger_when_lease_is_live_and_pinned(self):
        """issue_number + active_run_id set, and a live lease with a pinned
        target_repo -> the stage start lands on the ledger, not the session."""
        run_id = uuid.uuid4().hex
        self._run_id = run_id
        session_id = f"hook-cutover-{run_id[:8]}"

        lock = touch_issue_lock(self._ISSUE, run_id, session_id=session_id, target_repo=self._REPO)
        assert lock.acquired is True
        assert lock.target_repo == self._REPO

        session = AgentSession.create(
            project_key="test-hook-cutover",
            chat_id="x",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir="/tmp",
            issue_number=self._ISSUE,
            active_run_id=run_id,
        )
        try:
            # ISSUE is always startable (no predecessor check) -- sufficient
            # to exercise the write-through without walking the full spine.
            _start_pipeline_stage(session_id, "ISSUE")

            ledger = PipelineLedger.get_or_create(self._REPO, self._ISSUE)
            saved = json.loads(ledger.stage_states_json)
            assert saved.get("ISSUE") == "in_progress", (
                "expected _start_pipeline_stage to write ISSUE=in_progress to "
                "the issue-keyed ledger when the lease is live and pinned"
            )

            reloaded = AgentSession.query.filter(session_id=session_id)[0]
            assert not reloaded.stage_states, (
                "the session's own stage_states must stay untouched -- the "
                "write must land on the ledger only, never a session-side mirror"
            )
        finally:
            session.delete()

    def test_falls_back_to_session_when_no_live_lease(self):
        """No issue_number/active_run_id (or no live lease) -> falls back to
        the session-keyed path exactly as before the cutover (regression
        guard for non-SDLC-tracked / lease-less skill invocations)."""
        session_id = f"hook-cutover-fallback-{uuid.uuid4().hex[:8]}"
        session = AgentSession.create(
            project_key="test-hook-cutover",
            chat_id="x",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir="/tmp",
            issue_number=self._ISSUE,
            active_run_id=None,  # no lease minted -> ledger resolution is skipped
        )
        try:
            _start_pipeline_stage(session_id, "ISSUE")

            reloaded = AgentSession.query.filter(session_id=session_id)[0]
            saved = json.loads(reloaded.stage_states)
            assert saved.get("ISSUE") == "in_progress"

            # No ledger record was ever created for this issue -- with no
            # run_id, resolution short-circuits before any lease/ledger touch.
            existing = PipelineLedger.query.filter(ledger_key=f"{self._REPO}:{self._ISSUE}")
            assert existing == []
        finally:
            session.delete()


class TestSkillToolStartStage:
    """Test _handle_skill_tool_start: maps Skill tool calls to pipeline stage starts.

    Resolution goes through ``agent.hooks.session_resolver.resolve_inflight_session``
    (issue #2205), so these tests patch that resolver directly rather than
    relying on env vars alone -- mirroring production where VALOR_SESSION_ID
    and AGENT_SESSION_ID can be distinct identifiers.
    """

    def test_known_skill_triggers_start_stage(self, monkeypatch):
        """A known SDLC skill calls _start_pipeline_stage with the resolved
        session's session_id and the mapped stage."""
        tool_input = {"skill": "do-build"}
        resolved = MagicMock(session_id="session-abc")
        monkeypatch.setattr(
            "agent.hooks.session_resolver.resolve_inflight_session", lambda: resolved
        )

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-1")

        mock_start.assert_called_once_with("session-abc", "BUILD")

    def test_all_mapped_skills_trigger_correct_stage(self, monkeypatch):
        """Every entry in _SKILL_TO_STAGE maps to the correct stage."""
        resolved = MagicMock(session_id="session-xyz")
        monkeypatch.setattr(
            "agent.hooks.session_resolver.resolve_inflight_session", lambda: resolved
        )
        for skill_name, expected_stage in _SKILL_TO_STAGE.items():
            with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
                _handle_skill_tool_start({"skill": skill_name}, claude_uuid="uuid-2")
            mock_start.assert_called_once_with("session-xyz", expected_stage)

    def test_unknown_skill_name_is_ignored(self, monkeypatch):
        """A skill not in _SKILL_TO_STAGE silently no-ops."""
        tool_input = {"skill": "do-discover-paths"}
        resolved = MagicMock(session_id="session-def")
        monkeypatch.setattr(
            "agent.hooks.session_resolver.resolve_inflight_session", lambda: resolved
        )

        with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-3")

        mock_start.assert_not_called()

    def test_missing_skill_key_is_ignored(self, monkeypatch, caplog):
        """Empty skill name silently no-ops."""
        tool_input = {}
        resolved = MagicMock(session_id="session-ghi")
        monkeypatch.setattr(
            "agent.hooks.session_resolver.resolve_inflight_session", lambda: resolved
        )

        with (
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.DEBUG),
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-4")

        mock_start.assert_not_called()
        assert "empty skill name" in caplog.text

    def test_no_session_id_skips_gracefully(self, monkeypatch, caplog):
        """When no in-flight session resolves (env vars unset / no match),
        _start_pipeline_stage is not called."""
        tool_input = {"skill": "do-build"}
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        with (
            patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start,
            caplog.at_level(logging.DEBUG),
        ):
            _handle_skill_tool_start(tool_input, claude_uuid="uuid-5")

        mock_start.assert_not_called()
        assert "No in-flight session resolved" in caplog.text

    def test_bridge_shape_resolves_via_valor_session_id(self, monkeypatch):
        """Bridge-shape regression guard: a real session with
        agent_session_id != session_id, resolved via VALOR_SESSION_ID, still
        triggers start_stage with the true session_id (issue #2205)."""
        session_id = f"start-stage-bridge-{uuid.uuid4().hex[:8]}"
        session = AgentSession.create(
            project_key="test-start-stage-bridge",
            chat_id="x",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir="/tmp",
        )
        try:
            assert session.agent_session_id != session.session_id
            monkeypatch.setenv("VALOR_SESSION_ID", session.session_id)
            monkeypatch.setenv("AGENT_SESSION_ID", session.agent_session_id)

            with patch("agent.hooks.pre_tool_use._start_pipeline_stage") as mock_start:
                _handle_skill_tool_start({"skill": "do-build"}, claude_uuid="uuid-6")

            mock_start.assert_called_once_with(session_id, "BUILD")
        finally:
            session.delete()

    def test_skill_to_stage_mapping_is_complete(self):
        """Verify all expected SDLC skills are present in _SKILL_TO_STAGE."""
        expected_skills = {
            "do-plan",
            "do-plan-critique",
            "do-build",
            "do-test",
            "do-patch",
            "do-pr-review",
            "do-docs",
            "do-merge",
        }
        assert expected_skills == set(_SKILL_TO_STAGE.keys())
