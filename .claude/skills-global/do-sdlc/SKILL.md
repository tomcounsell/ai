---
name: do-sdlc
description: "Supervise a full SDLC pipeline run to merge in a local Claude Code session. Triggered by 'do-sdlc', 'run the full pipeline', 'ship this issue end to end', 'supervise the sdlc'."
context: fork
---

# do-sdlc — Local Pipeline Supervisor

This skill is the **local stand-in for the bridge PM session**. `/sdlc` is a single-stage router by contract: it dispatches ONE sub-skill and returns, expecting a PM session to re-invoke it. In a local Claude Code session there is no PM loop — this skill IS that loop: it re-invokes the router, dispatching each stage to a subagent on the stage-appropriate model (opus/sonnet), until merge, a blocking guard, or the iteration cap.

You are the supervisor, not the worker. You assess, dispatch, and track. The stage subagents do all the work.

## Repo Context Probe

If `docs/sdlc/do-sdlc.md` exists, read it and honor its declarations; otherwise use the generic defaults described below. The defaults drive the pipeline through `sdlc-tool` (synced to every machine via `~/.local/bin`), `gh`, and `git`.

## Hard Rules

1. **NEVER write code, run tests, or create plans directly** — every stage executes inside a stage subagent that invokes the stage's `/do-*` skill.
2. **NEVER decide dispatch yourself** — `sdlc-tool next-skill` is the only source of dispatch decisions. It encodes all guards (G1–G8) and dispatch rows. Do not second-guess it, reorder stages, or skip it "because the next stage is obvious".
3. **NEVER continue past a `blocked` decision** — surface the reason to the human and stop. Guards block for a reason.
4. **ALWAYS pass `model:` per the Stage→Model table** when spawning a stage subagent. Never rely on the inherited default.
5. **ALWAYS record the dispatch before spawning the subagent** — this preserves the G4 oscillation signal even if the subagent crashes.
6. **ALWAYS dispatch with `run_in_background: false`, never end the turn waiting on a background child.** This skill runs in a forked context (`context: fork`) that gets exactly one turn. The Agent tool defaults to background execution — it returns immediately and notifies later. A fork has no later turn to be notified on, so a background dispatch is unrecoverable: the fork reports "running in the background, I'll continue when it completes" and then never does (issue #1915). Every stage subagent, including both halves of a `multi` dispatch, must be spawned with `run_in_background: false` so its result is in hand before the loop advances.
7. **NEVER spawn agent teammates for stage work.** Where Claude Code agent teams are enabled, ignore those affordances: a teammate's idle notification is not a completion signal (teammates go idle mid-task with deliverables unfinished), and an in-process teammate cannot be reliably resumed. Every dispatch is a foreground subagent per Rule 6.

## Worktree & branch ownership

**Slug identity always wins.** Each issue's build fork exclusively owns `.worktrees/{slug}` and `session/{slug}`, derived from the plan slug — this is the single source of truth (`worktree_manager.py` + `resolve_branch_for_stage`). Do NOT pre-allocate per-supervisor `.worktrees/sdlc-{N}` lanes: nothing reads a lane override, so lane instructions are silently dropped and every issue's builders land in `.worktrees/{slug}` regardless. Converging fork + supervisor onto one branch per plan is deliberate — it structurally collapses duplicate PRs, since GitHub permits only one open PR per head branch. Concurrent builders inside the one slug worktree must write disjoint file sets (do-build's `Parallel: true` convention: no shared-file writes).

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

Same resolution as `/sdlc` Step 1:

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
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}" 2>/dev/null || true
```

Read the JSON from the tool result and **record the `run_id`** (`{"session_id": ..., "created": ..., "run_id": "<hex>"}`) — carry it through every iteration of the Step 3 loop. Reuses the existing `sdlc-local-{N}` session on re-runs. The ownership contract:

- **Every state-mutating `sdlc-tool` call** (`dispatch record`, `stage-marker`, `verdict record`, `meta-set`) **MUST pass `--run-id {run_id}` explicitly.** A missing flag is a named non-zero error (`RUN_ID_REQUIRED`) — the call never mints or adopts an identity.
- A foreign run_id (another live run owns the issue lock) yields `ISSUE_LOCKED` with the owning `run_id`/`session_id` — treat it like a router block: stop and report.
- **Recovery after run_id loss** (context compaction, restarted supervisor): re-run the `session-ensure` above. While the old lock is live it returns `ISSUE_LOCKED` (bounded by the ≤300s lock TTL, since nothing renews the orphaned run's lock); after the TTL lapses a fresh contest mints a new run_id. **If you still have the run_id**, add `--reuse-run-id {run_id}` to recover immediately under the same identity — the tool verifies the claim against the live lock (or, on a free lock, the session record) and never adopts an unverified one.

## Step 3: Supervision Loop

Repeat the following cycle. **Iteration cap: 15 dispatches** (a happy path is 8 stages; the cap is a backstop above realistic patch/re-review cycles — G4 catches genuine oscillation long before it).

### 3a. Ask the router

```bash
sdlc-tool next-skill --issue-number {issue_number}
```

(Read-only — `next-skill` takes no `--run-id`.)

Interpret the JSON from the tool result:

- `{"blocked": true, ...}` → **STOP the loop.** Report the `reason` and `guard_id` to the human, plus a summary of stages completed so far. Do not retry, do not guess an alternative skill.
- `{"skill": "...", "dispatched": true, ...}` → single dispatch; continue to 3b.
- `{"multi": true, "dispatches": [...], ...}` → parallel-safe pair (e.g. DOCS + PATCH); continue to 3b, spawning BOTH subagents in one message so they run concurrently, each on its own stage's model.
- Anything else (error key, empty) → STOP and surface the error.

### 3b. Record the dispatch

```bash
sdlc-tool dispatch record --skill {skill} --issue-number {issue_number} --run-id {run_id}
# include --pr-number {pr} once a PR exists (review/patch/docs/merge stages)
```

For a multi-dispatch, record only the FIRST skill in the list (the pair is guard-gated as one decision).

### 3c. Spawn the stage subagent

Use the Agent tool (general-purpose), with `model:` from the Stage→Model table and **`run_in_background: false`** (Hard Rule 6 — this fork cannot be resumed by a background notification). For a `multi` dispatch, both calls go in the same message with `run_in_background: false` each; the harness runs them concurrently and blocks for both results before your next turn. Prompt template:

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
sdlc-tool stage-marker --stage TEST --status completed --issue-number {issue_number} --run-id {run_id} 2>/dev/null || true
# or --status failed, per the subagent's report
```

All other stage skills self-mark; do NOT double-write markers for them.

### 3d.5. Tool-availability mismatch guard (issue #2022)

Inspect every stage subagent's final report before acting on it. If the final message is (or begins with) a **bare shell command** — it starts with `git `, `gh `, `cd `, `pytest`, `python `, or otherwise reads as a command line rather than the outcome/verdict/artifacts report the prompt template asks for — AND the child made **zero tool calls**, the child was spawned on an agent type without the tools its first step needed: it emitted the command it could not run as plain text. Treat this as a **tool-availability mismatch, never a normal completion**:

1. Log it: "TOOL-AVAILABILITY MISMATCH: stage={skill}, final message is a bare shell command with zero tool calls"
2. Re-dispatch the same stage once on a Bash-capable agent type (`general-purpose`)
3. If the re-dispatch shows the same signature, stop and surface the mismatch to the human — do not loop

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

## Relationship to /sdlc

| | `/sdlc` | `/do-sdlc` |
|---|---|---|
| Contract | dispatch ONE stage, return | loop until merge/blocked |
| Progression | PM session re-invokes | this skill re-invokes the router |
| Model assignment | PM passes `--model` when spawning dev sessions | supervisor passes `model:` on the Agent tool |
| Where it runs | bridge PM sessions + local | local Claude Code sessions |

Both consume the same router (`sdlc-tool next-skill` → `agent.sdlc_router.decide_next_dispatch()`) and the same stored stage state — there is exactly one source of dispatch truth.
