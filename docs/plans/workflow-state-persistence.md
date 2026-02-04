---
tracking: https://github.com/tomcounsell/ai/issues/16
type: feature
---

# Plan: Workflow State Persistence

## Overview

Add a state management system for tracking multi-phase workflows that may span multiple sessions or be delegated via Telegram.

## Source Inspiration

From `indydan/tac-6/adws/adw_modules/state.py` - the `ADWState` class.

## Problem Statement

Currently, Valor lacks:
- Persistent state for multi-phase tasks (plan -> build -> test -> review)
- Unique workflow identifiers for tracking
- Ability to resume workflows across sessions
- State passing between chained operations

This is especially problematic for Telegram-delegated work that might need multiple interactions.

## Proposed Solution

Create a `WorkflowState` class that:
- Assigns unique 8-character IDs to workflows
- Persists state to `agents/{workflow_id}/state.json`
- Supports stdin/stdout piping for chaining
- Integrates with the completion tracking already in CLAUDE.md

### New Files to Create

```
agent/
  workflow_state.py     # Core state management class
  workflow_types.py     # Pydantic models for state data
```

### WorkflowState Class Design

```python
class WorkflowState:
    """Container for workflow state with file persistence."""

    STATE_FILENAME = "state.json"

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id
        self.data: Dict[str, Any] = {"workflow_id": workflow_id}

    def update(self, **kwargs):
        """Update state with new key-value pairs."""
        core_fields = {
            "workflow_id", "issue_number", "branch_name",
            "plan_file", "phase", "status", "telegram_chat_id"
        }
        for key, value in kwargs.items():
            if key in core_fields:
                self.data[key] = value

    def save(self, phase: Optional[str] = None) -> None:
        """Save state to agents/{workflow_id}/state.json."""
        ...

    @classmethod
    def load(cls, workflow_id: str) -> Optional["WorkflowState"]:
        """Load existing state from file."""
        ...

    @classmethod
    def from_stdin(cls) -> Optional["WorkflowState"]:
        """Read state from stdin for piped workflows."""
        ...

    def to_stdout(self):
        """Output state to stdout for piping to next phase."""
        ...
```

### State Data Model

```python
class WorkflowStateData(BaseModel):
    workflow_id: str
    issue_number: Optional[int] = None
    branch_name: Optional[str] = None
    plan_file: Optional[str] = None
    phase: Optional[str] = None  # plan, build, test, review, document
    status: Optional[str] = None  # pending, in_progress, completed, failed
    telegram_chat_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
```

### Directory Structure

```
agents/
  {workflow_id}/
    state.json          # Persistent workflow state
    plan.md             # Generated plan (if applicable)
    logs/               # Phase-specific logs
      plan.log
      build.log
      test.log
```

## Integration Points

1. **Telegram Bridge**: Create workflow state when receiving task, update on completion
2. **SDK Client**: Pass workflow_id to track conversations
3. **Completion Tracking**: Link to existing `mark_complete` criteria in CLAUDE.md

## Implementation Steps

1. Create `agent/workflow_types.py` with Pydantic models
2. Create `agent/workflow_state.py` with `WorkflowState` class
3. Add helper function `generate_workflow_id()` for 8-char IDs
4. Update `bridge/telegram_bridge.py` to create/track workflows
5. Add workflow state to SDK session management
6. Create `agents/` directory structure

## Benefits

- Track long-running tasks across sessions
- Enable workflow chaining (plan | build | test)
- Resume interrupted workflows
- Better visibility into Telegram-delegated work
- Foundation for SDLC automation

## Estimated Effort

Medium-High - Core infrastructure change

## Dependencies

- Pydantic (already installed)

## Risks

- Need to handle orphaned workflow states
- State file corruption recovery
- Integration with existing completion tracking
