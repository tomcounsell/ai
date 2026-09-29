---
name: do-build
description: "Use when executing a plan document to ship a feature. Triggered by 'build this', 'execute the plan', 'implement the plan', or any request to run/ship a plan."
argument-hint: "<plan-path-or-issue-number>"
context: fork
effort: medium
---

# Build (Plan Execution)

Turn a plan document into an open PR that meets the Definition of Done below. You are the
**team lead**: you resolve the plan, set up an isolated worktree, dispatch builder/validator
agents to do the work, run the gates, open the PR, and report. You never write code yourself.

## Repo Context Probe

If `docs/sdlc/do-build.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file layers repo automation onto this baseline: a pipeline state machine and stage
markers, a worktree manager, cross-repo resolution, freshness/prerequisite/build/docs validation
scripts, a plan-hash mid-build guard, lint commands, and docs-gate conventions. Any step below
marked "if the context file declares X" is skipped when it does not; the build then runs on
`git`, `gh`, and the Task tool alone. The order (resolve → branch → implement → test → review →
document → PR) holds either way.

| Sub-file | Load when |
|----------|-----------|
| `WORKFLOW.md` | Before any agent deploys (Steps 0-5.6: stage marker, tasks, agent dispatch, verification) |
| `PR_AND_CLEANUP.md` | All build tasks complete and validated (Steps 6-9: docs gate, PR, cleanup, docs cascade, report) |

## Definition of Done

A build is done only when all hold:

- **Built**: every plan task implemented and working, each builder's diff covering the task's enumerated deliverables, and the plan's `## Success Criteria` met
- **Tested**: the repo's tests pass, the plan's `## Verification` checks pass, and any validators the context file declares pass
- **Reviewed**: review passes with no blocking issues
- **Demonstrated**: the feature produces its intended user-visible output (message, API response, UI state)
- **Documented**: the plan's required docs exist, written after review
- **Quality**: the repo's lint/format checks pass
- **Shipped**: `session/{slug}` has commits beyond main, is pushed, and has one open PR (`Closes #N`)

If any criterion fails, report it and do not advance to the next stage.

## Plan Resolution

PLAN_ARG: $ARGUMENTS

Accepts a plan path (`/do-build docs/plans/foo.md`) or an issue number (`/do-build #17` / `17`).
If PLAN_ARG is empty or literally `$ARGUMENTS`, take it from a `/do-build <arg>` in the user's
message, else the most recently referenced plan path or issue number in the conversation; if still
ambiguous, stop and ask the caller which plan to build. Never guess.

For an issue number, the plan is the one `docs/plans/*.md` whose frontmatter `tracking:` URL ends in
`/issues/{N}`. Zero matches: error "No plan found tracking issue #{N}". More than one: error listing
the paths. `{slug}` comes from the plan filename unless the context file says otherwise.

## Target Repo Resolution (Cross-Repo Support)

The plan may live in a different repo than the orchestrator. Resolve it and use `TARGET_REPO` as
the root for every git, worktree, and PR operation:

```bash
TARGET_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
ORCHESTRATOR_REPO=$(git rev-parse --show-toplevel)
if [ "$TARGET_REPO" != "$ORCHESTRATOR_REPO" ]; then
    TARGET_GH_REPO=$(git -C "$TARGET_REPO" remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/\.git$//')
fi
```

Cross-repo builds pass `--repo $TARGET_GH_REPO` to every `gh` call; pipeline state stays in the
orchestrator repo with `target_repo` recorded. If the context file declares a repo-resolution
helper, use it instead.

## Instructions

Detailed procedures live in `WORKFLOW.md` and `PR_AND_CLEANUP.md`.

1. **Resolve the plan** and `{slug}`; read the plan. If the state substrate records that this build was reached by exhausting a critique bound, record the accepted residual concerns in the plan before building (the context file names the condition and where the note goes; with no context file, skip).
2. **Resume check (if the context file declares a state machine)**: resume from a recorded stage, skipping completed ones.
3. **Freshness check (if declared)**: if the plan has not incorporated the latest tracking-issue comments, stop and report that `/do-plan` must run first.
4. **Prerequisites (if declared, or the plan has `## Prerequisites`)**: run each check; any failure stops the build.
5. **Create an isolated worktree** in the target repo:
   ```bash
   git -C "$TARGET_REPO" worktree add "$TARGET_REPO/.worktrees/{slug}" -b session/{slug} 2>/dev/null \
     || git -C "$TARGET_REPO" worktree add "$TARGET_REPO/.worktrees/{slug}" session/{slug}
   ```
   All agent work happens inside it. If the context file declares a worktree manager, use it instead (it handles resumption and branch-in-use errors).
6. **Initialize build state and record the plan hash (if declared).**
7. **Create tasks** from the plan's Team Members and Step by Step Tasks with `TaskCreate`, dependencies via `addBlockedBy`, then deploy agents per WORKFLOW.md. **Batch small sequential plans into ONE builder**: when tasks are small and strictly dependent (no `Parallel: true`), dispatch one foreground builder with the full ordered task list; per-task dispatch pays a round trip that can cost more than the coding. Keep per-task dispatch for parallel tasks and tasks large enough to risk exhausting a builder's context. Advance the pipeline stage at each transition if a state machine is declared.
8. **Verify each builder's output** against its deliverables (WORKFLOW.md Step 3.5).
9. **Validate against the plan**: the plan's `## Verification` checks, plus any declared validators (failures route to `/do-patch`, bounded).
10. **Documentation gate** (PR_AND_CLEANUP.md Step 6); a declared docs-validation script blocks the PR on failure.
11. **Verify commits before PR**: `git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD`; zero commits → abort with "BUILD FAILED: No commits on session/{slug}." and do not push. If a plan-hash guard is declared, abort if the plan changed mid-build.
12. **Push and open or reuse the PR** (PR_AND_CLEANUP.md Step 7), then run `/do-docs {PR-number}` (Step 7.6).
13. **Leave the plan in place**; `/do-merge` migrates it after merge.
14. **Report** with the PR URL (PR_AND_CLEANUP.md Step 9).

## Lint Discipline

If the repo auto-fixes lint/format on commit (a pre-commit hook the context file describes), use
`--no-verify` on intermediate WIP commits and let the hook run on final commits; don't spend
iterations on manual lint. Otherwise run the repo's lint/format checks once before the final commit.

## Constraints

- **Orchestrator, not builder**: never use Write/Edit on code yourself. Builders never open the PR; you do, after all gates pass.
- **Foreground only**: tasks with `Parallel: true` and no blocking dependencies run as multiple `run_in_background: false` Task calls in the same message, never via background scheduling. A fork has one turn and cannot be resumed by a background notification (#1915).
- **No agent teammates**: where agent teams are enabled, ignore them; a teammate's idle notification is not a completion signal and in-process teammates cannot be reliably resumed.
- **All work in-turn (#2051)**: run builders, tests, and validation scripts to completion within this turn. If you background a long command, poll it in-turn until it exits and act on the result. Before waiting on anything, confirm a live producer will complete it; nothing resumes you later. Pass this same brief to every child.
- **Validators wait for their builders**: a `validate-*` task always depends on its `build-*` task.
- **No temporary files in the repo**: scratch work goes to /tmp; commit only deliverables.
- **Never cd into worktrees**: your CWD stays in the main repo. Use `git -C $TARGET_REPO/.worktrees/{slug}`, subshells `(cd ... && ...)`, and `--head session/{slug}`. If your CWD is inside a worktree when it is deleted, the shell breaks permanently. Only subagents `cd` into the worktree.
- **Commit at logical checkpoints** throughout implementation, not one batch at the end.
- **PROGRESS.md** at the worktree root is the gitignored in-session scratchpad; missing is a warning, not a blocker. The plan doc and git log are authoritative.
- **Failures**: on a task failure, decide retry, skip, or abort from the agent's output; never proceed past a blocking failure.

## OUTCOME Contract Emission

As the very last line of your final response, emit an OUTCOME contract so the pipeline can classify the build result programmatically:

- **Success** (PR created): `<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"<URL>"}} -->`
- **Fail** (build failed, no PR): `<!-- OUTCOME {"status":"fail","stage":"BUILD","artifacts":{}} -->`

The context file names the parser when the repo has an SDLC pipeline.
