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
| **Router mode** | a single-stage router invocation | Steps 1–4 once | dispatch ONE sub-skill, then return; the supervising session re-invokes |
| **Supervisor mode** | `/do-sdlc` | Steps 1–7 | loop until merge/blocked |

Router mode assesses state and dispatches exactly one `/do-*` skill, expecting the supervising
session to re-invoke it after each stage completes. Supervisor mode is the local stand-in for that
session: it re-invokes the router itself, dispatching each stage to a subagent on the
stage-appropriate model (opus/sonnet), until merge, a blocking guard, or the iteration cap. You
are the supervisor or router, never the worker: you assess, dispatch, and track, and the stage
subagents do all the work.

**Redundant-context check.** If a live supervising context already owns this issue, supervisor
mode is redundant — that context IS the supervision loop. Do not run it; drive the pipeline one
stage at a time through router mode instead. A live *supervised-run signal* for the issue number
is a different case: if its `owner_run_id` is one this run already holds, it is this run's own
hand-off and must be inherited, not treated as grounds to stand down (see Step 2).

## Repo Context Probe

If `docs/sdlc/do-sdlc.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The defaults drive the pipeline through `sdlc-tool` (a stage-state CLI on `PATH`), `gh`, and
`git`. Repo-coupled specifics — worktree ownership, target-repo and `GH_REPO` semantics, the
router's internal guard implementation and its source references, the router-shim relationship,
and `sdlc-tool` command discipline — live in that context file, not in this global body.

## Hard Rules

1. **NEVER write code, run tests, or create plans directly** — every stage executes inside a stage
   subagent that invokes the stage's `/do-*` skill (supervisor mode), or is dispatched as exactly
   one sub-skill after which the router returns (router mode).
2. **NEVER decide dispatch yourself** — `sdlc-tool next-skill` is the only source of dispatch
   decisions; it encodes all guards (G1–G9) and dispatch rows. Do not second-guess it, reorder
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
   reports "running in the background, I'll continue when it completes" and then never does. Every
   stage subagent must be spawned with `run_in_background: false` so its result is in hand before
   the loop advances — in router mode too, for the one stage you dispatch.
7. **NEVER spawn agent teammates for stage work.** Where agent teams are enabled, ignore those
   affordances: a teammate's idle notification is not a completion signal (teammates go idle
   mid-task with deliverables unfinished), and an in-process teammate cannot be reliably resumed.
   Every dispatch is a foreground subagent per Rule 6.
8. **NEVER loop in router mode** — invoke one sub-skill, then return. The supervising session
   handles progression.

## Worktree & branch ownership

**Slug identity always wins.** Each issue's build fork exclusively owns `.worktrees/{slug}` and
`session/{slug}`, where `{slug}` is the lane's identity, recorded once at lane start and read
(never re-derived) via the lane-identity resolver. Do NOT pre-allocate per-supervisor lanes:
nothing reads a lane override, so lane instructions are silently dropped and every issue's
builders land in `.worktrees/{slug}` regardless. Converging fork + supervisor onto one branch per
plan is deliberate — it structurally collapses duplicate PRs, since GitHub permits only one open
PR per head branch. Concurrent builders inside the one slug worktree must write disjoint file sets
(do-build's `Parallel: true` convention: no shared-file writes).

## Stage→Model Dispatch Table

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
Scope `gh` reads with `--repo` whenever the repository is known or derivable — under a foreign
`GH_REPO`, or from a wrong cwd, a bare `gh` command answers about a *different* repository and
exits 0. Resolve the slug from `GH_REPO` or from `gh repo view --json nameWithOwner -q
.nameWithOwner` run at the target repo root.

- **Issue reference** (e.g., `issue 123`, `issue #123`): `gh issue view {number} --repo <resolved>` when
  a slug resolved.
- **PR reference** (e.g., `PR 363`, `pr #363`): `gh pr view {number} --repo <resolved> --json
  number,title,state,headRefName,reviewDecision,statusCheckRollup,body` to get the branch name,
  review state, and check status. Then extract the linked issue number from the PR body (look
  for `Closes #N` or `Fixes #N`).
- **Bare feature description** (no number): in supervisor mode, dispatch a `/do-issue` stage
  subagent first (sonnet), read the created issue number from its report, then proceed. In router
  mode, do not proceed without an issue number — surface that to the supervising session.

**PR state informs Step 3**: a provided PR's current state (checks passing/failing, review
approved/changes-requested) tells you which stage to resume from. Skip stages already complete —
do not restart from scratch.

## Step 2: Ensure the Tracking Session

```bash
# SDLC_REPO: GitHub slug (org/repo) — used to build issue/PR URLs.
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
# Run identity: `session-ensure` is the EXCLUSIVE minting site for the run_id — one hex
# identity for this whole run, minted by winning the issue lock and emitted in the JSON.
# No env vars, no run files: every state-mutating call passes it back via `--run-id`.
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"
```

When the target repo is not the one you are standing in, `sdlc-tool` needs its filesystem path.
The repo context probe declares the env var that carries it; export that var once, for the
lifetime of the loop, and use it wherever this body writes `{target_repo_path}`.

Let stderr through and read the JSON payload. `session-ensure` reports a refusal *in the payload*
(`{"blocked": true, "reason": ...}`), not through the exit code — it exits 0 either way — so a
discarded diagnostic here is a run that proceeds under an identity it does not hold. **Record the
`run_id`** from that payload and carry it through every subsequent step; re-runs reuse the
existing tracking session.

The full ownership contract — which calls require `--run-id`, the three-way refusal decision
table, the self-identity check that keeps a run from standing down for its own lock, and recovery
after run_id loss — is in [Run Identity & Lock Ownership](RUN_IDENTITY.md). Read it now if
`session-ensure` returned a `blocked` payload, and re-read it at any later refusal.

## Step 3: Assess Current State

Check what already exists for this issue. Local operations run against the target repo path from
Step 2 (`.` for same-repo work). Run ALL of these checks — do not skip any.

**Command discipline (Steps 3-4):** run each check as a separate single-line command and read the
output from the tool result — no pipes, no command substitution, no `||` fallbacks, no
environment-variable capture. You interpret the output and decide the next step.

### Step 3.0: Query stage state (primary signal)

Stage completion is the **exclusive signal** for routing decisions, determined ONLY by stored
state — never by artifact inference.

```bash
sdlc-tool stage-query --issue-number {issue_number}
```

Interpret the JSON output from the tool result:

- Non-empty object with stage keys (e.g. `{"ISSUE": "completed", "PLAN": "completed", "BUILD":
  "in_progress"}`): use it as the exclusive signal for the dispatch table. A stage is behind us
  ONLY if its value is `"completed"` — or `"skipped"`, which only PLAN and CRITIQUE can ever hold
  and which means the pipeline never dispatched that stage because this issue has no plan
  document. Never re-dispatch a skipped stage. Skip steps 3a-3e.
- Empty `{}` or an `unavailable` marker: fall through to the dispatch-history fallback below.

### Steps 3a-3e: Dispatch History Fallback

These run ONLY when stage state is unavailable. Use conversation context to identify which skills
were already dispatched in this session; artifacts check preconditions ("does a PR exist?") and
never declare a stage complete. `gh` uses `$GH_REPO` automatically for the cross-repo case.

```bash
# 3a. Check if a plan doc references this issue
grep -r "#{issue_number}" docs/plans/
```

```bash
# 3b. List all branches (filter for session/ prefix in the tool result)
git -C "{target_repo_path}" branch -a
```

```bash
# 3c. Check if a PR already exists
gh pr list --repo <resolved> --search "#{issue_number}" --state open
# Cross-check with live refs — the --search index lags GitHub; --head queries live refs:
#   gh pr list --repo <resolved> --head session/{slug} --state open   (reuse if present; keyed by head branch, not issue #)
```

```bash
# 3d. If a PR exists, get its state: checks, review, branch
gh pr view {pr_number} --repo <resolved> --json number,headRefName,reviewDecision,statusCheckRollup,body
```

3e. Read `reviewDecision` from that output. `APPROVED` / `CHANGES_REQUESTED` are formal GitHub
outcomes. An empty value is **ambiguous**: on a non-self-authored PR no review is posted yet, but
on a self-authored PR it is expected even after a full review. Always cross-check the recorded
REVIEW verdict from Step 3.0 before concluding no review exists.

### Step 3.4: Check Documentation Status

REQUIRED when a PR exists and review is clean (APPROVED); skip only if the pipeline hasn't
reached REVIEW yet.

```bash
# 3.4a. List files changed in the PR (count docs/ entries from the tool result)
gh pr diff {pr_number} --repo <resolved> --name-only

# 3.4b. Find the plan path for this issue (first match from the tool result)
grep -rl "#{issue_number}" docs/plans/

# 3.4c. Read the plan's Documentation section (inspect for unchecked tasks)
cat docs/plans/{plan-filename}.md
```

For the DOCS stage completion check, re-read the Step 3.0 `stage-query` output. Do not pipe JSON
through a shell here.

**Decision logic for docs**:
- If the plan has a `## Documentation` section with unchecked tasks → docs NOT done
- If PR has zero `docs/` file changes AND plan requires doc tasks → docs NOT done
- If docs tasks are all checked AND `docs/` changes exist in PR → docs done
- When in doubt, dispatch `/do-docs` — it is idempotent and will no-op if nothing needs updating

## Step 3.5: Legal Dispatch Guards (reference)

`sdlc-tool next-skill` evaluates the G1–G9 guards itself — do NOT re-evaluate them by hand. The
table below exists so you can interpret a `blocked` decision or a forced dispatch when the tool
returns one. The router's implementation, its ordering rationale, and the guard-by-guard nuances
are declared in the repo context probe.

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

**G4 is universal** — every stage, including DOCS and MERGE. Repeated dispatches without state
change WILL trip it. G4 also precedes G8, so a persistently false artifact claim is re-dispatched
silently at first and escalates via the G4 cap rather than blocking on the first mismatch.

**G5 applies to CRITIQUE only**, not REVIEW — review verdicts legitimately change on unchanged
diffs (CI flips, new comments). G4 handles REVIEW non-determinism instead.

**Open-PR step-asides.** Once `pr_number` is set, G1 and G5's revision branch defer to G3, the
canonical open-PR plan-stage redirect — a stale pre-PR critique verdict must never route a shipped
PR back to `/do-plan`. G7 is likewise gated on `pr_number is None`.

**G7 blocks build while plan revision is in flight.** The lock is set by `/do-plan-critique` when
the verdict requires a revision pass, cleared by `/do-plan` after pushing the revision, and
self-heals from the plan frontmatter. `/do-plan` also records an event-scoped timestamp alongside
its revision-applied flag, so the router can tell the settle-and-build revision a critique verdict
judged apart from a later unrelated one — that convergence latch engages only for verdicts that do
not require a revision; NEEDS REVISION / MAJOR REWORK stay stale and route back to
`/do-plan-critique`.

**ISSUE_LOCKED is not a G-guard.** `next-skill` checks the issue-level ownership lock *before*
evaluating G1–G9 and short-circuits on it. See [Run Identity & Lock Ownership](RUN_IDENTITY.md).

**Known gap — stale REVIEW verdict after PATCH.** G3 and G6 key off the recorded REVIEW verdict
containing `APPROVED`, not off whether it postdates the most recent PATCH commit. Before trusting
a router-proposed `/do-merge`, confirm with `sdlc-tool verdict get --stage REVIEW --issue-number
{N}`; if the verdict predates the patch, dispatch `/do-pr-review` first.

**Merge gate:** `/do-merge` fires only when REVIEW and DOCS are complete, the PR merge state is
CLEAN, CI is all-passing, and the recorded REVIEW verdict is APPROVED at the current head.

## Step 4: Dispatch ONE Sub-Skill

**Do not pattern-match against a hand-edited table.** Call the routing tool and dispatch whatever
skill it returns; it evaluates every guard and dispatch row against live state. In router mode
this is the whole job: call `next-skill`, record the dispatch, invoke the ONE returned skill, then
**return**. In supervisor mode the same call feeds Step 5's loop.

```bash
# Get the next dispatch decision
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id}
```

The tool decides at most ONE skill per call, outputting JSON in one of these shapes. Every shape
carries `decision`:

```json
{"skill": "/do-build", "reason": "...", "row_id": "4a", "decision": "dispatch", "recorded": false, "recorded_reason": "NOT_PERSISTED_CALL_DISPATCH_RECORD"}
{"decision": "terminal", "reason": "Pipeline complete — nothing to dispatch ...", "evidence": "merge_marker", "row_id": "T"}
{"blocked": true, "decision": "blocked", "reason": "G4: stage oscillation ...", "guard_id": "G4"}
{"blocked": true, "decision": "blocked", "reason": "ISSUE_LOCKED", "owner_run_id": "...", "owner_session_id": "...", "orphaned_lock": false}
```

`next-skill` **writes nothing** — with or without `--run-id`. `recorded: false` is on every
dispatch decision to say so out loud: the ledger has NOT advanced until you make the separate
`sdlc-tool dispatch record` call below. Skipping it leaves the router re-deriving from a history
that never grew, so it re-decides the same row until G4 blocks the lane for oscillating (#2897).

**How to use the output:**
1. If `decision` is `"dispatch"`: record the dispatch via `sdlc-tool dispatch record` (see below),
   then invoke the returned `skill`.
2. If `decision` is `"terminal"`: the lane is **finished**. Report the pipeline complete, citing
   `reason` and `evidence`. Do NOT record a dispatch, do NOT invoke a skill, and do NOT report it
   as a failure — this is a success, not the `blocked` error stop below.
3. If `decision` is `"blocked"`: surface the `reason` to the human and wait. Do NOT loop or guess an
   alternative skill — this holds identically for a G-guard block and for `ISSUE_LOCKED`, where
   you also report `owner_session_id`.
4. If `decision` is `"error"`: log the `error` field and escalate to the human.

**Before recording and dispatching**, also supply `--proposed-skill` when you already know what
skill you intend to invoke (enables G3 PR-lock detection):
```bash
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id} --proposed-skill /do-build
```

Record every dispatch decision via `sdlc-tool dispatch record` BEFORE invoking the sub-skill —
this preserves the G4 oscillation signal even if the sub-skill crashes mid-execution.

```bash
sdlc-tool dispatch record --skill /do-build --issue-number {issue_number} --run-id {run_id}
# add --pr-number {pr_number} for review/patch/merge stages
# inspect history (debug G4 state; read-only, no --run-id):
sdlc-tool dispatch get --issue-number {issue_number}
```

`sdlc-tool dispatch record` is the only correct runtime entry point — never reach past it into
the router's internals from a shell or skill script.

Do NOT restart from scratch if prior stages are already complete.

## Step 5: Supervision Loop (supervisor mode only)

Repeat the following cycle. **Iteration cap: 15 dispatches** (a happy path is 8 stages; the cap is
a backstop above realistic patch/re-review cycles — G4 catches genuine oscillation long before
it). Router mode never enters this step; it returns after Step 4.

### 5a. Ask the router

```bash
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id}
```

(Read-only — `next-skill` never mints, adopts, or renews the lock. `--run-id` is an identity
assertion for its lock peek, so this run is never told to stand down for a lock it holds itself.
Always pass it here.)

Interpret the JSON from the tool result (same shapes as Step 4):

- `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` → route it through
  [Run Identity & Lock Ownership](RUN_IDENTITY.md) before deciding whether this is actually your
  own lock. Only a genuinely foreign owner stops the loop.
- `{"decision": "terminal", ...}` → **EXIT the loop cleanly.** The pipeline is complete; report it as done, citing `reason` and `evidence`. This is NOT the error stop below — do not report a guard, a blocker, or a failure, and do not record a dispatch.
- `{"blocked": true, ...}` (other reasons) → **STOP the loop.** Report the `reason` and `guard_id` to the human, plus a summary of stages completed so far. Do not retry, do not guess an alternative skill.
- `{"skill": "...", "decision": "dispatch", "recorded": false, ...}` → continue to 5b, which is what actually writes the dispatch. The router decides at most ONE skill per call; there is no parallel-pair shape.
- Anything else (error key, empty) → STOP and surface the error.

### 5b. Record the dispatch

```bash
sdlc-tool dispatch record --skill {skill} --issue-number {issue_number} --run-id {run_id}
# include --pr-number {pr} once a PR exists (review/patch/docs/merge stages)
```

### 5c. Spawn the stage subagent

**One writer per artifact — enumerate live children before you dispatch.** Every stage you dispatch
is a writer on a shared artifact, and the plan doc in particular is a single file no worktree
isolates. If a child is still holding it, do not dispatch a second one onto it and do not edit it
yourself; wait for the outstanding child to report. Hard Rule 6 already keeps exactly one child out
at a time, so the ordinary loop satisfies this for free — the rule bites when you are tempted to
fan out, or to "just fix one line" in a doc a child is revising.

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

`/do-test` and `/do-patch` do not write their own stage markers, so the supervisor must:

```bash
sdlc-tool stage-marker --stage TEST --status completed --issue-number {issue_number} --run-id {run_id}
# or --status failed, per the subagent's report
```

All other stage skills self-mark; do NOT double-write markers for them.

A non-zero exit means the marker did **not** land — the ledger does not say what you think it
says. Read the stderr diagnostic and route it:

- `ISSUE_LOCKED` naming a foreign `owner_run_id` → **stop the loop** and report; continuing writes
  nothing and loses the run.
- `LEASE_ABSENT`, or `ISSUE_LOCKED` naming an id you recognize as your own → your lease lapsed.
  Run the Step 5d.6 re-ensure, **adopt the `run_id` it returns**, and retry the marker once.
- Anything else (a broker error, a timeout) → transient. Retry once; report-and-continue only if
  it persists. A transient error is never an unconditional pipeline abort.

### 5d.4. REVIEW self-check gate

If the stage just dispatched in 5c was `/do-pr-review`, do not treat its return as sufficient to
advance. It should have already finalized its verdict internally (an atomic verdict+trailer+marker
write), but the supervisor is the loud backstop for that contract. Call the read-only self-check
yourself:

```bash
sdlc-tool verdict selfcheck --pr {pr_number} --issue-number {issue_number}
```

Interpret the typed JSON (`{ok, verdict_present, trailer_matches_head, marker_completed, reason}`):

- `{"ok": true, ...}` → verdict, freshness trailer, and REVIEW completion marker are all present.
  Proceed as normal.
- `{"ok": false, ...}` → **HALT the loop immediately.** Print `reason` loudly to the operator along
  with which of `verdict_present` / `trailer_matches_head` / `marker_completed` is false. Do NOT
  re-dispatch REVIEW, advance to DOCS/MERGE, or silently loop back to the router — this is a single
  loud refusal. Report it as a stop condition in Step 6, like a `blocked` router decision.

### 5d.5. Tool-availability mismatch guard (issue #2022)

Inspect every stage subagent's final report before acting on it. If the final message reads as a
**bare shell command** (`git `, `gh `, `cd `, `pytest`) rather than the outcome/verdict/artifacts
report the prompt template asks for — AND the child made **zero tool calls** — it was spawned on
an agent type lacking the tools its first step needed. This is a **tool-availability mismatch,
never a normal completion**:

1. Log it: "TOOL-AVAILABILITY MISMATCH: stage={skill}, final message is a bare shell command with zero tool calls"
2. Re-dispatch the same stage once on a Bash-capable agent type (`general-purpose`)
3. If the re-dispatch shows the same signature, stop and surface the mismatch to the human — do not loop

### 5d.6. Between-stage continuity re-ensure

After the stage subagent returns and before asking the router again (5a), re-ensure this run's
identity:

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Never discard its stderr or stdout — the payload is the whole point of the step. **Adopt the
`run_id` it returns**, which may differ from the one you carried in, and branch on the payload
rather than the exit code. [Run Identity & Lock Ownership](RUN_IDENTITY.md) has the full
disposition, including the transient-error class that must never become a pipeline abort.

### 5e. Check exit conditions

- Dispatched skill was `/do-merge` AND the subagent reports a merge → verify with `gh pr view {pr} --repo <resolved> --json state,mergedAt` from the tool result. If `MERGED`: **exit the loop, success.**
- Router returned `terminal` → already exited cleanly in 5a. **Success**, not a stop condition.
- Router returned `blocked` → already stopped in 5a.
- Iteration cap reached → stop and report how far the pipeline got.
- Otherwise → loop back to 5a. Brief one-line progress note per iteration (e.g. "CRITIQUE done (READY TO BUILD) → dispatching BUILD on sonnet").

## Step 6: Final Report

On exit (any path), report:

1. **Outcome**: merged / pipeline complete (terminal, with `evidence`) / blocked (with guard + reason) / cap reached
2. **Stage trail**: each dispatch in order with its outcome and verdict
3. **Artifacts**: issue, plan path, PR number, merge commit
4. **Anything needing human attention**: unresolved blockers, skipped acknowledgments, follow-ups

## Step 7: Release the run lease

You hold this issue's run lease for as long as this supervision loop lives, and nothing reclaims
it when you simply stop — there is no terminal transition on a HALT. Left held, it makes the
*next* run on this issue refuse with a foreign-owner block until the lease's ceiling lapses.
After the Final Report, hand the lease back with the pipeline tool's **`session-release`**
subcommand, passing this run's issue number and current `run_id` (the repo context probe declares
the exact invocation). It is ownership-checked and best-effort: a wrong or already-released
`run_id` is a safe no-op, so run it whenever in doubt, and never let its output change your
reported outcome.

Do this on the exits nothing else observes: the **5d.4 REVIEW self-check HALT**, a **`blocked`**
router decision (5a / 5e), a **`terminal`** router decision (this run may never have written a
MERGE marker — the lane can be finished by evidence it inherited), and the **iteration cap**
being reached. The **merged** exit needs no
action from you — completing the MERGE stage releases the lease in the tool layer, on the marker
write itself.
