---
status: Spike
type: investigation
appetite: Small
owner: Valor Engels
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1228
parent_plan: docs/plans/sdlc-1228.md
---

# Shared-State Spike: What state do sibling PM sessions actually share?

## Purpose

The original `worker_key` design (PR #828) chose `project_key` for PM sessions with the rationale "multiple PM sessions in the same project racing would cause git conflicts and state corruption." PR #1087 reaffirmed that decision and explicitly excluded PM from slug routing. Issue #1228 revisits this choice because slugged PM siblings now have isolated worktrees — the original premise (PMs all share the main checkout) no longer holds.

This spike enumerates **every** Redis key, file path, and process-level resource that two PM sessions for the same `project_key` could contend on, then judges each contender as either:

- **Isolated-by-slug** — already safe; no change needed.
- **Genuinely shared** — needs a mitigation, a lock, or blocks the parallelism work.
- **Shared but idempotent** — concurrent writes are safe by construction (e.g., `setex` of the same value).

The spike output is a written verdict: green (proceed to phased implementation in `sdlc-1228.md`), amber (proceed with named mitigations), or red (no-go decision document).

## Scope

Compare two hypothetical concurrent PM sessions, both for `project_key="valor"`, with distinct slugs `sdlc-A` and `sdlc-B`, distinct `.worktrees/sdlc-A/` and `.worktrees/sdlc-B/` worktrees, and distinct branches `session/sdlc-A` and `session/sdlc-B`.

**Out of scope:**
- Two PM sessions with the **same** slug (must continue to serialize — that race is real and unavoidable).
- Two PM sessions where one is slugless (the original PR #828 invariant — slugless PMs touch the main checkout and must serialize on `project_key`).
- Dev sessions (covered by PR #1087 — slug-keyed parallelism already works).

## Method

For each candidate resource, the spike must answer four questions:

1. **Read or write?** Is the resource read-only (concurrent reads are safe) or written-to?
2. **Per-slug or per-project?** Does the key/path embed the slug, or is it shared across the project?
3. **Idempotent?** If two sessions write concurrently, is the result deterministic (same value) or last-write-wins (data loss possible)?
4. **Observable failure mode?** What would a race produce — a git conflict, a lost Redis write, a stale dashboard counter, a corrupted memory record?

## Audit Checklist

The spike's output (committed to this file, replacing this section) must complete the table for every entry below.

### A. Filesystem

- [ ] **Main checkout (`/Users/valorengels/src/ai/`)** — slugged PMs operate in worktrees, but do any code paths still `cwd` into the main checkout? Search: `grep -rn "os.chdir\|cwd=" agent/ tools/ bridge/ --include="*.py"`.
- [ ] **`.worktrees/{slug}/`** — verified per-slug. Confirm no shared symlink under both worktrees points back into the main repo's mutable state.
- [ ] **`docs/plans/{slug}.md`** — written by `/do-plan` on `main`, not in the worktree. Two PM siblings working on **different** slugs touch different files. Verify the commit-and-push step is sequenced through git's own locking, not racing on the index.
- [ ] **`docs/plans/{slug}-spike.md` and other plan sub-docs** — same as above.
- [ ] **`logs/worker.log`, `logs/bridge.log`** — append-only by separate processes. Concurrent appends from two PM sessions are line-interleaved but not corrupting (POSIX `O_APPEND` semantics).
- [ ] **`/tmp/` scratchpad files** — PM sessions sometimes write `/tmp/*.md` for sub-agent prompts. Verify each session uses a unique filename (slug or session_id).
- [ ] **`.claude/settings.local.json`** — read-only at session creation; verify no per-session writes during a PM run.
- [ ] **`config/projects.json` (vault symlink)** — read-only.

### B. Git Operations

- [ ] **Commits to `main`** — only `/do-plan` and `/do-merge` write to `main`. `/do-plan` runs early (before the parent spawns siblings); `/do-merge` is the SDLC final stage and is generally invoked one-at-a-time per PR. **Concurrent `git commit` on `main` from two worktrees is the highest-risk contender** — the local clone has a single `.git/index` lock. Investigate: do worktrees share `.git/index`? (Answer expected: no — each worktree has its own index, but they share `.git/HEAD` for the **main** checkout, and `git push origin main` from two worktrees can race on the remote ref.)
- [ ] **`git fetch origin main`** — both PMs may fetch concurrently. Concurrent fetches into the same `.git/` directory share the object store and the packed-refs file; verify git's internal locking handles this.
- [ ] **`git push origin session/{slug}`** — per-slug branches; no contention.
- [ ] **`git push origin main`** — two PMs both completing `/do-merge` concurrently could race on the remote ref. Mitigation: `git push` retries with `--force-with-lease` are unsafe; rely on remote rejection + retry instead.
- [ ] **Worktree creation/teardown** — `agent/worktree_manager.py` writes to `.git/worktrees/{slug}/`. Each slug has its own subdirectory; verify no shared lockfile under `.git/worktrees/`.

### C. Redis Keys (Popoto-managed)

For each, list the key shape, who writes, and whether two PM siblings for the same project would contend.

- [ ] **`AgentSession:{session_id}` hashes** — per-session, no contention.
- [ ] **`AgentSession:$Index:project_key:{project_key}`** — Popoto secondary index. Two siblings adding/removing from the same set are safe (Redis SADD/SREM are atomic).
- [ ] **`AgentSession:$Index:slug:{slug}`** — per-slug.
- [ ] **`AgentSession:$Index:status:{status}`** — shared across the entire keyspace. Atomic SADD/SREM; safe.
- [ ] **`Memory:{memory_id}` and indexes** — memory writes go through Popoto. Two PMs writing to the **subconscious memory store** for the same project: are memory IDs deterministic or per-session? Verify `tools/memory_search.py` and the post-session extraction path.
- [ ] **`telegram:outbox:{session_id}`** — per-session, no contention.
- [ ] **`email:outbox:{session_id}`** — per-session.
- [ ] **`bridge:project:{project_key}:*`** — any project-keyed bridge state? Search: `grep -rn 'project_key' bridge/ --include="*.py"`.
- [ ] **`worker:_send_callbacks` / `worker:_reaction_callbacks`** — process-local Python dicts in `agent/agent_session_queue.py`, not Redis. Read-only after registration; safe.
- [ ] **Dashboard counters (`dashboard:*`, `stats:*`)** — cumulative counts. INCR is atomic; safe.
- [ ] **Reflection / autoexperiment state** — any `reflections:*` keys keyed by project? Search: `grep -rn '"reflections:' --include="*.py"`.
- [ ] **`bloom:*` (existence filter for memory)** — append-only bloom filter. Concurrent inserts are safe.

### D. Process-Level / In-Memory

- [ ] **Worker process (`worker/__main__.py`)** — single worker process per machine, multiple worker loops by `worker_key`. Each loop is its own asyncio task. No shared mutable Python state between worker loops other than:
  - `_active_clients` dict (`agent/sdk_client.py`) — keyed by `session_id`, no contention.
  - `_global_session_semaphore` — global cap of 8. Two PM siblings both holding a slot is fine until they each spawn dev children (see #1004 interaction below).
- [ ] **Anthropic API client / rate-limit semaphore** — `agent/anthropic_client.py::semaphore_slot()`. Shared across all sessions. Two PM siblings making concurrent Haiku/Opus calls is the intended throttling — no correctness issue.
- [ ] **Telegram session lock** — the bridge holds the Telegram client; PM sessions write to the outbox, the bridge reads. No contention.
- [ ] **Hooks (`.claude/hooks/`)** — per-session via `CLAUDE_CODE_TASK_LIST_ID`. Verify no shared file under `.claude/hooks/` is written during a PM run.

### E. SDLC Pipeline State

- [ ] **`stage_states` per session** — keyed by `session_id` via `session_events`. Per-session, no contention.
- [ ] **`sdlc-tool stage-marker` and `stage-query`** — verify keys embed `issue_number` (per-issue, not per-project). Two PMs working on different issues for the same project: no contention.
- [ ] **Critique/review verdicts** — keyed by `(stage, issue_number)`. Per-issue.
- [ ] **GitHub issue label state (`plan`, `in-progress`)** — written via `gh` CLI. Two PMs labeling different issues: no contention. Two PMs labeling the same issue is impossible (same-slug case, excluded from this spike).

### F. The #1004 PM↔Dev Deadlock Interaction

This is the **non-state-sharing** risk the spike must judge:

- Global cap: `MAX_CONCURRENT_SESSIONS=8`.
- 3 PM siblings × 1 dev child each = 6 slots. Safe.
- 3 PM siblings × 2 dev children each = 9 slots. **Deadlock risk** — each PM holds a slot, waiting for its dev child, but no slot is available for the dev to start.
- Mitigation options to evaluate (decision lives in the main plan):
  - (a) Reserve N slots for dev children (e.g., `MAX_CONCURRENT_PM_SESSIONS=4` of the 8).
  - (b) PM sessions release their slot while waiting for a dev child (`waiting_for_children` state already exists per `docs/features/session-lifecycle.md`).
  - (c) Document a soft cap on PM siblings the parent is allowed to spawn (e.g., 3) and rely on the global cap headroom.

## Verdict Format

The spike report must end with one of three verdicts:

### Verdict: GREEN
> All shared-state contenders are isolated-by-slug or shared-but-idempotent. The 4-line `worker_key` change is safe. Proceed to Phase 1 of `sdlc-1228.md`.

### Verdict: AMBER
> Some contenders are genuinely shared but mitigable. List the mitigations as Phase 0.5 in `sdlc-1228.md` (e.g., serialize `git push origin main` via a per-project advisory lock; cap concurrent PM siblings at N to avoid the #1004 deadlock).

### Verdict: RED
> One or more contenders cannot be made safe without a larger architectural change. The plan converts to a no-go decision document with the named blocker(s).

## Spike Output Sections (to be filled by builder)

The builder running the spike replaces the placeholder `[TBD]` blocks below with concrete findings.

### Findings — Filesystem

[TBD — to be filled by spike execution]

### Findings — Git Operations

[TBD]

### Findings — Redis Keys

[TBD]

### Findings — Process-Level

[TBD]

### Findings — SDLC Pipeline State

[TBD]

### Findings — #1004 Interaction

[TBD]

### Verdict

[TBD — GREEN / AMBER / RED with one-paragraph justification]

### Recommended Phase 0.5 Mitigations (if AMBER)

[TBD]

## Acceptance for the Spike Itself

- [ ] Every checkbox in §Audit Checklist resolved with one of: `[isolated-by-slug]`, `[shared-but-idempotent]`, `[shared — needs mitigation: ...]`, `[shared — blocker]`.
- [ ] Verdict line is unambiguous (one of GREEN / AMBER / RED).
- [ ] Each "shared — needs mitigation" entry has a named, implementable mitigation OR is reclassified as a blocker.
- [ ] The spike report is committed to this file (replacing the placeholders) and linked from `sdlc-1228.md`.

## Success Criteria

- [ ] Every contender in §Audit Checklist is classified.
- [ ] The verdict (GREEN/AMBER/RED) is set and justified in one paragraph.
- [ ] If AMBER, each mitigation is named, owned, and sequenced into Phase 0.5 of `sdlc-1228.md`.
- [ ] If RED, the named blocker is captured as a no-go decision in `sdlc-1228.md` with a one-line summary suitable for the issue comment.
- [ ] The parent plan `docs/plans/sdlc-1228.md` cross-links this file and gates Phase 1 on the verdict being GREEN or AMBER.

## No-Gos

- This spike is **investigation only** — no code changes, no test writes, no `worker_key` modification. Proposals land in `sdlc-1228.md`.
- Do not propose a fix that breaks slugless-PM serialization (PR #828 invariant).
- Do not propose a fix that allows two PM sessions with the same slug to run concurrently.

## Update System

No update system changes required — this is a documentation-only investigation artifact. The spike runs in the local repo; no deploy, no migration, no env-var, no `scripts/update/` change.

## Agent Integration

No agent integration required — this spike produces a written investigation report, not a runtime feature. The agent does not invoke the spike at runtime.

## Failure Path Test Strategy

This is an investigation, not code. The "failure path" for the spike is producing an inconclusive or unsafe verdict. Mitigation:

- Each table cell must resolve to one of the four named classifications. An empty cell or "unsure" is itself a blocker (RED) — the spike must not advance Phase 1 on incomplete data.
- The verdict justification paragraph must name the highest-risk contender and explain why it falls into its category. If the highest-risk contender is "unknown," the verdict is RED.
- A subsequent reviewer (the critique stage) should be able to falsify the verdict by pointing to a specific resource the audit missed; such a finding triggers a re-spike, not a Phase 1 advance.

## Test Impact

No existing tests affected — this is an investigation artifact (a written report inside `docs/plans/`), not a code change. Tests for the eventual `worker_key` behavior change are tracked in the parent plan `docs/plans/sdlc-1228.md` under its own `## Test Impact` section.

## Rabbit Holes

- **"Audit every Redis key in the codebase"** — out of scope. Limit to keys touched by PM sessions during a typical SDLC run (plan→critique→build→test→patch→review→docs→merge). Keys touched only by the bridge, the watchdog, the email relay, or analytics rollups are out of scope unless the audit surfaces a path-specific reason.
- **"Re-derive the original PR #828 / #1087 reasoning from scratch"** — read the PR bodies, treat them as a strong prior, and only refute specific claims with evidence. Do not re-litigate the original decision in the abstract.
- **Designing the mitigation in the spike** — the spike names the contender; the mitigation design lives in the parent plan. Stop at "here's what's shared, here's the suggested mitigation shape."
- **"Performance modeling under parallel load"** — out of scope. The spike is correctness-focused. Throughput/latency measurements live with the parent plan's wall-time acceptance criterion.

## Documentation

No documentation changes shipped from this spike file alone — this file IS the documentation artifact. Cross-linking lives in the parent plan:

- [ ] Parent plan `docs/plans/sdlc-1228.md` links this spike from its §Solution and §Phases sections.
- [ ] If the verdict is GREEN or AMBER, `docs/features/bridge-worker-architecture.md` will be updated as part of the parent plan (not this spike).
- [ ] If the verdict is RED, the no-go decision is recorded in the parent plan; this file becomes the supporting evidence.
