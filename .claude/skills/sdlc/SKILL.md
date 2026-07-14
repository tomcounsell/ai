---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The PM session handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE sub-skill, and returns. The PM session handles pipeline progression by re-invoking `/sdlc` after each stage completes. In a local Claude Code session (no PM loop), use `/do-sdlc` to supervise the full pipeline in one invocation.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

## Worktree & branch ownership

**Slug identity always wins.** Each issue's build fork exclusively owns `.worktrees/{slug}` and `session/{slug}`, derived from the plan slug — this is the single source of truth (`worktree_manager.py` + `resolve_branch_for_stage`). Do NOT pre-allocate per-supervisor `.worktrees/sdlc-{N}` lanes: nothing reads a lane override, so lane instructions are silently dropped and every issue's builders land in `.worktrees/{slug}` regardless. Converging fork + supervisor onto one branch per plan is deliberate — it structurally collapses duplicate PRs, since GitHub permits only one open PR per head branch. Concurrent builders inside the one slug worktree must write disjoint file sets (do-build's `Parallel: true` convention: no shared-file writes).

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

## Step 1.5: Session Tracking

The run identity is a single `run_id` minted **once** at the start of the run and held for the whole pipeline by the supervising session (issue #2026, WS1 — single-owner lease). Stage forks **inherit** that `run_id` through the supervised-run signal; they never re-mint and never juggle the lock.

```bash
# Start of the run — mint the run_id and acquire the lease (writes the signal):
sdlc-tool session-ensure --issue-number {issue_number}
# => {"session_id": ..., "created": ..., "run_id": "<hex>"}
```

**Inheriting the run_id in a stage fork.** If this conversation already carries the `run_id` (from the supervisor or an earlier stage), just use it — do **not** call `session-ensure` again. If you re-run a bare `session-ensure` while the supervised run is live, the tool refuses with the named `SUPERVISED_RUN_ACTIVE`, carrying the supervisor's `run_id` for you to inherit:

```json
{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id": "<hex>", "owner_run_id": "<hex>", "owner_session_id": "..."}
```

Read `run_id` from that payload and continue the stage under it — this is inheritance, not a block. The refusal mints nothing: the only path a bare ensure has under a live signal is "use the supervisor's `run_id`." A stale/expired signal (the lease was released at run end, or its TTL lapsed) falls back to normal standalone semantics and mints fresh.

`session-ensure` remains the EXCLUSIVE minting site for the run identity (issue #2003). Three rules that DO matter:

- **Pass `--issue-number` to every `sdlc-tool` invocation.** It is the authoritative session selector.
- **Pass `--run-id {run_id}` to every state *write*** (`dispatch record`, `verdict record`, `meta-set`, `stage-marker`, and `merge_predicate --run-id`). A missing flag is a named non-zero error (`RUN_ID_REQUIRED`) — no mint, no adopt. Read-only subcommands (`stage-query`, `next-skill`, `verdict get`, `dispatch get`) take no run-id.
- **Do NOT export `AGENT_SESSION_ID`** — env vars do not persist across Claude Code bash blocks.

## Step 2: Assess Current State

Check what already exists for this issue. Use `$SDLC_TARGET_REPO` for local operations (defaults to `.` for same-repo work). Run ALL of these checks — do not skip any.

**Command discipline (applies to every check in Steps 2-3):** run each check as a separate single-line command and read the output from the tool result — no pipes, no command substitution, no `||` fallbacks, no environment-variable capture. You interpret the output and decide the next step.

### Step 2.0: Query stage_states from PipelineStateMachine (primary signal)

Query the PM session's `stage_states` for authoritative stage completion data. This is the **exclusive signal** for routing decisions. Stage completion is determined ONLY by stored state — never by artifact inference.

The tool resolves the active session from `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, or `--issue-number` internally.

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

`$SDLC_TARGET_REPO` is exported by the harness so `git -C` picks it up without further shell composition; `gh` uses `$GH_REPO` automatically for the cross-repo case.

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
# Cross-check with live refs — the --search index lags GitHub; --head queries live refs:
#   gh pr list --head session/{slug} --state open   (reuse if present; keyed by head branch, not issue #)
```

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

## Step 3.5: Legal Dispatch Guards (reference)

`sdlc-tool next-skill` (Step 4) evaluates these guards itself — do NOT re-evaluate them by hand. The table exists so you can interpret a `blocked` decision or a forced dispatch when the tool returns one. Canonical implementation: `agent.sdlc_router.decide_next_dispatch()`; the parity test `tests/unit/test_sdlc_skill_md_parity.py` keeps this table in sync with the Python rules.

Guards are evaluated in the **pinned `GUARDS` list order** `[G1, G2, G3, G4, G8, G7, G5, G6]` — the first to return a non-`None` decision wins. Guard IDs are historical (assigned in introduction order), not evaluation order; the table below is listed in evaluation order:

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Latest critique verdict contains `NEEDS REVISION` or `MAJOR REWORK` AND `last_dispatched_skill == /do-plan-critique` | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` (2) AND CRITIQUE is not completed | Escalate: `blocked` with reason `critique cycle cap reached` |
| G3: PR lock | `pr_number` is set AND (`last_dispatched_skill` OR proposed dispatch) is `/do-plan` or `/do-plan-critique` | `/do-merge` (if REVIEW and DOCS complete), `/do-patch` (if review requested changes), else `/do-pr-review` |
| G4: Oscillation (universal) | `same_stage_dispatch_count >= 3` | Escalate: `blocked` with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| G8: Stage-advance verification | `context["stage_artifacts_verified"] is False` (a claimed stage artifact — PR, branch, plan commit — failed live verification) | Re-dispatch the skill owning `context["unverified_stage"]` |
| G7: Plan-revising lock | `pr_number` is None AND `plan_revising == True` AND `revision_applied != True` | `/do-plan` (if `last_dispatched_skill == /do-plan-critique`); Escalate `blocked` (if no `/do-plan` in last `MAX_PLAN_REVISING_DISPATCHES + 1` turns) |
| G5: Unchanged critique artifact | `_verdicts["CRITIQUE"]` has `artifact_hash` AND current plan file hash matches | Use cached verdict: `/do-plan` (NEEDS REVISION) or `/do-build` (READY TO BUILD). Never re-dispatch `/do-plan-critique` on an unchanged plan. |
| G6: Terminal merge ready | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` |

**G4 is universal** — it applies to EVERY stage, including DOCS and MERGE. Repeated dispatches of `/do-docs` or `/do-merge` without state change WILL trip the guard.

**G4 precedes G8 by design (issue #1267).** G8 re-dispatches the same stage's skill on a false artifact claim, with nothing upstream to stop it on a persistently false claim. Because G4 runs first, it fires and blocks once `same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES` before G8 gets another chance to re-dispatch — silent re-dispatch first, escalate via the existing G4 cap second, not an immediate block on the first mismatch.

**G8 makes no live calls.** Live verification of claimed stage artifacts happens in the next-skill context-assembly path (`tools/sdlc_next_skill.py`), which sets `context["stage_artifacts_verified"]` / `context["unverified_stage"]`; G8 (`agent.sdlc_router.guard_g8_artifact_verification`) only reads those flags. This keeps `agent/sdlc_router.py` import-free of `tools/` (see `tests/unit/test_architectural_constraints.py`). Absent/unset/`True` is a no-op.

**G5 applies to CRITIQUE only**, not REVIEW. Review verdicts legitimately change on unchanged diffs (CI flips, new comments, linked issues). G4 handles REVIEW non-determinism instead.

**G1 open-PR step-aside (#1932):** once `pr_number` is set, G1 no longer fires — it steps aside and defers to G3, the canonical open-PR plan-stage redirect. Without this, a NEEDS REVISION/MAJOR REWORK critique verdict recorded before the PR was opened could route a shipped PR back to `/do-plan`.

**G5 open-PR step-aside (#1932):** on its NEEDS_REVISION/MAJOR_REWORK branch (cached critique verdict, unchanged plan hash), G5 also steps aside once `pr_number` is set and defers to G3 instead of re-dispatching `/do-plan`. The READY_TO_BUILD branch already deferred on `pr_number` or `BUILD == completed`; this closes the same gap on the revision branch.

**G7 blocks build while plan revision is in flight.** The lock is set by `/do-plan-critique` (Step 5.6) when the verdict requires a revision pass, cleared by `/do-plan` (Phase 4, Step 2b) after pushing the revision, and self-heals when `revision_applied: true` is in the plan frontmatter. Gated on `pr_number is None` so an already-shipped PR is never blocked.

**G7 precedes G5 and G6 in list order (issue #1871).** G5's cached READY-TO-BUILD fast path does not itself read `plan_revising`, so G7 must run first to intercept a stale-hash cache hit while a revision is pending. The "an already-mergeable PR is never blocked by a stale `plan_revising` flag" guarantee does **not** come from list position relative to G6 — it comes from G7's own Gate 1 (`pr_number` set → return `None`). G6 only ever fires when `pr_number` is set, so in every state where G6 could dispatch `/do-merge`, G7 has already deferred at Gate 1; G6 always wins regardless of list position.

**Convergence latch — `revision_applied_at` (issue #1760).** `revision_applied` is sticky: `/do-plan` sets it `true` on every revision pass and it never resets, so it can't tell "this is the settle-and-build revision the critique verdict judged" apart from "a later, unrelated `/do-plan` dispatch". `/do-plan` Phase 4 Step 2a now also writes an event-scoped `revision_applied_at: <ISO-8601 UTC timestamp>` in the SAME step as `revision_applied: true` (never a follow-up edit). `agent.sdlc_router._critique_verdict_is_stale()` uses it as a latch: a `/do-plan` dispatch at or before `revision_applied_at` is treated as converged (not stale); one that postdates it re-stales normally, so a later unrelated revision never gets a free pass to BUILD. Absent/unparseable `revision_applied_at` leaves the latch inert (fail-safe to pre-#1760 timestamp-only staleness). **Verdict-kind gate (#2049):** the latch engages only for verdicts that do not require a revision (the settle-and-build READY TO BUILD path); for NEEDS REVISION / MAJOR REWORK the settled revision *invalidates* the verdict — it stays stale and row 2b routes to `/do-plan-critique`, never back to `/do-plan`. See [SDLC Pipeline — Convergence Latch](../../../docs/features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760) for the full mechanism.

**ISSUE_LOCKED (not a G-guard, issues #1954/#2003):** `sdlc-tool next-skill` checks the issue-level ownership lock *before* evaluating G1-G8, and short-circuits to `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": ..., "owner_session_id": ..., "orphaned_lock": ...}` if a foreign run holds the lock for this issue. Ownership is keyed by `run_id` (minted only by `session-ensure`, carried via `--run-id`), never by session_id or process identity. `ensure_session` surfaces the same `{"blocked": true, ...}` shape at its own call site. `dispatch record`'s CLI wrapper surfaces the lock differently: on a failed write it peeks the lock and, if contention caused the failure, merges `reason`/`owner_run_id`/`owner_session_id` into its existing `{"ok": false, "history_length": N}` result (never `blocked`) — see `_cli_record()` in `tools/sdlc_dispatch.py`. `orphaned_lock: true` means the owning run died before its next renewal — the lock frees itself within the lease TTL (`ISSUE_LOCK_TTL_SECONDS`, default 30 min; the happy path releases it immediately at run end). **Self-owned continue path:** if `owner_run_id` equals a `run_id` this conversation minted earlier for this issue, the lock is YOURS — this is not a block. Continue the stage under that run_id (a bare `session-ensure` under the live supervised-run signal returns `SUPERVISED_RUN_ACTIVE` carrying the same run_id — inherit it, per Step 1.5). Only a FOREIGN `owner_run_id` is a hard block: surface the `reason` and owner identifiers to the human, do not loop, and do not attempt to route around it by guessing an alternative skill — exactly like a G1-G8 block.

**Known gap — stale REVIEW verdict after PATCH (issue #1932 / PR #1941):** G3 and G6 above key off `_verdicts["REVIEW"]` containing `APPROVED`, not off whether that verdict was recorded *after* the most recent PATCH commit. Before PR #1941's router fix (and for any similar gap not yet caught), `next-skill` can propose `/do-merge` on a stale pre-patch `APPROVED`/`CHANGES REQUESTED` verdict because nothing forces a fresh `/do-pr-review` after `/do-patch` resolves REVIEW findings. Before trusting a router-proposed `/do-merge`, verify with `sdlc-tool verdict get --stage REVIEW --issue-number {N}` that the recorded verdict is `APPROVED` and postdates the patch commit; if not, manually dispatch `/do-pr-review` first.

Record every dispatch decision via `sdlc-tool dispatch record` BEFORE invoking the sub-skill — this preserves the G4 oscillation signal even if the sub-skill crashes mid-execution.

```bash
# Record a dispatch event (call BEFORE invoking the sub-skill)
sdlc-tool dispatch record --skill /do-build --issue-number {issue_number} --run-id {run_id}

# Record with PR context (for review/patch/merge stages)
sdlc-tool dispatch record --skill /do-pr-review --issue-number {issue_number} --pr-number {pr_number} --run-id {run_id}

# Inspect the dispatch history (debug G4 state; read-only, no --run-id)
sdlc-tool dispatch get --issue-number {issue_number}
```

The CLI wraps `agent.sdlc_router.record_dispatch()` and `tools.stage_states_helpers.update_stage_states()` — it is the correct runtime entry point. Never call `record_dispatch()` directly from a shell or skill script; always use `sdlc-tool dispatch record`.

## Step 4: Dispatch ONE Sub-Skill (or a Parallel-Safe Pair)

**Do not pattern-match against a hand-edited table.** Instead, call the routing tool and dispatch whatever skill it returns. The tool evaluates all guards (G1–G8) and dispatch rules (18 rows) against live state.

**Row 3 open-PR step-aside (#1932):** row 3 (`NEEDS REVISION` critique → `/do-plan`) already steps aside when the critique verdict is stale (plan revised since); it now also steps aside once `pr_number` is set, so a PR that already exists never gets routed back to `/do-plan` off a stale-but-not-yet-superseded NEEDS REVISION verdict — row 7 / G3 own PR-stage routing instead.

**Row 8d — crashed re-review recovery (#1932):** if `/do-pr-review` was dispatched after PATCH completed but crashed before persisting a REVIEW verdict, REVIEW is left at either `failed` (dead-ends at `Blocked`) or `completed` (silently misroutes to row 9's `/do-docs`, skipping review). Row 8d matches on the *absence* of a recorded verdict plus `last_dispatched_skill == /do-pr-review` (marker-agnostic — it does not require a specific REVIEW value) and re-dispatches `/do-pr-review`. Ordered before row 9 so it intercepts both crash markers. Loop-bound by G4.

**Row 9 verdict gate (#1932):** row 9 (`/do-docs`) now requires a recorded `APPROVED` review verdict, not just `REVIEW == completed`. Previously REVIEW could be marked `completed` with no verdict ever recorded (the row 8d crash state above), which silently misrouted to `/do-docs`, skipping review entirely. Row 8d now owns that no-verdict state instead — the two rows are disjoint by verdict, not by table-position luck.

**Row 8e — no-verdict completion recovery (#2062):** any remaining `REVIEW == completed` + no-recorded-verdict state that row 8d's preconditions exclude (e.g. `PATCH=pending`, `last=/do-build` — the #1897 misroute state) is owned by row 8e, which re-dispatches `/do-pr-review`. This is also the recovery row for the `stage-marker` REVIEW-completion refusal: `stage-marker --stage REVIEW --status completed` now refuses with the named `REVIEW_VERDICT_MISSING` when no substrate verdict is readable, and the refused state redirects here to re-review instead of deadlocking. Loop-bound by G4.

**Row 10 verdict gate (#2062):** row 10 (`/do-merge`) now requires a recorded `APPROVED` review verdict (mirroring row 9) AND head_sha freshness — `REVIEW == completed` alone is no longer merge-ready, so the no-verdict crash state can never fall through to `/do-merge`.

**Head_sha staleness (row 8f + G6, #2062):** `next-skill` context assembly fetches the live PR head and the router compares it against the verdict's `REVIEW_CONTEXT head_sha=` trailer — the same freshness definition `tools/merge_predicate` enforces, ending the router↔predicate oscillation. An APPROVED verdict whose trailer mismatches the live head (post-approval commit), has no parseable trailer, or whose live-head lookup failed (fail-closed) routes to `/do-pr-review` at the new head via row 8f; G6 steps aside for the same signal. Re-review records a fresh verdict with the current head's trailer, so the loop converges (G4-bounded).

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

Blocked (issue-level ownership lock -- not a G-guard, see Step 3.5):
```json
{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": "...", "owner_session_id": "...", "orphaned_lock": false}
```

**How to use the output:**
1. If `multi` is `true`: invoke the `pthread` skill to run all listed `skills` as parallel sub-agents. Record dispatch for the *first* skill in the list (the multi-dispatch is gated by guards as one decision -- a guard fire on the first dispatch replaces the whole pair). After both sub-agents complete, re-invoke `/sdlc` to re-dispatch based on the new pipeline state.
2. If `dispatched` is `true` (single): record the dispatch via `sdlc-tool dispatch record` (see Step 3.5), then invoke the returned `skill`.
3. If `blocked` is `true`: surface the `reason` to the human and wait. Do NOT loop or guess an alternative skill. This applies identically whether the block came from a G1-G8 guard or from `reason: "ISSUE_LOCKED"` (another live session already owns this issue) -- report `owner_session_id` to the human, do not loop, do not attempt to route around it.
4. If neither key is present (error): log the `error` field and escalate to the human.

**Before recording and dispatching**, also supply `--proposed-skill` when you already know what skill you intend to invoke (enables G3 PR-lock detection):
```bash
sdlc-tool next-skill --issue-number {issue_number} --proposed-skill /do-build
```

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
| MERGE | /do-merge {pr_number} | sonnet | Programmatic merge gate: verifies all stages, then merges |

The **Dev Model** column shows the model the PM should pass via `--model` when spawning a dev session for that stage (see Stage→Model Dispatch Table in PM persona).
