---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-28
tracking: https://github.com/tomcounsell/ai/issues/1808
last_comment_id:
---

# Investigation: Wedged-But-Alive Worker Leaves Sessions Pending Indefinitely

## Problem

A spin-out from #1804. During that plan's review an open question surfaced: when a session sat *indefinitely* in `pending` (the original #1804 symptom), the 300s `_agent_session_health_check` backstop (`agent/session_health.py:2552`) should have re-scanned and started/nudged a worker. An indefinite hang therefore implies a **distinct defect** beyond the dead notify subscription that #1804 fixed — a worker process that is *alive* (heartbeat green, `ps` shows it) yet *wedged* so it cannot pick up `pending` work, AND the 300s backstop fails to recover it.

**Current behavior:**
- Sessions can (allegedly) remain `pending` forever even though the worker process is alive and writing heartbeats.
- The health-check pending branch nudges the worker's event and `continue`s, never escalating when the worker future is non-`done()` but not actually consuming.

**Desired outcome:**
- A confirmed root cause for the wedge (a reproducible code path), OR a documented determination that the symptom is no longer reproducible after #1804 shipped — in which case #1808 closes as "not reproducible — resolved by #1804".
- Either way: a committed reproduction harness (regression test) and an opt-in diagnostic that will capture the wedge if it recurs in production, so we are not flying blind next time.

## Freshness Check

**Baseline commit:** `c01485e474677b53a7a1a23cd219a7c3960eadcc`
**Issue filed at:** 2026-06-26T15:35:02Z
**Disposition:** Unchanged (precondition #1804 now satisfied, as the issue anticipated)

**File:line references re-verified:**
- `agent/session_health.py:2552-2566` — pending branch treats any non-`done()` worker future as alive and only `event.set(); continue` — **still holds** (verified verbatim at plan time).
- `agent/agent_session_queue.py:1312-1314` — worker loop blocks on `await semaphore.acquire()` before popping — **still holds**.
- `worker/__main__.py:216` — `_global_session_semaphore = asyncio.Semaphore(_max_sessions)` — **still holds**.
- `worker/__main__.py:54-75` — heartbeat runs on a dedicated daemon thread outside the event loop (#1767) — **still holds**.

**Cited sibling issues/PRs re-checked:**
- #1804 — **CLOSED 2026-06-26T16:34:56Z**, merged via PR #1809 (`71c1edc7`). The notify-listener NUMSUB self-check shipped; the dead-subscription cause is ruled out. This is exactly the precondition #1808 required.

**Commits on main since issue was filed (touching referenced files):**
- `71c1edc7` fix(worker): notify listener NUMSUB self-check + VALOR_WORKER_MODE in plist (#1804) — this is the gating precondition, not a change to the wedge surface. No other commits touched `session_health.py`, `agent_session_queue.py`, `worker/__main__.py`, or `session_state.py`.

**Active plans in `docs/plans/` overlapping this area:** `worker_watchdog_ustate_recovery.md` (#1767) — **related, not overlapping**. #1767 addresses an OS-level U-state (uninterruptible) hung *process*, recovered by an external watchdog. #1808 is an *in-process* wedge (event loop or worker-loop coroutine parked) where the process is fully responsive at the OS level and the heartbeat stays green. Different layer, different recovery mechanism.

**Notes:** No drift. All cited line numbers are current against the baseline commit.

## Prior Art

- **#1804 / PR #1809**: Standalone worker ran in bridge mode; notify-listener miss stranded sessions. Shipped the subscribe-time NUMSUB self-check. Outcome: merged. Relevance: it is the *precondition* for this investigation — it removes the notify subscription as a candidate cause, so any remaining indefinite-pending hang is a different defect.
- **#1767 / `worker_watchdog_ustate_recovery.md`**: Worker watchdog fails to recover a U-state (uninterruptible) hung worker. Outcome: closed; introduced the **off-event-loop heartbeat thread** (`worker/__main__.py:54-75`). Relevance: directly explains why a wedged worker keeps a green heartbeat — the heartbeat no longer depends on the event loop, so a frozen event loop is invisible to heartbeat-based liveness.
- **#1537**: Liveness recovery requeues a hung session to `pending` without killing its subprocess — orphan wedges the worker slot. Outcome: closed. Relevance: **direct precedent for hypothesis 1** (semaphore-slot exhaustion). An orphaned running session that never releases its `_global_session_semaphore` slot is a concrete mechanism for parking every worker loop at `await semaphore.acquire()`.
- **#1270**: Per-tool timeout enforcement in session-liveness-check. Outcome: closed. Relevance: the 30s tool-timeout sub-loop is a parallel recovery path; the investigation must check whether it, too, is frozen during the wedge (it runs on the same event loop).
- **PR #1773 (#1768)**: stall-advisory actor + `granite_wedged` signal (auto-recover wedged *sessions*). Relevance: a related but session-scoped wedge detector; the investigation should check whether its signal fires for the worker-loop wedge or only for in-session PTY stalls.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1767 heartbeat thread | Moved heartbeat off the event loop so OS-level hangs still write heartbeats | Side effect: a frozen *event loop* now also keeps a green heartbeat, masking the in-process wedge this issue describes. Heartbeat liveness can no longer distinguish "loop healthy" from "loop wedged". |
| #1537 slot-orphan handling | Killed orphaned subprocesses on requeue | If any path still requeues/finalizes without releasing the semaphore slot (or the slot leaks on an exception path in `_worker_loop`), the slot exhaustion recurs. Investigation must confirm the slot is always released. |

**Root cause pattern:** Liveness is inferred from coarse, process-level or future-level signals (heartbeat written, worker future not `done()`) that do not prove the worker loop is *making progress* (popping/processing). The 300s backstop's pending branch inherits this blind spot: a non-`done()` worker future is assumed healthy.

## Research

**Queries used:**
- detect blocked asyncio event loop python loop.slow_callback_duration debug mode diagnose stalled coroutine

**Key findings:**
- asyncio ships a built-in debug mode (`loop.set_debug(True)` / `PYTHONASYNCIODEBUG=1`) that logs any callback whose execution exceeds `loop.slow_callback_duration` (default 100ms). This pinpoints the exact synchronous call blocking the event loop — directly applicable to hypothesis 2. Source: https://docs.python.org/3/library/asyncio-dev.html
- `loop.slow_callback_duration` can be lowered (e.g. 0.05) to catch smaller blocks; "Executing <Task...> took N seconds" log lines name the offending task. Source: https://docs.python.org/3/library/asyncio-eventloop.html
- Full debug mode is too costly for steady-state production; `aiodebug` (and the newer `BlockBuster`) provide blocking-call logging without the rest of asyncio debug overhead — a model for an opt-in, low-cost diagnostic toggle. Sources: https://superfastpython.com/asyncio-log-long-running-aiodebug/ , https://dev.to/cbornet/introducing-blockbuster-is-my-asyncio-event-loop-blocked-3487

How this informs the plan: the diagnostic deliverable (Deliverable B) will gate `loop.set_debug(True)` + a tuned `slow_callback_duration` behind an opt-in env flag so a real-world recurrence logs the blocking callsite, without paying debug-mode cost in steady state.

## Spike Results

### spike-1: Does the health-check pending branch ever escalate a non-`done()` but non-consuming worker?
- **Assumption**: "The 300s backstop's pending branch has no path to recover a worker whose future is pending (parked in an `await`) but which cannot pop work."
- **Method**: code-read (`agent/session_health.py:2552-2620`)
- **Finding**: **Confirmed.** The branch is `worker_alive = worker is not None and not worker.done(); if worker_alive: event.set(); continue`. There is no liveness/progress check on the worker loop itself — a parked worker future is treated identically to a healthy idle one. Escalation (start a worker) only happens when the future is missing or `done()`.
- **Confidence**: high
- **Impact on plan**: Establishes the primary root-cause hypothesis to confirm by reproduction (Task 2). The fix, if confirmed, is a separate slug (add a worker-loop progress signal so the pending branch can escalate a parked-but-non-consuming worker).

### spike-2: Can a heartbeat stay green while every coroutine is frozen?
- **Assumption**: "Heartbeat liveness cannot detect an event-loop wedge."
- **Method**: code-read (`worker/__main__.py:54-75`)
- **Finding**: **Confirmed.** The heartbeat is a daemon thread (`_heartbeat_thread_main`) that writes on its own `threading.Event` timer, independent of the asyncio loop. A blocked event loop does not stop it.
- **Confidence**: high
- **Impact on plan**: Justifies Deliverable B (opt-in asyncio-debug diagnostic) — heartbeat-based liveness is structurally unable to catch this wedge, so we need a loop-level detector to capture a production recurrence.

## Data Flow

The wedge interrupts the normal pickup path. Tracing it:

1. **Entry point**: `enqueue_agent_session()` writes an `AgentSession(status="pending")` and publishes `valor:sessions:new` / sets `_active_events[worker_key]`.
2. **Notify listener** (`_session_notify_listener`, post-#1804): receives the pub/sub message and sets the worker's event — *if the listener coroutine is scheduled*. On a wedged event loop it never runs.
3. **Worker loop** (`_worker_loop`, `agent_session_queue.py:1303+`): wakes on the event, calls `await semaphore.acquire()` (line 1314), then `_pop_agent_session`. **Wedge surfaces here:** if the semaphore is depleted (a prior running session never released its slot) the loop parks at `acquire()` and never pops. Its future is pending (`not done()`).
4. **Health backstop** (`_agent_session_health_check`, every 300s): the pending branch (line 2557) sees `worker_alive == True` (future not done), sets the event, and `continue`s. The session stays `pending`. **Dead end.**
5. **Output**: session never transitions to `running`; user sees no response indefinitely.

The investigation must determine *which* of steps 2–4 actually wedges in the field (event loop frozen entirely vs. one worker loop parked on the semaphore vs. PTY-pool acquire inside `_execute_agent_session` holding a slot).

## Architectural Impact

- **New dependencies**: none (asyncio debug is stdlib; no `aiodebug` dependency — we use `loop.set_debug`).
- **Interface changes**: none to production code paths. Deliverable B adds an opt-in env flag read at worker startup; default-off means zero behavior change in steady state.
- **Coupling**: unchanged. The reproduction harness imports existing internals (`_global_session_semaphore`, `_active_workers`, `_agent_session_health_check`) read-only.
- **Data ownership**: unchanged.
- **Reversibility**: fully reversible — the diagnostic is a guarded toggle; the test is additive.

## Appetite

**Size:** Medium

**Team:** Solo dev (debugging-specialist for reproduction + async analysis), validator, documentarian

**Interactions:**
- PM check-ins: 1-2 (the branch decision — root cause found vs not reproducible — is a reporting checkpoint)
- Review rounds: 1

The cost here is reproduction difficulty and disciplined log analysis, not coding volume. The deliverables are deliberately small.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto-backed AgentSession) | `python -c "import redis,os; redis.Redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0')).ping()"` | Reproduction harness creates/queries AgentSession records |
| pytest available | `python -c "import pytest"` | Runs the reproduction/regression test |

## Solution

### Key Elements

- **Reproduction harness (Deliverable A)**: an in-process test that drives the semaphore-exhaustion scenario — register a non-`done()` worker future for a `worker_key`, drain `_global_session_semaphore` to zero, enqueue a `pending` session, run `_agent_session_health_check()`, and assert whether the session is recovered. This mechanically confirms or refutes hypothesis 1 and becomes the regression test for any future fix.
- **Opt-in event-loop wedge diagnostic (Deliverable B)**: behind `WORKER_ASYNCIO_DEBUG=1`, the worker enables `loop.set_debug(True)` and lowers `loop.slow_callback_duration` so a real-world recurrence logs the exact blocking callsite (hypothesis 2). Default-off; zero steady-state cost.
- **Findings doc + decision (Deliverable C)**: `docs/features/worker-wedge-investigation.md` records the four hypotheses, the reproduction outcome, and the binary decision with its rationale.

### Flow

Enqueue pending session → worker loop parks at `semaphore.acquire()` (slot leaked by a stuck running session) → 300s health check pending branch sees non-`done()` future → `event.set(); continue` → session stays pending → **investigation reproduces this in a test and decides: root cause confirmed (file fix issue) OR not reproducible (close #1808)**.

### Technical Approach

- **Confirm/reject hypothesis 1 (semaphore exhaustion) first** — it is the highest-probability, most directly testable cause (cf. #1537). The harness manipulates `_session_state._global_session_semaphore` and `_active_workers` directly and calls the real `_agent_session_health_check`.
- **Log analysis for hypotheses 2–4**: grep `logs/worker.log` for `[session-health]` cadence (a 300s gap with no health-check log line proves the loop itself is frozen → hypothesis 2). If health-check lines appear on schedule but the session stays pending, the wedge is in the worker loop, not the loop scheduler → hypothesis 1 or 3.
- **Hypothesis 3 (PTY-pool acquire)**: read `_execute_agent_session` and the PTYPool acquire path; check whether a session can hold a semaphore slot while blocked acquiring a PTY slot. If so, it is a variant of hypothesis 1.
- **Hypothesis 4 (set/clear race)**: re-read the guard at `agent_session_queue.py:1347-1366`; confirm whether an `event.set()` from the health check can be lost between the worker's `event.clear()` and `await event.wait()`.
- **Decision gate**: a root cause is "found" only when the harness deterministically reproduces a non-`done()` worker that cannot pop `pending` work AND the 300s backstop fails to escalate it. If, after exhausting hypotheses 1–4, no path reproduces post-#1804, the determination is "not reproducible — resolved by #1804" and #1808 closes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The health-check loop wraps each session in `try/except Exception: logger.exception(...)` (`session_health.py:~2620`). The reproduction harness must assert the pending branch's *observable behavior* (session recovered or not), not just that no exception was raised — a swallowed exception that skips recovery is itself a candidate finding.
- [ ] Deliverable B's debug-flag block must log on enable (so an operator can confirm it took effect) and fail open (never crash worker startup if `set_debug` raises).

### Empty/Invalid Input Handling
- [ ] Reproduction harness covers the boundary: semaphore at exactly 0 available, and one above 0, to confirm the pending branch behaves differently only on true exhaustion.
- [ ] `WORKER_ASYNCIO_DEBUG` unset / empty / `"0"` must all be treated as off; only `"1"`/truthy enables debug. Add an assertion for the unset case.

### Error State Rendering
- [ ] No user-visible UI in scope. The user-visible symptom is the *absence* of a response; the regression test asserts the recovery transition that would restore responses, which is the meaningful failure-state assertion here.

## Test Impact

- [ ] `tests/integration/test_worker_wedge_pending.py` — CREATE: new reproduction/regression harness (no existing file).
- [ ] No existing tests are expected to break: Deliverable B is a default-off, additive env-gated branch in `worker/__main__.py` startup, and the harness is new. If a hypothesis-1 fix later lands under a separate slug, *that* slug owns any updates to existing health-check tests.

No existing tests are modified by this investigation plan — it is additive (one new test file plus a default-off diagnostic toggle), so no current behavior or interface that existing tests assert is changed.

## Rabbit Holes

- **Building the actual fix in this plan.** The fix for any confirmed root cause (e.g. a worker-loop progress signal feeding the pending branch) is a *separate slug*, filed only after findings are reviewed. Do not start it here.
- **Generalized "is the event loop healthy" framework.** Resist building a full stall-watchdog/`BlockBuster` integration. The opt-in `set_debug` toggle is enough to capture a recurrence; a production-grade loop-stall detector is its own project.
- **Chasing #1767's U-state path.** That is an OS-level process hang with its own watchdog. This investigation is strictly the in-process, heartbeat-green wedge. Do not conflate them.
- **Re-litigating #1804.** The notify subscription is ruled out; do not re-instrument it.

## Risks

### Risk 1: The symptom is not reproducible in a test harness
**Impact:** The investigation cannot confirm hypothesis 1 deterministically; the decision defaults toward "not reproducible".
**Mitigation:** This is an acceptable, in-scope outcome per the acceptance criteria. The harness still documents the *attempted* scenarios, and Deliverable B ensures a real-world recurrence is captured next time. "Not reproducible" is a valid terminal state, not a failure.

### Risk 2: Reproduction confirms a root cause but the harness is non-deterministic (timing-dependent)
**Impact:** A flaky regression test that intermittently false-fails CI.
**Mitigation:** Drive the scenario via direct state manipulation (drain the semaphore explicitly, register the worker future explicitly) rather than racing real concurrency. Deterministic setup → deterministic assertion.

### Risk 3: Investigation scope creep into fixing
**Impact:** Plan balloons; the contingent fix gets half-built.
**Mitigation:** The fix is an explicit No-Go; the terminal task only *files* the fix issue (if warranted), it does not build it.

## Race Conditions

### Race 1: Health-check `event.set()` lost between worker `event.clear()` and `await event.wait()`
**Location:** `agent/agent_session_queue.py:1347-1366` (clear) and `2557-2566` (set in health check)
**Trigger:** Health check sets the event while the worker is between `event.clear()` and `await event.wait()`.
**Data prerequisite:** A `pending` session exists for the `worker_key`.
**State prerequisite:** Worker loop is in the no-work drain window.
**Mitigation (to verify, not implement):** The worker already does a synchronous `_has_pending` re-check before clearing (`agent_session_queue.py:1347-1366`). The investigation must confirm this guard closes the window; if it does not, that is hypothesis-4's finding. No new mitigation is built in this plan.

## No-Gos (Out of Scope)

- [EXTERNAL] Implementing the production fix for any confirmed root cause (e.g. adding a worker-loop progress/liveness signal so the pending branch can escalate a parked worker). The fix approach requires human review of the investigation findings before a fix issue is opened and built; this plan's terminal task *files* that fix issue but deliberately does not implement it. (Advisory No-Go — no anti-criterion: the deliverable is a knowledge decision plus a filed issue, and the absence of a fix is the expected state until findings are reviewed.)
- [EXTERNAL] Reproducing the wedge against the live production worker. Requires running against a machine and traffic the agent cannot drive deterministically; the in-process harness substitutes for it, and Deliverable B captures the live case if it recurs.

## Update System

No update system changes required for the core investigation. Deliverable B adds an **opt-in** `WORKER_ASYNCIO_DEBUG` env var read at worker startup — it is default-off and needs no propagation (operators set it ad hoc when diagnosing). Add one commented placeholder line to `.env.example` (`# WORKER_ASYNCIO_DEBUG=` with a one-line comment) so the flag is discoverable; no `scripts/update/run.py` or `migrations.py` change is needed since there is no schema, dependency, or service change.

## Agent Integration

No agent integration required — this is a worker-internal investigation. No new CLI entry point in `pyproject.toml [project.scripts]`, no `.mcp.json` change, and the bridge does not call any new code. The diagnostic toggle is an operator-facing env var, not an agent-invoked surface. The reproduction harness is a pytest test, not an agent tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worker-wedge-investigation.md` recording: the four hypotheses, the reproduction-harness scenario and outcome, the `WORKER_ASYNCIO_DEBUG` diagnostic and how to use it, and the binary decision (root cause found → fix-issue link, OR not reproducible → close rationale).
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on the new test explaining what wedge it reproduces and what a pass/fail means.
- [ ] Comment on the `WORKER_ASYNCIO_DEBUG` block in `worker/__main__.py` citing this investigation and the asyncio-debug research.

## Success Criteria

- [ ] Reproduction harness `tests/integration/test_worker_wedge_pending.py` exists and runs deterministically (no timing flake).
- [ ] All four hypotheses (semaphore exhaustion, event-loop block, PTY-pool acquire, set/clear race) are explicitly confirmed or rejected with evidence in the findings doc.
- [ ] A binary decision is recorded: **root cause found** (with a filed fix issue linked) OR **not reproducible — resolved by #1804** (with #1808 closed and the rationale documented).
- [ ] `WORKER_ASYNCIO_DEBUG=1` diagnostic is implemented, default-off, fails open, and is documented.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`) — `docs/features/worker-wedge-investigation.md` created and indexed.

## Team Orchestration

The lead agent orchestrates; it does not investigate directly.

### Team Members

- **Investigator (worker-wedge)**
  - Name: wedge-investigator
  - Role: Reproduce the wedge, confirm/reject the four hypotheses via the harness + log analysis, author the findings doc and decision.
  - Agent Type: debugging-specialist
  - Resume: true

- **Builder (diagnostic-toggle)**
  - Name: diag-builder
  - Role: Implement the default-off `WORKER_ASYNCIO_DEBUG` diagnostic in `worker/__main__.py` and the `.env.example` placeholder.
  - Agent Type: builder
  - Resume: true

- **Validator (investigation)**
  - Name: wedge-validator
  - Role: Verify the harness is deterministic, the diagnostic is fail-open and default-off, the decision is recorded, and docs exist.
  - Agent Type: validator
  - Resume: true

- **Documentarian (findings)**
  - Name: findings-doc
  - Role: Create `docs/features/worker-wedge-investigation.md` and index it.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build reproduction harness
- **Task ID**: build-repro-harness
- **Depends On**: none
- **Validates**: tests/integration/test_worker_wedge_pending.py (create)
- **Informed By**: spike-1 (confirmed: pending branch never escalates a non-done worker), #1537 (slot-orphan precedent)
- **Assigned To**: wedge-investigator
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Drive hypothesis 1: register a non-`done()` worker future in `_active_workers[worker_key]`, drain `_global_session_semaphore` to 0, enqueue a `pending` AgentSession, run `await _agent_session_health_check()`, assert whether the session is recovered.
- Add boundary cases: semaphore at 0 (exhausted) vs 1 (available); `WORKER_ASYNCIO_DEBUG` unset.
- Clean up all test AgentSession records via Popoto (`.delete()`), never raw Redis. Use a `test-wedge-` project_key prefix.

### 2. Confirm/reject all four hypotheses
- **Task ID**: analyze-hypotheses
- **Depends On**: build-repro-harness
- **Assigned To**: wedge-investigator
- **Agent Type**: debugging-specialist
- **Parallel**: false
- Hypothesis 1 (semaphore exhaustion): result from the harness.
- Hypothesis 2 (event-loop block): analyze `logs/worker.log` for `[session-health]` cadence gaps; document the detection method.
- Hypothesis 3 (PTY-pool acquire holding a slot): read `_execute_agent_session` + PTYPool acquire; determine if a slot can be held while blocked on a PTY.
- Hypothesis 4 (set/clear race): re-read `agent_session_queue.py:1347-1366`; confirm the `_has_pending` guard closes the window.
- Record each verdict with evidence.

### 3. Implement opt-in event-loop wedge diagnostic
- **Task ID**: build-diagnostic
- **Depends On**: none
- **Validates**: tests/integration/test_worker_wedge_pending.py (env-flag handling assertions)
- **Informed By**: Research (asyncio set_debug / slow_callback_duration; aiodebug/BlockBuster pattern)
- **Assigned To**: diag-builder
- **Agent Type**: builder
- **Parallel**: true
- In `worker/__main__.py` startup: if `WORKER_ASYNCIO_DEBUG` is truthy, call `loop.set_debug(True)` and set `loop.slow_callback_duration` (tunable, default ~0.1s). Log on enable; fail open if it raises.
- Add `# WORKER_ASYNCIO_DEBUG=` placeholder + comment to `.env.example`.

### 4. Decision + (conditional) fix-issue filing
- **Task ID**: record-decision
- **Depends On**: analyze-hypotheses
- **Assigned To**: wedge-investigator
- **Agent Type**: debugging-specialist
- **Parallel**: false
- If a root cause is deterministically reproduced: file a follow-up fix issue (new slug) describing the confirmed cause and proposed fix (e.g. worker-loop progress signal feeding the pending branch); link it from the findings doc. Do NOT implement the fix.
- If not reproducible: record "not reproducible — resolved by #1804" rationale and prepare #1808 for closure.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: analyze-hypotheses, build-diagnostic, record-decision
- **Assigned To**: findings-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-wedge-investigation.md` (hypotheses, harness, diagnostic usage, decision).
- Add entry to `docs/features/README.md` index.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-repro-harness, analyze-hypotheses, build-diagnostic, record-decision, document-feature
- **Assigned To**: wedge-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the harness is deterministic (run it 3×, same result).
- Confirm the diagnostic is default-off and fails open.
- Confirm the decision is recorded and docs exist.
- Generate the final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reproduction harness exists | `test -f tests/integration/test_worker_wedge_pending.py` | exit code 0 |
| Harness runs deterministically | `pytest tests/integration/test_worker_wedge_pending.py -q` | exit code 0 |
| Diagnostic is default-off (no set_debug at import) | `grep -n "WORKER_ASYNCIO_DEBUG" worker/__main__.py` | output contains WORKER_ASYNCIO_DEBUG |
| Findings doc created | `test -f docs/features/worker-wedge-investigation.md` | exit code 0 |
| Findings doc indexed | `grep -c "worker-wedge-investigation" docs/features/README.md` | output > 0 |
| Env placeholder present | `grep -c "WORKER_ASYNCIO_DEBUG" .env.example` | output > 0 |
| No fix built in this plan (pending branch unchanged) | `git diff --name-only main -- agent/session_health.py \| wc -l` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Diagnostic scope** — Is the default-off `WORKER_ASYNCIO_DEBUG` toggle (Deliverable B) wanted as a permanent diagnostic, or should the investigation be pure analysis (harness + findings doc only) with no production-code touch? It is low-cost and directly aids capturing a real recurrence, but it does add one guarded branch to worker startup.
2. **"Not reproducible" closure** — If the harness cannot reproduce the wedge post-#1804, is documenting the attempted scenarios + shipping the diagnostic sufficient to close #1808, or do you want a defined production observation window (e.g. "no recurrence in N days with the diagnostic available") before closing?
3. **Fix-issue pre-filing** — If a root cause is confirmed, should the follow-up fix issue be filed automatically by the investigation (Task 4), or do you want to review the findings and decide the fix approach yourself before any issue is opened?
