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
5. Opens a PR and reports the result

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `WORKFLOW.md` | Starting execution (Steps 0-5.6: stage marker, task creation, agent deployment, monitoring, validation) |
| `PR_AND_CLEANUP.md` | All build tasks complete and validated (Steps 6-9: docs gate, PR, cleanup, docs cascade, reporting) |

## Invocation Methods

1. **By plan path**: `/do-build docs/plans/my-feature.md`
2. **By issue number**: `/do-build #17` or `/do-build 17`

Both methods execute the same plan if the plan file has a frontmatter line like `tracking: https://github.com/your-org/your-repo/issues/17`.

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
Detailed procedures for each step live in `WORKFLOW.md` (Steps 0-5.6) and
`PR_AND_CLEANUP.md` (Steps 6-9) — load them at the phases the sub-file table names.

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
10. **Load `WORKFLOW.md` now** — before any agent deploys — then **create all tasks** with `TaskCreate` before starting execution; set dependencies (`addBlockedBy`).
11. **Deploy agents** in order, respecting dependencies and parallel flags, using WORKFLOW.md's builder prompt template — every builder prompt must mandate working inside the worktree and forbid `git checkout` on session/ branches. **Batch small sequential plans into ONE builder**: when the plan's tasks are small and strictly dependent (no `Parallel: true`), dispatch a single foreground builder carrying the full ordered task list in one continuous prompt instead of one builder per task — each per-task dispatch pays a dispatch→verify round trip that on a small plan can cost more wall-clock than the coding itself. Keep per-task dispatch for parallel tasks and for tasks individually large enough to risk builder context exhaustion. Agents follow the Build → Test loop with up to 5 fix-and-retry iterations. **Advance the pipeline stage** at each transition (branch → implement → test → review → document → pr) **if the context file declares a state machine**; otherwise just proceed in that order.
12. **Monitor progress** and handle any issues (see WORKFLOW.md Step 4).
13. **Verify Definition of Done** — all tasks complete with code working, the repo's tests passing, and lint/format clean.
14. **Validate the build against the plan (if the context file declares validators)** — run the deterministic plan validator and/or AI semantic evaluator against the plan's assertions and acceptance criteria; route failures to `/do-patch` (bounded iterations) and re-run. Generic default: confirm the plan's `## Verification` checks pass (see WORKFLOW.md Step 5.1) and the repo's tests pass.
15. **Documentation gate** — ensure the plan's required docs were created/updated (see PR_AND_CLEANUP.md Step 6). If the context file declares a docs-validation script, run it; it BLOCKS PR creation on failure.
16. **Verify commits exist before PR** — `git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD`; if zero commits, **ABORT**: "BUILD FAILED: No commits on session/{slug}." Do NOT push or open a PR. **If the context file declares a plan-hash mid-build guard**, also verify the plan hash is unchanged and abort if it drifted (a concurrent revision landed).
17. **Push and open a PR** — `git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}` then `gh pr create` (add `--repo $TARGET_GH_REPO` only for cross-repo builds). See PR_AND_CLEANUP.md Step 7 for the PR body template.
18. **Run the documentation cascade** — invoke `/do-docs {PR-number}` with plan context (see PR_AND_CLEANUP.md Step 7.6).
19. **Plan stays until merge** — do NOT delete the plan here; `/do-merge` handles it after the PR merges (issue closes via `Closes #N`).
20. **Report completion** with the PR URL when all tasks are done (report format in PR_AND_CLEANUP.md Step 9).

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
- **Deploy agents via Task tool** - Tasks map to Task tool calls; a small strictly-sequential plan batches into ONE builder call carrying the full ordered task list (see step 11)
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together, always in the foreground** - Tasks with `Parallel: true` and no blocking dependencies run simultaneously via multiple `run_in_background: false` Task calls in the same message, never via background scheduling (see WORKFLOW.md Step 3 — a fork has one turn and cannot be resumed by a background notification, issue #1915)
- **Never use agent teammates for pipeline work** - Where Claude Code agent teams are enabled, ignore those affordances inside this skill: dispatch is always foreground Task/subagent calls. A teammate's idle notification is NOT a completion signal — teammates go idle mid-task with deliverables unfinished (idle ≠ done) — and in-process teammates cannot be reliably resumed.
- **Do all work in-turn, synchronously (issue #2051)** - Run every piece of work this build needs — builder children, the test suite, validation scripts — to completion **within your current turn** and record the result before the turn ends. If you start something long-running (e.g. a backgrounded test command), poll it in-turn with repeated status checks until it finishes, then act on the result in the same turn. Before waiting on anything, verify a live producer exists that will actually complete it — you are the only driver; nothing resumes you later. Propagate this same brief into every child you spawn.
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.
- **Never cd into worktrees** - The orchestrator's CWD must stay in the main repo. Use `git -C $TARGET_REPO/.worktrees/{slug}` for git commands, subshells `(cd $TARGET_REPO/.worktrees/{slug} && ...)` when scripts need worktree CWD, and `--head session/{slug}` for `gh pr create`. For cross-repo builds, use `--repo $TARGET_GH_REPO` with `gh pr create`. Only subagents (Task tool) should have bare `cd` into worktrees — their shell sessions are independent and disposable. If the orchestrator's CWD ends up inside a worktree and that worktree is deleted, the shell breaks permanently and cannot recover.
- **SDLC enforcement** - All builder agents follow Plan → Branch → Implement → Test → Review → Document → PR with fix-and-retry loops at Test and Review stages (up to 5 iterations)
- **Definition of Done** - Tasks are complete only when: Built (code working), Tested (tests pass), Reviewed (review passes), Documented (docs created after review), Quality (lint/format pass)
- **Commits at logical checkpoints** - Commits happen at logical checkpoints throughout Implement — not batched at end. Any commit-message hygiene hook the repo has runs at each commit.
- **PROGRESS.md is the standard in-session scratchpad** — dev sessions maintain it at the worktree root. It is gitignored (not committed). Missing PROGRESS.md is a warning, not a blocker (see WORKFLOW.md Step 5.6). The plan doc and git log remain the authoritative progress record.
- **PR creation belongs to the orchestrator** - Builder agents never open the PR; the orchestrator does, after all tasks complete and gates pass.

## Error Handling

If a task fails:
1. Check the agent's output for details
2. Decide: retry, skip, or abort
3. For validators: if validation fails, report what's wrong
4. Don't proceed past blocking failures

## OUTCOME Contract Emission

As the very last line of your final response, emit an OUTCOME contract so the pipeline can classify the build result programmatically:

- **Success** (PR created): `<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"<URL>"}} -->`
- **Fail** (build failed, no PR): `<!-- OUTCOME {"status":"fail","stage":"BUILD","artifacts":{}} -->`

This structured output is parsed by the repo's pipeline harness (Tier 0) before any text pattern matching — the context file names the exact parser when the repo has an SDLC pipeline.
