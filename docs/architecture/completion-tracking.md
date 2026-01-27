# Work Completion & Branch-Based Tracking

## Problem

When Valor receives work via Telegram (e.g., "update README and create docs plans"), the SDK agent runs in a long-lived session (potentially 2+ hours). Without persistent state tracking:

1. **Session state is ephemeral** - If bridge restarts, work-in-progress is lost
2. **No visibility of pending work** - User can't see what's incomplete when looking at repo
3. **Follow-up context lost** - Hard to resume work after interruption
4. **No verification of success criteria** - Work might be "done" but not meeting quality standards

## Solution

### Branch-Based Work Tracking

Git branches serve as persistent state containers for work-in-progress. Each significant work request gets its own feature branch with a plan document.

**Key components:**

1. **Feature Branches**: Each work unit = one `feature/YYYYMMDD-description` branch
2. **Plan Document**: First commit in branch = `docs/plans/ACTIVE-branch-name.md`
3. **Branch State Checker**: `agent/branch_manager.py` → Detects in-progress work
4. **Completion Criteria**: `CLAUDE.md` → Single source of truth loaded into agent prompt
5. **Completion = Merge**: Work done when branch merged to main, plan deleted

### Completion Token System

Work is marked **COMPLETE** when:
- All quality checks pass (from `CLAUDE.md`)
- Branch merged to main
- Plan document removed
- Repository back on main branch

### Completion Criteria (from CLAUDE.md)

All must pass:

- ✅ **Deliverable exists and works** - Code runs, feature behaves as specified
- ✅ **Code quality met** - Linted (ruff/black), type-checked, no TODOs
- ✅ **Changes committed** - All work committed and pushed to remote
- ✅ **Artifacts created** - Plans/docs/PRs exist as claimed
- ✅ **Original request fulfilled** - Success criteria met, no blockers
- ✅ **Branch merged to main** - Feature branch cleaned up, back on main

### How It Works

#### Session Start Flow

```
New message arrives →
  Check current branch:
    - On main? → Clean state, initialize branch if multi-step work
    - On feature/*? → Work in progress, notify user
      - User says "continue" → Resume work on this branch
      - User sends new request → Return to main, start fresh
```

#### Branch Initialization (Multi-Step Work)

When work requires multiple steps:

1. **Create feature branch**: `feature/20260121-update-readme`
2. **Generate plan doc**: `docs/plans/ACTIVE-20260121-update-readme.md`
3. **First commit**: Plan with success criteria
4. **Agent works**: Makes changes, commits progress
5. **Completion**: Merge to main, delete plan, mark COMPLETE

#### Agent Perspective

The agent receives completion criteria and branch context via system prompt:

```
[SOUL.md content]

---

## Work Completion Criteria
[Full criteria section from CLAUDE.md]
```

When work is done, agent should report:

```markdown
## Work Completion Summary

**Status**: ✅ COMPLETE

### Completion Checks:
✅ Deliverable Exists and Works
✅ Code Quality Standards Met
✅ Changes Committed (abc1234)
✅ Artifacts Created
✅ Original Request Fulfilled

### Artifacts Created:
- `docs/plans/feature-x.md`
- `src/feature.py`
- https://github.com/org/repo/pull/123

### Summary:
Updated README with docs link, created 5 documentation plans...
```

#### Bridge Perspective

1. **Message arrives** → Check git branch state
2. **Branch state determines action**:
   - `CLEAN` (on main, no active plan) → Initialize new branch if needed
   - `IN_PROGRESS` (feature branch exists) → Notify user, ask continue or fresh
   - `BLOCKED` (uncommitted on main) → Warn user
3. **Response received** → Parse for completion markers
4. **On completion** → Verify branch merged, plan deleted

#### User Perspective

- **During work**: May see "I'm working on this" after 3 min if silent
- **When complete**: Receives completion summary with checks and artifacts
- **Follow-up messages**: System knows to continue or start fresh based on status

## Implementation

### Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Completion criteria (single source of truth) |
| `agent/completion.py` | Verification logic, criteria parser |
| `agent/sdk_client.py` | Injects criteria into system prompt |
| `bridge/telegram_bridge.py` | Session management based on completion status |

### Completion Check Functions

```python
from agent import verify_completion, CompletionResult

result = verify_completion(
    working_dir=Path("/path/to/project"),
    artifacts=["README.md", "docs/plan.md", "https://github.com/org/repo/pull/123"],
    summary="Updated README and created docs plans"
)

print(result.status)  # "COMPLETE" | "IN_PROGRESS" | "BLOCKED"
print(result.all_checks_passed)  # True/False
print(result.format_summary())  # Markdown summary
```

### Session Continuity

**Scenario 1: Work incomplete, user checks in**
```
User: "@valor how's it going?"
Bridge: Checks session metadata → status=IN_PROGRESS
Bridge: Resumes session with reminder of completion criteria
Agent: "Still working on the docs plans, 3 of 5 done..."
```

**Scenario 2: Work complete, user sends new task**
```
User: "Now add a CI workflow"
Bridge: Checks session metadata → status=COMPLETE
Bridge: Starts fresh session (new session_id)
Agent: Begins new work with clean slate
```

**Scenario 3: Work complete, user replies to continue**
```
User: [Replies to completed work message] "Also update the tests"
Bridge: Detects reply to Valor message → is_reply_to_valor=True
Bridge: Resumes session despite COMPLETE status (explicit continuation)
Agent: "Adding test updates to the existing work..."
```

## Why This Design

### Single Source of Truth (CLAUDE.md)

**Problem**: If criteria exist in multiple places (code comments, docs, prompts), they diverge.

**Solution**: CLAUDE.md is the canonical definition. Code reads from it programmatically.

**Benefits**:
- Update once, changes propagate everywhere
- Developers and agents see same criteria
- Version controlled with clear history

### Criteria in System Prompt

**Problem**: How does the agent know what "complete" means?

**Solution**: Inject criteria into system prompt on every session start.

**Benefits**:
- Agent has criteria in context for every decision
- No special tool needed - agent can self-assess
- Works with SDK's existing architecture

### Explicit Completion Reporting

**Problem**: Parsing "Done!" vs "Done with X, still working on Y" is ambiguous.

**Solution**: Agent reports structured completion summary with all checks.

**Benefits**:
- Bridge can parse reliably
- User sees what passed/failed
- Clear record in logs

## Testing

```bash
# Test completion checker
pytest tests/test_completion.py

# Test criteria loading
python -c "from agent import load_completion_criteria; print(load_completion_criteria())"

# Test verification
python -c "
from pathlib import Path
from agent import verify_completion
result = verify_completion(
    Path.cwd(),
    artifacts=['README.md'],
    summary='Test'
)
print(result.format_summary())
"
```

## Future Enhancements

1. **Programmatic tool call**: Once SDK supports custom tools, add `mark_complete()` as MCP tool
2. **Automated quality checks**: Run ruff/black/mypy and include results
3. **Test execution**: Run tests and report pass/fail
4. **PR validation**: Check if PR is mergeable, approved, etc.
5. **Time tracking**: Log how long work took from start to completion

## Migration Notes

**Before**: No explicit completion, relied on agent saying "done" in prose.

**After**: Structured completion with verification checks.

**Breaking changes**: None - additive only. Old sessions continue working.

**Rollout**: Enable via flag or gradual adoption per project.
