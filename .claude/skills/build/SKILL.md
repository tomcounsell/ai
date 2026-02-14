---
name: build
description: Execute a plan document using team orchestration. Deploys builder/validator agent pairs to complete tasks in order, with parallel execution where specified.
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
4. **Create an isolated worktree** - Create `.worktrees/{slug}/` with branch `session/{slug}`:
   ```bash
   # Builds use session/{slug} branch convention — builds are just a skill invoked
   # within a session. Planning and building can happen in the same session,
   # so there's no reason for a separate build/ branch prefix.
   git worktree add .worktrees/{slug} -b session/{slug} main
   # Copy settings that aren't tracked by git
   cp .claude/settings.local.json .worktrees/{slug}/.claude/settings.local.json 2>/dev/null || true
   ```
   All subsequent agent work happens inside `.worktrees/{slug}/`, NOT the main repo directory. Derive `{slug}` from the plan filename.
5. **Parse the Team Members** and Step by Step Tasks sections
6. **Create all tasks** using `TaskCreate` before starting execution
7. **Deploy agents** in order, respecting dependencies and parallel flags (agents follow SDLC: Build → Test loop with up to 5 iterations)
8. **Monitor progress** and handle any issues
9. **Verify Definition of Done** - Ensure all tasks completed with: code working, tests passing, docs created, quality checks pass
10. **Run documentation gate** - Validate docs changed, scan related docs, create review issues
11. **Push and open a PR** - `git -C .worktrees/{slug} push -u origin session/{slug}` then `gh pr create`
12. **Run documentation cascade** - Invoke `/update-docs {PR-number}` to surgically update affected docs
13. **Migrate completed plan** - Delete plan file and close tracking issue
14. **Report completion** with PR URL when all tasks are done

## Critical Rules

- **You are the orchestrator, not a builder** - Never use Write/Edit tools directly
- **Deploy agents via Task tool** - Each task in the plan becomes a Task tool call
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together** - Tasks with `Parallel: true` and no blocking dependencies can run simultaneously
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.
- **Never cd into worktrees** - The orchestrator's CWD must stay in the main repo. Use `git -C .worktrees/{slug}` for git commands and `--head session/{slug}` for `gh pr create`. Only subagents (Task tool) should cd into worktrees — their shell sessions are independent and disposable. If the orchestrator cd's into a worktree and then deletes it, the shell breaks permanently.
- **SDLC enforcement** - All builder agents follow Plan → Build → Test → Review → Ship with test failure loops (up to 5 iterations)
- **Definition of Done** - Tasks are complete only when: Built (code working), Tested (tests pass), Documented (docs created), Quality (lint/format pass)

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

### Step 4: Monitor and Coordinate

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

### Step 5: Final Validation and Definition of Done

When the final `validate-all` task completes, verify Definition of Done criteria:

**Definition of Done Checklist:**
- [x] **Built**: All code implemented and working
- [x] **Tested**: All unit tests passing, integration tests passing
- [x] **Documented**: Documentation created per plan's Documentation section
- [x] **Quality**: Ruff and Black checks pass, no lint errors
- [x] **Plans migrated**: Ready to migrate from docs/plans/ to docs/features/

If any criterion is not met, report the issue and do NOT proceed to PR creation.

### Step 6: Documentation Gate

After all validation tasks pass, run the documentation lifecycle checks:

**6.1 Validate Documentation Changes**

Run the doc validation script to verify documentation was created/updated:

```bash
python scripts/validate_docs_changed.py {PLAN_PATH}
```

- **Exit 0**: Documentation requirements met, proceed to next step
- **Exit 1**: Documentation missing or insufficient, **STOP and report failure**
- This check BLOCKS PR creation if it fails
- The script checks that documentation matching the plan was created in `docs/features/` or `docs/`

**6.2 Scan for Related Documentation**

Collect all changed files from git and scan for related docs:

```bash
CHANGED_FILES=$(git diff --name-only main...HEAD | tr '\n' ' ')
python scripts/scan_related_docs.py --json $CHANGED_FILES > /tmp/related_docs.json
```

This identifies existing documentation that may need updates based on code changes.

**6.3 Create Review Issues for Discrepancies**

Pipe the scan results to create GitHub issues for HIGH/MED-HIGH confidence matches:

```bash
cat /tmp/related_docs.json | python scripts/create_doc_review_issue.py
```

This creates tracking issues for documentation that should be reviewed for updates.

### Step 7: Create Pull Request

After documentation gate passes, push and create the PR:

```bash
git -C .worktrees/{slug} push -u origin session/{slug}
gh pr create --head session/{slug} --title "[plan title]" --body "$(cat <<'EOF'
## Summary
[Brief description of what was built]

## Changes
- [List key changes made]

## Testing
- [x] Unit tests passing
- [x] Integration tests passing
- [x] Linting (ruff, black) passing

## Documentation
- [x] Docs created per plan requirements
- [x] Related docs scanned for updates

## Definition of Done
- [x] Built: Code implemented and working
- [x] Tested: All tests passing
- [x] Documented: Docs created/updated
- [x] Quality: Lint and format checks pass

Closes #[issue-number]
EOF
)"
```

**Important**: The PR creation step is handled by the BUILD ORCHESTRATOR (this skill), NOT by individual builder agents. Builder agents focus on their assigned tasks, while the orchestrator creates the final PR after all tasks complete and gates pass.

### Step 7.5: Worktree Cleanup

After pushing and creating the PR, clean up the worktree:

```bash
git worktree remove .worktrees/{slug} --force
git worktree prune
```

### Step 7.6: Documentation Cascade

After the PR is created, run the `/update-docs` cascade to find and surgically update any existing documentation affected by the code changes in this build. Pass the PR number so the cascade can inspect the full diff:

```
/update-docs {PR-number}
```

This invokes the cascade command defined in `.claude/commands/update-docs.md`, which:
- Launches parallel agents to explore the change diff and inventory all docs
- Cross-references changes against every doc in the repo (triage questions)
- Makes targeted surgical edits to affected docs (read before edit, preserve structure)
- Creates GitHub issues for conflicts needing human review
- Commits any doc updates to the PR branch before merge

**Note**: The cascade is best-effort. If it finds nothing to update, that's fine — proceed to plan migration. If it makes edits, those are committed directly to the PR branch.

### Step 8: Plan Migration

After PR is successfully created and documentation cascade completes, clean up the completed plan:

```bash
python scripts/migrate_completed_plan.py {PLAN_PATH}
```

This deletes the plan document and closes the tracking issue, completing the lifecycle.

### Step 9: Report PR Link

After plan migration completes, include the PR URL prominently in your final response. When running via Telegram bridge, the agent's response (containing the PR link) will be automatically sent back to the chat where the build was initiated. No special action required - just ensure the PR URL is visible in your completion report.

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

### Definition of Done
- [x] Built: All code implemented and working
- [x] Tested: Unit tests passing, integration tests passing
- [x] Documented: Docs created per plan requirements (validated by docs gate)
- [x] Quality: Ruff and Black checks pass
- [x] Plans migrated: Plan moved from docs/plans/ to completed state

### Task Summary
| Task | Agent | Status | Test Iterations | Notes |
|------|-------|--------|----------------|-------|
| [name] | [agent] | Done | [N] | [brief note] |

### Validation Results
- [x] All build tasks completed
- [x] All validators passed
- [x] Documentation gate passed
- [x] Documentation cascade completed (`/update-docs`)
- [x] Success criteria met

### Artifacts Created
- [list of files created/modified]

### Next Steps
- Review and merge PR: [PR URL]
- PR link has been sent to Telegram chat
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
