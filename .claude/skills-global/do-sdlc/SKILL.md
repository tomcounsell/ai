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
2. **NEVER decide dispatch yourself** — `sdlc-tool next-skill` is the only source of dispatch decisions. It encodes all guards (G1–G7) and dispatch rows. Do not second-guess it, reorder stages, or skip it "because the next stage is obvious".
3. **NEVER continue past a `blocked` decision** — surface the reason to the human and stop. Guards block for a reason.
4. **ALWAYS pass `model:` per the Stage→Model table** when spawning a stage subagent. Never rely on the inherited default.
5. **ALWAYS record the dispatch before spawning the subagent** — this preserves the G4 oscillation signal even if the subagent crashes.

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
sdlc-tool session-ensure --issue-number {issue_number} --issue-url "https://github.com/$SDLC_REPO/issues/{issue_number}" 2>/dev/null || true
```

Idempotent — reuses the existing `sdlc-local-{N}` session on re-runs.

## Step 3: Supervision Loop

Repeat the following cycle. **Iteration cap: 15 dispatches** (a happy path is 8 stages; the cap is a backstop above realistic patch/re-review cycles — G4 catches genuine oscillation long before it).

### 3a. Ask the router

```bash
sdlc-tool next-skill --issue-number {issue_number}
```

Interpret the JSON from the tool result:

- `{"blocked": true, ...}` → **STOP the loop.** Report the `reason` and `guard_id` to the human, plus a summary of stages completed so far. Do not retry, do not guess an alternative skill.
- `{"skill": "...", "dispatched": true, ...}` → single dispatch; continue to 3b.
- `{"multi": true, "dispatches": [...], ...}` → parallel-safe pair (e.g. DOCS + PATCH); continue to 3b, spawning BOTH subagents in one message so they run concurrently, each on its own stage's model.
- Anything else (error key, empty) → STOP and surface the error.

### 3b. Record the dispatch

```bash
sdlc-tool dispatch record --skill {skill} --issue-number {issue_number}
# include --pr-number {pr} once a PR exists (review/patch/docs/merge stages)
```

For a multi-dispatch, record only the FIRST skill in the list (the pair is guard-gated as one decision).

### 3c. Spawn the stage subagent

Use the Agent tool (general-purpose), with `model:` from the Stage→Model table. Prompt template:

```
You are executing ONE SDLC stage for issue #{issue_number} in {repo_path}.

Invoke the Skill tool now: skill "{skill-name-without-slash}", args "{issue_number / pr_number / slug as the skill expects}".
The skill is the procedure — follow it exactly. Do not improvise the stage yourself.

Context:
- Issue: #{issue_number} — {title}
- PR: {#pr or "none yet"}
- Plan: {docs/plans/{slug}.md or "none yet"}
- Prior stage outcome: {one-line summary, or "None — first stage"}

When done, report back (this is data for the supervisor, not prose for a human):
- outcome: success | failure
- verdict: any verdict string the skill emitted (READY TO BUILD / NEEDS REVISION / APPROVED / CHANGES REQUESTED / ...)
- artifacts: plan path, PR number, branch name — whatever was created or changed
- failures: test failures, blockers, or errors verbatim if any
```

Carry forward context between iterations: once BUILD reports a PR number, include it in every subsequent prompt and in `dispatch record --pr-number`.

### 3d. Backfill stage markers (TEST and PATCH only)

`/do-test` and `/do-patch` do not write their own stage markers — on the bridge, the worker's dev-completion handler does it. Locally, the supervisor must:

```bash
sdlc-tool stage-marker --stage TEST --status completed --issue-number {issue_number} 2>/dev/null || true
# or --status failed, per the subagent's report
```

All other stage skills self-mark; do NOT double-write markers for them.

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
