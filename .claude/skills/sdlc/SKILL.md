---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The Observer handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The Observer Agent handles pipeline progression by re-invoking `/sdlc` after each stage completes.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

## Cross-Repo Resolution

For cross-project SDLC work, two environment variables are automatically set by `sdk_client.py`:

- `GH_REPO` (e.g., `tomcounsell/popoto`) — The `gh` CLI natively respects this, so all `gh` commands automatically target the correct repository.
- `SDLC_TARGET_REPO` (e.g., `/Users/valorengels/src/popoto`) — The absolute path to the target project's repo root. Use this for all local filesystem and git operations instead of assuming cwd is the target repo.

**When `SDLC_TARGET_REPO` is set, you MUST use it** for plan lookups, branch listings, and any git commands. The orchestrator's cwd is the ai/ repo, NOT the target project.

## Step 1: Resolve the Issue or PR

Determine whether the input is an issue reference or a PR reference:

- **Issue reference** (e.g., `issue 123`, `issue #123`): Fetch with `gh issue view {number}`
- **PR reference** (e.g., `PR 363`, `pr #363`): Fetch with `gh pr view {number}` to get the branch name, review state, and check status. Then extract the linked issue number from the PR body (look for `Closes #N` or `Fixes #N`).

```bash
# For issue references:
gh issue view {number}

# For PR references — get structured state for assessment:
gh pr view {number} --json number,title,state,headRefName,reviewDecision,statusCheckRollup,body
```

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
gh pr list --search "#{issue_number}" --state open

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
| Branch exists, no PR | `/do-build` with plan path | Build must create the PR — resume build |
| Tests failing | `/do-patch` then `/do-test` | Fix what is broken |
| PR exists, no review | `/do-pr-review {pr_number}` | Code is ready for review |
| PR review has blockers or nits | `/do-patch` | Address review feedback |
| Review clean, docs not updated | `/do-docs` | Last step before merge |
| All stages complete | Report done | Observer delivers to human |

**CRITICAL**: Before dispatching `/do-pr-review`, verify a PR actually exists by checking the output of `gh pr list`. If no PR exists for this branch, dispatch `/do-build` instead — it handles PR creation. Never send `/do-pr-review` without a real PR number.

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

The canonical pipeline graph is defined in `bridge/pipeline_graph.py`. All routing
derives from that module. The table below is for human readability only.

```
Happy path: ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
Cycles:     TEST(fail) -> PATCH -> TEST
            REVIEW(fail|partial) -> PATCH -> TEST -> REVIEW
```

| Stage | Skill | Notes |
|-------|-------|-------|
| ISSUE | /do-issue | Or already exists |
| PLAN | /do-plan {slug} | |
| BUILD | /do-build {plan or issue} | |
| TEST | /do-test | |
| PATCH | /do-patch | Routing-only; not a display stage |
| REVIEW | /do-pr-review | |
| DOCS | /do-docs | |
| MERGE | — | Human decision (Observer reports completion) |

This list is for reference only. This skill does NOT advance through stages -- it picks the right one and returns.
