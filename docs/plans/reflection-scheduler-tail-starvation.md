---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-29
tracking: https://github.com/tomcounsell/ai/issues/3580
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-29T07:00:48Z
---

# Reflection scheduler starves jobs late in the registry

## Problem

`ReflectionScheduler.tick()` walks the registry (`config/reflections.yaml`) top to bottom every ~60 s and starts every due `function`-type reflection until it has started `REFLECTION_STARTUP_MAX_CONCURRENT` (default 4) of them. Everything after that is skipped with a DEBUG log. #1812 added the cap to throttle the startup burst, and `docs/features/reflections.md` says excess reflections "defer naturally to the next tick".

In steady state they do not. The walk always restarts at the top, and the short-interval jobs near the top (`session-recovery-drip` 30 s, `circuit-health-gate` 60 s, `side-effect-drain` 60 s, six 300 s jobs) fill all four slots on nearly every tick. Jobs at the bottom of the file are due, not paused, and not running, but the walk never reaches them.

**Current behavior (production, 2026-09-21 to 2026-09-29):**
- 83 of 83 ticks after the 10:44 restart logged `Tick complete: 4 reflection(s) enqueued`, so the cap was hit every tick.
- `improvement-planner-tick` (every 900 s) fired 5 times in 8 days, last on 2026-09-23. `side-effect-drain` fired about 7,900 times in the same window.
- The improvement loop (#3177) was enabled but produced nothing (`valor-improve ranking` printed `no snapshot yet`).
- The deferral is logged only at DEBUG, so none of this showed in INFO logs.
- Raising the cap to 8 (the stopgap now live in the vault `.env`) unstarved the tail on the first tick.

**Desired outcome:** every due function-type reflection is dispatched within a stated, bounded number of ticks of becoming due, whatever its position in the file and whatever the cap. The per-tick cap and the between-dispatch `asyncio.sleep(0)` from #1812 stay. A deferral shows up at INFO.

## Freshness Check

**Baseline commit:** `3a62f200e46f619e01b654def08ae503f421dc57`
**Issue filed at:** 2026-09-29T04:57:28Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/reflection_scheduler.py:54`: `REFLECTION_STARTUP_MAX_CONCURRENT` read from env, default `"4"`. Still holds.
- `agent/reflection_scheduler.py:947-1048` (`tick()`): a fixed `for entry in self._entries` walk. The cap check at 1010-1016 logs `Deferring %s to next tick` at DEBUG and `continue`s. `asyncio.sleep(0)` is at 1032. Still holds.
- `agent/reflection_scheduler.py:1122-1126` (`start()`): the `Tick complete: %d reflection(s) enqueued` INFO line. Still holds.
- `agent/reflection_schedule.py:155` (`compute_next_due`): `every:` returns `last_run + duration`, or `now` when `last_run is None`. This matters for the design (see Technical Approach).
- `docs/features/reflections.md:422`: the "Startup-batch concurrency throttle" paragraph that promises deferral "to the next tick". Still holds.

**Cited sibling issues/PRs re-checked:**
- PR #1812: merged 2026-06-29. It added the cap and the yield. Its motivation (event-loop saturation starving `_deliver_sync`) still stands.
- #3571: open. It covers `cron:` reflections never being due, which is a separate defect in `is_reflection_due`. The jobs starved here all use `every:`.
- #3177: open. This is the improvement loop that the starvation silenced.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=2026-09-29T04:57:28Z` on `agent/reflection_scheduler.py`, `agent/reflection_schedule.py`, `docs/features/reflections.md`, `tests/unit/test_reflection_scheduler.py` is empty).

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR #1812** (fix(granite): re-enqueue timed-out deliveries + throttle startup reflection batch): added the per-tick cap and `asyncio.sleep(0)`. It succeeded at its goal (startup burst). It assumed the cap would only bind at startup. With today's registry, steady-state demand exceeds 4 per tick, so the cap binds on every tick, and the fixed walk order turns that into permanent starvation for the tail.
- **#1828** (Reflection scheduler subprocess split, closed): moved reflections into their own worker process. It did not touch dispatch order.
- **#3571** (cron reflections never fire, open): a different bug with the same visible symptom ("a reflection never runs"). It is not addressed here.

## Research

No relevant external findings. This is an internal scheduling fix. Oldest-due-first ordering under a fixed per-round capacity is textbook earliest-deadline-first/FIFO dispatch and needs no library. WebSearch was skipped (Phase 0.7 skip rule: purely internal).

## Spike Results

### spike-1: Does oldest-due-first ordering unstarve the tail under the production registry at cap 4?
- **Assumption**: "Sorting due function candidates by how long they have been due, before applying the cap, gives every job a bounded delay without raising the cap."
- **Method**: prototype (a throwaway discrete simulation of the production registry's 40 enabled `every:` function entries, 2,000 ticks of 61 s, all entries last-run at t=0; not committed).
- **Finding**:
  - Fixed walk order (today): `improvement-planner-tick` ran **0** times, `improvement-intent-reconcile` 161 times against an expected ~400, and `improvement-controller-tick` was up to 1,235 s late. This reproduces the production symptom.
  - Oldest-due-first: every entry ran. `improvement-planner-tick` ran 132 times (the expected count). The worst lateness for any entry was 381 s (about 6 ticks).
  - **Steady-state demand is 4.73 dispatches per tick** (`sum(min(1, 60/every))`), which is above the cap of 4. No ordering can keep every job on schedule at cap 4. Oldest-due-first spreads the shortfall evenly. `circuit-health-gate` (60 s) ran 1,419 times instead of 1,999 and was at most ~5 ticks late.
  - An alternative key (lateness divided by interval) kept the 60 s jobs within 1 tick but let daily jobs run up to ~3,700 s late, and it has no simple provable bound. Rejected.
- **Confidence**: high. The simulation reproduced the observed starvation, which validates the model.
- **Impact on plan**: use absolute oldest-due-first ordering with the provable bound below. Surface the capacity finding as an Open Question instead of silently changing the default cap.

## Data Flow

1. **Entry point**: `ReflectionScheduler.start()` calls `tick()` every `SCHEDULER_TICK_INTERVAL` (60 s) in the reflection worker (`python -m reflections`).
2. **Walk**: for each registry entry, `Reflection.get_or_create()` loads state. Paused and running entries are skipped, and so are entries that are not due. `reflection_due_epoch()` is read before any `mark_started()` (#3183 lane 5b).
3. **Today**: a due function entry is dispatched immediately via `asyncio.create_task(run_reflection(...))` until the cap is hit, and then the rest are skipped. A due agent entry is awaited inline via `run_reflection` (enqueue to the session queue).
4. **After this change**: agent entries are still handled inline during the walk. Function candidates `(index, entry, state, due_epoch)` are collected instead. After the walk, they are sorted by an age key and the first `cap` are dispatched with the same `create_task` + `sleep(0)` sequence. The rest are recorded as deferred.
5. **Output**: `run_reflection` executes the callable and writes `ReflectionRun` / `Reflection.ran_at`. The next due time is recomputed from that `ran_at` on the next tick.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none public. `tick()` still returns the dispatch count. `ReflectionScheduler` gains one private in-memory attribute (`_deferred_since: dict[str, float]`).
- **Coupling**: unchanged.
- **Data ownership**: deferral age is process-local and is not persisted. A restart forgets it, which is safe (see Risks).
- **Reversibility**: trivial. The change is confined to one method plus a log line.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (post-deploy RD-1 gate result)
- Review rounds: 1

## Prerequisites

No prerequisites. This work has no external dependencies.

## Resolved Decisions

### RD-1: Default cap stays 4 in code; the production revert to 4 is gated on observed deferral data

Resolves former Open Question 1 and the critique CONCERN (Risk & Robustness, Scope & Value).

- **Code default**: `REFLECTION_STARTUP_MAX_CONCURRENT` stays `"4"` in `agent/reflection_scheduler.py` and `.env.example`. The issue's acceptance criteria pin the unit-test bound at cap 4 and keep the startup burst capped at 4, and the #1812 motivation (event-loop saturation) was measured at that value. Changing the throttle default is a separate capacity decision and should be made from production data, which this change is what produces (the new `deferred` count).
- **Why not revert blindly**: at cap 4, steady-state demand (4.73 per tick) exceeds capacity, so fair ordering moves part of the shortfall onto `circuit-health-gate` (owns the queue_paused and hibernating flags) and `session-recovery-drip` (drips one session per tick). That slows outage recovery. The simulation predicts this, so production has to confirm or refute it before the stopgap `8` is removed.
- **Gated post-deploy step** (replaces the unconditional revert): after the fix is deployed via `/update`, set the vault `.env` to `4`, restart the reflection worker, and observe `logs/reflection_worker_error.log` for at least 1 h:
  - `grep "Tick complete: .* deferred by per-tick cap" logs/reflection_worker_error.log` and count ticks with a non-zero deferred count.
  - Measure the gap between successive `circuit-health-gate` `Completed:` lines.
  - Confirm `improvement-planner-tick` fires about every 900 s.
  - **Pass** (deferred count zero on most ticks AND `circuit-health-gate` gaps at most ~120 s AND the planner on cadence): leave the vault at `4`. Issue AC 5 is met as written.
  - **Fail**: set the vault `.env` back to `5` (the simulation's value that keeps every sub-900 s job within about 1-2 ticks), restart the worker, and file a follow-up issue to raise the code default, with the observed numbers attached. That follow-up must update the comment at `agent/reflection_scheduler.py:48-53` and the `.env.example` placeholder together with the default. This is a **named deviation** from issue AC 5 ("back to 4") and is reported as such in the PR / issue comment, not as routine.
- The post-deploy observation result (deferred-tick count, worst `circuit-health-gate` gap, planner cadence, and the final vault value) is posted as a comment on #3580.

## Solution

### Key Elements

- **Two-phase tick**: the walk collects due function candidates instead of dispatching them inline. Dispatch happens after the walk, in a fair order.
- **Age-ordered dispatch**: candidates are ordered oldest-due first, so a job the cap skipped is at the front of the line next tick.
- **Deferral memory**: the scheduler remembers when each currently-deferred reflection was first deferred. This pins the age of jobs whose due time does not age on its own (never-run `every:` entries).
- **Visible deferral**: an INFO line when a reflection starts a deferral episode, an INFO line when it is finally dispatched, and the deferred count in the `Tick complete` line.

### Flow

Tick starts → walk registry (skip paused / running / not due; agent entries dispatched inline) → collect function candidates → sort by age key → dispatch the first `cap` (each followed by `sleep(0)`) → record the rest as deferred (INFO on first deferral) → `Tick complete: X enqueued, Y deferred`

### Technical Approach

- **Sort key**: `(min(due_epoch, deferred_since.get(name, +inf)), registry_index)`, ascending. Oldest first. Registry index breaks ties, which keeps the old top-to-bottom order whenever ages are equal (for example, all never-run entries at startup). That keeps both existing cap tests valid unchanged.
  - `due_epoch` comes from the existing `reflection_due_epoch(entry, state, now)` already read in the walk. For an `every:` entry with a last run, this is `last_run + interval`, which is at or before the moment the entry became due, so it ages naturally.
  - **Never-run hazard (why `deferred_since` is required):** `compute_next_due` returns `now` for an `every:` entry with no `last_run` (`agent/reflection_schedule.py:155`). Its `due_epoch` is therefore re-stamped to the current tick every tick. With `due_epoch` alone it would always look youngest and would starve under load, which recreates this exact bug for new reflections. `deferred_since` (set to `now` on the first deferral, kept on later deferrals) pins its age.
  - If `due_epoch` is `None` (no schedule; legacy interval path), use `now`. `deferred_since` then ages it the same way.
- **Dispatch-exception semantics**: a candidate whose `create_task` raises still counts against the cap for that tick (the slot was spent attempting it), is logged at ERROR with `exc_info`, and is carried into the new `_deferred_since` (keeping its earlier value, else `now`). It therefore keeps its pinned age and ranks first next tick, so a never-run entry cannot lose its place because one dispatch attempt failed.
- **Deferral bookkeeping**: `self._deferred_since` is rebuilt every tick to contain exactly the candidates deferred this tick, keeping each one's earlier value if it was already present. An entry that was dispatched, became paused or running, stopped being due, or left the registry drops out automatically. No separate cleanup path is needed.
- **Provable bound (for the test and the docs)**: suppose a candidate C is deferred with age key K. Any entry that ranks ahead of C has key ≤ K. Once that entry is dispatched, its next due is at least dispatch time plus its interval, which is later than K, and its deferral entry clears. So each other entry can rank ahead of C at most once in C's deferral episode. C is therefore dispatched within **ceil((N − 1) / cap) ticks of its first deferral**, where N is the number of enabled function-type entries. With production numbers (N = 40, cap 4) that is at most 10 ticks. The simulation's steady-state worst case was about 6.
- **Logging**:
  - First deferral of an episode: `logger.info("[reflection] Deferring %s (per-tick cap %d reached; due since %.0fs ago)", ...)`. Later deferrals in the same episode stay at DEBUG.
  - Dispatch of a previously deferred entry: `logger.info("[reflection] %s dispatched after %.0fs deferred by per-tick cap", ...)`.
  - `start()`'s line becomes `Tick complete: %d reflection(s) enqueued, %d deferred by per-tick cap` (using `len(self._deferred_since)`), and it is emitted when either count is non-zero.
- **Unchanged**: the cap constant and its env override, `asyncio.sleep(0)` after each function dispatch, the skip-if-running / stuck-reset / paused logic, reading `due_epoch` before `mark_started`, and agent-type reflections being awaited inline, serially, and uncapped.
- Update the comment block on `REFLECTION_STARTUP_MAX_CONCURRENT` (lines 48-53). The cap binds in steady state too, and the order is fair.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The per-entry `try/except Exception` in `tick()` logs at ERROR with `exc_info`. It must keep covering both phases. An exception while dispatching one candidate must not stop the remaining candidates from being dispatched or recorded as deferred. Add a test where `create_task` raises for one candidate, and assert that the next candidate still dispatches, the error is logged, the failed candidate used a cap slot, and it is present in `_deferred_since` with its earlier value (or `now` if new).

### Empty/Invalid Input Handling
- [ ] Empty registry: `tick()` returns 0 and `_deferred_since` stays empty.
- [ ] A candidate with `due_epoch is None` sorts using `now` and still ages via `deferred_since` (covered by the never-run test).

### Error State Rendering
- [ ] No user-visible output. The INFO deferral lines are the observability surface and are asserted with `caplog`.

## Test Impact

- [ ] `tests/unit/test_reflection_scheduler.py::TestReflectionScheduler::test_tick_caps_function_dispatches_at_max_concurrent`: no change needed. It must still pass: all candidates are never-run and have equal keys, so the tie-break keeps registry order and exactly `cap` dispatch. Verify it; don't edit it. This holds only while every entry resolves to `due_epoch == now` (interval=300 normalized to `every: 300s`, `ran_at` None, `_latest_run_timestamp` returning None), i.e. while the test Redis has no `ReflectionRun` rows for those names. The validator notes this dependency when confirming the test unchanged.
- [ ] `tests/unit/test_reflection_scheduler.py::TestReflectionScheduler::test_tick_small_batch_under_cap_unaffected`: no change needed. It must still pass.
- [ ] `tests/unit/test_reflection_scheduler.py::TestReflectionScheduler::test_scheduler_tick_skips_not_due` / `test_scheduler_tick_skips_running` / `test_skip_running_preserves_running_status`: no change needed. The walk's skip logic is untouched.
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryReload::test_tick_reloads_before_evaluating_entries`: no change needed. `reload_if_changed()` still runs first.
- [ ] `tests/unit/test_reflection_scheduler.py`: UPDATE by adding new multi-tick tests (a tail job behind hogs dispatched within the stated bound; a never-run tail dispatched within the bound; one INFO deferral line per episode plus a dispatched-after-deferral line; an exception on one candidate does not block the others).

## Rabbit Holes

- Reworking the cap to count in-flight tasks instead of per-tick dispatches. That is a different throttle with different startup semantics. Not needed to fix starvation.
- Priority classes, or using the registry's `priority:` field, as the ordering key. Age ordering already guarantees a bound, and priority weighting is how starvation comes back.
- Persisting deferral age to Redis. Process-local state is enough because a restart re-derives age from `ran_at`, and never-run entries restart their clock (bounded).
- Fixing `cron:` due evaluation (#3571).

## Risks

### Risk 1: Short-interval jobs run later than today
**Impact:** Steady-state demand (4.73 per tick) is above the cap, so fairness moves some of the shortfall from the tail onto `circuit-health-gate`, `side-effect-drain`, and the 300 s jobs. The simulation showed `circuit-health-gate` up to ~5 ticks late and running about 30% less often at cap 4.
**Mitigation:** Every job is bounded (at most ceil((N−1)/cap) ticks). The new `Tick complete ... deferred` count makes sustained overload visible at INFO. The capacity decision is resolved in RD-1: the production revert to cap 4 is gated on a 1 h post-deploy observation of the deferred count and `circuit-health-gate` cadence.

### Risk 2: Deferral age lost on restart
**Impact:** A never-run entry deferred before a restart starts its deferral clock again.
**Mitigation:** The bound applies again from the restart. Entries with a `ran_at` keep their true age through `due_epoch`, which lives in Redis.

## Race Conditions

No race conditions identified. `tick()` runs on one asyncio task and is the only reader and writer of `_deferred_since`. The only change in timing is that a candidate's `state` is read during the walk and passed to `run_reflection` a few awaits later in the same tick. That candidate was not running when it was read, so no task of its own can be mutating it. `due_epoch` is still read before `mark_started()`, which keeps the #3183 lane 5b invariant.

## No-Gos (Out of Scope)

- [ORDERED] Reverting `REFLECTION_STARTUP_MAX_CONCURRENT` from the stopgap `8` back to `4` in the vault `.env` (`~/Desktop/Valor/.env`) and restarting the reflection worker. This must wait until this fix is merged and deployed via `/update`. Reverting earlier starves the tail again. It is listed under Success Criteria as the post-deploy step, gated by the 1 h observation in RD-1: if the gate fails, the vault stays at `5` and a follow-up issue raises the code default (named deviation from issue AC 5).
- [SEPARATE-SLUG #3571] `cron:` reflections never being due.

## Update System

No update system changes are required. The change is confined to `agent/reflection_scheduler.py`. `/update` already restarts the reflection worker on a code change. There are no new env vars or config files (`REFLECTION_STARTUP_MAX_CONCURRENT` already exists in `.env.example`).

## Agent Integration

No agent integration is required. This is internal to the reflection worker's scheduler. No CLI entry point, MCP surface, or bridge import changes.

## Documentation

- [ ] Update `docs/features/reflections.md` ("Startup-batch concurrency throttle", line ~422) to describe the oldest-due-first dispatch order, the ceil((N−1)/cap)-tick bound, the never-run pinning, the INFO deferral lines, the `deferred` count in `Tick complete`, and that the cap also binds in steady state when demand exceeds it.
- [ ] Update the inline comment on `REFLECTION_STARTUP_MAX_CONCURRENT` in `agent/reflection_scheduler.py` and the `tick()` docstring.
- [ ] Update the `.env.example` comment above `REFLECTION_STARTUP_MAX_CONCURRENT` (it also says excess reflections "defer to the next tick") to describe oldest-due-first ordering and the bound. The value stays `4` (RD-1).

## Success Criteria

- [ ] With the cap at 4 and hog entries that fill every slot, a multi-tick unit test shows a due entry at the end of the registry dispatched within ceil((N−1)/cap) ticks of first deferral (the test asserts that bound explicitly).
- [ ] The same guarantee holds for a never-run tail entry.
- [ ] `test_tick_caps_function_dispatches_at_max_concurrent` and `test_tick_small_batch_under_cap_unaffected` pass unchanged.
- [ ] A deferral episode produces exactly one INFO `Deferring <name>` line and one INFO `dispatched after ... deferred` line (caplog test).
- [ ] `asyncio.sleep(0)` still follows every function dispatch, and agent-type reflections are still dispatched inline and uncapped.
- [ ] `docs/features/reflections.md` describes the fair dispatch order.
- [ ] Post-deploy (ORDERED, see No-Gos and RD-1): `REFLECTION_STARTUP_MAX_CONCURRENT` is set to `4` in the vault `.env`, the reflection worker is restarted, and `logs/reflection_worker_error.log` is observed for at least 1 h: `improvement-planner-tick` fires about every 900 s, and the `Tick complete: ... deferred by per-tick cap` count and the worst `circuit-health-gate` `Completed:` gap are recorded.
- [ ] Post-deploy report: a comment on #3580 states the number of ticks with a non-zero deferred count, the worst `circuit-health-gate` gap, the planner cadence, and the final vault value. If the RD-1 gate failed, the vault is at `5`, a follow-up issue to raise the code default is filed and linked, and the deviation from issue AC 5 is named.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (scheduler)**
  - Name: scheduler-builder
  - Role: Implement the two-phase fair dispatch, deferral bookkeeping, logging, and new tests
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Validator (scheduler)**
  - Name: scheduler-validator
  - Role: Verify the bound, the unchanged cap tests, and the #1812 invariants
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reflections-documentarian
  - Role: Update `docs/features/reflections.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement fair dispatch in `tick()`
- **Task ID**: build-scheduler
- **Depends On**: none
- **Validates**: tests/unit/test_reflection_scheduler.py
- **Informed By**: spike-1 (absolute oldest-due-first; never-run entries need `deferred_since` pinning)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `self._deferred_since: dict[str, float] = {}` to `ReflectionScheduler.__init__`.
- In `tick()`, keep the walk's paused / running / stuck-reset / due checks and the `due_epoch` read exactly as they are. Agent-type entries keep dispatching inline. Function-type entries append `(index, entry, state, due_epoch)` to a candidates list.
- After the walk, sort the candidates by `(min(due_epoch if due_epoch is not None else now, self._deferred_since.get(name, inf)), index)`. Dispatch the first `REFLECTION_STARTUP_MAX_CONCURRENT` with the existing `create_task` + done-callback + `await asyncio.sleep(0)` sequence, each inside its own `try/except` that logs at ERROR. For each remaining candidate, keep or set `deferred_since` and log INFO only on a new deferral (DEBUG otherwise). Replace `self._deferred_since` with this tick's deferred set.
- When a candidate that was in the previous deferred set is dispatched, log INFO `dispatched after %.0fs deferred by per-tick cap`.
- Change `start()`'s tick line to `Tick complete: %d reflection(s) enqueued, %d deferred by per-tick cap`, emitted when either count is non-zero.
- Update the `REFLECTION_STARTUP_MAX_CONCURRENT` comment block and the `tick()` docstring.
- Add the tests listed in Test Impact. Use per-name `MagicMock` states returned from a patched `Reflection.get_or_create`, patch `time.time` to advance by 61 s per tick, and have the fake `create_task` set the dispatched state's `ran_at = now`. Patch `agent.reflection_scheduler._latest_run_timestamp` to return None in every new test, and set `ran_at` explicitly (numeric or None) on every per-name state, so no new test reads `ReflectionRun` rows from the test Redis. The bound assertion is computed as `math.ceil((N - 1) / REFLECTION_STARTUP_MAX_CONCURRENT)`, not hard-coded.
- A candidate whose dispatch raises counts against the cap and is carried into the new `_deferred_since` (earlier value, else `now`); see Technical Approach.

### 2. Validate the scheduler change
- **Task ID**: validate-scheduler
- **Depends On**: build-scheduler
- **Assigned To**: scheduler-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `scripts/pytest-clean.sh tests/unit/test_reflection_scheduler.py -q`.
- Confirm the two existing cap tests are unmodified in the diff and pass.
- Confirm `asyncio.sleep(0)` and the cap constant are retained, and that agent-type dispatch is still inline.
- Confirm that a new test fails against the pre-change `tick()` (red proof: temporarily revert the scheduler hunk, run, restore).

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-scheduler
- **Assigned To**: reflections-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite the "Startup-batch concurrency throttle" paragraph in `docs/features/reflections.md` as described under Documentation.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-scheduler, validate-scheduler, document-feature
- **Assigned To**: scheduler-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and confirm the Success Criteria (except the ORDERED post-deploy row).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Scheduler tests pass | `scripts/pytest-clean.sh tests/unit/test_reflection_scheduler.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/reflection_scheduler.py tests/unit/test_reflection_scheduler.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/reflection_scheduler.py tests/unit/test_reflection_scheduler.py` | exit code 0 |
| Deferral memory present | `grep -c "_deferred_since" agent/reflection_scheduler.py` | output > 0 |
| Cap retained (#1812) | `grep -c "REFLECTION_STARTUP_MAX_CONCURRENT" agent/reflection_scheduler.py` | output > 0 |
| Event-loop yield retained (#1812) | `grep -c "asyncio.sleep(0)" agent/reflection_scheduler.py` | output > 0 |
| Deferral no longer DEBUG-only | `grep -c "Deferring %s (per-tick cap" agent/reflection_scheduler.py` | output > 0 |
| `.env.example` comment updated | `grep -c -i "oldest-due" .env.example` | output > 0 |
| Docs describe fair order | `grep -c -i "oldest-due" docs/features/reflections.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness, Scope & Value | The spike shows cap 4 is below steady-state demand (4.73 per tick), so the ORDERED post-deploy revert to 4 knowingly moves the shortfall onto the high-priority short-interval jobs. `circuit-health-gate` manages the queue_paused and hibernating flags and would run about 30% less often and up to ~5 ticks late. `session-recovery-drip` drips one session per tick and loses throughput. Outage recovery gets slower, the trade-off sits only in Open Question 1, and the only post-deploy check is the planner cadence. | RD-1 (Resolved Decisions); Success Criteria post-deploy rows; No-Gos ORDERED item | Resolve Open Question 1 before build and record the chosen cap and the reason as a resolved decision. The post-deploy step must grep `Tick complete: .* deferred by per-tick cap` in `logs/reflection_worker_error.log` for at least 1 h after the change. If the deferred count is non-zero on most ticks, or `circuit-health-gate` `Completed:` lines are more than ~120 s apart, keep the vault `.env` at >= 5 and file the default change instead of reverting to 4. If the code default changes, update the comment at `agent/reflection_scheduler.py:48-53` and the `.env.example` placeholder too. Add a success criterion that reports the deferred count post-deploy. |
| NIT | Risk & Robustness | When `ran_at` is None or non-numeric, `_effective_last_run` falls through to `_latest_run_timestamp`, which runs a real `ReflectionRun.query` on the test Redis, so the new multi-tick tests depend on DB contents. | Step 1 build task (test isolation bullet) | Patch `agent.reflection_scheduler._latest_run_timestamp` to return None in the new tests, and set a numeric (or None) `ran_at` on every per-name state explicitly. |
| NIT | History & Consistency | The plan does not say whether a candidate whose `create_task` raised uses up a cap slot or keeps its `deferred_since`. Under slice semantics it drops out of the deferred set, so a never-run entry loses its pinned age. | Technical Approach (dispatch-exception semantics); Failure Path Test Strategy; Step 1 | Carry a candidate whose dispatch raised into the new `_deferred_since` (keep its earlier value, else `now`). It still counts against the cap for that tick. |
| NIT | History & Consistency | `test_tick_caps_function_dispatches_at_max_concurrent` relies on every entry resolving to `due_epoch == now` (interval=300 is normalized to `every: 300s`, `ran_at` is None, and `_latest_run_timestamp` returns None). The tie-break claim holds only while the test Redis has no `ReflectionRun` rows for those names. | Test Impact (existing cap test note) | No plan change needed. Note it when verifying the test unchanged. |

---

## Open Questions

None. Former Open Question 1 (default cap vs. steady-state demand) is resolved as RD-1 under Resolved Decisions.
