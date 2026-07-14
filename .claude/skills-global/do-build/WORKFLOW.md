# Build Workflow (Detailed Steps)

Step-by-step execution workflow for the build orchestrator. Read this file when executing a plan.

## Step 0: Stage Marker (only if the context file declares a substrate)

If the context file declares an orchestration substrate (a pipeline state
machine + stage markers), write the BUILD `in_progress` marker now and follow
its degraded-mode handling — a forked sub-skill announces degraded mode rather
than silently lagging state. The build itself (worktree, agents, tests, PR) never
depends on the substrate, so a missing or degraded substrate never blocks it.

In the generic case (no substrate declared), skip this step.

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

IMPORTANT: You MUST work in the worktree directory: {TARGET_REPO}/.worktrees/{slug}/
Run \`cd {TARGET_REPO}/.worktrees/{slug}/\` before doing any work.
All file reads, writes, and commands should use this worktree path, not the main repo.
Note: {TARGET_REPO} is the target repository root (which may differ from the orchestrator repo for cross-repo builds).

NEVER use \`git checkout\` or \`git checkout -b\` on session/ branches. The worktree IS the checkout — just \`cd\` into it. Running \`git checkout session/{slug}\` will fail with a fatal error because the branch is locked by the worktree.

Plan context: [relevant plan sections]

Your assignment:
- [specific actions from task]
[If the task carries a `Domain: <tag>` line, read `../do-plan/DOMAIN_FRAMING.md` and
append that domain's rules here so the builder/reviewer applies them.]

Commit at logical checkpoints as you work — not as one batch at the end. Any commit-message hygiene hook the repo has runs at each commit.

Do all work in-turn, synchronously: run commands (tests, builds, scripts) to completion and read their results within your turn. If you background a long command, poll it in-turn until it exits and record the result before your turn ends — nothing will resume you later to collect it.

When complete, update your task status.

SELF-CHECK (mandatory before marking task complete):
1. Run \`git status\` in the worktree and include the output in your response
2. Run \`git log --oneline main..HEAD\` and include the output
3. If you made zero file changes, explicitly state "NO CHANGES MADE" and explain why`,
  subagent_type: "[agent type from task]",
  run_in_background: false
})
```

**Always `run_in_background: false`, even for `Parallel: true` tasks.** `do-build` runs in a forked context (`context: fork`) that gets exactly one turn — a background dispatch returns immediately and notifies later, but the fork has no later turn to receive that notification, so it's unrecoverable (issue #1915: forks reporting "running in the background, I'll continue when it completes" and then never continuing, leaving unpushed branches and no PR). To run tasks in parallel, make multiple foreground `Task` calls in the **same message** — the harness executes them concurrently and blocks for all results before your next turn. Never rely on background scheduling to achieve parallelism inside a fork.

**The same in-turn contract covers commands, not just Task calls (issue #2051).** Run test suites, builds, and validation scripts synchronously and read their output in the same turn. If a long command must be backgrounded (e.g. a full test suite), poll it to completion **in-turn** — repeated status checks inside this same turn until it exits — then record the result (pass/fail counts, exit code) before the turn ends. Before waiting on anything, confirm a live producer exists that will complete it: a stopped fork receives no completion events, no monitor notifications, and no scheduled wake-ups. The proven pattern is start → poll in-turn → read result → act → record, all in one turn. Include this same brief in every child prompt (the template above carries it).

## Step 3.5: Post-Task Output Verification

After each **builder** agent task completes (agent type = `builder`), verify that it actually produced changes. Skip this check for `validator`, `code-reviewer`, and `documentarian` agent types — they may legitimately produce no file changes.

```bash
# Check if the builder agent produced any file changes in the worktree
DIFF_STAT=$(git -C $TARGET_REPO/.worktrees/{slug} diff --stat HEAD)
UNCOMMITTED=$(git -C $TARGET_REPO/.worktrees/{slug} status --porcelain)
```

**If both `DIFF_STAT` and `UNCOMMITTED` are empty** (no committed changes AND no uncommitted changes):
1. Log the failure: "BUILDER AGENT PRODUCED NO CHANGES: task=[task name], agent=[agent name], worktree=$TARGET_REPO/.worktrees/{slug}"
2. Check the agent's response output for "NO CHANGES MADE" — if present, include the agent's explanation
3. **Mark the task as FAILED** — do not proceed as if it succeeded
4. Report the failure to the user with diagnostic info: task name, agent type, worktree path, and agent response summary

**If changes exist**: Log the diff stat and proceed normally.

**Tool-availability mismatch guard (issue #2022) — applies to EVERY agent type, including validator/documentarian:** inspect each child's final message before treating it as a completion. If the final message is (or begins with) a bare shell command — e.g. it starts with `git `, `gh `, `cd `, `pytest`, `python `, `grep `, or reads as a command line rather than a report — AND the child made zero tool calls / produced zero changes, the child was spawned on an agent type without the tools it needed (it emitted the command it could not run as plain text). This is a **tool-availability mismatch, never a normal completion**:
1. Log: "TOOL-AVAILABILITY MISMATCH: task=[task name], agent type=[type], final message begins with a bare shell command and zero tool calls were made"
2. Re-dispatch the same task once with a Bash-capable agent type (`builder`, `documentarian`, or `general-purpose`)
3. If the re-dispatch shows the same signature, mark the task FAILED and surface the mismatch — do not loop

## Step 4: Monitor and Coordinate

Every Task call in Step 3 runs `run_in_background: false`, so its result is already in hand when the call returns — there is no separate polling loop. Coordination is just:

- Check `TaskList({})` to see overall progress after each batch of foreground Task calls returns
- When a blocker's Task call returns complete, dependent tasks auto-unblock — deploy them next
- **On any agent failure:** commit whatever work exists in the worktree as a safety net before deciding retry/skip/abort:
  ```bash
  git -C $TARGET_REPO/.worktrees/{slug} add -A && git -C $TARGET_REPO/.worktrees/{slug} commit -m "[WIP] partial work before agent failure" || true
  ```

## Step 4.5: Pre-Validation Commit Check

Before proceeding to final validation, verify that the session branch has at least one commit beyond main. This catches the case where all builder agents silently failed to produce work.

```bash
COMMIT_COUNT=$(git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD | wc -l | tr -d ' ')
```

**If `COMMIT_COUNT` is 0:**
1. **ABORT the build** — do not proceed to validation or PR creation
2. Report a clear error: "BUILD FAILED: Zero commits on session/{slug} after all builder tasks completed. No code was produced."
3. List all builder tasks that were deployed, their completion status, and whether they reported changes
4. Suggest: "Re-run /do-build or investigate why builder agents produced no output"

**If `COMMIT_COUNT` > 0:** Log "session/{slug} has {COMMIT_COUNT} commit(s) beyond main" and proceed.

## Step 5: Final Validation and Definition of Done

When the final `validate-all` task completes, verify Definition of Done criteria.

**Pipeline stage at this point:** `test` → advance to `review` before proceeding (if the context file declares a state machine; otherwise just proceed).

**Definition of Done Checklist (pre-documentation):**
- [x] **Built**: All code implemented and working
- [x] **Tested**: All unit tests passing, integration tests passing
- [x] **Quality**: the repo's lint/format checks pass, no lint errors
- [x] **Reviewed**: Review passes (no blocking issues)
- [x] **Demonstrated**: Feature produces intended user-visible output (e.g., rendered message, API response, UI state)

If any criterion is not met, report the issue and do NOT proceed to the Document stage.

**Note**: Documentation validation happens AFTER review passes — see PR_AND_CLEANUP.md Step 6. The canonical pipeline order is: Plan → Branch → Implement → Test → Review → Document → PR. Fix-and-retry loops re-enter at Test (for test failures) or Review (for review failures).

## Step 5.1: Run Verification Checks from Plan

If the plan has a `## Verification` section with a machine-readable table, run
each check and confirm its expected result. This replaces manual validation
judgment with deterministic pass/fail. Run the checks inside the worktree
(`cd .worktrees/{slug}` in a subshell).

Generic baseline: read the `## Verification` table from the plan, run each
`Command`, and compare against its `Expected` column. If any check fails, fix the
specific failure and re-run. If the context file declares a verification-table
parser/runner, use it instead. If the plan has no `## Verification` section, this
step is a no-op.

## Step 5.5: CWD Safety Reset

Before running any orchestrator bash commands, verify the shell CWD is the main repo root (not inside a worktree). Run this as a sanity check:

```bash
cd $(git rev-parse --show-toplevel) && pwd
```

The output should be the main repo path, NOT a `.worktrees/` path. If the CWD is somehow inside the worktree, this resets it. All subsequent orchestrator commands depend on CWD being the repo root.

## Step 5.6: PROGRESS.md Soft Check

After validating Definition of Done, run a soft check for the working-state scratchpad. Missing PROGRESS.md is a warning, not a blocker — PR creation is not gated on this:

```bash
[ -f $TARGET_REPO/.worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at worktree root — not blocking, but recovery from compaction may be degraded next run."
```

## Agent Deployment Context

When deploying an agent, include:
1. The specific task actions from the plan
2. Relevant file paths from the plan's "Relevant Files" section
3. Success criteria from the plan
4. Validation commands they should run (for validators)
5. Reminder: No temporary files in repo - use /tmp for scratch work, only commit deliverables
