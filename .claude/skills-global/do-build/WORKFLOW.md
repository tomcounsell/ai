# Build Workflow (Detailed Steps)

## Step 0: Stage Marker (only if the context file declares a substrate)

If the context file declares a pipeline state machine and stage markers, write the BUILD
`in_progress` marker now and follow its degraded-mode handling. The build never depends on the
substrate; a missing or degraded one never blocks it.

**A missing context file is not proof there is no substrate (#2419).** If your prompt carries a
run identity (`run_id`) and `sdlc-tool` is on PATH, a supervisor is tracking this run. Write the
markers yourself: `in_progress` here, `completed` when the PR is open (`PR_AND_CLEANUP.md`):

```bash
sdlc-tool stage-marker --stage BUILD --status in_progress --issue-number {issue_number} --run-id {run_id}
```

Report a failed write but do not block on it. Skip this step only when there is neither a run
identity nor `sdlc-tool`.

## Steps 1-2: Tasks and Dependencies

Create one task per plan task with `TaskCreate` (full task details in the description), then set
dependencies with `TaskUpdate({taskId, addBlockedBy: [...]})`.

## Step 3: Deploy Agents

Brief each agent with the objective, the files, and what done means:

```typescript
Task({
  description: "[Task subject]",
  prompt: `Execute task: [Task Name]

Work only in the worktree {TARGET_REPO}/.worktrees/{slug}/ (cd into it first); {TARGET_REPO} may differ from the orchestrator repo. Never \`git checkout\` a session/ branch: the worktree IS the checkout and the branch is locked to it.

Plan context: [relevant plan sections, relevant files]
Your assignment: [specific actions from the task]
Done means: [the task's success criteria and enumerated deliverables; validation commands for validators]
[If the task has a \`Domain: <tag>\` line, append that domain's rules from ../do-plan/DOMAIN_FRAMING.md.]

Commit at logical checkpoints as you work. Scratch files go to /tmp, never the repo.
Do all work in-turn: run commands to completion and read their results within your turn; if you background one, poll it until it exits. Nothing resumes you later.
When the task is done and checked, update your task status and stop. Before reporting complete, include the output of \`git status\` and \`git log --oneline main..HEAD\` from the worktree; if you changed nothing, say "NO CHANGES MADE" and why.`,
  subagent_type: "[agent type from task]",
  run_in_background: false
})
```

**Always `run_in_background: false`, even for `Parallel: true` tasks.** do-build runs in a fork
with exactly one turn; a background dispatch notifies a turn that never comes (#1915: forks said
"I'll continue when it completes" and never did, leaving unpushed branches and no PR). For
parallelism, make several foreground `Task` calls in the same message; the harness runs them
concurrently and returns all results.

## Step 3.5: Post-Task Output Verification

A completion report is a claim; the diff is the evidence.

- **Builders** (skip for validator, code-reviewer, documentarian): check
  `git -C $TARGET_REPO/.worktrees/{slug} diff --stat HEAD` and `status --porcelain`. If both are
  empty, log "BUILDER AGENT PRODUCED NO CHANGES: task=..., agent=..., worktree=...", include any
  "NO CHANGES MADE" explanation, mark the task FAILED, and report it.
- **Under-delivery**: match every file and named function/section the task specifies against the
  diff. For anything missing, re-brief the same builder (continue it by agent id, foreground) with
  a precise delta brief of what is still missing, rather than re-dispatching the whole task or
  accepting the report.
- **Tool-availability mismatch (#2022), every agent type**: if a child's final message is or begins
  with a bare shell command (`git `, `gh `, `cd `, `pytest`, `python `, `grep `) and it made zero
  tool calls, it was spawned on an agent type lacking the tools it needed. Log "TOOL-AVAILABILITY
  MISMATCH", re-dispatch once on a Bash-capable type (`builder`, `documentarian`,
  `general-purpose`); if the same signature repeats, mark the task FAILED and surface it. Do not loop.

## Step 4: Monitor and Coordinate

Foreground Task calls return with results in hand; check `TaskList({})` after each batch and
deploy newly unblocked tasks. On any agent failure, commit whatever exists before deciding
retry/skip/abort:

```bash
git -C $TARGET_REPO/.worktrees/{slug} add -A && git -C $TARGET_REPO/.worktrees/{slug} commit -m "[WIP] partial work before agent failure" || true
```

## Step 4.5: Pre-Validation Commit Check

Before final validation, `git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD`. Zero
commits → abort the build: "BUILD FAILED: Zero commits on session/{slug} after all builder tasks
completed", listing each builder task and whether it reported changes.

## Step 5: Definition of Done

When the final `validate-all` task completes, check the Definition of Done in SKILL.md (all but
Documented and Shipped, which come later). Advance the pipeline stage `test` → `review` if a state
machine is declared. If any criterion fails, report it and do not proceed to the Document stage.
Fix-and-retry loops re-enter at Test (test failures) or Review (review failures).

## Step 5.1: Run Verification Checks from Plan

If the plan has a `## Verification` table, run each `Command` inside the worktree (subshell) and
compare against its `Expected` column; fix and re-run any failure. If the context file declares a
verification-table runner, use it. No section: no-op.

## Step 5.5: CWD Safety Reset

Before any orchestrator bash command, confirm your CWD is the main repo root, not a `.worktrees/` path:

```bash
cd $(git rev-parse --show-toplevel) && pwd
```

## Step 5.6: PROGRESS.md Soft Check

Warn-only; PR creation is not gated on it:

```bash
[ -f $TARGET_REPO/.worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at worktree root — not blocking, but recovery from compaction may be degraded next run."
```
