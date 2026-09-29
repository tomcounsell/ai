---
name: do-sdlc
description: "Supervise a full SDLC run to merge ('do-sdlc', 'run the full pipeline', 'ship this issue end to end', 'supervise the sdlc'); also the /sdlc router contract."
context: fork
---

# do-sdlc — SDLC Pipeline Supervisor and Single-Stage Router

Drive one issue through the SDLC pipeline by asking the router what comes next and dispatching
that stage, never by doing stage work yourself. Two entry modes share one step spine:

| Mode | Entry | Executes | Contract |
|------|-------|----------|----------|
| **Router mode** | a single-stage router invocation | Steps 1–4 once | dispatch ONE sub-skill, then return; the supervising session re-invokes |
| **Supervisor mode** | `/do-sdlc` | Steps 1–7 | loop until merge, a block, or the iteration cap |

Supervisor mode is the local stand-in for a supervising session: it dispatches each stage to a
subagent on the stage's model until the lane finishes. If a live supervising context already owns
this issue, supervisor mode is redundant: drive it through router mode instead. A live
*supervised-run signal* whose `owner_run_id` this run already holds is this run's own hand-off and
is inherited, not grounds to stand down (see Step 2).

**Done:** the PR is merged (verified), or the router returns `terminal`, or you stopped on a
`blocked` decision, a REVIEW self-check HALT, or the iteration cap, and reported which (Step 6) and
released the lease (Step 7). This skill runs in a fork with exactly one turn: a text-only message
while stages remain is a progress note, not the end, so keep dispatching until one of those exits.

## Repo Context Probe

If `docs/sdlc/do-sdlc.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.
The defaults drive the pipeline through `sdlc-tool` (a stage-state CLI on `PATH`),
`gh`, and `git`. Repo-coupled specifics (worktree ownership, target-repo and `GH_REPO` semantics,
the guard implementation, the router shim, `sdlc-tool` invocations) live in that context file.

The probe also sets **verdict authority**: a repo with no `docs/sdlc/` context declares no verdict
substrate, so posted GitHub reviews are the authoritative REVIEW verdicts and Step 5d.4 skips
itself. Say so in your first status line ("no verdict substrate declared: GitHub reviews are
authoritative verdicts"). This is a mode, never a refusal.

## Hard Rules

1. You assess, dispatch, and track; you never write code, run tests, or create plans. Every stage
   runs in a stage subagent invoking its `/do-*` skill (supervisor mode), or as the one sub-skill
   the router dispatches (router mode).
2. `sdlc-tool next-skill` is the only source of dispatch decisions; it encodes every guard and
   dispatch row. Do not reorder, skip, or second-guess it.
3. Stop on a `blocked` decision and surface the reason to the human.
4. Pass `model:` per the Stage→Model table on every stage spawn; never rely on the inherited default.
5. Record the dispatch before spawning the subagent, so the G4 oscillation signal survives a crash.
6. Dispatch with `run_in_background: false` and never end the turn waiting on a background child.
   This fork gets one turn; the Agent tool defaults to background, and a fork has no later turn to
   be notified on, so a background dispatch is never collected. This holds in router mode too.
7. Never spawn agent teammates for stage work: a teammate's idle notification is not a completion
   signal and an in-process teammate cannot be reliably resumed.
8. Router mode never loops: invoke one sub-skill, then return.

## Worktree & branch ownership

Each issue's build owns `.worktrees/{slug}` and `session/{slug}`, where `{slug}` is the lane
identity recorded once at lane start and read (never re-derived) via the lane-identity resolver. Do
not pre-allocate per-supervisor lanes: nothing reads a lane override, so builders land in
`.worktrees/{slug}` regardless. One branch per plan is deliberate: GitHub permits one open PR per
head branch, which collapses duplicate PRs. Concurrent builders in the one worktree write disjoint
file sets.

## Stage→Model Dispatch Table

| Stage | Skill | `model:` | Effort | Why |
|-------|-------|----------|--------|-----|
| ISSUE | /do-issue | sonnet | medium | One-shot writing; PLAN and CRITIQUE catch its misses |
| PLAN | /do-plan | opus | medium | Gate: architectural design that BUILD executes |
| CRITIQUE | /do-plan-critique | opus | medium | Gate: its misses reach BUILD unchallenged |
| BUILD | /do-build | opus | medium | Multi-step codebase work; TEST and REVIEW catch misses |
| TEST | /do-test | opus | medium | Runs suites and triages failures; REVIEW and CI catch misses |
| PATCH | /do-patch | opus | low | Works from a precise review or test spec |
| REVIEW | /do-pr-review | opus | high | Last judgment gate before an irreversible merge; nothing downstream catches its misses |
| DOCS | /do-docs | opus | low | Multi-file writing against a diff; REVIEW catches misses |
| MERGE | /do-merge | opus | medium | Programmatic gate on an irreversible step; the script decides, the model must not argue past a refusal |

Opus finishes agentic stages in fewer turns than Sonnet, which makes it faster and cheaper end to
end on everything but one-shot writing. The Agent tool takes `model` but not `effort`; each stage's
effort comes from its skill's `effort:` frontmatter.

## Step 1: Resolve the Issue or PR

Determine whether the input is an issue reference, a PR reference, or a bare feature description.
Scope every `gh` read with `--repo`: under a foreign `GH_REPO` or from a wrong cwd, a bare `gh`
command answers about a *different* repository and exits 0. Resolve the slug from `GH_REPO` or from
`gh repo view --json nameWithOwner -q .nameWithOwner` at the target repo root.

- **Issue** (`issue 123`, `#123`): `gh issue view {number} --repo <resolved>`.
- **PR** (`PR 363`): `gh pr view {number} --repo <resolved> --json
  number,title,state,headRefName,reviewDecision,statusCheckRollup,body`, then take the linked
  issue from `Closes #N` / `Fixes #N` in the body. The PR's state tells you which stage to resume
  from; never restart completed stages.
- **Bare description**: in supervisor mode, dispatch a `/do-issue` stage subagent first and read
  the new issue number from its report. In router mode, do not proceed without an issue number;
  surface that to the supervising session.

## Step 2: Ensure the Tracking Session

```bash
# SDLC_REPO: GitHub slug (org/repo) — used to build issue/PR URLs.
SDLC_REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || git remote get-url origin | sed 's/.*github.com[:/]//;s/.git$//')
# session-ensure is the only minting site for the run_id: one hex identity for the whole run,
# minted by winning the issue lock. Every state-mutating call passes it back via --run-id.
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"
```

Run every `sdlc-tool` call from inside the target repo's checkout; the tool resolves the repo from
the caller's cwd. `{target_repo_path}` below is that checkout's root. To act on a checkout you are
not standing in, pass its path on each call through the env var the repo context probe declares;
an `export` does not survive between shell invocations.

Let stderr through and read the JSON. `session-ensure` exits 0 either way and reports refusal *in
the payload* (`{"blocked": true, "reason": ...}`). **Record the `run_id`** and carry it through
every step; re-runs reuse the existing tracking session. On a `blocked` payload, or any later
refusal, read [Run Identity & Lock Ownership](RUN_IDENTITY.md): it holds the three-way refusal
table, the self-identity check, and run_id-loss recovery.

## Step 3: Assess Current State

Local operations run against the target repo path (`.` for same-repo work). Run each check as a
separate single-line command and read the result from the tool output: no pipes, command
substitution, `||` fallbacks, or env-var capture.

### Step 3.0: Query stage state (primary signal)

```bash
sdlc-tool stage-query --issue-number {issue_number}
```

Stored stage state is the only signal for stage completion, never artifact inference. A non-empty
object (e.g. `{"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"}`) is
authoritative: a stage is behind us only if it is `"completed"` or `"skipped"` (only PLAN and
CRITIQUE can be skipped, meaning the issue has no plan document; never re-dispatch one). Skip
3a–3e. On `{}` or an `unavailable` marker, fall through to the fallback.

### Steps 3a-3e: Dispatch History Fallback

Only when stage state is unavailable. Use conversation context for what was already dispatched;
artifacts check preconditions and never declare a stage complete.

```bash
grep -r "#{issue_number}" docs/plans/                                    # 3a. plan doc
git -C "{target_repo_path}" branch -a                                    # 3b. session/ branches
gh pr list --repo <resolved> --search "#{issue_number}" --state open     # 3c. existing PR
# The --search index lags GitHub; cross-check live refs, keyed by head branch:
#   gh pr list --repo <resolved> --head session/{slug} --state open
gh pr view {pr_number} --repo <resolved> --json number,headRefName,reviewDecision,statusCheckRollup,body   # 3d.
```

3e. `reviewDecision` `APPROVED` / `CHANGES_REQUESTED` are formal outcomes. An empty value is
ambiguous: on a self-authored PR it is expected even after a full review. Cross-check the recorded
REVIEW verdict from Step 3.0 first; with no verdict substrate, the PR's posted reviews are the
verdict.

### Step 3.4: Check Documentation Status

Only once a PR exists and review is APPROVED. Read the Step 3.0 output for DOCS; otherwise check
`gh pr diff {pr_number} --repo <resolved> --name-only` for `docs/` changes and the plan's
`## Documentation` section (`grep -rl "#{issue_number}" docs/plans/`). Docs are not done if that
section has unchecked tasks, or if the plan requires doc tasks and the PR touches no `docs/` file.
When in doubt, dispatch `/do-docs`; it no-ops when nothing needs updating.

## Step 3.5: Legal Dispatch Guards (reference)

`sdlc-tool next-skill` evaluates these itself; do not re-evaluate them by hand. The table is for
interpreting a `blocked` decision or a forced dispatch. Guards run in the pinned `GUARDS` order
`[T, G1, G2, G3, G4, G9, G8, G7, G5, G6]`, first non-`None` decision wins (IDs are historical,
not evaluation order). `T` runs first: a lane that has shipped has no correct dispatch.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| T: Terminal lane | `stage_states["MERGE"]` settled (`completed`/`skipped`) OR `pr_state == "MERGED"` | `Terminal` decision — a clean "nothing to dispatch" exit, distinct from `blocked`. Preempts every guard and the dispatch table. Disable via `SDLC_TERMINAL_GUARD=false`. |
| G1: Critique loop | Latest critique verdict contains `NEEDS REVISION` or `MAJOR REWORK` AND `last_dispatched_skill == /do-plan-critique` | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` (2) AND CRITIQUE is not completed | Escalate: `blocked` with reason `critique cycle cap reached` |
| G3: PR lock | `pr_number` is set AND (`last_dispatched_skill` OR proposed dispatch) is `/do-plan` or `/do-plan-critique` | Four-leg ladder: `/do-merge` (REVIEW and DOCS complete, verdict `APPROVED`, head verified fresh) → `/do-patch` (review requested changes, or REVIEW failed) → `/do-docs` (REVIEW completed with a head-fresh `APPROVED` verdict, AND DOCS not completed — #3227) → else `/do-pr-review` |
| G4: Oscillation (universal) | `same_stage_dispatch_count >= 3` | Escalate: `blocked` with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| G9: Blocked-on-conflict | Recorded REVIEW verdict contains `BLOCKED_ON_CONFLICT` AND `pr_merge_state` not in the non-conflicting set (`CLEAN`, `HAS_HOOKS`, `UNSTABLE`, `BLOCKED`, `BEHIND`) AND the verdict is not stale (no `/do-patch` landed after it, #2796) | Escalate: `blocked` with reason naming the PR, the merge state, and the rebase — no SDLC skill resolves merge conflicts |
| G8: Stage-advance verification | `context["stage_artifacts_verified"] is False` (a claimed stage artifact — PR, branch, plan commit — failed live verification) | Re-dispatch the skill owning `context["unverified_stage"]` |
| G7: Plan-revising lock | `pr_number` is None AND `plan_revising == True` AND no `/do-plan` revision has landed since the latest CRITIQUE verdict (event-scoped, #2787 — NOT the sticky `revision_applied` boolean) | `/do-plan` (if `last_dispatched_skill == /do-plan-critique`); Escalate `blocked` (if no `/do-plan` in last `MAX_PLAN_REVISING_DISPATCHES + 1` turns) |
| G5: Unchanged critique artifact | `_verdicts["CRITIQUE"]` has `artifact_hash` AND current plan file hash matches | Use cached verdict: `/do-plan` (NEEDS REVISION) or `/do-build` (READY TO BUILD, no concerns). Never re-dispatch `/do-plan-critique` on an unchanged plan. **Steps aside unconditionally on `READY TO BUILD (with concerns)`** so rows 2b/4b/4c own that state (#2787); the bound there is `MAX_CONCERN_RECRITIQUE_ROUNDS`, not G5. |
| G6: Terminal merge ready | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` AND the REVIEW verdict's head is verified fresh against `context['pr_head_sha']` | `/do-merge {pr_number}` |

Facts for reading a decision:

- **G4 is universal**, DOCS and MERGE included, and precedes G8: a persistently false artifact claim
  is re-dispatched silently first and escalates through the G4 cap.
- **G5 is CRITIQUE-only.** Review verdicts legitimately change on unchanged diffs (CI flips, new
  comments); G4 handles REVIEW non-determinism.
- **Open-PR step-asides.** Once `pr_number` is set, G1 and G5's revision branch defer to G3, and G7
  is gated on `pr_number is None`, so a stale pre-PR critique verdict never routes a shipped PR back
  to `/do-plan`. Every plan-stage row stands down on `_plan_stage_stood_down` (`pr_number` set, or
  BUILD `in_progress`/`completed`) (#3249).
- **Terminal merge needs a verified-fresh head.** G3 leg 1, G6, and dispatch row 10 each require
  positive evidence that the `APPROVED` verdict judged the live head. An absent
  `context['pr_head_sha']` is not evidence: they decline, and a merge-ready state with no head
  signal escalates to `Blocked(guard_id='NO_RULE')` by design. In a repo with no verdict substrate,
  confirm the posted GitHub review postdates the latest push before merging.
- **`Blocked(NO_RULE)` at `BUILD == completed` with no PR and no branch** is correct, not a bug: the
  plan was accepted, so a missing branch means the build output was lost. Find the branch; do not
  re-dispatch `/do-plan`.
- **G7 blocks build while a plan revision is in flight.** `/do-plan-critique` sets the lock when a
  revision is required; `/do-plan` clears it after pushing the revision and records an event-scoped
  timestamp, so the router can tell the revision a verdict judged from a later one. The lock
  self-heals from plan frontmatter.
- **ISSUE_LOCKED is not a G-guard**: `next-skill` checks the issue lock before any guard (see
  [Run Identity & Lock Ownership](RUN_IDENTITY.md)).

## Step 4: Dispatch ONE Sub-Skill

Call the routing tool and dispatch whatever it returns. In router mode this is the whole job:
call `next-skill`, record the dispatch, invoke the ONE returned skill, then **return**. In
supervisor mode the same call feeds Step 5.

```bash
sdlc-tool next-skill --issue-number {issue_number} --run-id {run_id}
# add --proposed-skill /do-build when you already know the intended skill (enables G3 PR-lock detection)
```

It decides at most ONE skill per call. Every shape carries `decision`:

```json
{"skill": "/do-build", "reason": "...", "row_id": "4a", "decision": "dispatch", "recorded": false, "recorded_reason": "NOT_PERSISTED_CALL_DISPATCH_RECORD"}
{"decision": "terminal", "reason": "Pipeline complete — nothing to dispatch ...", "evidence": "merge_marker", "row_id": "T"}
{"blocked": true, "decision": "blocked", "reason": "G4: stage oscillation ...", "guard_id": "G4"}
{"blocked": true, "decision": "blocked", "reason": "ISSUE_LOCKED", "owner_run_id": "...", "owner_session_id": "...", "orphaned_lock": false}
```

`next-skill` writes nothing. `recorded: false` means the ledger has not advanced until you call
`dispatch record`; skipping it makes the router re-decide the same row until G4 blocks the lane
(#2897).

- `dispatch`: record it, then invoke the returned `skill`.
- `terminal`: the lane is finished. Report it complete with `reason` and `evidence`; record
  nothing, invoke nothing. This is success, not a block.
- `blocked`: surface `reason` to the human and wait (for `ISSUE_LOCKED`, also `owner_session_id`,
  after the self-identity check in RUN_IDENTITY.md). Never guess an alternative skill.
- `error`: log the `error` field and escalate.

```bash
sdlc-tool dispatch record --skill /do-build --issue-number {issue_number} --run-id {run_id}
# add --pr-number {pr_number} for review/patch/docs/merge stages
# inspect history (debug G4; read-only, no --run-id):
sdlc-tool dispatch get --issue-number {issue_number}
```

`dispatch record` is the only correct runtime entry point; never reach past it into the router's
internals.

## Step 5: Supervision Loop (supervisor mode only)

Iteration cap: 15 dispatches (a happy path is 8 stages; G4 catches real oscillation long before
the cap). Router mode returns after Step 4.

### 5a. Ask the router

Call `next-skill` as in Step 4, always with `--run-id` (a read-only identity assertion for its
lock peek, so this run is never told to stand down for its own lock). Then:

- `ISSUE_LOCKED` → apply RUN_IDENTITY.md's self-identity check; only a genuinely foreign owner stops
  the loop.
- `terminal` → exit the loop as success, citing `reason` and `evidence`. Record nothing.
- other `blocked` → stop; report `reason`, `guard_id`, and the stages completed so far.
- `dispatch` → continue to 5b.
- anything else (error key, empty) → stop and surface it.

### 5b. Record the dispatch

`sdlc-tool dispatch record --skill {skill} --issue-number {issue_number} --run-id {run_id}` (plus
`--pr-number {pr}` once a PR exists).

### 5c. Spawn the stage subagent

One writer per artifact: the plan doc in particular is a single file no worktree isolates. While a
child holds an artifact, do not dispatch a second writer onto it or edit it yourself.

Use the Agent tool (general-purpose) with `model:` from the Stage→Model table and
`run_in_background: false`. Prompt:

```
You are executing ONE SDLC stage for issue #{issue_number} in {repo_path}.

Invoke the Skill tool now: skill "{skill-name-without-slash}", args "{issue_number / pr_number / slug as the skill expects}".
The skill is the procedure — follow it exactly. Do not improvise the stage yourself.
Keep working until the stage is done, and only stop early when you cannot go on without the supervisor or before a risky step. When the stage is done and checked, stop and report; do not start work that belongs to a later stage.

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

Carry the `run_id` into every stage prompt, and once BUILD reports a PR number, into every later
prompt and `dispatch record --pr-number`.

### 5d. Backfill stage markers (TEST and PATCH only)

`/do-test` and `/do-patch` do not write their own markers; every other stage self-marks, so never
double-write.

```bash
sdlc-tool stage-marker --stage TEST --status completed --issue-number {issue_number} --run-id {run_id}
# or --status failed, per the subagent's report
```

A non-zero exit means the marker did not land. Route the stderr diagnostic:

- `ISSUE_LOCKED` naming a foreign `owner_run_id` → stop the loop and report.
- `LEASE_ABSENT`, or `ISSUE_LOCKED` naming your own id → the lease lapsed. Run the 5d.6 re-ensure,
  adopt the `run_id` it returns, and retry the marker once.
- Anything else (broker error, timeout) → transient. Retry once; if it persists, report and
  continue. Never abort the pipeline on a transient error.

### 5d.4. REVIEW self-check gate (only when a verdict substrate is declared)

With no declared substrate (the repo's `docs/sdlc/do-pr-review.md` declares none), the posted
GitHub review is the verdict: skip this gate and advance on the stage report plus the review it
cites. Halting a substrate-less repo for a missing recorded verdict is a defect (#2777).

With a substrate, after every `/do-pr-review` dispatch, run the read-only self-check:

```bash
sdlc-tool verdict selfcheck --pr {pr_number} --issue-number {issue_number}
```

`{"ok": true, ...}` → proceed. `{"ok": false, ...}` → **halt the loop**: print `reason` and which of
`verdict_present` / `trailer_matches_head` / `marker_completed` is false. Do not re-dispatch REVIEW,
advance, or loop back to the router; report it as a stop condition in Step 6.

### 5d.5. Tool-availability mismatch guard (issue #2022)

If a stage subagent's final message is a **bare shell command** (`git `, `gh `, `cd `, `pytest`)
instead of the requested report AND it made **zero tool calls**, it was spawned on an agent type
lacking the tools it needed. This is never a completion. Log `TOOL-AVAILABILITY MISMATCH:
stage={skill}`, re-dispatch once on `general-purpose`, and if the same signature repeats, stop and
surface it.

### 5d.6. Between-stage continuity re-ensure

After each stage returns and before 5a:

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Never discard its output. **Adopt the `run_id` it returns** (it may differ) and branch on the
payload, not the exit code; RUN_IDENTITY.md has the full disposition, including the transient class
that must never become a pipeline abort.

### 5e. Check exit conditions

- `/do-merge` reports a merge → verify with `gh pr view {pr} --repo <resolved> --json
  state,mergedAt`. `MERGED` → exit, success.
- `terminal` or `blocked` → already handled in 5a.
- Iteration cap reached → stop and report how far the pipeline got.
- Otherwise → back to 5a, with a one-line progress note (e.g. "CRITIQUE done (READY TO BUILD) →
  dispatching BUILD on opus").

## Step 6: Final Report

On every exit: the **outcome** (merged / pipeline complete with `evidence` / blocked with guard and
reason / cap reached); the **stage trail** (each dispatch with outcome and verdict); **artifacts**
(issue, plan path, PR, merge commit); and **anything needing a human** (unresolved blockers,
skipped acknowledgments, follow-ups).

## Step 7: Release the run lease

Nothing reclaims the run lease when you stop, and a held lease makes the next run on this issue
refuse with a foreign-owner block until its ceiling lapses. After the Final Report, run the
pipeline tool's **`session-release`** subcommand with the issue number and current `run_id` (the
repo context probe declares the invocation) on every exit except a merge: the 5d.4 HALT, a
`blocked` decision, a `terminal` decision (this run may never have written a MERGE marker), and the
iteration cap. The merged exit needs nothing; the MERGE marker write releases the lease. The release
is ownership-checked and best-effort: a wrong or already-released `run_id` is a safe no-op, and its
output never changes your reported outcome.
