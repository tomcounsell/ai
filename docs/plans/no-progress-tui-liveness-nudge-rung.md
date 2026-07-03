---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1878
last_comment_id:
revision_applied: true
---

# Redefine no_progress recovery around TUI liveness + add a "continue"-nudge rung before kill

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

**Desired outcome:** A granite session whose normalized TUI frame is still *changing* is
never killed on elapsed time alone during priming. A genuinely wedged session is first
**nudged** (cheap `continue` steering message) and only killed+respawned (expensive) if it
stays frozen. A truly-dead prime (no normalized-frame change) is still reaped within the
container's `STARTUP_HARD_CEILING_S (600s)`. Non-PTY (SDK/headless) sessions keep their
age-only never-started kill.

## Freshness Check

**Baseline commit:** `6de1f531` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-03T05:48:09Z (createdAt)
**Disposition:** Unchanged (issue filed today; only doc/plan commits landed on main since,
none touching the referenced code paths — `ebd017c2` touches migrations for a steering
field, `6de1f531` is a plan doc).

**File:line references re-verified against `6de1f531`:**
- `agent/session_health.py:545` `_prime_pty_alive` — **still holds.** Branch 2 (`last_pty_read_loop_at is None → False`, non-PTY escape) and Branch 4 (`last_pty_activity_at` within `NEVER_STARTED_PTY_LIVENESS_SECS`) confirmed present.
- `agent/session_health.py:1088` `_never_started_past_grace` — **still holds.** `sdk_ever_output` derived from `last_tool_use_at`/`last_turn_at`; threshold `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`.
- `agent/session_health.py:4121` — **corrected pointer.** The D0 kill gate that calls `_prime_pty_alive(fresh_ns, now)` and `continue`s on defer, else calls `_apply_recovery_transition(...)`. (The issue cited this behavior without a line; it lives at ~L4090–4141.)
- `agent/session_health.py:1533` `_should_kill_no_progress` — **still holds.** Shared Tier-2 reprieve gate (`True`=kill, `False`=reprieve). Called from `_apply_recovery_transition`'s `no_progress` branch at L2295.
- `agent/session_health.py:2290` `_apply_recovery_transition` `no_progress` branch — **still holds.** `recovery_attempts` bump is at L2376 (after the reprieve gate), confirming a rung inserted before the bump does NOT count against `MAX_RECOVERY_ATTEMPTS`.
- `agent/granite_container/container.py:1642` `_prime_session` — **CONFIRMED DEFECT.** The three `read_until_idle` calls at L1681, L1699, L1725 do **not** pass `on_read_iteration`. The steady-state call at L1214–1217 **does** pass `on_read_iteration=self._pty_read_iteration_cb`. So priming is blind to liveness stamping.
- `agent/granite_container/container.py:826` `self._pty_read_iteration_cb` — **confirmed.** A throttled (`PTY_READ_ITER_MIN_INTERVAL_S = 1.0`) wrapper around `_fire_pty_read_raw → _fire_pty_read → on_pty_read`; `None` when no `on_pty_read` writer is wired (i.e. byte-identical to no-op in tests without a bridge adapter).
- `agent/granite_container/bridge_adapter.py:937` `_make_pty_read_callback` — **confirmed.** Stamps `last_pty_read_loop_at` unconditionally and `last_pty_activity_at` only on **normalized** (`_normalize_pty_buffer`) frame change (#1768). This is the exact writer we need priming to drive.
- `agent/session_stall_classifier.py:101` `NEVER_STARTED_PTY_LIVENESS_SECS = 90` and `agent/session_health.py:303` `HEARTBEAT_FRESHNESS_WINDOW = 90`, `agent/granite_container/container.py:162` `STARTUP_HARD_CEILING_S = 600.0`, `:280` `PRIME_POST_WRITE_TIMEOUT_S = 360.0` — all confirmed.
- `agent/steering.py:37` `push_steering_message(session_id, text, sender, is_abort=False, target_agent=None, front=False)` — confirmed signature; auto-detects abort keywords (`continue` is not one).

**Cited sibling issues/PRs re-checked:**
- #1792 — CLOSED 2026-06-25 (PR #1798 `feat(session-health): gate never_started kill on PTY liveness`). This plan **completes** that incomplete fix.
- #1768 — CLOSED (frame normalization). Its `_normalize_pty_buffer` is the anti-spinner guard we must NOT regress.
- #1724 — CLOSED (fresh-heartbeat blinding). Related recovery-actor work; the D0 gate at L4090 is #1724-origin.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=<createdAt> -- agent/session_health.py agent/granite_container/container.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** none. `granite_hook_driven_turn_returns.md` touches turn-return plumbing but not the prime/liveness path; no conflict.

**Notes:** The claim that `_prime_session` emits no liveness stamps is verified true on
current main. The `_pty_read_iteration_cb` writer already exists (post-#1843 Gap B) — the
fix is to *wire it into priming*, not to build a new writer.

## Prior Art

- **Issue #1792 / PR #1798**: `gate never_started kill on PTY liveness`. Added `_prime_pty_alive()` deferral keyed on `last_pty_read_loop_at` + `last_pty_activity_at`. **Failed to actually protect priming** because those fields are never written during `_prime_session()`. This plan makes the deferral engage as originally intended.
- **Issue #1768**: `_normalize_pty_buffer()` strips the spinner glyph + elapsed-seconds counter before diffing, so an animating-but-wedged TUI yields a stable string. Content change = genuine liveness; spinner motion = not. Must be preserved.
- **Issue #1724 / #1226 / #1356**: the never-started D0 gate and the no-output budget gates that surround it. The nudge rung must live inside this established recovery structure without weakening those gates.
- **#1843 Gap B**: introduced `_pty_read_iteration_cb` (the throttled per-iteration read callback) and wired it into the steady-state `read_until_idle` call. Priming was left unwired — the gap this plan closes.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1798 (#1792) | Added `_prime_pty_alive()` deferral to the D0 never-started kill gate, keyed on `last_pty_read_loop_at` + `last_pty_activity_at`. | The deferral reads two liveness fields that `_prime_session()` never stamps. Priming is a blocking `read_until_idle()` sequence that (unlike steady-state) never passes `on_read_iteration`, so the fields stay `None` for the entire prime window and the deferral's Branch 4 (alive case) can never be reached — Branch 2 (`last_pty_read_loop_at is None`) short-circuits to kill-eligible. The fix protected a window that produced no evidence of its own liveness. |

**Root cause pattern:** The deferral and the field-stamping were built in different work
streams (#1792 vs #1843). The consumer (`_prime_pty_alive`) assumed a producer
(`_pty_read_iteration_cb` wired into priming) that was only ever wired into steady-state.
The fix is to connect the existing producer to the prime path — no new machinery.

## Data Flow

1. **Entry point**: `BridgeAdapter` (production) constructs `Container` with `on_pty_read` set → `Container.__init__` builds `self._pty_read_iteration_cb = _throttle(self._fire_pty_read_raw, 1.0)` (`container.py:826`).
2. **Priming (defect surface)**: `Container.run()` → `_spawn_pair()` → `_prime_session(pty, slash_cmd)` (`container.py:1642`). Its three `read_until_idle` calls (L1681 trust-dismiss loop, L1699 pre-write, L1725 post-write, up to `PRIME_POST_WRITE_TIMEOUT_S=360s`) run **without** `on_read_iteration`. → no callback fires → `_fire_pty_read_raw` never called → `on_pty_read` never called → `last_pty_read_loop_at`/`last_pty_activity_at` stay `None`.
3. **Stamping (what should happen)**: with `on_read_iteration=self._pty_read_iteration_cb` wired, each inner poll tick (throttled to ≤1/s) calls `bridge_adapter._make_pty_read_callback` → stamps `last_pty_read_loop_at` (unconditional) + `last_pty_activity_at` (only when `_normalize_pty_buffer(buffer)` changed — #1768 gate).
4. **Health check (consumer)**: `_agent_session_health_check` loop → D0 gate at `session_health.py:4090`: `_never_started_past_grace(entry)` true → re-read `fresh_ns` → `_prime_pty_alive(fresh_ns, now)`. With fields now fresh: Branch 3 (read-loop within `HEARTBEAT_FRESHNESS_WINDOW=90s`) and Branch 4 (activity within `NEVER_STARTED_PTY_LIVENESS_SECS=90s`) pass → returns `True` → `continue` (defer kill).
5. **Frozen frame (genuine death)**: TUI stops repainting → `_normalize_pty_buffer` stable → `last_pty_activity_at` stops advancing → after 90s Branch 4 returns `False` → `_prime_pty_alive` returns `False` → recovery proceeds.
6. **New nudge rung (part B)**: in `_apply_recovery_transition`'s `no_progress` branch, before the cancel/requeue, **only for the post-prime #944 running-scan producer** (`reason != "no progress signal observed (never_started past grace)"`) and only for a PTY session with a frozen frame: on first observation (`last_continue_nudge_at is None`) `push_steering_message(session_id, "continue", "session-health")`, stamp `last_continue_nudge_at`, and defer the kill this tick (return `False`). On the next recovery decision, if still within `CONTINUE_NUDGE_REPRIEVE_SECS` → keep waiting (no re-nudge); once the reprieve has elapsed and the frame is still frozen → proceed to cancel/requeue. The **never-started (D0) producer is excluded** — it fires mid-prime where the nudge cannot drain, so it goes straight to the part-A deferral / kill path with no nudge.
7. **Backstop**: if the read loop never stamps at all (totally dead prime), `_prime_pty_alive` Branch 2/3 stays kill-eligible AND the container's own `STARTUP_HARD_CEILING_S=600s` exits the run — no infinite hang.

## Architectural Impact

- **New dependencies**: none. Reuses `_pty_read_iteration_cb`, `_normalize_pty_buffer`, `_prime_pty_alive`, `push_steering_message` — all existing.
- **Interface changes**: `_prime_session()` gains no new public signature; internally its `read_until_idle` calls pass the existing per-iteration callback. `_apply_recovery_transition`'s `no_progress` branch gains a pre-kill nudge rung (internal). One new nullable `AgentSession` field: `last_continue_nudge_at`.
- **Coupling**: slightly tighter between prime and the liveness-stamping writer — but that coupling is *intended* (it's the whole point of #1792). No new cross-module coupling.
- **Data ownership**: `last_continue_nudge_at` owned by `session_health` (writer) and read by the same recovery decision. No cross-process contention beyond existing Popoto save patterns.
- **Reversibility**: high. Part A is env-reversible via existing kill-switches (`NEVER_STARTED_PTY_LIVENESS_SECS <= 0` disables the deferral). Part B gated behind a new env constant that can be set to disable nudging.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm nudge-during-prime scope decision; confirm reprieve-window value)
- Review rounds: 1 (async/liveness correctness is subtle — one focused review)

## Prerequisites

No prerequisites — this work has no external dependencies (purely internal session-health
and container behavior, no new secrets or services).

## Solution

### Key Elements

- **Prime liveness stamping (part A)**: `_prime_session()` fires the same throttled per-iteration read callback the steady-state loop uses, so priming stamps `last_pty_read_loop_at` (unconditional) and `last_pty_activity_at` (on normalized-frame change). This alone makes the existing `_prime_pty_alive()` deferral engage and protect slow-but-alive cold starts.
- **Frozen-frame kill (part A, no new constant)**: the kill fires only when the normalized frame is frozen past the existing staleness windows — `_prime_pty_alive` already encodes this via `HEARTBEAT_FRESHNESS_WINDOW (90s)` on the read loop and `NEVER_STARTED_PTY_LIVENESS_SECS (90s)` on activity. Spinner-only animation does NOT count (normalization already strips it — #1768 preserved).
- **Continue-nudge rung (part B)**: a cheap pre-kill rung in `_apply_recovery_transition`'s `no_progress` branch. On a suspected wedge for a PTY session, push a `continue` steering message, wait a reprieve window, and only escalate to cancel+requeue if the frame stays frozen. Sits *before* the `recovery_attempts` bump, so it does not consume a recovery attempt.
- **Non-PTY preservation (part A/B)**: `_prime_pty_alive` Branch 2 (`last_pty_read_loop_at is None → kill-eligible`) already preserves age-only never-started kill for SDK/headless sessions; the nudge rung is gated on the same PTY-presence check so non-PTY sessions are untouched.

### Flow

Slow granite cold start → prime `read_until_idle` ticks fire throttled callback → liveness
fields advance → health check D0 gate → `_prime_pty_alive` returns True → **defer kill**
(session keeps priming) → prime completes → steady-state.

Genuinely wedged *post-prime* session (#944 running-scan producer) → frame stops changing →
`last_pty_activity_at` goes stale (90s) → recovery decision → `last_continue_nudge_at is None`
→ **push `continue` nudge**, stamp `last_continue_nudge_at`, defer this tick → next decision
within reprieve → **wait** (no re-nudge) → next decision past reprieve, still frozen →
**cancel + requeue** (recovery attempt consumed).

Never-started prime wedge (D0 producer, zero SDK turns) → **no nudge** (gated out) → part-A
`_prime_pty_alive` deferral protects it while the frame changes; once the frame freezes past
the 90s activity window it goes straight to the kill path, backstopped by
`STARTUP_HARD_CEILING_S (600s)`.

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

**Part B — the continue-nudge rung (resolves OQ3).**
Insert in `_apply_recovery_transition`, inside the `reason_kind == "no_progress"` branch,
**after** the `_should_kill_no_progress` Tier-2 gate returns "kill" (L2295) and **before**
the task cancel (L2320) / `recovery_attempts` bump (L2376):

- **Producer gate (resolves BLOCKER 2 — do NOT nudge the never-started producer).** There are two `no_progress` producers that reach this branch, and only one can actually drain a nudge:
  - **D0 / never-started** (`session_health.py:4136`) passes the fixed literal `reason="no progress signal observed (never_started past grace)"`. This producer fires **precisely when the session is still mid-prime / frozen with zero SDK turns** — it structurally cannot consume a `continue` steering message (no turn-boundary drain during `_prime_session`, see Rabbit Hole + OQ1). Nudging it would burn the reprieve window uselessly and repeat the #1798 "assumed-but-unenforced gate" pattern.
  - **#944 running-scan orphan** (`session_health.py:3229`) passes a `reason` beginning `"no progress signal, orphaned running row (no in-scope handle, #944), …"`. This is a genuine *post-prime, steady-state* running row (it has a turn count and has been running past the min-running guard) — it **can** drain a nudge at its next turn boundary. This is the only producer the nudge is scoped to.
  - **Predicate:** gate the nudge on `reason != "no progress signal observed (never_started past grace)"`. Because the never-started reason is a fixed literal and the running-scan reason never equals it, this cleanly admits only the post-prime producer. (Implementation may equivalently thread a `never_started: bool` kwarg from both call sites; the string check is the least-code option and is regression-locked by a test.)
- **PTY gate**: additionally, only for PTY sessions (`getattr(entry, "last_pty_read_loop_at", None) is not None`) whose normalized frame is frozen. Non-PTY (SDK/headless) sessions skip the rung entirely (resolves OQ4 for the nudge path) — this is a *separate* skip from the never-started skip and gets its own regression test.
- **Budget + idempotency ladder (resolves BLOCKER 1 — strict if/elif/else, no never-escalating loop).** **One nudge per wedge**, not per recovery attempt. The nudge sits *before* the `recovery_attempts` bump, so it does **not** count against `MAX_RECOVERY_ATTEMPTS`. Idempotency is a new nullable field `last_continue_nudge_at`, evaluated as a **strict ordered ladder keyed on `is None` first** (never the same predicate for both push and kill):

  ```
  if last_continue_nudge_at is None:
      # first observation of this wedge → nudge once
      push_steering_message(entry.session_id, "continue", "session-health")
      entry.last_continue_nudge_at = now; entry.save(...)
      return False                      # defer kill this tick
  elif (now - last_continue_nudge_at) < CONTINUE_NUDGE_REPRIEVE_SECS:
      # already nudged, reprieve still in flight → keep waiting, no re-nudge
      return False                      # defer kill this tick
  else:
      # already nudged AND reprieve elapsed AND frame still frozen →
      # the nudge did not un-stick it → fall through to the existing kill path
      pass                              # proceed to cancel + requeue
  ```

  The `is None` arm is reachable **exactly once** per wedge (it stamps the field on that same tick), so there is no re-nudge loop. Escalation is guaranteed: once the field is set, every subsequent tick is either "wait" (within reprieve) or "kill" (past reprieve) — never another nudge. A test asserts **exactly one** `push_steering_message` call across a nudge → wait → escalate sequence.
- **Reprieve window**: a new named, env-overridable constant `CONTINUE_NUDGE_REPRIEVE_SECS` (default provisional ~45s — long enough for one turn boundary to drain the steering message and repaint, short enough to stay well under `STARTUP_HARD_CEILING_S`). Marked provisional/tunable per the magic-number convention.
- **Steering call**: `push_steering_message(entry.session_id, "continue", "session-health")`. `continue` is not an abort keyword, so `is_abort` stays False. The message drains at the normal turn boundary via `agent/steering.py`, so there is no double-driving with the turn loop — the loop is the sole consumer.
- **Escalation counter (producer-attributed per the critique NIT)**: emit project-scoped telemetry counters suffixed by producer so escalations are attributable. Because the never-started producer is gated out, in practice the only suffix that fires is `:running_scan` — but suffixing future-proofs against a new `no_progress` producer. Counters: `{project_key}:session-health:continue_nudge_total:{producer}` on push and `:continue_nudge_escalated:{producer}` on the follow-up kill, where `{producer}` is `running_scan` (or `never_started` if the gate is ever relaxed). This mirrors the existing `tier2_reprieve_total:{reprieve}` namespace rather than inventing a flat taxonomy.

**Part B/A — non-PTY preservation (resolves OQ4).**
`_prime_pty_alive` Branch 2 already returns `False` (kill-eligible) when
`last_pty_read_loop_at is None`, preserving the age-only never-started kill for SDK/headless
sessions — no change needed there, only a regression test to lock it. The nudge rung adds
the same `last_pty_read_loop_at is not None` guard so non-PTY sessions never receive a
`continue` nudge and retain exact current semantics.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_prime_pty_alive`, `_should_kill_no_progress`, and `_never_started_past_grace` all wrap their bodies in `try/except → return False`. The new nudge rung must follow the same fail-silent contract: any exception in `push_steering_message` or the `last_continue_nudge_at` save is caught, logged at `debug`/`warning`, and treated as "no nudge happened this tick" (fall through to the existing kill path) — assert via a test that injects a raising `push_steering_message` and confirms the session still gets recovered (no crash, kill proceeds).
- [ ] The prime callback wiring must not let a raising callback break the read loop — `test_read_until_idle_per_iteration.py::test_raising_callback_does_not_break_read_loop` already covers this at the `read_until_idle` layer; add a prime-level assertion that a raising `_pty_read_iteration_cb` does not abort `_prime_session`.

### Empty/Invalid Input Handling
- [ ] `last_continue_nudge_at` unset (None) on first wedge → nudge fires. Whitespace/None `session_id` → `push_steering_message` guard; assert no crash.
- [ ] Empty normalized frame (buffer strips to "") during prime → `last_pty_activity_at` correctly does NOT advance (no false liveness) — covered by the #1768 normalization; add an assertion.

### Error State Rendering
- [ ] Not user-visible (internal recovery). The observable "error state" is the telemetry counter + log line on escalation. Assert `continue_nudge_escalated` increments when the frame stays frozen after the reprieve, and `continue_nudge_total` increments on every nudge.

## Test Impact

- [ ] `tests/unit/test_never_started_recovery.py` — UPDATE: existing tests assume age-only never-started kill fires at 150s for granite sessions. Add cases where liveness fields are fresh (deferral engages, no kill) and where they are stale (kill still fires). Keep the non-PTY age-only case as a hard regression assertion (OQ4).
- [ ] `tests/unit/granite_container/test_persona_priming.py` — UPDATE: add a test asserting `_prime_session()` stamps `last_pty_read_loop_at`/`last_pty_activity_at` mid-prime (via a fake `on_pty_read` writer + a repainting fake PTY). This is the AC "verified by a test asserting the fields advance mid-prime."
- [ ] `tests/unit/granite_container/test_read_until_idle_per_iteration.py` — UPDATE (or add sibling): assert the prime path passes `on_read_iteration` (regression lock so a future refactor can't silently drop it).
- [ ] `tests/unit/granite_container/test_pty_read_iteration_throttle.py` — VERIFY (likely no change): confirm the shared throttle prevents a prime+steady-state double-stamp storm; add an assertion if not already covered.
- [ ] `tests/unit/test_session_stall_classifier.py` — VERIFY: no constant values change; confirm `NEVER_STARTED_PTY_LIVENESS_SECS`/`HEARTBEAT_FRESHNESS_WINDOW` reuse is asserted, add a test for the new `CONTINUE_NUDGE_REPRIEVE_SECS` default + env override.
- [ ] New: `tests/unit/test_continue_nudge_rung.py` — CREATE: nudge-before-kill for the #944 running-scan PTY frozen-frame wedge; **exactly one** `push_steering_message` across a nudge → wait → escalate sequence (BLOCKER 1 lock — no never-escalating re-nudge loop); nudge does NOT count against `MAX_RECOVERY_ATTEMPTS`; escalation to kill after reprieve if still frozen; **D0 never-started producer (`reason="no progress signal observed (never_started past grace)"`) proceeds straight to kill with NO nudge** (BLOCKER 2 lock — distinct assertion from the non-PTY skip); non-PTY session skips the nudge; fail-silent when `push_steering_message` raises.

## Rabbit Holes

- **Restructuring priming into the steady-state read loop.** Tempting ("unify the read paths") but the trust-dialog dismiss / pre-write / post-write sequence in `_prime_session` is load-bearing (#1612, #1644). Just pass the callback into the existing reads.
- **Inventing a new prime-specific stall constant.** The timeout hierarchy is already crowded (150s kill / 360s prime / 600s ceiling). Reuse `NEVER_STARTED_PTY_LIVENESS_SECS` + `HEARTBEAT_FRESHNESS_WINDOW`; do not add a fourth overlapping clock.
- **Making the nudge drain mid-prime.** A blocking `read_until_idle` during priming has no turn-boundary steering drain, so a `continue` nudge pushed while the PTY is *still in `_prime_session`* cannot be consumed until steady-state begins. Do NOT try to build a mid-prime steering drain — the nudge rung's job is to catch *post-prime* steady-state wedges; the prime window is protected by part A's deferral (defer-while-alive, reap-when-dead). This boundary is called out as Open Question 1 for confirmation.
- **Counting spinner motion as liveness.** `_normalize_pty_buffer` already strips the spinner + elapsed counter. Reuse it; never diff the raw buffer.
- **Touching the shared `_should_kill_no_progress` reprieve gate.** It is called from multiple producers (recovery transition + progress-deadline cancel scope). Put the nudge rung in `_apply_recovery_transition` only, so the progress-deadline poller is unaffected.

## Risks

### Risk 1: Prime callback stamps a stale/misleading `last_pty_activity_at`, masking a genuinely wedged prime
**Impact:** A prime that repaints its spinner but produces no real content could look alive forever and never get reaped.
**Mitigation:** `_make_pty_read_callback` stamps `last_pty_activity_at` only on **normalized**-frame change (`_normalize_pty_buffer` strips spinner + counter). A spinner-only prime yields a stable normalized string → activity goes stale → deferral stops at 90s. Locked by a test with a synthetic spinner-animating-but-content-static buffer (AC #5, #1768 regression).

### Risk 2: The `continue` nudge double-drives a session also being driven by the normal turn loop
**Impact:** Two `continue` messages, or a nudge racing a real turn, could corrupt the TUI or duplicate work.
**Mitigation:** The steering queue (`agent/steering.py`) is the single drain point at the turn boundary; the turn loop is the sole consumer. The nudge only pushes one message and only when `last_continue_nudge_at` is unset/expired. See Race 1.

### Risk 3: Nudge rung weakens the age-only kill for non-PTY sessions
**Impact:** SDK/headless sessions could hang past their 150s grace if the nudge path swallowed them.
**Mitigation:** Both the deferral (`_prime_pty_alive` Branch 2) and the nudge rung gate on `last_pty_read_loop_at is not None`. Non-PTY sessions are never deferred and never nudged. Hard regression test (AC #6).

### Risk 4: New nullable `AgentSession` field requires a data migration
**Impact:** Existing running sessions lack the field; a naive read could error.
**Mitigation:** Additive nullable `DatetimeField(null=True)` is auto-healed by `_heal_descriptor_pollution` (issues #1099, #1172) — no data migration needed. `getattr(entry, "last_continue_nudge_at", None)` reads default to None. Documented in ## Update System.

## Race Conditions

### Race 1: Concurrent nudge push vs. turn-loop steering drain
**Location:** `agent/session_health.py` `_apply_recovery_transition` no_progress branch (~L2290) vs. `agent/granite_container/container.py` steady-state steering poll.
**Trigger:** The health-check tick pushes a `continue` nudge at the same moment the turn loop drains the steering queue at a turn boundary.
**Data prerequisite:** `last_continue_nudge_at` must be persisted before the next recovery decision reads it, so a second tick doesn't double-push.
**State prerequisite:** The steering queue must remain the single consumer; the nudge is a normal RPUSH, drained in order.
**Mitigation:** `last_continue_nudge_at` is stamped and saved in the same recovery decision that pushes the nudge, and the push happens **only in the `is None` arm** of the strict idempotency ladder (§Technical Approach Part B). Once stamped, every subsequent tick takes the `elif` (wait) or `else` (kill) arm — never a second push — so the field being set is itself the guard, independent of clock skew. The steering queue's RPUSH/LPOP ordering guarantees the turn loop drains at most one `continue` per push. No lock needed — the `is None` gate is single-shot and the queue is already serialized.

### Race 2: Prime callback stamp vs. health-check re-read
**Location:** `container.py` `_prime_session` throttled callback vs. `session_health.py:4092` `fresh_ns = AgentSession.get_by_id(...)`.
**Trigger:** The health check re-reads `fresh_ns` between two prime callback stamps.
**Data prerequisite:** The re-read (`fresh_ns`) must see the most recent stamp to defer correctly.
**State prerequisite:** Stamps are ≤1/s (throttle); the health-check tick is coarser.
**Mitigation:** The D0 gate already re-reads `fresh_ns` fresh from Redis right before calling `_prime_pty_alive`, so it sees the latest persisted stamp. A stamp landing microseconds after the read only delays the deferral by one health-check tick — harmless (the session is alive either way). No mitigation beyond the existing re-read.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing is filed as a separate issue for this work — the whole fix (parts A and B) is in scope for this plan.
- Mid-prime steering drain (a way to consume a `continue` nudge while `_prime_session` is still blocking) is **not** built here — it is a design question surfaced in Open Questions, not a deferred implementation task. Part A's deferral fully covers the prime window; the nudge is for post-prime wedges. If the answer to Open Question 1 requires it, it becomes its own plan.

Nothing else deferred — every relevant item (callback wiring, frozen-frame reap via existing constants, nudge rung, non-PTY preservation, all six acceptance-criteria tests) is in scope for this plan.

## Update System

- **No `/update` skill or `scripts/update/run.py` changes required** — this is a purely internal session-health/container behavior change; no new deps, config files, or propagated artifacts.
- **No data migration required** for the new `last_continue_nudge_at` `DatetimeField(null=True)`: additive nullable `AgentSession` fields are auto-healed by `_heal_descriptor_pollution` (issues #1099, #1172); existing records read `None` via `getattr(..., None)`. Per `docs/sdlc/do-plan.md`, no `scripts/update/migrations.py` entry is needed because there is no data to backfill or transform — the field simply defaults to null.
- **New env constant** `CONTINUE_NUDGE_REPRIEVE_SECS` follows the existing `os.environ.get(..., default)` pattern in `agent/session_stall_classifier.py`; it is optional with a provisional default, so no `.env` / `.env.example` change is required (it is a tuning knob, not a secret).

## Agent Integration

No agent integration required — this is a worker/session-health-internal change. It does not
add or modify any CLI entry point in `pyproject.toml [project.scripts]`, any MCP server in
`mcp_servers/`, any `.mcp.json` registration, or any bridge (`bridge/telegram_bridge.py`)
call path. The `continue` steering message is pushed *internally* by session-health via the
existing `agent/steering.py::push_steering_message`, which is already wired into the worker's
turn loop — no new surface is exposed to the conversational agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document that priming now stamps PTY liveness fields and that the never-started kill is deferred while the normalized frame changes.
- [ ] Update the session-health recovery description (in `docs/features/granite-pty-production.md` or `docs/features/session-lifecycle.md`, wherever the recovery ladder is documented) to add the `continue`-nudge rung before kill+respawn, and note it does not consume a `MAX_RECOVERY_ATTEMPTS`.
- [ ] Cross-reference #1792 (deferral) and #1768 (normalization) as the prior art this completes.

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for these internals.

### Inline Documentation
- [ ] Docstring on `_prime_session` noting it now wires `on_read_iteration` for liveness stamping (and why — completes #1792).
- [ ] Docstring/comment on the new nudge rung in `_apply_recovery_transition` explaining the pre-`recovery_attempts`-bump placement and the `CONTINUE_NUDGE_REPRIEVE_SECS` idempotency guard.
- [ ] Comment on `CONTINUE_NUDGE_REPRIEVE_SECS` marking it provisional/tunable (magic-number convention).

## Success Criteria

- [ ] A granite session whose TUI frame keeps changing during a long (>150s) Opus cold-start prime is **not** killed by `no_progress`/`never_started` (test: fresh liveness fields → `_prime_pty_alive` defers).
- [ ] `_prime_session()` stamps `last_pty_read_loop_at` and `last_pty_activity_at` during priming, verified by a test asserting the fields advance mid-prime.
- [ ] A genuinely frozen prime (no normalized-frame change past the 90s activity window) is still reaped — the kill path fires, and the container backstop stays within `STARTUP_HARD_CEILING_S (600s)`.
- [ ] Recovery attempts a `continue` steering nudge before kill+respawn for a **post-prime #944 running-scan** wedge; **exactly one** nudge is pushed per wedge (strict `is None`-first ladder — no never-escalating re-nudge loop); escalation to respawn only occurs if the frame stays frozen after the `CONTINUE_NUDGE_REPRIEVE_SECS` reprieve; the nudge does not consume a `MAX_RECOVERY_ATTEMPTS`.
- [ ] The D0 **never-started** producer (`reason="no progress signal observed (never_started past grace)"`, fires mid-prime) is **never** nudged — it goes straight to the part-A deferral / kill path (regression test, distinct from the non-PTY skip).
- [ ] Spinner-only animation (no normalized-content change) does **not** count as liveness — #1768 not regressed (test with a synthetic spinner-animating-but-content-static buffer).
- [ ] Non-PTY (SDK/headless) sessions retain age-only never_started kill semantics and never receive a `continue` nudge (hard regression assertion).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `_prime_session` passes `on_read_iteration` into its `read_until_idle` calls.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (prime-liveness)**
  - Name: `prime-builder`
  - Role: Wire `on_read_iteration=self._pty_read_iteration_cb` into `_prime_session`'s `read_until_idle` calls; add prime liveness-stamping tests.
  - Agent Type: builder
  - Domain: async/concurrency (PTY read loop, throttled callback)
  - Resume: true

- **Builder (nudge-rung)**
  - Name: `nudge-builder`
  - Role: Add the `continue`-nudge rung + `last_continue_nudge_at` field + `CONTINUE_NUDGE_REPRIEVE_SECS` constant; telemetry counters; nudge tests.
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto (steering queue, session save)
  - Resume: true

- **Validator (session-health)**
  - Name: `sh-validator`
  - Role: Verify all six acceptance criteria; run targeted tests; confirm non-PTY regression and #1768 non-regression.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `sh-documentarian`
  - Role: Update granite/session-lifecycle docs + inline docstrings.
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
- **Parallel**: true
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

### 3. Continue-nudge rung (part B)
- **Task ID**: build-nudge-rung
- **Depends On**: none
- **Validates**: tests/unit/test_continue_nudge_rung.py (create), tests/unit/test_session_stall_classifier.py
- **Assigned To**: nudge-builder
- **Agent Type**: builder
- **Domain**: async/concurrency + Redis/Popoto
- **Parallel**: true
- Add `last_continue_nudge_at = DatetimeField(null=True)` to `AgentSession` (additive nullable; no migration).
- Add `CONTINUE_NUDGE_REPRIEVE_SECS` env-overridable constant (provisional default ~45s, grain-of-salt comment) in `agent/session_stall_classifier.py`.
- Insert the nudge rung in `_apply_recovery_transition`'s `no_progress` branch, after `_should_kill_no_progress` returns kill and before task cancel / `recovery_attempts` bump. Gates (all must pass to nudge): (a) **producer gate** `reason != "no progress signal observed (never_started past grace)"` (excludes the D0 never-started producer that fires mid-prime); (b) **PTY gate** `last_pty_read_loop_at is not None`; (c) frozen frame. Then apply the **strict if/elif/else ladder keyed on `is None` first**: `if last_continue_nudge_at is None` → push + stamp + defer; `elif now - last_continue_nudge_at < CONTINUE_NUDGE_REPRIEVE_SECS` → defer (no re-nudge); `else` → fall through to kill.
- Push via `push_steering_message(entry.session_id, "continue", "session-health")` in the `is None` arm only.
- Emit producer-suffixed `continue_nudge_total:{producer}` / `continue_nudge_escalated:{producer}` project-scoped counters (`{producer}` = `running_scan`).
- Fail-silent: wrap nudge in try/except → fall through to kill on error.
- Create `tests/unit/test_continue_nudge_rung.py` covering: nudge-before-kill for the #944 running-scan PTY frozen wedge; **exactly one** `push_steering_message` across nudge → wait → escalate (BLOCKER 1 lock); nudge does NOT consume `MAX_RECOVERY_ATTEMPTS`; escalation after reprieve; **D0/never-started producer goes straight to kill with no nudge** (BLOCKER 2 lock, distinct from the non-PTY-skip case); non-PTY skips nudge; fail-silent on raising `push_steering_message`.

### 4. Validate part A
- **Task ID**: validate-prime
- **Depends On**: build-prime-liveness, build-prime-kill-tests
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run prime/never-started/stall-classifier tests; verify AC #1, #2, #3, #5, #6.

### 5. Validate part B
- **Task ID**: validate-nudge
- **Depends On**: build-nudge-rung
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run nudge-rung tests; verify AC #4 and the non-PTY nudge-skip.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-prime, validate-nudge
- **Assigned To**: sh-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` (and session-lifecycle recovery-ladder doc) for prime liveness stamping + the nudge rung.
- Verify inline docstrings/comments landed.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sh-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full targeted suite + lint/format; verify all six acceptance criteria and the Verification table.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Prime wires callback | `grep -c "on_read_iteration=self._pty_read_iteration_cb" agent/granite_container/container.py` | output > 1 |
| Prime tests pass | `pytest tests/unit/granite_container/test_persona_priming.py tests/unit/granite_container/test_read_until_idle_per_iteration.py -q` | exit code 0 |
| Never-started tests pass | `pytest tests/unit/test_never_started_recovery.py -q` | exit code 0 |
| Nudge-rung tests pass | `pytest tests/unit/test_continue_nudge_rung.py -q` | exit code 0 |
| Stall-classifier tests pass | `pytest tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| New reprieve constant exists | `grep -c "CONTINUE_NUDGE_REPRIEVE_SECS" agent/session_stall_classifier.py` | output > 0 |
| Nudge field added | `grep -c "last_continue_nudge_at" models/agent_session.py` | output > 0 |
| No new prime stall constant | `grep -rc "PRIME_FROZEN_STALL_SECS\|PRIME_LIVENESS_STALL_SECS" agent/` | match count == 0 |
| Lint clean | `python -m ruff check agent/ models/ tests/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ models/ tests/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room), 2026-07-03. Verdict: NEEDS REVISION (2 blockers). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Nudge idempotency spec (Part B, L180) is self-contradictory: "unset OR older than reprieve window → push nudge" and "reprieve elapsed → kill" are the same predicate with no ordering, so a literal implementation re-nudges forever and never escalates (silent never-recovers loop). | **RESOLVED** (revision 2026-07-03) | Part B "Budget + idempotency ladder" now specifies a strict ordered if/elif/else keyed on `last_continue_nudge_at is None` first (push+stamp+defer), `elif` within-reprieve (defer, no re-nudge), `else` (fall through to kill). The `is None` arm is single-shot (stamps on the same tick). Success Criteria + Step 3 + `test_continue_nudge_rung.py` now require asserting **exactly one** `push_steering_message` across nudge → wait → escalate. |
| BLOCKER | Risk & Robustness + History & Consistency (both flagged) | Nudge rung is placed generically in `_apply_recovery_transition`'s shared `no_progress` branch, but the D0 never-started-past-grace producer reaches that branch precisely when the session is still mid-prime/frozen (reachable only when `sdk_ever_output`=False). So the nudge fires against a session that structurally cannot drain it — burning the ~45s reprieve uselessly and contradicting the plan's own Rabbit Hole ("Making the nudge drain mid-prime") + OQ1 (post-prime scope only). Repeats the #1798 "assumed-but-unenforced gate" pattern. | **RESOLVED** (revision 2026-07-03) | Part B now adds a **producer gate** `reason != "no progress signal observed (never_started past grace)"` (the literal D0 reason at `session_health.py:4136`), admitting only the #944 running-scan post-prime producer (`session_health.py:3229`). Data Flow item 6, the Flow section, Success Criteria, Step 3, and `test_continue_nudge_rung.py` now require a regression test asserting the D0/never-started producer proceeds straight to kill with no nudge — distinct from the non-PTY-skip assertion. |
| CONCERN | Scope & Value | Part B (nudge rung) targets a post-prime steady-state wedge that the issue never evidenced — the root-caused failure (session c7bd42…, killed ~154s during priming) is fully fixed by Part A alone. Plan's own OQ1/OQ3 signal the uncertainty. | **ACKNOWLEDGED — kept in scope** | Non-blocking. Part B is retained per issue #1878's explicit ask ("add a 'continue'-nudge rung before kill"). With the BLOCKER-2 producer gate, Part B is now correctly and narrowly scoped to the post-prime #944 running-scan wedge — the exact steady-state case the issue names — while Part A independently fixes the priming failure. OQ1/OQ3 are now resolved (see Open Questions → answered), removing the uncertainty this concern flagged. Splitting Part B into a separate issue was considered and rejected: the nudge rung is small, its gates are now precise, and #1878 scopes both parts together. |
| CONCERN | Scope & Value | Part B bolts a parallel reprieve mechanism (`last_continue_nudge_at`, `CONTINUE_NUDGE_REPRIEVE_SECS`, two ad-hoc counters) onto the outside of `_should_kill_no_progress`, whose docstring says the no_progress kill decision must live in exactly one place (#1820 OQ3). Duplicates the existing reprieve vocabulary (`_tier2_reprieve_signal`, `reprieve_count`, `tier2_reprieve_total:{reprieve}`). | **PARTIALLY ADOPTED** | Non-blocking. Counter taxonomy now mirrors the existing reprieve namespace (producer-suffixed `continue_nudge_total:{producer}` / `continue_nudge_escalated:{producer}`, matching `tier2_reprieve_total:{reprieve}`) rather than a flat pair. The rung deliberately stays in `_apply_recovery_transition` (not folded into `_should_kill_no_progress`): the nudge is an *action* taken **after** `_should_kill_no_progress` has already returned "kill", not a reprieve *signal* feeding that decision — folding a side-effecting steering push into the pure kill-decision predicate would violate the same single-responsibility contract this concern cites. The distinct `last_continue_nudge_at` timestamp is required (it survives across ticks; `reprieve_count` semantics differ). |
| NIT | Risk & Robustness | Proposed `continue_nudge_total` / `continue_nudge_escalated` counters don't distinguish producer (D0/never-started vs #944 running-scan), so escalations can't be attributed. | **RESOLVED** | Counters are now suffixed by producer (`:running_scan`; `:never_started` reserved if the producer gate is ever relaxed). See Part B "Escalation counter" + Step 3. |

---

## Open Questions

_All resolved in the 2026-07-03 revision pass — no open questions remain. Dispositions recorded below for traceability._

1. **Nudge-during-prime boundary (scope confirmation) — RESOLVED: post-prime only.** A `continue` steering nudge cannot be drained while `_prime_session()` is still blocking on `read_until_idle` (no turn-boundary drain during priming). The nudge rung is scoped to *post-prime* steady-state wedges (the #944 running-scan producer) and this is now **enforced in code**, not just intended: the BLOCKER-2 producer gate (`reason != "no progress signal observed (never_started past grace)"`) excludes the mid-prime D0 producer, so a nudge can only fire for a session past priming that can actually drain it. Part A's deferral (defer-while-alive, reap-when-dead) protects the prime window. A mid-prime steering drain is explicitly **not** built (see No-Gos and Rabbit Holes); if ever wanted it becomes its own plan.
2. **Reprieve-window value — RESOLVED: 45s provisional, env-overridable.** `CONTINUE_NUDGE_REPRIEVE_SECS` ships at a provisional default of ~45s (one turn boundary to drain + repaint, well under the 600s `STARTUP_HARD_CEILING_S`), marked provisional/tunable per the magic-number convention and overridable via `os.environ.get(...)`. It is a fresh named constant rather than tied to an existing one, because no existing constant carries "time for one steering message to drain and repaint" semantics — coupling it to `NEVER_STARTED_PTY_LIVENESS_SECS` (90s, a liveness-staleness window) would conflate two unrelated concerns. The env override is the escape hatch if 45s proves wrong in production.
3. **Nudge granularity — RESOLVED: one nudge per wedge.** Exactly one nudge per wedge, sitting before the `MAX_RECOVERY_ATTEMPTS` bump (so a wedged session gets: nudge → reprieve → attempt 1 → attempt 2 → failed). One nudge is sufficient: the nudge is a cheap probe of "can a turn-boundary drain un-stick this?" — if one `continue` at the turn boundary does not move the frame within the reprieve, a second identical nudge is unlikely to, and the existing two-attempt kill+respawn ladder is the stronger remedy. Keeping it to one nudge also makes the idempotency ladder single-shot (BLOCKER-1 fix) and avoids re-nudge-loop hazards. Per-attempt nudging (up to 2) was considered and rejected as added complexity for negligible recovery gain.
