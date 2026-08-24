---
status: Cancelled
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1541
last_comment_id:
---

# Conversational Dev Session Fan-Out (per-project cap)

## Problem

When several dev questions arrive in one project's Telegram chat, they **serialize behind each other and behind the PM session**, so the chat goes silent for long stretches even though the worker is healthy and global concurrency slots are free.

Root cause is timing. `AgentSession.worker_key` (`models/agent_session.py:500-503`) returns `project_key` for any dev session without a slug, so every slugless ("conversational") dev session in a project funnels through a single serial `_worker_loop`. The worktree isolation that would make parallelism safe **already exists** — issue #1272 / PR #1280 forces every dev session, including conversational ones, into its own `.worktrees/{slug}/` worktree by synthesizing a slug `dev-{aid[:8]}`. But that slug is allocated at **execution time** (`agent/session_executor.py:781-792`), long after `worker_key` was read at **enqueue/routing time** (`agent/agent_session_queue.py:366-385`, `:1127-1138`). By the time the worktree exists, the session is already bound to the `project_key` loop. Each session gets its own worktree, yet they still run one-at-a-time.

**Current behavior:**
- A project's conversational dev sessions all route to `worker_key=project_key` and run strictly serially.
- A single wedged/slow dev or PM session starves every queued dev question in that project (observed: a Dev chat with 4 questions queued, the oldest pending >27h, behind a PM wedged on one 29-minute SDK turn).
- Each session nonetheless already runs in an isolated worktree (#1272), so the serialization is conservative beyond what isolation requires.

**Desired outcome:**
- Up to **3 dev sessions per project** run **concurrently**, each in its own worktree, instead of one-at-a-time.
- A wedged/slow dev no longer blocks sibling dev questions in the same project.
- A busy project cannot consume all global slots and starve other projects — concurrency is **capped per project**, not just globally.

## Freshness Check

**Baseline commit:** `8cc68d3f0d33df08013bb2dbf644f87296d9e4d6`
**Issue filed at:** 2026-06-01T09:21:58Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py:500-503` — slugless dev `worker_key` returns `project_key` — still holds (issue cited 472-503; the `worker_key` property spans 471-503, the slugless-dev branch is 500-503).
- `agent/session_executor.py:781-792` — execution-time synthetic slug `dev-{aid[:8]}` — still holds. Worktree force-on at `:814-816`. Cleanup regex `^dev-[0-9a-f]{8}$` at `:2078`.
- `agent/agent_session_queue.py:366-385` — inline `worker_key` recompute for the notify publish ("KEEP IN SYNC") — still holds.
- `agent/agent_session_queue.py:1127-1138` — second inline `worker_key` recompute in `enqueue_agent_session` for `_ensure_worker` — still holds.
- `worker/__main__.py:181-184` — `MAX_CONCURRENT_SESSIONS` default 8 — still holds.
- `agent/sdlc_router.py:65` — `MAX_PARALLEL_DEVS = 3` (decompose-only) — still holds.
- `models/agent_session.py:716-717` — `agent_session_id` popped at construction (AutoKeyField) — still holds. `async_create` returns the saved instance (`:815`).

**Cited sibling issues/PRs re-checked:**
- #1272 — CLOSED. Synthetic-slug-at-execution + CLI symmetry. This plan moves its allocation earlier.
- PR #1280 — MERGED 2026-05-05. The implementation of #1272.
- #887 — CLOSED. Parallel-session main-checkout contamination guard. The guard relies on dev sessions having a slug — early allocation strengthens it.
- #828 — invariant ("slugless PMs serialize per `project_key`") intact and explicitly preserved here.

**Commits on main since issue was filed (touching referenced files):** None since filing (issue filed same day as baseline). Recent prior commits to these files (#1459 `clean_indexes`, #1377 stageless-dev / branch-mismatch follow-ups) do not touch `worker_key` or synthetic-slug logic.

**Active plans in `docs/plans/` overlapping this area:** None. The `worker-*` plans concern lifecycle/hibernation, not routing fan-out. `worktree-parallel-sdlc.md` covers SDLC build fan-out (decompose), a sibling concern noted under Risks.

**Notes:** The decisive correction vs. the issue's "allocate at enqueue" sketch: `agent_session_id` does not exist until `async_create` saves the row, so allocation must occur **immediately after** `async_create` and **before** `worker_key` is read for routing — not strictly before enqueue.

## Prior Art

- **Issue #1272 / PR #1280**: Synthesize `dev-{aid[:8]}` at execution time so every dev session gets a worktree. Succeeded for isolation; left the routing-timing gap this plan closes.
- **Issue #887**: Guard against parallel-session main-checkout contamination. Closed. Establishes that dev-session worktree isolation is mandatory — the precondition that makes fan-out safe.
- **Issue #1085**: "worker_key for slugged dev sessions should prefer slug over chat_id." Shows `worker_key` has been iteratively refined; confirms the slug-keyed routing path is the intended isolation mechanism.
- **Issue #828**: Established the "slugless PMs serialize per project_key" invariant. This plan preserves it unchanged.
- No prior attempt to add a runtime per-project concurrency cap was found — `MAX_PARALLEL_DEVS` is a decompose-time gate, not a runtime semaphore.

## Architectural Impact

- **New dependencies**: None. Reuses `asyncio.Semaphore` and the existing Popoto model.
- **Interface changes**: `_push_agent_session` returns the effective `worker_key` (in addition to queue depth) so callers route on the persisted slug rather than recomputing it. A new idempotent model helper `AgentSession.ensure_dev_slug()`. A new module-level `_project_dev_semaphores` dict in `agent/session_state.py`.
- **Coupling**: **Decreases** `worker_key` duplication. Today `worker_key` logic is triplicated (the property + two inline "KEEP IN SYNC" recomputes). Persisting the slug before routing lets both routing chokepoints read `instance.worker_key` (the single source of truth) instead of re-deriving it.
- **Data ownership**: The synthetic slug becomes a **persisted** field on the `AgentSession` row at creation time, not an execution-time local. The model owns slug synthesis via `ensure_dev_slug()`.
- **Reversibility**: High. Setting `MAX_CONCURRENT_DEVS_PER_PROJECT=1` restores per-project serialization for dev sessions. Reverting the slug-allocation commit returns to execution-time synthesis (the existing fallback path remains).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (1 alignment check), code reviewer (1 round)

**Interactions:**
- PM check-ins: 1 (confirm the per-project cap covers all dev sessions, not just conversational — see Open Questions)
- Review rounds: 1

The coding surface is small (two functions + one model helper + one semaphore dict). The bottleneck is getting the concurrency ordering provably deadlock-free and confirming the cap's scope.

## Prerequisites

No prerequisites — this work has no external dependencies. The optional env var `MAX_CONCURRENT_DEVS_PER_PROJECT` defaults to 3 when unset.

## Solution

### Key Elements

- **`AgentSession.ensure_dev_slug()`** (new model helper): idempotent. If `session_type == "dev"` and `slug` is unset and `id` is present, set `slug = f"dev-{id[:8]}"`, persist, and return it; otherwise return the current `slug` unchanged. Single source of truth for synthetic-slug synthesis.
- **Early allocation at creation**: `_push_agent_session` captures the instance returned by `async_create`, calls `ensure_dev_slug()`, then reads `instance.worker_key` for the notify publish and returns the effective `worker_key` to `enqueue_agent_session` for `_ensure_worker`. Both routing chokepoints now read the persisted property instead of re-deriving.
- **Execution-time fallback retained**: `agent/session_executor.py` calls the **same** `ensure_dev_slug()` helper (idempotent — a no-op when the slug is already set). This removes the bespoke synthesis block while keeping a safety net for any dev session created through a path that bypasses `_push_agent_session`.
- **Per-project dev cap**: a `_project_dev_semaphores: dict[str, asyncio.Semaphore]` keyed by `project_key`, each sized `max(1, int(os.environ.get("MAX_CONCURRENT_DEVS_PER_PROJECT", "3")))`. A dev `_worker_loop` acquires its project's dev slot **before** the global semaphore, releasing in reverse order.

### Flow

Telegram message (conversational dev question) → bridge enqueues dev session → `async_create` saves row (AutoKey `id` assigned) → `ensure_dev_slug()` sets `slug=dev-{id[:8]}` and persists → `worker_key` now returns the slug → `_ensure_worker(slug)` starts a **dedicated** loop → dev loop acquires per-project dev slot → acquires global slot → pops & executes in `.worktrees/dev-{id[:8]}/` → releases global → releases per-project dev slot → post-run worktree cleanup (existing #1272 path).

Three fresh same-chat questions → three distinct slugs → three distinct loops → up to 3 run concurrently (cap), 4th waits for a per-project slot even if global slots are free.

### Technical Approach

1. **`ensure_dev_slug()` on the model.** Centralize the `dev-{id[:8]}` synthesis. Idempotent and safe to call from both creation and execution. The `id[:8]` of a 32-char hex AutoKey is lowercase hex, so the synthesized slug matches the existing cleanup regex `^dev-[0-9a-f]{8}$` (verified: a live `id` is `239d04fcf069...` → `dev-239d04fc`).

2. **Capture and route on the persisted slug.** In `_push_agent_session`, change `await AgentSession.async_create(...)` to `created = await AgentSession.async_create(...)`, call `ensure_dev_slug()` (off-thread, like the other Popoto writes), then replace the inline notify `worker_key` block (`:366-385`) with `_wk = created.worker_key`. Return `(depth, created.worker_key)` (or expose the worker_key via a small struct) so `enqueue_agent_session` uses it for `_ensure_worker`, replacing the second inline block (`:1127-1138`). Both "KEEP IN SYNC" duplications are deleted — the property becomes the only `worker_key` authority.

3. **Replace executor synthesis with the helper.** In `agent/session_executor.py:781-792`, call `slug = session.ensure_dev_slug()` instead of the bespoke `dev-{aid[:8]}` block. Keep the `is_synthetic_slug` worktree-forcing logic (`:814-816`) gated on the `dev-` prefix so behavior is unchanged for sessions that reach execution slugless.

4. **Per-project dev cap, acquired before the global slot.** Add `_project_dev_semaphores` to `agent/session_state.py` with a lazy accessor `get_project_dev_semaphore(project_key)`. In `_worker_loop`, before acquiring the global semaphore, **peek** the next pending session for this `worker_key` (the same cheap `AgentSession.query.filter(...)` the loop already runs for `_has_pending`). If it is a dev session, acquire `get_project_dev_semaphore(session.project_key)` first; then the global semaphore; then the real pop. Each dev session now has a unique `worker_key` (its slug), so the loop is a single consumer — the peeked session is the one popped, no TOCTOU. Release the global slot, then the per-project dev slot, in the existing `finally`.

   **Why before the global slot:** if a dev loop acquired the global slot first and then blocked on a full per-project cap, it would pin a global slot while idle — exactly the global-pool monopolization the issue forbids. Acquiring per-project-dev → global (consistent order, dev sessions only) keeps global slots free for other projects and is deadlock-free (non-dev loops never take the per-project semaphore, so no lock-order inversion).

5. **Cap scope.** The per-project dev semaphore counts **all** `session_type == "dev"` sessions for a `project_key`, including slugged SDLC build sub-devs. It composes with `MAX_PARALLEL_DEVS` (decompose-time fan-out, default 3) as a floor-of-the-two: a 3-way build fan-out exactly fills a cap of 3. Defaults align at 3 so existing build behavior is unaffected on a project not also running conversational devs. (Surfaced in Open Questions for confirmation — if build devs should be exempt, gate the acquire on `slug` shape.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ensure_dev_slug()` must never raise into the create path. The persist call is wrapped; on failure it logs `logger.warning` and returns `None` (session falls back to `project_key` routing — the pre-fix behavior, not a crash). Test asserts the warning is logged and routing degrades gracefully.
- [ ] The peek/semaphore acquire in `_worker_loop` must not swallow errors silently — a failed peek logs and falls through to the existing pop path (no per-project cap applied that iteration). Test asserts observable log + that the session still runs.
- [ ] The existing synthetic-slug cleanup `except Exception` (`session_executor.py:2095`) is unchanged; its non-fatal-warning behavior is covered by existing tests.

### Empty/Invalid Input Handling
- [ ] `ensure_dev_slug()` with `id=None` (row not yet saved) returns `None` without persisting — test the pre-save guard.
- [ ] `ensure_dev_slug()` on a non-dev session (PM/teammate) returns the existing slug untouched — test PM and teammate inputs are no-ops.
- [ ] `MAX_CONCURRENT_DEVS_PER_PROJECT` unset / empty / `"0"` / non-numeric → clamps to `max(1, ...)` with a warning, mirroring the global-semaphore clamp. Test each case.

### Error State Rendering
- [ ] No new user-visible output. The user-visible effect is *faster* chat responses; verify via the integration test that three concurrent dev sessions deliver three responses without one blocking another. Failure mode (a wedged head-of-line session) must not delay siblings — assert sibling completion while one session is artificially stalled.

## Test Impact

- [ ] `tests/unit/test_agent_session_worker_key.py` (or the existing `worker_key` unit test, confirm exact path at build) — UPDATE: add cases asserting a slugless dev session, once `ensure_dev_slug()` has run, returns the slug from `worker_key`; assert PM/teammate routing is unchanged.
- [ ] `tests/` synthetic-slug coverage for #1272 (search `dev-` / `synthetic-slug` markers) — UPDATE: assert the slug is now persisted at creation and that the executor helper call is idempotent (no double-synthesis, same slug value).
- [ ] Worker-loop / semaphore tests (search `MAX_CONCURRENT_SESSIONS`, `_global_session_semaphore`) — UPDATE/REPLACE: add a test that a 4th dev session in one project waits while 3 run, and that a dev session in another project is unaffected (global pool not monopolized).
- [ ] `enqueue_agent_session` / `_push_agent_session` tests (notify-publish payload, `_ensure_worker` routing) — UPDATE: assert the returned/published `worker_key` equals the persisted slug for dev sessions and `project_key`/`chat_id` for PM/teammate.

Justification for any "no existing test" gaps: the cap mechanism is new runtime behavior with no prior coverage; new tests are required (greenfield for the semaphore, additive for the routing).

## Rabbit Holes

- **Refactoring the whole `worker_key` triplication into a shared module.** Deleting the two inline recomputes by reading `instance.worker_key` is in scope and sufficient. Do not redesign the notify/pubsub payload schema or the `_ensure_worker` signature beyond passing the already-computed key.
- **Evicting wedged sessions.** This issue increases throughput; it does **not** kill a hung session. A per-turn stall watchdog is a separate concern (see No-Gos). Do not build timeout/eviction here.
- **PM fan-out.** Tempting to parallelize PMs too. Out of scope — preserves the #828 invariant.
- **Per-chat ordering guarantees.** Reply-to already resumes the original session (serial where order matters); fresh messages intentionally fan out. Do not build a per-chat ordering queue.
- **Dynamic/elastic per-project caps.** A static env-configured semaphore is the appetite. No adaptive sizing based on load.

## Risks

### Risk 1: Per-project cap interacts with SDLC build fan-out
**Impact:** If the cap covers all dev sessions, a 3-way decompose build fills the cap, so conversational dev questions in that project wait until the build's devs free slots (and vice versa).
**Mitigation:** Defaults align (`MAX_CONCURRENT_DEVS_PER_PROJECT=3 == MAX_PARALLEL_DEVS=3`), so a build that already self-limits to 3 fits exactly; no regression for build-only projects. Surfaced in Open Questions — if Tom wants conversational devs to have a separate budget, gate the acquire on slug shape (synthetic `dev-` prefix) instead of `session_type`. Documented either way.

### Risk 2: Deadlock or global-slot starvation from acquisition ordering
**Impact:** A dev loop holding a global slot while blocked on a full per-project cap would pin global slots and could starve other projects — the exact failure the issue forbids.
**Mitigation:** Strict acquisition order (per-project-dev **before** global, dev sessions only) with reverse release. Non-dev loops never touch the per-project semaphore, so there is no lock-order inversion. Covered by the "other project unaffected" integration test.

### Risk 3: Slug not persisted before `worker_key` is read (the original bug, reintroduced)
**Impact:** If `ensure_dev_slug()` runs after the notify publish or after `_ensure_worker`, routing reverts to `project_key` and fan-out silently fails.
**Mitigation:** `ensure_dev_slug()` is called inside `_push_agent_session` immediately after `async_create` and before the notify publish; `_push` returns the effective key so `enqueue_agent_session` cannot re-derive a stale one. The unit test asserts the published payload's `worker_key` equals the slug.

## Race Conditions

### Race 1: Read-after-write on the synthetic slug between creation and routing
**Location:** `agent/agent_session_queue.py` `_push_agent_session` (`:305-385`) and `enqueue_agent_session` (`:1127-1138`)
**Trigger:** The worker (or the notify listener) reads `worker_key` for routing before the slug is persisted.
**Data prerequisite:** `slug = dev-{id[:8]}` must be written to the row before any `worker_key` read for routing.
**State prerequisite:** `id` (AutoKey) must be assigned — guaranteed after `async_create` returns.
**Mitigation:** `ensure_dev_slug()` is awaited (off-thread) before the notify publish and before `_push` returns the key. Routing reads the returned/persisted key, never a pre-allocation snapshot.

### Race 2: Two loops both observe a free per-project dev slot (over-admission)
**Location:** `agent/agent_session_queue.py` `_worker_loop` admission gate (new code, near `:1225-1234`)
**Trigger:** Concurrent dev loops for the same project race on the per-project semaphore.
**Data prerequisite:** The per-project `asyncio.Semaphore` must exist before both loops check it.
**State prerequisite:** Single-threaded asyncio event loop — semaphore acquire is atomic; lazy creation via `dict.setdefault` is race-free within the loop.
**Mitigation:** `asyncio.Semaphore` enforces the cap atomically; the lazy accessor uses `setdefault` so concurrent first-touch yields one semaphore. No count is derived from a separate query.

### Race 3: Peek/pop TOCTOU on the admission gate
**Location:** `_worker_loop` peek-then-pop for the dev admission path.
**Trigger:** The session classified by the peek differs from the one popped.
**Data prerequisite:** The peeked session is the session that will be popped.
**State prerequisite:** Each dev `worker_key` is a unique slug → the loop is the sole consumer of that key.
**Mitigation:** Single-consumer-per-`worker_key` invariant (synthetic and real slugs are unique) makes peek and pop see the same session. The existing Redis pop lock still guards the transition for project-keyed loops (unchanged).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1541] Per-turn stall watchdog / wedged-session eviction — this issue raises throughput but does not evict a hung session; eviction is a distinct mechanism. Tracked as a follow-up on the same issue thread (noted in the issue body's open questions); file a dedicated issue at build time if Tom wants it pursued.
- PM session fan-out — preserves the PR #828 invariant (slugless PMs serialize per `project_key`). Not deferred laziness: parallelizing PMs on the main checkout would reintroduce git conflicts the invariant prevents.
- Teammate session routing changes — teammate sessions already fan out per `chat_id`; untouched.

## Update System

No update-script changes required for the code path. One additive doc/config item: the optional env var `MAX_CONCURRENT_DEVS_PER_PROJECT` is added to `.env.example` (commented, default 3) alongside the existing `MAX_CONCURRENT_SESSIONS` line. The update flow already syncs `.env.example` placeholders; no migration step is needed because the var is optional with a safe default. Existing installations gain fan-out automatically on restart (the cap defaults to 3); no per-machine action required.

## Agent Integration

No agent integration required — this is a worker-internal routing/concurrency change. The agent (PM/dev sessions) reaches no new CLI entry point or MCP tool; the behavior change is transparent (dev questions get answered concurrently). The bridge does not import the new helper directly. Integration coverage is via the worker-loop test asserting concurrent execution, not via a new agent-invokable surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` "Concurrency Controls (issue #810)" section: add a **Per-Project Dev Cap (`MAX_CONCURRENT_DEVS_PER_PROJECT`)** subsection after "Global Session Ceiling", describing the cap, default 3, acquisition order (per-project-dev before global), and the floor-of-two relationship with `MAX_CONCURRENT_SESSIONS`.
- [ ] Update `docs/features/sdlc-parallel-execution.md` and/or `docs/features/pm-dev-session-architecture.md` to describe per-project conversational-dev fan-out and how the runtime cap composes with the decompose-time `MAX_PARALLEL_DEVS`.
- [ ] Update the `worker_key` description in `docs/features/bridge-worker-architecture.md` (three-archetype routing) to note that slugless dev sessions are allocated a synthetic slug **at creation**, so they route by slug, not `project_key`.

### Inline Documentation
- [ ] Docstring for `AgentSession.ensure_dev_slug()` stating idempotency and the dual call sites (creation + executor fallback).
- [ ] Comment at the `_worker_loop` admission gate explaining the per-project-dev-before-global ordering and the single-consumer TOCTOU safety.
- [ ] Update the `worker_key` property docstring (`models/agent_session.py:472-503`) to state slugless dev sessions are now expected to carry a synthetic slug from creation time.

### `.env.example`
- [ ] Add commented `# MAX_CONCURRENT_DEVS_PER_PROJECT=3` under the existing `MAX_CONCURRENT_SESSIONS` line with a one-line comment.

## Success Criteria

- [ ] Three fresh conversational dev sessions in the same project run **concurrently** (distinct `worker_key`s = distinct slugs, distinct worktrees), not serially — verified by an integration test and observable in `localhost:8500/dashboard.json`.
- [ ] A per-project dev cap (default 3, `MAX_CONCURRENT_DEVS_PER_PROJECT`-overridable) blocks a 4th concurrent dev in the same project until a slot frees.
- [ ] A busy project at its cap does not prevent dev/PM sessions in **other** projects from running (global pool not monopolized) — integration test asserts cross-project independence.
- [ ] Slugged dev, PM, and teammate sessions retain current `worker_key` routing — unit test asserts no regression.
- [ ] Each concurrent dev session runs in its own `.worktrees/{slug}/` worktree with correct post-run cleanup (no main-checkout contamination, no orphaned worktrees) — existing #1272 cleanup path exercised.
- [ ] `worker_key` is computed in exactly one place (the property); the two inline "KEEP IN SYNC" recomputes are deleted — grep confirms.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (routing)**
  - Name: `routing-builder`
  - Role: `ensure_dev_slug()` model helper + early allocation in `_push_agent_session`/`enqueue_agent_session` + executor fallback swap + delete inline `worker_key` recomputes.
  - Agent Type: builder
  - Resume: true

- **Builder (concurrency)**
  - Name: `cap-builder`
  - Role: per-project dev semaphore in `session_state.py` + `_worker_loop` admission gate (peek + ordered acquire/release) + env clamp.
  - Agent Type: async-specialist
  - Resume: true

- **Validator (routing + cap)**
  - Name: `fanout-validator`
  - Role: verify routing has no regression, cap blocks the 4th, cross-project independence holds, no orphaned worktrees.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `fanout-doc`
  - Role: update the three feature docs + `.env.example` + inline docstrings.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — see template.)

## Step by Step Tasks

### 1. Early synthetic-slug allocation + worker_key dedup
- **Task ID**: build-routing
- **Depends On**: none
- **Validates**: `tests/unit/` worker_key tests, `_push_agent_session`/`enqueue_agent_session` notify-payload + `_ensure_worker` routing tests
- **Informed By**: recon (id is hex AutoKey assigned at `async_create`; `dev-{id[:8]}` matches cleanup regex)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `AgentSession.ensure_dev_slug()` (idempotent, pre-save guard, non-raising persist).
- Capture `created = await async_create(...)`; call `ensure_dev_slug()`; set notify `_wk = created.worker_key`; return effective worker_key from `_push_agent_session`.
- Route `_ensure_worker` on the returned key in `enqueue_agent_session`; delete both inline "KEEP IN SYNC" recomputes.
- Replace the executor's bespoke `dev-{aid[:8]}` block (`session_executor.py:781-792`) with `session.ensure_dev_slug()`.

### 2. Per-project dev concurrency cap
- **Task ID**: build-cap
- **Depends On**: none
- **Validates**: new worker-loop concurrency tests (4th dev waits; other project unaffected)
- **Informed By**: recon (global semaphore acquired before pop at `_worker_loop:1225-1234`; must acquire per-project-dev before global)
- **Assigned To**: cap-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_project_dev_semaphores` + `get_project_dev_semaphore(project_key)` (lazy `setdefault`, env clamp `max(1, ...)`) to `agent/session_state.py`.
- In `_worker_loop`: peek the next pending session; if dev, acquire per-project-dev before the global semaphore; release in reverse in the existing `finally`.
- Add a startup log line mirroring the global-semaphore init log.

### 3. Validation
- **Task ID**: validate-fanout
- **Depends On**: build-routing, build-cap
- **Assigned To**: fanout-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert 3 concurrent same-project dev sessions, 4th blocked, cross-project independence, PM/teammate/slugged-dev routing unchanged, `worker_key` single-source (grep), no orphaned worktrees.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fanout
- **Assigned To**: fanout-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `bridge-worker-architecture.md`, `sdlc-parallel-execution.md` / `pm-dev-session-architecture.md`, `.env.example`, and inline docstrings per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: fanout-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm every success criterion (including docs) met; final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| worker_key single-source | `grep -rn "KEEP IN SYNC with AgentSession.worker_key" agent/agent_session_queue.py` | exit code 1 |
| Cap env wired | `grep -rn "MAX_CONCURRENT_DEVS_PER_PROJECT" agent/ .env.example` | output contains `MAX_CONCURRENT_DEVS_PER_PROJECT` |
| Helper exists | `grep -rn "def ensure_dev_slug" models/agent_session.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Cap scope — all dev sessions vs. conversational only?** The plan caps **all** `session_type == "dev"` sessions per project (defaults align at 3 with `MAX_PARALLEL_DEVS`, so build fan-out is unaffected on a build-only project). The alternative is to count only synthetic-`dev-`-slugged conversational devs, giving builds and conversational Q&A separate budgets. Default chosen: cap everything (simpler, one mental model). Confirm, or request the split.
2. **Per-chat ordering.** Resolved by default: fresh same-chat questions fan out; reply-to resumes (serial). No per-chat ordering queue. Confirm this matches expectations for the Dev chat.
3. **Stall watchdog.** Confirmed out of scope here. Should a dedicated issue be filed now for per-turn wedged-session eviction, or left as a note on #1541?
