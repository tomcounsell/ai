---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-11
tracking: https://github.com/tomcounsell/ai/issues/2650
last_comment_id: 5251329845
---

# Plan-Doc Single-Writer Lease

## Problem

Two agents can hold the same `docs/plans/{slug}.md` at the same time, and neither finds out except by luck. On 2026-08-07 a plan writer watched its own document grow from 62 to 111 lines between two of its own reads and had to stop and ask a human who owned the file. Later the same day, a lane supervisor patched Task 1 of a plan while its own dispatched revision child was rewriting that plan, then had to shout a warning at the child mid-flight. A third writer's revision (`5945c0c9c`) silently reverted regions a concurrent writer had just authored; the damage was repaired region-by-region the same day in `28e2e2055`. A fourth committed a plan revision onto `session/retire-dead-intake-guard-tests` — a different lane's branch — because the shared checkout's HEAD had been switched underneath it, and the content had to be independently restored to main in `8afe2df22`.

Code work is isolated per lane in `.worktrees/{slug}/`. Plan docs, by owner policy, are not: they commit directly on `main` in the shared checkout, which every concurrent lane shares. That policy is not up for renegotiation here — the point of this work is to make it safe.

**Current behavior:** nothing checks who owns a plan doc before writing it. `plan_revising` looks like a busy signal but is a Redis meta flag consumed only by the router's G7 guard to decide whether to route to build; no write path reads it. `same_stage_dispatch_count` looks like a concurrency counter but counts *oscillation* (same skill, same stage snapshot, N times) and cannot tell "the first dispatch died and was recovered" from "two agents are in the stage right now." Dispatch records carry no pid at all. Every collision is therefore detected by an agent noticing its file moving, and resolved by escalating to a human-adjacent supervisor.

**Desired outcome:** a second writer on a plan doc is refused at the tool layer, before the write lands. A stage dispatch whose predecessor is still alive is refused at the tool layer, while a dispatch whose predecessor is provably dead still goes through. A plan commit attempted while HEAD is not `main` fails loudly instead of stranding content on a foreign branch. And when a collision is detected anyway, the loser has a written protocol to stand down by instead of improvising.

## Freshness Check

**Baseline commit:** `dbf761ed4`
**Issue filed at:** 2026-08-07T07:30:33Z (rescoped and retitled 2026-08-11)
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/sdlc_stage_query.py:526` — `plan_revising` read from `_plan_revising` — holds.
- `tools/sdlc_stage_query.py:543` — `same_stage_dispatch_count` computed from `_sdlc_dispatches` — holds.
- `agent/sdlc_router.py:1720` — `record_dispatch` appends `{skill, at, stage_snapshot}` and nothing else — holds; this is why AC 2 needs a schema extension rather than just a read.
- `models/session_lifecycle.py:1071` — `touch_issue_lock`, plain-Redis run_id-keyed lock whose payload already carries `pid`/`create_time`/`machine_id` — holds; this is the template for the plan-doc lease.
- `agent/pid_fence.py:1` — `(pid, create_time)` fence with the canonical unknown-means-unknown rule — holds.
- `tools/sdlc_stage_marker.py:510-522` — lease resolved *before* the state-machine write, `LEASE_ABSENT` exits 1 — holds, and contradicts the original issue's "the refusal persisted anyway" claim (see Recon "Revised").
- `.claude/hooks/manifest.toml` — `validate_design_system_readonly` registered on `matcher = "Write|Edit"` — holds; the precedent for a plan-doc write hook.
- Commit `c6e48a514` (shape 5's stranded commit) — **gone**; the session branch was deleted. The restore commit `8afe2df22` is present, so the event is corroborated by its repair rather than its cause.

**Cited sibling issues/PRs re-checked:**
- #2669 — MERGED 2026-08-10. Shipped the FETCH_HEAD and shared-`refs/stash` half. Out of scope here.
- #2628 — still OPEN. Owns the cross-lane pytest Redis DB collision. Out of scope here.
- #2629 — CLOSED. Its comment on this issue supplies the BUILD-stage double-dispatch evidence and the "naive refuse if count ≥ 1 is wrong" constraint, which AC 2 honors.
- #2657 — OPEN, filed 2026-08-07. Documents the resume-from-transcript supervisor forking that is shape 4's root cause. Deliberately not absorbed; this plan implements only the defensive half.
- #2675 — OPEN. Continuity re-ensure mints a new `run_id`, recreating the ledger anchor. Interacts with any ownership check — noted as Risk 3.
- #2645 — OPEN, Redis flush hardening. Adjacent, no overlap.

**Commits on main since issue was filed (touching referenced files):**
- `7773d8d97` — the #2669 work — **partially addresses**: closes the git-metadata half and states the two coordination norms in `do-plan`/`do-sdlc` as prose. Leaves everything in this plan.
- `8a9137002` "Judge only the file the Write named, and stop firing on quoted critique text" — **relevant**: it is a `Write`-matched hook precision fix; the new plan-doc hook must not repeat the mistake it corrects (judging content rather than the named path).
- `337cc1f31` "Let skills that mandate subagent dispatch actually dispatch" — irrelevant to the write path.
- `76a23e15a` — irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none. `codex-exec-dev-lane.md` is the only plan naming lanes and does not touch plan-doc writes.

**Notes:** the original issue asserted a fail-open `stage-marker` lease gate "being filed separately." No such issue exists and the current code refuses before writing. Dropped.

## Prior Art

- **PR #2669** (merged 2026-08-10): fixed the shared-`FETCH_HEAD` and shared-`refs/stash` halves of this issue and stated the plan-doc commit-promptness and live-child-enumeration norms as prose in the skill bodies. Succeeded at what it scoped. Its explicit non-goal was the tool-layer enforcement this plan provides.
- **Issue #2448 / `validate_no_destructive_git_in_shared_checkout.py`**: blocks whole-tree destructive git in the shared checkout, motivated by exactly this hazard — a MERGE-stage agent clobbering peers' uncommitted `docs/plans/*.md`. Succeeded, and is the direct structural precedent for the HEAD-is-main guard in Task 4: same dispatcher, same shared-checkout resolution, same inline-override convention.
- **Issue #2064 / PR #2107**: serialized full-suite pytest across worktrees with a lock. Succeeded. Precedent that a cross-worktree lock is an accepted shape here.
- **Issue #2026 / PR #2076**: SDLC fork-vs-supervisor hardening — single-owner lease, verdict-gated routing. Succeeded at the *routing* layer; it is why `touch_issue_lock` exists in its current run_id-keyed form. It did not reach the file-write layer, which is why plan docs stayed unprotected.
- **Issue #2137 / `validate_no_destructive_git_in_worktree.py`**: the inverse-surface guard. Relevant as the model for how narrowly to scope a deny-list.
- **Issue #2657**: filed for the supervisor-forking root cause. Not prior art so much as the sibling that owns the other end of shape 4.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2669 | Stated commit-promptness (do-plan Phase 4) and live-child enumeration (do-sdlc 3c) as prose in skill bodies | Prose in a skill body is advice to a model, not a gate. It cannot bind a *second* agent that never read the first one's intent, and it cannot bind a forked supervisor that believes it is the sole owner. |
| PR #2076 (#2026) | Run-id-keyed issue lease, verdict-gated routing | Guards *routing decisions*, not *file writes*. Two agents with a legitimately shared run_id (a supervisor and its own child) both pass every lease check and then collide on the file. |
| `plan_revising` meta flag | In-file/in-Redis busy signal set by critique | Never read by a write path. It is keyed by issue, so even if a writer checked it, it could not distinguish a supervisor from its own child on the same issue. |
| G4 / `same_stage_dispatch_count` | Caps repeated dispatches of the same skill at the same stage snapshot | An oscillation cap, not a concurrency check. It fires on *repetition over time*, is blind to *overlap in time*, and would wrongly refuse the legitimate recovery of a dispatch that died mid-work. |

**Root cause pattern:** every prior fix guarded a *decision* (route, dispatch, merge) keyed by *lane* or *run*. The collisions happen at a *write*, between agents that share a lane and a run. Nothing in the system has ever had a notion of "which agent holds this file right now."

## Data Flow

1. **Entry point**: an agent (supervisor or subagent) calls `Write` or `Edit` with `file_path` under `docs/plans/`.
2. **Claude Code PreToolUse hook**: the harness invokes the `Write|Edit`-matched hook with JSON on stdin carrying `session_id`, `transcript_path`, `cwd`, and `tool_input`.
3. **Identity resolution**: the hook derives a *holder id* from `transcript_path` — a subagent's transcript is a distinct file (`<session>/subagents/agent-*.jsonl`) while `session_id` is shared with the parent (spike-1). This is what makes the supervisor-vs-own-child case decidable.
4. **Lease check** (`models/plan_doc_lease.py`): `SET NX EX` on `sdlc:plandoc:{slug}` claims a free doc; a same-holder hit renews; a different-holder hit is evaluated for staleness (pid fence + TTL) and either claimed or refused.
5. **Decision**: allow (write proceeds, lease renewed) or `{"decision": "block", "reason": ...}` naming the current holder and the release/steal path.
6. **Release**: `sdlc-tool plan-lease release --slug` at the end of a revision pass (next to the existing `meta-set plan_revising false`), or TTL expiry, or a provably-dead holder being displaced by the next writer.

Second, independent flow — dispatch:

1. **Entry point**: `/do-sdlc` step 3b runs `sdlc-tool dispatch record --skill ... --run-id ...` before spawning a stage subagent.
2. **Liveness gate** (`tools/sdlc_dispatch.py`): reads the most recent dispatch for the same skill from `_sdlc_dispatches`; if it carries a `dispatcher` block whose `(pid, create_time)` fence resolves LIVE and no `completed` marker for that stage postdates it, the record is refused (`DISPATCH_LIVE`, exit 1).
3. **Recording**: on allow, the new record is appended carrying this dispatcher's own `{holder_id, pid, pid_create_time}`.

## Architectural Impact

- **New dependencies**: none. `psutil` is already a dependency (`agent/pid_fence.py`), Redis is already the substrate.
- **Interface changes**: `record_dispatch()` gains an optional `dispatcher` kwarg and its record grows a `dispatcher` key (additive; readers of `{skill, at, stage_snapshot}` are unaffected). `sdlc-tool` gains a `plan-lease` subcommand and `dispatch record` gains `--force`.
- **Coupling**: adds one coupling that did not exist — the hook layer now reads Redis. This is already true of `pre_tool_use.py` (it queries `AgentSession`), so the precedent and the import-cost profile are known.
- **Data ownership**: introduces a new owner-of-record for "who may write this plan doc," which nothing previously owned.
- **Reversibility**: high. Removing the manifest entry disables the lease guard entirely; the lease keys are TTL'd and self-evaporate.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the fail-open-vs-fail-closed call in Risk 1 is a judgment the PM should confirm)
- Review rounds: 2+ (this is concurrency code in a hook path; the last three PRs in this area each found a real bug in review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as R; R.ping()"` | Lease substrate |
| `psutil` importable | `python -c "import psutil"` | pid fence liveness |
| Hook manifest generator | `python -c "import scripts.update.hooks"` | Manifest→settings regeneration for the new hook entry |

## Spike Results

### spike-1: Is a subagent's write distinguishable from its parent supervisor's at hook time?
- **Assumption**: "The PreToolUse hook payload contains something that differs between a supervisor and its own dispatched child."
- **Method**: code-read + transcript inspection
- **Finding**: `session_id` is **shared** — a subagent transcript (`~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl`) carries the parent's `sessionId` on every entry. But subagents get their own **transcript file** and their own `agentId` field. So `transcript_path`, not `session_id`, is the agent-distinguishing handle, and the supervisor-vs-own-child case (AC 1's hardest clause) is decidable from the hook payload.
- **Confidence**: medium — confirmed that the *transcript file* is per-agent and that `sessionId` is not; **not** yet confirmed that Claude Code passes the *subagent's* transcript path (rather than the root session's) in the hook payload for a tool call made inside a subagent. Task 1 opens by asserting this against a live payload and the plan branches on the answer.
- **Impact on plan**: holder id = `sha256(transcript_path)[:16]`, with the transcript basename retained as a human-readable `agent_label`. If Task 1's assertion fails, fall back to the identity described in Risk 2.

### spike-2: Do dispatch records carry anything that could serve as a liveness signal today?
- **Assumption**: "`same_stage_dispatch_count` can be turned into a concurrency refusal without a schema change."
- **Method**: code-read (`agent/sdlc_router.py:1679-1731`)
- **Finding**: No. The record is exactly `{skill, at, stage_snapshot}`. There is no pid, no holder, no heartbeat. `compute_same_stage_count` walks the history comparing *snapshots*, which is an oscillation signal by construction.
- **Confidence**: high
- **Impact on plan**: AC 2 requires an additive schema change (Task 3), and old records must be treated as "unknown → allow" so the gate does not fire on pre-cutover history.

## Solution

### Key Elements

- **`models/plan_doc_lease.py`** — an agent-keyed, TTL'd, pid-fenced lease over one plan doc. Structural twin of `touch_issue_lock`, keyed by slug and holder instead of issue and run.
- **`validate_plan_doc_lease.py`** — a `Write|Edit` PreToolUse hook that resolves the holder, takes or renews the lease, and blocks a foreign live holder's write with a reason naming who holds it.
- **`sdlc-tool plan-lease`** — `status` / `release` / `steal`, so an agent can hand the doc back deliberately and an operator can break a lease without editing Redis.
- **Dispatch liveness gate** — `dispatch record` refuses when the prior same-skill dispatch is provably still alive, and allows when it is provably dead (the #2629 recovery case).
- **Plan-commit branch guard** — a tenth predicate in the existing Bash dispatcher: no `docs/plans/` commit in the shared checkout while HEAD is not `main`.
- **Stage-boundary dirty warning** — `stage-marker --status completed` names any dirty `docs/plans/` file on stderr. Warn only, never block.
- **Loser-side stand-down protocol** — written into the skill bodies, codifying what the #2629 agent did by hand.

### Flow

Agent edits plan doc → hook resolves holder from transcript_path → lease free or same holder → **write proceeds** (lease renewed)

Agent edits plan doc → hook resolves holder → lease held by a live foreign holder → **blocked**, reason names holder + `sdlc-tool plan-lease status --slug` → agent stands down per protocol

Agent edits plan doc → lease held by a provably-dead holder → **claimed**, write proceeds, stderr notes the displaced holder

Supervisor dispatches BUILD → `dispatch record` → prior BUILD dispatcher fence LIVE → **refused** (`DISPATCH_LIVE`) → supervisor waits

Supervisor dispatches BUILD → prior BUILD dispatcher fence DEAD → **recorded**, recovery proceeds

### Technical Approach

- **Lease store**: plain Redis key `sdlc:plandoc:{slug}` (not Popoto-managed — same call as `touch_issue_lock`, whose docstring explains why), JSON payload `{holder_id, agent_label, session_id, pid, pid_create_time, machine_id, hostname, issue_number, acquired_at, renewed_at}`. Single `touch_plan_doc_lease(slug, holder_id, ..., peek=False)` entry point returning a `PlanDocLeaseResult(acquired, owner_holder_id, owner_agent_label, orphaned)`. One function, three behaviors (acquire / renew / peek), exactly as `touch_issue_lock` does it — do not grow a second entry point.
- **Staleness, two independent clocks**: a TTL (`PLAN_DOC_LEASE_TTL_SECONDS`, default 900 — provisional/tunable, env-overridable, named constant per the repo's magic-number rule) and the `(pid, create_time)` fence from `agent/pid_fence.py`. A lease whose holder pid is **provably dead** is claimable immediately, before TTL. A lease whose fence is **unknown** (no recorded create_time, `AccessDenied`, psutil missing) is NOT claimable early — it waits for TTL. That is the canonical unknown rule from `pid_fence`: unknown authorizes no escalation, and stealing a live agent's file is an escalation.
- **Holder identity**: `sha256(transcript_path)[:16]` when `transcript_path` is present, else `sha256(session_id)[:16]`. Both agents in the supervisor-vs-child case have a `transcript_path`; only their values differ. Store the transcript basename as `agent_label` so the block reason is readable ("held by agent-abug-triage-b0b9…").
- **Slug derivation**: from the `file_path` in `tool_input` — basename minus `.md`. The hook judges *the path the tool named*, never file content (`8a9137002` is the cautionary precedent).
- **Hook scope**: fires only when the resolved path is under `docs/plans/` and ends in `.md`. Everything else is an immediate allow, before any Redis import, so the common Write/Edit path pays nothing but a path comparison.
- **Fail posture**: fail OPEN on any Redis or import error, logged via `log_hook_error`. A Redis hiccup must degrade to today's behavior (no protection), never to "nobody can write a plan." This mirrors `touch_issue_lock`'s documented fail-open and is the single most important defensive choice in this plan — see Risk 1.
- **Manifest registration**: one new `[[hook]]` entry, `event = "PreToolUse"`, `matcher = "Write|Edit"`, `exit_policy = "deny-only"` (the hook owns its fail-open internally; its exit 2 is a real decision to block). Never hand-edit `settings.json` — regenerate from the manifest.
- **Dispatch gate**: `record_dispatch` gains an optional `dispatcher: dict | None`; `tools/sdlc_dispatch.py` supplies `{holder_id, pid, pid_create_time}` and, before appending, scans history backward for the most recent record with the same `skill`. Refuse (exit 1, `DISPATCH_LIVE`, naming the live pid) only when that record has a `dispatcher` block AND the fence says LIVE AND no `completed` marker for the stage postdates its `at`. A record without a `dispatcher` block is pre-cutover — unknown — allow. `--force` overrides, and prints what it overrode.
- **Commit guard**: `is_plan_commit_off_main(command, cwd)` in `hook_utils/`, wired as predicate 10 in `dispatch/pre_tool_use_bash.py`. Fires only when: cwd resolves to this repo's shared toplevel (not under `.worktrees/`), the command is a `git commit` in command position, `git rev-parse --abbrev-ref HEAD` is not `main`, and `git diff --cached --name-only` (plus explicit pathspec args, plus `-a`'s effect via `git diff --name-only`) intersects `docs/plans/`. Inline override token `# allow-plan-commit-off-main`.
- **Stage-boundary warning**: in `tools/sdlc_stage_marker.py`'s `completed` path only, after the write succeeds, run `git -C <shared toplevel> status --porcelain -- docs/plans/`. Non-empty → one stderr line per dirty file. Wrapped in its own try/except; it can never change the marker's exit code, per that module's existing telemetry convention.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new hook's outer `except Exception` must not be bare-`pass`: it logs via `log_hook_error` and allows. Test asserts the log call fires and the decision is allow, using a Redis stub that raises.
- [ ] `touch_plan_doc_lease`'s Redis fail-open logs the swallowed error class explicitly (matching `touch_issue_lock`'s documented behavior). Test asserts the class name appears in the log record.
- [ ] The stage-marker dirty-warning block swallows everything. Test asserts a raising `git` subprocess leaves the marker's exit code and JSON untouched.

### Empty/Invalid Input Handling
- [ ] Hook payload with no `transcript_path` → falls back to `session_id`; with neither → fail open (allow), logged. Both tested.
- [ ] `tool_input` with no `file_path`, empty string, or a directory path → allow, no Redis call.
- [ ] A slug that is empty after stripping `.md`, or a path under `docs/plans/` that is not `.md` → allow, no Redis call.
- [ ] Malformed (non-JSON) lease payload in Redis → treated as a foreign non-matching holder, exactly as `touch_issue_lock` treats a legacy value. Never raises.
- [ ] `dispatch record` against an empty `_sdlc_dispatches` list, and against records missing `dispatcher` → allow.

### Error State Rendering
- [ ] The block reason is the agent's only channel here: test that it names the slug, the holding `agent_label`, the holder's age, and the exact `sdlc-tool plan-lease status --slug <slug>` command. A reason that says only "blocked" is a failed test.
- [ ] `DISPATCH_LIVE` stderr names the live pid and the `--force` escape. Asserted on stderr, not just exit code.

## Test Impact

- [ ] `tests/unit/test_sdlc_dispatch.py` — UPDATE: `record_dispatch_for_ledger` calls gain a `dispatcher` block; add cases for LIVE-refusal, DEAD-allow, missing-dispatcher-allow, and `--force`.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: `record_dispatch` is called directly here; assert the added key is additive and G4's snapshot comparison is unchanged by it (the snapshot must not include `dispatcher`, or every dispatch becomes unique and G4 dies silently).
- [ ] `tests/unit/test_hook_manifest.py` — UPDATE: the manifest gains an entry; whatever fixture pins entry count/order needs the new row.
- [ ] `tests/unit/test_hooks_audit.py` — UPDATE: new hook script must satisfy the audit's conventions.
- [ ] `tests/unit/test_pre_tool_use_dispatcher.py` — UPDATE: the Bash dispatcher gains a tenth predicate; the ordering/first-block-wins fixtures enumerate predicates.
- [ ] `tests/unit/test_sdlc_stage_marker.py` — UPDATE: assert the dirty-plans warning appears on stderr for a `completed` write and that exit code/JSON are unchanged.
- [ ] `tests/unit/test_update_hardlinks.py` — UPDATE only if the new hook is global-scope; it is project-scope, so verify no change is needed rather than assuming.
- [ ] New: `tests/unit/test_plan_doc_lease.py`, `tests/unit/test_validate_plan_doc_lease.py`, `tests/unit/test_validate_plan_commit_on_main.py`.

## Rabbit Holes

- **Consolidating the `Write|Edit` hooks into a dispatcher.** There are now two `Write|Edit` entries (`validate_design_system_readonly` and the new one), which is two interpreter starts per write. Mirroring `dispatch/pre_tool_use_bash.py` is the tidy answer and it is *not* this plan's job — it changes an existing validator's `exit_policy` semantics and would need its own red-state proof. Leave it.
- **Making the lease authoritative for anything other than writes.** Do not wire it into routing, merging, or the G7 guard. `plan_revising` stays exactly as it is.
- **A general-purpose file lease for any path.** Tempting, and wrong at this size. The shape that makes this tractable is that plan docs are one flat directory of slug-named files with a single well-understood write pattern.
- **Fixing supervisor forking.** #2657 owns it. Verifying ownership before dispatch is defense; chasing the resume-from-transcript semantics is a different project.
- **Making the lease survive process death gracefully via a heartbeat thread.** The pid fence plus TTL already covers it. A heartbeat means a thread in the hook process, which does not outlive the hook.

## Risks

### Risk 1: A fail-closed lease wedges all planning
**Impact:** If the hook blocks on a Redis error, an unreachable Redis stops every agent from writing any plan doc — a far worse outage than the collisions this fixes, and one that would present as an inexplicable universal block.
**Mitigation:** Fail open on every error path, logged with the error class. This is the same call `touch_issue_lock` makes and documents. Tested explicitly with a raising Redis stub. Accepted consequence: during a Redis outage, this protection is simply absent.

### Risk 2: `transcript_path` turns out not to be per-subagent in the hook payload
**Impact:** AC 1's supervisor-vs-own-child clause becomes unimplementable as designed — the two agents would hash to the same holder and the supervisor's stray edit would sail through as a renewal.
**Mitigation:** Task 1 asserts this against a live payload *first* and stops if it fails. Fallback identity, in order: the `agentId` field if the harness exposes it; otherwise a *tool-call-sequence* identity is not viable and the honest answer is to ship the lane-level lease (which still fixes shapes 1, 3 and 5) and record the supervisor-vs-child clause as unmet, rather than pretend. Do not invent an identity that cannot distinguish them.

### Risk 3: The dispatch gate collides with #2675's run_id re-minting
**Impact:** #2675 reports that continuity re-ensure mints a fresh `run_id` and recreates the ledger anchor. If the dispatch history is wiped with it, the gate has nothing to read and silently allows — a fail-open that looks like a pass.
**Mitigation:** The gate keys on the *dispatcher pid fence*, not on run_id, so a re-minted run does not make a live prior dispatcher look dead. Add an explicit test that a run_id change between two dispatch records does not clear the LIVE refusal. Where the history is genuinely gone, log the fact rather than allowing silently.

### Risk 4: The hook adds latency to every Write and Edit
**Impact:** Plan docs are a small fraction of writes; a Redis round trip on every source-file write would be a real regression.
**Mitigation:** The path check happens before any import of the lease module. Test asserts that a non-plan path performs zero Redis calls, using a stub that raises on any call.

### Risk 5: Stale leases from crashed agents block a lane for the full TTL
**Impact:** An agent killed mid-revision leaves a lease; if its pid is recycled or unreadable, the next writer waits up to 15 minutes.
**Mitigation:** The provably-dead path claims immediately, which covers the common case. `sdlc-tool plan-lease steal` covers the rest and prints what it displaced. TTL is env-overridable for a machine that wants a shorter window.

## Race Conditions

### Race 1: Two agents claim a free lease simultaneously
**Location:** `models/plan_doc_lease.py::touch_plan_doc_lease`
**Trigger:** Both hooks fire before either writes.
**Data prerequisite:** The lease key must not exist.
**State prerequisite:** Exactly one claimant may win.
**Mitigation:** `SET NX EX` is atomic; the loser's `NX` fails and it re-reads the payload. The `touch_issue_lock` re-read race (key expired between the failed `SET NX` and the follow-up `GET` → treat as free, this attempt wins) is reproduced deliberately, with the same reasoning.

### Race 2: Lease approval and the actual file write are not atomic
**Location:** hook exit → harness performs the Write
**Trigger:** The hook allows, then the process is descheduled before the write lands.
**Data prerequisite:** none.
**State prerequisite:** The lease is held for the duration.
**Mitigation:** Irreducible, and harmless: the lease persists across the window, so a peer arriving mid-window is refused. The window can only produce a *delayed* write by the legitimate holder, never a concurrent one. Documented, not defended against.

### Race 3: A dispatcher dies between the liveness read and the dispatch
**Location:** `tools/sdlc_dispatch.py` liveness gate
**Trigger:** The prior dispatcher exits microseconds after the fence reads it as LIVE.
**Data prerequisite:** A `dispatcher` block on the prior record.
**State prerequisite:** none.
**Mitigation:** Refusing a dispatch is recoverable — the supervisor retries and the next read sees it dead. This is the safe direction of the race, and `--force` is the manual out. The inverse (reading a live dispatcher as dead) is prevented by the fence's unknown rule.

### Race 4: pid recycling defeats the fence
**Location:** `agent/pid_fence.py` consumers
**Trigger:** The OS reuses a dead holder's pid before the lease is evaluated.
**Data prerequisite:** Recorded `pid_create_time`.
**State prerequisite:** none.
**Mitigation:** `create_time` comparison makes a recycled pid read as a *different* process, i.e. not-live-as-recorded, i.e. claimable. That is the correct outcome. `pid_fence`'s module docstring already states this is detection, not a guarantee, and there is no pidfd on darwin — do not attempt to close it here.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2657] The resume-from-transcript supervisor forking that spawns rival incarnations of one pipeline supervisor. This plan implements only the defensive ownership check at dispatch; the harness-level cause is #2657's.
- [SEPARATE-SLUG #2628] Cross-lane pytest Redis DB flushing. Listed in the original issue's inventory for completeness only.
- [SEPARATE-SLUG #2675] Continuity re-ensure re-minting `run_id` and recreating the ledger anchor. This plan must not regress it and tests against it (Risk 3), but does not fix it.
- Consolidating the two `Write|Edit` hooks into an in-process dispatcher — see Rabbit Holes. Not deferred to a future issue; deliberately not done, because the current two-entry shape is correct and cheap enough.
- Changing the commit-plans-on-main policy itself. The policy is owner-set; this plan makes it safe, not different.

## Update System

The new hook is registered through `.claude/hooks/manifest.toml`, and `scripts/update/run.py` regenerates `.claude/settings.json` from it — so a machine picks the guard up on its next `/update` with no extra step. Three things to verify during build:

- The manifest entry is `scope = "project"`, so no hardlink into `~/.claude/hooks/` and no `~/.claude/settings.json` rewrite is involved.
- No new dependency, config file, or secret is introduced, so nothing new must propagate.
- `sdlc-tool plan-lease` is a subcommand of the existing `sdlc-tool` entry point, not a new `[project.scripts]` row, so no reinstall is needed.

Existing installations need no migration: absent lease keys simply mean every doc is free.

## Agent Integration

The agent reaches this work through two surfaces it already has:

- **`sdlc-tool plan-lease {status,release,steal}`** — a subcommand of the existing `sdlc-tool` console script, invoked via Bash. No `pyproject.toml [project.scripts]` change.
- **The hook layer** — the block reason is the integration. It is the only thing the agent sees when refused, so it must be actionable prose, not a code (covered under Failure Path Test Strategy → Error State Rendering).

Integration test: a test that shells out to `sdlc-tool plan-lease status --slug <slug>` and asserts JSON on stdout, confirming the subcommand is actually wired into the CLI's argument parser rather than merely importable.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/plan-doc-single-writer-lease.md` — the lease's identity model, the two staleness clocks, the fail-open posture and why, the dispatch gate, the commit guard, and the operator escape hatches.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/hook-manifest.md` if it enumerates registered hooks; verify rather than assume.
- [ ] Update `docs/sdlc/do-plan.md` with the `plan-lease release` invocation, placed next to the existing `meta-set plan_revising false` line so the two are read together.

### External Documentation Site
- [ ] Not applicable — no external docs site covers SDLC internals. `site/` is the public explainer and does not describe hooks.

### Inline Documentation
- [ ] `models/plan_doc_lease.py` module docstring carries the identity model and the fail-open rationale, in the style of `touch_issue_lock`'s.
- [ ] The new hook's docstring names the collision shapes it prevents with their 2026-08-07 evidence, following `validate_no_destructive_git_in_shared_checkout.py`'s precedent.

## Success Criteria

- [ ] A second agent's `Write`/`Edit` to a plan doc held by a live foreign holder is blocked, and the reason names the holder.
- [ ] A supervisor's own `Edit` to a doc its dispatched child holds is blocked (the same-`session_id`, different-`transcript_path` case).
- [ ] A lease whose holder pid is provably dead is claimed by the next writer without waiting for TTL; a lease whose fence is unknown is not.
- [ ] A `dispatch record` for a stage whose prior dispatcher is live is refused; one whose prior dispatcher is dead succeeds; one with no `dispatcher` block on the prior record succeeds.
- [ ] A `git commit` touching `docs/plans/` from the shared checkout with HEAD ≠ `main` is blocked; the same commit from a worktree, or with the override token, is not.
- [ ] `stage-marker --status completed` names dirty `docs/plans/` files on stderr and its exit code is unchanged.
- [ ] A non-plan-doc `Write` performs zero Redis calls.
- [ ] Every new guard is demonstrated red: each has a mutation that turns exactly the intended test(s) red, pasted into the PR body.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lease core)**
  - Name: `lease-builder`
  - Role: `models/plan_doc_lease.py` + the `sdlc-tool plan-lease` subcommand
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (hook layer)**
  - Name: `hook-builder`
  - Role: the `Write|Edit` lease hook, the commit-branch predicate, manifest registration
  - Agent Type: builder
  - Domain: security/untrusted-input (command-shape parsing)
  - Resume: true

- **Builder (pipeline)**
  - Name: `pipeline-builder`
  - Role: dispatch liveness gate + stage-marker dirty warning
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `lease-tester`
  - Role: mutation-checked coverage across all three surfaces
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

### 1. Confirm hook-payload agent identity (gates everything)
- **Task ID**: verify-identity
- **Depends On**: none
- **Validates**: tests/unit/test_validate_plan_doc_lease.py (create)
- **Informed By**: spike-1 (subagent transcripts are per-agent files; `sessionId` is shared)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Capture a real PreToolUse payload from inside a dispatched subagent and confirm `transcript_path` points at the subagent's own `subagents/agent-*.jsonl`, not the root session file.
- If it does not, STOP and report — Risk 2's fallback is a scope decision, not a builder's call.
- Land the identity resolver (`holder_id`, `agent_label`) with its tests as the first commit, so everything downstream builds on a verified premise.

### 2. Build the lease core
- **Task ID**: build-lease
- **Depends On**: verify-identity
- **Validates**: tests/unit/test_plan_doc_lease.py (create)
- **Assigned To**: lease-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data
- **Parallel**: false
- `touch_plan_doc_lease` with acquire/renew/peek in one entry point, modeled on `models/session_lifecycle.py::touch_issue_lock`.
- Two staleness clocks: `PLAN_DOC_LEASE_TTL_SECONDS` (default 900, env-overridable, commented as provisional) and the `agent/pid_fence.py` fence, with unknown never authorizing an early claim.
- Fail open on every Redis exception, logging the error class.
- `sdlc-tool plan-lease {status,release,steal}`.

### 3. Build the write-time guard
- **Task ID**: build-hook
- **Depends On**: build-lease
- **Validates**: tests/unit/test_validate_plan_doc_lease.py, tests/unit/test_hook_manifest.py, tests/unit/test_hooks_audit.py
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- `validate_plan_doc_lease.py`: judge the path the tool named, never content (`8a9137002`). Non-plan paths return before importing the lease module.
- Block reason names slug, holder label, holder age, and the `sdlc-tool plan-lease status` command.
- Register one `[[hook]]` entry, `matcher = "Write|Edit"`, `exit_policy = "deny-only"`, `scope = "project"`; regenerate `settings.json` from the manifest — never hand-edit it.

### 4. Build the commit-branch guard
- **Task ID**: build-commit-guard
- **Depends On**: none
- **Validates**: tests/unit/test_validate_plan_commit_on_main.py (create), tests/unit/test_pre_tool_use_dispatcher.py
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Domain**: security/untrusted-input
- **Parallel**: true
- `is_plan_commit_off_main` in `hook_utils/`, wired as predicate 10 in `dispatch/pre_tool_use_bash.py`, fail-open like its neighbors.
- Command-position anchored, per `validate_no_destructive_git_in_shared_checkout.py`: `git commit -m "move plan to main"` must not fire.
- Covers `-a`, explicit pathspecs, and the already-staged case. Never fires inside `.worktrees/` or a foreign repo. Inline override `# allow-plan-commit-off-main`.

### 5. Build the dispatch liveness gate
- **Task ID**: build-dispatch-gate
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_dispatch.py, tests/unit/test_sdlc_router_oscillation.py
- **Informed By**: spike-2 (records carry no liveness today; the change is additive)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Additive `dispatcher` block on the dispatch record. **The stage snapshot must not include it** — G4 compares snapshots, and a per-dispatch-unique field would silently kill oscillation detection. Assert this in a test.
- Refuse `DISPATCH_LIVE` only on LIVE + no newer `completed` marker; allow on dead and on pre-cutover records. `--force` prints what it overrode.
- Test that a `run_id` change between records does not clear the refusal (Risk 3).

### 6. Add the stage-boundary dirty warning
- **Task ID**: build-dirty-warning
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_stage_marker.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- `completed` path only, after a successful write, against the shared toplevel.
- Warn on stderr, one line per dirty file. Cannot change exit code or JSON — same posture as the module's existing telemetry wrapper.

### 7. Write the loser-side protocol into the skills
- **Task ID**: build-skill-guidance
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- `do-build` and `do-sdlc`: on detecting a collision — diff your work against the peer's HEAD, stash anything genuinely unique, stand down **without mutating stage state**, and report. Cite the #2629 run, where this exact sequence cost only wasted compute.
- `do-sdlc` step 3b: verify ledger ownership (run_id + liveness) before dispatching, referencing #2657 for why a supervisor can be wrong about owning its own run.
- `do-plan` / `do-plan-critique`: release the lease at the end of a revision pass, beside `meta-set plan_revising false`.
- Keep the bodies generic and the repo specifics in `docs/sdlc/`, per the skill-context convention.

### 8. Mutation-checked test pass
- **Task ID**: test-all
- **Depends On**: build-hook, build-commit-guard, build-dispatch-gate, build-dirty-warning
- **Assigned To**: lease-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- For each guard, produce a mutation that turns exactly the intended tests red, and record which tests and how many. Measured numbers only — the #2669 PR body had to be corrected three times for remembered ones.
- Explicitly cover: zero Redis calls on a non-plan write; fail-open under a raising Redis; unknown-fence not claiming early.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: test-all
- **Assigned To**: lease-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/plan-doc-single-writer-lease.md` + README index row.
- `docs/sdlc/do-plan.md` lease-release invocation; verify `docs/features/hook-manifest.md` rather than assuming.

### 10. Final validation
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
| Dispatch-gate tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_dispatch.py tests/unit/test_sdlc_router_oscillation.py -q` | exit code 0 |
| Stage-marker tests | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| Hook wiring tests | `scripts/pytest-clean.sh tests/unit/test_hook_manifest.py tests/unit/test_hooks_audit.py tests/unit/test_pre_tool_use_dispatcher.py -q` | exit code 0 |
| Hook registered from manifest | `grep -c validate_plan_doc_lease .claude/settings.json` | output > 0 |
| settings.json is generated, not hand-edited | `python -m scripts.update.hooks --check` | exit code 0 |
| Lease CLI is actually wired | `sdlc-tool plan-lease status --slug nonexistent-slug` | exit code 0 |
| TTL is a named constant, not a literal | `grep -rn "PLAN_DOC_LEASE_TTL_SECONDS" models/plan_doc_lease.py` | output > 0 |
| No bare 900 literal in the lease module | `grep -c "900" models/plan_doc_lease.py` | match count == 0 |
| Anti-criterion: snapshot must not carry dispatcher | `grep -c '"dispatcher"' <(python -c "import inspect,agent.sdlc_router as r; print(inspect.getsource(r.build_stage_snapshot))")` | match count == 0 |
| Anti-criterion: lease never touches raw Redis on Popoto keys | `grep -cE "\.delete\(|\.srem\(|\.sadd\(|\.zrem\(" models/plan_doc_lease.py` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Fail-open is the plan's default (Risk 1). Confirm.** A Redis outage means no plan-doc protection at all, silently, for the duration. The alternative — refusing plan writes when the lease store is unreachable — turns a Redis hiccup into "nobody can plan." I have chosen fail-open to match `touch_issue_lock`, but this is the one call where the wrong choice is expensive in both directions.
2. **If spike-1's fallback triggers (Risk 2), ship the lane-level lease or stop?** If `transcript_path` turns out not to be per-subagent, the supervisor-vs-own-child clause of AC 1 is unimplementable as designed. A lane-level lease still closes shapes 1, 3 and 5 — most of the observed damage — but would leave AC 1 partially unmet. Ship the partial and say so, or hold the whole thing?
3. **Should `plan-lease steal` require a reason string that gets logged?** It is the one operation that can destroy a peer's work window. A mandatory `--reason` costs nothing and makes the audit trail readable, but adds friction to the one path used when something has already gone wrong.
