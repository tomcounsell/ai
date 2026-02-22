# Build Workflow (Detailed Steps)

Step-by-step execution workflow for the build orchestrator. Read this file when executing a plan.

## Step 1: Initialize Task List

Read the plan and create tasks:

```typescript
// For each task in "Step by Step Tasks":
TaskCreate({
  subject: "[Task Name]",
  description: "[Full task details from plan]",
  activeForm: "[Description in progress form]"
})
// Note the returned taskId for dependency tracking
```

## Step 2: Set Dependencies

After creating all tasks, set up the dependency chain:

```typescript
TaskUpdate({
  taskId: "[task-id]",
  addBlockedBy: ["dependency-task-id-1", "dependency-task-id-2"]
})
```

## Step 3: Deploy Agents

For each task, deploy the assigned agent:

```typescript
Task({
  description: "[Task subject]",
  prompt: `Execute task: [Task Name]

IMPORTANT: You MUST work in the worktree directory: {absolute_path_to}/.worktrees/{slug}/
Run \`cd {absolute_path_to}/.worktrees/{slug}/\` before doing any work.
All file reads, writes, and commands should use this worktree path, not the main repo.

Plan context: [relevant plan sections]

Your assignment:
- [specific actions from task]

When complete, commit your changes and update your task status.`,
  subagent_type: "[agent type from task]",
  run_in_background: [true if Parallel: true]
})
```

## Step 4: Monitor and Coordinate

- Check `TaskList({})` to see overall progress
- Use `TaskOutput({task_id, block: false})` to check on background agents
- When a blocker completes, dependent tasks auto-unblock

**Health Monitoring for Background Agents:**

After deploying background agents, actively monitor their health:

1. Poll `TaskOutput({task_id, block: false, timeout: 30000})` for each background agent when checking progress
2. Check `TaskList` to see if tasks have moved to completed status
3. If a background agent's TaskOutput returns completion but TaskList still shows `in_progress`, use `TaskUpdate` to mark it
4. **Warning threshold (5 min):** If an agent has produced no new output for 5+ minutes, note this as a potential issue
5. **Failure threshold (15 min):** If an agent has been completely silent for 15+ minutes:
   - Attempt to resume the agent using its agentId
   - If resume fails, mark the task as failed
   - Report the failure prominently so the user is aware
6. **On any agent failure:** Commit whatever work exists in the worktree as a safety net:
   ```bash
   git -C .worktrees/{slug} add -A && git -C .worktrees/{slug} commit -m "[WIP] partial work before agent failure" || true
   ```

## Step 5: Final Validation and Definition of Done

When the final `validate-all` task completes, verify Definition of Done criteria:

**Definition of Done Checklist:**
- [x] **Built**: All code implemented and working
- [x] **Tested**: All unit tests passing, integration tests passing
- [x] **Documented**: Documentation created per plan's Documentation section
- [x] **Quality**: Ruff and Black checks pass, no lint errors
- [x] **Plans migrated**: Ready to migrate from docs/plans/ to docs/features/

If any criterion is not met, report the issue and do NOT proceed to PR creation.

## Step 5.5: CWD Safety Reset

Before running any orchestrator bash commands, verify the shell CWD is the main repo root (not inside a worktree). Run this as a sanity check:

```bash
cd $(git rev-parse --show-toplevel) && pwd
```

The output should be the main repo path, NOT a `.worktrees/` path. If the CWD is somehow inside the worktree, this resets it. All subsequent orchestrator commands depend on CWD being the repo root.

## Agent Deployment Context

When deploying an agent, include:
1. The specific task actions from the plan
2. Relevant file paths from the plan's "Relevant Files" section
3. Success criteria from the plan
4. Validation commands they should run (for validators)
5. Reminder: No temporary files in repo - use /tmp for scratch work, only commit deliverables

## Example Execution

Given a plan with tasks:
```
1. build-api (Parallel: true)
2. build-frontend (Parallel: true)
3. validate-api (Depends On: build-api)
4. validate-frontend (Depends On: build-frontend)
5. integration-test (Depends On: validate-api, validate-frontend)
```

Execution order:
1. Create all 5 tasks
2. Set dependencies
3. Deploy build-api AND build-frontend simultaneously (both parallel, no deps)
4. When build-api completes -> validate-api starts
5. When build-frontend completes -> validate-frontend starts
6. When BOTH validators complete -> integration-test starts
