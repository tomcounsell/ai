---
name: do-build
description: "Use when executing a plan document to ship a feature. Triggered by 'build this', 'execute the plan', 'implement the plan', or any request to run/ship a plan."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Build (Plan Execution)

You are the **team lead** executing a plan document. You orchestrate work using Task tools - you NEVER build directly.

## Repo Context Probe

If `docs/sdlc/do-build.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo layers its build automation onto this generic baseline: a pipeline state machine and stage markers, a worktree manager, cross-repo target resolution, freshness/prerequisite/build/docs validation scripts, a plan-hash mid-build guard, the lint/format commands, and the docs-gate + plan-migration conventions. When the file is absent (the common case in a foreign repo), this skill runs entirely on `git`, `gh`, and the Task tool: it resolves the plan, creates an isolated worktree/branch, deploys builder/validator agents to execute the plan's tasks, verifies the Definition of Done and that the repo's tests pass, opens a PR, and reports — no repo-specific tooling required.

Throughout the steps below, any action described as "if the context file declares X" is skipped in the generic case. The orchestration order (resolve → branch → implement → test → review → document → PR) holds either way; only the substrate calls that record/advance pipeline state are gated.

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

PLAN_ARG: $ARGUMENTS

**If PLAN_ARG is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Resolve PLAN_ARG using this priority order:

1. **Check the user's message**: If the user's message contains `/do-build <something>`, extract `<something>` as PLAN_ARG.
2. **Check conversation context**: Scan recent messages for an explicitly mentioned plan path (e.g., `docs/plans/foo.md`) or issue number (e.g., `#564`, `issue 564`). Use the most recently referenced one.
3. **Still ambiguous**: STOP and ask the caller (user, SDLC, PM session — whoever invoked this): "Which plan should I build? Please provide a plan path (e.g., `docs/plans/foo.md`) or issue number (e.g., `#564`)." Do NOT guess or pick a plan at random.

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

## Target Repo Resolution (Cross-Repo Support)

After resolving `PLAN_PATH`, determine which git repository the plan belongs to. This is critical for cross-repo builds where the plan lives in a different repo than the orchestrator.

```bash
# Resolve the target repo root from the plan file's location
TARGET_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
ORCHESTRATOR_REPO=$(git rev-parse --show-toplevel)

# Check if this is a cross-repo build
if [ "$TARGET_REPO" != "$ORCHESTRATOR_REPO" ]; then
    echo "CROSS-REPO BUILD: Plan is in $TARGET_REPO (orchestrator is $ORCHESTRATOR_REPO)"
    # Resolve the target repo's GitHub identity for PR creation
    TARGET_GH_REPO=$(git -C "$TARGET_REPO" remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/\.git$//')
fi
```

In the common single-repo case `TARGET_REPO` is just `git rev-parse --show-toplevel` and `TARGET_GH_REPO` is unset. If the context file declares a repo-resolution helper, use it instead.

**All subsequent git, worktree, and PR operations must use `TARGET_REPO` as the repo root**, not the orchestrator repo. Specifically:
- `create_worktree(Path(TARGET_REPO), slug)` instead of `create_worktree(Path('.'), slug)`
- `git -C $TARGET_REPO/.worktrees/{slug}` for all git commands in the worktree
- `gh pr create --repo $TARGET_GH_REPO` when creating the PR
- Pipeline state is still stored in the orchestrator repo but includes `target_repo` in the state dict

If `TARGET_REPO == ORCHESTRATOR_REPO`, this is a same-repo build and no special handling is needed (all existing behavior works as-is).

## Instructions

The generic orchestration flow. Each numbered step that touches a pipeline
substrate (state machine, stage markers, validation scripts, plan-hash guard) is
**gated behind the context file** — in a foreign repo those sub-steps are skipped
and the build proceeds on `git`/`gh`/Task alone. The ordering is unconditional.

1. **Resolve the plan path** using the Plan Resolution logic above; derive `{slug}` from the plan filename.
2. **Read the plan** at `PLAN_PATH`.
3. **Resume check (if the context file declares a pipeline state machine)** — load any existing build state for `{slug}`; if a prior stage is recorded, resume from it and skip completed stages. Otherwise treat this as a fresh build.
4. **Freshness check (if the context file declares one)** — verify the plan has incorporated the latest tracking-issue comments. If stale, stop and report that `/do-plan` must run first. Generic default: skip.
5. **Prerequisite validation (if the context file declares a checker, or the plan has a `## Prerequisites` section)** — run each prerequisite check command; if any fails, report and stop. No section ⇒ passes automatically.
6. **Resolve target repo** (see "Target Repo Resolution" above): `TARGET_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)`.
7. **Create an isolated worktree.** Generic baseline:
   ```bash
   git -C "$TARGET_REPO" worktree add "$TARGET_REPO/.worktrees/{slug}" -b session/{slug} 2>/dev/null \
     || git -C "$TARGET_REPO" worktree add "$TARGET_REPO/.worktrees/{slug}" session/{slug}
   ```
   This is the isolation boundary: all agent work happens inside `$TARGET_REPO/.worktrees/{slug}/`, never the orchestrator repo directory. If the context file declares a worktree manager (idempotent get-or-create, stale-worktree recovery, settings-file copying, clean-git-state guard), use it instead — it handles interrupted-session resumption and branch-already-in-use errors.
8. **Initialize/record build state (if the context file declares a state machine)** — initialize pipeline state for a fresh build, and record the plan hash at build start for the mid-build revision guard. Generic default: skip.
9. **Parse the Team Members and Step by Step Tasks** sections of the plan.
10. **Create all tasks** with `TaskCreate` before starting execution; set dependencies (`addBlockedBy`).
11. **Deploy agents** in order, respecting dependencies and parallel flags. Agents follow the Build → Test loop with up to 5 fix-and-retry iterations. **Advance the pipeline stage** at each transition (branch → implement → test → review → document → pr) **if the context file declares a state machine**; otherwise just proceed in that order.
12. **Monitor progress** and handle any issues (see Workflow Step 4).
13. **Verify Definition of Done** — all tasks complete with code working, the repo's tests passing, and lint/format clean.
14. **Validate the build against the plan (if the context file declares validators)** — run the deterministic plan validator and/or AI semantic evaluator against the plan's assertions and acceptance criteria; route failures to `/do-patch` (bounded iterations) and re-run. Generic default: confirm the plan's `## Verification` checks pass (see Workflow Step 5.1) and the repo's tests pass.
15. **Documentation gate** — ensure the plan's required docs were created/updated (see Workflow Step 6). If the context file declares a docs-validation script, run it; it BLOCKS PR creation on failure.
16. **Verify commits exist before PR** — `git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD`; if zero commits, **ABORT**: "BUILD FAILED: No commits on session/{slug}." Do NOT push or open a PR. **If the context file declares a plan-hash mid-build guard**, also verify the plan hash is unchanged and abort if it drifted (a concurrent revision landed).
17. **Push and open a PR** — `git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}` then `gh pr create` (add `--repo $TARGET_GH_REPO` only for cross-repo builds).
18. **Run the documentation cascade** — invoke `/do-docs {PR-number}` to surgically update affected docs.
19. **Plan stays until merge** — do NOT delete the plan here; `/do-merge` handles it after the PR merges (issue closes via `Closes #N`).
20. **Report completion** with the PR URL when all tasks are done.

## Lint Discipline

If the repo auto-handles lint/format (via a pre-commit hook or editor-time
formatter the context file describes), agents should never waste iterations on
lint fixes:

- **Intermediate commits**: Use `--no-verify` to skip the pre-commit hook during WIP commits mid-task, avoiding lint interruptions while still working.
- **Final commits**: Let the pre-commit hook run (no `--no-verify`) so it auto-fixes and re-stages. Only genuinely unfixable issues block the commit.
- **Avoid redundant manual lint** when an auto-fix hook already runs on commit.

If the repo has no such automation (the generic case), agents run its lint/format
checks once before the final commit and fix any issues manually.

## Critical Rules

- **You are the orchestrator, not a builder** - Never use Write/Edit tools directly
- **Deploy agents via Task tool** - Each task in the plan becomes a Task tool call
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together** - Tasks with `Parallel: true` and no blocking dependencies can run simultaneously
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.
- **Never cd into worktrees** - The orchestrator's CWD must stay in the main repo. Use `git -C $TARGET_REPO/.worktrees/{slug}` for git commands, subshells `(cd $TARGET_REPO/.worktrees/{slug} && ...)` when Python scripts need worktree CWD, and `--head session/{slug}` for `gh pr create`. For cross-repo builds, use `--repo $TARGET_GH_REPO` with `gh pr create`. Only subagents (Task tool) should have bare `cd` into worktrees — their shell sessions are independent and disposable. If the orchestrator's CWD ends up inside a worktree and that worktree is deleted, the shell breaks permanently and cannot recover.
- **SDLC enforcement** - All builder agents follow Plan → Branch → Implement → Test → Review → Document → PR with fix-and-retry loops at Test and Review stages (up to 5 iterations)
- **Definition of Done** - Tasks are complete only when: Built (code working), Tested (tests pass), Reviewed (review passes), Documented (docs created after review), Quality (lint/format pass)
- **Commits at logical checkpoints** - Commits happen at logical checkpoints throughout Implement — not batched at end. The commit message hook enforces hygiene at each commit.
- **PROGRESS.md is the standard in-session scratchpad** — dev sessions maintain it at the worktree root per builder.md's "Working-state externalization" section. It is gitignored (not committed). Missing PROGRESS.md is a warning, not a blocker. The plan doc and git log remain the authoritative progress record.

## Workflow

### Step 0: Stage Marker (only if the context file declares a substrate)

If the context file declares an orchestration substrate (a pipeline state
machine + stage markers), write the BUILD `in_progress` marker now and follow
its degraded-mode handling — a forked sub-skill announces degraded mode rather
than silently lagging state. The build itself (worktree, agents, tests, PR) never
depends on the substrate, so a missing or degraded substrate never blocks it.

In the generic case (no substrate declared), skip this step.

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

NEVER use \`git checkout\` or \`git checkout -b\` on session/ branches. The worktree IS the checkout — just \`cd\` into it. Running \`git checkout session/{slug}\` will fail with a fatal error because the branch is locked by the worktree.

Plan context: [relevant plan sections]

Your assignment:
- [specific actions from task]

Commit at logical checkpoints as you work — not as one batch at the end. The commit message hook enforces hygiene at each commit.

SELF-CHECK (mandatory before marking task complete):
1. Run \`git status\` in the worktree and include the output in your response
2. Run \`git log --oneline main..HEAD\` and include the output
3. If you made zero file changes, explicitly state "NO CHANGES MADE" and explain why

When complete, update your task status.`,
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

When the final `validate-all` task completes, verify Definition of Done criteria.

**Pipeline stage at this point:** `test` → advance to `review` before proceeding (if the context file declares a state machine; otherwise just proceed).

**Definition of Done Checklist (pre-documentation):**
- [x] **Built**: All code implemented and working
- [x] **Tested**: All unit tests passing, integration tests passing
- [x] **Quality**: the repo's lint/format checks pass, no lint errors
- [x] **Reviewed**: Review passes (no blocking issues)
- [x] **Demonstrated**: Feature produces intended user-visible output (e.g., rendered message, API response, UI state)

If any criterion is not met, report the issue and do NOT proceed to the Document stage.

**Note**: Documentation validation happens AFTER review passes — see Step 6. The canonical pipeline order is: Plan → Branch → Implement → Test → Review → Document → PR. Fix-and-retry loops re-enter at Test (for test failures) or Review (for review failures).

### Step 5.1: Run Verification Checks from Plan

If the plan has a `## Verification` section with a machine-readable table, run
each check and confirm its expected result. This replaces manual validation
judgment with deterministic pass/fail. Run the checks inside the worktree
(`cd .worktrees/{slug}`).

Generic baseline: read the `## Verification` table from the plan, run each
`Command`, and compare against its `Expected` column. If any check fails, fix the
specific failure and re-run. If the context file declares a verification-table
parser/runner, use it instead. If the plan has no `## Verification` section, this
step is a no-op.

### Step 5.5: CWD Safety Reset

Before running any orchestrator bash commands, verify the shell CWD is the main repo root (not inside a worktree). Run this as a sanity check:

```bash
cd $(git rev-parse --show-toplevel) && pwd
```

The output should be the main repo path, NOT a `.worktrees/` path. If the CWD is somehow inside the worktree, this resets it. All subsequent orchestrator commands depend on CWD being the repo root.


### Step 5.6: PROGRESS.md Soft Check

After validating Definition of Done, run a soft check for the working-state scratchpad. Missing PROGRESS.md is a warning, not a blocker — PR creation is not gated on this:

```bash
[ -f $TARGET_REPO/.worktrees/{slug}/PROGRESS.md ] || echo "[warn] No PROGRESS.md at worktree root — not blocking, but recovery from compaction may be degraded next run."
```

### Step 6: Documentation Gate

After review passes, advance to the `document` stage (if the context file
declares a state machine) and run documentation lifecycle checks. This is the
Document phase of the pipeline: `Plan → Branch → Implement → Test → Review →
**Document** → PR`. Documentation is written and validated here, after
implementation is reviewed — not interleaved with implementation.

**6.1 Validate Documentation Changes**

Confirm the plan's required documentation was created/updated. Inspect the
session branch's diff for the doc files the plan's `## Documentation` section
named (run inside the worktree so `git diff` sees the branch changes; use a
`(cd .worktrees/{slug} && ...)` subshell so the orchestrator's CWD stays in the
main repo). If a required doc is missing, **STOP and report failure** — this
gate BLOCKS PR creation.

If the context file declares a docs-validation script, run it (it enforces the
gate deterministically). Generic default: verify by hand that the plan's named
doc paths appear in `git diff --name-only main...HEAD`.

**6.2 Scan for Related Documentation (optional)**

If the context file declares a related-docs scanner, collect the changed files
(`git diff --name-only main...HEAD`) and run it to identify existing docs that
may need updates. Otherwise rely on the `/do-docs` cascade in Step 7.6.

**6.3 Create Review Issues for Discrepancies (optional)**

If a scanner ran and the context file declares an issue-creation helper, file
review issues for HIGH/MED-HIGH confidence matches. Otherwise skip — the
`/do-docs` cascade flags conflicts itself.

### Step 7: Create Pull Request

After the documentation gate passes, advance to the `pr` stage (if the context file declares a state machine), then push and create the PR. For cross-repo builds, use `$TARGET_REPO` and `--repo $TARGET_GH_REPO`:

```bash
git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}
# For cross-repo builds, add: --repo $TARGET_GH_REPO
gh pr create --head session/{slug} --title "[plan title]" --body "$(cat <<'EOF'
## Summary
[Brief description of what was built]

## Changes
- [List key changes made]

## Testing
- [x] Unit tests passing
- [x] Integration tests passing
- [x] Lint/format checks passing

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

After pushing and creating the PR, return to the repo root and clean up the worktree. The `cd` to the repo root FIRST prevents CWD death if the shell is inside the worktree:

```bash
# Return to repo root BEFORE cleanup (prevents CWD death)
cd "$TARGET_REPO"

# Remove the worktree but KEEP the branch — the PR still references session/{slug}.
git -C "$TARGET_REPO" worktree remove "$TARGET_REPO/.worktrees/{slug}"
git -C "$TARGET_REPO" worktree prune
```

If the context file declares a worktree manager, use its removal helper instead (it adds busy-session guards and stale-ref pruning). Do NOT delete the branch — it is cleaned up when the PR is merged.

### Step 7.6: Documentation Cascade

After the PR is created, run the `/do-docs` cascade to find and surgically update any existing documentation affected by the code changes in this build. Pass the PR number AND plan context so the cascade understands the feature intent:

```
/do-docs {PR-number}

Plan: {PLAN_PATH}
Goal: [1-2 sentence summary from plan]
Issue: #{issue-number}
```

This invokes the cascade skill defined in `.claude/skills-global/do-docs/SKILL.md`, which:
- Launches parallel agents to explore the change diff and inventory all docs
- Cross-references changes against every doc in the repo (triage questions)
- Makes targeted surgical edits to affected docs (read before edit, preserve structure)
- Creates GitHub issues for conflicts needing human review
- Commits any doc updates to the PR branch before merge

**Note**: The cascade is best-effort. If it finds nothing to update, that's fine — proceed to reporting. If it makes edits, those are committed directly to the PR branch.

### Step 8: Plan Stays Until Merge

After PR is created and documentation cascade completes, the plan document is **not deleted here**. It remains at `{PLAN_PATH}` so that:
- `do-merge` can read it to verify all checklist items are done
- `do-docs` can use it as context during the DOCS stage

The plan will be deleted by `do-merge` after the PR is successfully merged.

### Step 9: Report PR Link

After plan migration completes, include the PR URL prominently in your final response. When running via Telegram bridge, the agent's response (containing the PR link) will be automatically sent back to the chat where the build was initiated. No special action required - just ensure the PR URL is visible in your completion report.

### OUTCOME Contract Emission

As the very last line of your final response, emit an OUTCOME contract so the pipeline can classify the build result programmatically:

- **Success** (PR created): `<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"<URL>"}} -->`
- **Fail** (build failed, no PR): `<!-- OUTCOME {"status":"fail","stage":"BUILD","artifacts":{}} -->`

This structured output is parsed by the repo's pipeline harness (Tier 0) before any text pattern matching — the context file names the exact parser when the repo has an SDLC pipeline.

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
/do-build docs/plans/implement-auth.md
```

**By issue number:**
```
/do-build #42
/do-build 42
```

Both methods will execute the same plan if the plan file has:
```yaml
tracking: https://github.com/your-org/your-repo/issues/42
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
- [x] Reviewed: Review passed (no blocking issues)
- [x] Documented: Docs created after review (validated by docs gate)
- [x] Quality: the repo's lint/format checks pass
- [x] Plans migrated: Plan moved from docs/plans/ to completed state

### Task Summary
| Task | Agent | Status | Test Iterations | Notes |
|------|-------|--------|----------------|-------|
| [name] | [agent] | Done | [N] | [brief note] |

### Validation Results
- [x] All build tasks completed
- [x] All validators passed
- [x] Documentation gate passed
- [x] Documentation cascade completed (`/do-docs`)
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
