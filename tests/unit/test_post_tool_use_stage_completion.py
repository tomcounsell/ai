"""Unit tests for Skill tool stage completion in agent/hooks/post_tool_use.py.

Tests _complete_pipeline_stage() and the integration of stage completion logic
into the post_tool_use_hook dispatcher.
"""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.hooks.post_tool_use import _complete_pipeline_stage
from agent.hooks.pre_tool_use import _start_pipeline_stage
from agent.pipeline_ledger import PipelineLedger
from models.agent_session import AgentSession, SessionType
from models.session_lifecycle import release_issue_lock, touch_issue_lock

_LEDGER_CUTOVER_REPO = "test-owner/post-hook-cutover-repo"
_LEDGER_CUTOVER_ISSUE = 900401


def _cleanup_ledger(target_repo: str, issue_number: int) -> None:
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


def _create_session(
    session_id: str, *, issue_number: int | None, active_run_id: str | None
) -> AgentSession:
    return AgentSession.create(
        project_key="test-post-hook-cutover",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=session_id,
        working_dir="/tmp",
        issue_number=issue_number,
        active_run_id=active_run_id,
    )


class TestCompletePipelineStage:
    """Test _complete_pipeline_stage helper.

    _complete_pipeline_stage() resolves its state machine via
    ``agent.pipeline_state.resolve_pipeline_state_machine()`` (issue #2012
    follow-up), which returns a ``(state_machine, used_ledger, detail)``
    3-tuple. The mocked ``agent.pipeline_state`` module below stubs
    ``resolve_pipeline_state_machine`` directly to exercise the
    pre-existing session-keyed fallback behavior these tests cover.
    """

    def _make_mocks(self, current_stage: str | None = "BUILD"):
        """Create mock AgentSession and PipelineStateMachine modules."""
        mock_session = MagicMock()
        mock_session.session_id = "parent-session-1"
        mock_session.issue_number = None
        mock_session.active_run_id = None

        mock_sm_instance = MagicMock()
        mock_sm_instance.current_stage.return_value = current_stage

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

    def test_completes_in_progress_stage(self, caplog):
        """When a stage is in_progress, complete_stage() is called with that stage."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage="BUILD")

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
            _complete_pipeline_stage("parent-session-1")

        mock_sm.current_stage.assert_called_once()
        mock_sm.complete_stage.assert_called_once_with("BUILD")
        assert "Completed pipeline stage BUILD" in caplog.text

    def test_skips_when_no_in_progress_stage(self, caplog):
        """When no stage is in_progress, complete_stage() is not called."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage=None)

        with (
            patch.dict(
                "sys.modules",
                {
                    "agent.pipeline_state": mock_psm_mod,
                    "models.agent_session": mock_as_mod,
                },
            ),
            caplog.at_level(logging.DEBUG),
        ):
            _complete_pipeline_stage("parent-session-2")

        mock_sm.complete_stage.assert_not_called()
        assert "No in_progress stage" in caplog.text

    def test_logs_warning_when_session_not_found(self, caplog):
        """When session is not in Redis, logs a warning and does not crash."""
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
            _complete_pipeline_stage("nonexistent-session")

        mock_psm_mod.PipelineStateMachine.assert_not_called()
        assert "nonexistent-session" in caplog.text
        assert "not found" in caplog.text

    def test_swallows_complete_stage_exception(self, caplog):
        """If complete_stage() raises, the exception is caught and logged."""
        _, mock_sm, mock_psm_mod, mock_as_mod = self._make_mocks(current_stage="TEST")
        mock_sm.complete_stage.side_effect = RuntimeError("state machine error")

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
            # Must not raise
            _complete_pipeline_stage("parent-session-3")

        assert "Failed to complete pipeline stage" in caplog.text
        assert "state machine error" in caplog.text

    def test_swallows_redis_error(self, caplog):
        """If Redis lookup raises, the exception is caught and logged."""
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
            _complete_pipeline_stage("parent-session-4")

        assert "Failed to complete pipeline stage" in caplog.text


class TestCompletePipelineStageLedgerCutover:
    """Real Popoto/Redis integration (no mocks): _complete_pipeline_stage()
    prefers the issue-keyed PipelineLedger when the session's per-issue
    run_id lease is live and pinned to a target_repo (issue #2012
    follow-up) -- the counterpart to
    ``TestStartPipelineStageLedgerCutover`` in
    ``test_pre_tool_use_start_stage.py``.
    """

    def setup_method(self):
        _cleanup_ledger(_LEDGER_CUTOVER_REPO, _LEDGER_CUTOVER_ISSUE)
        self._run_id = None

    def teardown_method(self):
        _cleanup_ledger(_LEDGER_CUTOVER_REPO, _LEDGER_CUTOVER_ISSUE)
        if self._run_id:
            release_issue_lock(_LEDGER_CUTOVER_ISSUE, self._run_id)

    def test_completes_stage_on_ledger_when_lease_is_live_and_pinned(self):
        """issue_number + active_run_id set, and a live lease with a pinned
        target_repo -> the stage completion lands on the ledger, not the
        session. Starts the stage via the real pre_tool_use hook first so
        the ledger genuinely has an in_progress stage to complete."""
        run_id = uuid.uuid4().hex
        self._run_id = run_id
        session_id = f"post-hook-cutover-{run_id[:8]}"

        lock = touch_issue_lock(
            _LEDGER_CUTOVER_ISSUE,
            run_id,
            session_id=session_id,
            target_repo=_LEDGER_CUTOVER_REPO,
        )
        assert lock.acquired is True

        session = _create_session(
            session_id, issue_number=_LEDGER_CUTOVER_ISSUE, active_run_id=run_id
        )
        try:
            # ISSUE is always startable (no predecessor check) -- sufficient
            # to exercise the start/complete write-through without walking
            # the full pipeline spine.
            _start_pipeline_stage(session_id, "ISSUE")
            _complete_pipeline_stage(session_id)

            ledger = PipelineLedger.get_or_create(_LEDGER_CUTOVER_REPO, _LEDGER_CUTOVER_ISSUE)
            saved = json.loads(ledger.stage_states_json)
            assert saved.get("ISSUE") == "completed"
            assert saved.get("PLAN") == "ready"

            reloaded = AgentSession.query.filter(session_id=session_id)[0]
            assert not reloaded.stage_states, (
                "the session's own stage_states must stay untouched -- both "
                "the start and the complete must land on the ledger only"
            )
        finally:
            session.delete()

    def test_falls_back_to_session_when_no_live_lease(self):
        """No active_run_id (no lease minted) -> falls back to the
        session-keyed path exactly as before the cutover."""
        session_id = f"post-hook-cutover-fallback-{uuid.uuid4().hex[:8]}"
        session = _create_session(
            session_id, issue_number=_LEDGER_CUTOVER_ISSUE, active_run_id=None
        )
        try:
            _start_pipeline_stage(session_id, "ISSUE")
            _complete_pipeline_stage(session_id)

            reloaded = AgentSession.query.filter(session_id=session_id)[0]
            saved = json.loads(reloaded.stage_states)
            assert saved.get("ISSUE") == "completed"
            assert saved.get("PLAN") == "ready"

            existing = PipelineLedger.query.filter(
                ledger_key=f"{_LEDGER_CUTOVER_REPO}:{_LEDGER_CUTOVER_ISSUE}"
            )
            assert existing == []
        finally:
            session.delete()


class TestDriverTakeoverAtHookLayer:
    """The exact #1997/#2008 handoff shape, reproduced at the LIVE hook
    layer (not just the offline sdlc-tool CLI writers already covered by
    ``tests/unit/test_sdlc_takeover_regression.py``).

    Session A (the "driver") starts ISSUE via ``_start_pipeline_stage`` --
    this lands on the issue-keyed ledger because A's lease is live and
    pinned. The driver then goes terminal: its lease is released. Session B
    (the "takeover" -- a different session_id and active_run_id, re-acquires
    the SAME issue's lease under a new run_id) calls
    ``_complete_pipeline_stage``. The regression assertion:
    ``_complete_pipeline_stage`` must see A's in_progress ISSUE via the
    SAME ledger key ``(target_repo, issue_number)`` and complete it there --
    never on B's own (empty) session-keyed store, which is what the
    pre-cutover ``PipelineStateMachine(session)`` construction would have
    done (B's session never saw ISSUE start at all).
    """

    _REPO = "test-owner/hook-takeover-repo"
    _ISSUE = 900501

    def setup_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)
        self._driver_run_id = None
        self._takeover_run_id = None

    def teardown_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)
        if self._takeover_run_id:
            release_issue_lock(self._ISSUE, self._takeover_run_id)
        if self._driver_run_id:
            release_issue_lock(self._ISSUE, self._driver_run_id)

    def test_takeover_session_completes_driver_started_stage_via_ledger(self):
        # --- Driver: acquires the lease, starts ISSUE via the live hook ---
        driver_run_id = uuid.uuid4().hex
        self._driver_run_id = driver_run_id
        driver_session_id = "sdlc-local-hooktakeover-driver"

        driver_lock = touch_issue_lock(
            self._ISSUE, driver_run_id, session_id=driver_session_id, target_repo=self._REPO
        )
        assert driver_lock.acquired is True

        driver_session = _create_session(
            driver_session_id, issue_number=self._ISSUE, active_run_id=driver_run_id
        )
        try:
            _start_pipeline_stage(driver_session_id, "ISSUE")

            ledger_after_driver = PipelineLedger.get_or_create(self._REPO, self._ISSUE)
            assert json.loads(ledger_after_driver.stage_states_json).get("ISSUE") == "in_progress"

            # --- Driver goes terminal: its lease is released ---
            assert release_issue_lock(self._ISSUE, driver_run_id) is True

            # --- Takeover: a FOREIGN run_id/session_id wins the free lease ---
            takeover_run_id = uuid.uuid4().hex
            self._takeover_run_id = takeover_run_id
            takeover_session_id = "dev-hooktakeover-foreign"
            assert takeover_run_id != driver_run_id
            assert takeover_session_id != driver_session_id

            takeover_lock = touch_issue_lock(
                self._ISSUE,
                takeover_run_id,
                session_id=takeover_session_id,
                target_repo=self._REPO,
            )
            assert takeover_lock.acquired is True

            takeover_session = _create_session(
                takeover_session_id, issue_number=self._ISSUE, active_run_id=takeover_run_id
            )
            try:
                # Takeover's OWN session never saw ISSUE start -- its
                # session-keyed store is empty. The completion must still
                # land on the ledger the driver wrote to.
                assert not takeover_session.stage_states

                _complete_pipeline_stage(takeover_session_id)

                ledger_after_takeover = PipelineLedger.get_or_create(self._REPO, self._ISSUE)
                saved = json.loads(ledger_after_takeover.stage_states_json)
                assert saved.get("ISSUE") == "completed", (
                    "takeover's _complete_pipeline_stage did not see (or "
                    "complete) the driver's in_progress ISSUE via the shared "
                    "issue-keyed ledger"
                )
                assert saved.get("PLAN") == "ready"

                # Neither session's own stage_states was ever touched.
                reloaded_takeover = AgentSession.query.filter(session_id=takeover_session_id)[0]
                assert not reloaded_takeover.stage_states
                reloaded_driver = AgentSession.query.filter(session_id=driver_session_id)[0]
                assert not reloaded_driver.stage_states
            finally:
                takeover_session.delete()
        finally:
            driver_session.delete()


class TestPostToolUseHookSkillCompletion:
    """Test that post_tool_use_hook calls _complete_pipeline_stage on Skill tool completions.

    Phase 5 update: Session resolution uses AGENT_SESSION_ID env var instead of
    session_registry.resolve().
    """

    @pytest.mark.asyncio
    async def test_skill_tool_triggers_complete_stage(self, monkeypatch):
        """A known SDLC Skill tool call invokes _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        monkeypatch.setenv("AGENT_SESSION_ID", "bridge-session-1")
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-build"},
            "session_id": "uuid-abc",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-1", context=MagicMock())

        mock_complete.assert_called_once_with("bridge-session-1")

    @pytest.mark.asyncio
    async def test_unknown_skill_does_not_trigger_complete_stage(self, monkeypatch):
        """A non-SDLC skill (e.g., do-discover-paths) does not call _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        monkeypatch.setenv("AGENT_SESSION_ID", "bridge-session-2")
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-discover-paths"},
            "session_id": "uuid-def",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-2", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_skill_tool_does_not_trigger_complete_stage(self, monkeypatch):
        """Non-Skill tools (e.g., Bash) do not call _complete_pipeline_stage."""
        mock_watchdog = AsyncMock(return_value={})
        monkeypatch.setenv("AGENT_SESSION_ID", "bridge-session-3")
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "session_id": "uuid-ghi",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-3", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_session_skips_complete_stage(self, monkeypatch):
        """When AGENT_SESSION_ID is not set, _complete_pipeline_stage is not called."""
        mock_watchdog = AsyncMock(return_value={})
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-plan"},
            "session_id": "uuid-jkl",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch("agent.hooks.post_tool_use._complete_pipeline_stage") as mock_complete,
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-4", context=MagicMock())

        mock_complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_stage_exception_does_not_propagate(self, monkeypatch):
        """Exceptions from _complete_pipeline_stage are swallowed."""
        mock_watchdog = AsyncMock(return_value={})
        monkeypatch.setenv("AGENT_SESSION_ID", "bridge-session-5")
        input_data = {
            "tool_name": "Skill",
            "tool_input": {"skill": "do-build"},
            "session_id": "uuid-mno",
        }

        with (
            patch("agent.health_check.watchdog_hook", mock_watchdog),
            patch(
                "agent.hooks.post_tool_use._complete_pipeline_stage",
                side_effect=RuntimeError("unexpected failure"),
            ),
        ):
            from agent.hooks.post_tool_use import post_tool_use_hook

            # Must not raise
            result = await post_tool_use_hook(input_data, tool_use_id="tu-5", context=MagicMock())

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_watchdog_always_runs(self):
        """Watchdog hook is always called, regardless of tool type."""
        mock_watchdog = AsyncMock(return_value={})
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "foo.py"},
            "session_id": "uuid-pqr",
        }

        with patch("agent.health_check.watchdog_hook", mock_watchdog):
            from agent.hooks.post_tool_use import post_tool_use_hook

            await post_tool_use_hook(input_data, tool_use_id="tu-6", context=MagicMock())

        mock_watchdog.assert_called_once()
