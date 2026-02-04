---
status: Implemented
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2024-01-15
tracking: https://github.com/tomcounsell/ai/issues/16
---

# Workflow State Persistence

## Problem

Multi-phase workflows (plan → build → test → review) have no persistent state. When Telegram-delegated work spans multiple sessions, context is lost.

**Current behavior:**
- Each session starts fresh with no memory of previous work
- Workflow phase (plan/build/test) not tracked
- No unique identifiers for correlating related work
- Can't resume interrupted workflows

**Desired outcome:**
- Workflows have unique 8-character IDs
- State persists to `agents/{workflow_id}/state.json`
- Workflows can be resumed across sessions
- State can be piped between chained operations

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Solution

### Key Elements

- **WorkflowState class**: Container for state with file persistence, stdin/stdout piping
- **Pydantic models**: Type-safe state data with validation
- **ID generation**: 8-character unique workflow identifiers
- **Bridge integration**: Create/track workflows from Telegram messages
- **SDK integration**: Pass workflow_id through conversation context

### Flow

**Telegram message** → Create workflow (ID: abc12345) → **WorkflowState saved** → SDK session with workflow_id → **Phase updates** → State persisted → **Resume later** → Load workflow state → Continue work

### Technical Approach

- Pydantic models for type-safe state validation
- JSON file persistence in `agents/{workflow_id}/state.json`
- Stdin/stdout piping for shell workflow chaining
- Integration with existing bridge message handling
- Pass workflow_id through SDK session context

### Source Inspiration

From `indydan/tac-6/adws/adw_modules/state.py` - the `ADWState` class.

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
        ...

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
    plan_file: str  # Required - path to docs/plans/*.md
    tracking_url: str  # Required - GitHub issue or Notion task URL
    issue_number: Optional[int] = None  # GitHub issue number (if GitHub)
    branch_name: Optional[str] = None
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

## Risks

### Risk 1: Orphaned workflow states
**Impact:** Disk fills with abandoned state files
**Mitigation:** Add cleanup for states older than 30 days, or on explicit completion

### Risk 2: State file corruption
**Impact:** Workflow can't be resumed, data loss
**Mitigation:** Write to temp file first, atomic rename; add recovery/rebuild logic

### Risk 3: Integration with existing completion tracking
**Impact:** Duplicate state management, inconsistent behavior
**Mitigation:** Ensure WorkflowState complements (not replaces) existing `mark_complete` patterns

## No-Gos (Out of Scope)

- Database-backed state storage (file-based only for now)
- Multi-user workflow sharing
- Workflow history/versioning beyond current state
- Complex workflow branching/merging
- State encryption

## Success Criteria

- [x] `WorkflowState` class exists with all methods (update, save, load, from_stdin, to_stdout)
- [x] `generate_workflow_id()` creates 8-character unique IDs
- [x] State persists to `agents/{workflow_id}/state.json`
- [x] Bridge creates workflow on new Telegram tasks
- [x] SDK sessions track workflow_id
- [x] Workflows can be resumed by ID
- [x] Unit tests pass for core state operations

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (core-modules)**
  - Name: core-builder
  - Role: Implement workflow_types.py and workflow_state.py
  - Agent Type: builder
  - Resume: true

- **Validator (core-modules)**
  - Name: core-validator
  - Role: Verify core modules work correctly
  - Agent Type: validator
  - Resume: true

- **Builder (bridge-integration)**
  - Name: bridge-builder
  - Role: Integrate workflow state with Telegram bridge
  - Agent Type: builder
  - Resume: true

- **Builder (sdk-integration)**
  - Name: sdk-builder
  - Role: Integrate workflow state with SDK client
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify bridge and SDK integrations work together
  - Agent Type: validator
  - Resume: true

- **Builder (directory-setup)**
  - Name: setup-builder
  - Role: Create agents/ directory structure
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Complete end-to-end validation
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build Workflow Types
- **Task ID**: build-types
- **Depends On**: none
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/workflow_types.py`
- Implement `WorkflowStateData` Pydantic model with all fields
- Add proper type hints, Optional fields, and datetime handling
- Include docstrings

### 2. Build Workflow State
- **Task ID**: build-state
- **Depends On**: build-types
- **Assigned To**: core-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/workflow_state.py`
- Implement `WorkflowState` class with all methods
- Implement `generate_workflow_id()` for 8-char IDs
- Add error handling and directory creation logic

### 3. Validate Core Modules
- **Task ID**: validate-core
- **Depends On**: build-state
- **Assigned To**: core-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify Pydantic model structure
- Verify WorkflowState class implementation
- Test generate_workflow_id() returns 8-char strings
- Check all required methods exist

### 4. Build Bridge Integration
- **Task ID**: build-bridge
- **Depends On**: validate-core
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Import WorkflowState in bridge/telegram_bridge.py
- Create workflow on new task receipt
- Update workflow state on completion
- Add workflow_id to message handling context

### 5. Build SDK Integration
- **Task ID**: build-sdk
- **Depends On**: validate-core
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Add workflow_id parameter to SDK session creation
- Track workflow state in session management
- Pass workflow_id through conversation context

### 6. Validate Integrations
- **Task ID**: validate-integration
- **Depends On**: build-bridge, build-sdk
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify workflow_id flows from bridge to SDK
- Check state creation, update, and persistence
- Verify error handling at integration points

### 7. Setup Directory Structure
- **Task ID**: build-directory
- **Depends On**: validate-integration
- **Assigned To**: setup-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agents/` directory with README
- Add .gitignore entries for state files and logs
- Document directory structure

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-directory
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify complete implementation
- Test workflow lifecycle end-to-end
- Confirm all success criteria met
- Generate final report

## Validation Commands

- `python -c "from agent.workflow_types import WorkflowStateData; print('types OK')"` - verify types import
- `python -c "from agent.workflow_state import WorkflowState, generate_workflow_id; print(generate_workflow_id())"` - verify state module
- `pytest tests/agent/test_workflow_state.py -v` - run unit tests (if created)
- `ls -la agents/` - verify directory structure

## Design Decisions

1. **Retention**: Keep the 12 most recent workflows resumable. Older workflows can be cleaned up.
2. **Creation criteria**: Workflows are only created for tracked work - must have both a plan document (`docs/plans/*.md`) AND a tracking issue (GitHub) or task (Notion). Casual messages don't get workflows.
3. **Visibility**: workflow_id is internal only, not exposed to users in Telegram.
