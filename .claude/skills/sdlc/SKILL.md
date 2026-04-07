---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The PM (ChatSession) handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The PM (ChatSession) handles pipeline progression by re-invoking `/sdlc` after each stage completes.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

## Cross-Repo Resolution

For cross-project SDLC work, two environment variables are automatically set by `sdk_client.py`:

- `GH_REPO` (e.g., `tomcounsell/popoto`) — The `gh` CLI natively respects this, so all `gh` commands automatically target the correct repository.
- `SDLC_TARGET_REPO` (e.g., `~/src/popoto`) — The absolute path to the target project's repo root. Use this for all local filesystem and git operations instead of assuming cwd is the target repo.

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

Check what already exists for this issue. Use `$SDLC_TARGET_REPO` for local operations (defaults to `.` for same-repo work). Run ALL of these checks — do not skip any.

### Step 2.0: Query stage_states from PipelineStateMachine (primary signal)

Query the PM session's `stage_states` for authoritative stage completion data. This is the **exclusive signal** for routing decisions. Stage completion is determined ONLY by stored state — never by artifact inference.

```bash
# Query stage_states from the PM session via CLI tool
STAGE_STATES=$(python -m tools.sdlc_stage_query --session-id "$VALOR_SESSION_ID" 2>/dev/null)

# If VALOR_SESSION_ID is not set, try AGENT_SESSION_ID
if [ -z "$STAGE_STATES" ] || [ "$STAGE_STATES" = "{}" ]; then
    STAGE_STATES=$(python -m tools.sdlc_stage_query --session-id "$AGENT_SESSION_ID" 2>/dev/null)
fi

# Parse the result — if non-empty JSON, use it as primary signal
# Example output: {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress", ...}
```

**Decision logic:**
- If `STAGE_STATES` is non-empty JSON with stage data: use it as the **exclusive signal** for the dispatch table. A stage is considered complete ONLY if its value is `"completed"` in stage_states. Skip steps 2a-2e.
- If `STAGE_STATES` is empty `{}` or unavailable (no PM session, local Claude Code): use conversation dispatch history to determine what was already dispatched in this session. Do NOT infer from artifacts. If nothing has been dispatched, start from the beginning of the pipeline.

### Steps 2a-2e: Dispatch History Fallback

These checks run ONLY when stage_states is unavailable (empty JSON from step 2.0). When stage_states IS available, skip directly to the dispatch table using stage_states as the source of truth.

**IMPORTANT: Never infer stage completion from artifacts (plan files, PR existence, docs/ files, etc.). Stage completion is exclusively determined by stored state.**

When stage_states is unavailable, use conversation context to identify which skills were already dispatched in this session. Artifacts are used only to check preconditions (e.g., "does a PR exist?") — not to declare stages complete.

```bash
REPO="${SDLC_TARGET_REPO:-.}"

# 2a. Check if a plan doc references this issue
grep -r "#{issue_number}" "$REPO/docs/plans/" 2>/dev/null

# 2b. Check if a feature branch exists (in the target repo)
git -C "$REPO" branch -a | grep session/

# 2c. Check if a PR already exists (gh uses GH_REPO automatically)
gh pr list --search "#{issue_number}" --state open
```

If a PR exists, fetch its full state for assessment:
```bash
# 2d. Get PR state: checks, review, branch
gh pr view {pr_number} --json number,headRefName,reviewDecision,statusCheckRollup,body

# 2e. Check review status — look for APPROVED, CHANGES_REQUESTED, or no review
# reviewDecision: "APPROVED" means review is clean
# reviewDecision: "CHANGES_REQUESTED" means blockers exist
# reviewDecision: "" (empty) means no review yet
```

## Step 3: Check Documentation Status

This step is REQUIRED when a PR exists and review is clean (APPROVED). Skip it only if the pipeline hasn't reached the REVIEW stage yet.

```bash
# 3a. Check if any docs/ files were changed in the PR
gh pr diff {pr_number} --name-only | grep -c '^docs/' || echo "0"

# 3b. Check the plan's ## Documentation section for required doc tasks
PLAN_PATH=$(grep -rl "#{issue_number}" "$REPO/docs/plans/" 2>/dev/null | head -1)
if [ -n "$PLAN_PATH" ]; then
    # Extract the Documentation section and check for unchecked tasks
    sed -n '/^## Documentation/,/^## /p' "$PLAN_PATH" | grep -c '\- \[ \]' || echo "0"
fi

# 3c. Check stage_states for DOCS stage completion (reuse $STAGE_STATES from Step 2.0)
# NOTE: No re-query needed -- $STAGE_STATES was already fetched in Step 2.0
echo "$STAGE_STATES" | python -c "import sys,json; s=json.load(sys.stdin); print('DOCS_DONE' if s.get('DOCS')=='completed' else 'DOCS_NOT_DONE')" 2>/dev/null || echo "DOCS_NOT_DONE"
```

**Decision logic for docs**:
- If the plan has a `## Documentation` section with unchecked tasks → docs NOT done
- If PR has zero `docs/` file changes AND plan requires doc tasks → docs NOT done
- If docs tasks are all checked AND `docs/` changes exist in PR → docs done
- When in doubt, dispatch `/do-docs` — it is idempotent and will no-op if nothing needs updating

## Step 4: Dispatch ONE Sub-Skill

Based on the assessment, invoke exactly ONE sub-skill and return.

| # | State | Invoke | Reason |
|---|-------|--------|--------|
| 1 | No plan exists | `/do-plan {slug}` | Cannot build without a plan |
| 2 | Plan exists, not yet critiqued | `/do-plan-critique` with plan path | Plan must pass critique before build |
| 3 | Plan critiqued (NEEDS REVISION) | `/do-plan {slug}` | Revise plan based on critique findings |
| 4 | Plan critiqued (READY TO BUILD), no branch/PR | `/do-build` with plan path | Critique passed, implement it |
| 5 | Branch exists, no PR | `/do-build` with plan path | Build must create the PR — resume build |
| 6 | Tests failing | `/do-patch` then `/do-test` | Fix what is broken |
| 7 | PR exists, no review | `/do-pr-review {pr_number}` | Code is ready for review |
| 8 | PR review has findings (blockers, nits, OR tech debt) | `/do-patch` | ALL findings must be addressed |
| 8b | Patch applied after review findings | `/do-pr-review {pr_number}` | Re-review is REQUIRED after every patch |
| 9 | Review APPROVED with zero findings, docs NOT done (see Step 3) | `/do-docs` | Docs are required before merge |
| 10 | Review APPROVED with zero findings, docs done, AND all display stages show `completed` in stage_states (or stage_states unavailable), ready to merge | Report done | PM delivers to human |

**Row 10 merge gate**: ALL display stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS) must show `completed` in stage_states before dispatching Row 10. This prevents stages from being silently skipped when artifacts happen to exist from a different stage's work (e.g., build creating docs does not satisfy the DOCS stage). If stage_states is unavailable, use conversation dispatch history — if DOCS was never dispatched in this session, dispatch it.

**Row 8/8b is the patch-review cycle**: A "minimum approve" with unresolved nits or tech debt is NOT sufficient. Every finding from the review — blockers, nits, suggestions, and tech debt — must be patched or explicitly annotated with inline comments explaining why the finding was left in place. After patching, a fresh `/do-pr-review` is mandatory to verify all findings were addressed. This cycle repeats until the review returns zero unresolved findings.

**Row 9 is the docs gate**: A clean review does NOT mean "all stages complete." You MUST run Step 3's docs check before dispatching row 10. If you cannot confirm docs are done, dispatch `/do-docs`.

**CRITICAL**: Before dispatching `/do-pr-review`, verify a PR actually exists by checking the output of `gh pr list`. If no PR exists for this branch, dispatch `/do-build` instead — it handles PR creation. Never send `/do-pr-review` without a real PR number.

Do NOT restart from scratch if prior stages are already complete.

## Hard Rules

1. **NEVER write code directly** -- invoke `/do-build` or `/do-patch`
2. **NEVER run tests directly** -- invoke `/do-test`
3. **NEVER create plans directly** -- invoke `/do-plan`
4. **NEVER skip the issue** -- every piece of work needs a GitHub issue
5. **NEVER skip the plan** -- every code change needs a plan doc first
6. **NEVER commit to main** -- all code goes to `session/{slug}` branches
7. **NEVER loop** -- invoke one sub-skill, then return. The PM (ChatSession) handles progression.

## Pipeline Stages Reference

The canonical pipeline graph is defined in `bridge/pipeline_graph.py`. All routing
derives from that module. The table below is for human readability only.

```
Happy path: ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
Cycles:     CRITIQUE(fail) -> PLAN -> CRITIQUE (max 2 cycles)
            TEST(fail) -> PATCH -> TEST
            REVIEW(fail|partial) -> PATCH -> TEST -> REVIEW
```

| Stage | Skill | Notes |
|-------|-------|-------|
| ISSUE | /do-issue | Or already exists |
| PLAN | /do-plan {slug} | |
| CRITIQUE | /do-plan-critique | Validates plan before build |
| BUILD | /do-build {plan or issue} | |
| TEST | /do-test | |
| PATCH | /do-patch | Routing-only; not a display stage |
| REVIEW | /do-pr-review | |
| DOCS | /do-docs | |
| MERGE | — | Human decision (PM reports completion) |

This list is for reference only. This skill does NOT advance through stages -- it picks the right one and returns.
