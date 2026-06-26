---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-26
tracking: https://github.com/tomcounsell/ai/issues/1779
last_comment_id:
---

# Granite PTY Mid-Run Steering (bridge→PM and PM→Dev injection)

## Problem

This repo runs agent sessions through a two-layer PTY-based architecture (the granite
container). When a Telegram message arrives, the bridge creates an `AgentSession` that
runs inside the granite container: a synchronous `Container.run()` loop manages two
interactive Claude TUI processes — a PM PTY and a Dev PTY. The PM reads the task, decides
routing, and hands off to Dev via `[/dev]` prefixes. The bridge already has a working
steering injection path (`_ack_steering_routed`) that dual-writes to a Redis list
(`steering:{session_id}`) and `AgentSession.queued_steering_messages` — **but the running
granite container never reads from either during execution.** Two scenarios are broken:

**Scenario 1 — Bridge cannot steer a running PM session.** Tom sends a clarifying Telegram
message while the granite container is actively running. The bridge correctly identifies
the running session and calls `_ack_steering_routed`, which queues the message — but
nothing in `Container.run()` ever consumes it. The message is silently lost until the
session ends.

**Scenario 2 — PM cannot steer the Dev PTY mid-task.** When the PM routes `[/dev]` and Dev
starts executing, the container blocks synchronously waiting for Dev to reach idle. The PM
has no channel to push a mid-task correction into Dev's PTY before Dev's turn completes.

**Current behavior:**
- `Container.run()`'s steady-state loop (`for turn in range(self.max_turns)` at
  `agent/granite_container/container.py:1313`) reads PM idle → classifies → routes. There
  is no steering-queue poll at any point.
- `_ack_steering_routed` (`bridge/telegram_bridge.py:865`) dual-writes to the Redis list
  via `agent/steering.py::push_steering_message` AND to `AgentSession.queued_steering_messages`
  via the model method — but the container thread consumes neither.
- The Popoto `queued_steering_messages` field is polled at every turn boundary for SDK/CLI
  harness sessions, but granite execution bypasses that path entirely — it runs
  `BridgeAdapter.run()` → `Container.run()` as a single blocking `asyncio.to_thread` call.

**Desired outcome:**
1. A Telegram message arriving while a granite session is running is injected into the PM's
   PTY at its next turn boundary, so the PM can incorporate it before its next `[/dev]`.
2. The PM has a routing prefix (`[/dev:steer]`) to push a mid-task correction into Dev's
   PTY without waiting for Dev to finish its current turn.

## Freshness Check

**Baseline commit:** `4784cac222ddf6cc74c98478bdd2b84f717982ef`
**Issue filed at:** 2026-06-24T09:02:43Z (2 days before plan)
**Disposition:** Minor drift

**File:line references re-verified:**
- `bridge/telegram_bridge.py:865` — `_ack_steering_routed` definition — **still holds** (line
  865 exactly). Dual-write confirmed at lines 942-947.
- `bridge/telegram_bridge.py:947` — Redis push — **still holds**; line 947 is
  `push_steering_message(session_id, text, sender_name, is_abort=is_abort)`.
- `agent/granite_container/container.py:1316` — steady-state loop — **drifted** to line
  **1313** (`for turn in range(self.max_turns):`). The only per-turn hook is `_on_turn`
  (heartbeat) at 1369-1376; no steering poll. Claim holds.
- `agent/granite_container/bridge_adapter.py` — `BridgeAdapter` holds `self._agent_session`
  — **still holds** (set at line 399; `Container` constructed at 518 with callbacks but NOT
  the agent_session).
- `models/agent_session.py` — `queued_steering_messages = ListField(null=True)` at line 225;
  `push_steering_message`/`pop_steering_messages` at 2015/2054 — **still holds**.
- `agent/agent_session_queue.py:4067` — turn-boundary poll for SDK/CLI — re-exported helpers
  confirmed; granite bypasses this — claim holds.

**Cited sibling issues/PRs re-checked:**
- #1018 — CLOSED 2026-04-17 ("PM→Dev mid-execution steering silently fails on CLI-harness
  children"). Analogous bug for a *different* execution path (`scripts/steer_child.py`); does
  not cover granite. Confirms these are parallel fixes to parallel code paths.
- #1572 — CLOSED 2026-06-11 ("Granite PTY container: production cutover"). Introduced the
  `Container.run()` architecture this issue extends. Still the current architecture.

**Commits on main since issue was filed (touching referenced files):**
- `1d6adcb2` downgrade liveness save() log noise — **irrelevant** (logging only).
- `872d77c7` deterministic U-state worker recovery — **irrelevant** (watchdog, not steering).
- `88523548` quarantine non-bot ids on live-flag mismatch — **irrelevant** (bot-id gating).
- `fa56feef` stop stripping agent's own name from inbound — **irrelevant** to the steering path.

**Active plans in `docs/plans/` overlapping this area:** none. Other `granite_*` plans
(`granite_pty_production_cutover`, `granite_lossless_checkpoint_resume`,
`granite_root_session_runner`) address cutover, checkpoint/resume, and root-runner concerns
— none touch steering injection.

**Notes:** Loop reference corrected to `container.py:1313` in Technical Approach below.

## Prior Art

- **#1018** (closed): PM→Dev mid-execution steering for CLI-harness children via
  `scripts/steer_child.py`. Different execution model (SDK/CLI child sessions, not granite
  PTYs). No reusable code, but confirms the *pattern* — write to a child's input channel
  mid-task — is an accepted shape in this codebase.
- **#1572** (closed, PR #1487 PoC → cutover): introduced `Container.run()`, the synchronous
  alternating PM↔Dev loop. This plan extends that loop without re-architecting it.
- **`agent/steering.py`** (existing infrastructure): a complete, race-free per-session Redis
  list queue (`steering:{session_id}`, RPUSH/LPOP) with `push_steering_message`,
  `pop_all_steering_messages` (FIFO drain), `clear_steering_queue`, `has_steering_messages`.
  Its docstring already anticipates a single consumer per session — the granite container
  becomes that consumer for granite sessions. **No new queue infrastructure is needed.**

No prior attempt to add steering consumption to the granite container exists.

## Research

No relevant external findings — this is a purely internal change to an in-repo PTY
orchestration loop. No external libraries, APIs, or ecosystem patterns are involved.
Proceeding with codebase context.

## Data Flow

**Part 1 — Bridge → PM steering injection:**
1. **Entry point**: Tom sends a Telegram message while a granite session runs. The bridge's
   message handler matches the running session and calls `_ack_steering_routed`
   (`telegram_bridge.py:865`).
2. **Dual-write (bridge process)**: `_ack_steering_routed` calls
   `agent_session.push_steering_message(text)` (Popoto field) AND
   `agent.steering.push_steering_message(session_id, text, sender_name, is_abort)` which
   `RPUSH`es a JSON payload onto `steering:{session_id}` (Redis list).
3. **Poll (worker process, container thread)**: at the top of each `Container.run()`
   steady-state turn, the container calls its injected `poll_steering()` callback. The
   callback (wired by `BridgeAdapter`) calls `agent.steering.pop_all_steering_messages(session_id)`
   — an atomic FIFO `LPOP` drain that is **race-free across processes** (no
   read-modify-write).
4. **Injection**: if messages returned, the container waits for PM idle, then writes
   `\n[Steering from {sender}]: {text}\n` (one block per message) to the PM PTY. An
   `is_abort` message instead triggers a graceful loop break (`exit_reason="steer_abort"`).
5. **Output**: the existing per-turn idle read captures PM's response to the steering, and
   the loop routes PM's new classification normally.

**Part 2 — PM → Dev mid-task steering:**
1. **Entry point**: PM emits `[/dev:steer] <correction text>` as its turn output.
2. **Classification**: `classify_pm_prefix` (`granite_classifier.py:160`) already parses the
   `:steer` suffix into `harness="steer"` via `PREFIX_TOKEN_RE` (no classifier change needed
   to *parse* it).
3. **Routing**: `_route_pm_classification` (`container.py:1430`) detects the reserved
   `harness == "steer"` suffix in the dev branch (before `_get_builder`), writes the payload
   to the Dev PTY immediately (submitting it as a Dev turn), and does NOT block on
   `builder.run_turn`/Dev idle.
4. **Continuation**: the container writes a short acknowledgment to the PM PTY (so PM
   produces its next turn rather than hanging on an empty idle read), then returns
   `should_break=False`.
5. **Output**: Dev's response to the steer is folded into the next `[/dev]` handoff's
   `cycle_idle(dev)` wait — "picked up on the next dev turn."

## Architectural Impact

- **New dependencies**: none. Reuses `agent/steering.py` (already imported across the bridge).
- **Interface changes**: `Container.__init__` gains one optional keyword-only callback
  `poll_steering: Callable[[], list[dict]] | None = None`. Additive; default `None` preserves
  every existing caller (CLI `valor-granite-loop`, tests) unchanged. The classifier's public
  shape is unchanged — `[/dev:steer]` already parses; only the container's interpretation of
  the `steer` suffix is new.
- **Coupling**: slightly increases coupling between `Container` and `agent/steering.py`, but
  only via the `BridgeAdapter`-supplied callback — `Container` itself stays storage-agnostic
  (it calls a callable, never imports Redis). This preserves the container's testability with
  a plain in-process stub callback.
- **Data ownership**: the granite container becomes the authoritative consumer of
  `steering:{session_id}` for granite sessions (the role `agent/steering.py`'s docstring
  reserves for "the one consumer per session"). No ownership change for the Popoto
  `queued_steering_messages` field — granite simply does not consume it (the Redis list is
  the race-free channel; see Risk 1).
- **Reversibility**: high. Both paths are additive; reverting restores the prior blocking
  loop with no migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer (the synchronous-loop interaction in Part 2 warrants a
careful review pass).

**Interactions:**
- PM check-ins: 1-2 (confirm the Part 2 alternation semantics and the Redis-list-vs-ListField
  consumption choice — see Open Questions).
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on PATH | `command -v claude` | Integration tests drive real PTYs |
| Ollama model reachable | `curl -s http://localhost:11434/api/tags` | Integration tests are env-gated on this (skip otherwise) |
| Redis (popoto) reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Steering queue is Redis-backed |

Integration tests self-skip with a structured reason when `claude`/Ollama are unreachable
(same gate as `tests/integration/test_granite_container_loop.py`). Redis is required for both
unit and integration runs (already a baseline test dependency).

## Solution

### Key Elements

- **`poll_steering` callback on `Container`**: an optional, storage-agnostic callable the
  container invokes once per steady-state turn. Returns a list of pending steering message
  dicts (`text`, `sender`, `is_abort`). `Container` never touches Redis directly.
- **`BridgeAdapter` wiring**: builds the `poll_steering` closure from the session's
  `session_id`, delegating to `agent.steering.pop_all_steering_messages(session_id)`
  (race-free atomic LPOP drain). Fail-silent — a poll error returns `[]` and never crashes
  the run.
- **PM-injection step in the loop**: at the top of each turn, drain steering; if present,
  cycle PM to idle and write a `[Steering from {sender}]: {text}` block, then let the
  existing per-turn idle read capture PM's response. An `is_abort` message breaks the loop
  with `exit_reason="steer_abort"`.
- **`[/dev:steer]` reserved suffix**: the dev-routing branch detects `harness == "steer"`,
  writes the payload to the Dev PTY immediately (no Dev-idle wait), writes a short
  continuation ack to PM, and returns without breaking.

### Flow

**Part 1:** Granite session running → Tom sends Telegram message → bridge `_ack_steering_routed`
RPUSHes to `steering:{session_id}` → container's next turn polls and drains the queue → PM
PTY receives `[Steering from Tom]: …` at idle → PM incorporates it and emits its next
`[/dev]`/`[/user]`/`[/complete]`.

**Part 2:** PM decides Dev is off-track → PM emits `[/dev:steer] focus on the auth module, skip
the migration` → container writes that text to Dev's PTY immediately → container writes
"Steering delivered to Dev; continuing" ack to PM → PM emits its next turn → Dev's response to
the steer is folded into the next `[/dev]` handoff's idle wait.

### Technical Approach

- **Loop reference**: the steady-state loop is `for turn in range(self.max_turns):` at
  `agent/granite_container/container.py:1313` (issue cited 1316 — minor drift).

- **Consume the Redis list, not the Popoto field.** The issue's solution sketch proposes
  polling `AgentSession.queued_steering_messages`, but that Popoto `ListField` is consumed via
  a cross-process read-modify-write (`pop_steering_messages` reads the list, sets it to `[]`,
  partial-saves) which races the bridge's concurrent `push_steering_message`. The
  `steering:{session_id}` Redis list is **atomic** (RPUSH/LPOP) and `agent/steering.py`
  already exposes `pop_all_steering_messages(session_id)` as a FIFO drain whose docstring
  reserves exactly one consumer per session. Granite consumes the Redis list; this is a
  deliberate, prevention-first deviation from the issue sketch (see Open Question 1 and
  Risk 1). The Popoto field continues to serve the SDK/CLI harness path unchanged.

- **`Container` stays storage-agnostic.** Add `poll_steering: Callable[[], list[dict]] | None
  = None` as a keyword-only `__init__` parameter, stored as `self._poll_steering`. The
  container calls it; it never imports `agent/steering.py` or Redis. This keeps unit tests
  able to pass a trivial in-process stub callback (no Redis, no PTY mocking required for the
  routing-logic unit tests).

- **Injection point and ordering (Part 1).** At the top of each `for turn` iteration, after
  the existing stale-buffer guard and before the `pm_baseline` snapshot, call
  `self._poll_steering()` if set. If it returns messages: first handle any `is_abort` message
  by setting `result.exit_reason = "steer_abort"` and breaking. Otherwise, cycle PM to idle
  (`self._cycle_idle(self._pm_pty)`) so the write lands as a fresh user turn — this is what
  guarantees "does not interrupt PM's current tool execution," because `_cycle_idle` waits for
  PM to finish whatever it is doing — then write each message as `\n[Steering from {sender}]:
  {text}\n`. Fall through to the existing per-turn idle read, which (via the content-identity
  `pm_baseline` guard) captures PM's NEW response to the steering and routes it.

- **`[/dev:steer]` handling (Part 2).** `classify_pm_prefix` already yields
  `destination="dev", harness="steer"` for `[/dev:steer]`. Define a module constant
  `STEER_HARNESS_SUFFIX = "steer"` in `container.py` and, in `_route_pm_classification`'s dev
  branch (after the empty-payload guard, before `_get_builder`), branch on `classification.harness
  == STEER_HARNESS_SUFFIX`: write `dev_prompt + "\n"` to `self._dev_pty` (submits it as a Dev
  turn), do NOT call `builder.run_turn` and do NOT cycle Dev idle, then write a one-line
  continuation ack to the PM PTY (e.g. `PM_DEV_STEER_ACK`) so PM produces its next turn rather
  than hanging on an empty idle read, append a `TurnRecord` with `classification="dev_steer"`,
  and return `RouteOutcome(should_break=False)`. Empty `[/dev:steer]` payload nudges PM (reuse
  the existing empty-`[/dev]` compliance-miss path). Document that `steer` is a **reserved
  harness suffix** — `_get_builder` must never receive it (only `claude`/`None` and `pi` are
  real harnesses).

- **Abort semantics.** `pop_all_steering_messages` returns each message's `is_abort` flag
  (auto-detected from `ABORT_KEYWORDS`). When a drained message is an abort, the container
  breaks the loop gracefully with `exit_reason="steer_abort"`; the wrap-up guard's eligible-exit
  set is left unchanged (abort is a clean terminal, not a wrap-up trigger) — confirm the exit
  is handled by `session_executor`'s clean-exit gate or add `steer_abort` to the appropriate
  set during build.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `poll_steering` closure in `BridgeAdapter` wraps `pop_all_steering_messages` in
  try/except returning `[]` — add a unit test asserting a raised Redis error yields `[]` and
  logs a warning (observable behavior, not silent `pass`).
- [ ] The container's call to `self._poll_steering()` is wrapped fail-silent (like `_on_turn`)
  — test that a callback raising does not crash the run (loop continues).
- [ ] PM-injection write and `[/dev:steer]` Dev write: assert PTY-write failures surface as a
  loop break with a real `exit_reason` (`pm_hang`/`dev_hang`), not a swallowed exception.

### Empty/Invalid Input Handling
- [ ] `poll_steering` returns `[]` (no pending messages) → loop proceeds exactly as today
  (regression: assert the no-steering turn behavior is byte-identical to the current loop).
- [ ] Empty/whitespace `[/dev:steer]` payload → PM compliance nudge (reuse empty-`[/dev]` path),
  not a Dev write.
- [ ] Steering message with empty `text` → skipped, not written to PM PTY.
- [ ] Invalid JSON in the Redis list is already dropped-with-warning by
  `pop_all_steering_messages`; assert the container tolerates a `[]` drain in that case.

### Error State Rendering
- [ ] `is_abort` steering → `exit_reason="steer_abort"` and the wrap-up/terminal path still
  delivers a user-facing message (the human is told the run was aborted, not left silent).
- [ ] Verify steering injection does not suppress PM's eventual user-facing delivery.

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` — UPDATE: add a case asserting
  `[/dev:steer] fix the auth test` → `destination="dev", harness="steer", payload="fix the auth
  test"`. Existing cases unchanged (the parse already supports it; this locks in the contract).
- [ ] `tests/integration/test_granite_container_loop.py` — UPDATE (or add a sibling test file
  `tests/integration/test_granite_mid_run_steering.py`): add env-gated end-to-end cases for
  both steering paths without mocking PTY writes. Existing loop test is unaffected (additive).
- [ ] `tests/unit/test_steering.py` / `tests/unit/test_steering_mechanism.py` — REVIEW: confirm
  no assertion presumes the granite container is a non-consumer of `steering:{session_id}`;
  update only if such an assertion exists.
- [ ] No changes to `tests/unit/test_bridge_ack_steering_routed.py` — the bridge dual-write is
  unchanged; granite only adds a consumer.

No other existing tests are affected — the `Container` signature change is additive
(keyword-only, default `None`), so every current `Container(...)` construction and CLI
invocation continues to pass.

## Rabbit Holes

- **Re-architecting the synchronous loop into a concurrent PM/Dev model.** The issue
  explicitly forbids this and forbids new thread-synchronization primitives. Both steering
  paths must live inside the existing `for turn` loop. Do not introduce a background reader
  thread, an asyncio queue between PM and Dev, or a select-loop over both PTYs.
- **Making `[/dev:steer]` truly preempt a mid-flight Dev turn.** In the strict alternating
  loop, PM only runs when Dev is idle, so a "mid-task" Dev rarely exists at the moment PM emits
  `[/dev:steer]`. Chasing true preemption (interrupting Dev's in-progress tool call) means
  signal handling / PTY control characters and is out of scope. The write-and-continue
  semantics (buffer for Dev's next read) are the intended, bounded behavior — see Open
  Question 2.
- **Filtering steering by `target_agent`.** `push_steering_message` accepts an optional
  `target_agent` field, but no consumer filters on it today. Do not build target routing here;
  granite drains all messages to PM.
- **Migrating the SDK/CLI harness to the Redis list too.** Out of scope — that path already
  works via the Popoto field at its own turn boundary. Touching it risks regressing #1018's fix.
- **Unifying with `scripts/steer_child.py` (#1018).** Different execution model; explicitly
  dropped in the issue's recon.

## Risks

### Risk 1: Cross-process steering-queue race if the Popoto ListField is used
**Impact:** If the container polled `AgentSession.queued_steering_messages` (the issue's
sketch), the bridge's `push_steering_message` (read [A], append B, save [A,B]) could interleave
with the container's `pop_steering_messages` (read [A], save []), dropping a message.
**Mitigation:** Consume the atomic `steering:{session_id}` Redis list via
`pop_all_steering_messages` (LPOP is atomic; no read-modify-write). Documented as a deliberate
deviation from the issue sketch. The Popoto field is left for the SDK/CLI path.

### Risk 2: Part 2 alternation tension — PM rarely "catches" Dev mid-task
**Impact:** In the synchronous loop, PM is only active between Dev turns, so `[/dev:steer]`
usually lands when Dev is already idle. The steer text is then buffered as Dev's next turn
rather than interrupting an in-flight one. Behavior may surprise a PM expecting true preemption.
**Mitigation:** Document the write-and-continue semantics explicitly in
`docs/features/granite-pty-production.md`; the PM persona guidance should describe `[/dev:steer]`
as "queue a correction Dev will see immediately as its next input," not "interrupt Dev now." See
Open Question 2.

### Risk 3: PM hang after `[/dev:steer]` if no continuation prompt is written
**Impact:** After a `[/dev:steer]` the loop continues to the next turn, which calls
`_cycle_idle(PM)`. With nothing written to PM, PM has no new turn to produce → idle timeout →
spurious `pm_hang`.
**Mitigation:** Always write a one-line `PM_DEV_STEER_ACK` to the PM PTY immediately after the
Dev write, so PM emits its next classification. Covered by an integration assertion that the run
does not exit `pm_hang` after a `[/dev:steer]`.

### Risk 4: Steering injected at the wrong PM boundary corrupts classification
**Impact:** Writing the steering block while PM is mid-tool-call could merge with PM's in-flight
output and produce a malformed first line, tripping the compliance-miss path.
**Mitigation:** Always `_cycle_idle(self._pm_pty)` to confirm PM idle BEFORE writing the
steering block; the existing `pm_baseline` content-identity guard then requires a NEW
text-bearing entry, so PM's steering response is read cleanly.

## Race Conditions

### Race 1: Concurrent push (bridge) and drain (container) of the steering queue
**Location:** `agent/steering.py` (`push_steering_message` RPUSH vs. `pop_all_steering_messages`
LPOP) consumed from `agent/granite_container/container.py:1313` loop.
**Trigger:** Tom sends a Telegram message at the same instant the container drains the queue.
**Data prerequisite:** The bridge must have RPUSHed the JSON payload before the container's LPOP
for the message to be seen this turn; otherwise it is seen next turn.
**State prerequisite:** Exactly one consumer (the granite container) drains a given session's
list — guaranteed because a session runs in one worker thread.
**Mitigation:** Redis RPUSH/LPOP are atomic; a message pushed after the drain is simply picked up
on the next turn (at-most-one-turn latency, never lost). This is precisely why the Redis list is
chosen over the Popoto ListField (Risk 1).

### Race 2: `poll_steering` callback runs on the `asyncio.to_thread` worker thread
**Location:** `BridgeAdapter` closure → `Container.run()` worker thread.
**Trigger:** The container calls `poll_steering()` from the worker thread (same thread as
`_on_turn`, `_on_pty_read`).
**Data prerequisite:** Popoto's Redis client is a synchronous, thread-safe connection (already
used by `_bump_last_turn_at`/`_on_pty_read` from this same thread).
**State prerequisite:** None beyond an open Redis connection.
**Mitigation:** Blocking Redis I/O off the event loop is the established pattern for granite
callbacks; no new synchronization is introduced.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1018] PM→Dev mid-execution steering for SDK/CLI-harness children via
  `scripts/steer_child.py` — different execution model, already closed; not unified here.
- Nothing else deferred — both steering paths, the classifier contract test, the integration
  tests, the abort path, and the docs update are all in scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the worker's granite
execution path. No new dependencies, config files, services, or machine-specific wiring. The
`agent/steering.py` queue and the bridge dual-write already exist and ship today.

## Agent Integration

No new agent-facing CLI or MCP surface is required — this is a bridge/worker-internal change.
The agent reaches the new capability through paths that already exist:
- **Bridge → PM (Part 1)**: the bridge's existing `_ack_steering_routed` already queues steering
  on every matching-session message; this plan only adds the worker-side consumer. No bridge
  change.
- **PM → Dev (Part 2)**: the PM persona prime (`.claude/commands/granite/prime-pm-role.md`)
  must document the new `[/dev:steer]` prefix so the PM knows to emit it. This is a persona-prompt
  change (load-bearing for the feature to be usable), not a new tool registration. Add it during
  build and assert via a grep in Verification.
- **Integration tests** drive the real `valor-granite-loop`/`Container` path and verify both
  steering channels function end-to-end without mocking PTY writes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` with a new `## Mid-run steering` section
  documenting both paths: the bridge→PM Redis-list consumption (and why the Redis list, not the
  Popoto field) and the `[/dev:steer]` write-and-continue semantics (including the alternation
  caveat from Risk 2).
- [ ] No new `docs/features/README.md` row needed (granite-pty-production.md is already indexed);
  verify the index entry still describes the doc accurately and update its summary if needed.

### Inline Documentation
- [ ] Docstring on `Container.__init__`'s new `poll_steering` parameter (storage-agnostic
  contract; returns list of dicts with `text`/`sender`/`is_abort`).
- [ ] Docstring/comment on the `STEER_HARNESS_SUFFIX` constant marking `steer` as reserved.
- [ ] Comment at the loop injection point explaining the cycle-idle-before-write ordering.

## Success Criteria

- [ ] A Telegram message arriving during a running granite session is injected into PM's PTY at
  the next PM turn boundary — verified by an integration test that pushes to
  `steering:{session_id}` mid-run and inspects the PM transcript for the steering text.
- [ ] Steering injection does not interrupt PM's current tool execution — the container cycles PM
  to idle before writing; verified by the integration test observing PM completes its in-flight
  turn before the steering response.
- [ ] PM can emit `[/dev:steer] <message>` and the container writes it to the Dev PTY immediately
  without blocking on Dev idle — verified by an integration test asserting the run continues and
  does not exit `pm_hang`/`dev_hang`.
- [ ] `[/dev:steer]` parse contract locked by a unit test in `test_granite_classifier.py`.
- [ ] `is_abort` steering message breaks the loop with `exit_reason="steer_abort"` and the human
  still receives a user-facing message.
- [ ] Integration test exercises both paths without mocking PTY writes (env-gated; skips cleanly
  when `claude`/Ollama unreachable).
- [ ] `docs/features/granite-pty-production.md` documents both steering paths.
- [ ] PM persona prime documents `[/dev:steer]` (grep-confirmed in Verification).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff format . && python -m ruff check .` clean.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (container-steering)**
  - Name: `container-builder`
  - Role: Add `poll_steering` to `Container`, the loop injection step, the `[/dev:steer]`
    routing branch, and `STEER_HARNESS_SUFFIX`; wire the `BridgeAdapter` closure; update PM prime.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: `test-builder`
  - Role: Classifier contract test, fail-path unit tests (poll error → `[]`, callback raise →
    no crash), and the env-gated integration test for both paths.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (steering)**
  - Name: `steering-validator`
  - Role: Verify all success criteria, run the env-gated integration test where possible, confirm
    the additive signature did not break existing callers.
  - Agent Type: validator
  - Resume: true

- **Documentarian (granite-docs)**
  - Name: `granite-documentarian`
  - Role: Update `docs/features/granite-pty-production.md` and verify the features index.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Add `poll_steering` to Container and the PM-injection loop step
- **Task ID**: build-poll-and-inject
- **Depends On**: none
- **Validates**: tests/unit/granite_container/ (existing container unit tests still pass),
  tests/integration/test_granite_mid_run_steering.py (create)
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: true
- Add keyword-only `poll_steering: Callable[[], list[dict]] | None = None` to `Container.__init__`;
  store as `self._poll_steering`.
- At the top of the `for turn in range(self.max_turns)` loop (`container.py:1313`), after the
  stale-buffer guard and before `pm_baseline`, drain via `self._poll_steering()` (fail-silent).
- Handle `is_abort` → break with `exit_reason="steer_abort"`. Otherwise cycle PM idle, write each
  `\n[Steering from {sender}]: {text}\n`, fall through to the existing idle read.

### 2. Add `[/dev:steer]` routing branch and reserved suffix
- **Task ID**: build-dev-steer
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_granite_classifier.py,
  tests/integration/test_granite_mid_run_steering.py (create)
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: true
- Define `STEER_HARNESS_SUFFIX = "steer"`; in `_route_pm_classification`'s dev branch (after the
  empty-payload guard, before `_get_builder`), branch on `classification.harness == STEER_HARNESS_SUFFIX`.
- Write payload to Dev PTY (no idle wait), write `PM_DEV_STEER_ACK` to PM PTY, append
  `TurnRecord(classification="dev_steer")`, return `should_break=False`.
- Empty payload → reuse the empty-`[/dev]` compliance nudge.

### 3. Wire the BridgeAdapter poll_steering closure
- **Task ID**: build-adapter-wiring
- **Depends On**: build-poll-and-inject
- **Validates**: tests/unit/ (adapter closure unit test — poll error → `[]`)
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: false
- In `BridgeAdapter.run`, pass `poll_steering=` to the `Container(...)` construction, bound to a
  closure that calls `agent.steering.pop_all_steering_messages(self._session_id)` and returns the
  message dicts; wrap fail-silent (return `[]`, log warning on error). Resolve `session_id` from
  the agent_session.

### 4. Update PM persona prime with `[/dev:steer]`
- **Task ID**: build-pm-prime
- **Depends On**: build-dev-steer
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a `[/dev:steer]` description to `.claude/commands/granite/prime-pm-role.md` (and any shared
  prefix-contract block), framed as "queue an immediate correction Dev sees as its next input."

### 5. Tests — classifier contract, fail paths, and integration
- **Task ID**: build-tests
- **Depends On**: build-poll-and-inject, build-dev-steer, build-adapter-wiring
- **Validates**: tests/unit/granite_container/test_granite_classifier.py,
  tests/integration/test_granite_mid_run_steering.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Classifier: `[/dev:steer]` parse contract.
- Fail paths: poll raises → `[]`; container callback raise → loop continues; empty `[/dev:steer]`
  → nudge; `is_abort` → `steer_abort` exit with user-facing delivery.
- Integration (env-gated, no PTY mocking): Part 1 push-to-Redis-mid-run → PM transcript shows
  steering; Part 2 PM emits `[/dev:steer]` → Dev PTY receives text, run does not `pm_hang`.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: granite-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add `## Mid-run steering` to `docs/features/granite-pty-production.md` (both paths, Redis-list
  rationale, alternation caveat). Verify the features index entry.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every success criterion; confirm additive signature did
  not break existing callers (CLI + tests).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Container accepts poll_steering | `python -c "import inspect; from agent.granite_container.container import Container; assert 'poll_steering' in inspect.signature(Container.__init__).parameters"` | exit code 0 |
| Steer suffix reserved | `grep -n "STEER_HARNESS_SUFFIX" agent/granite_container/container.py` | output contains STEER_HARNESS_SUFFIX |
| Container consumes Redis list (not Popoto field) | `grep -n "pop_all_steering_messages" agent/granite_container/bridge_adapter.py` | output contains pop_all_steering_messages |
| Adapter does NOT poll Popoto field for granite steering | `grep -c "pop_steering_messages(" agent/granite_container/bridge_adapter.py` | match count == 0 |
| dev_steer routing present | `grep -n "dev_steer" agent/granite_container/container.py` | output contains dev_steer |
| PM prime documents [/dev:steer] | `grep -n "dev:steer" .claude/commands/granite/prime-pm-role.md` | output contains dev:steer |
| Classifier contract test exists | `grep -rn "dev:steer" tests/unit/granite_container/test_granite_classifier.py` | output contains dev:steer |
| Tests pass | `pytest tests/unit/granite_container/ tests/unit/test_steering.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Consume the Redis list or the Popoto field?** This plan recommends the atomic
   `steering:{session_id}` Redis list (`pop_all_steering_messages`) over the issue's stated
   `queued_steering_messages` ListField, to eliminate the cross-process read-modify-write race
   (Risk 1). Both are populated by the existing dual-write, so this is purely a consumer choice.
   Confirm this deviation from the issue sketch is acceptable.

2. **`[/dev:steer]` semantics under strict alternation.** Because PM only runs between Dev turns,
   `[/dev:steer]` is effectively "buffer a correction for Dev's next read," not "interrupt Dev
   now" (Risk 2). Is the write-and-continue semantic sufficient for the intended use, or is true
   mid-tool-call preemption a requirement (which would breach the no-re-architecture constraint)?

3. **Steering provenance in the PM injection.** Should the injected block include the sender name
   (`[Steering from Tom]: …`) verbatim, or a neutral `[Steering]: …`? The Redis payload carries
   `sender`; using it gives PM useful context but couples the prompt to bridge-supplied display
   names.
