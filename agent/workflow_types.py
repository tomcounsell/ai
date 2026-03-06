"""Workflow state management types for multi-phase workflows.

Provides Pydantic models for persistent workflow state management,
including plan → build → test → review workflows with unique workflow IDs.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Workflow phases
WorkflowPhase = Literal["plan", "build", "test", "review", "document"]

# Workflow status
WorkflowStatus = Literal["pending", "in_progress", "completed", "failed"]


class WorkflowStateData(BaseModel):
    """Persistent state for multi-phase workflows.

    Stored in agents/{workflow_id}/state.json
    Contains essential identifiers and state to track workflow progression
    through plan → build → test → review phases.

    Attributes:
        workflow_id: Unique 8-character workflow identifier
        plan_file: Path to the plan document in docs/plans/*.md (required)
        tracking_url: GitHub issue or Notion task URL for tracking (required)
        issue_number: GitHub issue number if using GitHub tracking
        branch_name: Git branch name for this workflow
        phase: Current workflow phase
        status: Current workflow status
        telegram_chat_id: Telegram chat ID for notifications
        created_at: Workflow creation timestamp
        updated_at: Last update timestamp
    """

    workflow_id: str = Field(..., description="Unique 8-character workflow identifier")
    plan_file: str = Field(..., description="Path to docs/plans/*.md plan document")
    tracking_url: str = Field(
        ..., description="GitHub issue or Notion task URL for tracking"
    )
    issue_number: int | None = Field(
        None, description="GitHub issue number if GitHub tracking"
    )
    branch_name: str | None = Field(None, description="Git branch name")
    phase: WorkflowPhase | None = Field(None, description="Current workflow phase")
    status: WorkflowStatus | None = Field(None, description="Current workflow status")
    telegram_chat_id: int | None = Field(
        None, description="Telegram chat ID for notifications"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Workflow creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    class Config:
        """Pydantic model configuration."""

        json_encoders = {datetime: lambda v: v.isoformat()}
        populate_by_name = True
