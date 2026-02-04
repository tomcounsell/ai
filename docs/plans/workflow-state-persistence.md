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

## Team Members

### builder
Implementation agent responsible for writing code, creating files, and making changes.

- **Role**: Implementation
- **Capabilities**: File creation, code writing, system modifications
- **Restrictions**: None

### validator
Read-only verification agent that validates implementations without making changes.

- **Role**: Validation
- **Capabilities**: File reading, test execution, verification
- **Restrictions**: No file modifications, read-only operations

## Step by Step Tasks

### 1. build-types
**Agent**: builder
**Parallel**: false
**Depends On**: None

**Actions**:
- Create `agent/workflow_types.py` file
- Implement `WorkflowStateData` Pydantic model with all fields (workflow_id, issue_number, branch_name, plan_file, phase, status, telegram_chat_id, created_at, updated_at)
- Add proper type hints and Optional fields
- Include docstrings for the model and fields
- Ensure proper datetime handling with timezone awareness

### 2. build-state
**Agent**: builder
**Parallel**: false
**Depends On**: build-types

**Actions**:
- Create `agent/workflow_state.py` file
- Implement `WorkflowState` class with all methods (\_\_init\_\_, update, save, load, from_stdin, to_stdout)
- Implement `generate_workflow_id()` helper function to create 8-character unique IDs
- Add proper error handling for file operations
- Implement stdin/stdout piping functionality
- Add directory creation logic for `agents/{workflow_id}/`
- Include comprehensive docstrings

### 3. validate-core
**Agent**: validator
**Parallel**: false
**Depends On**: build-state

**Actions**:
- Read `agent/workflow_types.py` and verify Pydantic model structure
- Read `agent/workflow_state.py` and verify class implementation
- Verify `generate_workflow_id()` returns 8-character strings
- Check that all required methods exist (update, save, load, from_stdin, to_stdout)
- Verify error handling is present
- Run unit tests if they exist
- Report any issues or confirm validation success

### 4. build-bridge-integration
**Agent**: builder
**Parallel**: false
**Depends On**: validate-core

**Actions**:
- Read `bridge/telegram_bridge.py` to understand current structure
- Import `WorkflowState` and `generate_workflow_id`
- Add workflow creation when receiving new tasks
- Update workflow state on task completion
- Add workflow_id to message handling context
- Ensure proper state persistence throughout workflow lifecycle
- Add error handling for state operations

### 5. build-sdk-integration
**Agent**: builder
**Parallel**: true
**Depends On**: validate-core

**Actions**:
- Read `agent/sdk_client.py` to understand session management
- Add workflow_id parameter to SDK session creation
- Update session management to track workflow state
- Ensure workflow_id is passed through conversation context
- Add state persistence hooks in appropriate SDK lifecycle methods
- Add error handling for workflow state operations

### 6. validate-integration
**Agent**: validator
**Parallel**: false
**Depends On**: build-bridge-integration, build-sdk-integration

**Actions**:
- Read updated `bridge/telegram_bridge.py` and verify workflow integration
- Read updated `agent/sdk_client.py` and verify session management changes
- Verify workflow_id flows correctly from bridge to SDK
- Check that state is created, updated, and persisted correctly
- Verify error handling exists at integration points
- Run integration tests if available
- Test end-to-end flow: create workflow -> track state -> persist
- Report any issues or confirm validation success

### 7. build-directory-setup
**Agent**: builder
**Parallel**: false
**Depends On**: validate-integration

**Actions**:
- Create `agents/` directory at project root
- Add `.gitkeep` or README to track directory in git
- Create example workflow directory structure
- Add `.gitignore` entries for `agents/*/state.json` and `agents/*/logs/`
- Document directory structure in code comments or README
- Verify directory permissions are correct

### 8. validate-all
**Agent**: validator
**Parallel**: false
**Depends On**: build-directory-setup

**Actions**:
- Verify `agents/` directory exists with proper structure
- Read all modified files and verify complete implementation
- Check that workflow state can be created, saved, loaded, and piped
- Verify bridge creates workflows correctly
- Verify SDK tracks workflow_id in sessions
- Run full test suite (unit + integration if available)
- Test complete workflow lifecycle end-to-end
- Verify all acceptance criteria from the plan are met
- Report final validation status

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
