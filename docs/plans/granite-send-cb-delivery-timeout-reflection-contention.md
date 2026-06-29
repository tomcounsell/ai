---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-29
tracking: https://github.com/tomcounsell/ai/issues/1805
last_comment_id:
---

# Granite send_cb delivery timeout under startup reflection-batch contention

## Problem

A CEO sends a Telegram message right after a worker restart. The granite session
generates a correct 1735-char reply — then it vanishes. The reply never reaches
the chat, and the only trace is a WARNING and a `granite_delivery_failure`
session-event the user never sees. The session then loops: generate →
delivery-timeout → retry.

**Current behavior:**
- At worker startup the reflection scheduler's first `tick()` finds ~all of the
  **30 enabled reflections** (`config/reflections.yaml`) overdue and dispatches
  the function-type ones as concurrent `asyncio.create_task(...)` in one pass
  (`agent/reflection_scheduler.py:683`), with no concurrency cap or stagger.
- The granite container runs in `asyncio.to_thread`; its `send_cb` delivery is
  scheduled back onto the **same single event loop** via
  `asyncio.run_coroutine_threadsafe(coro, loop)` and blocked on
  `future.result(timeout=30s)` (`agent/granite_container/bridge_adapter.py:939-940`).
- The startup reflection burst saturates the loop, the delivery coroutine cannot
  be scheduled within 30s, `future.result` raises `concurrent.futures.TimeoutError`
  (which stringifies to `""`), and the catch-all `except` at
  `bridge_adapter.py:948-957` **drops the payload** — it is never re-enqueued.
  `_record_delivery_failure` (`:960`) writes a dashboard-only event with no
  user-visible recovery.

**Desired outcome:**
- A generated reply is **never silently lost**. On a delivery timeout the known
  payload is re-enqueued to the Telegram outbox so the relay still delivers it.
- The startup reflection batch no longer starves the loop hard enough to time out
  delivery in the first place (defense in depth).
- Dropped/recovered deliveries are surfaced loudly enough to notice in the
  dashboard.

## Freshness Check

**Baseline commit:** `ce0cc37cfc694d69cde0dbd8080a06efb2bcc08d`
**Issue filed at:** 2026-06-26T10:21:55Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold against baseline):**
- `agent/granite_container/bridge_adapter.py:83` — `DEFAULT_DELIVERY_TIMEOUT_S = 30.0`, hardcoded constant, no env override. Confirmed.
- `bridge_adapter.py:939-940` — `run_coroutine_threadsafe` + `future.result(timeout=timeout_s)`. Confirmed.
- `bridge_adapter.py:948-957` — catch-all `except` drops payload via `_record_delivery_failure`. Confirmed.
- `bridge_adapter.py:960-977` — `_record_delivery_failure` appends dashboard-only `granite_delivery_failure` event. Confirmed.
- `agent/reflection_scheduler.py:683-691` — function reflections dispatched as concurrent `asyncio.create_task` in `tick()`. Confirmed.
- `worker/__main__.py:583` + `:777` — reflection scheduler started as `asyncio.create_task` under the single `asyncio.run(...)` loop, shared with session execution. Confirmed.
- `config/reflections.yaml` — **30 enabled** reflections (`docs-auditor` disabled at the `enabled: false` entry). Issue said "~31"; corrected to 30.
- `agent/output_handler.py:~750-774` — normal Telegram enqueue: `r.rpush("telegram:outbox:{session_id}", json.dumps(payload))` + `r.expire(..., OUTBOX_TTL=3600)`. This is the re-enqueue target shape.

**Cited sibling issues/PRs re-checked:**
- #1803 (reflections-symlink worker wedge) — context for how this surfaced; not a code dependency.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-06-26T10:21:55Z` over `bridge_adapter.py` and `reflection_scheduler.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** None directly. `granite_pty_production_cutover.md`, `reliable-pm-final-delivery.md`, and `reflections-quality-pass.md` are adjacent but address different concerns (cutover, PM final delivery, reflection quality) — no overlap with delivery-timeout recovery or startup-batch throttling.

## Prior Art

- **No prior issue/PR** addresses send_cb delivery-timeout recovery or reflection startup-batch throttling. Searched closed issues/merged PRs for "delivery timeout", "send_cb", "reflection startup", "event loop contention".
- **#1647** introduced `_user_facing_routed` from the `_deliver_sync` return value — the flag this fix must keep honest (set True only on confirmed delivery OR confirmed outbox re-enqueue).
- **BRIDGE-1 / #1648** established the to_thread + captured-loop + `run_coroutine_threadsafe` delivery design and the transcript tailer; this fix extends the same delivery path.

## Research

No relevant external findings — this is purely internal asyncio/event-loop behavior and the repo's own Redis-outbox convention. Proceeding with codebase context. (Key internal fact: `concurrent.futures.TimeoutError` raised by `future.result(timeout=...)` stringifies to the empty string, which is why the issue log shows `raised:  (payload=1735 chars)`.)

## Data Flow

1. **Entry point**: CEO message → bridge enqueues an Eng AgentSession; worker picks it up and runs the granite container under `asyncio.to_thread` (`bridge_adapter.py:566`).
2. **Generation**: container produces a user-facing payload; its `on_user_payload` callback fires **on the to_thread worker thread**.
3. **Delivery attempt**: `_deliver_sync` (`bridge_adapter.py:873`) schedules the async `send_cb` onto the captured worker loop via `run_coroutine_threadsafe` and blocks on `future.result(timeout=30s)`.
4. **Contention**: concurrently, `ReflectionScheduler.start()` → `tick()` has dispatched ~30 `asyncio.create_task` reflections onto the same loop; the delivery coroutine cannot run in time.
5. **Drop (current bug)**: `future.result` raises TimeoutError → `_record_delivery_failure` → payload gone, `delivered=False`.
6. **Output (fixed)**: on timeout, synchronously `rpush` the payload to `telegram:outbox:{session_id}` (same shape as `output_handler.py`); the relay drains the outbox and delivers; record a "recovered via outbox" event.

## Why Previous Fixes Failed

No prior fixes for this exact defect. The root-cause pattern to avoid repeating: a *symptom-only* fix (just bumping the 30s timeout) would still drop the payload whenever the loop is busy longer than the new bound. The durable fix must make the payload recoverable (re-enqueue) AND reduce the contention that triggers it.

## Architectural Impact

- **New dependencies**: none. Reuses the existing Redis client and `telegram:outbox:*` list convention already used by `output_handler.py`.
- **Interface changes**: `_deliver_sync` gains a re-enqueue fallback branch; a small sync outbox-write helper is added to `bridge_adapter.py`. `DEFAULT_DELIVERY_TIMEOUT_S` becomes env-overridable. The reflection scheduler gains a startup-throttle parameter.
- **Coupling**: slightly increases bridge_adapter's coupling to the outbox key shape — mitigated by extracting/reusing a shared payload builder rather than duplicating the dict inline.
- **Data ownership**: unchanged — the outbox remains owned by the relay.
- **Reversibility**: high. Both levers are additive and gated by named constants/env vars; reverting restores prior behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in, 1 review round

**Interactions:**
- PM check-ins: 1-2 (confirm re-enqueue-vs-drop is the desired posture; confirm throttle shape)
- Review rounds: 1

## Prerequisites

No external prerequisites — Redis is already required by the worker. Run `python scripts/check_prerequisites.py docs/plans/granite-send-cb-delivery-timeout-reflection-contention.md`.

## Solution

### Key Elements

- **Outbox re-enqueue on timeout**: when `_deliver_sync` hits a delivery timeout (or loop-closed), it synchronously writes the already-known payload to `telegram:outbox:{session_id}` from the calling to_thread thread (bypassing the contended loop), so the relay still delivers it. The reply is never lost.
- **Honest routing flag**: `_user_facing_routed` is set True when delivery succeeds **or** the outbox re-enqueue succeeds; it stays False only if both fail.
- **Startup-batch throttle**: the reflection scheduler limits how many function-type reflections it dispatches per tick and/or yields the loop between dispatches, so a post-restart burst can't starve the delivery coroutine.
- **Env-overridable timeout**: `DEFAULT_DELIVERY_TIMEOUT_S` reads an env var with a sane provisional default and a grain-of-salt comment.
- **Louder surfacing**: delivery-failure events distinguish `recovered_via_outbox` from a true `dropped` outcome so the dashboard can flag genuine losses.

### Flow

CEO message after restart → granite generates reply → `_deliver_sync` tries loop delivery → loop busy with reflection burst → timeout → **sync rpush to telegram outbox** → relay drains outbox → reply reaches chat → event recorded as `recovered_via_outbox`.

### Technical Approach

- **Re-enqueue helper** in `bridge_adapter.py`: a sync function that builds the same payload dict as `output_handler.py` (`chat_id`, `reply_to`, `text`, `session_id`, `timestamp`, optional `file_paths`) and does `r.rpush` + `r.expire(OUTBOX_TTL)`. `telegram:outbox:*` is a plain Redis list, **not** a Popoto-managed key, so a direct rpush is correct and consistent with the existing handler (no Popoto-ORM requirement here). Prefer extracting a shared payload builder over duplicating the dict.
- **Wire it into `_deliver_sync`**: in the `except RuntimeError` (loop_closed) and catch-all `except` (TimeoutError) branches, attempt re-enqueue before returning. Return True iff re-enqueue confirmed; record `recovered_via_outbox` vs `dropped` accordingly.
- **Throttle the startup batch** in `agent/reflection_scheduler.py::tick`: cap concurrent function-reflection dispatch per tick (named constant, env-overridable) and `await asyncio.sleep(0)` between dispatches to yield to other loop work; remaining due reflections roll into the next tick. Keep agent-type reflections (already `await`ed serially) as-is.
- **Env-overridable constants** with grain-of-salt comments (per the named-magic-number convention): `GRANITE_DELIVERY_TIMEOUT_S` (default 30.0) and `REFLECTION_STARTUP_MAX_CONCURRENT` (provisional default, e.g. 4).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The catch-all `except Exception` in `_deliver_sync` (`bridge_adapter.py:948`) currently swallows into a dashboard-only event. Add a test asserting that on TimeoutError the payload is re-enqueued (observable: outbox rpush called) AND a `recovered_via_outbox` event is appended.
- [ ] Add a test for the double-failure path: timeout AND re-enqueue fails (Redis raises) → `dropped` event recorded, `_user_facing_routed` stays False, container does not crash.

### Empty/Invalid Input Handling
- [ ] Verify re-enqueue with an empty/whitespace payload is a no-op that does not write a junk outbox entry (mirror any guard in `output_handler.py`).
- [ ] Verify throttle handles a tick where 0 reflections are due (no-op) and where all 30 are due (only N dispatched, rest deferred to next tick).

### Error State Rendering
- [ ] No new user-visible error surface (by design — recovery is silent-success). Assert the user ultimately receives the payload via the relay in an integration-style test of the re-enqueue path, and that a genuine `dropped` is visible on the dashboard event stream.

## Test Impact

- [ ] `tests/unit/granite_container/test_bridge_adapter_delivery.py::test_timeout_records_failure_with_exception_type` — UPDATE: timeout now re-enqueues to the outbox; assert the rpush occurred and the event reason reflects `recovered_via_outbox` (not a silent drop). The `TimeoutError`-in-reason assertion may move to the double-failure `dropped` test.
- [ ] `tests/unit/granite_container/test_bridge_adapter_delivery.py` — UPDATE: add `test_timeout_reenqueues_to_outbox`, `test_double_failure_records_dropped`, `test_loop_closed_reenqueues`.
- [ ] `tests/unit/granite_container/test_bridge_adapter.py` — UPDATE only if the env-overridable timeout changes constructor defaults touched by these tests (verify).
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: add a test that a tick with > N due function-reflections dispatches at most N and defers the rest; verify existing tick tests still pass with the throttle (they should, since small batches are under the cap).

## Rabbit Holes

- **Multi-event-loop / process isolation for reflections.** Tempting "real fix" but far too large for this bug and risky. Out of scope — throttling is the proportionate lever.
- **Cancelling stuck sync reflections.** `asyncio.wait_for` over `run_in_executor` is detection-only and cannot cancel the thread; do not try to forcibly kill executor threads here.
- **Rewriting the relay/outbox contract.** Reuse the existing `telegram:outbox:{session_id}` shape verbatim; do not invent a new queue.
- **Tuning every reflection's individual timeout.** Different problem (reflection quality), tracked elsewhere.

## Risks

### Risk 1: Duplicate delivery (loop delivery races the outbox re-enqueue)
**Impact:** User sees the reply twice if the timed-out coroutine eventually runs AND the re-enqueue also delivers.
**Mitigation:** On timeout, cancel the pending future (`future.cancel()`) before re-enqueueing so the loop-path coroutine does not also complete. Document that `run_coroutine_threadsafe` futures are cancellable pre-execution; if already running, accept at-least-once and rely on the relay/redundancy filter.

### Risk 2: Throttle slows legitimate reflection cadence
**Impact:** With a per-tick cap, a large overdue backlog drains over several 60s ticks instead of instantly.
**Mitigation:** Backlog is bounded (30 reflections) and ticks are frequent; a cap of ~4/tick drains in a couple of ticks. Cap is env-overridable for tuning.

## Race Conditions

### Race 1: Timed-out coroutine completes after re-enqueue
**Location:** `agent/granite_container/bridge_adapter.py:939-957`
**Trigger:** loop frees up just as the 30s timeout fires; both the original coroutine and the re-enqueued outbox entry deliver.
**Data prerequisite:** the payload is fully known before scheduling (it is — it's the `payload` arg).
**State prerequisite:** at most one delivery should reach the user.
**Mitigation:** `future.cancel()` on timeout before re-enqueue; downstream redundancy filter is the backstop for the already-running edge case.

### Race 2: Re-enqueue while relay is mid-drain
**Location:** `telegram:outbox:{session_id}` list.
**Trigger:** relay pops the list while bridge_adapter rpushes.
**Data prerequisite:** none — rpush is atomic; relay drains FIFO.
**State prerequisite:** ordering is best-effort, already true of the normal path.
**Mitigation:** none needed; rpush is atomic and the relay already tolerates concurrent producers (output_handler writes the same key).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1803] The reflections-symlink worker wedge that surfaced this — separate root cause, separate issue.
- Multi-loop / subprocess isolation of the reflection scheduler — too large; throttling addresses the contention proportionately. (Not deferred-as-laziness: this is a deliberate architecture boundary; an anti-criterion below asserts no new event loop is introduced.)

## Update System

Two new env-overridable knobs (`GRANITE_DELIVERY_TIMEOUT_S`, `REFLECTION_STARTUP_MAX_CONCURRENT`) with provisional in-code defaults. If they are read via `config/settings.py`, add fields there and placeholders (with a comment line above each) to `.env.example`; otherwise they read `os.environ` directly with defaults and need no propagation. No `scripts/update/run.py` or `migrations.py` changes — no Popoto model changes, no new dependencies.

## Agent Integration

No agent integration required — this is a bridge/worker-internal delivery-reliability and scheduler change. No new MCP tool, no `.mcp.json` change, no new CLI entry point. The bridge already calls this delivery path; behavior change is internal.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` (or the closest granite delivery doc) with the outbox re-enqueue-on-timeout recovery behavior and the env-overridable delivery timeout.
- [ ] Update the reflections feature doc (e.g. `docs/features/` reflections page) to document the startup-batch throttle and `REFLECTION_STARTUP_MAX_CONCURRENT`.
- [ ] Add/refresh entry in `docs/features/README.md` index if a new doc page is created.

### Inline Documentation
- [ ] Grain-of-salt comments on both new env-overridable constants marking them provisional/tunable.
- [ ] Docstring update on `_deliver_sync` describing the re-enqueue fallback and the `recovered_via_outbox` vs `dropped` distinction.

## Success Criteria

- [ ] A granite reply that times out on the loop is re-enqueued to `telegram:outbox:{session_id}` and ultimately delivered (no silent loss).
- [ ] `_user_facing_routed` is True when delivery OR re-enqueue succeeds; False only on double-failure.
- [ ] Delivery-failure events distinguish `recovered_via_outbox` from `dropped`.
- [ ] The reflection scheduler dispatches at most `REFLECTION_STARTUP_MAX_CONCURRENT` function-reflections per tick; remaining due ones roll to the next tick.
- [ ] `GRANITE_DELIVERY_TIMEOUT_S` overrides the 30s default; both new constants carry grain-of-salt comments.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No new event loop / subprocess introduced for reflections (anti-criterion below).

## Team Orchestration

### Team Members

- **Builder (delivery-recovery)**
  - Name: delivery-builder
  - Role: Implement outbox re-enqueue on timeout in `bridge_adapter.py`, future cancellation, env-overridable timeout, event taxonomy.
  - Agent Type: async-specialist
  - Resume: true

- **Builder (scheduler-throttle)**
  - Name: scheduler-builder
  - Role: Implement per-tick startup-batch throttle in `reflection_scheduler.py` with env-overridable cap.
  - Agent Type: async-specialist
  - Resume: true

- **Validator (delivery+scheduler)**
  - Name: delivery-validator
  - Role: Verify re-enqueue path, double-failure path, throttle behavior, and success criteria.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update granite + reflections feature docs and index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Outbox re-enqueue on delivery timeout
- **Task ID**: build-delivery-recovery
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_bridge_adapter_delivery.py
- **Assigned To**: delivery-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add a sync outbox-write helper to `bridge_adapter.py` reusing the `output_handler.py` payload shape (`telegram:outbox:{session_id}`, rpush + expire OUTBOX_TTL); prefer a shared payload builder over a duplicated dict.
- In `_deliver_sync` loop_closed and catch-all branches: `future.cancel()` then attempt re-enqueue; return True iff re-enqueue confirmed.
- Make `DEFAULT_DELIVERY_TIMEOUT_S` read `GRANITE_DELIVERY_TIMEOUT_S` (default 30.0) with a grain-of-salt comment.
- Split `_record_delivery_failure` outcomes into `recovered_via_outbox` vs `dropped`.

### 2. Startup reflection-batch throttle
- **Task ID**: build-scheduler-throttle
- **Depends On**: none
- **Validates**: tests/unit/test_reflection_scheduler.py
- **Assigned To**: scheduler-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- In `tick()`, cap function-reflection dispatch at `REFLECTION_STARTUP_MAX_CONCURRENT` (env-overridable, provisional default ~4) per tick; `await asyncio.sleep(0)` between dispatches; defer overflow to next tick.
- Add grain-of-salt comment on the cap constant.

### 3. Validate delivery + scheduler
- **Task ID**: validate-changes
- **Depends On**: build-delivery-recovery, build-scheduler-throttle
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run unit tests for both modules; verify re-enqueue, double-failure, throttle, and success criteria.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-changes
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update granite + reflections feature docs and index per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria including docs and anti-criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Delivery tests pass | `pytest tests/unit/granite_container/test_bridge_adapter_delivery.py -q` | exit code 0 |
| Scheduler tests pass | `pytest tests/unit/test_reflection_scheduler.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Re-enqueue wired | `grep -n "telegram:outbox" agent/granite_container/bridge_adapter.py` | output contains telegram:outbox |
| Timeout env-overridable | `grep -n "GRANITE_DELIVERY_TIMEOUT_S" agent/granite_container/bridge_adapter.py` | exit code 0 |
| Throttle constant present | `grep -n "REFLECTION_STARTUP_MAX_CONCURRENT" agent/reflection_scheduler.py` | exit code 0 |
| Anti-criterion: no new event loop for reflections | `grep -nE "new_event_loop\|asyncio.run\(" agent/reflection_scheduler.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Re-enqueue vs at-most-once:** Confirm the desired posture is at-least-once delivery (re-enqueue on timeout, accept rare duplicate if the original coroutine also runs) over the current silent drop. The plan assumes yes, with `future.cancel()` to minimize duplicates.
2. **Throttle cap value:** Is ~4 function-reflections per tick the right provisional default, or should the startup batch be more aggressively staggered (e.g., 2/tick)? Both are env-overridable.
3. **Surfacing genuine drops:** Should a true `dropped` (double-failure) escalate beyond a dashboard event — e.g., a louder alert — or is the dashboard event sufficient?
