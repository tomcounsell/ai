---
name: do-build
description: "Use when executing a plan document to ship a feature. Triggered by 'build this', 'execute the plan', 'implement the plan', or any request to run/ship a plan."
context: fork
---

# Build (Plan Execution)

You are the **team lead** executing a plan document. You orchestrate work using Task tools - you NEVER build directly.

## What this skill does

1. Resolves a plan document (by path or issue number)
2. Creates an isolated worktree for the build
3. Deploys builder/validator agent teams to execute the plan
4. Runs documentation gates and quality checks
5. Opens a PR and migrates the completed plan

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `WORKFLOW.md` | Starting execution (Steps 1-5: task creation, agent deployment, monitoring) |
| `PR_AND_CLEANUP.md` | All build tasks complete and validated (Steps 6-9: docs gate, PR, cleanup, migration) |

## Invocation Methods

1. **By plan path**: `/do-build docs/plans/my-feature.md`
2. **By issue number**: `/do-build #17` or `/do-build 17`

## Variables

PLAN_ARG: $1

## Plan Resolution

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
   git worktree add .worktrees/{slug} -b session/{slug} main
   cp .claude/settings.local.json .worktrees/{slug}/.claude/settings.local.json 2>/dev/null || true
   ```
   All subsequent agent work happens inside `.worktrees/{slug}/`, NOT the main repo directory. Derive `{slug}` from the plan filename.
5. **Parse the Team Members** and Step by Step Tasks sections
6. **Execute the workflow** - Load `WORKFLOW.md` for Steps 1-5 (task creation, deployment, monitoring, validation)
7. **PR and cleanup** - Load `PR_AND_CLEANUP.md` for Steps 6-9 (docs gate, PR creation, worktree cleanup, plan migration)

## Critical Rules

- **You are the orchestrator, not a builder** - Never use Write/Edit tools directly
- **Deploy agents via Task tool** - Each task in the plan becomes a Task tool call
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together** - Tasks with `Parallel: true` and no blocking dependencies can run simultaneously
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.
- **Never cd into worktrees** - The orchestrator's CWD must stay in the main repo. Use `git -C .worktrees/{slug}` for git commands, subshells `(cd .worktrees/{slug} && ...)` when Python scripts need worktree CWD, and `--head session/{slug}` for `gh pr create`. Only subagents (Task tool) should have bare `cd` into worktrees -- their shell sessions are independent and disposable. If the orchestrator's CWD ends up inside a worktree and that worktree is deleted, the shell breaks permanently and cannot recover.
- **SDLC enforcement** - All builder agents follow Plan -> Build -> Test -> Review -> Ship with test failure loops (up to 5 iterations)
- **Definition of Done** - Tasks are complete only when: Built (code working), Tested (tests pass), Documented (docs created), Quality (lint/format pass)

## Example Invocations

**By file path:**
```
/do-build docs/plans/implement-auth.md
```

**By issue number:**
```
/do-build #42
/do-build 42
```

Both methods will execute the same plan if the plan file has:
```yaml
tracking: https://github.com/valor-labs/ai/issues/42
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
