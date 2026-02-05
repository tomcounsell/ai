---
description: Execute a plan document using team orchestration. Deploys builder/validator agent pairs to complete tasks in order, with parallel execution where specified.
argument-hint: <path-to-plan.md or #issue-number>
model: sonnet
disallowed-tools: Write, Edit, NotebookEdit
---

# Build (Plan Execution)

You are the **team lead** executing a plan document. You orchestrate work using Task tools - you NEVER build directly.

## Invocation Methods

The build skill supports two invocation methods:

1. **By plan path**: `/build docs/plans/my-feature.md`
2. **By issue number**: `/build #17` or `/build 17`

When invoked with an issue number, the skill will:
- Search all files in `docs/plans/*.md`
- Find the plan with `tracking: https://github.com/{org}/{repo}/issues/{N}` in frontmatter
- Use that plan for execution
- Error if no match or multiple matches found

## Variables

PLAN_ARG: $1

## Plan Resolution

Before executing, resolve the plan path:

**Step 1: Detect argument type**
- If `PLAN_ARG` starts with `#` or is a pure number, treat as issue number
- Otherwise, treat as file path

**Step 2A: If issue number**
1. Extract the number (strip `#` if present)
2. Use Glob tool to find all plan files: `docs/plans/*.md`
3. Read each plan and check frontmatter for `tracking:` field
4. Match pattern: `/issues/{NUMBER}` where NUMBER equals the argument
5. If exactly one match: use that plan path
6. If no matches: Error - "No plan found tracking issue #{N}"
7. If multiple matches: Error - "Multiple plans found tracking issue #{N}: [list paths]"

**Step 2B: If file path**
- Use `PLAN_ARG` directly as `PLAN_PATH`
- Verify file exists (will error naturally if not)

**Step 3: Set PLAN_PATH**
- `PLAN_PATH` now contains the resolved absolute path to the plan document

## Instructions

1. **Resolve the plan path** using the Plan Resolution logic above
2. **Read the plan** at `PLAN_PATH`
3. **Run prerequisite validation** - `python scripts/check_prerequisites.py {PLAN_PATH}`. If any check fails, report the failures and stop. Do not proceed to task execution. If no Prerequisites section exists, this passes automatically.
4. **Create a feature branch** - `git checkout -b build/{slug}` (derive slug from the plan filename)
5. **Parse the Team Members** and Step by Step Tasks sections
6. **Create all tasks** using `TaskCreate` before starting execution
7. **Deploy agents** in order, respecting dependencies and parallel flags
8. **Monitor progress** and handle any issues
9. **Push and open a PR** - `git push -u origin build/{slug}` then `gh pr create`
10. **Report completion** with PR URL when all tasks are done

## Critical Rules

- **You are the orchestrator, not a builder** - Never use Write/Edit tools directly
- **Deploy agents via Task tool** - Each task in the plan becomes a Task tool call
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together** - Tasks with `Parallel: true` and no blocking dependencies can run simultaneously
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.

## Workflow

### Step 1: Initialize Task List

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

### Step 2: Set Dependencies

After creating all tasks, set up the dependency chain:

```typescript
TaskUpdate({
  taskId: "[task-id]",
  addBlockedBy: ["dependency-task-id-1", "dependency-task-id-2"]
})
```

### Step 3: Deploy Agents

For each task, deploy the assigned agent:

```typescript
Task({
  description: "[Task subject]",
  prompt: `Execute task: [Task Name]

Plan context: [relevant plan sections]

Your assignment:
- [specific actions from task]

When complete, update your task status.`,
  subagent_type: "[agent type from task]",
  model: "sonnet",  // or "opus" for complex work
  run_in_background: [true if Parallel: true]
})
```

### Step 4: Monitor and Coordinate

- Check `TaskList({})` to see overall progress
- Use `TaskOutput({task_id, block: false})` to check on background agents
- When a blocker completes, dependent tasks auto-unblock

### Step 5: Final Validation

When the final `validate-all` task completes, generate the completion report.

## Agent Deployment Context

When deploying an agent, include:
1. The specific task actions from the plan
2. Relevant file paths from the plan's "Relevant Files" section
3. Success criteria from the plan
4. Validation commands they should run (for validators)
5. Reminder: No temporary files in repo - use /tmp for scratch work, only commit deliverables

## Example Invocations

**By file path:**
```
/build docs/plans/implement-auth.md
```

**By issue number:**
```
/build #42
/build 42
```

Both methods will execute the same plan if the plan file has:
```yaml
tracking: https://github.com/valor-labs/ai/issues/42
```

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
4. When build-api completes → validate-api starts
5. When build-frontend completes → validate-frontend starts
6. When BOTH validators complete → integration-test starts

## Report Format

After all tasks complete:

```
## Plan Execution Complete

**Plan**: [plan name]
**Pull Request**: [PR URL]
**Total Tasks**: [count]

### Task Summary
| Task | Agent | Status | Notes |
|------|-------|--------|-------|
| [name] | [agent] | Done | [brief note] |

### Validation Results
- [x] All build tasks completed
- [x] All validators passed
- [x] Success criteria met

### Artifacts Created
- [list of files created/modified]

### Next Steps
- Review and merge PR: [PR URL]
- [Any follow-up items or manual steps needed]
```

## Error Handling

If a task fails:
1. Check the agent's output for details
2. Decide: retry, skip, or abort
3. For validators: if validation fails, report what's wrong
4. Don't proceed past blocking failures

## Notes

- The plan document is the source of truth
- Agents can be resumed using their agentId if they need to continue work
- Background agents continue running even if you move to other tasks
- Use `TaskStop` only if you need to abort a runaway agent
