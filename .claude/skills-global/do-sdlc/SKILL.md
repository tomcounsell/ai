---
name: do-sdlc
description: "Supervise a full SDLC pipeline run to merge in a local Claude Code session. Triggered by 'do-sdlc', 'run the full pipeline', 'ship this issue end to end', 'supervise the sdlc'. Also the home of the single-stage router contract that /sdlc (in this repo) executes."
context: fork
---

# do-sdlc — SDLC Pipeline Supervisor and Single-Stage Router

This skill is the **one substantive SDLC skill**. It has two entry modes over one shared step
spine:

| Mode | Entry | Executes | Contract |
|------|-------|----------|----------|
| **Router mode** | `/sdlc` (in this repo) | Steps 1–4 once | dispatch ONE sub-skill, then return; the PM session re-invokes |
| **Supervisor mode** | `/do-sdlc` | Steps 1–7 | loop until merge/blocked |

The router mode is the bridge PM's production contract — a single-stage router that assesses
state and dispatches exactly one `/do-*` skill, expecting the PM session (bridge or this skill's
supervisor loop) to re-invoke it after each stage completes. The supervisor mode is the local
stand-in for the bridge PM session: it re-invokes the router, dispatching each stage to a
subagent on the stage-appropriate model (opus/sonnet), until merge, a blocking guard, or the
iteration cap.

You are the supervisor or router, never the worker. You assess, dispatch, and track. The stage
subagents do all the work.

**Redundant-context check (issue #2026, WS-F):** if a bridge PM/dev context already owns this
issue — a live eng session (e.g. a bridge PM session) — then supervisor mode is redundant: that
context IS the supervision loop. Do not run it; drive via `/sdlc` (in this repo, the single-stage
router) instead. A live *supervised-run signal* for the issue number is a different case — see
the refusal decision table in Step 2: if the signal's `owner_run_id` is one this run already
holds, it is this run's own hand-off, not another owner, and must be inherited rather than
treated as redundant-context grounds to stand down.

## Repo Context Probe

If `docs/sdlc/do-sdlc.md` exists, read it and honor its declarations; otherwise use the generic
defaults described below. The defaults drive the pipeline through `sdlc-tool` (synced to every
machine via `~/.local/bin`), `gh`, and `git`. Repo-coupled specifics — worktree ownership,
`SDLC_TARGET_REPO`/`GH_REPO` semantics, the G1–G9 guard table's repo path references, and
`sdlc-tool` command discipline — live in that context file, not in this global body.

## Hard Rules

1. **NEVER write code, run tests, or create plans directly** — every stage executes inside a
   stage subagent that invokes the stage's `/do-*` skill (supervisor mode), or is dispatched as
   exactly one sub-skill and then the router returns (router mode).
2. **NEVER decide dispatch yourself** — `sdlc-tool next-skill` is the only source of dispatch
   decisions. It encodes all guards (G1–G9) and dispatch rows. Do not second-guess it, reorder
   stages, or skip it "because the next stage is obvious".
3. **NEVER continue past a `blocked` decision** — surface the reason to the human and stop.
   Guards block for a reason.
4. **ALWAYS pass `model:` per the Stage→Model table** when spawning a stage subagent. Never rely
   on the inherited default.
5. **ALWAYS record the dispatch before spawning the subagent** — this preserves the G4
   oscillation signal even if the subagent crashes.
6. **ALWAYS dispatch with `run_in_background: false`, never end the turn waiting on a background child.**
   This skill runs in a forked context (`context: fork`) that gets exactly one turn. The
   Agent tool defaults to background execution — it returns immediately and notifies later. A
   fork has no later turn to be notified on, so a background dispatch is unrecoverable: the fork
   reports "running in the background, I'll continue when it completes" and then never does
   (issue #1915). Every stage subagent must be spawned with `run_in_background: false` so its
   result is in hand before the loop advances. In router mode the same rule applies to the one
   stage you dispatch: it must complete before you return.
7. **NEVER spawn agent teammates for stage work.** Where Claude Code agent teams are enabled,
   ignore those affordances: a teammate's idle notification is not a completion signal (teammates
   go idle mid-task with deliverables unfinished), and an in-process teammate cannot be reliably
   resumed. Every dispatch is a foreground subagent per Rule 6.
8. **NEVER loop in router mode** — invoke one sub-skill, then return. The PM session handles
   progression.

## Worktree & branch ownership

**Slug identity always wins.** Each issue's build fork exclusively owns `.worktrees/{slug}` and
`session/{slug}`, where `{slug}` is the lane's identity, recorded once at lane start and read
(never re-derived) via the lane-identity resolver — the single source of truth (worktree manager
+ branch resolution; see the repo context probe for this repo's exact paths). Do NOT pre-allocate
per-supervisor `.worktrees/sdlc-{N}` lanes: nothing reads a lane override, so lane instructions
are silently dropped and every issue's builders land in `.worktrees/{slug}` regardless.
Converging fork + supervisor onto one branch per plan is deliberate — it structurally collapses
duplicate PRs, since GitHub permits only one open PR per head branch. Concurrent builders inside
the one slug worktree must write disjoint file sets (do-build's `Parallel: true` convention: no
shared-file writes).

## Stage→Model Dispatch Table

Mirrors the engineer persona's table (`config/personas/engineer.md`) — the local equivalent of
`valor-session create --model`.

| Stage | Skill | `model:` | Rationale |
|-------|-------|----------|-----------|
| ISSUE | /do-issue | sonnet | Structured writing |
| PLAN | /do-plan | opus | Adversarial reasoning, architectural design |
| CRITIQUE | /do-plan-critique | opus | Adversarial review (its internal critics self-pin to sonnet) |
| BUILD | /do-build | sonnet | Tool-heavy plan execution |
| TEST | /do-test | sonnet | Deterministic test runs |
| PATCH | /do-patch | sonnet | Targeted fix |
| REVIEW | /do-pr-review | opus | Nuanced code review judgment |
| DOCS | /do-docs | sonnet | Structured writing |
| MERGE | /do-merge | sonnet | Programmatic gate |

## Step 1: Resolve the Issue or PR

Determine whether the input is an issue reference, a PR reference, or a bare feature description.
Scope `gh` reads with `--repo` when the repository is known or derivable — under a foreign
`GH_REPO` (or from a wrong cwd) a bare `gh` command answers about a *different* repository and
exits 0. When the target repo is not this one, resolve the slug from `GH_REPO` (already an
org/repo slug) or `gh repo view --json nameWithOwner -q .nameWithOwner` from the target repo
root, and pass it as `gh ... --repo <slug>`.

- **Issue reference** (e.g., `issue 123`, `issue #123`): `gh issue view {number} --repo <resolved>` when
  a slug resolved.
- **PR reference** (e.g., `PR 363`, `pr #363`): `gh pr view {number} --repo <resolved> --json
  number,title,state,headRefName,reviewDecision,statusCheckRollup,body` to get the branch name,
  review state, and check status. Then extract the linked issue number from the PR body (look
  for `Closes #N` or `Fixes #N`).
- **Bare feature description** (no number): in supervisor mode, dispatch a `/do-issue` stage
  subagent first (sonnet), read the created issue number from its report, then proceed. In
  router mode, do not proceed without an issue number — surface that to the PM.

**PR state informs Step 3**: when a PR is provided, its current state (checks passing/failing,
review approved/changes-requested, etc.) tells you which pipeline stage to resume from. Skip
stages that are already complete — do not restart from scratch.

## Step 2: Ensure the Tracking Session

```bash
# SDLC_REPO: GitHub slug (org/repo) — used to build issue/PR URLs.
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
# SDLC_TARGET_REPO: filesystem path to the target repo (distinct from SDLC_REPO which is the
# GitHub slug). sdlc-tool forces cwd to ~/src/ai; this env var tells it where the target
# repo's plans live. Set once and exported for the lifetime of the supervision loop.
SDLC_TARGET_REPO=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
export SDLC_TARGET_REPO
# Run identity (issue #2003): `session-ensure` is the EXCLUSIVE minting site for the
# run_id — one uuid-hex identity for this whole supervision run, minted by winning the
# issue lock and emitted in the JSON output. No env vars, no run files: every
# state-mutating `sdlc-tool` call in Step 4/5 passes it back explicitly via
# `--run-id {run_id}`. The standalone worker threads its own run_id in-process, so the
# real worker-vs-local guard is preserved.
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"
```

Let stderr through and read the JSON payload. `session-ensure` reports a refusal *in the payload*
(`{"blocked": true, "reason": ...}`), not through the exit code — it exits 0 either way — so a
discarded diagnostic here is a run that proceeds under an identity it does not hold.

Read the JSON from the tool result and **record the `run_id`** (`{"session_id": ..., "created": ..., "run_id": "<hex>"}`) — carry it through every subsequent step. Reuses the existing
`sdlc-local-{N}` session on re-runs. The ownership contract:

- **Every state-mutating `sdlc-tool` call** (`dispatch record`, `stage-marker`, `verdict record`, `meta-set`) **MUST pass `--run-id {run_id}` explicitly.** A missing flag is a named non-zero error (`RUN_ID_REQUIRED`) — the call never mints or adopts an identity.
- **Pass `--issue-number` to every `sdlc-tool` invocation.** It is the authoritative session selector.
- **`stage-query`, `verdict get`, and `dispatch get` accept no `--run-id` argument** — they have no lock to compare against. `next-skill` sits in neither bucket: it *accepts* `--run-id` as a read-only identity assertion for its issue-lock peek — always pass it there (issue #2766).
- **Do NOT export `AGENT_SESSION_ID`** — env vars do not persist across Claude Code bash blocks.

**Self-heal on resume (issue #2144).** State-mutating writes (`stage-marker`, `verdict record`,
`meta-set`, `dispatch record`) now **self-heal** their run identity: if a resumed turn has lost
the `run_id` from context (worker restart mid-pipeline), the write re-establishes the *same*
run's identity from the environment (live supervised signal → `.sdlc-run` / `active_run_id` →
verified re-acquire of the free lock) and retries once, instead of silently refusing
`RUN_ID_REQUIRED`/`LEASE_ABSENT` and freezing the ledger. This is best-effort and non-blocking;
a foreign live lease still hard-refuses. Still pass `--run-id` on the happy path; the heal is a
safety net for the resume edge, not a license to omit it.

### Three-way refusal decision table

`session-ensure` (and any subsequent re-ensure — see Step 5's between-stage continuity check) can
refuse with exactly three payload shapes. Discriminate them; do not collapse all three to "stop":

| Refusal | Shape | Action |
|---|---|---|
| **Hand-off** | `{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id", "owner_run_id", "owner_session_id"}` | This is a **designed hand-off**, not a block. It mints nothing — the supervisor already owns the run. **Pass the self-identity check below first.** If it confirms your own signal: **inherit** `owner_run_id` (carry it forward as `run_id`, pass it back via `--run-id`/`--reuse-run-id`), and **continue** — never stop for a confirmed-own signal. |
| **Orphaned lock** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": true}` | The prior owner died before renewing; the lock frees within its TTL (duration and renewal semantics are #2446's — cross-reference it, do not reimplement). Wait, re-ensure, then **rebind `run_id` to whatever the re-ensure returns** before continuing — a post-TTL fresh contest mints a NEW run_id, and every downstream `--run-id` call must use the rebound value or it silently orphans. |
| **Foreign holder** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": false}` | Apply the same self-identity check as the hand-off row: `owner_run_id ∈ {run_ids this run has held}` means this is your own lock, not a foreign one — **inherit and continue**, never stop. Only when `owner_run_id` is genuinely foreign is this the **unconditional stop condition.** A genuine live foreign run owns the issue. Stop and report. (issue #2766 — always pass `--run-id {run_id}` on Step 4's `next-skill` call so this row is only ever reached for a genuine rival, never your own lock.) |

**Self-identity check before standing down** (applies to the hand-off row above): `SUPERVISED_RUN_ACTIVE`
fires only on a LIVE signal — a stale/expired one falls through to the orphaned-lock/foreign-holder
rows instead. The **decisive** term is `owner_run_id ∈ {run_ids this run has held}`: a live signal
carrying a run_id this run never held is a genuine concurrent rival — **stop and report**, even though
the payload shape looks like a hand-off. `owner_session_id == sdlc-local-{issue_number}` is
*necessary-but-not-sufficient* — ledger anchors are keyed by issue number, not by run, so a second
concurrent `/do-sdlc` on the same issue emits a byte-identical `owner_session_id`. Compare `owner_run_id`
explicitly; do not substitute `run_id` (the sibling field the tool also returns) for it. A match on
`owner_run_id` is this run's own ghost (inherit-and-continue); a mismatch is a rival even when
`owner_session_id` matches (stop-and-report). (see #2446, #2451)
- **Recovery after run_id loss** (context compaction, restarted supervisor): re-run the `session-ensure` above. While the old lock is live it returns `ISSUE_LOCKED`; the lock frees within its TTL and a fresh contest then mints a new run_id (duration and renewal semantics are #2446's — do not restate a number here). **If you still have the run_id**, add `--reuse-run-id {run_id}` to recover immediately under the same identity — the tool verifies the claim against the live lock, or — on a free lock — against the session record or the issue's durable run-identity anchor, and never adopts an unverified one.
- **Ledger-anchor rule:** `sdlc-local-{N}` is a non-executable ledger anchor (`is_ledger`, #2042), not
  a live executable session. It permanently shows `status=running` and carries the run's `_meta` stage
  state — it must **not** be killed, and a running-looking anchor is not evidence of a rogue pipeline.

## Step 3: Assess Current State

Check what already exists for this issue. Use `$SDLC_TARGET_REPO` for local operations (defaults
to `.` for same-repo work). Run ALL of these checks — do not skip any.

**Command discipline (applies to every check in Steps 3-4):** run each check as a separate
single-line command and read the output from the tool result — no pipes, no command substitution,
no `||` fallbacks, no environment-variable capture. You interpret the output and decide the next
step.

### Step 3.0: Query stage_states from PipelineStateMachine (primary signal)

Query the PM session's `stage_states` for authoritative stage completion data. This is the
**exclusive signal** for routing decisions. Stage completion is determined ONLY by stored state —
never by artifact inference.

The tool resolves the active session from `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, or
`--issue-number` internally.

```bash
sdlc-tool stage-query --issue-number {issue_number}
```

Interpret the JSON output from the tool result:
- Non-empty object with stage keys (e.g. `{"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"}`): use it as the **exclusive signal** for the dispatch table. A stage is behind us ONLY if its value is `"completed"` — or `"skipped"`, which only PLAN and CRITIQUE can ever hold and which means the pipeline never dispatched that stage because this issue has no plan document (#2577, see [`docs/features/off-pipeline-merge-path.md`](../../../docs/features/off-pipeline-merge-path.md)). Never re-dispatch a skipped stage. Skip steps 3a-3e.
- Empty `{}` or an `unavailable` marker: fall through to the dispatch-history fallback in steps 3a-3e. Do NOT infer stage completion from artifacts.

### Steps 3a-3e: Dispatch History Fallback

These checks run ONLY when stage_states is unavailable (empty JSON from step 3.0). When
stage_states IS available, skip directly to the dispatch table using stage_states as the source
of truth.

**IMPORTANT: Never infer stage completion from artifacts (plan files, PR existence, docs/ files,
etc.). Stage completion is exclusively determined by stored state.**

When stage_states is unavailable, use conversation context to identify which skills were already
dispatched in this session. Artifacts are used only to check preconditions (e.g., "does a PR
exist?") — not to declare stages complete.

`$SDLC_TARGET_REPO` is exported by the harness so `git -C` picks it up without further shell
composition; `gh` uses `$GH_REPO` automatically for the cross-repo case.

```bash
# 3a. Check if a plan doc references this issue
grep -r "#{issue_number}" docs/plans/
```

```bash
# 3b. List all branches (filter for session/ prefix in the tool result)
git -C "$SDLC_TARGET_REPO" branch -a
```

```bash
# 3c. Check if a PR already exists
gh pr list --repo <resolved> --search "#{issue_number}" --state open
# Cross-check with live refs — the --search index lags GitHub; --head queries live refs:
#   gh pr list --repo <resolved> --head session/{slug} --state open   (reuse if present; keyed by head branch, not issue #)
```

If a PR exists, fetch its full state for assessment:
```bash
# 3d. Get PR state: checks, review, branch
gh pr view {pr_number} --repo <resolved> --json number,headRefName,reviewDecision,statusCheckRollup,body

# 3e. Check review status — look for APPROVED, CHANGES_REQUESTED, or no review
# reviewDecision: "APPROVED" means formal GitHub review approved (non-self-authored PRs)
# reviewDecision: "CHANGES_REQUESTED" means formal GitHub review requested changes
# reviewDecision: "" (empty) — AMBIGUOUS for self-authored PRs:
#   - For non-self-authored PRs: no review posted yet
#   - For self-authored PRs: expected even after review — check _verdicts["REVIEW"] from sdlc_stage_query
# Always cross-check _meta.latest_review_verdict before concluding no review exists.
```

### Step 3.4: Check Documentation Status

This step is REQUIRED when a PR exists and review is clean (APPROVED). Skip it only if the
pipeline hasn't reached the REVIEW stage yet.

```bash
# 3.4a. List files changed in the PR (count docs/ entries from the tool result)
gh pr diff {pr_number} --repo <resolved> --name-only
```

```bash
# 3.4b. Find the plan path for this issue (first match from the tool result)
grep -rl "#{issue_number}" docs/plans/
```

```bash
# 3.4c. Read the plan's Documentation section (inspect for unchecked tasks in the tool result)
cat docs/plans/{plan-filename}.md
```

For the DOCS stage completion check, re-read the `sdlc-tool stage-query` output from Step 3.0. Do
not pipe JSON through a shell here.

**Decision logic for docs**:
- If the plan has a `## Documentation` section with unchecked tasks → docs NOT done
- If PR has zero `docs/` file changes AND plan requires doc tasks → docs NOT done
- If docs tasks are all checked AND `docs/` changes exist in PR → docs done
- When in doubt, dispatch `/do-docs` — it is idempotent and will no-op if nothing needs updating

## Step 3.5: Legal Dispatch Guards (reference)

`sdlc-tool next-skill` evaluates the G1–G9 guards itself — do NOT re-evaluate them by hand. The
table below exists so you can interpret a `blocked` decision or a forced dispatch when the tool
returns one. Canonical implementation: `agent.sdlc_router.decide_next_dispatch()`; the parity
test `tests/unit/test_sdlc_skill_md_parity.py` keeps this table in sync with the Python rules.
Repo-specific guard nuances are declared in the repo context probe (`docs/sdlc/do-sdlc.md`).

Guards are evaluated in the **pinned `GUARDS` list order** `[G1, G2, G3, G4, G9, G8, G7, G5, G6]` — the first to return a non-`None` decision wins. Guard IDs are historical (assigned in introduction order), not evaluation order; the table below is listed in evaluation order:

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Latest critique verdict contains `NEEDS REVISION` or `MAJOR REWORK` AND `last_dispatched_skill == /do-plan-critique` | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` (2) AND CRITIQUE is not completed | Escalate: `blocked` with reason `critique cycle cap reached` |
| G3: PR lock | `pr_number` is set AND (`last_dispatched_skill` OR proposed dispatch) is `/do-plan` or `/do-plan-critique` | `/do-merge` (if REVIEW and DOCS complete), `/do-patch` (if review requested changes), else `/do-pr-review` |
| G4: Oscillation (universal) | `same_stage_dispatch_count >= 3` | Escalate: `blocked` with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| G9: Blocked-on-conflict | Recorded REVIEW verdict contains `BLOCKED_ON_CONFLICT` AND `pr_merge_state` not in the non-conflicting set (`CLEAN`, `HAS_HOOKS`, `UNSTABLE`, `BLOCKED`, `BEHIND`) AND the verdict is not stale (no `/do-patch` landed after it, #2796) | Escalate: `blocked` with reason naming the PR, the merge state, and the rebase — no SDLC skill resolves merge conflicts |
| G8: Stage-advance verification | `context["stage_artifacts_verified"] is False` (a claimed stage artifact — PR, branch, plan commit — failed live verification) | Re-dispatch the skill owning `context["unverified_stage"]` |
| G7: Plan-revising lock | `pr_number` is None AND `plan_revising == True` AND no `/do-plan` revision has landed since the latest CRITIQUE verdict (event-scoped, #2787 — NOT the sticky `revision_applied` boolean) | `/do-plan` (if `last_dispatched_skill == /do-plan-critique`); Escalate `blocked` (if no `/do-plan` in last `MAX_PLAN_REVISING_DISPATCHES + 1` turns) |
| G5: Unchanged critique artifact | `_verdicts["CRITIQUE"]` has `artifact_hash` AND current plan file hash matches | Use cached verdict: `/do-plan` (NEEDS REVISION) or `/do-build` (READY TO BUILD, no concerns). Never re-dispatch `/do-plan-critique` on an unchanged plan. **Steps aside unconditionally on `READY TO BUILD (with concerns)`** so rows 2b/4b/4c own that state (#2787); the bound there is `MAX_CONCERN_RECRITIQUE_ROUNDS`, not G5. |
| G6: Terminal merge ready | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` |

**G4 is universal** — it applies to EVERY stage, including DOCS and MERGE. Repeated dispatches of
`/do-docs` or `/do-merge` without state change WILL trip the guard.

**G4 precedes G8 by design (issue #1267).** G8 re-dispatches the same stage's skill on a false
artifact claim, with nothing upstream to stop it on a persistently false claim. Because G4 runs
first, it fires and blocks once `same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES` before
G8 gets another chance to re-dispatch — silent re-dispatch first, escalate via the existing G4
cap second, not an immediate block on the first mismatch.

**G9 (issue #2796).** A `BLOCKED_ON_CONFLICT` REVIEW verdict previously routed by accident — row 8
sent it to `/do-patch` (which cannot rebase), row 8b sent it back to `/do-pr-review` (whose
preflight re-recorded the same verdict) — ping-ponging until G4 escalated with a reason naming
neither the conflict nor the rebase. G9 escalates immediately with a reason that names both. Its
non-conflicting step-aside set (`CLEAN`, `HAS_HOOKS`, `UNSTABLE`, `BLOCKED`, `BEHIND`) mirrors
`/do-pr-review`'s own preflight decision table exactly, so a PR that is merely `BEHIND` or
`UNSTABLE` is never wrongly told it has merge conflicts. `tools/sdlc_stage_query.py::_fetch_pr_merge_state`
retries once on a transient `UNKNOWN` read before G9 (or G6) ever sees the value.

**G8 makes no live calls.** Live verification of claimed stage artifacts happens in the
next-skill context-assembly path (`tools/sdlc_next_skill.py`), which sets
`context["stage_artifacts_verified"]` / `context["unverified_stage"]`; G8
(`agent.sdlc_router.guard_g8_artifact_verification`) only reads those flags. This keeps
`agent/sdlc_router.py` import-free of `tools/` (see `tests/unit/test_architectural_constraints.py`).
Absent/unset/`True` is a no-op, and a stage whose claimed artifact has no resolvable identifier
(e.g. no recorded `pr_number`) is skipped rather than reported as a mismatch (#2757). The
verifier's `git` checks run with `cwd=_target_repo_cwd()` (`SDLC_TARGET_REPO`, a filesystem path)
so they inspect the SDLC target repo, not the process cwd — see the cwd-threading contract
(#2078) in `docs/features/sdlc-router-oscillation-guard.md`.

**G5 applies to CRITIQUE only**, not REVIEW. Review verdicts legitimately change on unchanged
diffs (CI flips, new comments, linked issues). G4 handles REVIEW non-determinism instead.

**G1 open-PR step-aside (#1932):** once `pr_number` is set, G1 no longer fires — it steps aside
and defers to G3, the canonical open-PR plan-stage redirect. Without this, a NEEDS
REVISION/MAJOR REWORK critique verdict recorded before the PR was opened could route a shipped PR
back to `/do-plan`.

**G5 open-PR step-aside (#1932):** on its NEEDS_REVISION/MAJOR_REWORK branch (cached critique
verdict, unchanged plan hash), G5 also steps aside once `pr_number` is set and defers to G3
instead of re-dispatching `/do-plan`. The READY_TO_BUILD branch already deferred on `pr_number`
or `BUILD == completed`; this closes the same gap on the revision branch.

**G7 blocks build while plan revision is in flight.** The lock is set by `/do-plan-critique`
(Step 5.6) when the verdict requires a revision pass, cleared by `/do-plan` (Phase 4, Step 2b)
after pushing the revision, and self-heals when `revision_applied: true` is in the plan
frontmatter. Gated on `pr_number is None` so an already-shipped PR is never blocked.

**G7 precedes G5 and G6 in list order (issue #1871).** G5's cached READY-TO-BUILD fast path does
not itself read `plan_revising`, so G7 must run first to intercept a stale-hash cache hit while a
revision is pending. The "an already-mergeable PR is never blocked by a stale `plan_revising`
flag" guarantee does **not** come from list position relative to G6 — it comes from G7's own Gate
1 (`pr_number` set → return `None`). G6 only ever fires when `pr_number` is set, so in every
state where G6 could dispatch `/do-merge`, G7 has already deferred at Gate 1; G6 always wins
regardless of list position.

**Convergence latch — `revision_applied_at` (issue #1760).** `revision_applied` is sticky:
`/do-plan` sets it `true` on every revision pass and it never resets, so it can't tell "this is
the settle-and-build revision the critique verdict judged" apart from "a later, unrelated
`/do-plan` dispatch". `/do-plan` Phase 4 Step 2a now also writes an event-scoped
`revision_applied_at: <ISO-8601 UTC timestamp>` in the SAME step as `revision_applied: true`
(never a follow-up edit). `agent.sdlc_router._critique_verdict_is_stale()` uses it as a latch: a
`/do-plan` dispatch at or before `revision_applied_at` is treated as converged (not stale); one
that postdates it re-stales normally, so a later unrelated revision never gets a free pass to
BUILD. Absent/unparseable `revision_applied_at` leaves the latch inert (fail-safe to pre-#1760
timestamp-only staleness). **Verdict-kind gate (#2049):** the latch engages only for verdicts that
do not require a revision (the settle-and-build READY TO BUILD path); for NEEDS REVISION / MAJOR
REWORK the settled revision *invalidates* the verdict — it stays stale and row 2b routes to
`/do-plan-critique`, never back to `/do-plan`. **With-concerns scope (#2787):** on the `READY TO
BUILD (with concerns)` path a bounded branch runs ahead of the latch — below
`MAX_CONCERN_RECRITIQUE_ROUNDS` the concern-closing revision re-stales the verdict so row 2b
re-critiques it; at the bound the latch engages and row 4c builds with the residual concerns
recorded as accepted. See [With-Concerns Re-Critique Gate](../../../docs/features/with-concerns-recritique-gate.md)
and [SDLC Pipeline — Convergence Latch](../../../docs/features/sdlc-pipeline.md#convergence-latch-revision_applied_at-issue-1760)
for the full mechanism.

**ISSUE_LOCKED (not a G-guard, issues #1954/#2003):** `sdlc-tool next-skill` checks the
issue-level ownership lock *before* evaluating G1-G9, and short-circuits to
`{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": ..., "owner_session_id": ...,
"orphaned_lock": ...}` if a foreign run holds the lock for this issue. Ownership is keyed by
`run_id` (minted only by `session-ensure`, carried via `--run-id`), never by session_id or
process identity. `ensure_session` surfaces the same `{"blocked": true, ...}` shape at its own
call site. `dispatch record`'s CLI wrapper surfaces the lock differently: on a failed write it
peeks the lock and, if contention caused the failure, merges `reason`/`owner_run_id`/
`owner_session_id` into its existing `{"ok": false, "history_length": N}` result (never
`blocked`) — see `_cli_record()` in `tools/sdlc_dispatch.py`. `orphaned_lock: true` means the
owning run died before its next renewal — the lock frees itself within the lease TTL
(`ISSUE_LOCK_TTL_SECONDS`, default 30 min; the happy path releases it immediately at run end).
**The signal is renewal freshness, not process liveness (issue #2620):** the lease payload's
`pid` belongs to the ephemeral `session-ensure` CLI and is dead within seconds of acquire, so
`_lock_owner_is_live` keys on the `renewed_at` stamp that every renewal tick refreshes. A
recently-renewed lease is a LIVE owner (`orphaned_lock: false` → stop), and only a lease nobody
has renewed for `ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` (default 20 min) reads orphaned.
**Self-owned continue path:** if `owner_run_id` is a `run_id` this run has already held (minted
at the start of the run, or inherited from the supervisor per Step 2), the lock is YOURS — this
is not a block. Continue the stage under that run_id (a bare `session-ensure` under the live
supervised-run signal returns `SUPERVISED_RUN_ACTIVE` carrying the same run_id — inherit it, per
Step 2). Only a FOREIGN `owner_run_id` is a hard block: surface the `reason` and `owner_run_id`
to the human and stop; do not loop, do not attempt to route around it.

**Known gap — stale REVIEW verdict after PATCH (issue #1932 / PR #1941):** G3 and G6 above key
off `_verdicts["REVIEW"]` containing `APPROVED`, not off whether that verdict was recorded
*after* the most recent PATCH commit. Before PR #1941's router fix (and for any similar gap not
yet caught), `next-skill` can propose `/do-merge` on a stale pre-patch `APPROVED`/`CHANGES
REQUESTED` verdict because nothing forces a fresh `/do-pr-review` after `/do-patch` resolves
REVIEW findings. Before trusting a router-proposed `/do-merge`, verify with `sdlc-tool verdict
get --stage REVIEW --issue-number {N}` that the recorded verdict is `APPROVED` and postdates the
patch commit; if not, manually dispatch `/do-pr-review` first.

**Row 10 merge gate:** `/do-merge` fires only when REVIEW and DOCS are complete, the PR merge
state is CLEAN, CI is all-passing, and the recorded REVIEW verdict is APPROVED at the current
head.


## Step 4: Dispatch ONE Sub-Skill

**Do not pattern-match against a hand-edited table.** Instead, call the routing tool and dispatch
whatever skill it returns. The tool evaluates all guards (G1–G9) and dispatch rules (19 rows)
against live state. In router mode this is the whole job: call `next-skill`, record the dispatch,
invoke the ONE returned skill, then **return**. In supervisor mode the same call feeds Step 5's
loop.

```bash
# Get the next dispatch decision
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id}
```

The tool dispatches at most ONE skill per call. It outputs JSON in one of these shapes:

Single dispatch:
```json
{"skill": "/do-build", "reason": "...", "row_id": "4a", "dispatched": true}
```

Blocked:
```json
{"blocked": true, "reason": "G4: stage oscillation ...", "guard_id": "G4"}
```

Blocked (issue-level ownership lock -- not a G-guard, see Step 3.6):
```json
{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id": "...", "owner_session_id": "...", "orphaned_lock": false}
```

**How to use the output:**
1. If `dispatched` is `true`: record the dispatch via `sdlc-tool dispatch record` (see below),
   then invoke the returned `skill`.
2. If `blocked` is `true`: surface the `reason` to the human and wait. Do NOT loop or guess an
   alternative skill. This applies identically whether the block came from a G1-G9 guard or from
   `reason: "ISSUE_LOCKED"` (another live session already owns this issue) -- report
   `owner_session_id` to the human, do not loop, do not attempt to route around it.
3. If neither key is present (error): log the `error` field and escalate to the human.

**Before recording and dispatching**, also supply `--proposed-skill` when you already know what
skill you intend to invoke (enables G3 PR-lock detection):
```bash
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id} --proposed-skill /do-build
```

Record every dispatch decision via `sdlc-tool dispatch record` BEFORE invoking the sub-skill —
this preserves the G4 oscillation signal even if the sub-skill crashes mid-execution.

```bash
# Record a dispatch event (call BEFORE invoking the sub-skill)
sdlc-tool dispatch record --skill /do-build --issue-number {issue_number} --run-id {run_id}

# Record with PR context (for review/patch/merge stages)
sdlc-tool dispatch record --skill /do-pr-review --issue-number {issue_number} --pr-number {pr_number} --run-id {run_id}

# Inspect the dispatch history (debug G4 state; read-only, no --run-id)
sdlc-tool dispatch get --issue-number {issue_number}
```

The CLI wraps `agent.sdlc_router.record_dispatch()` and `tools.stage_states_helpers.update_stage_states()` — it is the correct runtime entry point. Never call `record_dispatch()` directly from a shell or skill script; always use `sdlc-tool dispatch record`.

Do NOT restart from scratch if prior stages are already complete.

## Step 5: Supervision Loop (supervisor mode only)

Repeat the following cycle. **Iteration cap: 15 dispatches** (a happy path is 8 stages; the cap
is a backstop above realistic patch/re-review cycles — G4 catches genuine oscillation long before
it). Router mode never enters this step — it returns after Step 4.

### 5a. Ask the router

```bash
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id}
```

(Read-only — `next-skill` never mints, adopts, or renews the lock. `--run-id` is a read-only
identity assertion: it peeks under the caller's own stated identity so this run is never told to
stand down for a lock it holds itself, issue #2766. Always pass it here.)

Interpret the JSON from the tool result (same shapes as Step 4):

- `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` → the payload additionally carries
  `peek_identity` (`"caller"` | `"session_mirror"` | `"unresolved"`) and, only when `--run-id` was
  supplied and did not match the live lock, `session_mirror_run_id`. Both are **diagnostics only**
  — they never override the block and there is no automatic re-ensure/retry keyed on them.
  `peek_identity: "unresolved"` means the block is *inconclusive* (the fallback session lookup
  missed) — report it to the human as such and stop, exactly like any other block; do not retry.
  Apply the self-identity check below to the row's `owner_run_id` before deciding whether this is
  actually your own lock.
- `{"blocked": true, ...}` (other reasons) → **STOP the loop.** Report the `reason` and `guard_id` to the human, plus a summary of stages completed so far. Do not retry, do not guess an alternative skill.
- `{"skill": "...", "dispatched": true, ...}` → continue to 5b. The router dispatches at most ONE skill per call; there is no parallel-pair shape.
- Anything else (error key, empty) → STOP and surface the error.

### 5b. Record the dispatch

```bash
sdlc-tool dispatch record --skill {skill} --issue-number {issue_number} --run-id {run_id}
# include --pr-number {pr} once a PR exists (review/patch/docs/merge stages)
```

### 5c. Spawn the stage subagent

**One writer per artifact — enumerate live children before you dispatch.** Every stage you
dispatch is a writer on a shared artifact, and the plan doc in particular is a single file on the
shared main checkout that no worktree isolates. Before spawning, account for the children you
already have out: if a child is still holding the plan doc, do not dispatch a second one onto it,
and do not edit it yourself while it does. Wait for the outstanding child to report, then
dispatch.

This is not hypothetical. On 2026-08-07 one plan doc took two concurrent revision children twice
over (#2650, shape 3) — a writer watched its line count grow from 62 to 111 between its own
reads and had to stop and ask who owned the file — and a supervisor patched a plan task while its
own dispatched child held the doc, then had to warn the child mid-flight to re-read before
committing (shape 4). Both were recovered only because an agent happened to notice the file
moving underneath it.

Since Hard Rule 6 already requires `run_in_background: false`, the ordinary loop has exactly one
child out at a time and satisfies this for free. The rule bites when you are tempted to fan out,
or to "just fix one line" in a doc a child is revising. Neither is safe; the second is the one
that feels harmless.

Use the Agent tool (general-purpose), with `model:` from the Stage→Model table and
**`run_in_background: false`** (Hard Rule 6 — this fork cannot be resumed by a background
notification). Prompt template:

```
You are executing ONE SDLC stage for issue #{issue_number} in {repo_path}.

Invoke the Skill tool now: skill "{skill-name-without-slash}", args "{issue_number / pr_number / slug as the skill expects}".
The skill is the procedure — follow it exactly. Do not improvise the stage yourself.

Context:
- Issue: #{issue_number} — {title}
- PR: {#pr or "none yet"}
- Plan: {docs/plans/{slug}.md or "none yet"}
- Prior stage outcome: {one-line summary, or "None — first stage"}
- Run identity: {run_id} — pass --run-id {run_id} on every state-mutating sdlc-tool call (stage-marker, verdict record, meta-set, dispatch record) and on next-skill (a read-only identity assertion for its lock peek, issue #2766); stage-query/verdict get/dispatch get take none.
- Commit early: commit to session/{slug} as work lands (small logical checkpoints), not only at the end — a preempt or lease lapse mid-stage must never lose work.

When done, report back (this is data for the supervisor, not prose for a human):
- outcome: success | failure
- verdict: any verdict string the skill emitted (READY TO BUILD / NEEDS REVISION / APPROVED / CHANGES REQUESTED / ...)
- artifacts: plan path, PR number, branch name — whatever was created or changed
- failures: test failures, blockers, or errors verbatim if any
```

Carry forward context between iterations: the `run_id` goes into every stage prompt, and once
BUILD reports a PR number, include it in every subsequent prompt and in `dispatch record
--pr-number`.

### 5d. Backfill stage markers (TEST and PATCH only)

`/do-test` and `/do-patch` do not write their own stage markers — on the bridge, the worker's
dev-completion handler does it. Locally, the supervisor must:

```bash
sdlc-tool stage-marker --stage TEST --status completed --issue-number {issue_number} --run-id {run_id}
# or --status failed, per the subagent's report
```

All other stage skills self-mark; do NOT double-write markers for them.

A non-zero exit means the marker did **not** land — the ledger does not say what
you think it says. Read the stderr diagnostic and route it:

- `ISSUE_LOCKED` naming an `owner_run_id` that is **not** yours → a foreign run
  owns this issue. **Stop the loop** and report; continuing writes nothing and
  loses the run.
- `LEASE_ABSENT`, or `ISSUE_LOCKED` naming an id you recognize as your own →
  your lease lapsed. Run the Step 5e.6 re-ensure, **adopt the `run_id` it
  returns**, and retry the marker once under the adopted id.
- Anything else (a Redis/broker error, a timeout) → transient. Retry once, and
  only report-and-continue if it persists. A transient error is never an
  unconditional pipeline abort.

### 5d.4. REVIEW self-check gate (issue #2193)

If the stage just dispatched in 5c was `/do-pr-review`, do not treat its
return as sufficient to advance. `/do-pr-review` should have already called
`sdlc-tool verdict finalize` internally (an atomic verdict+trailer+marker
write) before returning — but the supervisor is the loud, committed backstop
for that contract, not a formality. Call the read-only self-check yourself:

```bash
sdlc-tool verdict selfcheck --pr {pr_number} --issue-number {issue_number}
```

Interpret the typed JSON result (`{ok, verdict_present, trailer_matches_head,
marker_completed, reason}`):

- `{"ok": true, ...}` → the verdict, its freshness trailer, and the REVIEW
  completion marker are all confirmed present. Proceed to 5e / loop back to
  5a as normal.
- `{"ok": false, ...}` → **HALT the loop immediately.** Print the machine-readable
  `reason` field loudly to the operator (e.g. "REVIEW SELF-CHECK FAILED:
  reason={reason} — pr={pr_number} issue={issue_number}"), along with which of
  `verdict_present` / `trailer_matches_head` / `marker_completed` is false.
  Do NOT re-dispatch REVIEW yourself, do NOT advance to DOCS/MERGE, and do NOT
  silently loop back to the router — this is a single loud refusal, replacing
  the old failure mode where the router would silently re-dispatch REVIEW
  forever on the same missing state. Report this as a stop condition in Step 6
  (Final Report) just like a `blocked` router decision.

This gate is prose/logic in this skill body only — it does not modify
`agent/sdlc_router.py`'s dispatch rows (those already fail-closed and
re-dispatch on missing state; see the router's rows 8/8b/9). It exists
specifically to make the supervised `/do-sdlc` path *loud* on the exact
failure the router would otherwise handle silently over multiple iterations.

### 5d.5. Tool-availability mismatch guard (issue #2022)

Inspect every stage subagent's final report before acting on it. If the final message is (or
begins with) a **bare shell command** — it starts with `git `, `gh `, `cd `, `pytest`, `python
`, or otherwise reads as a command line rather than the outcome/verdict/artifacts report the
prompt template asks for — AND the child made **zero tool calls**, the child was spawned on an
agent type without the tools its first step needed: it emitted the command it could not run as
plain text. Treat this as a **tool-availability mismatch, never a normal completion**:

1. Log it: "TOOL-AVAILABILITY MISMATCH: stage={skill}, final message is a bare shell command with zero tool calls"
2. Re-dispatch the same stage once on a Bash-capable agent type (`general-purpose`)
3. If the re-dispatch shows the same signature, stop and surface the mismatch to the human — do not loop

### 5d.6. Between-stage continuity re-ensure (issue #2452)

After the stage subagent returns (5c/5d/5d.4/5d.5) and before asking the router again (5a), re-ensure
this run's identity:

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Do **not** append a stderr redirect to `/dev/null`, a trailing `|| true`, or any other form that
discards the diagnostic.
The whole point of this step is the payload; a form that destroys it makes every instruction below
unfollowable and lets the run continue under an identity it has silently lost.

This is a **continuity proof** — the tool verifies the held `run_id` against the live lock/session
record and against the durable issue-keyed run-identity anchor on the ledger — not a lease
keepalive; it does not renew or extend the TTL (that is the heartbeat's job, issue #2714).

**Adopt the returned `run_id`.** When the payload carries no `blocked` flag, read `run_id` out of it
and use **that** value as `{run_id}` for every subsequent stage, prompt and `--run-id` flag in this
run. It may differ from the one you carried in: a lapsed lease is rebound, and a fresh contest mints
a new identity. Keeping your own stale copy is precisely how a run ends up writing markers nobody
accepts.

**Branch on the payload, not the exit code.** `session-ensure` exits 0 on every outcome it can
report — success and refusal alike — and signals refusal as `{"blocked": true, "reason": ...}` on
stdout, with the human-readable diagnostic on stderr. (Its sibling `stage-marker` *does* exit
non-zero — do not generalize one tool's disposition to the other.) A non-zero exit here is a
wrapper or usage error, emits **no payload at all**, and is not recoverable: stop and report rather
than retrying. On a `blocked` payload, route it through the **same** three-way table and
`owner_run_id` self-identity check from Step 2, so the own-ghost abandonment bug does not simply
relocate from the top of the loop to this stage seam:

- **Foreign owner** — `ISSUE_LOCKED` whose `owner_run_id` is neither yours nor a self-identity you
  recognize: this run has genuinely lost the issue. **Stop the loop** and report. This is the one
  stop condition.
- **Self / hand-off** — `SUPERVISED_RUN_ACTIVE`, or an `owner_run_id` the self-identity check
  confirms is your own: **inherit** `owner_run_id` as `{run_id}` and continue.
- **Orphaned lock** — `orphaned_lock: true`: wait out the TTL, re-ensure, adopt what comes back.
- **Transient** — a Redis/broker error, a timeout, or any payload that is none of the above:
  **surface it and retry**, then continue. Never convert a transient error into a pipeline abort —
  halting a healthy run on a broker blip is worse than the bug this step exists to catch. A payload
  that parses but carries neither `run_id` nor `blocked` belongs here too. **Empty stdout is not
  transient** — that is the wrapper/usage error above, and retrying it forever is how a broken
  install turns into an identity-less run.

### 5e. Check exit conditions

- Dispatched skill was `/do-merge` AND the subagent reports a merge → verify with `gh pr view {pr} --repo <resolved> --json state,mergedAt` from the tool result. If `MERGED`: **exit the loop, success.**
- Router returned `blocked` → already stopped in 5a.
- Iteration cap reached → stop and report how far the pipeline got.
- Otherwise → loop back to 5a. Brief one-line progress note per iteration (e.g. "CRITIQUE done (READY TO BUILD) → dispatching BUILD on sonnet").

## Step 6: Final Report

On exit (any path), report:

1. **Outcome**: merged / blocked (with guard + reason) / cap reached
2. **Stage trail**: each dispatch in order with its outcome and verdict
3. **Artifacts**: issue, plan path, PR number, merge commit
4. **Anything needing human attention**: unresolved blockers, skipped acknowledgments, follow-ups

## Step 7: Release the run lease

You hold this issue's run lease for as long as this supervision loop lives, and
nothing reclaims it when you simply stop — there is no terminal transition on a
HALT. Left held, it makes the *next* run on this issue refuse with a foreign-owner
block until the lease's ceiling lapses hours later.

So after the Final Report, hand the lease back with the pipeline tool's
**`session-release`** subcommand, passing this run's issue number and `run_id`
(see the repo context probe for the exact invocation). It is ownership-checked
and best-effort: a wrong or already-released `run_id` is a safe no-op, so run it
whenever in doubt and never let its output change your reported outcome.

Do this on the exits nothing else observes:

- the **5d.4 REVIEW self-check HALT**
- a **`blocked`** router decision (5a / 5e)
- the **iteration cap** being reached

The **merged** exit needs no action from you — completing the MERGE stage
releases the lease in the tool layer, on the marker write itself. Running the
step there anyway is harmless (it reports no lease held), but it is not what
makes the merged path correct.

## Relationship to /sdlc (in this repo)

| | `/sdlc` (in this repo) | `/do-sdlc` |
|---|---|---|
| Contract | dispatch ONE stage, return | loop until merge/blocked |
| Progression | PM session re-invokes | this skill re-invokes the router |
| Model assignment | PM passes `--model` when spawning dev sessions | supervisor passes `model:` on the Agent tool |
| Where it runs | bridge PM sessions + local | local Claude Code sessions |

`/sdlc` is a thin `context: fork` shim over this skill's router mode: it reads this body's Step
1–4 (resolve → session-ensure → assess → dispatch ONE), executes them once, and returns. Both
entry points consume the same router (`sdlc-tool next-skill` → `agent.sdlc_router.decide_next_dispatch()`)
and the same stored stage state — there is exactly one source of dispatch truth.
