---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2642
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-07T08:00:11Z
---

# Flip Steering Writers to the Room Key

## Problem

A session can be *steered* mid-flight: a supervisor, the Telegram bridge, the watchdog, or a
health-check hook pushes a short instruction onto a Redis list that the running session drains at
a turn boundary. That list has always been keyed by session id (`steering:{session_id}`), so an
instruction addressed to a session that dies before draining it is stranded forever.

The #2494 durability plan reparents that inbox onto a **Room** — a durable `(project_key,
addressee)` pair representing "the conversation", which outlives any individual session. PR #2622
shipped the *read* half: `steering:room:{room_id}` is drained by all five consumers plus the
`valor-session status` peek. No writer targets it, so the Room leg is permanently empty and the
durability property does not yet exist.

The writer flip was deliberately withheld because of the Race 1 two-phase deploy rule
(`docs/plans/durability-room-job-agentrun.md:318`, `:643`): ship the dual-read consumer first,
flip writers only after every worker is on new code. Every machine has since had the chance to
pick up #2622.

**Current behavior:** `steering:room:{room_id}` is read by everybody and written by nobody. Steers
land on `steering:{session_id}` and die with the session.

**Desired outcome:** every *conversation-level* steering write targets the Room key when a Room
resolves, so a steer outlives its target session and is drained by whichever session next serves
that Room. The legacy key remains the target for writes that are meaningless or destructive when
delivered to a different session — **aborts** and **session-scoped diagnostics** — and remains the
fallback when no Room resolves. It is fully drained by the untouched dual-read consumers either way.

Three classes of write are therefore *deliberately* left on the legacy key. This is not an
incomplete flip; it is the flip's correctness boundary, and it is stated as a success criterion:

| Class | Sites | Why legacy |
|---|---|---|
| Abort signals | any push with `is_abort` true, explicit or auto-detected from `ABORT_KEYWORDS` — including `scripts/steer_child.py:119` | "You MUST stop immediately" is destructive and non-idempotent. Delivered to the wrong session it kills innocent work while the intended target keeps running. Stranding an abort is the correct failure mode. (D4) |
| Session-scoped diagnostics | `agent/output_handler.py:1227`, `agent/session_health.py:3467`, `monitoring/session_watchdog.py:557` | Each payload describes state of *this* session — the draft it just emitted, the tool that wedged, the tool it repeated. Delivered to a successor it is noise at best; the drafter one also escapes its session-keyed attempt budget (`steering:attempts:{session_id}`, `agent/steering.py:262`) and can re-enter. The latter two fire at *wedged* sessions, the ones most likely to die undrained. (D1) |
| No live row / ORM-free writers | `bridge/telegram_bridge.py:2070`, `scripts/migrate_steering_queue_drain.py:126` | Neither holds a session row it can derive a Room from. Fabricating one would reintroduce the unsorted-`[0]` defect (Race 4). |

A fourth boundary is temporal rather than per-site: a Room-key message that no session drains is
otherwise immortal, so the Room leg — and only the Room leg — is age-bounded at drain time (D5).

## Freshness Check

**Baseline commit:** `e0157a0b2` (the single baseline for this document — see the Verification
preamble; re-verified at round 3, no drift from the original `e6d0e2bc7` read)
**Issue filed at:** 2026-08-07T06:15:51Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `agent/steering.py:110` — `key = _queue_key(session_id)` is the sole key selection in
  `push_steering_message` — still holds.
- `agent/steering.py:158-160` — `pop_all_steering_messages` flattens both legs, discarding
  provenance — still holds.
- `agent/steering.py:48-55` and `:15-22` — both docstrings still claim no writer targets the Room
  key — still holds.
- `agent/session_runner/runner.py:603-616` — `_default_steering_push` pushes with no room; sibling
  `_default_steering_pop` (587-601) already resolves `room_id_for_session` — still holds.
- `agent/health_check.py:475-487` — `_repush_messages` loops `push_steering_message(session_id,
  ...)`; `_handle_steering` (490) holds a resolved `room_id` at line 514 but never forwards it —
  still holds.
- `models/room.py:95-100` — `room_id_for_session` returns `None` without `project_key` — still holds.

**Cited sibling issues/PRs re-checked:**

- PR #2622 — merged 2026-08-07T03:46:37Z. This plan's entire premise; still current.
- #2626 — closed (the M2 tracking issue for #2622).
- #2494 — open parent.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since` over
`agent/steering.py`, `agent/health_check.py`, `agent/session_runner/runner.py`, `models/room.py`,
`agent/output_handler.py` returns nothing.

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` is the
parent plan — this work is one of its named remaining releases (line 655), not a conflict. A
sibling lane is in flight on `react()` transport derivation via the system Room; it touches
`models/room.py` consumers, not the steering keys. Coordination note only.

**Notes:** No drift. Every line number in the issue body is exact.

## Prior Art

- **PR #2622** — "Durability M2: Room model, DM recovery coverage, steering dual-read, shadow
  inbox (Tasks 10-12)", merged 2026-08-07. Shipped the read half this plan completes. Succeeded;
  its deliberate omission of the writer flip is the reason this plan exists.
- **PR #2610** — "steering no longer drops the pending session's own message", merged 2026-08-07.
  Adjacent steering-loss fix at the dispatch seam. Different failure mode (in-flight drop, not
  post-mortem stranding); no overlap.
- **PR #2627** — "Route chatless reflection output to the system Room instead of a dead telegram
  enqueue", merged 2026-08-07. Establishes the precedent that `system`-addressee sessions are a
  legitimate Room and are already being written to. Directly informs Risk 2 below.
- **PR #2631** — "Durability M3: Job model, granite routing, advisory promises, durable reactions",
  merged 2026-08-07. Parallel milestone; does not touch the steering keys.

No prior *failed* attempt at this flip exists — the withholding was a planned ordering constraint,
not a rollback. The **Why Previous Fixes Failed** section is therefore omitted.

## Research

No relevant external findings — proceeding with codebase context. This is a purely internal change:
Redis list key selection inside one repo's own module, with no external library, API, or ecosystem
pattern involved.

## Spike Results

Seven spikes. Six resolved by code-read; spike-5 was measured against live Redis. Two reversals are
recorded here rather than smoothed away. **spike-1b and spike-5 reversed the plan's central
mechanism**: the writer no longer resolves a Room itself — the caller supplies it. **spike-1b's
round-2 correction and spike-6 then narrowed the plan's scope**: the flip became selective (six
sites, not twelve) and the Room leg gained an age bound. spike-4's fix was consequently *dropped*,
because narrowing the flip dissolved the problem it existed to solve. Everything below reflects the
settled state.

### spike-1: Is `push_steering_message` the single writer funnel?

- **Assumption**: "There are three writer sites that each need an independent flip."
- **Method**: code-read (`grep -rn "push_steering_message(" bridge tools agent monitoring scripts
  --include="*.py"`)
- **Finding**: **The assumption is wrong in a helpful direction.** `push_steering_message` is the
  only function that RPUSHes/LPUSHes a steering payload. **Twelve** non-test call sites route
  through it: `bridge/telegram_bridge.py:979,2617,2650`, `tools/valor_session.py:856`,
  `agent/session_executor.py:853` (via the `_push_steering_message` alias imported at 851),
  `agent/session_health.py:3467`, `agent/output_handler.py:1227`, `agent/health_check.py:480`,
  `agent/session_runner/runner.py:610`, `monitoring/session_watchdog.py:557`,
  `scripts/steer_child.py:119`, `scripts/migrate_steering_queue_drain.py:126`.
  **Count correction (critique):** the earlier draft said "thirteen" and cited
  `agent/session_executor.py:1930`; that line does not call the writer. Success criteria below
  say "every non-test caller", never a count.
- **Confidence**: high
- **Impact on plan**: One funnel to change, plus a `room_id` argument threaded from each caller.

### spike-1b: Which callers already hold an `AgentSession` object?

- **Assumption**: "Most callers have only a bare `session_id` string, so the writer must look the
  session up itself."
- **Method**: code-read of ~30 lines of context around all twelve call sites
- **Finding**: **Ten of twelve have a session object in hand, or one stack frame up.** The `Room?`
  column records the round-2 correction: a site flips only if its payload is conversation-level
  **and** the object it would derive from is guaranteed to be the *live* row.

  | Site | Object in scope | Room? | Notes |
  |---|---|---|---|
  | `bridge/telegram_bridge.py:979` (`_ack_steering_routed`) | via callers | **yes, 4 of 5** | helper takes `session_id: str`. Callers 1809 (`matching_session`), 1835, 1868 (`live_guard`) and 2178 (`fresh_session`) hold a row. **Caller 2070 holds none** — 2058-2067 bind `guard_sessions` and test only truthiness, passing the bare string `guard_session_id`. It passes `room_id=None`. |
  | `bridge/telegram_bridge.py:2617` | `session` (loaded 2593-2594) | yes | that load is an **unsorted** `sessions[0]`; it must be sorted newest-first before deriving (Race 4) |
  | `bridge/telegram_bridge.py:2650` | `active_edit` | yes | selected by an **unsorted** `next(...)` over `edit_sessions` at 2641-2646; same sort requirement |
  | `tools/valor_session.py:856` | `session` | yes | resume path, distinct from the status peek at 1031 |
  | `agent/session_executor.py:853` | `session` | yes | passes `session.session_id` |
  | `agent/session_runner/runner.py:610` | `self._agent_session` | yes | sibling `_default_steering_pop` (599-601) already calls `room_id_for_session` on it |
  | `agent/health_check.py:480` (`_repush_messages`) | `room_id` one frame up | yes | `_handle_steering` receives a resolved `room_id`; `watchdog_hook:627` produced it |
  | `agent/session_health.py:3467` | `entry: AgentSession` | **no** | tool-timeout advisory naming the wedged tool — session-scoped diagnostic. Also the repo's only `front=True` push (3473); see Risk 3. Stays legacy (D1). |
  | `monitoring/session_watchdog.py:557` (`_inject_watchdog_steer`) | via callers | **no** | loop-break steer naming the repeated tool — session-scoped diagnostic. Stays legacy (D1); the helper gains no parameter. |
  | `agent/output_handler.py:1227` | `session` | **no** | drafter self-draft, bounded by the session-keyed counter `steering:attempts:{session_id}` (`agent/steering.py:262`). Stays legacy (D1). |
  | `scripts/steer_child.py:119` | `child` | **no (abort)** | unconditionally `is_abort=True` (line 123). The writer's abort guard forces legacy regardless (D4); the call site passes `room_id=None` so the intent is legible there too. |
  | `scripts/migrate_steering_queue_drain.py:126` | **none** | no | raw Redis hash scan; deliberately ORM-free |

- **Confidence**: high
- **Impact on plan**: **Decisive.** It removes any need for the writer to query for a session.
  `room_id_for_session(session)` is pure `getattr` + string work with zero Redis I/O
  (`models/room.py:65-100`), so an explicit `room_id` argument from each caller is free. See
  spike-5 for why the alternative is not viable.
- **Round-2 corrections**: (a) caller 2070 was wrongly listed as holding `matching_session`; it
  holds no row and must pass `room_id=None` rather than have a builder invent `guard_sessions[0]`.
  (b) Three diagnostic writers and one unconditional-abort writer are removed from the flip set —
  **six sites flip, six deliberately do not**. (c) Two flipped sites derive from unsorted row
  selections that must be sorted first.

### spike-5: How expensive is `AgentSession.query.filter(session_id=...)`?

- **Assumption**: "The writer can cheaply resolve a session by `session_id` when the caller does
  not supply one." (This was the previous draft's core mechanism.)
- **Method**: measured against live Redis on the plan baseline
- **Finding**: **Prohibitive.** `models/agent_session.py:155` declares `session_id = Field()` and
  Popoto's `Field()` defaults to `indexed=False` — only `KeyField`/`IndexedField` resolve by set
  intersection, everything else scans every `AgentSession` hash. Three consecutive warm calls
  returning **zero** rows measured 2.55s / 2.06s / 2.36s. On `bridge/telegram_bridge.py:979`
  (the inbound-Telegram fast path) and inside `_requeue_pending_steers` (once per leftover
  message) that is a seconds-scale regression per push.
  Indexing the field is also rejected: `session_id` is near-unique, and
  `models/agent_session.py:329` already records the repo's rule that indexing a
  high-cardinality value creates one Redis Set per distinct value. A `Field(indexed=True)` flip
  would additionally require a `rebuild_indexes()` migration in `MIGRATIONS`, converting a Small
  plan into a schema migration.
- **Confidence**: high (measured)
- **Impact on plan**: **The writer performs no lookup at all.** `push_steering_message` becomes
  pure key selection on an argument it is handed. The 299 repo-wide
  `filter(session_id=...)` scans are a real standing problem, but they are not this plan's
  problem and this plan adds none.

### spike-2: Is the per-message provenance tag necessary?

- **Assumption**: "`pop_all_steering_messages` must tag each entry with its source leg so
  `_repush_messages` can route Room-sourced siblings back to the Room key."
- **Method**: code-read (`agent/health_check.py:490-566`, `agent/steering.py:138-161`)
- **Finding**: Unnecessary. `_handle_steering` already holds the resolved `room_id` (it passes it
  to the drain at line 514); forwarding it as `_repush_messages(session_id, messages,
  room_id=room_id)` satisfies the issue's acceptance criterion with zero payload change, zero
  consumer change, and no per-message tag. A legacy-sourced sibling in that same drain is
  **upgraded** to the Room key, which is safe because all consumers dual-read both legs.
- **Confidence**: high
- **Impact on plan**: Drops the tagging design entirely. Also keeps the per-message loop free of
  any lookup, which matters because `_repush_messages` runs inside a PostToolUse hook.
- **Count correction (critique)**: `_repush_messages` has **four** call sites in
  `_handle_steering`, not three: 530 (abort-sibling primary), 536 (abort-sibling retry, inside
  `except`), 560 (non-abort primary), 564 (non-abort retry, inside `except`). The two inside
  `except` blocks are exactly what a mechanical replace on the primary call shape misses, and the
  miss is silent — the message lands, just on the legacy leg.

### spike-3: Do the ~66 existing steering tests survive the flip?

- **Assumption**: "Flipping the writer will break the existing suite, which pushes to synthetic
  session ids like `test_dualread_order`."
- **Method**: code-read (`tests/integration/test_steering.py`, `models/room.py:95-100`)
- **Finding**: They survive. Under the final design (spike-1b/spike-5: no lookup, `room_id` is an
  explicit argument) a call that passes no `room_id` writes to the legacy key by construction.
  Every existing test calls `push_steering_message` without a `room_id`, so its behavior is
  literally unchanged. One test is a genuine exception:
  `tests/integration/test_steering.py::TestRoomDualRead::test_handle_steering_drains_room_leg`
  (class at line 1678, method at line **1748** — an earlier draft cited class `TestSteeringDualRead`
  and line ~1746, neither of which resolves) asserts in its docstring and body that a Room-drained
  message is re-pushed **to the legacy list** — that assertion inverts once `_handle_steering`
  forwards its `room_id`, and must be updated.
- **Confidence**: high
- **Impact on plan**: "No `room_id` → legacy key" is a hard correctness requirement, not a nicety.
  Pins the one test needing UPDATE. It also survives D5 unchanged: the age filter applies only to
  the Room leg and only past `STEERING_ROOM_MAX_AGE_SECONDS`, which no test-scoped message reaches.

### spike-6: What bounds the lifetime of an undrained Room-key message?

- **Assumption**: "Something already expires a steering message that nobody drains, so the Room key
  inherits a bound for free."
- **Method**: code-read of `agent/steering.py` end to end, plus a repo-wide caller search
- **Finding**: **Nothing bounds it. Three independent mechanisms are each absent.**
  1. **No TTL.** The module docstring states it outright: `TTL: None (persist until consumed or
     session completion)` (`agent/steering.py:13`). Neither `push_steering_message` nor
     `_room_queue_key` calls `EXPIRE`. Today "session completion" is a real bound because the key
     is session-scoped and dies with a finalized session. **A Room key has no completion event** —
     the Room is immortal by design, which is the whole point of the change.
  2. **No sweeper.** `clear_steering_queue` (`agent/steering.py:183`) has **zero** production
     callers. Every reference in the repo is a test (`tests/integration/test_steering.py`,
     `tests/unit/test_watchdog_token_alert.py`, `tests/unit/test_watchdog_loop_break_steer.py`)
     or the `agent/__init__.py` re-export at lines 37 and 75. Nothing schedules it.
  3. **No age filter on read.** The payload carries a `timestamp` (`agent/steering.py:119`) and
     `pop_all_steering_messages` documents it (line 153), but **no drain path reads it**.
     `_drain_list` LPOPs and JSON-decodes; it never compares against the clock.
- **The destructive instance.** `bridge/telegram_bridge.py:978` auto-sets `is_abort` from
  `ABORT_KEYWORDS = {stop, cancel, abort, nevermind}`. Post-flip, a user typing "stop" in a chat
  whose session has already finished would write a **permanent abort** to that chat's Room key; the
  next session opened in that chat drains it at its first turn boundary and is told "You MUST stop
  immediately." That specific case is closed independently by D4 (aborts never leave the legacy
  key), but the general case — any instruction outliving its usefulness — is not.
- **Confidence**: high (all three verified by direct read)
- **Impact on plan**: adds decision **D5** and Task 4. The bound goes on the *read* side, in
  `_drain_list`, not as a Redis TTL: a TTL on the list expires the whole key including fresh
  entries pushed later, whereas a per-entry `timestamp` comparison expires exactly the stale ones.
  It costs one float comparison per entry on a path that already decodes every entry. Applied to
  the Room key only, so the legacy leg's behavior is bit-for-bit what it is today.

### spike-4: Are all consumers actually Room-aware?

- **Assumption**: "PR #2622 gave every consumer a Room leg, so the flip is read-safe."
- **Method**: code-read (`grep -rn peek_steering` across the repo)
- **Finding**: **No — one consumer was missed.** `agent/steering.py:223-238
  peek_steering_sender` reads `LINDEX _queue_key(session_id) -1` only and has no `room_id`
  parameter. Its sole production caller is `agent/output_handler.py:1160-1162`, the drafter
  self-draft loop guard (`if peek_steering_sender(session_id) == DRAFTER_FALLBACK_SENDER: return`).
  The drafter's own push at `agent/output_handler.py:1227` goes through `push_steering_message`, so
  after the flip that push lands on the Room key and the guard can never observe it. The guard
  fails open and the drafter fallback can re-enter.
- **Confidence**: high
- **Round-2 resolution: the finding is real but its premise no longer holds, so the fix is
  DROPPED.** The fail-open only exists *if* `agent/output_handler.py:1227` writes to the Room key.
  It no longer does — the drafter self-draft steer is a session-scoped diagnostic and stays on the
  legacy key (D1). The guard therefore keeps observing exactly the messages it observes today,
  through the unchanged single-leg `LINDEX`.
  `peek_steering_sender` gains **no** `room_id` parameter, `agent/output_handler.py:1160-1162` is
  untouched, and the 11 `peek_steering_sender` patch sites in `tests/unit/test_output_handler.py`
  (701, 740, 786, 819, 843, 885, 922, 1112, 2528, 2565, 2600) need no re-check. Both facts are
  Verification anti-criteria, so "unmodified" is checked rather than assumed.
- **Why dropping is better than fixing.** Flipping the drafter push and then repairing the guard
  would also require moving its loop budget: `bump_self_draft_attempts` is keyed
  `steering:attempts:{session_id}` (`agent/steering.py:262`), so a Room-durable self-draft steer
  escapes the budget entirely — session A exhausts its three attempts and dies, session B drains
  the leftover with a fresh zero budget. That is a message-amplification loop on a live chat, and
  it is a strictly larger change than the guard fix. Both are recorded as Rabbit Holes: if the
  drafter push is ever flipped, the peek Room leg **and** the attempt-counter re-keying must land
  in the same release.
- **Impact on plan**: removes a task, a risk and a decision from the release.

## Data Flow

1. **Classify the write at the call site (new).** Six sites are *conversation-level* and flip; six
   deliberately do not (Problem section's three-class table, spike-1b's `Room?` column). A flipped
   caller that holds an `AgentSession` computes `room_id_for_session(session)` — pure `getattr`
   plus string formatting (`models/room.py:65-100`), no Redis, no ORM query, no measurable cost.
   A non-flipped caller passes an explicit `room_id=None` with the reason inline.
2. **Row selection must be sorted first (new).** Where the caller picked its row from a multi-row
   `filter(session_id=...)`, it sorts newest-first by `created_at` before selecting, so the Room is
   derived from the live row and not a superseded one (Race 4).
3. **Entry point** — the caller invokes
   `push_steering_message(session_id, text, sender, ..., room_id=<derived or None>)`.
4. **Key selection (changed)** — one expression, evaluated *below* the `ABORT_KEYWORDS`
   auto-detect: `_room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`.
   No lookup, no exception handling. Payload JSON is byte-identical to today. The `is_abort` term is
   D4: an abort is destructive and non-idempotent, so it never leaves the session it was aimed at,
   and reading `is_abort` before the auto-detect has run would read a stale value.
5. **Storage** — RPUSH (or LPUSH when `front=True`) onto the selected list. The repo's only
   `front=True` push is a diagnostic that stays legacy, so `front` never LPUSHes onto a shared Room
   list (Risk 3).
6. **Drain (changed only in that the Room leg is age-bounded)** — the worker's turn-boundary
   `_default_steering_pop`, the watchdog hook's `_handle_steering`, and three other consumers each
   drain legacy-then-Room. `_drain_list` discards **Room-key** entries older than
   `STEERING_ROOM_MAX_AGE_SECONDS` (D5); the legacy key is never filtered, so every message that
   exists today behaves exactly as it does today. A surviving Room-key message is served to
   whichever session in that Room next reaches a turn boundary — including a session created
   *after* the original target died. That is the durability property.
7. **Abort re-push (changed target)** — `_handle_steering` drains both legs; on an abort it
   forwards its already-resolved `room_id` into `_repush_messages`, which re-pushes the *non-abort*
   siblings to the Room key. The abort itself is consumed, never re-pushed, so this does not
   conflict with D4.
8. **Output** — the steer reaches a live session instead of being stranded.

## Architectural Impact

- **New dependencies**: none. `agent/steering.py` gains **no** model-layer import — it never touches
  the model layer. It does gain a settings read for the D5 age bound
  (`config.settings` → `TimeoutSettings.steering_room_max_age_seconds`), which is config, not model.
  `models.room.room_id_for_session` is imported at the *flipped* calling modules, which mostly
  import it already (`agent/session_runner/runner.py`, `agent/session_executor.py`,
  `tools/valor_session.py`). One new importer: `bridge/telegram_bridge.py` — function-local with
  `# noqa: PLC0415` where the module already uses that pattern.
  **`monitoring/session_watchdog.py`, `agent/session_health.py`, `agent/output_handler.py` and
  `scripts/steer_child.py` gain no such import**: their writes stay legacy (D1/D4), and the absence
  of the import is a Verification anti-criterion.
- **Interface changes**: three signatures gain an optional `room_id: str | None = None` keyword —
  `agent/steering.py::push_steering_message`, `agent/health_check.py::_repush_messages`, and the
  thin helper `bridge/telegram_bridge.py::_ack_steering_routed`. Two private helpers gain an optional
  `max_age_seconds: float | None = None` — `agent/steering.py::_drain_list` and
  `agent/steering.py::_peek_list` (D5); `has_steering_messages`'s Room leg swaps a raw `llen` for a
  filtered `_peek_list` call without changing its own signature.
  **`peek_steering_sender`, `pop_steering_message` and
  `monitoring/session_watchdog.py::_inject_watchdog_steer` are unchanged** — the earlier draft's
  peek-sender work is out of scope now that the drafter's push stays on the legacy key, and
  `pop_steering_message` inlines its own `lpop` loop with zero production callers. All added
  keywords are backward-compatible and additive.
- **New configuration**: one `TimeoutSettings` field, `steering_room_max_age_seconds`
  (env `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS`), with a default and a provisional-value comment.
  No new secret, no new config file.
- **Coupling**: `agent.steering` stays a Redis-key module with no model-layer dependency —
  deliberately, because the alternative (an internal `AgentSession` lookup) costs ~2.4s per push
  (spike-5) and would create a new steering→model edge. The settings edge it does add is the repo's
  standard one for a tunable bound.
- **Data ownership**: this is the point of the change, with a stated boundary. Conversation-level
  steering moves from being owned by a mortal `AgentSession` to being owned by an immortal `Room`.
  Aborts and session-scoped diagnostics stay owned by the session, because their meaning does.
- **Reversibility**: high. Reverting the writer restores legacy-only writes; consumers dual-read,
  so any messages already sitting on Room keys keep draining after a revert. There is no data
  migration and no schema change; the settings field defaults and can be left in place.

## Appetite

**Size:** Medium

Raised from Small at round 2. The shape outgrew a Small: six flipped sites plus six explicitly
non-flipped ones, five pre-existing unsorted row selections to harden (Race 4), a new
`TimeoutSettings` field and drain-time age filter (D5), an abort-exclusion branch (D4), an AST
census test, and a held-merge pipeline exit. `Small` also kept the plan off the force-FULL critique
triage path, which is the wrong signal for a change on the steering critical path.

**Team:** Solo dev, code reviewer

**Interactions:**

- PM check-ins: 1-2 (the fleet deploy gate is a human confirmation, and the selective-flip boundary
  — six sites deliberately not flipped — is a design call that needs a nod)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | The steering integration suite runs against real Redis |
| On-pin venv | `.venv/bin/python -c "import sys,pathlib; assert '.'.join(map(str,sys.version_info[:2])) in pathlib.Path('.python-version').read_text()"` | `scripts/pytest-clean.sh` aborts on an off-pin venv (#2617) |

## Solution

### Decisions

Five decisions are settled; there is no Open Questions section left to answer. D1 was **reversed**
at round 2 and D4/D5 were added there.

- **D1 — the flip is selective: three session-scoped diagnostic writers stay on the legacy key, and
  `peek_steering_sender` is therefore not touched at all.** `agent/output_handler.py:1227` (drafter
  self-draft), `agent/session_health.py:3467` (tool-timeout advisory naming the wedged tool) and
  `monitoring/session_watchdog.py:557` (loop-break steer naming the repeated tool) each carry a
  payload that describes *this* session's state. Delivered to a successor it is noise; the latter
  two fire at *wedged* sessions — precisely the ones most likely to die undrained — so a uniform
  flip maximizes the chance a stale diagnostic reaches an innocent successor. Each site passes an
  explicit `room_id=None` with the reason inline.
  This **reverses** the earlier D1, which put a Room leg on `peek_steering_sender` to repair a
  fail-open guard that the uniform flip itself created. With the drafter push staying legacy there
  is no fail-open: the guard reads the one leg the message is on. `peek_steering_sender`,
  `agent/output_handler.py:1160-1162` and the 11 patch sites in `tests/unit/test_output_handler.py`
  are all unmodified, and that is checked as an anti-criterion rather than assumed. Consequences of
  ever revisiting this are recorded in Rabbit Holes (the peek Room leg **and** the session-keyed
  attempt budget at `agent/steering.py:262` would have to move together).
- **D2 — same-Room cross-delivery of *instructions* via the shared `system` addressee is accepted
  for the soak.** It is not a defect of this change; it is the Room durability model working as
  designed (see Risk 2), and a misrouted instruction is recoverable by re-steering. It is
  documented in `docs/features/session-steering.md` as behavior, not as a caveat. Consumer-side
  `target_agent` filtering stays a No-Go, filed as its own issue only if a real mis-delivery is
  observed. **This acceptance covers instructions only** — the two cases where it does not hold are
  carved out by D4 (destructive) and D5 (stale).
- **D3 — the merge hold is mechanical, and the run has a defined exit.** A `[ORDERED]` No-Go in a
  plan document has no effect on `/do-merge`, which sees a review-clean PR and merges it. Three
  parts, in order of force:
  1. **The PR is opened with `gh pr create --draft`.** This is the actual gate:
     `tools/merge_predicate.py:465-466` fails any PR whose `mergeStateStatus` is not
     `CLEAN`/`UNSTABLE`, and GitHub reports `DRAFT`, so `/do-merge` refuses fail-closed.
  2. **The PR carries the `hold` label**, applied with `gh pr edit <N> --add-label hold`. This is
     the human-visible signal only — no code reads PR labels
     (`grep -rn 'label' tools/merge_predicate.py` returns nothing). The label is `hold`, which is
     what exists in this repo (`gh label list` → `hold #c73f67`); there is **no** `do-not-merge`
     label, so that name would have been applied to nothing and silently no-opped.
  3. **MERGE is never dispatched.** This is the pipeline exit, and it is the part a mechanical hold
     needs or the router loops `/do-merge` against a draft forever. MERGE **cannot** be recorded
     `skipped` — `agent/pipeline_state.SKIPPABLE_STAGES` permanently excludes REVIEW, DOCS and
     MERGE and `tools/sdlc_stage_marker.py:322-330` refuses the write with `STAGE_NOT_SKIPPABLE`,
     because each is a gate the merge predicate reads. So the marker stays at `pending` (never
     `in_progress`, which is the wedge), the run closes after DOCS, and the remaining action is
     carried by a follow-up operator issue rather than by a stage marker. Task 8 spells this out.

  The PR body states the removal condition verbatim: *"Do not mark ready for review or merge until
  fleet-wide `/update` past PR #2622 is confirmed. Removal owner: Valor Engels (repo operator)."*
- **D4 — an abort never targets a Room key**, whether `is_abort` was passed explicitly or inferred
  by the `ABORT_KEYWORDS` auto-detect. "You MUST stop immediately" is destructive and
  non-idempotent: misrouted, it kills a session that was never targeted while the intended target
  runs on. That is a wrong *action*, not the recoverable wrong-recipient hazard D2 accepts.
  Stranding an abort with its dead session is the correct failure mode. The concrete trigger is
  `bridge/telegram_bridge.py:978`, which auto-sets `is_abort` from `{stop, cancel, abort,
  nevermind}` — a user typing "stop" after their session finished would otherwise leave a permanent
  abort on that chat's Room key that kills the next session opened there.
  **Implementation gotcha:** the auto-detect currently runs *after* `key = _queue_key(session_id)`,
  so key selection must move below it or the abort branch reads a stale `is_abort`. Task 1 requires
  the whole selection to be written as one expression in one place for exactly this reason.
- **D5 — the Room leg is age-bounded at drain time.** Nothing else bounds it: `agent/steering.py`
  documents "TTL: None", `clear_steering_queue` has zero production callers, and no drain path
  filters on `timestamp` (spike-6). Post-flip an undrained steer would be immortal, and "delivered
  to a session that did not exist at write time" is the temporal twin of the cross-delivery hazard
  D2 accepts on the concurrent axis — except unbounded in time, which is not the same trade.
  `_drain_list` drops **Room-key** entries older than `STEERING_ROOM_MAX_AGE_SECONDS`, logging each
  drop with the key and the age so a missing steer stays diagnosable. The **legacy key is never
  filtered**, so every message that exists today behaves exactly as it does today. The bound is a
  named env-overridable `TimeoutSettings` field, not a literal; the default is provisional and
  tunable. Drain-time filtering, not a background reaper — see Rabbit Holes.

  **The bound applies to every Room-leg reader, not just the destructive one.** `agent/steering.py`
  has three Room-aware read paths and they must agree, or the operator surface contradicts the
  drain:

  | Reader | Room-leg mechanism | Treatment |
  |---|---|---|
  | `pop_all_steering_messages` (138) | `_drain_list` | filtered — this is the drain |
  | `peek_steering_messages` (208) | `_peek_list` (LRANGE) | **filtered too** — `_peek_list` gains the same `max_age_seconds`, applied on the Room key only. Non-destructive: it *skips* stale entries, it does not delete them (the next drain does that). |
  | `has_steering_messages` (200) | raw `llen` on both legs | Room leg switches to `bool(_peek_list(room_key, max_age_seconds=...))`; the legacy leg keeps its `llen` fast path |

  `peek_steering_messages` is what `valor-session status` calls
  (`tools/valor_session.py:1031`) — the one operator-visible surface this plan pins as must-keep-
  working. Leaving it unfiltered would make `status` report pending Room steers that the very next
  drain silently discards, which is the opposite of "a missing steer stays diagnosable". Note the
  anti-criterion is on the **call site** at `tools/valor_session.py:1031` (it already passes a
  `room_id` and needs no edit), not on `peek_steering_messages` itself.

  **`pop_steering_message` (164) is explicitly out of scope.** It does not call `_drain_list` — it
  inlines its own `r.lpop` + `json.loads` loop — and it has **zero production callers**: a repo-wide
  grep excluding `tests/` returns only its own definition and the `agent/__init__.py` re-export at
  lines 41 and 73. Refactoring it through `_drain_list` to give it the bound would be unasked scope,
  so it keeps its current behavior and gains no parameter. **`peek_steering_sender` (223) also gains
  nothing** — it reads the legacy leg only (D1), which is never filtered.

### Key Elements

- **Room derivation at the call site, never inside the writer.** `push_steering_message` gains an
  optional `room_id` and does nothing but choose a key from it. It performs no lookup. This is
  the single most important shape decision in the plan: an internal
  `AgentSession.query.filter(session_id=...)` resolution measures ~2.4s per call (spike-5) on a
  path that includes the inbound-Telegram fast path.
- **The flip is selective, and the boundary is the design.** Six conversation-level sites derive a
  Room; six deliberately pass `room_id=None` — three session-scoped diagnostics (D1), one
  unconditional abort (D4), one site that holds no session row at all
  (`bridge/telegram_bridge.py:2070`), and the ORM-free migration script. Every non-test caller
  passes the keyword **explicitly**, with the reason inline where it is `None`. Uniformity is not
  the goal; delivering each message to a session for which it is meaningful is.
- **`room_id_for_session(session)` is free** (pure attribute reads, `models/room.py:65-100`), which
  is what makes caller-side derivation viable at all.
- **Derive only from a row known to be live.** Where the caller picked from a multi-row
  `filter(session_id=...)`, it sorts newest-first by `created_at` before selecting; where it holds
  no row, it passes `None` rather than fabricating one. Race 4 has the five sites and the measured
  evidence that this is a *fix to pre-existing code*, in scope because this plan is what makes a
  wrong row selection load-bearing.
- **No `room_id` → legacy key; abort → legacy key regardless.** Both are structural, not
  defensive: one expression below the auto-detect,
  `_room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`. No message
  is ever dropped for lack of a Room, and every existing test (which passes no `room_id`) is
  unaffected.
- **The Room leg is age-bounded; the legacy leg is not.** D5's `max_age_seconds` on `_drain_list`
  is passed only for the Room key, so today's behavior on the legacy key is bit-for-bit preserved.
- **`peek_steering_sender` is untouched.** The earlier draft's sentinel-preferring dual read is
  out: the drafter's own push stays on the legacy key, so the guard cannot fail open (D1). The
  plan checks "unmodified" as an anti-criterion rather than asserting it.
- **Docstring truth-up across three modules.** `agent/steering.py` (module docstring and
  `_room_queue_key`), `agent/health_check.py:511-513`, and
  `agent/session_runner/runner.py:589-591` each currently assert that writers do not target the
  Room key. After this change all four such claims are false — and the replacement text must state
  the *selective* status quo, since an unqualified "writers now target the Room key" is a new
  falsehood.

### Flow

**Durable branch** (a conversation-level, non-abort steer): supervisor sends a steer → caller sorts
its candidate rows newest-first and derives `room_id_for_session(<live row>)` →
`push_steering_message(session_id, ..., room_id=...)` → key selection sees a truthy `room_id` and
`is_abort` false → message lands on `steering:room:{project|addressee}` → target session dies
before its next turn boundary → a new session opens in the same Room → its turn-boundary drain
`_default_steering_pop` derives the same Room and reads the Room leg → the entry is within
`STEERING_ROOM_MAX_AGE_SECONDS` → **the steer is delivered**. That last hop is the durability
property, and the end-to-end test drives it through `_default_steering_pop` rather than
re-implementing the derivation (see Failure Path Test Strategy).

**Legacy branches**, all landing on `steering:{session_id}` and drained by the same dual-read
consumers exactly as today:

- the caller has no session row, or the session has no `project_key`, so `room_id` is `None`;
- the message is an abort, explicit or auto-detected (D4) — it dies with its target, by design;
- the write is a session-scoped diagnostic that passes `room_id=None` on purpose (D1).

**Expiry branch:** a Room-key entry no session drained inside the age bound is dropped at the next
drain with an `info` log naming the key and the age (D5), rather than waiting immortally for a
session that may be days newer than the instruction.

### Technical Approach

- **`agent/steering.py::push_steering_message`** — add `room_id: str | None = None`. **Delete**
  `key = _queue_key(session_id)` from its current position above the `ABORT_KEYWORDS` auto-detect
  and re-introduce it **below** that block as one expression:
  `key = _room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`.
  Writing it as two separate patches (one for `room_id`, one for `is_abort`) is how the ordering
  bug gets reintroduced. Nothing else in the function changes; the payload dict, the RPUSH/LPUSH
  split, and the existing `logger.info` (which interpolates `key`, so it self-describes which leg
  was written) are untouched. **No model import and no exception handler** — there is nothing that
  can raise.
- **`agent/steering.py::_drain_list` and `::_peek_list`** — both gain
  `max_age_seconds: float | None = None` (D5). When set, `_drain_list` **drops** entries whose
  payload `timestamp` is older than the bound (logging each drop at `info` with the key and the
  age); `_peek_list` **skips** them from its returned list without deleting anything.
  `pop_all_steering_messages` passes it to `_drain_list`, `peek_steering_messages` passes it to
  `_peek_list`, and `has_steering_messages` swaps its Room-leg `llen` for
  `bool(_peek_list(room_key, max_age_seconds=...))` — in all three cases **only** on the Room key,
  read from `TimeoutSettings.steering_room_max_age_seconds` rather than a literal so tests can vary
  it. **`pop_steering_message` is untouched**: it never calls `_drain_list` (it inlines its own
  `lpop` loop) and has zero production callers, so plumbing the bound into it is unasked scope.
  **`peek_steering_sender` is untouched**: it reads the legacy leg only, which is never filtered.

- **Flip table** — each site passes `room_id=room_id_for_session(<the session object in scope>)`:

  | File:line | Session object | Edit |
  |---|---|---|
  | `bridge/telegram_bridge.py:979` `_ack_steering_routed` | via 4 of 5 callers (1809, 1835, 1868, 2178) | add pass-through `room_id` param; each of those four derives from its **sorted** row |
  | `bridge/telegram_bridge.py:2617` | `session` (2593-2594) | sort before `[0]`, then pass `room_id` |
  | `bridge/telegram_bridge.py:2650` | `active_edit` (2641-2646) | sort `edit_sessions` before the `next(...)`, then pass `room_id` |
  | `tools/valor_session.py:856` | `session` | pass `room_id` (resume path only; the status peek at 1031 is untouched) |
  | `agent/session_executor.py:853` | `session` | pass `room_id` |
  | `agent/session_runner/runner.py:610` `_default_steering_push` | `self._agent_session` | pass `room_id`, mirroring `_default_steering_pop` at 599-601 — the read/write asymmetry there is the bug |

- **Legacy table** — each site passes an **explicit** `room_id=None` with a one-line comment naming
  the reason. The explicit keyword is what the AST census test checks; a bare omission fails it:

  | File:line | Why legacy | Decision |
  |---|---|---|
  | `agent/output_handler.py:1227` | drafter self-draft; its budget `steering:attempts:{session_id}` is session-keyed, so a Room-durable steer would escape it | D1 |
  | `agent/session_health.py:3467` | tool-timeout advisory naming the wedged tool; also the repo's only `front=True` push, which would LPUSH onto a shared list | D1 |
  | `monitoring/session_watchdog.py:557` `_inject_watchdog_steer` | loop-break steer naming the repeated tool; the helper gains **no** parameter | D1 |
  | `scripts/steer_child.py:119` | unconditionally `is_abort=True` (line 123); the writer's abort guard forces legacy anyway, but the explicit `None` makes the intent legible at the call site | D4 |
  | `bridge/telegram_bridge.py:2070` | holds **no** session row — 2058-2067 bind `guard_sessions` and test only truthiness. Do **not** invent `guard_sessions[0]` | Race 4 |
  | `scripts/migrate_steering_queue_drain.py:126` | **no change at all** — raw Redis hash scan, deliberately ORM-free. The census test's sole allowlist entry | — |

- **`agent/health_check.py::_repush_messages`** — add `room_id: str | None = None` and forward it
  to the `push_steering_message` call inside. `_handle_steering` passes its own already-resolved
  `room_id` at **all four** call sites: 530, 536, 560, 564. Two of those (536, 564) are retries
  inside `except` blocks; missing them silently demotes to legacy. The re-pushed siblings are
  non-abort by construction (the abort is consumed, not re-pushed), so this does not conflict with
  D4 — but do not "helpfully" re-push the abort.
- **`agent/steering.py::peek_steering_sender` and `agent/output_handler.py:1160-1162` — no change.**
  The drafter's push stays legacy (D1), so the single-leg `LINDEX` still observes it. This is
  checked as a diff anti-criterion.
- **`config/settings.py`** — one new `TimeoutSettings` field, `steering_room_max_age_seconds`, with
  the `.env.example` placeholder and its required comment line (D5).
- **Docstrings** — rewrite to the new *selective* status quo, no "formerly"/"used to" narration:
  `agent/steering.py:15-22` (module), `agent/steering.py:48-55` (`_room_queue_key`),
  `agent/health_check.py:511-513` (comment), `agent/session_runner/runner.py:589-591`
  (`_default_steering_pop`: "Writers are unchanged in this release."). Plus the `front` docstring at
  `agent/steering.py:96-100` and the new `max_age_seconds` parameter. An unqualified "writers now
  target the Room key" is a new falsehood — see the Documentation section.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `push_steering_message` introduces **no new exception handler**. Under the final design it
  performs no I/O beyond the RPUSH it already did, so there is nothing new that can raise. If the
  builder finds themselves adding a `try`/`except` to this function, the design has drifted back
  to internal resolution — stop and re-read spike-5.
- [ ] `agent/health_check.py:531-536` already has an `except Exception` around `_repush_messages`
  with a retry; the added `room_id` argument must be present on the retry call too (line 536, and
  the same pattern at 564), or the retry silently demotes to legacy. Pin with a test that forces
  the primary call to raise and asserts the retry still lands on the Room key.
- [ ] `room_id_for_session` is called at six new sites. It is total for any object (`getattr`-based,
  returns `None` without a `project_key`), so no caller needs a guard. Pin with a test passing an
  object with no `project_key` attribute at all.
- [ ] The age filter in **both** `_drain_list` and `_peek_list` must not raise on a malformed entry.
  A payload with a missing or non-numeric `timestamp` must be **kept**, not dropped/skipped and not
  crashed on — failing open loses nothing, while failing closed silently deletes steers. Pin with a
  Room-leg entry whose `timestamp` key is absent, through both functions.
- [ ] `.sort()` must be called on a materialized list, never on a `QueryBuilder`. The three bridge
  sites at 1801/1860/2169 have no enclosing `try`/`except`, so an `AttributeError` there is an
  unhandled crash on the inbound-Telegram steering path — the exact regression the round-3 blocker
  named. Pinned structurally by `test_room_derivation_sites_sort_before_selecting`'s materialization
  assertion (including its synthetic-negative self-check), because no runtime test in this repo
  drives those Telethon branches end to end.
- [ ] The `created_at or 0` sort key must not raise. `created_at` is a non-nullable `SortedField`
  (`models/agent_session.py:163`), so `or 0` never fires in practice; pin it anyway with a row
  whose `created_at` is `None` to prove the sort does not `TypeError` comparing `None` to float.

### The durability property (the reason this plan exists)

Every other test below asserts *which Redis list a write lands on*. That is a proxy. The
**binding** test asserts the behavior the feature is for: a steer written for session A is
delivered to a different session B serving the same Room.

- [ ] **`test_steer_survives_target_session_and_reaches_room_sibling`** — persist two
  `AgentSession` rows sharing `project_key="test-room-durability"` with `chat_id=None` on both, so
  `models/room.py:65-92` maps each to `SYSTEM_ADDRESSEE` and `room_id_for_session` returns an
  identical composite for both. Then:
  `push_steering_message(session_a.session_id, "do X", "Tom", room_id=room_id_for_session(session_a))`,
  finalize/delete session A, and assert the message comes back.
  **Read it back through the production path, not through `pop_all_steering_messages` directly.**
  Calling the drain helper with a test-computed `room_id` re-implements the production derivation
  inside the test, so it cannot catch a writer/reader derivation mismatch — the single most likely
  way this feature silently delivers nothing. Instead drive
  `agent/session_runner/runner.py::_default_steering_pop`, which derives the Room itself via
  `room_id_for_session(self._agent_session)`. It reads `self._agent_session` only via `getattr`, so
  the test binds a bare runner instance with `_agent_session` set to the persisted session B row —
  no harness spawn needed. Assert the returned list contains the steer pushed for session A.
  Tear down both rows via the ORM scoped by the `test-` project key (`instance.delete()`, never
  raw Redis).
- [ ] **Negative twin** — the same scenario with `room_id=None` on the push must NOT deliver to
  session B through `_default_steering_pop`. This is what proves the test is measuring the Room leg
  and not an artifact.
- [ ] **Staleness twin** — the same scenario with the pushed entry's `timestamp` backdated past
  `STEERING_ROOM_MAX_AGE_SECONDS` must NOT deliver (D5), and the drop must be logged.

### Empty/Invalid Input Handling

- [ ] `room_id=None` (the default, and what every existing test passes) → legacy key. Test it.
- [ ] `room_id=""` → falsy → legacy key. Test it.
- [ ] Session has no `project_key` → `room_id_for_session` returns `None` → caller passes `None` →
  legacy key. Test it (this is the issue's explicit acceptance criterion).
- [ ] `session_id=""` with a truthy `room_id` → the Room key is still the correct target; the
  legacy key would be the nonsensical `steering:`. Assert the Room key wins.
- [ ] `is_abort=True` with a truthy `room_id` → **legacy key** (D4). Test it.
- [ ] `text="stop"` with `is_abort` left at its default and a truthy `room_id` → the auto-detect
  sets `is_abort`, so → **legacy key**. This is the one that catches the ordering bug: it passes
  only if key selection sits below the `ABORT_KEYWORDS` block. Cover every keyword in the set.
- [ ] A Room-leg entry with a missing or non-numeric `timestamp` → kept, not dropped (fail open).

### Error State Rendering

- No user-visible output surface changes. The steering write is internal; its only rendering is the
  existing `logger.info` line, which already names the target key and therefore self-documents
  which leg was chosen.

## Test Impact

- [ ] `tests/integration/test_steering.py::TestRoomDualRead::test_handle_steering_drains_room_leg`
  (class at line 1678, method at line **1748**) — UPDATE: its docstring and final assertion both
  state the re-push targets the legacy list. Post-flip the re-push targets the Room key. Rewrite the
  assertion to drain the Room leg and correct the docstring. (An earlier draft cited class
  `TestSteeringDualRead` at line ~1746; that node id resolves to nothing.)
- [ ] `tests/integration/test_steering.py::TestAbortSiblingPreservation::test_abort_repushes_non_abort_siblings`
  (class at line 1765) — UPDATE: called with no `room_id`, so siblings still land on legacy and the
  test passes as-is. Add a **sibling case** with a `room_id` asserting Room-sourced siblings return
  to the Room key (the issue's explicit acceptance criterion). Note the *abort itself* must still
  land on legacy under D4 — assert that too, since it is the one place abort and Room meet.
- [ ] `tests/integration/test_steering.py` remaining ~64 tests — no change expected: none passes a
  `room_id` to `push_steering_message`, so every one keeps writing to the legacy key by
  construction, and the D5 age filter never fires on the legacy leg or inside a test's lifetime.
  If any turns red, the fallback branch is wrong — fix the code, not the test.
- [ ] `tests/unit/test_output_handler.py` (11 `peek_steering_sender` patch sites: 701, 740, 786,
  819, 843, 885, 922, 1112, 2528, 2565, 2600) — **no change**. `peek_steering_sender` keeps its
  signature and `agent/output_handler.py:1160-1162` keeps its call, because the drafter's push stays
  on the legacy key (D1). If a builder finds themselves editing these, the selective-flip boundary
  has been breached — stop and re-read D1.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` — no change: it AST-walks for
  `push_steering_message` call/dedup pairing, which the added keyword does not affect. It is also
  the pattern the new census test copies.
- [ ] Tests that exercise `bridge/telegram_bridge.py::_ack_steering_routed` — VERIFY: it gains an
  optional keyword, so positional callers are unaffected, but any `autospec` mock must be
  re-checked. `monitoring/session_watchdog.py::_inject_watchdog_steer` gains **no** parameter, so
  its tests are untouched.
- [ ] Tests that assert on `bridge/telegram_bridge.py` session selection at 1804, 1865, 2174, 2594,
  2642 — VERIFY: inserting a newest-first sort changes *which* row is selected when a superseded
  row exists. Any test relying on insertion order to pick a row is asserting the bug; update it to
  assert newest-first.
- [ ] `tests/unit/` config tests covering `TimeoutSettings` field coverage or `.env.example`
  completeness — VERIFY: a new field plus its placeholder must keep those green.
- [ ] New test additions go into `tests/integration/test_steering.py`: the durability property test
  driven through `_default_steering_pop` plus its negative and staleness twins, the superseded-row
  safety test, the legacy-fallback matrix, the abort matrix (explicit and every auto-detected
  keyword), the Room-write happy path, sibling re-push on all four `_repush_messages` paths
  including the two retries, payload-shape invariance, and the `_drain_list` age-filter cases
  (expired dropped, fresh kept, malformed kept). The two AST census tests
  (`test_every_flipped_writer_passes_room_id`, `test_room_derivation_sites_sort_before_selecting`)
  also live in `tests/integration/test_steering.py` — that is where the Verification rows anchor
  them by node id — and copy the walk pattern from `tests/unit/test_bridge_dispatch_contract.py`.

## Rabbit Holes

- **Building a provenance-tagging mechanism.** spike-2 proved it unnecessary. Threading the
  already-resolved `room_id` into `_repush_messages` satisfies the acceptance criterion. Do not
  invent a `_source` marker, do not change `pop_all_steering_messages`' return shape, do not touch
  the five consumers' unpacking.
- **Resolving the session inside `push_steering_message`.** Measured at ~2.4s per call (spike-5)
  because `session_id` is an unindexed `Field()`. Do not add it "just as a fallback for callers
  that pass nothing" — the fallback would fire on the Telegram fast path.
- **Caching a session→Room lookup.** Follows from the above: there is no lookup to cache.
- **Indexing `AgentSession.session_id`.** Real problem (299 `filter(session_id=...)` scans
  repo-wide), wrong plan. `session_id` is near-unique, so `IndexedField` would create one Redis
  Set per session — the exact anti-pattern `models/agent_session.py:329` records — and would
  require a `rebuild_indexes()` migration registered in `MIGRATIONS`. File it separately if it
  matters; it is not this release.
- **Solving same-Room cross-delivery for non-abort instructions.** See Risk 2. The `target_agent`
  field exists and is the natural gate, but no consumer filters on it today. Wiring that filter is
  its own release. (Cross-delivery of *aborts* is not deferred — it is prevented by D4.)
- **Giving `peek_steering_sender` a Room leg.** It was in scope in an earlier draft and is now
  explicitly out (D1). If the drafter self-draft push is ever flipped to the Room key, this becomes
  necessary again *and* the session-keyed attempt budget at `agent/steering.py:262` must move to
  the Room at the same time. Neither is this release.
- **A background reaper for stale Room keys.** D5 filters at drain time, which is O(1) extra work
  on a path already reading every entry. A scheduled sweeper would be a new service, a new failure
  mode, and would delete messages no one asked about. Not now.
- **"Finishing" the flip by making the six exception sites uniform.** Every one of them has a
  reason recorded at the call site and in the Problem section's table. Uniformity is not the goal;
  delivering each message to a session for which it is meaningful is.
- **Retiring the legacy read leg.** That is phase 3 of the plan's cutover checklist and a separate
  release. Every consumer keeps its legacy leg here.
- **Draining or migrating existing legacy-key contents.** The plan explicitly forbids an
  LRANGE+RPUSH copy script (`docs/plans/durability-room-job-agentrun.md:318`) — it produces
  duplicate steers. Old messages drain naturally through the untouched dual-read.

## Risks

### Risk 1: A machine still running pre-#2622 code never sees Room-key steers

**Impact:** On such a machine there is no dual-read consumer, so a steer written to
`steering:room:{room_id}` sits on a key nothing drains. Silent total steering loss for that machine
— no error, no log, just an instruction that never arrives.
**Mitigation:** The deploy gate, made mechanical (decision D3). The PR is driven to review-clean but
**opened as a draft** and labelled `hold` from the moment it exists, with the removal condition and
its named owner in the body. A prose No-Go alone does not stop `/do-merge`; draft state does, because
GitHub itself rejects the merge. This is not machine-verifiable from inside a single checkout, which
is exactly why it needs a gate on the PR rather than a check in the suite.

### Risk 2: The `system` addressee collapses every chatless session of a project into one Room

**Impact:** `models/room.py:66-92` maps a `chat_id` of `None`, `"0"`, or any non-numeric non-email
value to `SYSTEM_ADDRESSEE`. So all reflection, watchdog-target, and synthetic sessions in a project
share `{project}|system`. Post-flip, a steer aimed at system-session A sits on that shared key and
is drained by whichever system session next reaches a turn boundary — possibly session B.
**Mitigation:** For *conversation-level instructions* this is inherent to the Room durability model
— outliving the target session is the *point*, and PR #2627 already established the system Room as
a legitimate write target. Accepted, not fixed, in this release; documented explicitly in
`docs/features/session-steering.md` so the next person debugging a mis-delivered steer finds the
answer. The `target_agent` field is the designed gate if this proves harmful; wiring a
consumer-side filter on it is a separate issue, filed only if a real mis-delivery is observed
during the soak.

**What is NOT accepted, and is fixed here.** The exposure is materially smaller than the previous
draft's, because the writers most likely to land a *harmful* payload in the shared system Room are
no longer flipped: `monitoring/session_watchdog.py:557` and `agent/session_health.py:3467` both
fire at *wedged* sessions — the ones most likely to die before draining — and both carry a
diagnostic naming a specific tool in a specific session. Delivered to an innocent successor that is
misinformation, not steering. Both now pass `room_id=None` (D1). Aborts are excluded outright (D4),
and unbounded staleness is bounded (D5).

### Risk 3: `front=True` priority silently inverts across legs

**Impact:** `front=True` LPUSHes so the message is drained *next*, ahead of anything queued
(`agent/steering.py:96-100`). Consumers drain **legacy first**, then Room
(`agent/steering.py:158-160`). So a `front=True` push onto the Room leg is not "next" at all —
every legacy-leg message is consumed ahead of it. Cutover is exactly when legacy residue exists,
so the inversion would be worst at the moment it first mattered.
**Mitigation:** Structurally impossible in the shipped design. The repo's only `front=True` push is
`agent/session_health.py:3473` (the tool-timeout advisory), and that writer passes `room_id=None`
(D1), so `front` remains a within-legacy-leg contract exactly as documented. No new `front=True`
push may target a Room without first resolving the cross-leg ordering question; that constraint is
recorded in the `front` docstring as part of Task 5. A Verification anti-criterion asserts
`session_health.py` gains no `room_id_for_session` import.

### Risk 4: A session lookup lands on a latency-sensitive write path

**Impact:** `AgentSession.query.filter(session_id=...)` is an unindexed full scan measuring
~2.4s per call (spike-5). Any design that puts it inside `push_steering_message` regresses
`bridge/telegram_bridge.py:979` (inbound Telegram fast path) and `_requeue_pending_steers` (once
per leftover message) by seconds.
**Mitigation:** Eliminated by design, not mitigated: the writer performs no lookup. `room_id` is
always supplied by a caller that already holds the session, derived via the zero-I/O
`room_id_for_session`. The mechanical guard is the pair of Verification rows
`grep -c "query.filter" agent/steering.py == 0` and the `sed -n '/^def push_steering_message/,/^def
pop_all_steering_messages/p' … | grep -c "except" == 0` — an internal lookup cannot be added
without one or the other tripping. The `sed` form is load-bearing: the earlier `awk` range
collapsed to the signature line and passed unconditionally, so this risk's only mechanical guard
was vacuous until round 2.

## Race Conditions

### Race 1: Two-phase deploy of the read and write halves

**Location:** Fleet-wide; `agent/steering.py` writer vs. all five consumers.
**Trigger:** A steer written to the Room key by an updated machine while a worker on pre-#2622 code
is the only drainer for that session.
**Data prerequisite:** The dual-read consumer code must be resident on **every** machine before any
machine writes to the Room key.
**State prerequisite:** Fleet-wide `/update` past PR #2622.
**Mitigation:** The deploy gate. Merge is held on operator confirmation. This is exactly the Race 1
mitigation named at `docs/plans/durability-room-job-agentrun.md:318`, now at its second phase.

### Race 2: Same-Room concurrent drain

**Location:** `agent/steering.py:158-160` drain vs. `push_steering_message` write, across two
sessions sharing a `room_id`.
**Trigger:** Sessions A and B both live in `{project}|system`; a steer intended for A is drained by
B's turn boundary first.
**Data prerequisite:** None — both sessions legitimately serve the Room.
**State prerequisite:** None.
**Mitigation:** Accepted for *instructions* (see Risk 2), not prevented. The drain is LPOP-based so
the message is delivered exactly once, never duplicated — for an instruction the hazard is *which*
session receives it, not loss or double-delivery, and a misrouted instruction is recoverable by
re-steering.
**Where that reasoning does NOT hold, and what covers it:** an **abort** is destructive and
non-idempotent. Misrouted, it kills a session that was never targeted while the intended target
runs on — not a recoverable "wrong recipient" but a wrong *action*. Aborts are therefore excluded
from the Room leg entirely (D4), so this race cannot arise for them. A **stale** message is the
temporal twin of the same problem: it is drained by a session that may not exist yet at write time.
That is bounded by D5's Room-leg age filter.

### Race 3: Session `project_key` assigned between two pushes

**Location:** The caller-side `room_id_for_session(session)` derivation.
**Trigger:** Push 1 happens while the caller's in-memory session still has no `project_key`
(derives `None` → legacy); push 2 happens after it is set (derives a Room). The two messages split
across legs.
**Data prerequisite:** None.
**State prerequisite:** None.
**Mitigation:** Harmless by construction — consumers dual-read both legs and drain legacy first, so
FIFO order across the split is preserved for the common case and neither message is lost.

### Race 4: A superseded session row derives a different Room than the live one

**Location:** Caller-side derivation, wherever the caller's session object is stale.
**Trigger:** `models/agent_session.py:130` documents `superseded` as "Replaced by a newer session
for the same `session_id`". A superseded row can carry a different `chat_id` (→ a different
addressee → a Room the live session never drains) or no `project_key` (→ silent legacy fallback
that looks like a Room-less session but is a bug).
**Data prerequisite:** The caller must derive from a row that reflects the live session.
**State prerequisite:** None.
**Mitigation (corrected at round 2 — the previous wording asserted a fact that is false).**
The previous draft claimed "Where a caller does re-fetch (`bridge/telegram_bridge.py:2593-2594`),
it already uses the repo's newest-by-`created_at` shape." **It does not.** Measured on the baseline:

```python
sessions = list(AgentSession.query.filter(session_id=session_id))   # 2593
session = sessions[0] if sessions else None                          # 2594
```

No sort key. Nor does the `:2650` push's source — `active_edit` comes from an unsorted
`next((s for s in edit_sessions if s.status in (...)), None)` at 2641-2646. Three more
`_ack_steering_routed` callers have the same shape: `matching_session = sessions[0]` (1804),
`live_guard = _live[0]` (1865), `fresh_session = sessions[0]` (2174). So the round-1 unsorted-`[0]`
blocker was **relocated into the callers, not dissolved** — the design moved the query out of the
writer and into exactly the sites it now derives Rooms from.

**Real mitigation, in three parts:**

1. **Materialize, then sort, at all five sites.** `AgentSession.query.filter(...)` does **not**
   return a list — it returns a `popoto.models.query.QueryBuilder`, which supports `__getitem__`
   (hence the working `[0]`) but has **no `.sort` attribute**. Measured on the baseline:
   `type(AgentSession.query.filter(session_id='x'))` is `<class 'popoto.models.query.QueryBuilder'>`
   and `hasattr(q, 'sort')` is `False`. Three of the five sites bind the bare QueryBuilder —
   `sessions` at **1801**, `_live` at **1860**, `sessions` at **2169** — so a bare
   `sessions.sort(...)` there raises `AttributeError`. Only 2593 and 2641 already wrap in `list(...)`.

   So the edit is two steps, in this order, at each site:

   ```python
   sessions = list(AgentSession.query.filter(session_id=session_id, status=check_status))  # 1801
   sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
   matching_session = sessions[0]                                                          # 1803
   ```

   matching the ten existing uses of the `created_at or 0` idiom in this repo. This is a *fix to
   pre-existing code*, in scope precisely because this plan is what makes a wrong row selection
   load-bearing. `created_at` is a non-nullable `SortedField` (`models/agent_session.py:163`), so
   `or 0` is belt-and-braces and never compares `None`.

   **Why the `list(...)` is not cosmetic.** At 1801, 1860 and 2169 the selection sits in the
   inbound-Telegram steering path with **no enclosing `try`/`except`** around the query, so an
   `AttributeError` propagates straight into the Telethon handler and the steer is lost with a
   traceback. At 2593 it would be *worse than a crash*: the query sits inside `try:` with
   `except Exception` at 2595 setting `session = None`, so a missing `list(...)` there would be
   swallowed and silently disable edit-steering entirely. Neither failure is caught by a
   text-matching guard, which is why the sort census test below asserts the **materialization**,
   not the presence of a sort call.
2. **Pass `room_id=None` where the live row cannot be identified.** `bridge/telegram_bridge.py:2070`
   holds no session row at all (2058-2067 bind `guard_sessions` and test only truthiness). A builder
   working the caller table mechanically would invent `guard_sessions[0]` there and reintroduce the
   defect. The rule is explicit in Task 2: **no session object in hand → `room_id=None`, never a
   fabricated selection.**
3. **The anti-criterion must be positive, not a diff grep.** `git diff origin/main | grep -c
   "^+.*filter(session_id=" == 0` cannot catch any of this — every offending query already exists on
   `main`, so no `+` line appears. It is replaced by **two** positive Verification rows —
   `created_at or 0` occurrences in `bridge/telegram_bridge.py` (baseline 0, expected ≥ 5) *and*
   `list(AgentSession.query.filter` occurrences (baseline **3**, expected ≥ 6) — plus the AST test
   `test_room_derivation_sites_sort_before_selecting`, which asserts materialization rather than
   text-matching a sort call. The `created_at or 0` row alone is insufficient: it matches on code
   that raises `AttributeError` at runtime. The success criterion is restated as "no caller
   *derives a Room from* an unsorted row selection", not "introduces one".

Pinned additionally by a runtime test persisting a superseded row and a live row sharing one
`session_id` with different `chat_id`s, and asserting the steer lands on the live session's Room.

## No-Gos (Out of Scope)

- [ORDERED] **Merging this PR.** Blocked on a human-gated event: operator confirmation that every
  machine in the fleet has run `/update` past PR #2622. Drive to review-clean, then hold.
  **Enforcement, stated identically in D3, Task 8 and Verification: the PR is opened with
  `gh pr create --draft` — that is the gate, because `tools/merge_predicate.py:465-466` fails any
  PR whose `mergeStateStatus` is not `CLEAN`/`UNSTABLE` and GitHub reports `DRAFT`, so `/do-merge`
  refuses fail-closed. `gh pr edit <N> --add-label hold` is applied as the human-visible signal
  only; no code reads PR labels.** This bullet is documentation of that gate, not the gate itself.
  The MERGE stage is recorded as deliberately not dispatched so the pipeline has a terminal state
  (D3). Removal owner: Valor Engels (repo operator). The PR body must carry the removal condition
  verbatim.
- **Indexing `AgentSession.session_id`.** The repo-wide unindexed-scan problem (299 call sites,
  ~2.4s each) is real and out of scope here. This plan neither adds a scan nor fixes the existing
  ones. File separately if it becomes a priority.
- **Teaching `agent/sdlc_router.py` a `held` run state.** The router has no concept of one
  (`grep -n "hold"` returns a single unrelated docstring hit at line 342), and MERGE cannot be
  recorded `skipped` because `SKIPPABLE_STAGES` permanently excludes it. This release works around
  that by never dispatching MERGE and carrying the remaining action on a follow-up issue (D3). A
  first-class held state would be a genuine improvement to the pipeline and is worth its own issue,
  but building it is not a prerequisite for shipping a steering-key change.
- [SEPARATE-SLUG #2494] **Phase 2 — making the durable Room-inbox append authoritative for
  dispatch.** Tracked under the parent durability issue and separately gated on a soak period of
  error-free shadow appends.
- [SEPARATE-SLUG #2494] **Phase 3 — retiring the legacy read leg from the five consumers and the
  status peek.** The parent plan's cutover checklist sequences this as a later release; messages
  already sitting on legacy lists at deploy time must keep draining.
- [SEPARATE-SLUG #2494] **Consumer-side `target_agent` filtering to prevent same-Room
  cross-delivery.** Documented as accepted behavior here; only worth building if a real
  mis-delivery is observed during the soak.

## Update System

No update system changes required. This change adds no dependency, no config file, and no schema.
It is a three-branch key selection inside `agent/steering.py`, `room_id` arguments at six call
sites plus six explicit `room_id=None` sites, a `created_at` sort at five pre-existing row
selections, and a Room-leg age filter — all propagated by the ordinary `/update` git sync.
**The `/update` run itself is the deploy gate's subject, not its target** — the fleet must already
be past #2622 *before* this merges, which is an ordering constraint on merge, not a change to the
update process.

One new **optional** env key: `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS`, backing
`TimeoutSettings.steering_room_max_age_seconds` (default 21600). It needs no vault entry and no
`.env.example` line change is *required* for correctness — the default is the intended production
value — but add the `.env.example` placeholder with its explanatory comment line anyway, per the
repo's completeness check.

No Popoto schema migration is required: no model gains, loses, or changes a field, and no field's
`indexed` flag changes. `Room` and `AgentSession` are read-only inputs here. (This is a direct
consequence of the decision *not* to index `session_id` — see Rabbit Holes. Had the plan taken the
indexing route, this section would instead need a `rebuild_indexes()` migration registered in
`MIGRATIONS`.)

## Agent Integration

No agent integration required — this is an internal change to the steering transport. No new CLI
entry point in `pyproject.toml [project.scripts]`, no MCP surface. The bridge already imports
`push_steering_message` (`bridge/telegram_bridge.py:87`); it additionally imports
`models.room.room_id_for_session` to derive the Room at its flipped call sites, which is an
internal import, not a new agent surface.

The one operator-visible surface that must keep working is `valor-session status`, whose pending-
steering peek (`tools/valor_session.py:1031`) already passes a `room_id`. **That call site is
untouched** and its continued correctness is asserted in Verification. Note this is a *different*
function in the same file from the resume steer at line 856, which does change — the anti-criterion
is scoped to the peek call, not to the whole file.

The *callee* it reaches, `agent/steering.py::peek_steering_messages`, does change: it applies D5's
Room-leg age filter so `status` no longer reports steers the next drain will silently discard. That
is a behavior improvement to this surface, delivered with no edit at the call site — which is
exactly why the anti-criterion is on `tools/valor_session.py:1031` and not on the steering function.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-steering.md` — replace the dual-read/writers-unchanged
  description with the new status quo, which is *selective*: conversation-level writes target the
  Room key; aborts and session-scoped diagnostics stay on the legacy key; legacy is also the
  fallback for Room-less sessions and remains fully drained. Reproduce the Problem section's
  three-class table verbatim — "writers now target the Room key" is a new falsehood if written
  unqualified. Add a subsection documenting the same-Room delivery semantics from Risk 2 (a steer
  may be served to a different session in the same Room; the `system` addressee groups all chatless
  sessions of a project) and the Room-leg age bound from D5, naming
  `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS` and its default.
- [ ] Update `docs/plans/durability-room-job-agentrun.md` **line 673** (an earlier draft cited
  "~655") — move "the steering writer flip" out of the "Remaining, each its own release" list and
  record it as shipped *with its selective boundary*, leaving phase 2 and phase 3 as the remainder.
  Plan-doc edits commit on main.
- [ ] Add `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS` to
  `docs/features/config-timeout-catalog.md`'s field catalog.
- [ ] No `docs/features/README.md` index change — `session-steering.md` is already indexed.

### Inline Documentation

- [ ] Rewrite `agent/steering.py` module docstring (lines 15-22) to the new status quo.
- [ ] Rewrite `_room_queue_key` docstring (lines 48-55) — drop "No writer targets this key yet".
- [ ] Rewrite the `agent/health_check.py:511-513` comment — drop "Writers are unchanged — the
  re-pushes below always target the legacy list".
- [ ] Rewrite the `agent/session_runner/runner.py:589-591` `_default_steering_pop` docstring —
  drop "Writers are unchanged in this release."
- [ ] Docstring the `room_id` parameter on the three changed signatures only —
  `push_steering_message`, `agent/health_check.py::_repush_messages`, and
  `bridge/telegram_bridge.py::_ack_steering_routed` — including the "no `room_id` → legacy key"
  contract, the "abort → legacy key regardless" contract, and the note that the caller derives it
  via `room_id_for_session` because the writer deliberately does not look sessions up.
  (`peek_steering_sender` and `_inject_watchdog_steer` are not changed and get no such parameter.)
- [ ] Amend the `front` docstring at `agent/steering.py:96-100` to state that `front` orders within
  the legacy leg and that consumers drain legacy before Room, so a future `front=True` push must
  not target a Room without resolving cross-leg ordering (Risk 3).
- [ ] Docstring the `max_age_seconds` parameter on **both** `_drain_list` (drops) and `_peek_list`
  (skips, never deletes), and state at each of the three Room-leg call sites —
  `pop_all_steering_messages`, `peek_steering_messages`, `has_steering_messages` — why it is passed
  for the Room key and never for the legacy key (D5). Note in `pop_steering_message`'s docstring
  that it is deliberately unbounded because it has no production callers, so a future caller knows
  to route through `pop_all_steering_messages` instead.

## Success Criteria

- [ ] **The durability property holds end to end:** a steer written for session A is delivered to
  a different session B serving the same Room, after A is gone. Asserted by
  `test_steer_survives_target_session_and_reaches_room_sibling`, with a `room_id=None` negative
  twin proving the test measures the Room leg.
- [ ] `push_steering_message` targets `steering:room:{room_id}` when the caller supplies a truthy
  `room_id` **and the message is not an abort**, and `steering:{session_id}` in every other case.
  **Every non-test caller passes `room_id` explicitly** — every *conversation-level* caller passes
  a derived Room. **Five** documented exceptions pass an explicit `room_id=None` with the reason
  inline: `agent/output_handler.py:1227`, `agent/session_health.py:3467`,
  `monitoring/session_watchdog.py:557`, `scripts/steer_child.py:119`,
  `bridge/telegram_bridge.py:2070`. A **sixth** site, `scripts/migrate_steering_queue_drain.py:126`,
  is **not modified at all** — it is ORM-free by design and is the census allowlist's sole entry.
  (Five explicit `None`s is what the `>= 5` Verification row expects.)
- [ ] **No abort ever lands on a Room key**, whether `is_abort` was passed explicitly or set by the
  `ABORT_KEYWORDS` auto-detect. The key selection sits *below* the auto-detect block.
- [ ] **The Room leg is age-bounded, consistently across every Room-leg reader.** `_drain_list`
  discards Room-key entries older than `STEERING_ROOM_MAX_AGE_SECONDS`, and `_peek_list` skips them,
  so `pop_all_steering_messages`, `peek_steering_messages` and `has_steering_messages` agree. In
  particular the `valor-session status` peek does not advertise steers the next drain will discard.
  The legacy leg is never filtered anywhere. The bound is a named env-overridable setting, not a
  literal. `pop_steering_message` and `peek_steering_sender` are deliberately untouched (zero
  production callers; legacy leg only).
- [ ] `push_steering_message` performs no ORM query and adds no exception handler. A steering
  write costs the same Redis round trips it costs today.
- [ ] **No caller derives a Room from an unsorted row selection, and no sort is applied to an
  unmaterialized query.** All five multi-row selections in `bridge/telegram_bridge.py` (1803, 1864,
  2174, 2594, 2642) sort newest-first by `created_at` before selecting, and the three whose queries
  are not already wrapped (1801, 1860, 2169) gain a `list(...)` first — `query.filter(...)` returns
  a `QueryBuilder` with no `.sort`, so a sort without materialization is an `AttributeError` on the
  inbound-Telegram fast path. `bridge/telegram_bridge.py:2070` — which holds no row — passes
  `room_id=None` rather than fabricating one.
- [ ] **The invariant is machine-checked, and the check is not scoped to a fixed module list.** A
  census test discovers `push_steering_message` call sites by walking the repo's non-test Python
  files and fails if any call omits an explicit `room_id`, with exactly one allowlisted path. A
  writer added in a new module later is caught by the same test.
- [ ] `_repush_messages` receives and forwards `_handle_steering`'s resolved `room_id` at **all
  four** call sites (530, 536, 560, 564) — including the two retries inside `except` blocks.
- [ ] No provenance tag appears in the JSON payload persisted to Redis (the payload dict is
  byte-identical to today's).
- [ ] **`peek_steering_sender` is unmodified and `agent/output_handler.py:1160-1162` is
  unmodified** (verifiable from the diff). The drafter self-draft guard keeps observing exactly the
  messages it observes today because the drafter's own push stays on the legacy key.
- [ ] The five dual-read drain consumers are unmodified, and the `valor-session status` peek call
  at `tools/valor_session.py:1031` is unmodified (verifiable from the diff).
- [ ] `agent/session_health.py` and `monitoring/session_watchdog.py` gain **no**
  `room_id_for_session` import — the diagnostic writers stay legacy, which is also what keeps the
  `front=True` priority contract from inverting.
- [ ] The steering suite is green via `scripts/pytest-clean.sh tests/integration/test_steering.py`.
- [ ] All four stale "writers are unchanged" claims removed (`agent/steering.py` module docstring
  and `_room_queue_key`, `agent/health_check.py:511-513`,
  `agent/session_runner/runner.py:589-591`); no "formerly"/"used to" narration.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `python -m ruff check` and `python -m ruff format` clean.
- [ ] PR is review-clean and **held unmerged** pending the fleet deploy gate — opened with
  `gh pr create --draft` (the enforcing gate: `tools/merge_predicate.py:465-466` fails a `DRAFT`
  `mergeStateStatus`, so `/do-merge` refuses fail-closed) and labelled `hold` via
  `gh pr edit <N> --add-label hold` (the human-visible signal; no code reads labels), with the
  removal condition and its named owner in the body.
- [ ] **The pipeline run has a terminal state.** MERGE is never dispatched — its marker stays
  `pending`, never `in_progress` — and the run closes after DOCS, so the supervisor does not loop
  `/do-merge` against a draft PR. MERGE cannot be marked `skipped` (`SKIPPABLE_STAGES` excludes it),
  so an open follow-up operator issue is the durable carrier for the un-draft-and-merge action.
- [ ] **Issue #2642's body is amended** to record the two superseded constraints (internal
  resolution; provenance tag) with the spike evidence, so `/do-pr-review` grades the PR against the
  criteria it actually implements.

## Team Orchestration

### Team Members

- **Builder (steering-writer)**
  - Name: `steering-writer-builder`
  - Role: Flip the writer selectively (abort guard, Room-leg age bound), thread `room_id` through
    the flipped callers and the re-push, harden the five unsorted row selections, truth up the
    docstrings.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (steering-tests)**
  - Name: `steering-test-builder`
  - Role: Update the one inverted test; add the durability property test through
    `_default_steering_pop`, the abort-routing matrix, the staleness pair, the fallback matrix, the
    Room-write happy path, sibling provenance, and the two AST census tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (steering)**
  - Name: `steering-validator`
  - Role: Verify consumers are byte-unmodified, the payload shape is unchanged, and every success
    criterion holds.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `steering-documentarian`
  - Role: `docs/features/session-steering.md` and the parent plan's remaining-releases list.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Make the writer choose a key, with the abort guard, from one expression

- **Task ID**: build-writer
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-5 (an internal lookup costs ~2.4s), spike-3 (no `room_id` → legacy is a
  hard requirement or the suite goes red), D4 (aborts never leave the legacy key)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/steering.py::push_steering_message`.
- **Delete** the `key = _queue_key(session_id)` line at its current position (above the
  `ABORT_KEYWORDS` auto-detect) and re-introduce it **below** that block as a single expression:
  `key = _room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`.
  Two separate patches — one for `room_id`, one for `is_abort` — is how the ordering bug gets
  reintroduced; write it once, in one place, with the comment explaining why position matters.
- Leave the payload dict, the RPUSH/LPUSH split, and the log line untouched.
- Add **no** model import and **no** exception handler to this module. If either feels necessary,
  the design has drifted back to internal resolution — stop and re-read spike-5.

### 2. Thread room_id from the flipped callers; harden the row selections

- **Task ID**: build-callers
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-1b (the corrected caller table), Race 4 (four of five `_ack_steering_routed`
  callers select an unsorted row; caller 2070 holds none), D1 (the diagnostic writers stay legacy)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- **First, materialize — then sort — before touching any `room_id`.**
  `AgentSession.query.filter(...)` returns a `popoto.models.query.QueryBuilder`, **not** a list.
  It supports `__getitem__` (which is why `[0]` works today) but has **no `.sort` attribute**, so a
  bare `sessions.sort(...)` raises `AttributeError`. Verified on the baseline:
  `hasattr(AgentSession.query.filter(session_id='x'), 'sort')` is `False`.

  | Selection | Query line | Already `list(...)`? | Edit |
  |---|---|---|---|
  | `matching_session = sessions[0]` (1803) | 1801 | **no** | wrap the query in `list(...)`, then sort |
  | `live_guard = _live[0]` (1864) | 1860-1862 | **no** | wrap in `list(...)`, then sort `_live` |
  | `fresh_session = sessions[0]` (2174) | 2169-2172 | **no** | wrap in `list(...)`, then sort |
  | `session = sessions[0] if sessions else None` (2594) | 2593 | yes | sort only |
  | `active_edit = next(...)` over `edit_sessions` (2642) | 2641 | yes | sort `edit_sessions` only |

  Then insert `<name>.sort(key=lambda s: s.created_at or 0, reverse=True)` immediately before the
  `[0]` / `next(...)`. Keep the `or 0`; it matches ten existing sites and `created_at` is
  non-nullable, so it never fires.
- **Do not skip the `list(...)` at 1801/1860/2169.** Those three sit on the inbound-Telegram
  steering path with no enclosing `try`/`except` around the query, so the `AttributeError`
  propagates into the Telethon handler. At 2593 the same omission would be *silently* swallowed by
  the `except Exception` at 2595 (setting `session = None`, disabling edit-steering) — a failure the
  suite would not surface. Race 4 has the measured evidence.
- Then work the Technical Approach's **flip table** row by row: each site passes
  `room_id=room_id_for_session(<the session object already in scope>)`.
- `bridge/telegram_bridge.py::_ack_steering_routed` gains a pass-through
  `room_id: str | None = None`. Four callers (1809, 1835, 1868, 2178) derive from their sorted row.
  **Caller 2070 passes `room_id=None`.** It holds no session row — 2058-2067 bind `guard_sessions`
  and test only truthiness. Do **not** invent `guard_sessions[0]`; that is the exact defect Race 4
  forbids.
- `agent/session_runner/runner.py::_default_steering_push` **does** change — it gains
  `room_id=room_id_for_session(self._agent_session)`, mirroring `_default_steering_pop` at
  599-601, which already makes exactly that call. The read/write asymmetry there is the bug.
- Then work the **legacy table**: `agent/output_handler.py:1227`,
  `agent/session_health.py:3467`, `monitoring/session_watchdog.py:557`, `scripts/steer_child.py:119`
  each pass an **explicit `room_id=None`** with a one-line comment naming the reason (D1 or D4).
  The explicit keyword is what the census test checks; a bare omission fails it.
  `monitoring/session_watchdog.py::_inject_watchdog_steer` gains **no** parameter.
- `scripts/migrate_steering_queue_drain.py:126` is the one site that does **not** change at all.
- Do not introduce any new `AgentSession.query.filter(session_id=...)` call anywhere.

### 3. Thread room_id through the abort re-push

- **Task ID**: build-repush
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-2 (no per-message tag needed; four call sites, not three)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/health_check.py::_repush_messages`; forward it to the
  `push_steering_message` call inside.
- Pass `_handle_steering`'s already-resolved `room_id` at **all four** call sites: 530
  (abort-sibling primary), 536 (abort-sibling retry, inside `except`), 560 (non-abort primary),
  564 (non-abort retry, inside `except`). A mechanical replace on the primary call shape misses
  the two retries, and the miss is silent.
- Write each of those four calls on **one line** so the Verification grep row can see them; if a
  line-length limit forces a wrap, say so in the PR and the validator uses the AST check instead.
- Note the re-pushed siblings are non-abort by construction (the abort is consumed, not
  re-pushed), so D4 and this task do not conflict — but do not "helpfully" re-push the abort.

### 4. Bound the Room leg's age

- **Task ID**: build-staleness
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-6 (nothing bounds a Room-key message's lifetime), D5
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `steering_room_max_age_seconds: int = Field(default=21600, ge=60, le=604800, ...)` to
  `TimeoutSettings` in `config/settings.py`, following the shape and provisional-value comment
  style of the neighbouring `bridge_msg_claim_ttl_seconds`. Env key:
  `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS`. Add the `.env.example` placeholder with its required
  comment line.
- Add `max_age_seconds: float | None = None` to `agent/steering.py::_drain_list`. When set, drop
  entries whose payload `timestamp` is older than the bound, logging each drop at `info` with the
  key and the age so a missing steer stays diagnosable.
- Add the same parameter to `agent/steering.py::_peek_list`. It **skips** stale entries from the
  returned list; it must not delete anything — the peek is non-destructive by contract and the next
  drain is what removes them.
- Wire the bound at exactly three Room-leg read sites, **never on the legacy key** (that is what
  keeps every message that exists today behaving exactly as it does today):
  1. `pop_all_steering_messages` (line 138) → `_drain_list(_room_queue_key(room_id), max_age_seconds=...)`
  2. `peek_steering_messages` (line 208) → `_peek_list(_room_queue_key(room_id), max_age_seconds=...)`
  3. `has_steering_messages` (line 200) → replace the Room-leg `r.llen(_room_queue_key(room_id)) > 0`
     with `bool(_peek_list(_room_queue_key(room_id), max_age_seconds=...))`; leave the legacy-leg
     `llen` fast path alone.
  Sites 2 and 3 exist because `valor-session status` reads through `peek_steering_messages`
  (`tools/valor_session.py:1031`); an unfiltered peek would advertise steers the next drain throws
  away.
- **Do NOT touch `pop_steering_message` (line 164).** It does not call `_drain_list` — it inlines
  its own `r.lpop` + `json.loads` loop — and it has zero production callers (only its definition and
  the `agent/__init__.py` re-export at 41/73; every other reference is in `tests/`). Refactoring it
  through `_drain_list` is unasked scope. If a builder finds themselves editing it, stop.
- **Do NOT touch `peek_steering_sender` (line 223).** Legacy leg only (D1); never filtered.
- Read the bound from the settings object, not a module-level literal, so the test can vary it.

### 5. Truth up the docstrings

- **Task ID**: build-docstrings
- **Depends On**: build-writer, build-callers, build-repush, build-staleness
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Rewrite all four stale "writers are unchanged" claims: `agent/steering.py` module docstring
  (15-22), `_room_queue_key` (48-55), the `agent/health_check.py:511-513` comment, and the
  `agent/session_runner/runner.py:589-591` `_default_steering_pop` docstring. No
  "formerly"/"used to" narration.
- Each rewrite states the **selective** rule (conversation-level → Room; aborts and session-scoped
  diagnostics → legacy). An unqualified "writers now target the Room key" is a new falsehood.
- Document `room_id` on the three changed signatures (`push_steering_message`, `_repush_messages`,
  `_ack_steering_routed`) — the "no `room_id` → legacy", "abort → legacy regardless" contracts and
  why the writer deliberately does not look sessions up.
- Amend the `front` docstring (`agent/steering.py:96-100`) per Risk 3, and docstring
  `_drain_list`'s `max_age_seconds` per D5.

### 6. Update and extend the steering tests

- **Task ID**: build-tests
- **Depends On**: build-writer, build-callers, build-repush, build-staleness
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-3 (`test_handle_steering_drains_room_leg` assertion inverts)
- **Assigned To**: `steering-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- **ADD the durability property test first — it is the reason the release exists.**
  `test_steer_survives_target_session_and_reaches_room_sibling`: persist two `AgentSession` rows
  with `project_key="test-room-durability"` and `chat_id=None` (both map to `SYSTEM_ADDRESSEE`,
  `models/room.py:65-92`, so both derive the same composite). Push for A with A's Room, remove A,
  then read back **through `agent/session_runner/runner.py::_default_steering_pop`** — bind a bare
  runner instance with `_agent_session` set to session B's row (that function touches
  `self._agent_session` only via `getattr`, so no harness spawn is needed). Calling
  `pop_all_steering_messages` directly would re-implement the production Room derivation inside the
  test and could not catch a writer/reader mismatch. Tear down via the ORM scoped by the `test-`
  project key.
- ADD its negative twin: same scenario with `room_id=None` must NOT deliver to B.
- ADD: superseded-row safety — persist a superseded row and a live row sharing one `session_id`
  with different `chat_id`s; assert the steer lands on the live session's Room.
- ADD the **abort-routing matrix**: `is_abort=True` + truthy `room_id` → legacy; bare `"stop"` with
  `is_abort` defaulted + truthy `room_id` → legacy (this is the ordering test); each of
  `{"stop","cancel","abort","nevermind"}` → legacy; `"stop the deploy"` → Room.
- ADD the **staleness set** (four cases, not two): (a) a stale Room-leg entry is dropped by
  `pop_all_steering_messages` while a fresh one on the same key survives; (b) a stale **legacy**-leg
  entry is NOT dropped; (c) `peek_steering_messages` **skips** the stale Room-leg entry and — this
  is the operator-surface assertion — a subsequent `peek` still finds the fresh one, proving the
  peek did not delete anything; (d) `has_steering_messages` returns `False` when the Room leg holds
  only stale entries, and `True` when it holds a fresh one. Cases (c) and (d) are what keep
  `valor-session status` from advertising steers the next drain discards.
- ADD: a Room-leg entry with a missing/non-numeric `timestamp` is kept by `_drain_list` **and**
  returned by `_peek_list` (fail open on both).
- UPDATE `TestRoomDualRead::test_handle_steering_drains_room_leg` (line 1748): the re-push now
  targets the Room key. Fix the docstring too.
- ADD the legacy-fallback matrix — `room_id=None`, `room_id=""`, a session with no `project_key`
  — each lands on `_queue_key`. Plus `session_id=""` with a truthy `room_id` → the Room key wins.
- ADD: abort siblings re-pushed to the Room key, exercising the retry paths (536, 564) by forcing
  the primary call to raise.
- ADD: the payload persisted to Redis contains exactly `{text, sender, timestamp, is_abort}` (plus
  `target_agent` when set) — no provenance field.
- ADD **`test_every_flipped_writer_passes_room_id`** — the AST census. **Discover the call sites by
  walking the repo, not by iterating a hardcoded module table.** A fixed table only enforces the
  invariant against modules that existed at plan time: a future writer in a new module would omit
  `room_id`, silently default to the legacy key, and leave the census green — which is exactly the
  silent-failure mode this test was added to close. Instead:
  - Glob the repo's Python files (`Path(REPO_ROOT).rglob("*.py")`), excluding `tests/`, `.venv`,
    `.worktrees`, `node_modules` and any dot-directory. `ast.parse` each.
  - Every `ast.Call` resolving to `push_steering_message` **or** the `_push_steering_message` import
    alias must carry an explicit `room_id` keyword. Handle `ast.Name`, `ast.Attribute`, and the
    alias (`agent/session_executor.py:853`, `agent/session_health.py:3467`).
  - Allowlist exactly one path — `scripts/migrate_steering_queue_drain.py` — with the reason inline.
  - Assert the walk found at least as many call sites as the plan's caller table names (a floor, not
    an equality), so a discovery bug that silently matches nothing fails the test rather than
    passing vacuously.
  Reuse the path-glob discovery step and the scope-aware walker pattern from
  `tests/unit/test_bridge_dispatch_contract.py` (`_direct_calls` 66, `_banned_calls_in` 87,
  `ast.parse` 105). A repo walk also removes the plan-table-vs-test-list drift surface entirely.
- ADD **`test_room_derivation_sites_sort_before_selecting`** — for each of the five selections in
  Race 4, assert the enclosing function sorts by `created_at` **and** that the sorted name is bound
  to a materialized list. A text-match on the sort call alone passes on code that raises
  `AttributeError` at runtime, because `AgentSession.query.filter(...)` returns a `QueryBuilder`
  with no `.sort`. Concretely: for each `<name>.sort(...)` call found, walk back to the `ast.Assign`
  that binds `<name>` in the same function and assert its value is an `ast.Call` to `list` (or
  otherwise not a bare `.query.filter(...)` chain). Include a self-check case asserting the walker
  *rejects* a synthetic `sessions = AgentSession.query.filter(...)` / `sessions.sort(...)` pair, so
  the guard cannot silently degrade to a text match.
- Run via `scripts/pytest-clean.sh tests/integration/test_steering.py`, never bare pytest.

### 7. Validate

- **Task ID**: validate-all
- **Depends On**: build-docstrings, build-tests
- **Assigned To**: `steering-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm the diff adds no `AgentSession.query.filter(` call and no `except` inside
  `agent/steering.py::push_steering_message`.
- Confirm the key-selection expression sits **below** the `ABORT_KEYWORDS` auto-detect block.
- Confirm the five drain consumers are unmodified, `tools/valor_session.py:1031` (the status peek
  call) is unmodified, and **`peek_steering_sender` plus `agent/output_handler.py:1160-1162` are
  unmodified** — the D1 reversal makes these anti-criteria.
- Confirm `agent/session_health.py` and `monitoring/session_watchdog.py` gain no
  `room_id_for_session` import.
- Confirm the persisted payload shape is unchanged.
- Run every row of the Verification table.
- **Amend issue #2642's body** — `gh issue edit 2642` — recording that two of its stated
  constraints are superseded, with the evidence: (a) resolution does **not** happen inside
  `push_steering_message`; spike-5 measured `AgentSession.query.filter(session_id=...)` at
  2.55s/2.06s/2.36s because `models/agent_session.py:155` declares `session_id = Field()` and
  Popoto defaults to `indexed=False`. (b) There is **no** provenance tag; spike-2 shows forwarding
  the already-resolved `room_id` into `_repush_messages` satisfies the sibling criterion with a
  byte-identical payload, so criterion 4 is restated as "the JSON payload persisted to Redis is
  byte-identical to today's". Also restate criterion 1 as "every non-test caller passes `room_id`
  explicitly; conversation-level callers pass a derived Room" and correct "three writer sites" to
  twelve. Without this, `/do-pr-review` grades the PR against criteria it deliberately does not
  meet.
- Confirm the PR was created with `--draft`, carries the `hold` label, and states the removal
  condition and its named owner in its body.

### 8. Documentation, then close the run without dispatching MERGE

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `steering-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-steering.md` with the selective-flip rule (reproduce the Problem
  section's three-class table), the Risk 2 same-Room delivery semantics (D2: accepted behavior,
  documented as behavior), and the D5 age bound with its env key.
- Update the remaining-releases list at `docs/plans/durability-room-job-agentrun.md:673`.
- Add `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS` to `docs/features/config-timeout-catalog.md`.
- **Then terminate the pipeline run deliberately.** `agent/sdlc_router.py` has no notion of a held
  run and `.claude/skills/sdlc/SKILL.md` treats only `completed`/`skipped` as behind us, so letting
  the router reach MERGE would loop `/do-merge` against a draft PR forever.
  **MERGE cannot be marked `skipped`** — `tools/sdlc_stage_marker.py:322-330` refuses it with
  `STAGE_NOT_SKIPPABLE` because `agent/pipeline_state.SKIPPABLE_STAGES` permanently excludes
  REVIEW, DOCS and MERGE (each is a gate the merge predicate reads, so a skippable one would be a
  way to merge without the guarantee). Do not attempt that call; it fails loudly and leaves the run
  exactly as wedged.
  The exit is therefore **never dispatching MERGE at all**: leave the MERGE marker at `pending`
  (never write `in_progress` — an `in_progress` MERGE with no exit *is* the wedge), and carry the
  remaining action outside the pipeline:
  1. Post the hold as a comment on #2642: the PR number, the removal condition verbatim, and the
     named removal owner (Valor Engels, repo operator).
  2. File a follow-up operator issue — "Un-draft and merge PR #N (#2642) once fleet `/update` is
     past PR #2622" — so the action has a durable carrier that is not a wedged stage marker.
  3. Report the run to the supervisor as complete-through-DOCS with the verdict string
     `HELD: merge deliberately not dispatched`.
  The supervisor's exit condition is "draft PR open, `hold` applied, DOCS completed, MERGE never
  dispatched, follow-up issue filed" — not a merge, and not a stage-marker write.

## Verification

**Baseline commit: `e0157a0b2`** — the single baseline for this whole document, used by the
Freshness Check, the Structural Check Results and every threshold row below. (Earlier drafts cited
`e6d0e2bc7` in one section and `8afe2df22` in another; every number was re-measured on `e0157a0b2`
during the round-3 revision and all hold unchanged, so this was a citation inconsistency, never a
stale-number problem. Re-measured: `room_id` in `agent/steering.py` = 22, `_queue_key(session_id)` =
7, the `sed` span = 54 lines with 0 `except`, `created_at or 0` in `bridge/telegram_bridge.py` = 0,
`list(AgentSession.query.filter` = 3, `query.filter` in `agent/steering.py` = 0, `from models` = 0.)

Every threshold row below records its measured baseline inline, so a row cannot be satisfied by an
untouched checkout. Rows deleted at round 2 rather than re-tuned, with the reason:

- The `awk '/^def push_steering_message/,/^def /' … == 0` row was **vacuous**. In awk, a range whose
  end pattern matches the record that opened it collapses to that one record: measured on the
  baseline the pipeline emits exactly **1** line, the `def push_steering_message(` signature, which
  can never contain `except`. It passed unconditionally. Replaced by the `sed` row below, which
  spans **54** real lines and returns 0.
- `git diff origin/main | grep -c "^+.*filter(session_id=" == 0` was **unenforcing**. Every
  offending unsorted query already exists on `main`, so no `+` line appears while the Room is still
  derived from a possibly-superseded row. Replaced by a positive `created_at or 0` row plus an AST
  test.
- `grep -c "room_id=room_id" agent/health_check.py == 5` was **arithmetically wrong**: Task 3 also
  forwards `room_id` *inside* `_repush_messages`, so a correct build yields 6 and the gate failed a
  correct implementation. Split into a `== 4` row that actually pins the four call sites and a
  `>= 6` floor.
- The `room_id_for_session` rows for `monitoring/session_watchdog.py`, `agent/session_health.py`
  and `scripts/steer_child.py` are **inverted**, not deleted — those writers stay legacy (D1/D4),
  so their expectation is now `== 0`.

| Check | Command | Expected |
|-------|---------|----------|
| **Durability property delivered** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_steer_survives_target_session_and_reaches_room_sibling" -q` | exit code 0 (test must exist; a missing node id fails the row) |
| **Delivered via the production drain** | `grep -c "_default_steering_pop" tests/integration/test_steering.py` | ≥ 1 (baseline 0) — the durability test must read back through the runner's drain, not a hand-rolled `pop_all_steering_messages` call |
| **Negative twin passes** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "room_sibling or superseded"` | exit code 0, ≥ 3 tests collected |
| **Abort never lands on a Room key** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "abort_rout or abort_keyword"` | exit code 0, ≥ 4 tests collected |
| **Census test exists and passes** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_every_flipped_writer_passes_room_id" -q` | exit code 0 (anchored by node id so deleting the test fails the gate) |
| **Census discovers by repo walk, not a fixed table** | `sed -n '/def test_every_flipped_writer_passes_room_id/,/^def \\\|^class /p' tests/integration/test_steering.py \| grep -c "rglob\\\|glob("` | ≥ 1 — a hardcoded module list leaves a future writer in a new module silently on the legacy key, which is the failure mode this test exists to close |
| **Sort census passes** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_room_derivation_sites_sort_before_selecting" -q` | exit code 0 |
| Steering suite green | `scripts/pytest-clean.sh tests/integration/test_steering.py -q` | exit code 0 |
| Output-handler unit tests green | `scripts/pytest-clean.sh tests/unit/test_output_handler.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Writer takes a `room_id` and does not look one up | `grep -c "room_id" agent/steering.py` | ≥ 24 (baseline 22; +1 signature, +≥1 key selection) |
| **Writer performs no ORM query** | `grep -c "query.filter" agent/steering.py` | == 0 (baseline 0 — must stay 0) |
| **Writer imports no model layer** | `grep -c "from models" agent/steering.py` | == 0 (baseline 0 — must stay 0). A bare `grep -c "AgentSession"` is NOT usable here: the baseline is 3, from self-draft-counter docstrings at lines 252, 272, 301 that say the counter is *not* an AgentSession field. |
| **Writer adds no exception handler** | `sed -n '/^def push_steering_message/,/^def pop_all_steering_messages/p' agent/steering.py \| grep -c "except"` | == 0 (the span is **54** lines on the baseline and contains no `except`; unlike the deleted `awk` row this fails loudly if a handler is inserted) |
| **Abort guard sits below the auto-detect** | `sed -n '/^def push_steering_message/,/^def pop_all_steering_messages/p' agent/steering.py \| grep -n "ABORT_KEYWORDS\\\|_room_queue_key(room_id)"` | the `ABORT_KEYWORDS` line number is **lower** than the `_room_queue_key(room_id)` line number. Reversed order means `is_abort` is read stale and every keyword-detected abort goes to the Room. |
| **Writer names `is_abort` in the key selection** | `grep -c "not is_abort" agent/steering.py` | ≥ 1 (baseline 0) |
| Re-push forwards the room at all four call sites | `grep -c "_repush_messages(.*room_id=room_id" agent/health_check.py` | == 4 (baseline 0). Pins 530, 536, 560, 564 — including the two retries inside `except` blocks. Requires the calls be on one line; if a wrap is unavoidable, substitute the AST check named in Task 3. |
| Re-push forwards the room internally too | `grep -c "room_id=room_id" agent/health_check.py` | ≥ 6 (baseline 1 at line 514: the existing `pop_all_steering_messages` call; +4 call sites; +1 forward inside `_repush_messages`) |
| Bridge derives the Room | `grep -c "room_id_for_session" bridge/telegram_bridge.py` | ≥ 1 (baseline 0) |
| **Bridge sorts before deriving** | `grep -c "created_at or 0" bridge/telegram_bridge.py` | ≥ 5 (baseline **0**) — one per selection at 1803, 1864, 2174, 2594, 2642 |
| **Bridge materializes before sorting** | `grep -c "list(AgentSession.query.filter" bridge/telegram_bridge.py` | ≥ 6 (baseline **3** — lines 2289, 2593, 2641). The queries at 1801, 1860 and 2169 must gain a `list(...)` wrapper. **This row is load-bearing, not cosmetic:** `AgentSession.query.filter(...)` returns a `popoto.models.query.QueryBuilder` with **no `.sort` attribute**, so the `created_at or 0` row above matches happily on code that raises `AttributeError` at runtime. |
| **Sort census rejects an unmaterialized sort** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_room_derivation_sites_sort_before_selecting" -q` | exit code 0, and the test must include a synthetic-negative case proving it rejects a bare `sessions = AgentSession.query.filter(...)` / `sessions.sort(...)` pair |
| **Steering fast path does not raise** | `scripts/pytest-clean.sh tests/unit/test_bridge_dispatch_contract.py -q` plus the steering suite | exit code 0 — 1801/1860/2169 have no enclosing `try`/`except`, so an unmaterialized sort there propagates into the Telethon handler |
| Runner push derives the Room | `grep -c "room_id_for_session" agent/session_runner/runner.py` | ≥ 3 (baseline 2) |
| **Watchdog stays legacy (anti-criterion)** | `grep -c "room_id_for_session" monitoring/session_watchdog.py` | == 0 (baseline 0 — must stay 0; D1) |
| **Session-health stays legacy (anti-criterion)** | `grep -c "room_id_for_session" agent/session_health.py` | == 0 (baseline 0 — must stay 0; D1, and this is what keeps `front=True` from inverting, Risk 3) |
| **`steer_child` stays legacy (anti-criterion)** | `grep -c "room_id_for_session" scripts/steer_child.py` | == 0 (baseline 0 — must stay 0; D4) |
| **Legacy exceptions are explicit, not omissions** | `grep -c "room_id=None" agent/output_handler.py agent/session_health.py monitoring/session_watchdog.py scripts/steer_child.py bridge/telegram_bridge.py` (summed) | ≥ 5 (baseline 0) — the census test checks the same property structurally |
| **Peek untouched (anti-criterion)** | `git diff origin/main -- agent/steering.py \| grep -c "peek_steering_sender"` | == 0 (D1 reversal) |
| **Drafter guard untouched (anti-criterion)** | `git diff origin/main --quiet -- agent/output_handler.py \|\| git diff origin/main -- agent/output_handler.py \| grep -c "peek_steering"` | == 0 — the only permitted change to this file is the `room_id=None` keyword at 1227 |
| Room-leg age bound is a named setting | `grep -c "steering_room_max_age_seconds" config/settings.py` | ≥ 1 (baseline 0) |
| Age filter is Room-leg-only | `grep -c "max_age_seconds" agent/steering.py` | ≥ 5 (baseline 0). Derivation: `_drain_list` param + `_peek_list` param + three Room-key call sites (`pop_all_steering_messages`, `peek_steering_messages`, `has_steering_messages`). **`pop_steering_message` is NOT a call site** — it never calls `_drain_list` (it inlines `r.lpop`) and has zero production callers. |
| **Operator peek honors the bound** | `sed -n '/^def peek_steering_messages/,/^def peek_steering_sender/p' agent/steering.py \| grep -c "max_age_seconds"` | ≥ 1 (baseline 0) — `valor-session status` reads through this; an unfiltered peek advertises steers the next drain discards |
| **`has_steering_messages` Room leg honors the bound** | `sed -n '/^def has_steering_messages/,/^def peek_steering_messages/p' agent/steering.py \| grep -c "max_age_seconds"` | ≥ 1 (baseline 0) — its Room leg swaps raw `llen` for a filtered `_peek_list`; the legacy-leg `llen` stays |
| **`pop_steering_message` untouched (anti-criterion)** | `git diff origin/main -- agent/steering.py \| grep -c "^[-+].*def pop_steering_message"` | == 0 — zero production callers; plumbing the bound into its inlined `lpop` loop is unasked scope |
| Migration script left alone (anti-criterion) | `git diff origin/main --quiet -- scripts/migrate_steering_queue_drain.py` | exit code 0 |
| Stale module docstring gone | `grep -c "Writers still push to the legacy key only" agent/steering.py` | == 0 (baseline 1) |
| Stale key docstring gone | `grep -c "No writer targets this key yet" agent/steering.py` | == 0 (baseline 1) |
| Stale health_check comment gone | `grep -c "the re-pushes below always target the legacy list" agent/health_check.py` | == 0 (baseline 1) |
| Stale runner docstring gone | `grep -c "Writers are unchanged in this release" agent/session_runner/runner.py` | == 0 (baseline 1) |
| No provenance field in payload | `grep -c "_source" agent/steering.py` | == 0 (baseline 0) |
| Status peek call untouched (anti-criterion) | `git diff origin/main -- tools/valor_session.py \| grep -c "^[-+].*peek_steering"` | == 0 |
| Drain helper not removed (anti-criterion) | `git diff origin/main -- agent/steering.py \| grep -c "^-.*def _drain_list"` | == 0 |
| Legacy write leg still reachable | `grep -c "_queue_key(session_id)" agent/steering.py` | ≥ 7 (baseline 7 — the legacy leg is a fallback, never retired) |
| **Merge gate (the enforcing one)** | `gh pr view <N> --json isDraft -q .isDraft` | `true`. `tools/merge_predicate.py:465-466` fails any PR whose `mergeStateStatus` is not `CLEAN`/`UNSTABLE`; GitHub reports `DRAFT`, so `/do-merge` refuses fail-closed. |
| Merge signal (human-visible only) | `gh pr view <N> --json labels -q '.labels[].name'` | includes `hold` (`gh label list` → `hold #c73f67`). No code reads PR labels — `grep -rn 'label' tools/merge_predicate.py` returns nothing — so this row is a signal, never the gate. |
| Removal condition recorded | `gh pr view <N> --json body -q .body \| grep -c "fleet-wide"` | ≥ 1, with the named owner |
| **Pipeline has a terminal state** | `sdlc-tool stage-query --issue-number 2642` | MERGE still `pending` — never `in_progress`. It cannot be `skipped` (`SKIPPABLE_STAGES` excludes MERGE; the write is refused `STAGE_NOT_SKIPPABLE`), so "never dispatched" is the terminal state and the follow-up issue is the carrier. |
| **Held action has a durable carrier** | `gh issue list --search "Un-draft and merge PR" --state open --json number` | ≥ 1 open follow-up issue naming the PR and the removal owner |
| **Issue amended for superseded criteria** | `gh issue view 2642 --json body -q .body \| grep -c "superseded"` | ≥ 1 — otherwise `/do-pr-review` grades against criteria the plan deliberately reverses |

## Critique Results

Round 3 (FULL war room: Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **NEEDS REVISION** — 1 blocker, 3 concerns, 2 nits. **All six addressed; revision applied
2026-08-07.** Every claim was re-verified against the working tree at `e0157a0b2` before the fix was
written — the blocker exists precisely because a prior revision asserted an API that does not exist,
so nothing below is taken on the critique's word alone. Verification evidence:

- `type(AgentSession.query.filter(session_id='x'))` → `<class 'popoto.models.query.QueryBuilder'>`;
  `hasattr(q, 'sort')` → `False`; `hasattr(q, '__getitem__')` → `True`. Confirmed.
- `grep -n "AgentSession.query.filter" bridge/telegram_bridge.py` → 1801, 1860, 2169 unwrapped;
  2593, 2641 wrapped in `list(...)`. Confirmed.
- `agent/steering.py:164 pop_steering_message` inlines `r.lpop` + `json.loads`, never calls
  `_drain_list`. Non-test references: its definition and `agent/__init__.py` 41/73 only. Confirmed.
- `agent/steering.py:208 peek_steering_messages` → `_peek_list` (LRANGE, no `timestamp` read);
  `:200 has_steering_messages` → raw `llen` on both legs; `tools/valor_session.py:1031` calls
  `peek_steering_messages` with a `room_id`. Confirmed.
- Threshold baselines re-measured on `e0157a0b2`: `room_id` = 22, `_queue_key(session_id)` = 7,
  `sed` span = 54 lines / 0 `except`, `created_at or 0` = 0, `list(AgentSession.query.filter` = 3,
  `query.filter` in `agent/steering.py` = 0, `from models` = 0. All hold.

The round-2 log below the table is retained as history. All fourteen round-2 findings
were re-checked against the working tree during this pass and remain addressed.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | Task 2 mandates `sessions.sort(key=lambda s: s.created_at or 0, reverse=True)` at all five bridge selections, but at 1804 (`sessions`), 1865 (`_live`) and 2174 (`sessions`) the name is bound to a bare `AgentSession.query.filter(...)`, which returns a `popoto.models.query.QueryBuilder` with no `.sort` attribute. Only 2594 and 2642 wrap the query in `list(...)`. The mandated edit raises `AttributeError` on the inbound-Telegram steering fast path, and both Race-4 mechanical guards pass on the crashing code: the `created_at or 0` grep matches the string, and `test_room_derivation_sites_sort_before_selecting` only asserts the enclosing function contains a `created_at` sort. | **Fixed.** Race 4 mitigation part 1 rewritten as materialize-then-sort with the measured `QueryBuilder`/`hasattr` evidence and a per-site table; Task 2 gains the same table plus an explicit "do not skip the `list(...)`" bullet naming the no-`try` sites (1801/1860/2169) and the silently-swallowed one (2593, `except` at 2595). Two new Verification rows: `list(AgentSession.query.filter` >= 6 (baseline 3), and a sort-census row requiring a synthetic-negative self-check. `test_room_derivation_sites_sort_before_selecting` now asserts the sorted name is bound to a materialized list (walk back to the `ast.Assign`), not a text match on the sort call. New Failure Path bullet. Line-number citations corrected to the selection lines (1803/1864/2174/2594/2642) and their query lines. | Verified on the working tree: `AgentSession.query.filter(session_id='x')` returns `<class 'popoto.models.query.QueryBuilder'>` and `hasattr(q,'sort')` is False. Sites 1801/1860/2170 have no `list(...)` wrapper and no enclosing try/except, so the AttributeError propagates into the Telethon handler (unlike 2593, which sits inside `except Exception` at 2596 and would instead silently set `session = None`, disabling edit-steering). Required shape: `sessions = list(AgentSession.query.filter(session_id=session_id, status=check_status))` before the sort. The sort census test must assert the sorted name is a materialized list, not text-match a sort call. |
| CONCERN | Risk & Robustness | Task 4 instructs passing `max_age_seconds` from `pop_all_steering_messages` and `pop_steering_message` on the Room key, but `pop_steering_message` (agent/steering.py:164-181) never calls `_drain_list` — it inlines its own `r.lpop(key)` plus `json.loads` loop. Adding the parameter to `_drain_list` cannot reach it, so the instruction is unimplementable as written, and the Verification row's derivation (`_drain_list` param plus two Room-key call sites) counts a call site that does not exist. | **Fixed.** Task 4, D5, the Technical Approach bullet, the Documentation bullet and the Verification row are all scoped off `pop_steering_message`, with the zero-production-callers evidence recorded inline and an explicit "if a builder finds themselves editing it, stop". A new anti-criterion row pins it unmodified. The `max_age_seconds` count row is re-derived (>= 5) from the sites that actually exist. | Scope Task 4, the Verification row and the Documentation bullet ("state at both call sites") to `pop_all_steering_messages` only — it is the sole production drain path. A repo-wide grep excluding tests/ shows `pop_steering_message`'s only references are its definition and the `agent/__init__.py` re-export at lines 41/73; it has zero production callers, so refactoring it through `_drain_list` would be unasked scope. |
| CONCERN | Risk & Robustness | D5's age bound applies only at drain time, but the two non-draining readers of the Room leg bypass it: `peek_steering_messages` (agent/steering.py:208) uses `_peek_list` and `has_steering_messages` (agent/steering.py:200) uses raw `llen` on both legs. `peek_steering_messages` is the production `valor-session status` pending-steering peek at `tools/valor_session.py:1031` — the one operator-visible surface the plan declares must keep working and pins as an anti-criterion — so it reports stale Room-leg steers indefinitely that the next drain silently discards. | **Fixed by extending D5 to every Room-leg reader.** `_peek_list` gains the same `max_age_seconds` (skips, never deletes); `peek_steering_messages` passes it on the Room key; `has_steering_messages`'s Room leg swaps raw `llen` for a filtered `_peek_list` while the legacy leg keeps its `llen` fast path. D5 now carries a three-reader table. Task 4 enumerates the three wiring sites. Two new Verification rows scope-check the peek and the `has_` function. The staleness test pair becomes a four-case set adding a peek case (proving the peek skipped without deleting) and a `has_steering_messages` case. The anti-criterion stays on the **call site** `tools/valor_session.py:1031`, which needs no edit — Agent Integration says so explicitly. | `peek_steering_messages` at agent/steering.py:208-221 calls `_peek_list(_queue_key(session_id))` then `_peek_list(_room_queue_key(room_id))`; `_peek_list` is LRANGE-based and never reads `timestamp`. Either thread the same `max_age_seconds` into `_peek_list` on the Room key, or state in D5 and `docs/features/session-steering.md` that the peek deliberately shows pre-expiry entries and label them expired in the status output. D5 claims the drop is logged "so a missing steer stays diagnosable", but the surface an operator actually reaches for reads through the unfiltered peek. |
| CONCERN | Scope & Value | The AST census `test_every_flipped_writer_passes_room_id` is scoped to "each module in the caller table" — a hardcoded list of the eleven modules that call `push_steering_message` today. The invariant it claims to machine-check is therefore enforced only against modules that existed at plan time; a future writer added in a new module omits `room_id`, silently defaults to the legacy key, and the census stays green. That is the exact silent-failure mode round-2 finding 8 was raised to close. | **Fixed.** Task 6's census bullet now mandates repo-walk discovery (`rglob("*.py")`, excluding `tests/`, `.venv`, `.worktrees`, `node_modules`, dot-dirs) instead of a module table, keeping `scripts/migrate_steering_queue_drain.py` as the sole allowlist entry and the existing `ast.Name`/`ast.Attribute`/import-alias handling. It also adds a call-site floor so a discovery bug that matches nothing fails loudly rather than passing vacuously. New Verification row greps the test for `rglob`/`glob(`. Success criterion restated as "not scoped to a fixed module list". | Discover call sites by walking the repo's Python files for `push_steering_message` / `_push_steering_message` calls (excluding tests/) rather than iterating a fixed module table, keeping `scripts/migrate_steering_queue_drain.py` as the single allowlist entry. `tests/unit/test_bridge_dispatch_contract.py` — the pattern the plan already cites — resolves modules by path glob, so reuse that discovery step and keep the existing `ast.Name` / `ast.Attribute` / import-alias handling for `agent/session_executor.py:853` and `agent/session_health.py:3467`. A repo walk also removes the plan-table-vs-test-list drift surface. |
| NIT | History & Consistency | Success Criteria states "the six documented exceptions pass `None` with the reason inline" and then enumerates five paths before excepting `scripts/migrate_steering_queue_drain.py:126` as the sixth — which does not pass `None`, it is not modified at all. The Verification row "Legacy exceptions are explicit, not omissions" correctly expects >= 5, so the criterion's own count disagrees with the check enforcing it. | **Fixed.** Reworded exactly as suggested: five exceptions pass an explicit `room_id=None`; a sixth, `scripts/migrate_steering_queue_drain.py:126`, is unmodified and is the census allowlist's sole entry. The criterion now states parenthetically that five is what the `>= 5` row expects. | Reword to: five documented exceptions pass an explicit `room_id=None`; a sixth, `scripts/migrate_steering_queue_drain.py:126`, is unmodified and is the census allowlist's sole entry. |
| NIT | History & Consistency | Two different baseline commits are cited for the same plan: the Freshness Check records `e6d0e2bc7` while the Verification preamble and the Structural Check Results row both record `8afe2df22`. Neither is current `main` (`235da078c`). | **Fixed.** Collapsed to a single named baseline, `e0157a0b2` (current `main` at revision time), stated in the Verification preamble and referenced from the Freshness Check. Every threshold was re-measured on it during this revision and all hold; the preamble records both the re-measured values and the fact that this was a citation inconsistency, not a stale number. | Collapse to a single named baseline. The threshold values themselves were re-measured against the current working tree during this critique and all hold (room_id = 22, _queue_key(session_id) = 7, sed span = 54 lines with 0 except, created_at or 0 = 0, query.filter = 0, from models = 0), so this is a citation inconsistency, not a stale-number problem. |

### Structural Check Results (round 3)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | build-writer -> build-callers/build-repush/build-staleness -> build-docstrings/build-tests -> validate-all -> document-feature; no cycles |
| File paths exist | PASS | All referenced source paths exist; `_drain_list` (58), `push_steering_message` (85), `pop_all_steering_messages` (138), `pop_steering_message` (164), `has_steering_messages` (200), `peek_steering_messages` (208), `peek_steering_sender` (223) confirmed in `agent/steering.py` |
| Cross-references | FAIL → **resolved in revision** | Task 4 and the Documentation bullet referenced a `pop_steering_message` `_drain_list` call site that does not exist (now scoped off it entirely, with an anti-criterion pinning it unmodified); Success Criteria's "six exceptions pass None" disagreed with its own >= 5 Verification row (now five explicit `None`s plus one unmodified allowlist entry) |
| Verification baselines | PASS | Re-measured on `e0157a0b2` — the document's single baseline: `room_id` = 22, `_queue_key(session_id)` = 7, sed span = 54 lines with 0 `except`, `query.filter` = 0, `from models` = 0, `created_at or 0` in `bridge/telegram_bridge.py` = 0, `list(AgentSession.query.filter` = 3 |
| Prerequisites met | PASS | Redis reachable; venv on `.python-version` pin |

### Round-2 resolution log (history)


| # | Lane | Severity | Finding (compressed) | Disposition |
|---|------|----------|----------------------|-------------|
| 1 | A + B | BLOCKER | Race 4's mitigation rests on a false claim: `bridge/telegram_bridge.py:2593-2594` is an unsorted `sessions[0]`, not a newest-by-`created_at` selection. The `:2650` source (`active_edit`, 2641-2646) and three `_ack_steering_routed` callers (1804, 1865, 2174) have the same shape. Round 1's unsorted-`[0]` blocker was relocated into the callers, not dissolved. Caller 2070 holds no session row at all. | **Fixed.** Race 4 rewritten with the false claim quoted and corrected. Task 2 now sorts *first* at all five sites using the repo idiom (`created_at or 0`, ten existing uses), and mandates `room_id=None` at 2070 rather than a fabricated `guard_sessions[0]`. spike-1b's table corrected. The unenforcing diff-grep anti-criterion is replaced by a positive `created_at or 0 >= 5` row (baseline 0) plus `test_room_derivation_sites_sort_before_selecting`. Success criterion restated as "derives a Room from", not "introduces". |
| 2 | A + B | BLOCKER | Abort steers become Room-scoped and can abort the wrong session. `scripts/steer_child.py:119` is unconditionally `is_abort=True`; a chatless Eng child maps to `{project}\|system`. `_handle_steering` hands "You MUST stop immediately" to whichever session drains first, bypassing all four validation gates at 93-112. Risk 2's "which session, not loss" reasoning holds for an instruction but not a destructive control signal. | **Fixed, in the writer.** New decision **D4**: `key = _room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`, placed *below* the `ABORT_KEYWORDS` auto-detect (Task 1 spells out why one expression, not two patches). Guarding at the call sites would miss the keyword-detected aborts that `bridge/telegram_bridge.py:978` produces. New abort-routing test matrix, two new success criteria, two new Verification rows (including an ordering row). Risk 2 and Race 2 amended to say explicitly where their accepted-hazard reasoning stops. |
| 3 | A | BLOCKER | The merge-hold gate is specified three incompatible ways and names a nonexistent `do-not-merge` label. Draft state is the only thing that actually blocks (`tools/merge_predicate.py:465-466` refuses a non-CLEAN/UNSTABLE `mergeStateStatus`; a draft reports `DRAFT`). | **Fixed.** D3 rewritten to one mechanism with the exact commands and the `merge_predicate` citation; restated identically in the No-Go bullet, Task 8, Success Criteria, and two Verification rows. `hold` (which exists) is documented as a human-visible signal that no code reads. The plan's residual mentions of `do-not-merge` are now only in this log, as history. |
| 4 | B | BLOCKER | The flip is uniform, but three writers carry payloads meaningless or harmful in another session: `agent/output_handler.py:1227` (self-draft, and its budget `steering:attempts:{session_id}` is session-keyed so a Room-durable steer escapes it), `agent/session_health.py:3467` (tool-timeout advisory, also the only `front=True` push), `monitoring/session_watchdog.py:557` (loop-break steer). The latter two fire at *wedged* sessions. | **Fixed by narrowing the flip.** New **D1** (reversing the old D1): those three pass explicit `room_id=None` with the reason inline. Six of twelve callers flip; six deliberately do not, and the Problem section leads with the three-class table. This also **dissolves** the old Task 4, Risk 3-as-written, and all churn in `tests/unit/test_output_handler.py` — `peek_steering_sender` and `agent/output_handler.py:1160-1162` are now anti-criteria. The plan modifies zero consumers. |
| 5 | B | BLOCKER | No staleness bound on the Room leg. TTL is None, `clear_steering_queue` has zero production callers, no drain filters on `timestamp`. An undrained steer is immortal. | **Fixed.** New spike-6 and decision **D5**; new Task 4 adds `TimeoutSettings.steering_room_max_age_seconds` (default 21600, env-overridable) and a `max_age_seconds` filter in `_drain_list` applied **only** to the Room key. Two tests pin that the legacy leg is never filtered. The destructive sub-case this finding named (an immortal auto-detected "stop") is independently closed by D4. |
| 6 | A | CONCERN | `front=True` priority inverts on the Room leg — `agent/session_health.py:3473` LPUSHes but consumers drain legacy first. | **Fixed structurally.** The repo's only `front=True` push is that site, which now passes `room_id=None` (D1), so `front` stays a within-legacy-leg contract. Risk 3 rewritten to this. Pinned by an anti-criterion row (`room_id_for_session` in `agent/session_health.py` == 0) and a docstring amendment. |
| 7 | A | CONCERN | `grep -c "room_id=room_id" agent/health_check.py == 5` fails a *correct* build — Task 3 also forwards inside `_repush_messages`, yielding 6. | **Fixed.** Split into `grep -c "_repush_messages(.*room_id=room_id" == 4` (baseline 0 — the row that actually pins 530/536/560/564) and `grep -c "room_id=room_id" >= 6` (baseline 1). Task 3 states the one-line-per-call requirement the grep needs, with an AST fallback. |
| 8 | A | CONCERN | The durability invariant is diffused across the callers with no enforcement and a silent failure mode; the repo already has the AST-census pattern in `tests/unit/test_bridge_dispatch_contract.py`. | **Fixed.** `test_every_flipped_writer_passes_room_id` added to Task 6 — AST-walks every module in the caller table, requires an explicit `room_id` keyword on every `push_steering_message` call (handling the `_push_steering_message` alias), one allowlisted path. Anchored in Verification by pytest node id. This is also why the six legacy sites pass an explicit `None` rather than omitting the argument. |
| 9 | A | CONCERN | The plan reverses two of #2642's explicit constraints (internal resolution; provenance tag) without amending the issue, so `/do-pr-review` grades against superseded criteria. | **Fixed.** Task 7 now includes a `gh issue edit 2642` step recording both supersessions with the spike-5 and spike-2 evidence, restating criteria 1 and 4, and correcting "three writer sites" to twelve. A Verification row checks the issue body. |
| 10 | B | CONCERN | The durability test calls `pop_all_steering_messages` directly, re-implementing the production Room derivation inside the test, so it cannot catch a writer/reader derivation mismatch. | **Fixed.** The test now reads back through `agent/session_runner/runner.py::_default_steering_pop` with `_agent_session` bound to session B's row (that function uses `getattr` only, so no harness spawn). New Verification row requires `_default_steering_pop` to appear in the test file. |
| 11 | B | CONCERN | The `awk '/^def push_steering_message/,/^def /'` row is vacuous — the range collapses to the signature line, so it passes unconditionally. It was the sole mechanical guard Risk 4 named. | **Fixed.** Replaced with the `sed -n '/^def push_steering_message/,/^def pop_all_steering_messages/p'` form, measured at **54** lines with 0 `except` on the baseline. The deletion and its reason are recorded above the Verification table. |
| 12 | B | CONCERN | The merge hold has no terminal state — the router would dispatch `/do-merge` against a draft forever. | **Fixed (corrected in-pass).** The first attempt specified "record MERGE as `skipped`", which is unimplementable: `agent/pipeline_state.SKIPPABLE_STAGES` permanently excludes REVIEW/DOCS/MERGE and `tools/sdlc_stage_marker.py:322-330` refuses the write with `STAGE_NOT_SKIPPABLE`. Task 8 now specifies the only exit that exists — **never dispatch MERGE** (leave the marker `pending`, never `in_progress`), record the hold as a comment on #2642, and file a follow-up operator issue as the durable carrier for the un-draft-and-merge action. Two Verification rows check both halves. |
| 13 | A | NIT | `appetite: Small` no longer fits the shape. | **Fixed.** Raised to `Medium`, with the reason recorded in the Appetite section (including that `Small` kept the plan off the force-FULL triage path). |
| 14 | B | NIT | Three stale references: `TestSteeringDualRead` (the class is `TestRoomDualRead` at 1678), parent-plan line "~655" (it is 673), and Addressed-By cells routing peek work to "Task 5". | **Fixed.** Test Impact cites `TestRoomDualRead` at line 1748; Documentation and Task 8 cite line 673; the peek work no longer exists, so the stale task routing is moot. |

### Structural Check Results (round 2 — history)

The commit cited in this block is the round-2 baseline. The document's single authoritative baseline
is `e0157a0b2`; see the Verification preamble.

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | `build-writer` -> `build-callers`/`build-repush`/`build-staleness` -> `build-docstrings`/`build-tests` -> `validate-all` -> `document-feature`; no cycles |
| File paths exist | PASS | Every referenced source path exists; the twelve writer call sites, the four `_repush_messages` sites (530/536/560/564) and the five unsorted row selections (1804/1865/2174/2594/2642) confirmed at the cited lines |
| Prerequisites met | PASS | Redis reachable; venv on `.python-version` pin |
| Verification baselines | PASS | All threshold rows re-measured on `8afe2df22`: `room_id` in `agent/steering.py` = 22, `_queue_key(session_id)` = 7, the `sed` span = 54 lines with 0 `except`, `created_at or 0` in `bridge/telegram_bridge.py` = 0, `room_id_for_session` = 0 in bridge/session_health/watchdog/steer_child and 2 in the runner, `_repush_messages(.*room_id=room_id` = 0, `room_id=room_id` in `agent/health_check.py` = 1, all four stale-docstring rows = 1 |
| Cross-references | PASS | Merge gate stated identically in D3, the No-Go bullet, Task 8, Success Criteria and Verification; Race 4's prose matches the measured source; the selective-flip boundary is stated identically in Problem, spike-1b, D1/D4, Technical Approach, Success Criteria and Verification |
