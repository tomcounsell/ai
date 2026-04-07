"""Integration test for the parent-child session hook lifecycle.

Exercises the full round-trip: PM session spawns a child Dev session via
PreToolUse hook, child runs, SubagentStop hook completes the child and
updates the parent's pipeline stage_states. Uses real Redis (no mocks).

Tests both success and failure outcome paths, plus edge cases like empty
prompts and missing session registry entries.

See docs/features/pm-dev-session-architecture.md "Hook-Driven Lifecycle"
for the architecture this test validates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.agent_session import AgentSession

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_session_registry():
    """Reset the in-memory session registry before and after each test."""
    from agent.hooks.session_registry import _reset_for_testing

    _reset_for_testing()
    yield
    _reset_for_testing()


@pytest.fixture
def pm_session(redis_test_db):
    """Create a PM session in Redis for hook testing."""
    return AgentSession.create(
        session_id="pm-round-trip-test",
        session_type="chat",
        project_key="test",
        status="active",
        chat_id="999",
        sender_name="TestUser",
        message_text="Run the BUILD stage",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


FAKE_CLAUDE_UUID = "test-uuid-round-trip-001"


# ── Round-Trip: Success Path ─────────────────────────────────────────────────


class TestSuccessRoundTrip:
    """Full lifecycle with a successful BUILD stage outcome."""

    def test_pretooluse_starts_stage_without_creating_child(self, pm_session):
        """PreToolUse hook starts BUILD stage on parent but does NOT create a dev-* AgentSession.

        Child subprocess self-registers via VALOR_PARENT_SESSION_ID env var (issue #808).
        """
        from agent.hooks.session_registry import register_pending, resolve
        from bridge.pipeline_state import PipelineStateMachine

        # Walk through prerequisite stages so BUILD can start
        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")

        # Wire the session registry so hooks can resolve the PM session
        register_pending(pm_session.session_id)

        # Simulate PreToolUse hook detecting Agent tool with dev-session
        from agent.hooks.pre_tool_use import _extract_stage_from_prompt, _maybe_start_pipeline_stage

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD\nImplement the authentication feature.",
        }
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # Verify stage extraction works
        assert _extract_stage_from_prompt(tool_input["prompt"]) == "BUILD"

        # Verify NO dev-* AgentSession was created in Redis (child self-registers now)
        dev_sessions_by_parent_session_id = list(
            AgentSession.query.filter(parent_agent_session_id=pm_session.session_id)
        )
        assert len(dev_sessions_by_parent_session_id) == 0, (
            "pre_tool_use should not create dev-* records; "
            "child subprocess self-registers via VALOR_PARENT_SESSION_ID"
        )

        # Verify BUILD stage is now in_progress on the parent
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states)
        assert stage_states.get("BUILD") == "in_progress"

        # Verify registry mapping is set
        assert resolve(FAKE_CLAUDE_UUID) == pm_session.session_id

    def test_subagent_stop_completes_child_and_stage(self, pm_session):
        """SubagentStop hook marks Dev session complete (via agent_session_id UUID lookup)
        and records stage success.

        Uses the two-lookup pattern: bridge session_id → AgentSession → agent_session_id UUID
        → filter children by UUID (issue #808).
        """
        from agent.hooks.session_registry import register_pending
        from bridge.pipeline_state import PipelineStateMachine

        # Walk through prerequisite stages so BUILD can start
        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")

        register_pending(pm_session.session_id)

        # Simulate: child subprocess registered via VALOR_PARENT_SESSION_ID env var.
        # The parent's agent_session_id UUID is stored in parent_agent_session_id.
        parent_agent_uuid = pm_session.agent_session_id
        child_session = AgentSession.create_local(
            session_id="local-child-build-001",
            project_key="test",
            working_dir="/tmp",
            status="running",
            parent_agent_session_id=parent_agent_uuid,
        )

        # SubagentStop completes the child using two-lookup pattern
        from agent.hooks.subagent_stop import _register_dev_session_completion

        input_data = {"result": "PR created at https://github.com/test/repo/pull/42"}
        _register_dev_session_completion(
            agent_id="agent-001",
            input_data=input_data,
            claude_uuid=FAKE_CLAUDE_UUID,
        )

        # Verify child session status is completed
        updated_child = list(AgentSession.query.filter(session_id="local-child-build-001"))
        assert len(updated_child) == 1
        assert updated_child[0].status == "completed"

        # Verify BUILD stage is completed on the parent
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states)
        assert stage_states.get("BUILD") == "completed"


# ── Round-Trip: Failure Path ─────────────────────────────────────────────────


class TestFailureRoundTrip:
    """Full lifecycle where the Dev session reports a TEST failure."""

    def test_subagent_stop_records_stage_failure(self, pm_session):
        """SubagentStop hook marks stage as failed when output indicates failure."""
        from agent.hooks.session_registry import register_pending

        register_pending(pm_session.session_id)

        # PreToolUse: start TEST stage (need BUILD completed first)
        from bridge.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(pm_session)
        # Walk through prerequisite stages to get to TEST
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")
        sm.complete_stage("BUILD")

        # Now start TEST via the hook
        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: TEST\nRun the test suite and report results.",
        }
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # Verify TEST is in_progress
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states)
        assert stage_states.get("TEST") == "in_progress"

        # Simulate child session registered via env var (so subagent_stop can find it)
        parent_agent_uuid = pm_session.agent_session_id
        AgentSession.create_local(
            session_id="local-child-test-001",
            project_key="test",
            working_dir="/tmp",
            status="running",
            parent_agent_session_id=parent_agent_uuid,
        )

        # SubagentStop with failure output
        from agent.hooks.subagent_stop import _register_dev_session_completion

        input_data = {"result": "3 tests failed with AssertionError"}
        _register_dev_session_completion(
            agent_id="agent-002",
            input_data=input_data,
            claude_uuid=FAKE_CLAUDE_UUID,
        )

        # Verify TEST stage is failed on parent
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states)
        assert stage_states.get("TEST") == "failed"


# ── Edge Cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases: empty prompts, missing registry, non-dev-session agents."""

    def test_empty_prompt_skips_stage_start(self, pm_session):
        """Empty prompt in dev-session skips start_stage. No child AgentSession created by hook."""
        from agent.hooks.session_registry import register_pending

        register_pending(pm_session.session_id)

        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {
            "type": "dev-session",
            "prompt": "",
        }
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # No dev-* record created by hook (child self-registers via env var)
        dev_sessions = list(
            AgentSession.query.filter(parent_agent_session_id=pm_session.agent_session_id)
        )
        assert len(dev_sessions) == 0

        # Parent should have no in_progress stage
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states) if parent.stage_states else {}
        in_progress_stages = [s for s, v in stage_states.items() if v == "in_progress"]
        assert len(in_progress_stages) == 0

    def test_missing_registry_skips_gracefully(self, redis_test_db):
        """No session registry entry -> hook skips without error."""
        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD\nDo something.",
        }
        # No register_pending called -> resolve returns None
        _maybe_start_pipeline_stage(tool_input, claude_uuid="nonexistent-uuid")

        # No Dev session should be created
        all_devs = list(AgentSession.query.filter(session_type="dev"))
        assert len(all_devs) == 0

    def test_non_dev_session_ignored(self, pm_session):
        """Agent tool with type != 'dev-session' is ignored by hook."""
        from agent.hooks.session_registry import register_pending

        register_pending(pm_session.session_id)

        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {
            "type": "code-reviewer",
            "prompt": "Stage: REVIEW\nReview the code.",
        }
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # No Dev session created for non-dev-session types
        dev_sessions = list(
            AgentSession.query.filter(parent_agent_session_id=pm_session.agent_session_id)
        )
        assert len(dev_sessions) == 0

    def test_stage_extraction_patterns(self):
        """Verify stage extraction handles various prompt formats."""
        from agent.hooks.pre_tool_use import _extract_stage_from_prompt

        # Standard format
        assert _extract_stage_from_prompt("Stage: BUILD") == "BUILD"
        assert _extract_stage_from_prompt("Stage: TEST") == "TEST"
        assert _extract_stage_from_prompt("Stage: REVIEW") == "REVIEW"

        # Extended format
        assert _extract_stage_from_prompt("Stage to execute: PLAN") == "PLAN"
        assert _extract_stage_from_prompt("Stage to execute -- MERGE") == "MERGE"

        # Case insensitive
        assert _extract_stage_from_prompt("stage: build") == "BUILD"

        # Embedded in larger prompt
        assert (
            _extract_stage_from_prompt("Your assignment:\nStage: DOCS\nPlease update the docs.")
            == "DOCS"
        )

        # No stage found
        assert _extract_stage_from_prompt("Just do some work") is None
        assert _extract_stage_from_prompt("") is None
        assert _extract_stage_from_prompt(None) is None

    def test_subagent_stop_missing_registry_skips(self, redis_test_db):
        """SubagentStop with no registry entry skips gracefully."""
        from agent.hooks.subagent_stop import _register_dev_session_completion

        # Should not raise
        _register_dev_session_completion(
            agent_id="agent-orphan",
            input_data={"result": "some output"},
            claude_uuid="missing-uuid",
        )
        # No crash = success


# ── Stage State Injection ────────────────────────────────────────────────────


class TestStageStateInjection:
    """Verify SubagentStop injects stage_states back into the PM context."""

    @pytest.mark.asyncio
    async def test_hook_returns_pipeline_state(self, pm_session):
        """subagent_stop_hook returns pipeline state in the reason field."""
        from agent.hooks.session_registry import register_pending
        from bridge.pipeline_state import PipelineStateMachine

        # Walk through prerequisite stages so BUILD can start
        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")

        register_pending(pm_session.session_id)

        # Set up: start BUILD stage via hook (no child created by hook anymore)
        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {
            "type": "dev-session",
            "prompt": "Stage: BUILD\nBuild the thing.",
        }
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # Simulate child subprocess self-registering via VALOR_PARENT_SESSION_ID
        parent_agent_uuid = pm_session.agent_session_id
        AgentSession.create_local(
            session_id="local-child-inject-test",
            project_key="test",
            working_dir="/tmp",
            status="running",
            parent_agent_session_id=parent_agent_uuid,
        )

        # Complete via the async hook
        from agent.hooks.subagent_stop import subagent_stop_hook

        input_data = {
            "agent_type": "dev-session",
            "agent_id": "agent-inject-test",
            "session_id": FAKE_CLAUDE_UUID,
            "result": "PR created at https://github.com/test/repo/pull/99",
        }
        result = await subagent_stop_hook(input_data, tool_use_id=None, context={})

        # The hook should return pipeline state in the reason field
        assert "reason" in result
        assert "Pipeline state:" in result["reason"]
        assert "BUILD" in result["reason"]


# ── Env Var Linkage (issue #808) ─────────────────────────────────────────────


class TestEnvVarLinkage:
    """Verify the VALOR_PARENT_SESSION_ID env var approach creates linked sessions.

    Simulates what happens in the child subprocess: user_prompt_submit.py reads
    VALOR_PARENT_SESSION_ID and passes it to create_local(), creating one linked
    local-* AgentSession record (no orphaned dev-* record).
    """

    def test_child_session_linked_via_env_var(self, pm_session):
        """When VALOR_PARENT_SESSION_ID is set, child local-* record has correct parent."""
        parent_agent_uuid = pm_session.agent_session_id

        # Simulate child subprocess self-registering (what user_prompt_submit.py does)
        child_session = AgentSession.create_local(
            session_id="local-child-env-linkage",
            project_key="test",
            working_dir="/tmp",
            status="running",
            message_text="Build the feature",
            parent_agent_session_id=parent_agent_uuid,
        )

        # Verify the child record is linked to the parent via agent_session_id UUID
        assert child_session.parent_agent_session_id == parent_agent_uuid

        # Verify it can be found by querying with the parent's agent_session_id UUID
        linked_children = list(AgentSession.query.filter(parent_agent_session_id=parent_agent_uuid))
        assert len(linked_children) == 1
        assert linked_children[0].session_id == "local-child-env-linkage"

    def test_no_duplicate_records(self, pm_session):
        """With env var approach, only one AgentSession record exists per child subprocess.

        Previously: pre_tool_use created dev-* AND user_prompt_submit created local-*.
        Now: only local-* record is created (by child subprocess via env var).
        """
        from agent.hooks.session_registry import register_pending

        register_pending(pm_session.session_id)

        # Simulate PreToolUse: starts pipeline stage only (no record created)
        from agent.hooks.pre_tool_use import _maybe_start_pipeline_stage

        tool_input = {"type": "dev-session", "prompt": "Stage: BUILD\nBuild it."}
        _maybe_start_pipeline_stage(tool_input, claude_uuid=FAKE_CLAUDE_UUID)

        # Simulate child subprocess: creates ONE local-* record
        parent_agent_uuid = pm_session.agent_session_id
        AgentSession.create_local(
            session_id="local-child-single-record",
            project_key="test",
            working_dir="/tmp",
            status="running",
            parent_agent_session_id=parent_agent_uuid,
        )

        # Count ALL sessions linked to parent — should be exactly 1 (local-*)
        all_children = list(AgentSession.query.filter(parent_agent_session_id=parent_agent_uuid))
        assert len(all_children) == 1
        assert all_children[0].session_id.startswith("local-")


# ── Subagent Stop Two-Lookup Pattern (issue #808) ────────────────────────────


class TestSubagentStopCompletion:
    """Verify subagent_stop uses the two-lookup pattern to find child sessions.

    The two-lookup pattern:
    1. Resolve bridge session_id from Claude UUID (session_registry.resolve)
    2. Look up parent AgentSession to get agent_session_id UUID
    3. Query children by parent_agent_session_id = agent_session_id UUID
    """

    def test_completion_query_uses_agent_session_id_uuid(self, pm_session):
        """subagent_stop finds the local-* child record via agent_session_id UUID.

        Verifies that the two-lookup pattern in _register_dev_session_completion
        correctly resolves: bridge_session_id → agent_session_id UUID → child record.
        """
        from agent.hooks.session_registry import register_pending
        from bridge.pipeline_state import PipelineStateMachine

        # Walk through prerequisite stages so BUILD can start
        sm = PipelineStateMachine(pm_session)
        sm.start_stage("ISSUE")
        sm.complete_stage("ISSUE")
        sm.start_stage("PLAN")
        sm.complete_stage("PLAN")
        sm.start_stage("CRITIQUE")
        sm.complete_stage("CRITIQUE")
        sm.start_stage("BUILD")

        register_pending(pm_session.session_id)

        # Child subprocess registered itself with the parent's agent_session_id UUID
        parent_agent_uuid = pm_session.agent_session_id
        child = AgentSession.create_local(
            session_id="local-two-lookup-test",
            project_key="test",
            working_dir="/tmp",
            status="running",
            parent_agent_session_id=parent_agent_uuid,
        )

        # subagent_stop completes the child via two-lookup pattern
        from agent.hooks.subagent_stop import _register_dev_session_completion

        _register_dev_session_completion(
            agent_id="agent-two-lookup",
            input_data={"result": "Build succeeded"},
            claude_uuid=FAKE_CLAUDE_UUID,
        )

        # Verify child was found and completed
        updated = list(AgentSession.query.filter(session_id="local-two-lookup-test"))
        assert len(updated) == 1
        assert updated[0].status == "completed"

        # Verify BUILD stage completed on parent
        parent = list(AgentSession.query.filter(session_id=pm_session.session_id))[0]
        stage_states = json.loads(parent.stage_states)
        assert stage_states.get("BUILD") == "completed"
