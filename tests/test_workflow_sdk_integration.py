"""Test workflow state integration with SDK client."""

from pathlib import Path

import pytest

from agent.sdk_client import ValorAgent
from agent.workflow_state import WorkflowState, generate_workflow_id


class TestWorkflowSDKIntegration:
    """Test suite for workflow state integration with SDK client."""

    def test_agent_without_workflow_id(self):
        """Test ValorAgent creation without workflow_id."""
        agent = ValorAgent()
        assert agent.workflow_id is None
        assert agent.workflow_state is None
        assert agent.get_workflow_data() is None

    def test_agent_with_nonexistent_workflow_id(self):
        """Test ValorAgent with non-existent workflow_id."""
        agent = ValorAgent(workflow_id="nonexist")
        assert agent.workflow_id == "nonexist"
        assert agent.workflow_state is None
        assert agent.get_workflow_data() is None

    def test_agent_with_existing_workflow(self):
        """Test ValorAgent loads existing workflow state."""
        # Create workflow state
        wf_id = generate_workflow_id()
        state = WorkflowState(wf_id)
        state.update(
            plan_file="docs/plans/test.md",
            tracking_url="https://github.com/test/123",
            phase="plan",
            status="in_progress",
        )
        state.save()

        try:
            # Create agent with this workflow_id
            agent = ValorAgent(workflow_id=wf_id)

            # Verify workflow state loaded
            assert agent.workflow_id == wf_id
            assert agent.workflow_state is not None
            assert agent.workflow_state.workflow_id == wf_id

            # Verify workflow data
            data = agent.get_workflow_data()
            assert data is not None
            assert data.workflow_id == wf_id
            assert data.plan_file == "docs/plans/test.md"
            assert data.tracking_url == "https://github.com/test/123"
            assert data.phase == "plan"
            assert data.status == "in_progress"

        finally:
            # Clean up
            state_file = Path(f"/Users/valorengels/src/ai/agents/{wf_id}/state.json")
            if state_file.exists():
                state_file.unlink()
                state_file.parent.rmdir()

    def test_workflow_context_generation(self):
        """Test workflow context is generated correctly."""
        # Create workflow state
        wf_id = generate_workflow_id()
        state = WorkflowState(wf_id)
        state.update(
            plan_file="docs/plans/feature.md",
            tracking_url="https://github.com/test/456",
            phase="build",
            status="in_progress",
            branch_name="feature/test-branch",
        )
        state.save()

        try:
            # Create agent
            agent = ValorAgent(workflow_id=wf_id)

            # Generate workflow context
            context = agent._build_workflow_context()

            # Verify context contains expected information
            assert "WORKFLOW CONTEXT" in context
            assert wf_id in context
            assert "docs/plans/feature.md" in context
            assert "build" in context
            assert "in_progress" in context
            assert "feature/test-branch" in context
            assert "https://github.com/test/456" in context

        finally:
            # Clean up
            state_file = Path(f"/Users/valorengels/src/ai/agents/{wf_id}/state.json")
            if state_file.exists():
                state_file.unlink()
                state_file.parent.rmdir()

    def test_update_workflow_state(self):
        """Test updating workflow state through ValorAgent."""
        # Create workflow state
        wf_id = generate_workflow_id()
        state = WorkflowState(wf_id)
        state.update(
            plan_file="docs/plans/test.md",
            tracking_url="https://github.com/test/789",
            phase="plan",
            status="pending",
        )
        state.save()

        try:
            # Create agent
            agent = ValorAgent(workflow_id=wf_id)

            # Update workflow state
            agent.update_workflow_state(phase="build", status="in_progress")

            # Verify updates
            data = agent.get_workflow_data()
            assert data.phase == "build"
            assert data.status == "in_progress"

            # Verify persistence (reload from disk)
            reloaded_state = WorkflowState.load(wf_id)
            assert reloaded_state.data.phase == "build"
            assert reloaded_state.data.status == "in_progress"

        finally:
            # Clean up
            state_file = Path(f"/Users/valorengels/src/ai/agents/{wf_id}/state.json")
            if state_file.exists():
                state_file.unlink()
                state_file.parent.rmdir()

    def test_update_workflow_state_without_workflow_id(self):
        """Test update_workflow_state raises error when no workflow_id."""
        agent = ValorAgent()

        with pytest.raises(ValueError, match="Cannot update workflow state"):
            agent.update_workflow_state(phase="build")
