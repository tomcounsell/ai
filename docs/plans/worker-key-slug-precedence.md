---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-21
tracking: https://github.com/tomcounsell/ai/issues/1085
last_comment_id:
---

# worker_key prefers slug for slugged dev sessions

## Problem

Slugged dev sessions that happen to share a `chat_id` serialize through a single `_worker_loop` even though each lives on its own branch and worktree. The routing key is supposed to reflect isolation level, not communication topology, but the current property keys slugged dev sessions by `chat_id` instead of by `slug`.

**Current behavior:**
`AgentSession.worker_key` (`models/agent_session.py:280-282`) returns `self.chat_id or self.project_key` for slugged dev sessions. When five slugged dev sessions were enqueued during the #1041 test-suite cleanup — all with the default `chat_id=0` created via `valor_session create --role dev` — they all routed to the project-keyed worker loop and ran one at a time. We worked around it by assigning synthetic distinct `chat_id`s (`1041002`, `1041003`, ...) so the routing produced distinct `worker_key`s, which meant every session wrote outbox entries for ghost chats that do not exist.

**Desired outcome:**
Two slugged dev sessions with the same `chat_id` but different `slug`s naturally get distinct `worker_key`s and run concurrently, up to `MAX_CONCURRENT_SESSIONS`. No synthetic chat_id workaround, no ghost outbox entries.

## Freshness Check

**Baseline commit:** `3fd4f9c2316c6bf73fa7feb64a37ca93cc805211`
**Issue filed at:** 2026-04-20T16:42:17Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py:267-282` — property logic claimed by the issue — still holds exactly as described. Lines cited in the issue (`279-282`) are correct against HEAD.
- `worker/__main__.py:309-313` — uses `session.worker_key` directly (property, not an inline duplicate). Any property change propagates here automatically.
- `agent/agent_session_queue.py:362-370` and `:1110-1118` — two inline duplications of the property logic. **Not cited in the issue but directly affected.** Both must change in lockstep with the property.

**Cited sibling issues/PRs re-checked:**
- #831 — closed 2026-04-08 (PR #832). Introduced the current `worker_key` property. No follow-up changes on main since.
- #973 — closed 2026-04-16 (worktree-parallel-sdlc). Its plan explicitly reasoned about slugged dev routing and noted the `chat_id`-keyed behaviour as "parallel-safe" — that reasoning is now known to be incomplete (collides on shared `chat_id`). No code change needed from #973 is blocked by this fix.
- #1041 — closed 2026-04-20. The live scenario that surfaced this bug.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-04-20T16:42:17Z` on `models/agent_session.py`, `worker/__main__.py`, and the two integration test files returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** none — `docs/plans/worktree-parallel-sdlc.md` already shipped and does not block this change; no open plans touch `worker_key`.

**Notes:** The issue only calls out one file, but the property's logic is duplicated inline in two places in `agent/agent_session_queue.py` (a documented perf optimization to avoid a Popoto round-trip during enqueue/notify). Those duplicates must be kept in sync — this is a maintainability hazard the plan flags explicitly.

## Prior Art

- **PR #832** (merged 2026-04-08, closed #831): Introduced `AgentSession.worker_key` as a computed property. Established the `is_project_keyed` discriminator pattern. Its decision table (`docs/features/bridge-worker-architecture.md:162-167`) codified `chat_id` as the routing key for slugged dev sessions — the premise this fix overturns.
- **PR #989** (merged 2026-04-15, closed #986): `fix: startup recovery must not hijack local CLI sessions`. Touches `agent/session_health.py` recovery logic that reads `entry.worker_key` — works on whatever the property returns, no string-parsing of the key. Safe across this change.
- **PR #1029** (merged 2026-04-17): `Collapse session concurrency: single MAX_CONCURRENT_SESSIONS=8 cap`. Removed per-type caps; there is now only one global semaphore to worry about. A larger `worker_key` cardinality does not require additional cap tuning — the global cap still bounds concurrency regardless of how many keys exist.
- **PR #1051** (merged 2026-04-19, closed #1023): `refactor: split agent_session_queue.py`. Did not touch `worker_key` logic; the two inline duplications predate this split and survived it.
- **#973 / worktree-parallel-sdlc** (merged 2026-04-16): Spike finding verbatim — _"The remaining work is: (a) add `MAX_CONCURRENT_DEV_SESSIONS` as a separate cap, and (b) add a dashboard counter. No deep Redis namespacing refactor is needed."_ The #973 plan explicitly assumed `chat_id`-based routing was sufficient for parallel dev sessions. #1085 proves that assumption was incomplete for the same-chat case.

## Research

No relevant external findings — this is a pure internal Python property change. No libraries, APIs, or ecosystem patterns involved.

## Spike Results

No spikes dispatched. All three open questions resolve via code reading alone:

1. **Precedence ordering across session types** — answered by reading the current property (PM explicitly returns `project_key`; TEAMMATE explicitly returns `chat_id`; DEV is the only branch needing change). See **Technical Approach**.
2. **Test coverage** — `grep` across `tests/` found seven sites referencing `worker_key`. One (`tests/unit/test_agent_session.py:171-174 test_dev_with_slug_uses_chat_id`) directly asserts the old behaviour and must be updated. One (`tests/integration/test_worker_concurrency.py:388-439 TestDevWorktreeParallelism`) asserts parallelism via distinct `chat_id`s and must be augmented with a same-`chat_id` case. See **Test Impact**.
3. **Dashboard visibility** — `grep -l worker_key ui/` returns zero files. `ui/data/sdlc.py` and `ui/data/machine.py` aggregate by `session_type` / `project_key` / `slug`, never by `worker_key`. No UI change required.

## Data Flow

1. **Entry point**: Any code path that reads `session.worker_key` — the standalone worker startup loop (`worker/__main__.py:309-313`), the session executor respawn path (`agent/session_executor.py:341, 378, 464`), the session health monitor (`agent/session_health.py:254-959` — many sites), and the two inline duplicates inside `agent/agent_session_queue.py`.
2. **`AgentSession.worker_key` property**: Currently returns `self.slug` to the caller → no, returns `self.chat_id or self.project_key` for slugged dev. This is the one place to change.
3. **`_ensure_worker(worker_key, is_project_keyed)`**: Key used as the dict key for `_active_workers`, `_active_events`, `_starting_workers`. Values are opaque strings — any change in the computed key is transparent to this layer.
4. **`_pop_agent_session(worker_key, is_project_keyed)`**: For project-keyed workers, filters by `project_key` then re-filters in-Python by `s.worker_key == worker_key`. For chat-keyed workers, filters by `chat_id=worker_key`. **After this change**, a slugged dev session's `worker_key == slug` but its `chat_id` is still set to its original value. The current `is_project_keyed` branch uses `chat_id=worker_key` filtering, which would miss slugged dev sessions because `worker_key` is now the slug, not the chat_id. `slug` is currently a plain `Field` (not indexed — see `models/agent_session.py:216`), so we promote it to `KeyField(null=True)` and add a slug-filter branch. See **Technical Approach** below for the fix.
5. **Redis pub/sub notify payload** (`agent/agent_session_queue.py:371-377`): publishes `{"worker_key", "is_project_keyed", ...}` so the subscriber (`agent/agent_session_queue.py:808`) can call `_ensure_worker(wk, is_pk)`. Already key-opaque.
6. **Output**: The worker loop that handles the session's key pops it and executes. With the fixed property, two slugged dev sessions sharing a `chat_id` route to two distinct worker loops and run in parallel (subject to the global semaphore).

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: The computed value of `AgentSession.worker_key` changes for one case (session_type=DEV, slug set). The method signature and return type are unchanged. No call site changes its interface.
- **Coupling**: Slightly reduces coupling between the routing key and Telegram communication topology. The property now correctly reflects the slug-vs-project-vs-chat isolation hierarchy.
- **Data ownership**: Unchanged. `worker_key` remains a computed read-only property; no new stored state.
- **Reversibility**: Trivial — single-line logic revert restores old behavior. No migrations, no persistent state shape change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (spec is unambiguous; issue has explicit acceptance criteria)
- Review rounds: 1 (one code review pass sufficient)

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`AgentSession.worker_key` property**: single-site logic change — prefer `slug` over `chat_id` for slugged dev sessions.
- **Two inline duplications** in `agent/agent_session_queue.py`: update in lockstep to match the property.
- **`_pop_agent_session` filter logic**: the chat-keyed branch currently filters pending sessions by `chat_id=worker_key`. For slugged dev sessions, this filter must match by `slug=worker_key` instead — otherwise a worker keyed by a slug will never find its session because the session's `chat_id` is not its `worker_key`.
- **Docstring**: rewrite the `worker_key` property docstring to reflect the new precedence explicitly.
- **Tests**: update the one assertion that encodes the old behaviour; add a regression test for the same-`chat_id`, different-`slug` scenario; update the `TestDevWorktreeParallelism` test helper to exercise the new routing.
- **Feature doc**: update `docs/features/bridge-worker-architecture.md` decision table row for `dev` + slug (`chat_id` → `slug`).

### Flow

Operator runs `valor_session create --role dev --slug feat-A` (chat_id defaults to 0) → AgentSession persisted with slug=feat-A, chat_id=0 → `_enqueue_agent_session` computes `wk=feat-A` (inline), calls `_ensure_worker("feat-A", is_project_keyed=False)` → worker loop `feat-A` spawns, pops its session via `_pop_agent_session("feat-A", is_project_keyed=False)` → second invocation with `--slug feat-B` → `wk=feat-B`, spawns worker loop `feat-B`, pops its session → both workers run concurrently, each in its own worktree. Global semaphore (`MAX_CONCURRENT_SESSIONS=8`) bounds overall concurrency.

### Technical Approach

**Precedence decision (Open Question 1 resolution):** Apply `slug` preference *only* to DEV sessions. Teammate and PM session routing is unchanged. Rationale: PM sessions have no slug semantics (they orchestrate across multiple work items); teammate sessions are conversational and are correctly keyed by `chat_id` because multiple chats with the same operator should run in parallel. Only DEV is worktree-isolated and benefits from slug-keyed routing. Explicitly:

| Session type | Slug? | `worker_key` (new) | `worker_key` (old) |
|---|---|---|---|
| `pm` | N/A | `project_key` | `project_key` |
| `dev` | yes (worktree) | **`slug`** | `chat_id or project_key` |
| `dev` | no (main repo) | `project_key` | `project_key` |
| `teammate` | N/A | `chat_id or project_key` | `chat_id or project_key` |

**Property change:** Lines 280-282 of `models/agent_session.py` become:

```python
# dev: isolated by slug (worktree) if present, serialized by project otherwise
if self.slug:
    return self.slug
return self.project_key
```

**Inline duplicate updates:** `agent/agent_session_queue.py` lines 362-370 and 1110-1118 both contain the same four-branch inline computation. Both must change to `_wk = slug` / `wk = slug` in the `elif slug:` branch. These duplicates exist to avoid a Popoto round-trip during enqueue and notify — they are intentional but must track the property byte-for-byte.

**`_pop_agent_session` filter fix:** `agent/session_pickup.py:212` currently filters `AgentSession.query.async_filter(chat_id=worker_key, status="pending")` for non-project-keyed workers. This assumes `worker_key == chat_id` for chat-keyed workers. After this change, slugged dev workers are keyed by `slug`, but `slug` is currently a plain `Field` (not indexed) at `models/agent_session.py:216`. Two fix options:

- **Option A (recommended): promote `slug` to `KeyField(null=True)`** — one-line schema change at line 216. Then add a third branch to `_pop_agent_session`: if the worker_key corresponds to a slug-keyed worker, filter `slug=worker_key`. This requires a new discriminator (we can't tell slug-keyed from chat-keyed from `(worker_key, is_project_keyed)` alone). The simplest and cleanest discriminator: try `slug=worker_key` first — if any result has a truthy slug and its computed `worker_key == worker_key`, use that. Otherwise fall back to `chat_id=worker_key`. This keeps the filter paths indexed and correct.
- **Option B: project-key-and-re-filter pattern.** The project-keyed branch already uses this at lines 207-210: filter by `project_key`, then Python re-filter on `s.worker_key == worker_key`. For slug-keyed workers, we'd need to know the project_key, which is not currently passed to `_pop_agent_session`. We'd have to either (a) pass it alongside `worker_key` and `is_project_keyed` at spawn time (touches every `_ensure_worker` caller), or (b) scan all pending sessions and filter in Python (O(all-pending) — fine at realistic scale but touches scan cost).

Option A wins on three counts: single field-type change, uses Popoto indexes, no new parameters. The only cost is a one-time Popoto index rebuild on deploy (slug values are populated for maybe a few hundred existing records at most). The plan adopts Option A. Implementation: add a try-slug-then-fallback-to-chat pattern in `_pop_agent_session` so the same code path handles both teammate and slugged-dev non-project-keyed workers. The same fix applies to the sync fallback at `agent/session_pickup.py:370-373`.

**Docstring rewrite** on the property:

```python
"""Compute the worker loop routing key based on isolation level.

Teammate sessions run in parallel across chats, keyed by chat_id.
PM sessions and slugless dev sessions share the main working tree,
keyed by project_key.  Slugged dev sessions have their own worktree
(.worktrees/{slug}/) and branch (session/{slug}), so they route by
slug — two slugged dev sessions with the same chat_id still land on
different worker loops and run in parallel.
"""
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `models/agent_session.py` `worker_key` property has no exception handlers. State: "No exception handlers in scope for the property itself."
- [ ] `agent/session_pickup.py` `_pop_agent_session` has a broad `except Exception` around the query block (already tested by existing `TestPopLockContention` and `TestPerChatSerialization` suites). No new exception paths introduced by this change.

### Empty/Invalid Input Handling
- [ ] `worker_key` when `self.slug` is empty string: the current `if self.slug:` truthy check already handles empty strings identically to `None` (falls through to `project_key`). Preserve this — no change.
- [ ] `worker_key` when `self.slug is None` but `session_type == DEV`: falls through to `project_key`. Preserve this — no change.
- [ ] Add explicit assertion test for slug=`""` → `project_key` (not the slug).

### Error State Rendering
- [ ] This change has no user-visible output rendering. The only observable effect is worker parallelism. Verified by the integration test asserting `peak_running == 2` for two same-`chat_id` slugged dev sessions.

## Test Impact

- [ ] `tests/unit/test_agent_session.py::TestWorkerKeyProperty::test_dev_with_slug_uses_chat_id` — REPLACE: rename to `test_dev_with_slug_uses_slug`, assert `s.worker_key == "my-feature"` (the slug). The old test encoded the exact behaviour this plan removes.
- [ ] `tests/unit/test_agent_session.py::TestWorkerKeyProperty::test_two_slugged_dev_sessions_different_chats_different_worker_keys` — UPDATE: the existing assertion (`s1.worker_key != s2.worker_key`) still holds because both have distinct slugs. Add a second assertion that each `worker_key` equals the corresponding slug, and add a sibling test `test_two_slugged_dev_sessions_same_chat_different_slugs_different_worker_keys` for the exact bug scenario.
- [ ] `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism::test_two_slugged_dev_sessions_execute_concurrently` — UPDATE: the test currently uses distinct `chat_ids`. Add a new test `test_two_slugged_dev_sessions_same_chat_id_execute_concurrently` that uses the same `chat_id` for both sessions (the #1085 bug scenario) and different slugs; assert `peak_running == 2`. The existing test remains as a separate regression coverage for the distinct-chat case.
- [ ] `tests/integration/test_worker_drain.py::TestEnqueueEventSignal::test_enqueue_sets_worker_event` (lines 63-99) — UPDATE: the test uses a teammate session and asserts `worker_key == chat_id` — teammate behaviour is unchanged, so the test passes unmodified. Verify no changes needed; add a comment noting that teammate routing is intentionally unchanged.
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: lines 598-702 construct sessions and assert `session.worker_key == "valor"` for slugless dev (unchanged by this plan) and `== "local-valor"` for local sessions. No code changes; verify by re-running.
- [ ] Existing integration tests `tests/integration/test_remote_update.py:306` use `worker_key` as a function parameter in a mock side effect — opaque to the routing change. No update needed.
- [ ] Regression test to add: `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism::test_slug_keyed_pop_finds_session_by_slug` — create a slugged dev session, spawn a worker loop with `worker_key=<slug>`, assert the worker successfully pops the session. This catches the `_pop_agent_session` filter regression if the project-key-and-re-filter fix is missed.

## Rabbit Holes

- **Reworking the `is_project_keyed` discriminator into an enum with three values** (`PROJECT_KEYED`, `CHAT_KEYED`, `SLUG_KEYED`). Tempting because the query-filter logic now has a third case. Rejected: the project-key-and-re-filter pattern handles slug-keyed pops without adding a third branch. Enum expansion is a refactor orthogonal to the bug fix and would bloat this PR.
- **Auditing every `session.chat_id` read to check whether it now drifts from `worker_key`**. Not required — the routing key is opaque to callers. Only the two inline duplicates and `_pop_agent_session` filter logic care about how the key is computed.
- **Wiring `MAX_CONCURRENT_DEV_SESSIONS`**. Explicitly dropped by the issue. A separate cap is an orthogonal feature. With the corrected routing, the global `MAX_CONCURRENT_SESSIONS=8` already bounds the total; any additional dev-specific cap is a future concern.
- **Dashboard widget showing "N dev sessions in N worker loops"**. Not needed for the fix; the existing dashboard aggregates by session_type and displays running counts correctly. Any visibility improvement is a separate follow-up.

## Risks

### Risk 1: Inline `worker_key` duplicates drift out of sync with the property
**Impact:** Notification payloads would route to the wrong worker, or enqueue would spawn the wrong worker loop. Workers would sit idle while sessions sit pending.
**Mitigation:** Update all three sites in the same commit. Add a unit test that constructs an AgentSession and asserts that the values computed by each of the three sites agree for each session-type × slug-presence combination. This is a six-case truth table that would catch any future drift.

### Risk 2: `_pop_agent_session` filter miss
**Impact:** A slug-keyed worker spawns but cannot find its session, because the filter looks for `chat_id=<slug>` and never matches. The session sits pending until the health-check safety net (5 minutes later) retries.
**Mitigation:** The regression test `test_slug_keyed_pop_finds_session_by_slug` directly covers this. Implement the slug-first-then-chat filter pattern in both the async and sync fallback paths at `agent/session_pickup.py:212` and `:373`. Run `tests/integration/test_worker_concurrency.py` end-to-end; peak_running must equal 2 for the same-chat-id scenario.

### Risk 2b: Popoto index rebuild on `slug` promotion
**Impact:** Promoting `slug` from `Field` to `KeyField` changes Popoto's indexing behaviour. Existing slug values would not be indexed until the records are touched or an index rebuild runs. During the deploy window, `async_filter(slug=...)` queries may return empty for existing pre-deploy sessions, causing any already-queued slugged dev session to sit pending until a health-check fallback.
**Mitigation:** At realistic scale the pending queue turns over within minutes, so any pre-existing slugged dev session finishes via the fallback path within one health-check cycle. To eliminate the deploy-window gap, the plan includes a one-time index rebuild step: after deploying the model change, run `python -m tools.valor_session list` (which iterates all AgentSessions and re-saves through Popoto, repopulating indexes) or invoke Popoto's explicit `rebuild_indexes` if exposed. Document this in the PR description.

### Risk 3: A slug collides with a project_key
**Impact:** If a slug happened to equal a project_key (e.g., slug=`valor`), the slugged dev session would route to the same worker loop as PM sessions for that project, serializing with them. This would be a correctness regression relative to the intent of the change.
**Mitigation:** Slugs are human-chosen work-item identifiers (see `docs/features/session-isolation.md`), and project_keys are typically lowercased repo names. Collisions are not actively prevented today. Document this as a known limitation in the property's docstring. The cost of preventing it (slug prefix, validation at create time) exceeds the probability × impact. Leave for a follow-up if it ever happens in practice.

## Race Conditions

### Race 1: Worker spawn race across the three inline computations
**Location:** `agent/agent_session_queue.py:362-370` (notify publish), `:1110-1118` (enqueue), and `models/agent_session.py:267-282` (property) — plus the subscriber at `:808` which reads `worker_key` from the payload.
**Trigger:** A session is enqueued and the `_enqueue_agent_session` caller computes `wk` inline. In parallel, the Redis pub/sub listener receives the notify with `wk` from the payload. Both call `_ensure_worker(wk, is_pk)`. If the two `wk` values disagree (e.g., because one duplicate was updated and the other wasn't), two worker loops spawn for the same session — double-execution risk.
**Data prerequisite:** All three sites agree on `wk` for a given session.
**State prerequisite:** The session row in Redis must exist (already guaranteed by `_enqueue_agent_session`).
**Mitigation:** Update all three sites in the same commit; add a unit-level consistency test that feeds every permutation of (session_type, slug, chat_id, project_key) through all three computation sites and asserts they return the same value. `_ensure_worker`'s existing `_starting_workers` guard already prevents double-spawn in the degenerate case where two callers race with the same key.

## No-Gos (Out of Scope)

- Enum refactor of `is_project_keyed` → `worker_key_kind`. Future concern.
- `MAX_CONCURRENT_DEV_SESSIONS` env var. Separate feature (explicitly dropped by the issue).
- Per-slug dashboard counter. Cosmetic; no dashboard changes required for the fix.
- Slug/project_key collision detection. Documented as known limitation; not fixed here.
- Renaming the `is_project_keyed` boolean to be more accurate (it will now be true for slugless dev and PM, false for teammate and slugged-dev — same as today). Naming is already ambiguous but works.

## Update System

One minor consideration: the `slug` field is promoted from `Field` to `KeyField`, which is a Popoto schema-level change. On machines running the worker, the index rebuild runs lazily as sessions are touched — but to guarantee pending sessions created before the deploy route correctly, operators should run an index rebuild after pulling. The `/update` skill currently does `git pull`, sync deps, restart services. **Add a one-liner to the update skill's runbook** (or to this PR's description) noting that after pulling this change, operators should either (a) run `python -m tools.valor_session list` to trigger lazy re-save of all AgentSessions, or (b) let the health-check loop naturally recover any pre-deploy slugged dev session within 5 minutes. No new dependencies, no new config files, no new env vars.

## Agent Integration

No agent integration required — `worker_key` is an internal worker routing signal. The agent (via the bridge, tools, or MCP servers) does not read or write `worker_key` directly. No MCP server changes, no `.mcp.json` changes, no bridge changes. The only surfaces that interact with `worker_key` are `agent/session_pickup.py`, `agent/session_executor.py`, `agent/session_health.py`, `agent/agent_session_queue.py`, and `worker/__main__.py` — all internal to the worker process.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md`:
  - Revise the Decision Table (lines 160-167) to change the `dev` + slug row: `worker_key` column from `chat_id` to `slug`, Behavior note to mention slug-keyed parallelism (e.g., "Parallel-safe across chats AND across slugs in the same chat").
  - Update the "Two Worker Loop Archetypes" section (lines 173-181) to describe three archetypes: project-keyed (PM + slugless dev), chat-keyed (teammate), and slug-keyed (slugged dev).
  - Update the "Why `chat_id` Is Not the Isolation Key" paragraph (lines 169-171) to add: "Similarly, `chat_id` is insufficient for dev sessions — two dev sessions for different work items in the same chat would serialize even though they share no state. Slug is the correct routing key for worktree-isolated dev sessions."
- [ ] Update `docs/features/pm-dev-session-architecture.md` line 195 (the inline sentence matching the decision table) to reference slug-keyed dev routing.
- [ ] No new feature doc needed — this is a fix to an existing documented feature, not a new one.

### External Documentation Site
- [ ] No external docs site for this repo — skip.

### Inline Documentation
- [ ] Update the `worker_key` property docstring in `models/agent_session.py:267-274` to describe the new precedence (see Technical Approach for the new text).
- [ ] Add an inline comment at `agent/agent_session_queue.py:362` and `:1110` noting the duplication: `# KEEP IN SYNC with AgentSession.worker_key in models/agent_session.py`.

## Success Criteria

- [ ] `AgentSession.worker_key` returns `self.slug` for slugged dev sessions, regardless of `chat_id`. (Issue AC 1)
- [ ] Two slugged dev sessions created with the same `chat_id` (e.g., both defaulting to `0`) run concurrently in the worker. Demonstrated by an integration test. (Issue AC 2)
- [ ] Non-slugged dev sessions still serialize by `project_key`. (Issue AC 3)
- [ ] PM and teammate session routing is unchanged. Verified by unchanged tests in `test_agent_session.py::TestWorkerKeyProperty`. (Issue AC 4)
- [ ] No new failing tests on `main`; the one test that encoded the old behaviour is renamed and updated with justification. (Issue AC 5)
- [ ] `models/agent_session.py` `worker_key` docstring reflects the new precedence. (Issue AC 6)
- [ ] All three inline computation sites produce identical values for every session-type × slug combination (unit test asserts the truth table).
- [ ] `_pop_agent_session` successfully pops a slug-keyed session from a slug-keyed worker loop (integration test).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): the decision table in `bridge-worker-architecture.md` matches the new behaviour.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. Given the Small appetite, a single builder + validator pair is sufficient.

### Team Members

- **Builder (worker-key-property)**
  - Name: `worker-key-builder`
  - Role: Apply the property change, update the two inline duplicates, fix `_pop_agent_session` filter logic, update the property docstring and inline comments, update the feature docs.
  - Agent Type: builder
  - Resume: true

- **Validator (worker-key-tests)**
  - Name: `worker-key-validator`
  - Role: Verify the property change, verify all three inline sites return identical values for the truth table, verify the integration tests pass (including the new same-chat-id case), verify `docs/features/bridge-worker-architecture.md` decision table matches new behaviour.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using Tier 1 agents only (builder, validator). No specialists required — this is isolated Python logic plus documentation updates.

## Step by Step Tasks

### 1. Update property + inline duplicates + pop filter
- **Task ID**: build-worker-key
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py::TestWorkerKeyProperty`, `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism`, `tests/integration/test_worker_drain.py`
- **Informed By**: Issue #1085 (exact file:line cited), Research (no spikes), Data Flow analysis (identified `_pop_agent_session` filter as a dependent site)
- **Assigned To**: worker-key-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `models/agent_session.py:280-282`: replace `if self.slug: return self.chat_id or self.project_key` with `if self.slug: return self.slug`.
- Rewrite the property docstring at `models/agent_session.py:267-274` to describe the new three-archetype precedence (text in Technical Approach).
- Edit `agent/agent_session_queue.py:362-370`: change the `elif slug:` branch from `_wk = chat_id or project_key` to `_wk = slug`. Add a comment `# KEEP IN SYNC with AgentSession.worker_key in models/agent_session.py` at the top of the block.
- Edit `agent/agent_session_queue.py:1110-1118`: change the `elif slug:` branch from `wk = chat_id or project_key` to `wk = slug`. Add the same sync-comment at the top of the block.
- Edit `models/agent_session.py:216`: promote `slug = Field(null=True)` to `slug = KeyField(null=True)` so Popoto indexes slug values and `async_filter(slug=...)` becomes a native index lookup.
- Edit `agent/session_pickup.py:212` and `:373` (sync fallback): in the non-project-keyed branch, try `slug=worker_key` first (captures slugged-dev workers). If the result is non-empty, use it. Otherwise fall back to `chat_id=worker_key` (captures teammate workers). Both paths already re-use the downstream filtering (scheduled_at, zombie guard, sort by priority+created_at), so the change is contained to the initial query.
- Update `docs/features/bridge-worker-architecture.md` decision table row for `dev` + slug (`chat_id` → `slug`); revise the "Two Worker Loop Archetypes" section to describe three archetypes; extend the "Why `chat_id` Is Not the Isolation Key" section to mention dev sessions.
- Update `docs/features/pm-dev-session-architecture.md:195` to reference slug-keyed dev routing.

### 2. Update unit tests for the property
- **Task ID**: build-property-tests
- **Depends On**: build-worker-key
- **Validates**: `tests/unit/test_agent_session.py::TestWorkerKeyProperty`
- **Assigned To**: worker-key-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `test_dev_with_slug_uses_chat_id` (line 171) to `test_dev_with_slug_uses_slug` and update the assertion to `assert s.worker_key == "my-feature"`.
- Add a new test `test_two_slugged_dev_sessions_same_chat_different_slugs_different_worker_keys` in the same class: two DEV sessions, same `chat_id="chat-A"`, different slugs, assert distinct `worker_key`s each equal to their slug.
- Add a new test `test_dev_with_empty_slug_falls_through_to_project_key`: DEV session with `slug=""` must return `project_key` (not the slug).
- Add a new test `test_worker_key_truth_table_matches_enqueue_inline_computations`: for each permutation of (session_type, slug_present, chat_id_present), build an AgentSession, call the property, and build an equivalent dict passed to the inline-computation logic extracted from `agent/agent_session_queue.py:362-370`. Assert they agree. (Shared helper to avoid duplicating the computation in the test itself.)

### 3. Update integration tests for parallel execution
- **Task ID**: build-integration-tests
- **Depends On**: build-worker-key
- **Validates**: `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism`
- **Assigned To**: worker-key-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a new test `test_two_slugged_dev_sessions_same_chat_id_execute_concurrently` in `TestDevWorktreeParallelism`: two DEV sessions with identical `chat_id="0"`, different slugs (`feat-A`, `feat-B`); assert `peak_running == 2` using the same mock harness as the existing test.
- Add a new test `test_slug_keyed_pop_finds_session_by_slug` in `TestDevWorktreeParallelism`: create one slugged dev session, call `_pop_agent_session(slug, is_project_keyed=False)`, assert it returns the session (catches the `_pop_agent_session` filter regression).
- Verify the existing `test_two_slugged_dev_sessions_execute_concurrently` still passes unmodified (different chat_ids; unaffected by the change).

### 4. Validate
- **Task ID**: validate-worker-key
- **Depends On**: build-worker-key, build-property-tests, build-integration-tests
- **Assigned To**: worker-key-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session.py tests/integration/test_worker_concurrency.py tests/integration/test_worker_drain.py -v` — all pass.
- Run `python -m ruff format . && python -m ruff check .` — clean.
- Verify by reading `models/agent_session.py` that the property returns `self.slug` for the slugged-dev case.
- Verify `agent/agent_session_queue.py:362-370` and `:1110-1118` both changed and both have the sync comment.
- Verify `agent/session_pickup.py` filter logic uses project-key + re-filter for non-project-keyed branch.
- Verify `docs/features/bridge-worker-architecture.md` decision table row for slugged dev says `slug` (not `chat_id`).
- Run the complete truth table unit test and confirm all three inline sites agree.
- Report pass/fail with specific assertions.

### 5. Documentation and final validation
- **Task ID**: validate-all
- **Depends On**: validate-worker-key
- **Assigned To**: worker-key-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria checkboxes are satisfiable by running each check.
- Run full test suite `pytest tests/unit tests/integration -x -q`.
- Confirm the PR description references #1085 with `Closes #1085`.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_agent_session.py -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_worker_concurrency.py tests/integration/test_worker_drain.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/agent_session.py agent/agent_session_queue.py agent/session_pickup.py tests/unit/test_agent_session.py tests/integration/test_worker_concurrency.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/agent_session.py agent/agent_session_queue.py agent/session_pickup.py` | exit code 0 |
| Property returns slug for slugged dev | `python -c "from models.agent_session import AgentSession; from config.enums import SessionType; s = AgentSession(session_type=SessionType.DEV, slug='feat-X', chat_id='0', project_key='valor'); assert s.worker_key == 'feat-X', s.worker_key"` | exit code 0 |
| Property unchanged for PM | `python -c "from models.agent_session import AgentSession; from config.enums import SessionType; s = AgentSession(session_type=SessionType.PM, chat_id='anything', project_key='valor'); assert s.worker_key == 'valor', s.worker_key"` | exit code 0 |
| Property unchanged for teammate | `python -c "from models.agent_session import AgentSession; from config.enums import SessionType; s = AgentSession(session_type=SessionType.TEAMMATE, chat_id='chat-A', project_key='valor'); assert s.worker_key == 'chat-A', s.worker_key"` | exit code 0 |
| Inline duplicates updated (enqueue) | `grep -n "_wk = slug" agent/agent_session_queue.py` | output contains `_wk = slug` |
| Inline duplicates updated (notify) | `grep -n "wk = slug" agent/agent_session_queue.py` | output contains `wk = slug` |
| Sync comment added | `grep -c "KEEP IN SYNC with AgentSession.worker_key" agent/agent_session_queue.py` | output contains `2` |
| Feature doc updated | `grep -c "slug" docs/features/bridge-worker-architecture.md` | output > 3 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

All three open questions from the issue's Solution Sketch are resolved above via code reading:

1. **Ordering of precedence across session types** → resolved in **Technical Approach**: apply `slug` preference only to DEV sessions; PM and TEAMMATE routing is unchanged.
2. **Test coverage** → resolved in **Test Impact**: one test (`test_dev_with_slug_uses_chat_id`) encodes the old behaviour and is explicitly replaced; two new integration tests cover the bug scenario; no other tests assert the old routing.
3. **Dashboard visibility** → resolved in **Research** / **Solution**: `grep -l worker_key ui/` returns zero files; no UI aggregation groups by `worker_key`. No UI change required.

One remaining question for the supervisor:

1. **Slug/project_key collision risk** — slugs are human-chosen; if a slug ever equals a project_key, the slugged dev session routes to the PM-serializing worker loop. The plan documents this as a known limitation rather than preventing it (collision probability × impact is low relative to prevention cost). **Question for review: is that the right trade-off, or should we add a validation check at `valor_session create --slug` time that rejects slugs equal to any configured `project_key`?** Defaulting to "document and move on" unless overridden.
