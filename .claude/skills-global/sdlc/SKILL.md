---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The PM session handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The PM session handles pipeline progression by re-invoking `/sdlc` after each stage completes.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

## Cross-Repo Resolution

For cross-project SDLC work, three resolution mechanisms cover the three different operations the pipeline runs:

- `GH_REPO` (e.g., `tomcounsell/popoto`) — The `gh` CLI natively respects this, so all `gh` commands automatically target the correct repository. Set by `sdk_client.py`.
- `SDLC_TARGET_REPO` (e.g., `~/src/popoto`) — The absolute path to the target project's repo root. Use this for all local filesystem and git operations instead of assuming cwd is the target repo. Set by `sdk_client.py`.
- `AI_REPO_ROOT` (default `~/src/ai`) — Used by the `sdlc-tool` wrapper to resolve where the SDLC tooling Python modules live, independent of cwd. The wrapper dispatches into `tools.sdlc_*` from the ai/ repo even when the orchestrator's cwd is a target project. See [`docs/features/sdlc-tool-resolver.md`](../../../docs/features/sdlc-tool-resolver.md).

**When `SDLC_TARGET_REPO` is set, you MUST use it** for plan lookups, branch listings, and any git commands. The orchestrator's cwd is the ai/ repo, NOT the target project. **For SDLC tooling (`sdlc-tool stage-query`, `sdlc-tool verdict record`, etc.), no env var is needed** — the wrapper resolves `AI_REPO_ROOT` itself with a `~/src/ai` default.

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
| G7: Plan-revising lock | `pr_number` is None AND `plan_revising == True` AND `revision_applied != True` | `/do-plan` (if `last_dispatched_skill == /do-plan-critique`); Escalate `blocked` (if no `/do-plan` in last `MAX_PLAN_REVISING_DISPATCHES + 1` turns) |

**G4 is universal** — it applies to EVERY stage, including DOCS and MERGE. Repeated dispatches of `/do-docs` or `/do-merge` without state change WILL trip the guard.

**G5 applies to CRITIQUE only**, not REVIEW. Review verdicts legitimately change on unchanged diffs (CI flips, new comments, linked issues). G4 handles REVIEW non-determinism instead.

**G7 blocks build while plan revision is in flight.** The lock is set by `/do-plan-critique` (Step 5.6) when the verdict requires a revision pass. It is cleared by `/do-plan` (Phase 4, Step 2b) after committing and pushing the revision. G7 self-heals when `revision_applied: true` is in the plan frontmatter even if the explicit lock-clear step was skipped. G7 is gated on `pr_number is None` so an already-shipped PR is never blocked.

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

## Step 4: Dispatch ONE Sub-Skill (or a Parallel-Safe Pair)

**Do not pattern-match against a hand-edited table.** Instead, call the routing tool and dispatch whatever skill it returns. The tool evaluates all guards (G1–G7) and dispatch rules (14 rows) against live state.

```bash
# Get the next dispatch decision
sdlc-tool next-skill --issue-number {issue_number}
```

The tool outputs JSON in one of three shapes:

Single dispatch:
```json
{"skill": "/do-build", "reason": "...", "row_id": "4a", "dispatched": true}
```

Multi-dispatch (parallel-safe pair, e.g. DOCS + PATCH after REVIEW):
```json
{"multi": true, "dispatched": true,
 "skills": ["/do-docs", "/do-patch"],
 "dispatches": [
   {"skill": "/do-docs", "reason": "...", "row_id": "9"},
   {"skill": "/do-patch", "reason": "...", "row_id": "8"}
 ],
 "reason": "parallel-safe pair: /do-docs (9) + /do-patch (8)"}
```

Blocked:
```json
{"blocked": true, "reason": "G4: stage oscillation ...", "guard_id": "G4"}
```

**How to use the output:**
1. If `multi` is `true`: invoke the `pthread` skill to run all listed `skills` as parallel sub-agents. Record dispatch for the *first* skill in the list (the multi-dispatch is gated by guards as one decision -- a guard fire on the first dispatch replaces the whole pair). After both sub-agents complete, re-invoke `/sdlc` to re-dispatch based on the new pipeline state.
2. If `dispatched` is `true` (single): record the dispatch via `sdlc-tool dispatch record` (see Step 3.5), then invoke the returned `skill`.
3. If `blocked` is `true`: surface the `reason` to the human and wait. Do NOT loop or guess an alternative skill.
4. If neither key is present (error): log the `error` field and escalate to the human.

### Multi-dev fan-out (BUILD stage, Phase 1)

When BUILD is the dispatched stage and the plan decomposes cleanly into independent work units, the PM may fan out one Dev sub-session per unit instead of running a single Dev session through the plan serially. The pattern:

1. Decompose the plan:
   ```bash
   sdlc-decompose docs/plans/{slug}.md
   ```
   Emits a JSON array of units (`unit_id`, `description`, `tasks`). Cap is `MAX_PARALLEL_DEVS` (default 3) -- over-cap decompositions exit non-zero and the PM falls back to single-dev BUILD.
2. If the array has only one unit: dispatch `/do-build` normally (single-dev path).
3. If the array has 2+ units: for each unit `u_i`, **sequentially** call
   ```bash
   valor-session create --role dev --parent $AGENT_SESSION_ID \
     --slug {slug}-u{i} --message "Implement unit {u_i}: {description}. Tasks: ..."
   ```
   Sub-slug worker_keys are distinct, so the worker runs the children concurrently. (Sequential creation only -- timestamp-based ID collision risk for parallel creation.)
4. Call `valor-session wait-for-children --session-id $AGENT_SESSION_ID`. This transitions the PM to `waiting_for_children`; `_finalize_parent_sync` auto-resumes the PM when every child reaches a terminal status.
5. On resume:
   - If any child has non-`completed` terminal status: use `valor-session steer --id <child-id> --message "fix: ..."` to re-drive that child rather than spawning a replacement. Re-wait via `wait-for-children`.
   - If all children completed: dispatch one merge-integration Dev session with slug `{slug}-merge`. Its message instructs it to `git checkout session/{slug}` then `git merge session/{slug}-u1 session/{slug}-u2 ...` in unit_id order. On conflict, the merge session writes the conflict file list to `last_error` and exits non-zero -- escalate to human (no automated conflict resolution).
6. After the merge session completes, steer to TEST stage on the parent slug; the single-dev path resumes.

Fan-out is BUILD-only in Phase 1. TEST, REVIEW, DOCS, MERGE remain serial.

**Before recording and dispatching**, also supply `--proposed-skill` when you already know what skill you intend to invoke (enables G3 PR-lock detection):
```bash
sdlc-tool next-skill --issue-number {issue_number} --proposed-skill /do-build
```

**Context notes for specific rows** (for human reference — the tool encodes these internally):
- *Row 4b/4c concern path*: When critique verdict is "READY TO BUILD (with concerns)", the tool routes to `/do-plan` if `revision_applied` is not yet set in plan frontmatter, else `/do-build`.
- *Row 8/8b patch-review cycle*: Every review finding (blockers, nits, tech debt) must be patched; re-review is mandatory after each patch.
- *Row 10 merge gate*: ALL display stages must show `completed` before merge. The tool enforces this internally.

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

Pipeline state transitions are defined in `agent/pipeline_graph.py` (state-machine bookkeeping: which stage is next-ready when one completes). Dispatch logic is defined in `agent/sdlc_router.py` (`decide_next_dispatch`). Both are accessed at runtime via `sdlc-tool`. The table below is for human readability only.

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
