---
name: do-sdlc
description: "Supervise a full SDLC pipeline run to merge in a local Claude Code session. Triggered by 'do-sdlc', 'run the full pipeline', 'ship this issue end to end', 'supervise the sdlc'."
context: fork
---

# do-sdlc — Local Pipeline Supervisor

This skill is the **local stand-in for the bridge PM session**. `/sdlc` (in this repo) is a single-stage router by contract: it dispatches ONE sub-skill and returns, expecting a PM session to re-invoke it. In a local Claude Code session there is no PM loop — this skill IS that loop: it re-invokes the router, dispatching each stage to a subagent on the stage-appropriate model (opus/sonnet), until merge, a blocking guard, or the iteration cap.

You are the supervisor, not the worker. You assess, dispatch, and track. The stage subagents do all the work.

**Redundant-context check (issue #2026, WS-F):** if a bridge PM/dev context already owns this issue — a live eng session (e.g. a bridge PM session) — then `/do-sdlc` is redundant: that context IS the supervision loop. Do not run it; drive via `/sdlc` (in this repo, the single-stage router) instead. A live *supervised-run signal* for the issue number is a different case — see the refusal decision table in Step 2: if the signal's `owner_run_id` is one this run already holds, it is this run's own hand-off, not another owner, and must be inherited rather than treated as redundant-context grounds to stand down.

## Repo Context Probe

If `docs/sdlc/do-sdlc.md` exists, read it and honor its declarations; otherwise use the generic defaults described below. The defaults drive the pipeline through `sdlc-tool` (synced to every machine via `~/.local/bin`), `gh`, and `git`.

## Hard Rules

1. **NEVER write code, run tests, or create plans directly** — every stage executes inside a stage subagent that invokes the stage's `/do-*` skill.
2. **NEVER decide dispatch yourself** — `sdlc-tool next-skill` is the only source of dispatch decisions. It encodes all guards (G1–G8) and dispatch rows. Do not second-guess it, reorder stages, or skip it "because the next stage is obvious".
3. **NEVER continue past a `blocked` decision** — surface the reason to the human and stop. Guards block for a reason.
4. **ALWAYS pass `model:` per the Stage→Model table** when spawning a stage subagent. Never rely on the inherited default.
5. **ALWAYS record the dispatch before spawning the subagent** — this preserves the G4 oscillation signal even if the subagent crashes.
6. **ALWAYS dispatch with `run_in_background: false`, never end the turn waiting on a background child.** This skill runs in a forked context (`context: fork`) that gets exactly one turn. The Agent tool defaults to background execution — it returns immediately and notifies later. A fork has no later turn to be notified on, so a background dispatch is unrecoverable: the fork reports "running in the background, I'll continue when it completes" and then never does (issue #1915). Every stage subagent must be spawned with `run_in_background: false` so its result is in hand before the loop advances.
7. **NEVER spawn agent teammates for stage work.** Where Claude Code agent teams are enabled, ignore those affordances: a teammate's idle notification is not a completion signal (teammates go idle mid-task with deliverables unfinished), and an in-process teammate cannot be reliably resumed. Every dispatch is a foreground subagent per Rule 6.

## Worktree & branch ownership

**Slug identity always wins.** Each issue's build fork exclusively owns `.worktrees/{slug}` and `session/{slug}`, where `{slug}` is the lane's identity, recorded once at lane start and read (never re-derived) via `tools/lane_identity.py::resolve_lane_slug` — this is the single source of truth (`worktree_manager.py` + `resolve_branch_for_stage`; see [`docs/features/sdlc-lane-identity.md`](../../../docs/features/sdlc-lane-identity.md) in repos that have it). Do NOT pre-allocate per-supervisor `.worktrees/sdlc-{N}` lanes: nothing reads a lane override, so lane instructions are silently dropped and every issue's builders land in `.worktrees/{slug}` regardless. Converging fork + supervisor onto one branch per plan is deliberate — it structurally collapses duplicate PRs, since GitHub permits only one open PR per head branch. Concurrent builders inside the one slug worktree must write disjoint file sets (do-build's `Parallel: true` convention: no shared-file writes).

## Stage→Model Dispatch Table

Mirrors the engineer persona's table (`config/personas/engineer.md`) — the local equivalent of `valor-session create --model`.

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

## Step 1: Resolve the Issue

Same resolution as `/sdlc` (in this repo) Step 1:

- **Issue reference** (`208`, `issue #208`): `gh issue view {number}`
- **PR reference** (`PR 363`): `gh pr view {number} --json number,title,state,headRefName,reviewDecision,statusCheckRollup,body` and extract the linked issue number from the body (`Closes #N` / `Fixes #N`)
- **Bare feature description** (no number): dispatch a `/do-issue` stage subagent first (sonnet), read the created issue number from its report, then proceed.

Do not proceed without an issue number.

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
# state-mutating `sdlc-tool` call in Step 3 passes it back explicitly via
# `--run-id {run_id}`. The standalone worker threads its own run_id in-process, so the
# real worker-vs-local guard is preserved.
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}"
```

Let stderr through and check the exit code. A discarded diagnostic here is a run
that proceeds under an identity it does not hold.

Read the JSON from the tool result and **record the `run_id`** (`{"session_id": ..., "created": ..., "run_id": "<hex>"}`) — carry it through every iteration of the Step 3 loop. Reuses the existing `sdlc-local-{N}` session on re-runs. The ownership contract:

- **Every state-mutating `sdlc-tool` call** (`dispatch record`, `stage-marker`, `verdict record`, `meta-set`) **MUST pass `--run-id {run_id}` explicitly.** A missing flag is a named non-zero error (`RUN_ID_REQUIRED`) — the call never mints or adopts an identity.

### Three-way refusal decision table

`session-ensure` (and any subsequent re-ensure — see Step 3's between-stage continuity check) can
refuse with exactly three payload shapes. Discriminate them; do not collapse all three to "stop":

| Refusal | Shape | Action |
|---|---|---|
| **Hand-off** | `{"blocked": true, "reason": "SUPERVISED_RUN_ACTIVE", "run_id", "owner_run_id", "owner_session_id"}` | This is a **designed hand-off**, not a block. It mints nothing — the supervisor already owns the run. **Pass the self-identity check below first.** If it confirms your own signal: **inherit** `owner_run_id` (carry it forward as `run_id`, pass it back via `--run-id`/`--reuse-run-id`), and **continue** — never stop for a confirmed-own signal. |
| **Orphaned lock** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": true}` | The prior owner died before renewing; the lock frees within its TTL (duration and renewal semantics are #2446's — cross-reference it, do not reimplement). Wait, re-ensure, then **rebind `run_id` to whatever the re-ensure returns** before continuing — a post-TTL fresh contest mints a NEW run_id, and every downstream `--run-id` call must use the rebound value or it silently orphans. |
| **Foreign holder** | `{"blocked": true, "reason": "ISSUE_LOCKED", "owner_run_id", "owner_session_id", "orphaned_lock": false}` | The **only unconditional stop condition of the three.** A genuine live foreign run owns the issue. Stop and report. |

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

## Step 3: Supervision Loop

Repeat the following cycle. **Iteration cap: 15 dispatches** (a happy path is 8 stages; the cap is a backstop above realistic patch/re-review cycles — G4 catches genuine oscillation long before it).

### 3a. Ask the router

```bash
sdlc-tool next-skill --issue-number {issue_number}
```

(Read-only — `next-skill` takes no `--run-id`.)

Interpret the JSON from the tool result:

- `{"blocked": true, ...}` → **STOP the loop.** Report the `reason` and `guard_id` to the human, plus a summary of stages completed so far. Do not retry, do not guess an alternative skill.
- `{"skill": "...", "dispatched": true, ...}` → continue to 3b. The router dispatches at most ONE skill per call; there is no parallel-pair shape.
- Anything else (error key, empty) → STOP and surface the error.

### 3b. Record the dispatch

```bash
sdlc-tool dispatch record --skill {skill} --issue-number {issue_number} --run-id {run_id}
# include --pr-number {pr} once a PR exists (review/patch/docs/merge stages)
```

### 3c. Spawn the stage subagent

**One writer per artifact — enumerate live children before you dispatch.** Every stage you dispatch is a writer on a shared artifact, and the plan doc in particular is a single file on the shared main checkout that no worktree isolates. Before spawning, account for the children you already have out: if a child is still holding the plan doc, do not dispatch a second one onto it, and do not edit it yourself while it does. Wait for the outstanding child to report, then dispatch.

This is not hypothetical. On 2026-08-07 one plan doc took two concurrent revision children twice over (#2650, shape 3) — a writer watched its line count grow from 62 to 111 between its own reads and had to stop and ask who owned the file — and a supervisor patched a plan task while its own dispatched child held the doc, then had to warn the child mid-flight to re-read before committing (shape 4). Both were recovered only because an agent happened to notice the file moving underneath it.

Since Hard Rule 6 already requires `run_in_background: false`, the ordinary loop has exactly one child out at a time and satisfies this for free. The rule bites when you are tempted to fan out, or to "just fix one line" in a doc a child is revising. Neither is safe; the second is the one that feels harmless.

Use the Agent tool (general-purpose), with `model:` from the Stage→Model table and **`run_in_background: false`** (Hard Rule 6 — this fork cannot be resumed by a background notification). Prompt template:

```
You are executing ONE SDLC stage for issue #{issue_number} in {repo_path}.

Invoke the Skill tool now: skill "{skill-name-without-slash}", args "{issue_number / pr_number / slug as the skill expects}".
The skill is the procedure — follow it exactly. Do not improvise the stage yourself.

Context:
- Issue: #{issue_number} — {title}
- PR: {#pr or "none yet"}
- Plan: {docs/plans/{slug}.md or "none yet"}
- Prior stage outcome: {one-line summary, or "None — first stage"}
- Run identity: {run_id} — pass --run-id {run_id} on every state-mutating sdlc-tool call (stage-marker, verdict record, meta-set, dispatch record); read-only calls take none.
- Commit early: commit to session/{slug} as work lands (small logical checkpoints), not only at the end — a preempt or lease lapse mid-stage must never lose work.

When done, report back (this is data for the supervisor, not prose for a human):
- outcome: success | failure
- verdict: any verdict string the skill emitted (READY TO BUILD / NEEDS REVISION / APPROVED / CHANGES REQUESTED / ...)
- artifacts: plan path, PR number, branch name — whatever was created or changed
- failures: test failures, blockers, or errors verbatim if any
```

Carry forward context between iterations: the `run_id` goes into every stage prompt, and once BUILD reports a PR number, include it in every subsequent prompt and in `dispatch record --pr-number`.

### 3d. Backfill stage markers (TEST and PATCH only)

`/do-test` and `/do-patch` do not write their own stage markers — on the bridge, the worker's dev-completion handler does it. Locally, the supervisor must:

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
  your lease lapsed. Run the Step 3d.6 re-ensure, **adopt the `run_id` it
  returns**, and retry the marker once under the adopted id.
- Anything else (a Redis/broker error, a timeout) → transient. Retry once, and
  only report-and-continue if it persists. A transient error is never an
  unconditional pipeline abort.

### 3d.4. REVIEW self-check gate (issue #2193)

If the stage just dispatched in 3c was `/do-pr-review`, do not treat its
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
  completion marker are all confirmed present. Proceed to 3e / loop back to
  3a as normal.
- `{"ok": false, ...}` → **HALT the loop immediately.** Print the machine-readable
  `reason` field loudly to the operator (e.g. "REVIEW SELF-CHECK FAILED:
  reason={reason} — pr={pr_number} issue={issue_number}"), along with which of
  `verdict_present` / `trailer_matches_head` / `marker_completed` is false.
  Do NOT re-dispatch REVIEW yourself, do NOT advance to DOCS/MERGE, and do NOT
  silently loop back to the router — this is a single loud refusal, replacing
  the old failure mode where the router would silently re-dispatch REVIEW
  forever on the same missing state. Report this as a stop condition in Step 4
  (Final Report) just like a `blocked` router decision.

This gate is prose/logic in this skill body only — it does not modify
`agent/sdlc_router.py`'s dispatch rows (those already fail-closed and
re-dispatch on missing state; see the router's rows 8/8b/9). It exists
specifically to make the supervised `/do-sdlc` path *loud* on the exact
failure the router would otherwise handle silently over multiple iterations.

### 3d.5. Tool-availability mismatch guard (issue #2022)

Inspect every stage subagent's final report before acting on it. If the final message is (or begins with) a **bare shell command** — it starts with `git `, `gh `, `cd `, `pytest`, `python `, or otherwise reads as a command line rather than the outcome/verdict/artifacts report the prompt template asks for — AND the child made **zero tool calls**, the child was spawned on an agent type without the tools its first step needed: it emitted the command it could not run as plain text. Treat this as a **tool-availability mismatch, never a normal completion**:

1. Log it: "TOOL-AVAILABILITY MISMATCH: stage={skill}, final message is a bare shell command with zero tool calls"
2. Re-dispatch the same stage once on a Bash-capable agent type (`general-purpose`)
3. If the re-dispatch shows the same signature, stop and surface the mismatch to the human — do not loop

### 3d.6. Between-stage continuity re-ensure (issue #2452)

After the stage subagent returns (3c/3d/3d.4/3d.5) and before asking the router again (3a), re-ensure
this run's identity:

```bash
sdlc-tool session-ensure --issue-number {issue_number} --reuse-run-id {run_id}
```

Do **not** append a stderr redirect to `/dev/null`, a trailing `|| true`, or any other form that
discards the diagnostic or the exit code.
The whole point of this step is the payload; a form that destroys it makes every instruction below
unfollowable and lets the run continue under an identity it has silently lost.

This is a **continuity proof** — the tool verifies the held `run_id` against the live lock/session
record and against the durable issue-keyed run-identity anchor on the ledger — not a lease
keepalive; it does not renew or extend the TTL (that is the heartbeat's job, issue #2714).

**Adopt the returned `run_id`.** On exit code 0, read `run_id` out of the JSON payload and use
**that** value as `{run_id}` for every subsequent stage, prompt and `--run-id` flag in this run. It
may differ from the one you carried in: a lapsed lease is rebound, and a fresh contest mints a new
identity. Keeping your own stale copy is precisely how a run ends up writing markers nobody accepts.

**On a non-zero exit**, the diagnostic is on stderr. Route the payload through the **same** three-way
table and `owner_run_id` self-identity check from Step 2, so the own-ghost abandonment bug does not
simply relocate from the top of the loop to this stage seam:

- **Foreign owner** — `ISSUE_LOCKED` whose `owner_run_id` is neither yours nor a self-identity you
  recognize: this run has genuinely lost the issue. **Stop the loop** and report. This is the one
  stop condition.
- **Self / hand-off** — `SUPERVISED_RUN_ACTIVE`, or an `owner_run_id` the self-identity check
  confirms is your own: **inherit** `owner_run_id` as `{run_id}` and continue.
- **Orphaned lock** — `orphaned_lock: true`: wait out the TTL, re-ensure, adopt what comes back.
- **Transient** — a Redis/broker error, a timeout, or any payload that is none of the above:
  **surface it and retry**, then continue. Never convert a transient error into a pipeline abort —
  halting a healthy run on a broker blip is worse than the bug this step exists to catch.

### 3e. Check exit conditions

- Dispatched skill was `/do-merge` AND the subagent reports a merge → verify with `gh pr view {pr} --json state,mergedAt` from the tool result. If `MERGED`: **exit the loop, success.**
- Router returned `blocked` → already stopped in 3a.
- Iteration cap reached → stop and report how far the pipeline got.
- Otherwise → loop back to 3a. Brief one-line progress note per iteration (e.g. "CRITIQUE done (READY TO BUILD) → dispatching BUILD on sonnet").

## Step 4: Final Report

On exit (any path), report:

1. **Outcome**: merged / blocked (with guard + reason) / cap reached
2. **Stage trail**: each dispatch in order with its outcome and verdict
3. **Artifacts**: issue, plan path, PR number, merge commit
4. **Anything needing human attention**: unresolved blockers, skipped acknowledgments, follow-ups

## Step 5: Release the run lease

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

- the **3d.4 REVIEW self-check HALT**
- a **`blocked`** router decision (3a / 3e)
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

Both consume the same router (`sdlc-tool next-skill` → `agent.sdlc_router.decide_next_dispatch()`) and the same stored stage state — there is exactly one source of dispatch truth.
