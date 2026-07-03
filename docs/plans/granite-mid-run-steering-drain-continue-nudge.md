---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1879
last_comment_id: 4877607376
revision_applied: true
---

# Mid-run steering drain + continue-nudge recovery rung for wedged granite sessions

## Problem

When the session-health monitor suspects a granite PTY session is *wedged but
alive* (a live `exec_task`, its normalized TUI frame frozen), the desired
recovery is cheap: push a single `continue` nudge into the live session and give
it a short reprieve to un-stick itself, escalating to the expensive kill+respawn
only if the frame stays frozen.

**Current behavior:** This is structurally impossible on current `main`. The
external steering queue (`agent/steering.py`) is drained for a granite PTY
session by exactly one consumer — the `_poll_steering` closure
(`bridge_adapter.py:620`) wired into `Container.run()`'s steady-state loop
(`container.py:2098`) — and that drain runs **only between completed turns**.
A wedged session is parked *inside* `_await_turn_end` (`container.py:1269`),
whose per-tick liveness pump polls only the hook edge file (`consumer.poll()`,
`container.py:1304`) and does a byte-level liveness read; it **never** drains the
external steering queue. So a `continue` pushed during a wedge is never consumed.
The drain point (top of a *completed* turn) and the wedge condition (a turn that
never completes) are mutually exclusive by construction — proven in #1878's
Structural Finding and carried here as #1879.

**Desired outcome:** A live-but-parked Container drains a `continue` nudge
mid-turn and injects it into the parked PTY without waiting for a turn boundary,
and a health-loop producer pushes exactly one such nudge for a genuine wedge
before the existing teardown backstop reclaims the session — without consuming a
`MAX_RECOVERY_ATTEMPTS` and without touching non-PTY (SDK/headless) sessions.

## Freshness Check

**Baseline commit:** `776de1ee`
**Issue filed at:** 2026-07-03T06:41:52Z
**Disposition:** Minor drift (line numbers moved; one new material finding surfaced)

**File:line references re-verified (issue cited commit `8c8d64b0`; now on `776de1ee`):**
- `agent/granite_container/bridge_adapter.py:620` — `_poll_steering` closure — **still holds** (exact line match).
- `agent/granite_container/container.py` `_await_turn_end` — issue said `:1302`; **drifted to `:1269`**. Liveness pump (`consumer.poll()` + `read_until_idle` + `_fire_pty_read`) at `:1304`, `:1327`, `:1331`.
- Steady-state `_poll_steering` drain — issue said `:2081`/`:2107`; **drifted to `:2098`** (drain) / `:2131` (`_cycle_idle`-before-write ordering guarantee).
- `agent/session_health.py` D0 never-started — issue said `:4136`; **drifted to `:4090`**; the branch now defers via `_prime_pty_alive` (`:545`) — a Part A (PR #1880) addition.
- `agent/session_health.py` #944 running-scan orphan — issue said `:3229`; **drifted to `:3222`** (`in_scope_handle is None` guard).
- `agent/agent_session_queue.py` progress-deadline cancel scope — issue said `:1790`/`reason_kind="progress_deadline"` — **still holds** (`:1790`); `_session_progress_ts` at `:1469`, `SESSION_PROGRESS_DEADLINE_S=1800` at `:1462`.

**Cited sibling issues/PRs re-checked:**
- #1878 — **CLOSED** 2026-07-03T15:17:52Z. Part A shipped as PR #1880 (merged 15:17:51Z). Its plan (`docs/plans/no-progress-tui-liveness-nudge-rung.md`, `status: docs_complete`) explicitly split Part B here and transferred the nudge acceptance criterion. This is the parent, not a conflict.
- #1792 / PR #1798 (`_prime_pty_alive` deferral) — completed by Part A; the D0 branch now honors PTY liveness.
- #1768 (`_normalize_pty_buffer`), #1779 (top-of-turn `_poll_steering` drain) — both present and are the seams this plan extends.

**Commits on main since issue filed (touching referenced files):**
- `e05d6516` "Redefine no_progress recovery around TUI liveness during priming (Part A) (#1880)" — narrows the D0 producer with a PTY-liveness deferral. It did **not** add any mid-run steering drain, so this plan's infrastructure is still required. No overlap conflict.

**Active plans in `docs/plans/` overlapping this area:** `no-progress-tui-liveness-nudge-rung.md` (#1878 Part A) — parent, already shipped/`docs_complete`; it deferred this work to #1879 by design. No coordination blocker.

**Material new finding (not in the issue):** `_hook_turn_end_wait_s` defaults to **600s** (`config/settings.py:488`, `_resolve_hook_turn_end_wait_s` fallback `container.py:126`). A session parked in `_await_turn_end` with a frozen frame therefore self-terminates to `saw_turn=False` / `pm_hang` (`container.py:1382`) at **600s** — *before* the 1800s `SESSION_PROGRESS_DEADLINE_S` the issue named as the "live-session no-progress killer." The 1800s progress-deadline is preempted for this wedge shape. **Consequence:** the nudge producer must fire *within* the 600s window (the 30s session-health loop qualifies), and the "escalation to respawn" backstop is the existing 600s `pm_hang` teardown, not the 1800s progress-deadline. This reshapes the producer choice (see Technical Approach) and is the single most important premise correction versus the issue text.

## Prior Art

- **#1878 / PR #1880** (parent, MERGED): Part A stamps PTY liveness during priming and redefines `no_progress` recovery around TUI liveness. Established the Structural Finding that no `no_progress` recovery state has a reachable steering consumer, and split the nudge rung here. Directly informs this plan's producer/consumer split.
- **#1779** (top-of-turn `_poll_steering` drain, MERGED): the mid-run steering injection this plan extends past the turn boundary. Its `_cycle_idle`-before-write ordering (`container.py:2131`) is the corruption-avoidance pattern this plan must respect.
- **#1768** (`_normalize_pty_buffer`, MERGED): the spinner-vs-content normalization that diff-gates `last_pty_activity_at`. Central to distinguishing a frozen frame from an animating one, and to the injected-echo hazard below.
- **#1820 / PR #1867** (progress-deadline cancel scope, MERGED): the 1800s live-session killer the issue named. This plan documents why it is *not* the reachable producer for this wedge shape (preempted at 600s).
- **#1792 / PR #1798** (`_prime_pty_alive`, MERGED): the "assumed-but-unwired consumer" anti-pattern the parent plan warned this work must avoid repeating.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1878 Part B (pre-split) | Attempted a `continue`-nudge rung wired to the #944 running-scan `no_progress` producer. | The scoped producer fires only when `in_scope_handle is None` (a crashed-worker orphan with **no live Container**), so the pushed `continue` had no consumer — the #1798 unwired-consumer anti-pattern. Split to #1879 rather than shipped. |

**Root cause pattern:** every attempt wired the nudge to a producer whose recovery state has no live steering consumer. The fix is not a new producer alone — it is the *missing consumer*: a mid-run drain inside `_await_turn_end` that makes the steering queue reachable while the session is parked. This plan builds that consumer first, then a producer that can reach it.

## Architectural Impact

- **New dependencies:** none (no new libraries, services, or model fields — see Update System).
- **Interface changes:** `Container.__init__` gains an optional `poll_wedge_nudge` callback (mirrors the existing `poll_steering`). `agent/steering.py` gains a wedge-nudge channel push/pop pair plus a TTL latch helper (mirrors the existing `bump_self_draft_attempts`/`_self_draft_attempts_key`).
- **Coupling:** the wedge-nudge channel is a *separate* Redis key from ordinary steering, so the mid-run drain and the top-of-turn drain stay orthogonal — the mid-run path never consumes ordinary operator steering, and ordinary steering behavior is byte-for-byte unchanged.
- **Data ownership:** the nudge latch lives in `agent/steering.py` as a raw-Redis TTL key (like the existing self-draft-attempts counter), NOT on the Popoto `AgentSession` model — so no schema migration.
- **Reversibility:** high. The consumer is additive and fail-silent; the producer is a new guarded branch. Removing either restores current behavior.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (the producer-choice premise correction and the injected-echo hazard both warrant alignment)
- Review rounds: 2+ (concurrency-sensitive granite infrastructure; async/PTY domain)

## Prerequisites

No external prerequisites — this work uses existing Redis, the granite PTY
container, and the session-health loop, all already provisioned. Reproduction
and validation run against the existing test harness.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | Steering channel + latch storage |

## Solution

### Key Elements

- **Wedge-nudge steering channel** (`agent/steering.py`): a `continue`-nudge
  push/pop pair keyed on a *distinct* Redis key from ordinary steering, plus a
  TTL latch helper that records "already nudged this turn-wait window" so at most
  one nudge fires before teardown.
- **Mid-run drain consumer** (`container.py` `_await_turn_end`): after the
  existing per-tick liveness pump, drain the wedge-nudge channel and inject
  `continue\n` into the parked PTY. Fail-silent; container-only, so non-PTY is
  untouched by construction.
- **Bridge-adapter wiring** (`bridge_adapter.py`): a `poll_wedge_nudge` closure
  (mirrors `_poll_steering`) bound to the session_id, passed into `Container`.
- **Health-loop producer** (`session_health.py` running-scan): a new branch for
  the *live-handle* case (`in_scope_handle is not None`) that, for a granite PTY
  session whose normalized frame has been frozen past a threshold and that has
  not yet been nudged this window, pushes one wedge-nudge and sets the latch —
  without killing and without incrementing `recovery_attempts`.

### Flow

Live granite PTY session parked in `_await_turn_end`, frame frozen
→ session-health 30s tick observes `in_scope_handle is not None` + `last_pty_activity_at` stale > `NUDGE_WEDGE_THRESHOLD_S` + no latch
→ push one wedge-nudge to `steering:nudge:{session_id}`; set latch (TTL = turn-wait window)
→ next `_await_turn_end` liveness tick drains the nudge channel, writes `continue\n` to the parked PTY
→ **if PM un-sticks:** a real turn/tool completes → `last_turn_at`/`last_tool_use_at` advance → session healthy, no kill
→ **if still frozen:** no genuine progress; the existing 600s `pm_hang` budget breaks the loop → normal teardown/recovery (the escalation), exactly one nudge having been spent.

### Technical Approach

- **Build the consumer first (the missing piece).** Add to `_await_turn_end`'s
  per-tick loop a fail-silent drain of `self._poll_wedge_nudge` (new optional
  callback). **Placement (critique r1 ordering fix):** the drain must go
  **after** the crash-detection / crash-resume block — i.e., after the
  `if not alive: … pty = resumed … continue` block ends at
  `container.py:1379`, and before the bounded-timeout check at
  `container.py:1382`. Placing it earlier (right after the step-2 liveness pump
  at `container.py:1327-1331`) is a bug: on a tick where the PTY has died, the
  drain would `write` the `continue` to the *dead* `pty` and the durable latch
  would still be spent, burning the one-nudge window with zero keystrokes
  reaching a live PTY. After line 1379 the `pty` local is guaranteed live
  (the `if not alive` arm either returns or reassigns `pty = resumed` and
  `continue`s), so the nudge always reaches a live PTY.
- **Reuse the exact crash-resume token — no new write shape.** For each drained
  nudge, inject `CRASH_RESUME_CONTINUE` (`container.py:387`, the literal
  `"continue"`) via `pty.write(CRASH_RESUME_CONTINUE)` — the *same* constant and
  call the crash-resume path already uses at `container.py:1528`. This makes the
  idempotency claim concrete (verified: `_resume_crashed_pty` writes
  `CRASH_RESUME_CONTINUE` at `:1528`) and sidesteps any submit/newline
  divergence by mirroring the existing re-arm mechanism byte-for-byte.
- **No `_cycle_idle` gate is needed or wanted.** The nudge class is a bare
  `continue` to a *parked* PTY, and the separate channel guarantees no ordinary
  operator steering is ever consumed mid-turn (so the "does not corrupt PM's
  in-flight turn" guarantee is preserved by *not* touching the ordinary path,
  and by only ever writing the idempotent `CRASH_RESUME_CONTINUE` token).
- **`session_id`-only channel keying is correct despite the role-parameterized
  wait (critique r1).** `_await_turn_end(…, role)` is called per-role (pm/dev),
  but the wedge-nudge channel is keyed by `session_id` alone. This is the right
  granularity, not an omission: (1) the *producer* observes only session-level
  Redis fields (`last_pty_activity_at`, `last_pty_read_loop_at`) and cannot
  distinguish a PM-parked wedge from a Dev-parked one — the wedge is a property
  of the AgentSession, not of a role; (2) the Container run loop is
  single-threaded, so **exactly one** `_await_turn_end` is executing at any
  instant, and it drains the nudge into the one `pty` it is bound to — which is
  precisely the currently-parked (wedged) PTY. A `continue` to "whichever PTY
  this session is parked on right now" is exactly the intended un-stick, so a
  role field would add a filter with no reachable failure it prevents. (Recorded
  as a deliberate design choice; if a future producer needs to target a specific
  role, add a `role` field to the nudge payload then — not now.)
- **Separate channel, not the ordinary queue.** `pop_all_steering_messages` is
  an atomic LPOP-all; draining it mid-turn would strip pending operator steering
  the top-of-turn path owns. So the nudge rides its own key
  (`steering:nudge:{session_id}`) with its own `push_wedge_nudge` /
  `pop_wedge_nudges`. Ordinary steering (`steering:{session_id}`) is untouched.
- **Two keys (signal channel + durable latch), not one GETDEL flag — this is
  load-bearing, not over-abstraction (critique r1).** The obvious collapse is a
  single one-slot flag: producer `SET steering:nudge:{id} continue EX <ttl> NX`,
  consumer `GETDEL`. It is wrong, because `GETDEL` **clears** the key on drain,
  which destroys the one-per-window latch: a producer tick that runs *after* the
  consumer has drained finds the key absent, its `SET NX` succeeds, and a
  **second** nudge fires inside the same turn-wait window — violating Success
  Criterion 2 / Risk 3 ("exactly one nudge per window"). The invariant requires
  a latch that **survives the drain**, which a self-clearing flag cannot be. So
  the design is deliberately two keys: (a) the signal channel
  `steering:nudge:{session_id}` that the consumer drains-and-clears, and (b) a
  separate TTL latch (`steering:nudge:latch:{session_id}`, `SET NX EX`) that the
  producer sets and that the consumer never touches — it expires only with the
  turn-wait window. The two-key split is what makes "at most one nudge per
  window" a structural property rather than a timing accident.
- **Why a *channel* and not just a single-producer flag: named future
  producers.** The push/pop channel (rather than an inline boolean the
  session-health loop owns) exists so additional detectors can feed the same
  one-shot recovery rung without re-plumbing the consumer: the cross-process
  liveness-wedge detector (#1815/#1823/#1728, already referenced by
  `_crash_resume_in_flight` at `container.py:1478-1480`) and the tool-timeout
  loop (`_agent_session_tool_timeout_loop`) are the concrete near-term
  candidates. Any of them can `push_wedge_nudge(session_id)`; the shared latch
  still guarantees one nudge per window across *all* producers.
- **Producer keys on the frozen normalized frame, gated to PTY + live handle.**
  In the running-scan (`session_health.py:~3221`, alongside the existing
  `in_scope_handle is None` #944 orphan `elif` at `:3221`) add a sibling branch
  for `in_scope_handle is not None`. Gate on granite-PTY transport
  (`last_pty_read_loop_at is not None`; non-PTY sessions have it `None`, exactly
  as `_prime_pty_alive` distinguishes them) AND `last_pty_activity_at` stale
  beyond `NUDGE_WEDGE_THRESHOLD_S` (well under the 600s `pm_hang` budget — start
  at ~240s) AND no active latch. On match: `push_wedge_nudge(session_id)`, set
  the latch, do **not** set `should_recover`, do **not** call
  `_apply_recovery_transition`, do **not** touch `recovery_attempts`.
- **#1820 ownership-boundary reconciliation (critique r1 BLOCKER).** The
  narrowed `elif` at `session_health.py:3221` was deliberately restricted by
  #1820 to `in_scope_handle is None` (the #944 shared-`worker_key` orphan net)
  precisely because `in_scope_handle is not None` sessions are owned by Fix #3's
  in-scope progress-deadline watcher (`agent_session_queue.py:1790`,
  `reason_kind="progress_deadline"`). Re-entering that population needs an
  explicit justification, and here it is: **what #1820 split is *recovery /
  kill* ownership — who is authorized to cancel-and-respawn an in-scope
  session — not who may *observe* it.** Fix #3 owns the cancel scope: it calls
  the recovery transition with `handle=None` because it kills the session its
  own owned task is executing. The new nudge branch takes **no recovery action
  whatsoever**: it never sets `should_recover`, never calls
  `_apply_recovery_transition`, never mutates `recovery_attempts`, and never
  kills. It only pushes a best-effort steering keystroke onto a separate Redis
  channel. It therefore cannot compete with, pre-empt, double-fire, or race
  Fix #3's kill decision — the two branches act on disjoint *verbs* (nudge vs.
  cancel), even though they observe the same `in_scope_handle is not None`
  population.
- **The decision windows are also disjoint, so there is zero temporal
  contention.** Fix #3 fires only at `SESSION_PROGRESS_DEADLINE_S = 1800s`
  (`agent_session_queue.py:1462`). For the `_await_turn_end` wedge shape this
  plan targets, the container self-terminates to `pm_hang` at the 600s
  `_hook_turn_end_wait_s` budget (spike-3) — **before** Fix #3's 1800s deadline
  is ever reached. The nudge fires in the 240s-600s band, entirely inside the
  pre-`pm_hang` window and entirely before Fix #3 could act. So even setting the
  verb argument aside, Fix #3 is preempted-unreachable for this shape and the
  branches never co-fire on the same session. (The nudge branch's own comment —
  see Documentation — must carry this reconciliation so a future reader does not
  mistake it for a re-widening of the #1820 split.)
- **The injected-echo hazard governs the reprieve/idempotency, not the kill.**
  `_normalize_pty_buffer` strips only animation noise, so the `continue` echo is
  *genuine new text* → it advances `last_pty_activity_at` once. Therefore
  `last_pty_activity_at` freshness is NOT a valid "the nudge worked" signal after
  a nudge. Two consequences: (1) the one-nudge-per-window guarantee is enforced
  by the durable TTL latch (not by re-reading activity), and (2) escalation is
  the existing 600s `pm_hang` backstop — no new kill code and no reliance on
  post-nudge `last_pty_activity_at`. "Genuine recovery" is observable only via
  `last_turn_at`/`last_tool_use_at` advancing (a real turn/tool), which the echo
  cannot fake.
- **No new kill path.** The escalation to respawn is the pre-existing
  `pm_hang`/recovery teardown that already fires at the 600s turn-wait budget.
  The nudge is purely a best-effort un-stick inserted ahead of it. This keeps the
  change additive and avoids duplicating recovery logic (#1036 "competing
  recovery functions" antipattern).
- **Constants:** `NUDGE_WEDGE_THRESHOLD_S` (default ~240s, env-overridable) and
  the latch TTL (= `_hook_turn_end_wait_s`, so at most one nudge per turn-wait
  window). Both live beside the existing session-health / container constants.

## Spike Results

### spike-1: Is the steering queue drained mid-`_await_turn_end`?
- **Assumption**: "`_await_turn_end`'s liveness pump does not drain external steering; only the top-of-turn `_poll_steering` does."
- **Method**: code-read (`container.py:1269-1400`, `:2090-2160`; `bridge_adapter.py:610-650`)
- **Finding**: Confirmed. `_await_turn_end` polls only `consumer.poll()` (hook edge file) + a byte-level `read_until_idle`; the external-steering drain (`self._poll_steering()`) is called exclusively at `container.py:2098`, top of the steady-state turn loop.
- **Confidence**: high
- **Impact on plan**: The mid-run drain is genuinely missing infrastructure; building it is the core deliverable.

### spike-2: Does injecting `continue` falsely reset the wedge detector?
- **Assumption**: "The `continue` echo advances `last_pty_activity_at`, masking a persistent wedge."
- **Method**: code-read (`bridge_adapter.py:150-181` `_normalize_pty_buffer`, `:962-989` `_make_pty_read_callback`)
- **Finding**: Confirmed. `_normalize_pty_buffer` strips spinner/elapsed/cursor animation only; genuinely new text (the `continue` echo) normalizes to a *different* string, so the diff-gate stamps `last_pty_activity_at`. The reprieve/idempotency must therefore not rely on `last_pty_activity_at` after a nudge.
- **Confidence**: high
- **Impact on plan**: Idempotency enforced by a durable TTL latch; "nudge worked" judged via `last_turn_at`/`last_tool_use_at`; escalation is the existing 600s `pm_hang` backstop.

### spike-3: Does the 1800s progress-deadline (the issue's named killer) actually fire for this wedge?
- **Assumption**: "The progress-deadline cancel scope is the reachable live-session producer."
- **Method**: code-read (`container.py:118-126`, `:1297`, `:1378-1400`; `config/settings.py:488`; `agent_session_queue.py:1790-1870`)
- **Finding**: Refuted for this wedge shape. `_hook_turn_end_wait_s` defaults to 600s, so `_await_turn_end` returns `saw_turn=False`/`pm_hang` at 600s — before the 1800s `SESSION_PROGRESS_DEADLINE_S`. The 1800s deadline is preempted; the reachable producer must fire within 600s.
- **Confidence**: high
- **Impact on plan**: Producer is the 30s session-health running-scan (live-handle branch), not the progress-deadline scope. Escalation backstop is the 600s `pm_hang`.

### spike-4: Can the nudge latch avoid a Popoto schema migration?
- **Assumption**: "The one-nudge latch requires a new `AgentSession` DatetimeField."
- **Method**: code-read (`agent/steering.py:214-247` self-draft-attempts pattern; `models/agent_session.py:378-567`)
- **Finding**: Refuted. `agent/steering.py` already stores raw-Redis TTL counters (`_self_draft_attempts_key`, `bump_self_draft_attempts`) outside Popoto. The nudge latch follows that exact pattern — a TTL key — so no model field and no migration.
- **Confidence**: high
- **Impact on plan**: No Popoto migration; Update System burden drops to zero (see that section).

## Data Flow

1. **Entry point (producer):** session-health loop tick (`_agent_session_health_check` running-scan, ~30s cadence). Reads a fresh `AgentSession`; observes `in_scope_handle` from `_active_sessions`, plus `last_pty_read_loop_at` / `last_pty_activity_at` on the row.
2. **Producer decision:** live handle + PTY transport + frozen frame + no latch → `push_wedge_nudge(session_id)` writes to `steering:nudge:{session_id}`; latch key set with TTL.
3. **Transport (Redis):** the nudge dict sits on its own list, isolated from `steering:{session_id}`.
4. **Consumer:** the parked `_await_turn_end` tick — **after** the crash-resume block (past `container.py:1379`, where `pty` is guaranteed live) — calls `self._poll_wedge_nudge()` (bridge-adapter closure → `pop_wedge_nudges(session_id)`), drains the nudge, and injects `CRASH_RESUME_CONTINUE` via `pty.write(CRASH_RESUME_CONTINUE)` (the same token/call the crash-resume path uses at `:1528`).
5. **Output — recovered:** PM finishes a turn → parent `Stop` edge → `_await_turn_end` returns `saw_turn=True`; `last_turn_at`/`last_tool_use_at` advance; the session continues normally.
6. **Output — still wedged:** no `Stop` edge; the 600s `_hook_turn_end_wait_s` budget expires → `saw_turn=False`/`pm_hang` → existing teardown/recovery. Exactly one nudge was spent (latch prevented re-nudge), no `recovery_attempts` consumed by the nudge.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The mid-run drain in `_await_turn_end` must be `try/except`-wrapped and log at `warning` on a raising `_poll_wedge_nudge` (mirroring the existing `_poll_steering` fail-silent block at `container.py:2100-2107` and `_fire_pty_read` at `:1411`). Add a test asserting a raising `poll_wedge_nudge` does NOT abort the turn wait (observable: the wait proceeds and still returns on the next `Stop` edge, and a `warning` is logged).
- [ ] `push_wedge_nudge` / `pop_wedge_nudges` / latch helpers must fail-silent on Redis errors (mirror `pop_all_steering_messages` at `steering.py:90-120`). Test: a Redis error in the poll closure yields `[]` and never crashes the run.
- [ ] The producer branch's push must be fail-silent — a push failure must not abort the health-loop tick or fall through to a kill. Test asserts a raising push leaves the session `running` (no recovery transition).

### Empty/Invalid Input Handling
- [ ] `pop_wedge_nudges` on an empty channel returns `[]` and injects nothing (no stray `continue\n` write). Test asserts no PTY write when the channel is empty.
- [ ] A whitespace-only / malformed nudge dict is skipped (reuse the `text.strip()` skip guard from the steady-state injection at `container.py:2148-2152`).

### Error State Rendering
- [ ] Not user-visible output. Verify instead that a spent-but-ineffective nudge is *observable*: assert the `pm_hang` teardown path still fires at the 600s budget after a nudge that did not un-stick the frame, and that a per-session telemetry counter (`{project_key}:session-health:wedge_nudge_sent`) increments on push (mirrors the existing `never_started_pty_deferred` counter at `session_health.py:~4123`).

## Test Impact

- [ ] `tests/unit/granite_container/test_container_hook_turn.py` (container turn-wait / `_await_turn_end` tests) — UPDATE: add a case proving the wedge-nudge channel is drained and `CRASH_RESUME_CONTINUE` injected during `_await_turn_end` (past the crash-resume block), and a regression-lock asserting the ordinary steering queue is NOT consumed mid-turn.
- [ ] `tests/unit/granite_container/test_granite_mid_run_steering_unit.py` (mid-run steering unit tests) — UPDATE: add wedge-nudge drain coverage alongside the existing mid-run steering cases; assert channel isolation from ordinary steering.
- [ ] `tests/unit/` steering tests (`test_steering*.py` if present) — UPDATE: add coverage for `push_wedge_nudge`/`pop_wedge_nudges`/latch on keys distinct from `steering:{id}` (signal `steering:nudge:{id}` and latch `steering:nudge:latch:{id}`); assert ordinary steering push/pop is unaffected.
- [ ] `tests/` session-health tests covering the running-scan / `no_progress` branch (the D0 and #944 tests added by PR #1880) — UPDATE: add a live-handle (`in_scope_handle is not None`) frozen-frame case asserting a nudge is pushed, `should_recover` stays False, `recovery_attempts` is NOT incremented, and no `_apply_recovery_transition` call occurs; assert the non-PTY (`last_pty_read_loop_at is None`) case pushes nothing.
- [ ] `tests/unit/granite_container/` steering-injection ordering test (from #1779) — UPDATE only if the new drain shares a helper; otherwise the top-of-turn drain path is unchanged and its tests must still pass verbatim (regression lock).

No existing test is deleted or replaced — the change is additive (a new channel, a new optional callback, a new producer branch). Existing steering and turn-wait behavior is preserved, so their tests remain valid as regression locks.

## Rabbit Holes

- **Reusing the ordinary steering queue for the nudge.** Tempting (no new key), but `pop_all_steering_messages` is atomic-all: draining it mid-turn would strip pending operator steering the top-of-turn path owns. Use a separate channel — do not try to peek-filter-selectively-remove the shared list.
- **Trusting `last_pty_activity_at` to decide "did the nudge work?".** The `continue` echo advances it (spike-2). Chasing a normalization tweak to strip the echo is a deep, fragile hole. Judge genuine recovery on `last_turn_at`/`last_tool_use_at`; enforce one-nudge with a durable latch.
- **Building a bespoke escalation-to-kill in the producer.** The 600s `pm_hang` budget already tears down a persistent wedge. Adding a second kill path duplicates recovery logic and risks the #1036 "competing recovery functions" antipattern. Reuse the backstop.
- **`_cycle_idle`-gating the mid-run write.** `_cycle_idle` waits for the turn to finish — which for a wedge never happens, so gating would deadlock the nudge. The separation of channels (not the idle gate) is what protects the ordinary in-flight turn.
- **An in-container self-nudge with no steering channel.** Simpler, but it does not satisfy the acceptance criterion wording ("a `continue` steering nudge *drained* and injected") and forecloses future external producers. Considered and rejected (see No-Gos).
- **Chasing the 1800s progress-deadline as the producer.** It is preempted at 600s for this wedge shape (spike-3). Do not wire the nudge there.
- **Collapsing the signal channel and the latch into one GETDEL flag.** Tempting (one key, `SET NX EX` + `GETDEL`), but `GETDEL` clears the key on drain and re-opens the one-per-window window (a later producer tick's `SET NX` succeeds → a second nudge). The latch MUST survive the drain, so it lives in a distinct key from the drained signal. See Technical Approach.

## Risks

### Risk 1: The mid-run `continue` write corrupts a genuinely in-flight (non-wedged) turn
**Impact:** A false-positive wedge detection injects `continue` mid-turn, derailing a healthy PM turn.
**Mitigation:** Two independent guards. (1) The producer only fires when `last_pty_activity_at` is stale beyond `NUDGE_WEDGE_THRESHOLD_S` — a genuinely active turn keeps that field fresh (diff-gated on the normalized frame), so an active turn is never a nudge target. (2) The nudge rides a separate channel and the mid-run drain writes only the idempotent `continue` token, never arbitrary operator text; the ordinary top-of-turn injection path (with its `_cycle_idle` ordering) is byte-for-byte unchanged.

### Risk 2: Nudge threshold vs. legitimate long tool calls
**Impact:** A long-but-legitimate tool call (e.g. a slow build) with a frozen frame could be nudged prematurely.
**Mitigation:** `last_pty_activity_at` is diff-gated on the normalized frame, and a live tool call typically repaints content (progress output) that de-freezes the frame. Set `NUDGE_WEDGE_THRESHOLD_S` conservatively (~240s) and env-overridable; a `continue` to a busy TUI is a no-op keystroke on the next prompt, not a turn interruption. Telemetry counter surfaces nudge frequency for tuning.

### Risk 3: Latch TTL too short → multiple nudges per wedge (violates "exactly one")
**Impact:** Re-nudging every 30s tick would spam the PTY and violate acceptance criterion 2.
**Mitigation:** Latch TTL = `_hook_turn_end_wait_s` (600s) so at most one nudge per turn-wait window; the window ends with the `pm_hang` teardown, which starts a fresh session/turn. Test asserts a second producer tick within the window pushes nothing.

## Race Conditions

### Race 1: Producer pushes while the consumer is between ticks
**Location:** `session_health.py` running-scan (push) ↔ `_await_turn_end` mid-run drain (placed after the crash-resume block, past `container.py:1379`, before the timeout check at `:1382`).
**Trigger:** The nudge lands on `steering:nudge:{id}` after a liveness tick has already polled.
**Data prerequisite:** The nudge dict must be durably on the Redis list before the *next* tick reads it.
**State prerequisite:** None beyond list durability — the drain is level-triggered (polls every `HOOK_POLL_INTERVAL_S`), so a nudge is picked up on the following tick.
**Mitigation:** Redis LPUSH is atomic and durable; the level-triggered per-tick drain guarantees pickup within one poll interval. No lost-update window.

### Race 2: Nudge echo advances `last_pty_activity_at` between producer read and latch set
**Location:** producer branch (read `last_pty_activity_at` → decide → push → set latch).
**Trigger:** A nudge from the *previous* window's echo could momentarily freshen `last_pty_activity_at`, hiding a still-wedged session on the next tick.
**Data prerequisite:** The latch must be set atomically with (or immediately after) the push, before the next tick.
**State prerequisite:** One nudge per window regardless of transient `last_pty_activity_at` freshening.
**Mitigation:** The durable TTL latch — not `last_pty_activity_at` — is the single source of "already nudged." Set it in the same producer branch as the push. Even if `last_pty_activity_at` freshens from the echo, the latch suppresses a second nudge; genuine recovery is judged on `last_turn_at`/`last_tool_use_at`.

### Race 3: Concurrent top-of-turn drain and mid-run drain
**Location:** `container.py:2098` (top-of-turn ordinary drain) vs. the new `_await_turn_end` mid-run nudge drain.
**Trigger:** Both run within one Container's single-threaded run loop, but read different keys.
**Data prerequisite:** The two channels (`steering:{id}` vs `steering:nudge:{id}`) must never share a key.
**State prerequisite:** The mid-run path must not consume ordinary steering.
**Mitigation:** Distinct Redis keys; the Container run loop is single-threaded (no true concurrency between the two drains within one session). Regression-lock test asserts channel isolation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1880] Prime-time liveness stamping and the D0 never-started redefinition — shipped in Part A; this plan does not touch `_prime_session` or `_prime_pty_alive`.
- Nudging non-PTY (SDK/headless) sessions — excluded by construction: `_await_turn_end` and the nudge channel are granite-container-only, and the producer gates on `last_pty_read_loop_at is not None`. This is an anti-criterion (see Verification).
- An in-container self-nudge mechanism that bypasses the steering channel — rejected in favor of the drained-steering design the acceptance criteria mandate; revisiting it is a separate design, not a follow-up of this plan.
- Wiring the nudge to the 1800s progress-deadline cancel scope — refuted by spike-3 (preempted at 600s); not a deferral, a correctness exclusion.
- A new kill/respawn code path — escalation reuses the existing 600s `pm_hang` teardown; adding a parallel killer is explicitly out of scope (avoids #1036 antipattern).

## Update System

No update system changes required. The nudge latch is a raw-Redis TTL key in
`agent/steering.py` (mirroring the existing `bump_self_draft_attempts` counter),
so there is **no Popoto model field and no schema migration** — `scripts/update/migrations.py`
is untouched. No new dependencies, config files, or services to propagate. The
two new tunables (`NUDGE_WEDGE_THRESHOLD_S`, latch TTL) are env-overridable
constants with safe defaults; adding them to `.env.example` is optional and not
required for the feature to function.

## Agent Integration

No agent integration required. This is a worker/container-internal recovery
mechanism with no agent-facing tool surface: no new CLI entry point in
`pyproject.toml [project.scripts]`, no MCP server or `.mcp.json` change, and no
new import in `bridge/telegram_bridge.py`. The bridge already spawns granite PTY
sessions via `BridgeAdapter`; the nudge machinery lives entirely below that seam.
Existing operator steering via `valor-session steer` continues to use the
ordinary `steering:{id}` channel, which this plan leaves unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` with a "Mid-run steering drain / wedge-nudge recovery rung" subsection: the separate nudge channel, the `_await_turn_end` drain seam, the health-loop producer gate, the injected-echo hazard, and the 600s `pm_hang` backstop.
- [ ] Update `docs/features/session-steering.md` (or the steering doc under `docs/features/`) to document the wedge-nudge channel as distinct from ordinary operator steering.
- [ ] Add/refresh the entry in `docs/features/README.md` index if the granite recovery surface is listed there.

### Inline Documentation
- [ ] Docstring on `push_wedge_nudge`/`pop_wedge_nudges` and the latch helper in `agent/steering.py` stating the channel-isolation contract.
- [ ] Comment in `_await_turn_end` at the drain seam explaining why no `_cycle_idle` gate is used (channel isolation + idempotent `CRASH_RESUME_CONTINUE` token), why the drain is placed **after** the crash-resume block (so `pty` is guaranteed live and the latch is never spent on a dead PTY), and citing #1879.
- [ ] Comment in the session_health producer branch explaining the PTY-transport + frozen-frame + latch gate, why `last_turn_at`/`last_tool_use_at` (not `last_pty_activity_at`) judge recovery, AND the #1820 reconciliation: this branch re-enters the `in_scope_handle is not None` population #1820 narrowed away, but takes no recovery action (no `should_recover`, no `_apply_recovery_transition`, no `recovery_attempts`), so it does not re-widen the #1820 recovery-ownership split — Fix #3 still solely owns the cancel scope.

## Success Criteria

- [ ] A wedged-but-alive granite session (live `exec_task`, normalized frame frozen) has a `continue` nudge drained from the wedge-nudge channel and injected into the parked PTY *inside* `_await_turn_end` (after the crash-resume block), with no completed turn boundary (unit/integration test).
- [ ] Exactly one `continue` nudge is pushed per turn-wait window (latch TTL); a second producer tick within the window pushes nothing (test).
- [ ] The nudge does NOT increment `recovery_attempts` and does NOT call `_apply_recovery_transition` (test asserts both).
- [ ] Escalation to respawn occurs only via the existing `pm_hang`/teardown backstop after the frame stays frozen through the reprieve — no new kill path (test drives a still-frozen post-nudge session to `pm_hang`).
- [ ] The mid-run drain never consumes the ordinary `steering:{id}` queue (channel-isolation regression lock).
- [ ] Non-PTY (SDK/headless) sessions are unaffected: the producer pushes nothing when `last_pty_read_loop_at is None` (test).
- [ ] A raising `poll_wedge_nudge` / Redis error does not abort the turn wait or the health tick (fail-silent tests).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (steering-channel)**
  - Name: steering-builder
  - Role: `agent/steering.py` wedge-nudge channel + TTL latch helpers
  - Agent Type: builder
  - Domain: redis-popoto (raw-Redis TTL key pattern; see DOMAIN_FRAMING.md)
  - Resume: true

- **Builder (container-drain)**
  - Name: container-builder
  - Role: `_await_turn_end` mid-run drain + `bridge_adapter.py` `poll_wedge_nudge` wiring
  - Agent Type: builder
  - Domain: async-concurrency (PTY turn-wait loop; fail-silent injection ordering)
  - Resume: true

- **Builder (health-producer)**
  - Name: producer-builder
  - Role: `session_health.py` live-handle frozen-frame producer branch + latch + telemetry counter
  - Agent Type: builder
  - Domain: async-concurrency (session-health loop; recovery-branch gating)
  - Resume: true

- **Test engineer (recovery-rung)**
  - Name: rung-tester
  - Role: unit/integration coverage across the three components + regression locks
  - Agent Type: test-engineer
  - Resume: true

- **Validator (recovery-rung)**
  - Name: rung-validator
  - Role: verify all success criteria + anti-criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: rung-doc
  - Role: feature + inline docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Wedge-nudge steering channel
- **Task ID**: build-steering
- **Depends On**: none
- **Validates**: `tests/unit/test_steering*.py` (create/extend)
- **Informed By**: spike-4 (TTL-key latch pattern, no migration)
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `push_wedge_nudge(session_id)` / `pop_wedge_nudges(session_id)` on a Redis key distinct from `_queue_key` (e.g. `steering:nudge:{id}`), fail-silent, mirroring `pop_all_steering_messages`.
- Add a TTL latch helper (set/check) mirroring `_self_draft_attempts_key` / `bump_self_draft_attempts`.
- Unit tests: channel isolation from ordinary steering; latch set/expire.

### 2. Mid-run drain consumer + bridge wiring
- **Task ID**: build-container
- **Depends On**: build-steering
- **Validates**: `tests/unit/granite_container/test_container_hook_turn.py` (extend) and `tests/unit/granite_container/test_granite_mid_run_steering_unit.py` (extend)
- **Informed By**: spike-1 (drain seam), spike-2 (idempotent `continue`, no `_cycle_idle`)
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: false
- Add optional `poll_wedge_nudge` callback to `Container.__init__`; drain + `pty.write(CRASH_RESUME_CONTINUE)` placed **after** the crash-resume block (past `container.py:1379`, before the `:1382` timeout check — NOT after the liveness pump at `:1327-1331`, which can target a dead PTY), fail-silent.
- Wire a `poll_wedge_nudge` closure in `bridge_adapter.py` bound to session_id (mirror `_poll_steering` at `:620`).
- Tests: mid-run injection without turn boundary; raising callback does not abort the wait; empty channel writes nothing; ordinary steering never consumed mid-turn; a nudge on a tick where the PTY just died is NOT written to the dead PTY (placement regression lock).

### 3. Health-loop producer branch
- **Task ID**: build-producer
- **Depends On**: build-steering
- **Validates**: session-health tests (extend PR #1880's D0/#944 cases)
- **Informed By**: spike-3 (600s window → 30s health loop is the producer), spike-2 (judge recovery on turn/tool fields)
- **Assigned To**: producer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the `in_scope_handle is not None` sibling branch in the running-scan: gate on PTY transport + frozen frame + no latch → push nudge, set latch, increment telemetry counter; no kill, no `recovery_attempts` change.
- Add `NUDGE_WEDGE_THRESHOLD_S` (default ~240s) and latch-TTL constants.
- Tests: nudge pushed for a live wedged PTY session; no push for non-PTY; no `recovery_attempts` increment; one-nudge-per-window.

### 4. Test coverage + regression locks
- **Task ID**: test-rung
- **Depends On**: build-container, build-producer
- **Assigned To**: rung-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- End-to-end: wedged session → nudge → un-stick path and still-frozen → `pm_hang` backstop path.
- Regression locks: ordinary steering behavior, top-of-turn drain, non-PTY untouched.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: test-rung
- **Assigned To**: rung-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and the steering feature doc; inline docstrings/comments per Documentation section.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: rung-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every Success Criterion and the Verification anti-criteria; produce a final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Wedge-nudge channel exists | `grep -c "def push_wedge_nudge" agent/steering.py` | output > 0 |
| Mid-run drain wired in turn-wait | `grep -c "poll_wedge_nudge" agent/granite_container/container.py` | output > 0 |
| Producer branch added | `grep -c "push_wedge_nudge" agent/session_health.py` | output > 0 |
| Producer telemetry counter present | `grep -c "wedge_nudge_sent" agent/session_health.py` | output > 0 |
| No Popoto migration added (anti-criterion) | `git diff --name-only main -- scripts/update/migrations.py` | output does not contain migrations.py |
| Nudge branch takes no recovery action (anti-criterion, test-backed) | `pytest tests/ -k "wedge_nudge and (recovery_attempts or no_recover)" -q` | exit code 0 (nudge branch never sets `should_recover` / calls `_apply_recovery_transition`) |
| Nudge does not consume recovery_attempts (test) | `pytest tests/ -k "wedge_nudge and recovery_attempts" -q` | exit code 0 |
| Channel isolation regression lock | `pytest tests/ -k "wedge_nudge and isolation" -q` | exit code 0 |
| Non-PTY unaffected (test) | `pytest tests/ -k "wedge_nudge and non_pty" -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | r1 | Producer re-enters the `in_scope_handle is not None` population #1820 narrowed away for Fix #3's progress-deadline watcher (`session_health.py:3168-3220` warning comment). | Technical Approach (#1820 reconciliation bullets); producer inline-comment doc task | Reconciled, not relocated: #1820 split *recovery/kill* ownership, not *observation*. The nudge branch takes no recovery action (no `should_recover`, no `_apply_recovery_transition`, no `recovery_attempts`), so it acts on a disjoint verb from Fix #3's cancel scope. Windows are also disjoint — the wedge's 600s `pm_hang` preempts Fix #3's 1800s deadline (spike-3), so the branches never co-fire. |
| Concern | r1 | Mid-run drain placed after the liveness pump (`:1327-1331`) burns the latch on a dead PTY when the crash-resume path reassigns `pty`. | Technical Approach (placement bullet); Task 2; Data Flow; Race 1 | Drain relocated to **after** the crash-resume block (past `container.py:1379`, before `:1382`), where `pty` is guaranteed live. Placement regression-lock test added. |
| Concern | r1 | Crash-resume "already writes continue" idempotency claim unverified. | Technical Approach (token bullet) | Verified: `_resume_crashed_pty` writes `CRASH_RESUME_CONTINUE = "continue"` (`container.py:387`) at `:1528`. Plan now reuses that exact constant/call. |
| Concern | r1 | Nudge channel keyed by `session_id` only while `_await_turn_end(…, role)` is role-parameterized. | Technical Approach (session_id-keying bullet) | Documented as safe: wedge is a session-level condition (producer reads session-level fields only); single-threaded run loop guarantees one `_await_turn_end` parked at a time, so the drain always reaches the parked PTY. Role field deferred to a future producer that needs it. |
| Concern | r1 | Verification anti-criterion greps undefined symbol `continue_nudge`. | Verification table | Replaced with a `wedge_nudge_sent` telemetry grep + a test-backed "nudge branch takes no recovery action" row. |
| Concern | r1 | Push/latch channel abstraction could collapse to one GETDEL flag, or needs future producers named. | Technical Approach (two-key bullets); Rabbit Holes | Justified keeping two keys: a single GETDEL flag clears on drain and re-opens the one-per-window window; the latch must survive the drain. Also named concrete future producers (#1815/#1823/#1728 wedge detector; tool-timeout loop). |
| NIT | r1 | Test paths reference nonexistent `tests/unit/granite/`. | Test Impact; Task 2 | Corrected to `tests/unit/granite_container/`; targets `test_container_hook_turn.py` and `test_granite_mid_run_steering_unit.py`. |

---

## Open Questions

1. **Producer premise correction:** The issue named the 1800s progress-deadline as the "live-session no-progress killer," but `_hook_turn_end_wait_s` defaults to 600s, so a `_await_turn_end` wedge self-terminates to `pm_hang` first (spike-3). This plan therefore places the producer in the 30s session-health running-scan and treats the 600s `pm_hang` as the escalation backstop. Confirm this reframing is acceptable, or state a preference for the in-container self-nudge alternative.
2. **`NUDGE_WEDGE_THRESHOLD_S` default:** 240s is chosen to sit safely under the 600s budget while tolerating brief legitimate frame-freezes. Is a different default (e.g. 180s or 300s) preferred given observed granite turn durations?
3. **Telemetry surface:** the plan adds a `wedge_nudge_sent` counter mirroring `never_started_pty_deferred`. Should it also emit a paired `wedge_nudge_recovered` counter (turn/tool advanced within the window) so nudge effectiveness is measurable on the dashboard, or is that scope creep for this rung?
