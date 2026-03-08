---
name: sdlc
description: "The single entry point for all development work. Assesses current state, then dispatches to the right sub-skill. NEVER write code directly — always delegate to /do-plan, /do-build, /do-test, /do-patch, /do-pr-review, /do-docs."
context: fork
---

# SDLC — Development Lifecycle Dispatcher

This skill is a **dispatcher**, not an implementation skill. It figures out where work stands and invokes the right sub-skill. You MUST NOT write code, run tests, or create plans directly — delegate everything.

## Step 0: Create Pipeline Tasks

Immediately create tasks for the full pipeline. These are **strictly sequential** — each stage must complete before the next begins. Do NOT run stages in parallel.

```
TaskCreate({ description: "1. ISSUE: Ensure GitHub issue exists", status: "in_progress" })
TaskCreate({ description: "2. PLAN: Create plan doc via /do-plan", status: "pending" })
TaskCreate({ description: "3. BUILD: Implement via /do-build", status: "pending" })
TaskCreate({ description: "4. TEST: Validate via /do-test", status: "pending" })
TaskCreate({ description: "5. PATCH: Fix test failures via /do-patch", status: "pending" })
TaskCreate({ description: "6. REVIEW: PR review via /do-pr-review", status: "pending" })
TaskCreate({ description: "7. PATCH: Fix review blockers via /do-patch", status: "pending" })
TaskCreate({ description: "8. DOCS: Update docs via /do-docs", status: "pending" })
TaskCreate({ description: "9. MERGE: Auto-merge or wait for human", status: "pending" })
```

As you assess state (Step 2), mark already-completed stages as `completed` and set the current stage to `in_progress`. Use `TaskUpdate` to advance one stage at a time as each sub-skill finishes. Never skip ahead.

## Step 0.5: Session Progress Tracking

Extract the session ID for progress tracking. The bridge injects a line like `SESSION_ID: abc123` into the enriched message context. Look for this pattern in the message you received.

```bash
# Extract SESSION_ID from context (example pattern - adapt to actual message format)
# Look for a line containing "SESSION_ID: " and extract the value after it
# Store in a variable for use throughout the pipeline:
# SESSION_ID="abc123"
```

**Pass SESSION_ID to every sub-skill invocation** by including `SESSION_ID: $SESSION_ID` in the prompt text when invoking sub-skills (do-plan, do-build, do-test, etc.). This ensures progress tracking flows through the entire pipeline.

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

After the issue is verified or created, mark the ISSUE stage as completed (if SESSION_ID exists):
```bash
python -m tools.session_progress --session-id "$SESSION_ID" --stage ISSUE --status completed --issue-url "https://github.com/{owner}/{repo}/issues/{number}" 2>/dev/null || true
```

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
| PR exists, review has tech debt or nits | `/do-patch` to fix them, then `/do-test`, then re-review | Don't leave tech debt behind |
| PR exists, review blockers | `/do-patch` to fix blockers, then `/do-test`, then re-review | Address feedback |
| PR approved (clean), docs not updated | `/do-docs` | Last step before merge |
| PR approved, docs done, auto-merge eligible | Auto-merge: `gh pr merge --squash --delete-branch` + cleanup | Small, clean changes ship fast |
| PR approved, docs done, NOT auto-merge eligible | **STOP. Report completion. Wait for human to say "merge".** | Human gate required |

### Auto-Merge Eligibility

A PR may be auto-merged (no human gate) if **all four** conditions are true:

1. **No open questions** — the pipeline never paused for human input
2. **Clean review** — 0 blockers, 0 tech debt, 0 nits from `/do-pr-review`
3. **All tests pass** — `/do-test` passed without failures
4. **Small change** — total diff is under 150 lines (`gh pr diff --stat` additions + deletions < 150)

Check line count:
```bash
gh pr diff {pr_number} --stat | tail -1
# e.g. "3 files changed, 42 insertions(+), 18 deletions(-)"
# Parse additions + deletions; if total < 150, eligible
```

If ANY condition fails, stop and wait for human "merge" instruction as before.

**IMPORTANT: PR reviews must be published on GitHub.** Before advancing past the REVIEW stage, verify a review or review comment exists on the PR:
```bash
# Check for formal reviews
REVIEW_COUNT=$(gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --jq length)

# Check for review comments (used for self-authored PRs where gh pr review fails)
COMMENT_COUNT=$(gh api repos/{owner}/{repo}/issues/{pr_number}/comments --jq '[.[] | select(.body | startswith("## Review:"))] | length')

# At least one must exist
if [ "$REVIEW_COUNT" -eq 0 ] && [ "$COMMENT_COUNT" -eq 0 ]; then
  echo "No review found — re-invoking /do-pr-review"
fi
```
If both counts are 0, re-invoke `/do-pr-review`. A review that only exists in agent output is NOT a review. For self-authored PRs, a comment starting with "## Review:" counts as a valid review.

**IMPORTANT: Tech debt and nits get patched.** After REVIEW, if the review found ANY tech debt or nits, invoke `/do-patch` to fix them before proceeding to DOCS. Only skip the patch step if the review found zero issues (clean approval).

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
9. MERGE  — auto-merge if eligible, otherwise wait for human + post-merge cleanup
```

## Merge Phase

If auto-merge eligible (see criteria above), proceed directly. Otherwise, wait for the human to say "merge".

Execute the merge and clean up the local worktree and branch:

```bash
# 1. Merge the PR (human-initiated)
gh pr merge {pr_number} --squash --delete-branch

# 2. Clean up local worktree and branch
python scripts/post_merge_cleanup.py {slug}
```

The cleanup script removes the `.worktrees/{slug}/` directory and deletes the local `session/{slug}` branch. It is safe to run even if the worktree or branch is already gone.

Without this step, `gh pr merge --delete-branch` will fail to delete the local branch because git refuses to delete a branch referenced by an active worktree.

## After Dispatching

Once you invoke a sub-skill and it completes, assess state again (Step 2) and invoke the next sub-skill. Continue until the pipeline reaches MERGE or you hit a state that requires human input.

If a sub-skill fails or the agent gets stuck, report the blocker clearly and stop. Do not attempt to work around the pipeline.
