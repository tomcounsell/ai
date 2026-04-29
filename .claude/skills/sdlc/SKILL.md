---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The PM session handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The PM session handles pipeline progression by re-invoking `/sdlc` after each stage completes.

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

## Step 1.5: Ensure Local Session Exists

After resolving the issue number, ensure a local SDLC session exists in Redis so that stage markers can track progress. This is a no-op for bridge-initiated sessions (which already have `VALOR_SESSION_ID`), but critical for local Claude Code sessions.

```bash
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}" 2>/dev/null || true
```

This is idempotent -- running it multiple times for the same issue reuses the same session. Inside a bridge-initiated session (where `VALOR_SESSION_ID` is set), the call is a true no-op — it returns the already-active session without creating a new record. Do NOT export `AGENT_SESSION_ID` -- env vars do not persist across Claude Code bash blocks. Instead, pass `--issue-number` to all subsequent `sdlc_stage_marker` and `sdlc_stage_query` invocations.

## Step 2: Assess Current State

Check what already exists for this issue. Use `$SDLC_TARGET_REPO` for local operations (defaults to `.` for same-repo work). Run ALL of these checks — do not skip any.

### Step 2.0: Query stage_states from PipelineStateMachine (primary signal)

Query the PM session's `stage_states` for authoritative stage completion data. This is the **exclusive signal** for routing decisions. Stage completion is determined ONLY by stored state — never by artifact inference.

Run the stage query tool directly and read its output from the tool result -- no shell substitution, no pipes, no environment-variable capture. The tool resolves the active session from `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, or `--issue-number` internally.

```bash
sdlc-tool stage-query --issue-number {issue_number}
```

Interpret the JSON output from the tool result:
- Non-empty object with stage keys (e.g. `{"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"}`): use it as the **exclusive signal** for the dispatch table. A stage is considered complete ONLY if its value is `"completed"`. Skip steps 2a-2e.
- Empty `{}` or an `unavailable` marker: fall through to the dispatch-history fallback in steps 2a-2e. Do NOT infer stage completion from artifacts.

### Steps 2a-2e: Dispatch History Fallback

These checks run ONLY when stage_states is unavailable (empty JSON from step 2.0). When stage_states IS available, skip directly to the dispatch table using stage_states as the source of truth.

**IMPORTANT: Never infer stage completion from artifacts (plan files, PR existence, docs/ files, etc.). Stage completion is exclusively determined by stored state.**

When stage_states is unavailable, use conversation context to identify which skills were already dispatched in this session. Artifacts are used only to check preconditions (e.g., "does a PR exist?") — not to declare stages complete.

Run each check as a separate single-line command and read the output from the tool result. `$SDLC_TARGET_REPO` is exported by the harness so `git -C` picks it up without further shell composition; `gh` uses `$GH_REPO` automatically for the cross-repo case.

```bash
# 2a. Check if a plan doc references this issue
grep -r "#{issue_number}" docs/plans/
```

```bash
# 2b. List all branches (filter for session/ prefix in the tool result)
git -C "$SDLC_TARGET_REPO" branch -a
```

```bash
# 2c. Check if a PR already exists
gh pr list --search "#{issue_number}" --state open
```

Filter the outputs by reading the tool results -- do not pipe through `grep` / `head` / `||`. The LLM interprets the output and decides the next step.

If a PR exists, fetch its full state for assessment:
```bash
# 2d. Get PR state: checks, review, branch
gh pr view {pr_number} --json number,headRefName,reviewDecision,statusCheckRollup,body

# 2e. Check review status — look for APPROVED, CHANGES_REQUESTED, or no review
# reviewDecision: "APPROVED" means formal GitHub review approved (non-self-authored PRs)
# reviewDecision: "CHANGES_REQUESTED" means formal GitHub review requested changes
# reviewDecision: "" (empty) — AMBIGUOUS for self-authored PRs:
#   - For non-self-authored PRs: no review posted yet
#   - For self-authored PRs: expected even after review — check _verdicts["REVIEW"] from sdlc_stage_query
# Always cross-check _meta.latest_review_verdict before concluding no review exists.
```

## Step 3: Check Documentation Status

This step is REQUIRED when a PR exists and review is clean (APPROVED). Skip it only if the pipeline hasn't reached the REVIEW stage yet.

Run each check as a separate single-line command -- no pipes, no command substitution, no `||` fallbacks. The LLM interprets the tool results and decides the next step.

```bash
# 3a. List files changed in the PR (count docs/ entries from the tool result)
gh pr diff {pr_number} --name-only
```

```bash
# 3b. Find the plan path for this issue (first match from the tool result)
grep -rl "#{issue_number}" docs/plans/
```

```bash
# 3c. Read the plan's Documentation section (inspect for unchecked tasks in the tool result)
cat docs/plans/{plan-filename}.md
```

For the DOCS stage completion check, re-read the `sdlc-tool stage-query` output from Step 2.0. Do not pipe JSON through a shell here.

**Decision logic for docs**:
- If the plan has a `## Documentation` section with unchecked tasks → docs NOT done
- If PR has zero `docs/` file changes AND plan requires doc tasks → docs NOT done
- If docs tasks are all checked AND `docs/` changes exist in PR → docs done
- When in doubt, dispatch `/do-docs` — it is idempotent and will no-op if nothing needs updating

## Step 3.5: Legal Dispatch Guards

Before consulting the dispatch table in Step 4, evaluate the following guards against the enriched `sdlc_stage_query` output (`stages` + `_meta`). If any guard fires, it forces a specific dispatch or escalates to `blocked`, and Step 4 is SKIPPED.

The canonical Python implementation is `agent.sdlc_router.decide_next_dispatch()`. The parity test in `tests/unit/test_sdlc_skill_md_parity.py` asserts this markdown stays in sync with the Python rules.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Latest critique verdict contains `NEEDS REVISION` or `MAJOR REWORK` AND `last_dispatched_skill == /do-plan-critique` | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` (2) AND CRITIQUE is not completed | Escalate: `blocked` with reason `critique cycle cap reached` |
| G3: PR lock | `pr_number` is set AND (`last_dispatched_skill` OR proposed dispatch) is `/do-plan` or `/do-plan-critique` | `/do-merge` (if REVIEW and DOCS complete), `/do-patch` (if review requested changes), else `/do-pr-review` |
| G4: Oscillation (universal) | `same_stage_dispatch_count >= 3` | Escalate: `blocked` with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| G5: Unchanged critique artifact | `_verdicts["CRITIQUE"]` has `artifact_hash` AND current plan file hash matches | Use cached verdict: `/do-plan` (NEEDS REVISION) or `/do-build` (READY TO BUILD). Never re-dispatch `/do-plan-critique` on an unchanged plan. |
| G6: Terminal merge ready | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` |

**G4 is universal** — it applies to EVERY stage, including DOCS and MERGE. Repeated dispatches of `/do-docs` or `/do-merge` without state change WILL trip the guard.

**G5 applies to CRITIQUE only**, not REVIEW. Review verdicts legitimately change on unchanged diffs (CI flips, new comments, linked issues). G4 handles REVIEW non-determinism instead.

After evaluating guards, record the dispatch decision via `sdlc-tool dispatch record` BEFORE invoking the sub-skill. This preserves the G4 oscillation signal even if the sub-skill crashes mid-execution.

```bash
# Record a dispatch event (call BEFORE invoking the sub-skill)
sdlc-tool dispatch record --skill /do-build --issue-number {issue_number}

# Record with PR context (for review/patch/merge stages)
sdlc-tool dispatch record --skill /do-pr-review --issue-number {issue_number} --pr-number {pr_number}

# Inspect the dispatch history (debug G4 state)
sdlc-tool dispatch get --issue-number {issue_number}
```

The CLI wraps `agent.sdlc_router.record_dispatch()` and `tools.stage_states_helpers.update_stage_states()` — it is the correct runtime entry point. Never call `record_dispatch()` directly from a shell or skill script; always use `sdlc-tool dispatch record`.

## Step 4: Dispatch ONE Sub-Skill

Based on the assessment, invoke exactly ONE sub-skill and return.

| # | State | Invoke | Reason |
|---|-------|--------|--------|
| 1 | No plan exists | `/do-plan {slug}` | Cannot build without a plan |
| 2 | Plan exists, not yet critiqued | `/do-plan-critique` with plan path | Plan must pass critique before build |
| 3 | Plan critiqued (NEEDS REVISION) | `/do-plan {slug}` | Revise plan based on critique findings |
| 4a | Plan critiqued (READY TO BUILD, zero concerns), no branch/PR | `/do-build` with plan path | No revision needed — critique passed cleanly |
| 4b | Plan critiqued (READY TO BUILD, concerns present), `revision_applied` not set in plan frontmatter | `/do-plan {slug}` with directive to apply concern findings | Revision pass before build — embed Implementation Notes into plan text |
| 4c | Plan critiqued (READY TO BUILD, concerns present), `revision_applied: true` in plan frontmatter | `/do-build` with plan path | Revision pass already complete — proceed to build |
| 5 | Branch exists, no PR | `/do-build` with plan path | Build must create the PR — resume build |
| 6 | Tests failing | `/do-patch` then `/do-test` | Fix what is broken |
| 7 | PR exists, no review | `/do-pr-review {pr_number}` | Code is ready for review |
| 8 | PR review has findings (blockers, nits, OR tech debt) | `/do-patch` | ALL findings must be addressed |
| 8b | Patch applied after review findings | `/do-pr-review {pr_number}` | Re-review is REQUIRED after every patch |
| 9 | Review APPROVED with zero findings, docs NOT done (see Step 3) | `/do-docs` | Docs are required before merge |
| 10 | Review APPROVED with zero findings, docs done, AND all display stages show `completed` in stage_states (or stage_states unavailable), ready to merge | `/do-merge {pr_number}` | Execute programmatic merge gate |
| 10b | stage_states unavailable AND an open PR exists for this issue | `/do-merge {pr_number}` | Fallback: if stage_states cannot confirm stages but an open PR exists after DOCS, dispatch merge |

**Row 10 merge gate**: ALL display stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS) must show `completed` in stage_states before dispatching Row 10. This prevents stages from being silently skipped when artifacts happen to exist from a different stage's work (e.g., build creating docs does not satisfy the DOCS stage). If stage_states is unavailable, use conversation dispatch history — if DOCS was never dispatched in this session, dispatch it. **Fallback**: When stage_states is unavailable AND an open PR exists for this issue (`gh pr list --search "#{issue_number}" --state open`), dispatch `/do-merge` — an open PR after DOCS means the pipeline reached the merge gate but the PM exited prematurely.

**Row 4b/4c is the concern-triggered revision path**: When the critique verdict is "READY TO BUILD (with concerns)", the SDLC router dispatches `/do-plan` with a directive to apply the concern findings — specifically, to embed each concern's Implementation Note into the plan text. This is a plan clarity step, not a defect fix. CONCERNs are not reclassified as blockers; they remain acknowledged risks. After the revision pass, the plan's frontmatter is updated with `revision_applied: true`. The next SDLC invocation then detects this flag and routes to Row 4c (`/do-build`). Detection logic:
- Row 4b: critique verdict contains "with concerns" AND plan frontmatter does NOT contain `revision_applied: true`
- Row 4a or 4c: critique verdict contains "no concerns" OR plan frontmatter contains `revision_applied: true`

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
7. **NEVER loop** -- invoke one sub-skill, then return. The PM session handles progression.

## Pipeline Stages Reference

The canonical pipeline graph is defined in `bridge/pipeline_graph.py`. All routing
derives from that module. The table below is for human readability only.

```
Happy path: ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
Cycles:     CRITIQUE(fail) -> PLAN -> CRITIQUE (max 2 cycles)
            TEST(fail) -> PATCH -> TEST
            REVIEW(fail|partial) -> PATCH -> TEST -> REVIEW
```

| Stage | Skill | Dev Model | Notes |
|-------|-------|-----------|-------|
| ISSUE | /do-issue | — | Or already exists |
| PLAN | /do-plan {slug} | opus | Adversarial design |
| CRITIQUE | /do-plan-critique | opus | Adversarial review |
| BUILD | /do-build {plan or issue} | sonnet | Plan execution |
| TEST | /do-test | sonnet | Deterministic runs |
| PATCH | /do-patch | sonnet | Targeted fix (see resume rules in PM persona) |
| REVIEW | /do-pr-review | opus | Code review judgment |
| DOCS | /do-docs | sonnet | Structured writing |
| MERGE | /do-merge {pr_number} | sonnet | Programmatic gate: verifies all stages, then merges |

The **Dev Model** column shows the model the PM should pass via `--model` when spawning a dev session for that stage (see Stage→Model Dispatch Table in PM persona).

This list is for reference only. This skill does NOT advance through stages -- it picks the right one and returns.
