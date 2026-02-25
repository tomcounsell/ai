---
name: sdlc
description: "The single entry point for all development work. Assesses current state, then dispatches to the right sub-skill. NEVER write code directly — always delegate to /do-plan, /do-build, /do-test, /do-patch, /do-pr-review, /do-docs."
context: fork
---

# SDLC — Development Lifecycle Dispatcher

This skill is a **dispatcher**, not an implementation skill. It figures out where work stands and invokes the right sub-skill. You MUST NOT write code, run tests, or create plans directly — delegate everything.

## Step 0: Create Pipeline Tasks

Immediately create tasks for the full pipeline so progress is trackable and resumable:

```
TaskCreate({ description: "ISSUE: Ensure GitHub issue exists", status: "in_progress" })
TaskCreate({ description: "PLAN: Create plan doc via /do-plan", status: "pending" })
TaskCreate({ description: "BUILD: Implement via /do-build", status: "pending" })
TaskCreate({ description: "TEST: Validate via /do-test", status: "pending" })
TaskCreate({ description: "PATCH: Fix test failures via /do-patch", status: "pending" })
TaskCreate({ description: "REVIEW: PR review via /do-pr-review", status: "pending" })
TaskCreate({ description: "PATCH: Fix review blockers via /do-patch", status: "pending" })
TaskCreate({ description: "DOCS: Update docs via /do-docs", status: "pending" })
TaskCreate({ description: "MERGE: Ready for human merge", status: "pending" })
```

As you assess state (Step 2), mark already-completed stages as `completed` and set the current stage to `in_progress`. Update tasks as each sub-skill finishes.

## Step 1: Ensure a GitHub Issue Exists

If an issue number was provided, fetch it:
```bash
gh issue view {number}
```

If NO issue number was provided (just a feature description), create one first:
```bash
gh issue create --title "Brief title" --body "Description from the user's request"
```

**Do not proceed without an issue number.**

## Step 2: Assess Current State

Check what already exists for this issue:

```bash
# Check if a plan doc references this issue
grep -r "#{issue_number}" docs/plans/ 2>/dev/null

# Check if a feature branch exists
git branch -a | grep session/

# Check if a PR already exists
gh pr list --search "#{issue_number}" --state open
```

## Step 3: Pick Up From the Right Stage

Based on the assessment, invoke ONE sub-skill and let it run:

| State | What to invoke | Why |
|-------|---------------|-----|
| No plan exists | `/do-plan {slug}` referencing the issue | Can't build without a plan |
| Plan exists, no branch/PR | `/do-build` with the plan path or issue number | Plan is ready, time to implement |
| Branch exists, tests failing | `/do-patch` then `/do-test` | Fix what's broken |
| Branch exists, tests passing, no PR | `/do-pr-review` | Code is ready for review |
| PR exists, review blockers | `/do-patch` to fix blockers, then `/do-test`, then re-review | Address feedback |
| PR approved, docs not updated | `/do-docs` | Last step before merge |
| PR approved, docs done | Report ready for human merge | Nothing left to automate |

**Do NOT restart from scratch if prior stages are already complete.**

## Hard Rules

1. **NEVER write code directly** — invoke `/do-build` or `/do-patch`
2. **NEVER run tests directly** — invoke `/do-test`
3. **NEVER create plans directly** — invoke `/do-plan`
4. **NEVER skip the issue** — every piece of work needs a GitHub issue
5. **NEVER skip the plan** — every code change needs a plan doc first
6. **NEVER commit to main** — all code goes to `session/{slug}` branches

## Pipeline Stages (Ground Truth)

```
1. ISSUE  — gh issue create (or already exists)
2. PLAN   — /do-plan {slug}
3. BUILD  — /do-build {plan or issue}
4. TEST   — /do-test
5. PATCH  — /do-patch (fix test failures, loop back to TEST)
6. REVIEW — /do-pr-review
7. PATCH  — /do-patch (fix review blockers, loop back to TEST → REVIEW)
8. DOCS   — /do-docs
9. MERGE  — human merges the PR
```

## After Dispatching

Once you invoke a sub-skill and it completes, assess state again (Step 2) and invoke the next sub-skill. Continue until the pipeline reaches MERGE or you hit a state that requires human input.

If a sub-skill fails or the agent gets stuck, report the blocker clearly and stop. Do not attempt to work around the pipeline.
