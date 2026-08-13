---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-11
tracking: https://github.com/tomcounsell/ai/issues/2650
last_comment_id: 5251329845
---

# Plan-Doc Single-Writer Lease

## Problem

Two agents can hold the same `docs/plans/{slug}.md` at the same time, and neither finds out except by luck. On 2026-08-07 a plan writer watched its own document grow from 62 to 111 lines between two of its own reads and had to stop and ask a human who owned the file. Later the same day, a lane supervisor patched Task 1 of a plan while its own dispatched revision child was rewriting that plan, then had to shout a warning at the child mid-flight. A third writer's revision (`5945c0c9c`) silently reverted regions a concurrent writer had just authored; the damage was repaired region-by-region in `28e2e2055`. A fourth committed a plan revision onto `session/retire-dead-intake-guard-tests` — a different lane's branch — because the shared checkout's HEAD had been switched underneath it, and the content had to be independently restored to main in `8afe2df22`.

Code work is isolated per lane in `.worktrees/{slug}/`. Plan docs, by owner policy, are not: they commit directly on `main` in the shared checkout, which every concurrent lane shares. That policy is not up for renegotiation here — the point of this work is to make it safe.

**Current behavior:** nothing checks who owns a plan doc before writing it. `plan_revising` looks like a busy signal but is a Redis meta flag consumed only by the router's G7 guard to decide whether to route to build; no write path reads it. Every collision is detected by an agent noticing its file moving, and resolved by escalating to a human-adjacent supervisor.

**Desired outcome:** a second writer on a plan doc is refused at the tool layer, before the write lands. A plan commit attempted while HEAD is not `main` fails loudly instead of stranding content on a foreign branch.

## Scope Decision (2026-08-11)

The owner cut this plan to a minimal set. Two guards ship; everything else was dropped or split out.

**Ships:** the plan-doc write lease, and the off-main plan-commit guard. Between them they close the two shapes that actually destroyed or stranded content.

**Split out to #2731:** the stage-dispatch liveness gate. It needs an additive schema change to dispatch records, a `--force` path, and a test against #2675's run_id re-minting — real work, and not what makes plan docs safe. It is also the only remaining shape the lease does not cover, so it is filed with its evidence rather than dropped.

**Dropped outright** (see Rabbit Holes): the stage-boundary dirty-`docs/plans/` warning, `plan-lease steal`, `--reason` audit strings on lease operations, and a written loser-side stand-down protocol. Each was a guardrail around a guardrail.

**Fail-open is settled.** A Redis outage means this protection is simply absent for the duration. The owner's ruling: a Redis outage is too rare to design around. Task 4 makes `/update` say so out loud when Redis is down, which is the whole of the compensating control.

## Freshness Check

**Baseline commit:** `dbf761ed4`
**Issue filed at:** 2026-08-07T07:30:33Z (rescoped and retitled 2026-08-11)
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/sdlc_stage_query.py:526` — `plan_revising` read from `_plan_revising` — holds.
- `models/session_lifecycle.py:1071` — `touch_issue_lock`, plain-Redis run_id-keyed lock whose payload already carries `pid`/`create_time`/`machine_id` — holds; this is the template for the plan-doc lease.
- `agent/pid_fence.py:1` — `(pid, create_time)` fence with the canonical unknown-means-unknown rule — holds.
- `.claude/hooks/manifest.toml` — `validate_design_system_readonly` registered on `matcher = "Write|Edit"` — holds; the precedent for a plan-doc write hook.
- `.claude/hooks/validators/validate_no_destructive_git_in_shared_checkout.py` — shared-checkout resolution, `.worktrees/` exclusion, command-position anchoring, inline override token — holds; the direct model for Task 3.
- `scripts/update/run.py:1332` — a Redis-down `apply_redis_persistence` result is logged **without** `always=True`, i.e. quietly — holds, and is what Task 4 fixes.
- Commit `c6e48a514` (the stranded plan commit) — **gone**; the session branch was deleted. Its repair `8afe2df22` is present, so the event is corroborated by its fix rather than its cause.
- `agent/sdlc_router.py:1720` — `record_dispatch` carries no liveness — holds, and is now #2731's problem.

**Cited sibling issues/PRs re-checked:**
- #2669 — MERGED 2026-08-10. Shipped the FETCH_HEAD and shared-`refs/stash` half, and stated the two coordination norms as prose. Out of scope here.
- #2731 — filed 2026-08-11 from this plan's cut. Owns the dispatch liveness gate.
- #2657 — OPEN. Resume-from-transcript supervisor forking, the root cause behind the supervisor-vs-child shape. Not absorbed.
- #2628 — OPEN. Owns the cross-lane pytest Redis DB collision.
- #2629 — CLOSED. Supplied the BUILD double-dispatch evidence, now carried by #2731.

**Commits on main since issue was filed (touching referenced files):**
- `7773d8d97` (#2669) — **partially addresses**: closes the git-metadata half, leaves everything here.
- `8a9137002` "Judge only the file the Write named, and stop firing on quoted critique text" — **relevant**: a `Write`-matched hook precision fix. The new hook must judge the path the tool named, never content.
- `337cc1f31`, `76a23e15a` — irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** the original issue asserted a fail-open `stage-marker` lease gate "being filed separately." No such issue exists and `tools/sdlc_stage_marker.py:510-522` refuses before writing. Dropped.

## Prior Art

- **PR #2669** (merged 2026-08-10): fixed the shared-`FETCH_HEAD` and `refs/stash` halves and wrote the commit-promptness and live-child-enumeration norms into the skill bodies as prose. Succeeded at what it scoped; explicitly did not attempt tool-layer enforcement.
- **Issue #2448 / `validate_no_destructive_git_in_shared_checkout.py`**: blocks whole-tree destructive git in the shared checkout, motivated by exactly this hazard — an agent clobbering peers' uncommitted `docs/plans/*.md`. Succeeded, and is the structural precedent for Task 3: same dispatcher, same shared-checkout resolution, same inline-override convention.
- **Issue #2026 / PR #2076**: SDLC fork-vs-supervisor hardening; run_id-keyed issue lease. Succeeded at the routing layer, which is why `touch_issue_lock` exists in its current form. Never reached the file-write layer.
- **Issue #2064 / PR #2107**: serialized full-suite pytest across worktrees with a lock. Precedent that a cross-worktree lock is an accepted shape here.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2669 | Stated commit-promptness and live-child enumeration as prose in skill bodies | Prose is advice to a model, not a gate. It cannot bind a second agent that never read the first one's intent, nor a forked supervisor that believes it is the sole owner. |
| PR #2076 (#2026) | Run-id-keyed issue lease, verdict-gated routing | Guards *routing decisions*, not *file writes*. A supervisor and its own child share a run_id, pass every lease check, and then collide on the file. |
| `plan_revising` meta flag | Busy signal set by critique | Never read by a write path, and keyed by issue — so even if checked, it could not tell a supervisor from its own child. |

**Root cause pattern:** every prior fix guarded a *decision* keyed by *lane* or *run*. The collisions happen at a *write*, between agents that share a lane and a run. Nothing in the system has ever had a notion of "which agent holds this file right now."

## Data Flow

1. **Entry point**: an agent calls `Write` or `Edit` with `file_path` under `docs/plans/`.
2. **PreToolUse hook**: the harness invokes the `Write|Edit`-matched hook with JSON on stdin carrying `session_id`, `transcript_path`, `cwd`, `tool_input`.
3. **Identity resolution**: holder id derives from `transcript_path` — a subagent's transcript is a distinct file (`<session>/subagents/agent-*.jsonl`) while `session_id` is shared with the parent (spike-1). This is what makes the supervisor-vs-own-child case decidable.
4. **Lease check** (`models/plan_doc_lease.py`): `SET NX EX` on `sdlc:plandoc:{slug}` claims a free doc; a same-holder hit renews; a different-holder hit is either claimed (holder provably dead) or refused.
5. **Decision**: allow, or `{"decision": "block", "reason": ...}` naming the current holder.
6. **Release**: `sdlc-tool plan-lease release --slug` at the end of a revision pass, or TTL expiry, or displacement by the next writer once the holder is provably dead.

## Architectural Impact

- **New dependencies**: none. `psutil` and Redis are already in play.
- **Interface changes**: `sdlc-tool` gains a `plan-lease` subcommand. Nothing existing changes signature.
- **Coupling**: the hook layer now reads Redis. Already true of `pre_tool_use.py` (it queries `AgentSession`), so the import-cost profile is known.
- **Data ownership**: introduces an owner-of-record for "who may write this plan doc," which nothing previously owned.
- **Reversibility**: high. Delete the manifest entry and the guard is gone; lease keys are TTL'd and self-evaporate.

## Appetite

**Size:** Small

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (only if spike-1's premise fails — see Risk 2)
- Review rounds: 1-2 (concurrency code in a hook path; the last three PRs in this area each found a real bug in review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as R; R.ping()"` | Lease substrate |
| `psutil` importable | `python -c "import psutil"` | pid fence liveness |

## Spike Results

### spike-1: Is a subagent's write distinguishable from its parent supervisor's at hook time?
- **Assumption**: "The PreToolUse hook payload contains something that differs between a supervisor and its own dispatched child."
- **Method**: code-read + transcript inspection
- **Finding**: `session_id` is **shared** — a subagent transcript (`~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl`) carries the parent's `sessionId` on every entry. But subagents get their own transcript **file** and their own `agentId` field. So `transcript_path`, not `session_id`, is the agent-distinguishing handle.
- **Confidence**: medium — confirmed the transcript file is per-agent and `sessionId` is not; **not** confirmed that Claude Code passes the *subagent's* transcript path in the hook payload for a tool call made inside a subagent. Task 1 asserts this against a live payload before anything is built on it.
- **Impact on plan**: holder id = `sha256(transcript_path)[:16]`, transcript basename retained as a readable `agent_label`. If Task 1's assertion fails, see Risk 2 — ship the lane-level lease and say plainly which criterion is unmet.

### spike-2: Can the dispatch double-entry be fixed without a schema change?
- **Assumption**: "`same_stage_dispatch_count` can be turned into a concurrency refusal as-is."
- **Method**: code-read (`agent/sdlc_router.py:1679-1731`)
- **Finding**: No. The record is exactly `{skill, at, stage_snapshot}` — no pid, no holder. `compute_same_stage_count` compares *snapshots*, which is an oscillation signal by construction.
- **Confidence**: high
- **Impact on plan**: this is why the dispatch gate is out of scope and filed as #2731 rather than folded in as "one more check."

## Solution

### Key Elements

- **`models/plan_doc_lease.py`** — an agent-keyed, TTL'd, pid-fenced lease over one plan doc. Structural twin of `touch_issue_lock`, keyed by slug and holder instead of issue and run.
- **`validate_plan_doc_lease.py`** — a `Write|Edit` PreToolUse hook that resolves the holder, takes or renews the lease, and blocks a foreign live holder's write with a reason naming who holds it.
- **`sdlc-tool plan-lease {status,release}`** — two verbs. Enough to hand a doc back deliberately and to see who holds one.
- **Plan-commit branch guard** — a tenth predicate in the existing Bash dispatcher: no `docs/plans/` commit from the shared checkout while HEAD is not `main`.
- **A loud `/update` line when Redis is down** — the compensating control for fail-open.

### Flow

Agent edits plan doc → hook resolves holder from transcript_path → lease free or same holder → **write proceeds** (lease renewed)

Agent edits plan doc → lease held by a live foreign holder → **blocked**, reason names the holder and `sdlc-tool plan-lease status --slug`

Agent edits plan doc → lease held by a provably-dead holder → **claimed**, write proceeds, stderr notes the displaced holder

Agent runs `git commit` touching `docs/plans/` in the shared checkout with HEAD ≠ main → **blocked**, reason names the current branch

### Technical Approach

- **Lease store**: plain Redis key `sdlc:plandoc:{slug}` (not Popoto-managed — the same call `touch_issue_lock` makes and documents), JSON payload `{holder_id, agent_label, session_id, pid, pid_create_time, machine_id, acquired_at, renewed_at}`. One entry point, `touch_plan_doc_lease(slug, holder_id, ..., peek=False)`, returning `PlanDocLeaseResult(acquired, owner_holder_id, owner_agent_label, orphaned)`. Three behaviors in one function, exactly as `touch_issue_lock` does it — do not grow a second entry point.
- **Staleness, two independent clocks**: `PLAN_DOC_LEASE_TTL_SECONDS` (default 900 — provisional/tunable, env-overridable, a named constant per the repo's magic-number rule) and the `agent/pid_fence.py` fence. A lease whose holder pid is **provably dead** is claimable immediately, before TTL. A lease whose fence is **unknown** (no recorded create_time, `AccessDenied`, psutil missing) is NOT claimable early — it waits for TTL. That is `pid_fence`'s canonical rule: unknown authorizes no escalation, and stealing a live agent's file is an escalation. These two clocks are what makes a `steal` verb unnecessary.
- **Holder identity**: `sha256(transcript_path)[:16]`, falling back to `sha256(session_id)[:16]`. Both agents in the supervisor-vs-child case have a `transcript_path`; only the values differ. `agent_label` (the transcript basename) makes the block reason readable.
- **Slug derivation**: from the `file_path` in `tool_input` — basename minus `.md`. The hook judges the path the tool named, never file content (`8a9137002` is the cautionary precedent).
- **Hook scope**: fires only when the resolved path is under `docs/plans/` and ends in `.md`. Everything else returns before importing the lease module, so the common Write/Edit path pays a path comparison and nothing else.
- **Fail posture**: fail OPEN on any Redis or import error, logged via `log_hook_error`. A Redis hiccup degrades to today's behavior, never to "nobody can write a plan." Settled by the owner; see Risk 1.
- **Manifest registration**: one new `[[hook]]` entry, `event = "PreToolUse"`, `matcher = "Write|Edit"`, `exit_policy = "deny-only"` (the hook owns its fail-open internally; its exit 2 is a real decision to block), `scope = "project"`. Regenerate `settings.json` from the manifest — never hand-edit it.
- **Commit guard**: `is_plan_commit_off_main(command, cwd)` in `hook_utils/`, wired as predicate 10 in `dispatch/pre_tool_use_bash.py`, fail-open like its neighbors. Fires only when cwd resolves to this repo's shared toplevel (not under `.worktrees/`), the command is a `git commit` in command position, `git rev-parse --abbrev-ref HEAD` is not `main`, and the committed set intersects `docs/plans/`. Inline override token `# allow-plan-commit-off-main`.
- **`/update` Redis liveness**: `scripts/update/run.py:1332` currently logs a Redis-down `apply_redis_persistence` skip quietly. Make that path `always=True` and append to `result.warnings`, so a down Redis is stated at every update rather than buried at verbose level.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The hook's outer `except Exception` must not be bare-`pass`: it logs via `log_hook_error` and allows. Tested with a Redis stub that raises.
- [ ] `touch_plan_doc_lease`'s Redis fail-open logs the swallowed error class explicitly, matching `touch_issue_lock`. Test asserts the class name reaches the log record.
- [ ] The commit-guard predicate fails open on any exception, like its nine neighbors in the dispatcher.

### Empty/Invalid Input Handling
- [ ] Hook payload with no `transcript_path` → falls back to `session_id`; with neither → allow, logged.
- [ ] `tool_input` with no `file_path`, an empty string, or a directory path → allow, no Redis call.
- [ ] A path under `docs/plans/` that is not `.md`, or whose slug is empty after stripping → allow, no Redis call.
- [ ] Malformed (non-JSON) lease payload in Redis → treated as a foreign non-matching holder, exactly as `touch_issue_lock` treats a legacy value. Never raises.
- [ ] `git commit` with no staged files, and in a non-repo cwd → allow.

### Error State Rendering
- [ ] The block reason is the agent's only channel here: it must name the slug, the holding `agent_label`, the holder's age, and the exact `sdlc-tool plan-lease status --slug <slug>` command. A reason that says only "blocked" is a failed test.
- [ ] The commit-guard reason names the current branch and the override token.

## Test Impact

- [ ] `tests/unit/test_hook_manifest.py` — UPDATE: the manifest gains an entry; whatever fixture pins entry count or order needs the new row.
- [ ] `tests/unit/test_hooks_audit.py` — UPDATE: the new hook script must satisfy the audit's conventions.
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — UPDATE: the Bash dispatcher gains a tenth predicate; the ordering and first-block-wins fixtures enumerate predicates.
- [ ] `tests/unit/test_update_hardlinks.py` — VERIFY, do not assume: the new hook is `scope = "project"`, so no hardlink change should be needed. Confirm rather than skip.
- [ ] Any test asserting `scripts/update/run.py`'s Redis-durability logging — UPDATE for the newly-loud down path.
- [ ] New: `tests/unit/test_plan_doc_lease.py`, `tests/unit/test_validate_plan_doc_lease.py`, `tests/unit/test_validate_plan_commit_on_main.py`.

No existing test asserts plan-doc write behavior, because nothing previously guarded it.

## Rabbit Holes

Deliberately not built. These are not deferrals — they are decisions.

- **`plan-lease steal`.** The provably-dead path plus a 900s TTL already covers every stale lease. A steal verb exists to defeat the guard, and the guard is 20 lines.
- **`--reason` audit strings on lease operations.** More ceremony than the thing being protected.
- **A stage-boundary dirty-`docs/plans/` warning.** Once writes are leased, two agents cannot be mid-edit on one doc, and #2669 already wrote the commit-promptly norm into `do-plan`. A warning nobody reads is not a control.
- **A written loser-side stand-down protocol.** The block reason tells the loser what happened and who holds the doc; that is the protocol. #2731 carries the version that matters, for stage dispatch, where there is no block reason to read.
- **Consolidating the two `Write|Edit` hooks into a dispatcher.** Tidy, and it would change an existing validator's `exit_policy` semantics for no behavioral gain. Two interpreter starts on a Write is not the problem here.
- **A general-purpose file lease for any path.** The thing that makes this tractable is that plan docs are one flat directory of slug-named files with a single well-understood write pattern.
- **Fixing supervisor forking.** #2657 owns it.

## Risks

### Risk 1: Fail-open means no protection during a Redis outage
**Impact:** If Redis is unreachable, every plan-doc write proceeds unguarded, silently.
**Mitigation:** Accepted by the owner — a Redis outage is too rare to design around, and the alternative (refusing plan writes when the lease store is down) turns a hiccup into "nobody can plan." The compensating control is Task 4: `/update` states a down Redis loudly instead of at verbose level. Tested explicitly with a raising Redis stub.

### Risk 2: `transcript_path` turns out not to be per-subagent in the hook payload
**Impact:** The supervisor-vs-own-child clause becomes unimplementable as designed — both agents hash to one holder and the supervisor's stray edit sails through as a renewal.
**Mitigation:** Task 1 asserts this against a live payload first. Owner's ruling if it fails: **ship the partial** — the lane-level lease still closes the two-children and stranded-commit shapes — and state plainly in the PR body that the supervisor-vs-child criterion is unmet. Do not invent an identity that cannot distinguish them.

### Risk 3: Stale leases block a lane
**Impact:** An agent killed mid-revision leaves a lease; if its pid is unreadable, the next writer waits up to 15 minutes.
**Mitigation:** The provably-dead path claims immediately, which is the common case. TTL is env-overridable. No steal verb, by decision.

### Risk 4: The hook adds latency to every Write and Edit
**Impact:** Plan docs are a small fraction of writes; a Redis round trip on every source-file write would be a real regression.
**Mitigation:** The path check happens before any import of the lease module. Test asserts a non-plan path performs zero Redis calls, using a stub that raises on any call.

## Race Conditions

### Race 1: Two agents claim a free lease simultaneously
**Location:** `models/plan_doc_lease.py::touch_plan_doc_lease`
**Trigger:** Both hooks fire before either writes.
**Data prerequisite:** The lease key must not exist.
**State prerequisite:** Exactly one claimant may win.
**Mitigation:** `SET NX EX` is atomic; the loser's `NX` fails and it re-reads the payload. `touch_issue_lock`'s re-read race (key expired between the failed `SET NX` and the follow-up `GET` → treat as free, this attempt wins) is reproduced deliberately, with the same reasoning.

### Race 2: Lease approval and the actual file write are not atomic
**Location:** hook exit → harness performs the Write
**Trigger:** The hook allows, then the process is descheduled before the write lands.
**Data prerequisite:** none.
**State prerequisite:** The lease is held for the duration.
**Mitigation:** Irreducible and harmless — the lease persists across the window, so a peer arriving mid-window is refused. The window can only delay the legitimate holder's write, never admit a concurrent one. Documented, not defended against.

### Race 3: pid recycling defeats the fence
**Location:** `agent/pid_fence.py` consumers
**Trigger:** The OS reuses a dead holder's pid before the lease is evaluated.
**Data prerequisite:** Recorded `pid_create_time`.
**State prerequisite:** none.
**Mitigation:** `create_time` comparison makes a recycled pid read as a different process — not-live-as-recorded, therefore claimable, which is correct. `pid_fence`'s docstring already states this is detection, not a guarantee, and there is no pidfd on darwin. Do not attempt to close it here.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2731] The stage-dispatch liveness gate: refusing a dispatch whose same-skill predecessor is still alive, and the loser-side stand-down protocol that goes with it. Split out of this plan with its evidence and acceptance criteria intact.
- [SEPARATE-SLUG #2657] The resume-from-transcript supervisor forking that spawns rival incarnations of one pipeline supervisor. This plan blocks the *write* such a fork would make; the harness-level cause is #2657's.
- [SEPARATE-SLUG #2628] Cross-lane pytest Redis DB flushing. Inventoried in the original issue for completeness only.
- [SEPARATE-SLUG #2675] Continuity re-ensure re-minting `run_id`. This plan does not touch it; #2731 must test against it.

Everything else the owner cut is a decision, not a deferral, and is recorded in Rabbit Holes rather than here.

## Update System

The hook is registered through `.claude/hooks/manifest.toml`, and `scripts/update/run.py` regenerates `.claude/settings.json` from it — so a machine picks the guard up on its next `/update` with no extra step. Verify during build:

- The entry is `scope = "project"`: no hardlink into `~/.claude/hooks/`, no `~/.claude/settings.json` rewrite.
- No new dependency, config file, or secret, so nothing new must propagate.
- `sdlc-tool plan-lease` is a subcommand of the existing `sdlc-tool` console script, not a new `[project.scripts]` row — no reinstall needed.

One deliberate change to `/update` itself: Task 4 makes a down local Redis a loud warning rather than a verbose-level log line, because this plan's fail-open posture depends on someone noticing.

Existing installations need no migration: absent lease keys mean every doc is free.

## Agent Integration

- **`sdlc-tool plan-lease {status,release}`** — a subcommand of the existing `sdlc-tool` console script, invoked via Bash. No `pyproject.toml [project.scripts]` change.
- **The hook layer** — the block reason *is* the integration. It is the only thing a refused agent sees, so it must be actionable prose (covered under Failure Path Test Strategy → Error State Rendering).

Integration test: shell out to `sdlc-tool plan-lease status --slug <slug>` and assert JSON on stdout, confirming the subcommand is wired into the CLI's parser rather than merely importable.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/plan-doc-single-writer-lease.md` — the identity model, the two staleness clocks, the fail-open posture and the owner's reasoning for it, the commit guard, and what was deliberately left out (with the #2731 pointer).
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/sdlc/do-plan.md` with the `plan-lease release` invocation, placed beside the existing `meta-set plan_revising false` line so the two are read together.
- [ ] Verify (do not assume) whether `docs/features/hook-manifest.md` enumerates registered hooks and needs the new row.

### External Documentation Site
- [ ] Not applicable — no external site covers SDLC internals.

### Inline Documentation
- [ ] `models/plan_doc_lease.py` module docstring carries the identity model and the fail-open rationale, in the style of `touch_issue_lock`'s.
- [ ] The hook docstring names the collision shapes it prevents with their 2026-08-07 evidence, following `validate_no_destructive_git_in_shared_checkout.py`'s precedent.

## Success Criteria

- [ ] A second agent's `Write`/`Edit` to a plan doc held by a live foreign holder is blocked, and the reason names the holder.
- [ ] A supervisor's own `Edit` to a doc its dispatched child holds is blocked (the same-`session_id`, different-`transcript_path` case) — or, if spike-1's premise fails, this criterion is explicitly reported unmet in the PR body rather than quietly dropped.
- [ ] A lease whose holder pid is provably dead is claimed by the next writer without waiting for TTL; a lease whose fence is unknown is not.
- [ ] A `git commit` touching `docs/plans/` from the shared checkout with HEAD ≠ `main` is blocked; the same commit from a worktree, or with the override token, is not.
- [ ] A non-plan-doc `Write` performs zero Redis calls.
- [ ] `/update` states a down local Redis loudly.
- [ ] Each guard is demonstrated red: a mutation that turns exactly the intended tests red, with measured counts pasted into the PR body.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lease + hook)**
  - Name: `lease-builder`
  - Role: `models/plan_doc_lease.py`, the `plan-lease` subcommand, the `Write|Edit` hook and its manifest entry
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (guards)**
  - Name: `guard-builder`
  - Role: the off-main commit predicate and the `/update` Redis warning
  - Agent Type: builder
  - Domain: security/untrusted-input (command-shape parsing)
  - Resume: true

- **Test engineer**
  - Name: `lease-tester`
  - Role: mutation-checked coverage across both guards
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `lease-validator`
  - Role: verifies acceptance criteria and the red-state proofs
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lease-documentarian`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Confirm hook-payload agent identity (gates the lease)
- **Task ID**: verify-identity
- **Depends On**: none
- **Validates**: tests/unit/test_validate_plan_doc_lease.py (create)
- **Informed By**: spike-1 (subagent transcripts are per-agent files; `sessionId` is shared)
- **Assigned To**: lease-builder
- **Agent Type**: builder
- **Parallel**: false
- Capture a real PreToolUse payload from inside a dispatched subagent and confirm `transcript_path` points at the subagent's own `subagents/agent-*.jsonl`, not the root session file.
- If it does not: do NOT invent a substitute identity. Fall back to `session_id` (lane-level), continue, and record the unmet criterion for the PR body — that is the owner's standing ruling (Risk 2).
- Land the identity resolver (`holder_id`, `agent_label`) with its tests as the first commit.

### 2. Build the lease and the write guard
- **Task ID**: build-lease
- **Depends On**: verify-identity
- **Validates**: tests/unit/test_plan_doc_lease.py (create), tests/unit/test_validate_plan_doc_lease.py, tests/unit/test_hook_manifest.py, tests/unit/test_hooks_audit.py
- **Assigned To**: lease-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data
- **Parallel**: false
- `touch_plan_doc_lease` with acquire/renew/peek in one entry point, modeled on `models/session_lifecycle.py::touch_issue_lock`.
- Two staleness clocks: `PLAN_DOC_LEASE_TTL_SECONDS` (default 900, env-overridable, commented as provisional) and the `pid_fence`, with unknown never authorizing an early claim.
- Fail open on every Redis exception, logging the error class.
- `sdlc-tool plan-lease {status,release}` — two verbs, no more.
- `validate_plan_doc_lease.py`: judge the path the tool named, never content (`8a9137002`). Non-plan paths return before importing the lease module. Block reason names slug, holder label, holder age, and the `plan-lease status` command.
- One `[[hook]]` entry, `matcher = "Write|Edit"`, `exit_policy = "deny-only"`, `scope = "project"`; regenerate `settings.json` from the manifest — never hand-edit it.

### 3. Build the off-main commit guard
- **Task ID**: build-commit-guard
- **Depends On**: none
- **Validates**: tests/unit/test_validate_plan_commit_on_main.py (create), tests/unit/test_pre_tool_use_dispatcher.py
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: security/untrusted-input
- **Parallel**: true
- `is_plan_commit_off_main` in `hook_utils/`, wired as predicate 10 in `dispatch/pre_tool_use_bash.py`, fail-open like its neighbors.
- Command-position anchored, per `validate_no_destructive_git_in_shared_checkout.py`: `git commit -m "move plan to main"` must not fire.
- Covers `-a`, explicit pathspecs, and the already-staged case. Never fires inside `.worktrees/` or a foreign repo. Inline override `# allow-plan-commit-off-main`.

### 4. Make a down Redis loud in /update
- **Task ID**: build-redis-warning
- **Depends On**: none
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- `scripts/update/run.py:1332`: the Redis-down skip path logs at verbose level today. Make it `always=True` and append to `result.warnings`.
- This is the entire compensating control for the fail-open posture. Keep it to that — no new health subsystem.

### 5. Mutation-checked test pass
- **Task ID**: test-all
- **Depends On**: build-lease, build-commit-guard, build-redis-warning
- **Assigned To**: lease-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- For each guard, produce a mutation that turns exactly the intended tests red, and record which tests and how many. Measured numbers only — the #2669 PR body had to be corrected three times for remembered ones.
- Explicitly cover: zero Redis calls on a non-plan write; fail-open under a raising Redis; unknown-fence not claiming early.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: test-all
- **Assigned To**: lease-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/plan-doc-single-writer-lease.md` + README index row, including what was deliberately left out and the #2731 pointer.
- `docs/sdlc/do-plan.md` lease-release invocation; verify `docs/features/hook-manifest.md` rather than assuming.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: lease-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table, confirm every Success Criterion, confirm the red-state proofs are in the PR body.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lease core tests | `scripts/pytest-clean.sh tests/unit/test_plan_doc_lease.py -q` | exit code 0 |
| Write-guard tests | `scripts/pytest-clean.sh tests/unit/test_validate_plan_doc_lease.py -q` | exit code 0 |
| Commit-guard tests | `scripts/pytest-clean.sh tests/unit/test_validate_plan_commit_on_main.py -q` | exit code 0 |
| Hook wiring tests | `scripts/pytest-clean.sh tests/unit/test_hook_manifest.py tests/unit/test_hooks_audit.py tests/unit/test_pre_tool_use_dispatcher.py -q` | exit code 0 |
| Hook registered from manifest | `grep -c validate_plan_doc_lease .claude/settings.json` | output > 0 |
| Lease CLI is actually wired | `sdlc-tool plan-lease status --slug nonexistent-slug` | exit code 0 |
| TTL is a named constant | `grep -c "PLAN_DOC_LEASE_TTL_SECONDS" models/plan_doc_lease.py` | output > 0 |
| No bare TTL literal in the lease module | `grep -c "900" models/plan_doc_lease.py` | match count == 0 |
| Anti-criterion: no steal verb shipped | `grep -c "steal" models/plan_doc_lease.py` | match count == 0 |
| Anti-criterion: lease never raw-deletes Popoto keys | `grep -cE "\.delete\(\|\.srem\(\|\.sadd\(\|\.zrem\(" models/plan_doc_lease.py` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
