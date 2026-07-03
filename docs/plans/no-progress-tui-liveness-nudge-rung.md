---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1878
last_comment_id: IC_kwDOEYGa088AAAABInmWRw
revision_applied: true
---

# Redefine no_progress recovery around TUI liveness (Part A) — continue-nudge rung split to #1879

## Scope note (2026-07-03, second revision)

This plan originally scoped **two** coordinated changes: **Part A** (redefine
no_progress around TUI liveness by stamping PTY liveness during priming) and
**Part B** (a `continue`-nudge recovery rung before kill). The second re-critique
surfaced a **structural blocker**: Part B is dead-on-arrival because **no
`no_progress` recovery state has a reachable steering consumer** (see
[## Structural Finding](#structural-finding--why-the-continue-nudge-is-split-out)).
Grounding that finding in the actual code proved that a `continue` nudge before
kill is genuinely infeasible on current `main` without new infrastructure (a
mid-run steering drain inside the blocking turn/prime read loop). Therefore:

- **Part A ships in this plan.** It is well-specified, independently correct, and
  fully fixes the root-caused priming failure (session `c7bd42…`).
- **Part B is split to follow-up issue #1879** ("Mid-run steering drain to enable
  a continue-nudge recovery rung for wedged granite sessions"). The nudge
  acceptance criterion is transferred there, not silently dropped.

## Problem

Granite eng/SDLC sessions drive the real interactive `claude` TUI over a PTY inside a
per-session container. A background session-health monitor (`agent/session_health.py`)
watches every running session and, if it sees "no forward progress," recovers it: cancel
the worker task, re-queue to `pending`, and after `MAX_RECOVERY_ATTEMPTS` (2) finalize as
`failed`.

**Current behavior:** "No progress" during the startup window is defined as a pure
elapsed-time test — `_never_started_past_grace()` fires at `NEVER_STARTED_GRACE_SECS (120)
+ NEVER_STARTED_CONFIRM_MARGIN_SECS (30) = 150s` with zero SDK turns. But a granite
session doing a legitimate Opus persona cold-start (`_prime_session`) routinely takes
minutes, and the TUI shows *visible signs of life* (streaming repaints) the entire time.
The monitor kills healthy-but-slow sessions on a stopwatch, re-queues, cold-respawns the
PTY pair, deterministically re-hits the same slow start, and finalizes `failed`.

Concrete failure (root-caused in the issue): session `c7bd42e41cd0423f8bb18c07e9c4ec15`
was killed at ~154s (`kind=no_progress: never_started past grace`) on **both** recovery
attempts, → `failed`. Every liveness field (`last_pty_activity_at`,
`last_pty_read_loop_at`, `last_stdout_at`, `last_tool_use_at`) was `None` the whole time.

**Issue #1792 already tried to fix this** (PR #1798) by adding the `_prime_pty_alive()`
deferral: defer the kill if the PTY read loop is alive AND the screen has recent activity.
**That fix is defeated** — `_prime_pty_alive()` keys on `last_pty_read_loop_at` and
`last_pty_activity_at`, but neither is stamped during `_prime_session()`, so the entire
prime window is invisible to the deferral and it can never engage.

**Desired outcome (Part A, this plan):** A granite session whose normalized TUI frame is
still *changing* is never killed on elapsed time alone during priming. A truly-dead prime
(no normalized-frame change) is still reaped within the container's
`STARTUP_HARD_CEILING_S (600s)`. Non-PTY (SDK/headless) sessions keep their age-only
never-started kill.

**Deferred outcome (Part B, → #1879):** A genuinely wedged *post-prime* session is first
**nudged** (cheap `continue` steering message) and only killed+respawned (expensive) if it
stays frozen. This requires a mid-run steering drain that does not exist today — see the
Structural Finding.

## Freshness Check

**Baseline commit (original plan):** `6de1f531` (`git rev-parse HEAD` at plan time).
**Re-critique / second-revision baseline:** `8c8d64b0` (plan commit at the second revision).
**Third-revision (split) baseline:** `9f69d114` (plan commit splitting Part B to #1879). All
file:line references below were independently re-verified against this HEAD, and tracking
issue #1878 was re-titled + annotated (Solution sketch B / OQ3 / AC #4 marked out-of-scope,
transferred to #1879) so the tracking artifact matches the Part-A-only plan scope.
**Issue filed at:** 2026-07-03T05:48:09Z (createdAt)
**Disposition:** Unchanged (issue filed today; only doc/plan commits landed on main since,
none touching the referenced code paths).

**File:line references re-verified against `8c8d64b0`:**
- `agent/session_health.py:545` `_prime_pty_alive` — **still holds.** Branch 2 (`last_pty_read_loop_at is None → False`, non-PTY escape) and Branch 4 (`last_pty_activity_at` within `NEVER_STARTED_PTY_LIVENESS_SECS`) confirmed present.
- `agent/session_health.py:1088` `_never_started_past_grace` — **still holds.** `sdk_ever_output` derived from `last_tool_use_at`/`last_turn_at`; threshold `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`.
- `agent/session_health.py:4090` — the D0 kill gate that calls `_prime_pty_alive(fresh_ns, now)` and `continue`s on defer, else calls `_apply_recovery_transition(...)`. Confirmed at L4090–4141; the kill emits `reason="no progress signal observed (never_started past grace)"` at L4136.
- `agent/session_health.py:3221` `no_progress` running-scan producer — **confirmed.** Guarded by `in_scope_handle is None` (L3222); reason literal `"no progress signal, orphaned running row (no in-scope handle, #944), …"` at L3229.
- `agent/session_health.py:2139` `_apply_recovery_transition` — **still holds** (offset from the original pointer; the `no_progress` branch and `recovery_attempts` bump are inside it).
- `agent/granite_container/container.py:1642` `_prime_session` — **CONFIRMED DEFECT.** The three `read_until_idle` calls at L1681, L1699, L1725 do **not** pass `on_read_iteration`. The steady-state call at L1214–1217 **does** pass `on_read_iteration=self._pty_read_iteration_cb`. So priming is blind to liveness stamping.
- `agent/granite_container/container.py:2079` `_poll_steering` — **confirmed sole external-steering drain**, called only at the top of the steady-state `for turn` loop (between completed turns).
- `agent/granite_container/container.py:1302` `_await_turn_end` — **confirmed.** The per-tick liveness pump (L1328 `read_until_idle` + L1331 `_fire_pty_read`) polls the hook edge file (`consumer.poll()`), NOT the external steering queue.
- `agent/granite_container/bridge_adapter.py:620` `_poll_steering` closure + `:937` `_make_pty_read_callback` — **confirmed.** The read callback stamps `last_pty_read_loop_at` unconditionally and `last_pty_activity_at` only on **normalized** frame change (#1768). This is the writer priming must drive.
- `agent/agent_session_queue.py:1790` progress-deadline cancel scope + `:1500` `_session_progress_ts` — **confirmed** (used in the Structural Finding).
- `agent/steering.py:37` `push_steering_message(...)` — confirmed signature.

**Cited sibling issues/PRs re-checked:**
- #1792 — CLOSED 2026-06-25 (PR #1798). Part A **completes** that incomplete fix.
- #1768 — CLOSED (frame normalization). Its `_normalize_pty_buffer` is the anti-spinner guard Part A must NOT regress.
- #1724 — CLOSED (fresh-heartbeat blinding). The D0 gate at L4090 is #1724-origin.
- #1779 — mid-run steering injection (the `_poll_steering` top-of-turn drain). The follow-up #1879 extends this.

**Notes:** The claim that `_prime_session` emits no liveness stamps is verified true on
current main. The `_pty_read_iteration_cb` writer already exists (post-#1843 Gap B) — Part
A wires it into priming, not building a new writer.

## Prior Art

- **Issue #1792 / PR #1798**: `gate never_started kill on PTY liveness`. Added `_prime_pty_alive()` deferral keyed on `last_pty_read_loop_at` + `last_pty_activity_at`. **Failed to actually protect priming** because those fields are never written during `_prime_session()`. Part A makes the deferral engage as originally intended.
- **Issue #1768**: `_normalize_pty_buffer()` strips the spinner glyph + elapsed-seconds counter before diffing, so an animating-but-wedged TUI yields a stable string. Content change = genuine liveness; spinner motion = not. Must be preserved.
- **Issue #1724 / #1226 / #1356**: the never-started D0 gate and the no-output budget gates that surround it. Part A must live inside this established recovery structure without weakening those gates.
- **#1843 Gap B**: introduced `_pty_read_iteration_cb` (the throttled per-iteration read callback) and wired it into the steady-state `read_until_idle` call. Priming was left unwired — the gap Part A closes.
- **#1779**: mid-run steering injection at the steady-state turn boundary (`_poll_steering`). This is the seam the deferred #1879 must extend to reach a wedged (mid-turn-parked) session.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1798 (#1792) | Added `_prime_pty_alive()` deferral to the D0 never-started kill gate, keyed on `last_pty_read_loop_at` + `last_pty_activity_at`. | The deferral reads two liveness fields that `_prime_session()` never stamps. Priming is a blocking `read_until_idle()` sequence that (unlike steady-state) never passes `on_read_iteration`, so the fields stay `None` for the entire prime window and the deferral's Branch 4 (alive case) can never be reached — Branch 2 (`last_pty_read_loop_at is None`) short-circuits to kill-eligible. The fix protected a window that produced no evidence of its own liveness. |

**Root cause pattern:** The deferral and the field-stamping were built in different work
streams (#1792 vs #1843). The consumer (`_prime_pty_alive`) assumed a producer
(`_pty_read_iteration_cb` wired into priming) that was only ever wired into steady-state.
Part A connects the existing producer to the prime path — no new machinery.

## Data Flow

1. **Entry point**: `BridgeAdapter` (production) constructs `Container` with `on_pty_read` set → `Container.__init__` builds `self._pty_read_iteration_cb = _throttle(self._fire_pty_read_raw, 1.0)` (`container.py:826`).
2. **Priming (defect surface)**: `Container.run()` → `_spawn_pair()` → `_prime_session(pty, slash_cmd)` (`container.py:1642`). Its three `read_until_idle` calls (L1681 trust-dismiss loop, L1699 pre-write, L1725 post-write, up to `PRIME_POST_WRITE_TIMEOUT_S=360s`) run **without** `on_read_iteration`. → no callback fires → `_fire_pty_read_raw` never called → `on_pty_read` never called → `last_pty_read_loop_at`/`last_pty_activity_at` stay `None`.
3. **Stamping (what should happen)**: with `on_read_iteration=self._pty_read_iteration_cb` wired, each inner poll tick (throttled to ≤1/s) calls `bridge_adapter._make_pty_read_callback` → stamps `last_pty_read_loop_at` (unconditional) + `last_pty_activity_at` (only when `_normalize_pty_buffer(buffer)` changed — #1768 gate).
4. **Health check (consumer)**: `_agent_session_health_check` loop → D0 gate at `session_health.py:4090`: `_never_started_past_grace(entry)` true → re-read `fresh_ns` → `_prime_pty_alive(fresh_ns, now)`. With fields now fresh: Branch 3 (read-loop within `HEARTBEAT_FRESHNESS_WINDOW=90s`) and Branch 4 (activity within `NEVER_STARTED_PTY_LIVENESS_SECS=90s`) pass → returns `True` → `continue` (defer kill).
5. **Frozen frame (genuine death)**: TUI stops repainting → `_normalize_pty_buffer` stable → `last_pty_activity_at` stops advancing → after 90s Branch 4 returns `False` → `_prime_pty_alive` returns `False` → recovery proceeds.
6. **Post-prime wedge (deferred to #1879)**: a genuinely wedged *steady-state* session would ideally be nudged with a `continue` before kill. On current main there is no code path where such a nudge can be drained (see Structural Finding), so this rung is split to #1879 and NOT built here.
7. **Backstop**: if the read loop never stamps at all (totally dead prime), `_prime_pty_alive` Branch 2/3 stays kill-eligible AND the container's own `STARTUP_HARD_CEILING_S=600s` exits the run — no infinite hang.

## Structural Finding — why the continue-nudge is split out

The second re-critique flagged that Part B's `continue`-nudge repeats the #1798
"assumed-but-unwired consumer" anti-pattern. Grounding the claim in the actual code
confirmed it: **there is no `no_progress` recovery state on current `main` where a
genuinely wedged granite session ALSO has a live, reachable steering consumer.**

**The only external-steering consumer for a granite PTY session** is the `_poll_steering`
closure (`bridge_adapter.py:620`, `pop_all_steering_messages`), wired into `Container.run()`
at `container.py:2081`. It is invoked **only at the top of the steady-state `for turn`
loop** (`container.py:2074-2139`) — i.e. **between completed turns**, before the blocking
per-turn wait. (SDK/headless sessions have a separate consumer at
`session_executor.py:1689`, but Part A/B explicitly exclude non-PTY sessions.) The inner
`claude` TUI's own `.claude/hooks` do **not** drain the outer AgentSession's steering queue.

**Where a wedged session actually parks:** while waiting for a turn to complete, the loop is
inside `_await_turn_end` (`container.py:1302`). Its per-tick liveness pump
(`read_until_idle` at L1328 + `_fire_pty_read` at L1331) polls only the **hook edge file**
(`consumer.poll()`) — it does **not** drain the external steering queue. So a `continue`
pushed while a session is wedged sits undrained until a turn completes, which by definition
is not happening.

**Neither `no_progress` producer that reaches `_apply_recovery_transition`'s `no_progress`
branch has a live consumer:**

1. **D0 / never-started** (`session_health.py:4136`, reason literal `"no progress signal
   observed (never_started past grace)"`): fires *mid-`_prime_session`* — the blocking
   `read_until_idle` sequence runs **before** the steady-state loop, so `_poll_steering`
   does not exist yet. No drain. (Already excluded in the prior revision.)
2. **#944 running-scan orphan** (`session_health.py:3229`): fires **only when
   `in_scope_handle is None`** (`session_health.py:3222`) — a crashed-worker `running` row
   whose worker_key was later reused by a *different* live worker. There is **no live
   `Container` / `exec_task` executing this session in any process**, so `steering:{id}`
   has no consumer anywhere. No drain. (This is the producer the prior revision scoped to —
   it is exactly the one the new blocker exposed as consumer-less.)

**The live-session no-progress killer is a different mechanism** — the progress-deadline
cancel scope (`agent_session_queue.py:1790`, `reason_kind="progress_deadline"`, NOT
`no_progress`). It runs above a live `exec_task`, but it fires only after
`SESSION_PROGRESS_DEADLINE_S` (1800s) of zero `last_tool_use_at`/`last_turn_at`/
`last_pty_activity_at` advancement (`_session_progress_ts`, `agent_session_queue.py:1500`).
Reaching `_poll_steering` at the top of a turn would itself produce PTY activity, so a
session stale enough to hit this deadline is — by construction — parked inside
`_await_turn_end` with the drain unreachable.

**Root:** the drain point (top of a *completed* turn) and the wedge condition (a turn that
never completes) are **mutually exclusive by construction**. A session reaching the drain is
making progress and is never a recovery target; a recovery target never reaches the drain.
Making the nudge work requires **new infrastructure** — a mid-run steering drain inside the
blocking `_await_turn_end` liveness pump (or via `read_until_idle`'s `on_read_iteration`),
which is precisely the "mid-prime steering drain" this plan lists as a No-Go. That is
tracked in **#1879** and is out of scope here.

## Architectural Impact

- **New dependencies**: none. Part A reuses `_pty_read_iteration_cb`, `_normalize_pty_buffer`, and `_prime_pty_alive` — all existing.
- **Interface changes**: `_prime_session()` gains no new public signature; internally its `read_until_idle` calls pass the existing per-iteration callback. No new `AgentSession` field, no new constant (the deferred nudge rung's `last_continue_nudge_at` and `CONTINUE_NUDGE_REPRIEVE_SECS` move to #1879).
- **Coupling**: slightly tighter between prime and the liveness-stamping writer — but that coupling is *intended* (it's the whole point of #1792). No new cross-module coupling.
- **Reversibility**: high. Part A is env-reversible via existing kill-switches (`NEVER_STARTED_PTY_LIVENESS_SECS <= 0` disables the deferral).

## Appetite

**Size:** Small (Part A alone — the nudge rung that made this Medium moved to #1879).

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirm the Part-A / Part-B split landed cleanly)
- Review rounds: 1 (async/liveness correctness is subtle — one focused review)

## Prerequisites

No prerequisites — this work has no external dependencies (purely internal session-health
and container behavior, no new secrets or services).

## Solution

### Key Elements

- **Prime liveness stamping (part A)**: `_prime_session()` fires the same throttled per-iteration read callback the steady-state loop uses, so priming stamps `last_pty_read_loop_at` (unconditional) and `last_pty_activity_at` (on normalized-frame change). This alone makes the existing `_prime_pty_alive()` deferral engage and protect slow-but-alive cold starts.
- **Frozen-frame kill (part A, no new constant)**: the kill fires only when the normalized frame is frozen past the existing staleness windows — `_prime_pty_alive` already encodes this via `HEARTBEAT_FRESHNESS_WINDOW (90s)` on the read loop and `NEVER_STARTED_PTY_LIVENESS_SECS (90s)` on activity. Spinner-only animation does NOT count (normalization already strips it — #1768 preserved).
- **Non-PTY preservation (part A)**: `_prime_pty_alive` Branch 2 (`last_pty_read_loop_at is None → kill-eligible`) already preserves age-only never-started kill for SDK/headless sessions — no change needed there, only a regression test to lock it.

### Flow

Slow granite cold start → prime `read_until_idle` ticks fire throttled callback → liveness
fields advance → health check D0 gate → `_prime_pty_alive` returns True → **defer kill**
(session keeps priming) → prime completes → steady-state.

Genuinely frozen prime (no normalized-frame change) → `last_pty_activity_at` goes stale
(90s) → `_prime_pty_alive` returns False → kill fires, backstopped by
`STARTUP_HARD_CEILING_S (600s)`.

Non-PTY (SDK/headless) session → `last_pty_read_loop_at is None` → `_prime_pty_alive`
Branch 2 returns False → age-only never-started kill retained exactly.

### Technical Approach

**Part A — wire the read callback into priming (resolves OQ1).**
Pass `on_read_iteration=self._pty_read_iteration_cb` into `_prime_session()`'s
`read_until_idle` calls. This is the least-code option and cannot double-stamp:

- The callback is **shared instance state** — `_throttle(self._fire_pty_read_raw, PTY_READ_ITER_MIN_INTERVAL_S=1.0)` — so a stamp in the prime window and a stamp in the steady-state window are rate-limited by the same throttle. There is no separate throttle instance to race.
- When no `on_pty_read` writer is wired (unit tests without a bridge adapter), `_pty_read_iteration_cb is None`, so passing it is **byte-identical to the current behavior** — no test regression.
- Do NOT restructure priming to run inside the steady-state loop (rejected: large blast radius, reorders the trust-dialog/pre-write/post-write sequence that #1612 and #1644 depend on).
- **Which of the three calls?** Wire the callback into **all three** `read_until_idle` calls in `_prime_session` (L1681 trust-dismiss, L1699 pre-write, L1725 post-write). The post-write call (L1725, up to 360s) is where the 150s grace is blown, so it is mandatory; wiring the two short pre-write reads too is near-free and keeps the whole prime window observable (belt-and-suspenders for a slow trust-dialog dismiss).

**Part A — frozen-frame kill window (resolves OQ2).**
**No new prime-specific stall constant is introduced.** The frozen-frame reap is already
governed by two existing env-overridable constants that `_prime_pty_alive` reads:
`HEARTBEAT_FRESHNESS_WINDOW (90s)` (read-loop staleness, Branch 3) and
`NEVER_STARTED_PTY_LIVENESS_SECS (90s)` (activity staleness, Branch 4). Once the normalized
frame freezes, activity stops advancing and within 90s the deferral stops → kill fires.
Both are `< STARTUP_HARD_CEILING_S (600s)`, satisfying the "truly-dead prime still gets
reaped" constraint; and the container's own `STARTUP_HARD_CEILING_S` is the hard backstop
if the read loop never stamps at all. Reusing existing constants avoids a fourth
overlapping timeout in an already-crowded hierarchy. (If a distinct prime-freeze window is
ever wanted, the reuse leaves `NEVER_STARTED_PTY_LIVENESS_SECS` as the single tuning knob.)

**Part A — non-PTY preservation (resolves OQ4).**
`_prime_pty_alive` Branch 2 already returns `False` (kill-eligible) when
`last_pty_read_loop_at is None`, preserving the age-only never-started kill for SDK/headless
sessions — no change needed there, only a regression test to lock it.

**Part B (continue-nudge rung) — deferred to #1879 (resolves OQ3).**
Not built in this plan. The Structural Finding proves the nudge has no live consumer in any
`no_progress` recovery state, so it requires the mid-run steering-drain infrastructure
tracked in #1879. This plan does not add `last_continue_nudge_at`, `CONTINUE_NUDGE_REPRIEVE_SECS`,
or any nudge counters — those are #1879's surface.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The prime callback wiring must not let a raising callback break the read loop — `test_read_until_idle_per_iteration.py::test_raising_callback_does_not_break_read_loop` already covers this at the `read_until_idle` layer; add a prime-level assertion that a raising `_pty_read_iteration_cb` does not abort `_prime_session`.

### Empty/Invalid Input Handling
- [ ] Empty normalized frame (buffer strips to "") during prime → `last_pty_activity_at` correctly does NOT advance (no false liveness) — covered by the #1768 normalization; add an assertion.

### Error State Rendering
- [ ] Not user-visible (internal recovery). The observable "error state" for Part A is the existing `never_started_pty_deferred` counter on defer; assert it increments when the deferral engages.

## Test Impact

- [ ] `tests/unit/test_never_started_recovery.py` — UPDATE: existing tests assume age-only never-started kill fires at 150s for granite sessions. Add cases where liveness fields are fresh (deferral engages, no kill) and where they are stale (kill still fires). Keep the non-PTY age-only case as a hard regression assertion (OQ4).
- [ ] `tests/unit/granite_container/test_persona_priming.py` — UPDATE: add a test asserting `_prime_session()` stamps `last_pty_read_loop_at`/`last_pty_activity_at` mid-prime (via a fake `on_pty_read` writer + a repainting fake PTY). This is the AC "verified by a test asserting the fields advance mid-prime."
- [ ] `tests/unit/granite_container/test_read_until_idle_per_iteration.py` — UPDATE (or add sibling): assert the prime path passes `on_read_iteration` (regression lock so a future refactor can't silently drop it).
- [ ] `tests/unit/granite_container/test_pty_read_iteration_throttle.py` — VERIFY (likely no change): confirm the shared throttle prevents a prime+steady-state double-stamp storm; add an assertion if not already covered.
- [ ] `tests/unit/test_session_stall_classifier.py` — VERIFY: no constant values change; confirm `NEVER_STARTED_PTY_LIVENESS_SECS`/`HEARTBEAT_FRESHNESS_WINDOW` reuse is asserted. (The `CONTINUE_NUDGE_REPRIEVE_SECS` test that was here moves to #1879.)
- [ ] No `tests/unit/test_continue_nudge_rung.py` in this plan — that suite is #1879's, gated on the mid-run-drain infrastructure.

## Rabbit Holes

- **Restructuring priming into the steady-state read loop.** Tempting ("unify the read paths") but the trust-dialog dismiss / pre-write / post-write sequence in `_prime_session` is load-bearing (#1612, #1644). Just pass the callback into the existing reads.
- **Inventing a new prime-specific stall constant.** The timeout hierarchy is already crowded (150s kill / 360s prime / 600s ceiling). Reuse `NEVER_STARTED_PTY_LIVENESS_SECS` + `HEARTBEAT_FRESHNESS_WINDOW`; do not add a fourth overlapping clock.
- **Building the continue-nudge rung here.** The nudge has no live steering consumer in any `no_progress` recovery state (see Structural Finding) — a `continue` pushed to a wedged or orphaned session is never drained. Building it now would repeat the #1798 unwired-consumer anti-pattern. It is split to #1879, which owns the mid-run steering-drain infrastructure the nudge depends on.
- **Making the nudge drain mid-prime / mid-turn.** A blocking `read_until_idle` during priming (and a blocking `_await_turn_end` during steady-state) has no external-steering drain, so a `continue` nudge cannot be consumed until a turn boundary is reached — which a wedged session never reaches. Do NOT try to graft a mid-run steering drain into this plan; that is #1879's whole scope.
- **Counting spinner motion as liveness.** `_normalize_pty_buffer` already strips the spinner + elapsed counter. Reuse it; never diff the raw buffer.

## Risks

### Risk 1: Prime callback stamps a stale/misleading `last_pty_activity_at`, masking a genuinely wedged prime
**Impact:** A prime that repaints its spinner but produces no real content could look alive forever and never get reaped.
**Mitigation:** `_make_pty_read_callback` stamps `last_pty_activity_at` only on **normalized**-frame change (`_normalize_pty_buffer` strips spinner + counter). A spinner-only prime yields a stable normalized string → activity goes stale → deferral stops at 90s. Locked by a test with a synthetic spinner-animating-but-content-static buffer (#1768 regression).

### Risk 2: Prime callback stamp storms Redis (prime + steady-state double-stamp)
**Impact:** Two stamp sources could double-write liveness fields at high frequency.
**Mitigation:** The callback is a single shared throttled instance (`_throttle(..., 1.0)`), so all stamps across prime and steady-state are rate-limited to ≤1/s by the same throttle. Verified by `test_pty_read_iteration_throttle.py`.

### Risk 3: Part A weakens the age-only kill for non-PTY sessions
**Impact:** SDK/headless sessions could hang past their 150s grace if the deferral swallowed them.
**Mitigation:** `_prime_pty_alive` Branch 2 gates on `last_pty_read_loop_at is not None`. Non-PTY sessions are never deferred. Hard regression test.

## Race Conditions

### Race 1: Prime callback stamp vs. health-check re-read
**Location:** `container.py` `_prime_session` throttled callback vs. `session_health.py:4092` `fresh_ns = AgentSession.get_by_id(...)`.
**Trigger:** The health check re-reads `fresh_ns` between two prime callback stamps.
**Data prerequisite:** The re-read (`fresh_ns`) must see the most recent stamp to defer correctly.
**State prerequisite:** Stamps are ≤1/s (throttle); the health-check tick is coarser.
**Mitigation:** The D0 gate already re-reads `fresh_ns` fresh from Redis right before calling `_prime_pty_alive`, so it sees the latest persisted stamp. A stamp landing microseconds after the read only delays the deferral by one health-check tick — harmless (the session is alive either way). No mitigation beyond the existing re-read.

## No-Gos (Out of Scope)

- **[SEPARATE-SLUG → #1879] The `continue`-nudge recovery rung.** Split to follow-up issue #1879. It requires a mid-run steering drain (so a wedged, live-but-parked Container can consume a `continue` without a turn boundary) that does not exist today. This plan ships Part A only.
- Mid-run/mid-prime steering drain — **not** built here. It is #1879's whole scope.

Everything else relevant to Part A (callback wiring, frozen-frame reap via existing
constants, non-PTY preservation, and the five Part-A acceptance-criteria tests) is in scope
for this plan.

## Update System

- **No `/update` skill or `scripts/update/run.py` changes required** — this is a purely internal session-health/container behavior change; no new deps, config files, or propagated artifacts.
- **No new `AgentSession` field and no data migration** — Part A adds no field (the deferred `last_continue_nudge_at` moves to #1879). No `scripts/update/migrations.py` entry is needed.
- **No new env constant** — Part A reuses `NEVER_STARTED_PTY_LIVENESS_SECS` and `HEARTBEAT_FRESHNESS_WINDOW` (the deferred `CONTINUE_NUDGE_REPRIEVE_SECS` moves to #1879).

## Agent Integration

No agent integration required — this is a worker/session-health-internal change. It does not
add or modify any CLI entry point in `pyproject.toml [project.scripts]`, any MCP server in
`mcp_servers/`, any `.mcp.json` registration, or any bridge (`bridge/telegram_bridge.py`)
call path. Part A only wires an existing internal per-iteration read callback into the prime
read loop; no new surface is exposed to the conversational agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document that priming now stamps PTY liveness fields and that the never-started kill is deferred while the normalized frame changes.
- [ ] Document the structural finding (why the `continue`-nudge is not built here): the external steering queue is drained only between completed turns, so a wedged `no_progress` session has no live consumer — the nudge is tracked in #1879. Cross-reference #1792 (deferral) and #1768 (normalization) as the prior art Part A completes.

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for these internals.

### Inline Documentation
- [ ] Docstring on `_prime_session` noting it now wires `on_read_iteration` for liveness stamping (and why — completes #1792).

## Success Criteria

- [ ] A granite session whose TUI frame keeps changing during a long (>150s) Opus cold-start prime is **not** killed by `no_progress`/`never_started` (test: fresh liveness fields → `_prime_pty_alive` defers).
- [ ] `_prime_session()` stamps `last_pty_read_loop_at` and `last_pty_activity_at` during priming, verified by a test asserting the fields advance mid-prime.
- [ ] A genuinely frozen prime (no normalized-frame change past the 90s activity window) is still reaped — the kill path fires, and the container backstop stays within `STARTUP_HARD_CEILING_S (600s)`.
- [ ] Spinner-only animation (no normalized-content change) does **not** count as liveness — #1768 not regressed (test with a synthetic spinner-animating-but-content-static buffer).
- [ ] Non-PTY (SDK/headless) sessions retain age-only never_started kill semantics (hard regression assertion).
- [ ] The `continue`-nudge acceptance criterion is transferred to #1879 (not silently dropped) and #1878's acceptance criteria are annotated to reflect the split.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_prime_session` passes `on_read_iteration` into its `read_until_idle` calls.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (prime-liveness)**
  - Name: `prime-builder`
  - Role: Wire `on_read_iteration=self._pty_read_iteration_cb` into `_prime_session`'s `read_until_idle` calls; add prime liveness-stamping + frozen-frame + non-PTY tests.
  - Agent Type: builder
  - Domain: async/concurrency (PTY read loop, throttled callback)
  - Resume: true

- **Validator (session-health)**
  - Name: `sh-validator`
  - Role: Verify all five Part-A acceptance criteria; run targeted tests; confirm non-PTY regression and #1768 non-regression.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `sh-documentarian`
  - Role: Update granite doc for prime liveness stamping + the structural-finding / #1879 split note + inline docstring.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Standard Tier-1 pool (builder, validator, documentarian). For the concurrency/liveness
work, paste the async/concurrency rules from `DOMAIN_FRAMING.md` into the builder tasks.

## Step by Step Tasks

### 1. Wire read callback into priming (part A)
- **Task ID**: build-prime-liveness
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_persona_priming.py, tests/unit/granite_container/test_read_until_idle_per_iteration.py, tests/unit/granite_container/test_pty_read_iteration_throttle.py
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- Pass `on_read_iteration=self._pty_read_iteration_cb` into all three `read_until_idle` calls in `_prime_session` (`container.py` L1681, L1699, L1725).
- Add a test asserting `_prime_session` stamps `last_pty_read_loop_at`/`last_pty_activity_at` mid-prime via a fake `on_pty_read` writer + repainting fake PTY.
- Add a regression test asserting the prime path passes `on_read_iteration` (so a refactor can't silently drop it) and that a raising callback does not abort `_prime_session`.
- Update `_prime_session` docstring (completes #1792).

### 2. Frozen-frame + non-PTY assertions (part A)
- **Task ID**: build-prime-kill-tests
- **Depends On**: build-prime-liveness
- **Validates**: tests/unit/test_never_started_recovery.py, tests/unit/test_session_stall_classifier.py
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests: fresh liveness fields during >150s prime → `_prime_pty_alive` defers (no kill); stale (frozen) fields → kill fires; spinner-animating-but-content-static buffer → no false liveness (#1768 non-regression).
- Add hard regression test: non-PTY (`last_pty_read_loop_at is None`) session retains age-only never-started kill.
- Confirm no constant values change; assert the reuse of `NEVER_STARTED_PTY_LIVENESS_SECS` + `HEARTBEAT_FRESHNESS_WINDOW`.

### 3. Validate part A
- **Task ID**: validate-prime
- **Depends On**: build-prime-liveness, build-prime-kill-tests
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run prime/never-started/stall-classifier tests; verify all five Part-A acceptance criteria.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-prime
- **Assigned To**: sh-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` for prime liveness stamping + the structural-finding / #1879 split note.
- Verify the inline `_prime_session` docstring landed.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full targeted suite + lint/format; verify all Part-A acceptance criteria and the Verification table.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Prime wires callback | `grep -c "on_read_iteration=self._pty_read_iteration_cb" agent/granite_container/container.py` | output > 1 |
| Prime tests pass | `pytest tests/unit/granite_container/test_persona_priming.py tests/unit/granite_container/test_read_until_idle_per_iteration.py -q` | exit code 0 |
| Never-started tests pass | `pytest tests/unit/test_never_started_recovery.py -q` | exit code 0 |
| Stall-classifier tests pass | `pytest tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| No new prime stall constant | `grep -rc "PRIME_FROZEN_STALL_SECS\|PRIME_LIVENESS_STALL_SECS" agent/` | match count == 0 |
| No nudge field added here | `grep -rc "last_continue_nudge_at" models/agent_session.py` | output == 0 (deferred to #1879) |
| Lint clean | `python -m ruff check agent/ tests/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tests/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), 2026-07-03. First pass: NEEDS REVISION (2 blockers). Second re-critique: NEEDS REVISION (1 new structural blocker). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER (2nd re-critique) | Risk & Robustness | Part B's `continue`-nudge is dead-on-arrival: the steering queue is drained only by the `_poll_steering` closure at the top of the live steady-state turn loop (`container.py:2081`), but the only `no_progress` producer the prior revision scoped to (#944 running-scan, `session_health.py:3229`) fires precisely when `in_scope_handle is None` — an orphaned running row with NO live Container. So a pushed `continue` has no consumer, repeating the #1798 unwired-consumer anti-pattern. | **RESOLVED via split → #1879 (revision 2026-07-03)** | Grounded the claim in code (`_poll_steering` at `container.py:2081`; `_await_turn_end` liveness pump at `container.py:1328` polls only the hook edge file, not steering; D0 fires mid-prime; #944 fires with `in_scope_handle is None`; progress_deadline fires only after zero PTY activity → parked in `_await_turn_end`). Determined **no** `no_progress` recovery state has a reachable steering consumer — the drain point (top of a completed turn) and the wedge condition (turn never completing) are mutually exclusive. Option (a) re-scope is infeasible; chose (b) **split**. Part A ships alone; Part B moved to #1879 with the mid-run-drain infrastructure it requires; #1878's nudge acceptance criterion transferred (not dropped). See [## Structural Finding]. |
| BLOCKER (1st pass) | History & Consistency | Nudge idempotency spec was self-contradictory (re-nudge-forever loop). | **MOOT — Part B split to #1879** | The nudge rung is no longer built in this plan; the idempotency ladder is #1879's concern. |
| BLOCKER (1st pass) | Risk & Robustness + History & Consistency | Nudge fired against the mid-prime D0 producer that cannot drain it. | **MOOT — Part B split to #1879** | Superseded by the 2nd-re-critique finding that NO producer has a live consumer. Resolved by the split. |
| CONCERN (1st pass) | Scope & Value | Part B targets a post-prime steady-state wedge the issue never evidenced; the root-caused failure is fully fixed by Part A alone. | **ADOPTED — Part B split out** | The re-critique confirmed Part B is not just unevidenced but structurally infeasible on current main. Part A (which fixes the actual failure) ships here; Part B is filed as #1879 to be built once the mid-run steering drain exists. |
| CONCERN (1st pass) | Scope & Value | Part B bolted a parallel reprieve mechanism onto `_should_kill_no_progress`. | **MOOT — Part B split to #1879** | No parallel mechanism is added in this plan. |
| NIT (1st pass) | Risk & Robustness | Producer-attributed nudge counters. | **MOOT — Part B split to #1879** | Counter taxonomy is #1879's concern. |

---

## Open Questions

_All resolved. Part A questions answered; Part B (nudge) questions moved to #1879 with the split._

1. **Where the read callback gets wired for priming — RESOLVED (Part A).** Pass `on_read_iteration=self._pty_read_iteration_cb` into all three `_prime_session` `read_until_idle` calls (least code, cannot double-stamp — shared throttle; byte-identical no-op when no writer is wired). Do NOT restructure priming into the steady-state loop.
2. **Stall-window value for the frozen-frame kill — RESOLVED (Part A).** No new constant. Reuse `HEARTBEAT_FRESHNESS_WINDOW (90s)` + `NEVER_STARTED_PTY_LIVENESS_SECS (90s)`, both `< STARTUP_HARD_CEILING_S (600s)`.
3. **Nudge budget & idempotency — MOVED TO #1879.** The `continue`-nudge rung is structurally infeasible on current main (no live steering consumer for any `no_progress` state — see Structural Finding). Its budget/idempotency/reprieve design is deferred to #1879, which owns the required mid-run steering-drain infrastructure.
4. **Non-PTY (SDK/headless) sessions — RESOLVED (Part A).** `_prime_pty_alive` Branch 2 (`last_pty_read_loop_at is None → kill-eligible`) preserves the age-only never-started kill; locked by a hard regression test.
