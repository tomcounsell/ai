---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The Observer handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The Observer Agent handles pipeline progression by re-invoking `/sdlc` after each stage completes.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

## Cross-Repo Resolution

When working on a non-ai project (e.g., popoto), the worker runs with `cwd=ai/` but the target repo is different. To ensure `gh` commands resolve against the correct repo, extract the `GITHUB:` line from the prompt context injected by `sdk_client.py`.

```bash
# Extract the target repo from prompt context (e.g., "GITHUB: tomcounsell/popoto")
# Look for a line like "GITHUB: org/repo" in the enriched message you received.
# If found, use --repo for ALL gh commands in this skill.
# If not found (local ai repo work), omit --repo (defaults to cwd repo).
REPO_FLAG=""
# Example: GITHUB_REPO="tomcounsell/popoto" extracted from context
if [ -n "$GITHUB_REPO" ]; then
  REPO_FLAG="--repo $GITHUB_REPO"
fi
```

**CRITICAL**: Always use `$REPO_FLAG` with every `gh` command below. Without it, cross-project SDLC work silently resolves issues and PRs against the wrong repository.

## Step 1: Resolve the Issue or PR

Determine whether the input is an issue reference or a PR reference:

- **Issue reference** (e.g., `issue 123`, `issue #123`): Fetch with `gh issue view {number} $REPO_FLAG`
- **PR reference** (e.g., `PR 363`, `pr #363`): Fetch with `gh pr view {number} $REPO_FLAG` to get the branch name, review state, and check status. Then extract the linked issue number from the PR body (look for `Closes #N` or `Fixes #N`).

```bash
# For issue references:
gh issue view {number} $REPO_FLAG

# For PR references — get structured state for assessment:
gh pr view {number} $REPO_FLAG --json number,title,state,headRefName,reviewDecision,statusCheckRollup,body
```

After fetching, **verify the reference belongs to the target project**: check that the URL contains the expected org/repo. If it resolves to a different repo, you have a cross-repo mismatch -- stop and report the error.

**PR state informs Step 2 assessment**: When a PR is provided, its current state (checks passing/failing, review approved/changes-requested, etc.) tells you which pipeline stage to resume from. Skip stages that are already complete -- do not restart from scratch.

If NO issue or PR number was provided (just a feature description), invoke `/do-issue` to create a quality issue. Do not proceed without an issue number.

## Step 2: Assess Current State

Check what already exists for this issue:

```bash
# Check if a plan doc references this issue
grep -r "#{issue_number}" docs/plans/ 2>/dev/null

# Check if a feature branch exists
git branch -a | grep session/

# Check if a PR already exists
gh pr list --search "#{issue_number}" --state open $REPO_FLAG

# Check test status (if branch/PR exists)
# Check review status (if PR exists)
# Check if docs exist
```

## Step 3: Dispatch ONE Sub-Skill

Based on the assessment, invoke exactly ONE sub-skill and return.

| State | Invoke | Reason |
|-------|--------|--------|
| No plan exists | `/do-plan {slug}` | Cannot build without a plan |
| Plan exists, no branch/PR | `/do-build` with plan path | Plan is ready, implement it |
| Tests failing | `/do-patch` then `/do-test` | Fix what is broken |
| Tests passing, no PR review | `/do-pr-review` | Code is ready for review |
| PR review has blockers or nits | `/do-patch` | Address review feedback |
| Review clean, docs not updated | `/do-docs` | Last step before merge |
| All stages complete | Report done | Observer delivers to human |

Do NOT restart from scratch if prior stages are already complete.

## Hard Rules

1. **NEVER write code directly** -- invoke `/do-build` or `/do-patch`
2. **NEVER run tests directly** -- invoke `/do-test`
3. **NEVER create plans directly** -- invoke `/do-plan`
4. **NEVER skip the issue** -- every piece of work needs a GitHub issue
5. **NEVER skip the plan** -- every code change needs a plan doc first
6. **NEVER commit to main** -- all code goes to `session/{slug}` branches
7. **NEVER loop** -- invoke one sub-skill, then return. The Observer handles progression.

## Pipeline Stages Reference

```
1. ISSUE  — /do-issue (or already exists)
2. PLAN   — /do-plan {slug}
3. BUILD  — /do-build {plan or issue}
4. TEST   — /do-test
5. PATCH  — /do-patch (fix test failures)
6. REVIEW — /do-pr-review
7. PATCH  — /do-patch (fix review blockers)
8. DOCS   — /do-docs
9. MERGE  — Human decision (Observer reports completion)
```

This list is for reference only. This skill does NOT advance through stages -- it picks the right one and returns.
