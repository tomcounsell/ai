---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2642
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-07T09:23:53Z
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

Writes therefore fall into **five** classes, not two (the table below has one row per class). Only
the first targets the Room unconditionally. This is not an incomplete flip; it is the flip's correctness boundary, and it is
stated as a success criterion:

| Class | Sites | Rule |
|---|---|---|
| **Conversation-level originating writes** | `bridge/telegram_bridge.py:979` (4 of its 5 callers), `:2617`, `:2650`, `tools/valor_session.py:856`, `agent/session_executor.py:853` | **Room** when a Room resolves. This is the flip. |
| **Requeue writes** | `agent/health_check.py:480` (`_repush_messages`), `agent/session_runner/runner.py:610` (`_default_steering_push`), `agent/session_executor.py:1933` | **The leg the message was drained from.** All three re-push messages that a dual-read drain took off *either* leg, so a fixed target launders one class into the other. Room-sourced → Room (or the durability property is lost on the first requeue); legacy-sourced → legacy (or D1's boundary is defeated). See **D6**. |
| Abort signals | any push with `is_abort` true, explicit or auto-detected from `ABORT_KEYWORDS` — including `scripts/steer_child.py:119` | **Legacy, always.** "You MUST stop immediately" is destructive and non-idempotent. Delivered to the wrong session it kills innocent work while the intended target keeps running. Stranding an abort is the correct failure mode. (D4) |
| Session-scoped diagnostics | `agent/output_handler.py:1227`, `agent/session_health.py:3467`, `monitoring/session_watchdog.py:557` | **Legacy.** Each payload describes state of *this* session — the draft it just emitted, the tool that wedged, the tool it repeated. Delivered to a successor it is noise at best; the drafter one also escapes its session-keyed attempt budget (`steering:attempts:{session_id}`, `agent/steering.py:262`) and can re-enter. The latter two fire at *wedged* sessions, the ones most likely to die undrained. (D1) |
| No live row / ORM-free writers | `bridge/telegram_bridge.py:2070`, `scripts/migrate_steering_queue_drain.py:126` | **Legacy.** Neither holds a session row it can derive a Room from. Fabricating one would reintroduce the unsorted-`[0]` defect (Race 4). |

A further boundary is temporal rather than per-site: a Room-key message that no session drains is
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
sites, not thirteen) and the Room leg gained an age bound. Round 4 then split the flip set again:
the three requeue writers became leg-preserving rather than Room-targeting (D6). spike-4's fix was consequently *dropped*,
because narrowing the flip dissolved the problem it existed to solve. Everything below reflects the
settled state.

### spike-1: Is `push_steering_message` the single writer funnel?

- **Assumption**: "There are three writer sites that each need an independent flip."
- **Method**: code-read (`grep -rn "push_steering_message(" bridge tools agent monitoring scripts
  --include="*.py"`)
- **Finding**: **The assumption is wrong in a helpful direction.** `push_steering_message` is the
  only function that RPUSHes/LPUSHes a steering payload. **Thirteen** non-test call sites route
  through it: `bridge/telegram_bridge.py:979,2617,2650`, `tools/valor_session.py:856`,
  `agent/session_executor.py:853` (via the `_push_steering_message` alias imported at 851) and
  `agent/session_executor.py:1933` (via a second alias `_push_steering` imported at 1930),
  `agent/session_health.py:3467`, `agent/output_handler.py:1227`, `agent/health_check.py:480`,
  `agent/session_runner/runner.py:610`, `monitoring/session_watchdog.py:557`,
  `scripts/steer_child.py:119`, `scripts/migrate_steering_queue_drain.py:126`.
  **Count correction (round 4).** Round 1 said thirteen and cited
  `agent/session_executor.py:1930`; round 2 "corrected" that to twelve on the ground that 1930 does
  not call the writer. Both were wrong in different ways: 1930 is the `from agent.steering import
  push_steering_message as _push_steering` line and the **call is three lines below it at 1933**,
  inside the `if len(steering_msgs) > 1:` remaining-messages requeue. Re-measured at round 4 with
  `grep -rn "push_steering_message\|_push_steering(" bridge tools agent monitoring scripts`. The
  count is thirteen and the missed site is a **requeue** writer — the exact class round 4's
  blocker 2 is about. Success criteria say "every non-test caller", never a count; the AST census
  test (Task 6) is what actually enforces coverage, precisely because this count has now been
  miscounted twice by hand.
- **Confidence**: high
- **Impact on plan**: One funnel to change, plus a `room_id` argument threaded from each caller.

### spike-1b: Which callers already hold an `AgentSession` object?

- **Assumption**: "Most callers have only a bare `session_id` string, so the writer must look the
  session up itself."
- **Method**: code-read of ~30 lines of context around all thirteen call sites
- **Finding**: **Eleven of thirteen have a session object in hand, or one stack frame up.** The
  `Target` column records the round-2 and round-4 corrections: a site targets the Room only if its
  payload is conversation-level **and** the object it would derive from is guaranteed to be the
  *live* row; a site that re-pushes an already-drained message targets whichever leg that message
  came from (D6).

  | Site | Object in scope | Target | Notes |
  |---|---|---|---|
  | `bridge/telegram_bridge.py:979` (`_ack_steering_routed`) | via callers | **Room, 4 of 5** | helper takes `session_id: str`. Callers 1809 (`matching_session`), 1835, 1868 (`live_guard`) and 2178 (`fresh_session`) hold a row. **Caller 2070 holds none** — 2058-2067 bind `guard_sessions` and test only truthiness, passing the bare string `guard_session_id`. It passes `room_id=None`. |
  | `bridge/telegram_bridge.py:2617` | `session` (loaded 2593-2594) | Room | that load is an **unsorted** `sessions[0]`; it must be sorted newest-first before deriving (Race 4) |
  | `bridge/telegram_bridge.py:2650` | `active_edit` | Room | selected by an **unsorted** `next(...)` over `edit_sessions` at 2641-2646; same sort requirement |
  | `tools/valor_session.py:856` | `session` | Room | resume path, distinct from the status peek at 1031 |
  | `agent/session_executor.py:853` | `session` | Room | `steer_session`, an originating PM steer; passes `session.session_id` |
  | `agent/session_runner/runner.py:610` (`_default_steering_push`) | `self._agent_session` | **per-leg (D6)** | its **only** caller is `_requeue_pending_steers` (631), re-pushing steers `_default_steering_pop` (587-601) drained from *both* legs. It still needs `room_id_for_session(self._agent_session)`, but applies it only to Room-sourced entries. |
  | `agent/health_check.py:480` (`_repush_messages`) | `room_id` one frame up | **per-leg (D6)** | `_handle_steering` receives a resolved `room_id` and drains both legs at 514, then re-pushes survivors. Same laundering hazard. |
  | `agent/session_executor.py:1933` (`_push_steering`) | `agent_session` (in scope at 1909) | **per-leg (D6)** | **found at round 4.** The turn-boundary drain at 1908-1910 reads both legs via `room_id_for_session(agent_session)`; `steering_msgs[1:]` are re-pushed here for future turns. Third instance of the same hazard. |
  | `agent/session_health.py:3467` | `entry: AgentSession` | **legacy** | tool-timeout advisory naming the wedged tool — session-scoped diagnostic. Also the repo's only `front=True` push (3473); see Risk 3. Stays legacy (D1). |
  | `monitoring/session_watchdog.py:557` (`_inject_watchdog_steer`) | via callers | **legacy** | loop-break steer naming the repeated tool — session-scoped diagnostic. Stays legacy (D1); the helper gains no parameter. |
  | `agent/output_handler.py:1227` | `session` | **legacy** | drafter self-draft, bounded by the session-keyed counter `steering:attempts:{session_id}` (`agent/steering.py:262`). Stays legacy (D1). |
  | `scripts/steer_child.py:119` | `child` | **legacy (abort)** | unconditionally `is_abort=True` (line 123). The writer's abort guard forces legacy regardless (D4); the call site passes `room_id=None` so the intent is legible there too. |
  | `scripts/migrate_steering_queue_drain.py:126` | **none** | legacy | raw Redis hash scan; deliberately ORM-free |

- **Confidence**: high
- **Impact on plan**: **Decisive.** It removes any need for the writer to query for a session.
  `room_id_for_session(session)` is pure `getattr` + string work with zero Redis I/O
  (`models/room.py:65-100`), so an explicit `room_id` argument from each caller is free. See
  spike-5 for why the alternative is not viable.
- **Round-2 corrections**: (a) caller 2070 was wrongly listed as holding `matching_session`; it
  holds no row and must pass `room_id=None` rather than have a builder invent `guard_sessions[0]`.
  (b) Three diagnostic writers and one unconditional-abort writer are removed from the flip set.
  (c) Two flipped sites derive from unsorted row selections that must be sorted first.
- **Round-4 corrections**: (d) a thirteenth site exists, `agent/session_executor.py:1933`, and it
  was never assigned a disposition. (e) The three requeue writers (`agent/health_check.py:480`,
  `agent/session_runner/runner.py:610`, `agent/session_executor.py:1933`) were previously in the
  flip set with an unconditional `room_id`. That defeats D1: each re-pushes messages a dual-read
  drain took off *either* leg, so a diagnostic written to legacy is laundered onto the shared Room
  leg on its first drain-and-requeue cycle. They become a third class, per-leg (D6). Final tally:
  **five originating sites target the Room, three are per-leg, five are legacy by rule, one is
  unmodified.**

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
- **Round-2 finding (now superseded)**: Unnecessary. `_handle_steering` already holds the resolved
  `room_id`; forwarding it as `_repush_messages(session_id, messages, room_id=room_id)` satisfies
  the issue's acceptance criterion with zero payload change and no per-message tag. A legacy-sourced
  sibling in that same drain is **upgraded** to the Room key, which is safe because all consumers
  dual-read both legs.
- **Round-4 reversal: a per-message *source-leg* tag IS necessary, and the round-2 reasoning was
  invalidated by D1.** The "upgrade is safe" argument was written when the flip was uniform — when
  every writer targeted the Room, promoting a legacy-sourced sibling changed nothing. D1 then made
  the flip selective, which makes the source leg **load-bearing**: an upgrade is now precisely the
  laundering D1 exists to prevent. Verified at round 4: `agent/health_check.py:514` drains both legs
  and re-pushes survivors at 530/536/560/564; `agent/session_runner/runner.py:587-601` drains both
  legs into `_pending_steers` and `_requeue_pending_steers` (616-635) re-pushes each through
  `_default_steering_push`; `agent/session_executor.py:1908-1939` does the same for
  `steering_msgs[1:]`. In all three, a watchdog or session-health diagnostic written to the legacy
  key lands on the shared Room key after one cycle — at *wedged* sessions, the case D1 names as
  most likely to die undrained.
- **What is added is a transient in-memory key, not a payload field.**
  `pop_all_steering_messages` stamps each returned dict with `_leg` (`"legacy"` or `"room"`). The
  three requeue writers read it and choose their target. `push_steering_message` never receives it
  and never persists it — the requeue writers build the payload from named fields (`text`,
  `sender`, `is_abort`, `target_agent`), so the JSON written to Redis stays byte-identical. The
  "no provenance field in the persisted payload" success criterion is unchanged and is now the
  thing that keeps this from becoming the payload-tagging design round 2 rejected. See **D6**.
- **Why not suppress by sender instead** (`room_id=None if msg["sender"] in {"watchdog", ...}`):
  a sender string is not a boundary. A diagnostic writer that renames its sender silently starts
  laundering, and no test would catch it. The source leg is the actual fact being preserved.
- **Confidence**: high
- **Impact on plan**: the return shape of `pop_all_steering_messages` gains one transient key; the
  five drain consumers still need no change (they read named fields; an extra key is inert — no
  test asserts exact dict equality on a drained message, checked at round 4). The per-message loop
  stays free of any lookup, which matters because `_repush_messages` runs inside a PostToolUse hook.
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
  the Room leg and only past `steering_room_max_age_s`, which no test-scoped message reaches.

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

1. **Classify the write at the call site (new).** Five sites are *conversation-level originating*
   writes and target the Room; three are *requeue* writes and target the source leg; five are
   legacy by rule; one is unmodified (Problem section's five-class table, spike-1b's `Target`
   column). An originating caller that holds an `AgentSession` computes
   `room_id_for_session(session)` — pure `getattr` plus string formatting
   (`models/room.py:65-100`), no Redis, no ORM query, no measurable cost. A legacy-by-rule caller
   passes an explicit `room_id=None` with the reason inline. A requeue caller derives a `room_id`
   the same way but applies it only to entries carrying `_leg == "room"` (D6).
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
6. **Drain (changed in that the Room leg is age-bounded and every entry is leg-stamped)** — the
   worker's turn-boundary `_default_steering_pop`, the watchdog hook's `_handle_steering`, and
   three other consumers each drain legacy-then-Room via `pop_all_steering_messages`, which now
   stamps each returned dict with a transient `_leg` (`"legacy"` or `"room"`). `_drain_list`
   discards **Room-key** entries older than `steering_room_max_age_s` (D5); the legacy key is never
   filtered, so every message that exists today behaves exactly as it does today. A surviving
   Room-key message is served to whichever session in that Room next **drains** — whichever comes
   first of a *session pickup* (`agent/session_pickup.py:224` `_drain_startup_steering`, which
   prepends the drained texts into a brand-new session's `message_text`) and a *turn boundary*
   (`_default_steering_pop`). In the durability scenario the pickup is usually first, because the
   successor session is created after the original target died. Both are deliveries and both are the
   durability property; `session_pickup.py` needs no edit. An earlier draft said "turn boundary"
   unqualified, which was wrong while the pickup path exists.
6a. **The terminal leftover drain reads the legacy leg only (changed, round 5).**
   `agent/session_executor.py:2465-2473` drains at session teardown and hands survivors to
   `_reenqueue_leftover_steering` (`:928`), which **spawns a continuation `AgentSession`**. It
   currently passes `room_id=room_id_for_session(agent_session) if agent_session else None`, which
   is inert today because the Room leg is empty; post-flip it would scoop the *shared* Room leg on
   every session teardown and convert an instruction — possibly aimed at a still-live sibling — into
   a new session. That is cross-delivery **plus** work amplification, strictly heavier than the
   recoverable wrong-recipient hazard D2 accepts.
   **The fix is one keyword: pass `room_id=None` at `agent/session_executor.py:2468`.** This drain
   exists to rescue *this session's own* unconsumed messages; a Room-leg message needs no rescuing,
   because durability is precisely what the Room key already provides — leaving it in place is the
   feature working, and the next pickup or turn boundary in that Room delivers it. It also avoids a
   fourth requeue writer. So **four** of the five dual-read consumers stay byte-unmodified and this
   one changes by a single keyword argument.
7. **Re-push (leg-preserving, D6)** — the three requeue writers put each survivor back on the leg
   it came from, reading the transient `_leg` and defaulting to legacy when it is absent, and carry
   the original `timestamp` forward so D5's clock is not restarted. So the
   abort-sibling re-push in `_handle_steering` returns Room-sourced siblings to the Room key and
   legacy-sourced siblings to the legacy key; the abort itself is consumed, never re-pushed, so
   this does not conflict with D4. `_leg` is never forwarded into `push_steering_message` and never
   reaches Redis — the persisted payload is byte-identical to today's.
8. **Output** — the steer reaches a live session instead of being stranded.

## Architectural Impact

- **New dependencies**: none. `agent/steering.py` gains **no** model-layer import — it never touches
  the model layer. It does gain a settings read for the D5 age bound
  (`config.settings` → `TimeoutSettings.steering_room_max_age_s`), which is config, not model.
  `models.room.room_id_for_session` is imported at the *flipped* calling modules, which mostly
  import it already (`agent/session_runner/runner.py`, `agent/session_executor.py`,
  `tools/valor_session.py`). One new importer: `bridge/telegram_bridge.py` — function-local with
  `# noqa: PLC0415` where the module already uses that pattern.
  **`monitoring/session_watchdog.py`, `agent/session_health.py`, `agent/output_handler.py` and
  `scripts/steer_child.py` gain no such import**: their writes stay legacy (D1/D4), and the absence
  of the import is a Verification anti-criterion.
- **Interface changes**: three signatures gain an optional `room_id: str | None = None` keyword —
  `agent/steering.py::push_steering_message`, `agent/health_check.py::_repush_messages`, and the
  thin helper `bridge/telegram_bridge.py::_ack_steering_routed`. `push_steering_message` gains a
  **second** keyword-only parameter, `timestamp: float | None = None`, so a requeue can carry the
  original stamp forward instead of restarting D5's clock (D5, round-5 blocker).
  Two private helpers gain an optional
  `max_age_seconds: float | None = None` — `agent/steering.py::_drain_list` and
  `agent/steering.py::_peek_list` (D5). `pop_all_steering_messages` keeps its signature but its
  **return shape gains one transient key**, `_leg`, on each dict (D6) — set by the reader, never
  persisted, inert to the five consumers, which read named fields.
  **`peek_steering_sender`, `pop_steering_message`, `has_steering_messages` and
  `monitoring/session_watchdog.py::_inject_watchdog_steer` are unchanged** — the earlier draft's
  peek-sender work is out of scope now that the drafter's push stays on the legacy key, and
  `pop_steering_message` / `has_steering_messages` both have zero production callers (see D5).
  All added keywords are backward-compatible and additive, but
  `tests/unit/test_public_api_contract.py:31-34` pins `push_steering_message`'s exact signature
  string and **must be updated** — see Test Impact.
- **New configuration**: one `TimeoutSettings` field, `steering_room_max_age_s`
  (env `TIMEOUTS__STEERING_ROOM_MAX_AGE_S`), with a default and a GRAIN-OF-SALT provisional-value
  comment. The `_s` suffix is the `TimeoutSettings` convention (`agent_session_retain_ttl_s`,
  `last_processed_ttl_s`, `dedup_record_ttl_s`), not `_seconds`. No new secret, no new config file.
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

Raised from Small at round 2, and the shape grew again at round 4 (a third writer class, a sixth
unsorted row selection) without crossing into Large: five originating flips, three per-leg requeue
writers, five explicit legacy sites, six pre-existing unsorted row selections to harden (Race 4), a
new `TimeoutSettings` field and drain-time age filter (D5), an abort-exclusion branch (D4), two AST
census tests, and a held-merge pipeline exit. `Small` also kept the plan off the force-FULL critique
triage path, which is the wrong signal for a change on the steering critical path.

**Team:** Solo dev, code reviewer

**Interactions:**

- PM check-ins: 1-2 (the fleet deploy gate is a human confirmation, and the selective-flip boundary
  — five sites deliberately legacy, three leg-preserving — is a design call that needs a nod)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | The steering integration suite runs against real Redis |
| On-pin venv | `.venv/bin/python -c "import sys,pathlib; assert '.'.join(map(str,sys.version_info[:2])) in pathlib.Path('.python-version').read_text()"` | `scripts/pytest-clean.sh` aborts on an off-pin venv (#2617) |

## Solution

### Decisions

Six decisions are settled; there is no Open Questions section left to answer. D1 was **reversed**
at round 2 and D4/D5 were added there. D6 was added at round 4, and it is what makes D1's boundary
actually hold.

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
  **Round-4 amendment: writing to the legacy key is not sufficient to keep a message there.** Three
  requeue writers re-push messages a dual-read drain took off either leg. Without D6 they would
  promote every legacy-sourced diagnostic to the Room on its first cycle, and D1 would be a
  statement about writers that the readers immediately undo.
- **D6 — a requeue writes to the leg it read from.** `agent/health_check.py::_repush_messages`,
  `agent/session_runner/runner.py::_default_steering_push` and `agent/session_executor.py:1933`
  are not originating writers: each re-pushes a message that
  `pop_all_steering_messages` already drained from *one specific leg*. Neither fixed target is
  correct. Sending everything to the Room launders the diagnostics D1 keeps legacy — at wedged
  sessions, the ones D1 names as most likely to die undrained. Sending everything to legacy demotes
  Room-sourced messages onto a mortal key on their first requeue, destroying the durability
  property in exactly the scenario the feature exists for (a session drains the Room, requeues, and
  dies before its next turn).
  **Mechanism:** `pop_all_steering_messages` stamps each returned dict with a transient
  `_leg` (`"legacy"` or `"room"`); each requeue writer passes
  `room_id=room_id if msg.get("_leg") == "room" else None` **and**
  `timestamp=msg.get("timestamp")` (D5's origination-age rule — a requeue must not restart the
  clock). **`_leg` is never forwarded into
  `push_steering_message` and never reaches Redis** — the requeue writers construct the payload
  from named fields, so the persisted JSON is byte-identical to today's and the
  no-provenance-field success criterion still holds and is now load-bearing.
  **Two of the three forward `target_agent` today; the runner does not
  (`agent/session_runner/runner.py:610-615`) and silently strips it.** Task 3 adds it in the same
  edit, so "rebuilt from named fields (`text`, `sender`, `is_abort`, `target_agent`)" is true of all
  three after this release rather than of two of them.
  **Absent `_leg` → legacy**, the same fail-safe default as absent `room_id`: an untagged message
  (a hand-built dict in a test, a future caller) can never be laundered, only left where it is.
  This **reverses spike-2's** "no per-message tag needed" conclusion, which was reasoned under the
  uniform flip: when every writer targeted the Room, the source leg carried no information. D1 made
  it load-bearing. Suppressing by sender string instead was considered and rejected — a sender is
  not a boundary, and a writer that renames its sender would silently start laundering.
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
  3. **MERGE is never dispatched, because DOCS is left `in_progress`.** This is the pipeline exit.
     No marker on MERGE itself can carry the hold: `agent/sdlc_router.py` has zero draft awareness
     (`grep -i draft agent/sdlc_router.py` returns nothing), and it never consults the MERGE marker
     when choosing to dispatch `/do-merge`. G3 (`:381-383`) routes `/do-merge` whenever REVIEW
     **and** DOCS are `completed`; G6 (`:723-727`) fast-paths to it on the same DOCS-done plus
     `REVIEW_APPROVED` condition. Leaving MERGE at `pending` therefore holds nothing — the router
     dispatches `/do-merge`, the merge predicate refuses fail-closed on the draft
     (`tools/merge_predicate.py:465-466`), nothing advances, and the run loops until
     `MAX_SAME_STAGE_DISPATCHES = 3` (`:86`) wedges it in G4 demanding an operator
     `sdlc-tool dispatch reset`.
     The marker that actually arms the merge dispatch is **DOCS**, so that is the one that carries
     the hold: Task 8 performs every documentation edit and then records DOCS `in_progress`, not
     `completed`. With DOCS short of `completed`, G3 falls through to its `else` branch and routes
     `/do-pr-review`, which is idempotent against a draft PR and advances without wedging. Neither
     DOCS nor MERGE can be recorded `skipped` (`agent/pipeline_state.py:83` is
     `frozenset({"PLAN", "CRITIQUE"})` and `tools/sdlc_stage_marker.py:322-326` refuses the write
     with `STAGE_NOT_SKIPPABLE`, because each is a gate the merge predicate reads), so the remaining
     action is carried by a follow-up operator issue rather than by a stage marker. Task 8 spells
     this out.

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
  `_drain_list` drops **Room-key** entries older than `steering_room_max_age_s`, logging each
  drop with the key and the age so a missing steer stays diagnosable. The **legacy key is never
  filtered**, so every message that exists today behaves exactly as it does today. The bound is a
  named env-overridable `TimeoutSettings` field, not a literal; the default is provisional and
  tunable. Drain-time filtering, not a background reaper — see Rabbit Holes.

  **D5 bounds age since origination, not age since last push (settled at round 5).** This is a
  decision, not an implementation detail, because D6 makes the two diverge. `push_steering_message`
  stamps `"timestamp": time.time()` unconditionally (`agent/steering.py:115-120`) and all three
  requeue writers rebuild the payload from named fields (`text`, `sender`, `is_abort`,
  `target_agent`) — so as written, every requeue restarts the clock. That is not an edge case:
  `agent/health_check.py:553-564` re-pushes every drained message on the ordinary non-abort
  PostToolUse path, so the reset would be the *dominant* path and a message that is repeatedly
  drained-and-requeued without ever being injected would stay exactly as immortal as it was before
  D5. The bound would then measure something no one cares about.
  **Mechanism:** `push_steering_message` gains a second keyword-only parameter,
  `timestamp: float | None = None`, and writes `"timestamp": time.time() if timestamp is None else
  timestamp`. The three requeue writers forward `timestamp=msg.get("timestamp")`; every
  *originating* caller passes nothing and gets `time.time()` as today. The persisted key set is
  unchanged, so the byte-identity criterion still holds. Two consequences the builder must carry:
  (a) `tests/unit/test_public_api_contract.py` pins the exact signature string and must gain **both**
  `room_id` and `timestamp` in one edit; (b) the census test's "explicit `room_id` keyword" rule is
  **not** widened to `timestamp` — an originating caller passing a `timestamp` would be a bug.

  **The bound applies to every Room-leg reader that has a production caller.**
  `agent/steering.py` has three Room-aware read paths; two are wired, and the third is scoped out
  by the same zero-production-callers rule that scopes out `pop_steering_message`:

  | Reader | Room-leg mechanism | Treatment |
  |---|---|---|
  | `pop_all_steering_messages` (138) | `_drain_list` | filtered — this is the drain |
  | `peek_steering_messages` (208) | `_peek_list` (LRANGE) | **filtered too** — `_peek_list` gains the same `max_age_seconds`, applied on the Room key only. Non-destructive: it *skips* stale entries, it does not delete them (the next drain does that). |
  | `has_steering_messages` (200) | raw `llen` on both legs | **unchanged, deliberately** — see below |

  `peek_steering_messages` is what `valor-session status` calls
  (`tools/valor_session.py:1031`) — the one operator-visible surface this plan pins as must-keep-
  working. Leaving it unfiltered would make `status` report pending Room steers that the very next
  drain silently discards, which is the opposite of "a missing steer stays diagnosable". Note the
  anti-criterion is on the **call site** at `tools/valor_session.py:1031` (it already passes a
  `room_id` and needs no edit), not on `peek_steering_messages` itself.

  **Two functions are explicitly out of scope, by the same rule.** Both have **zero production
  callers**, verified at round 4 by a repo-wide grep excluding `tests/`:
  - `pop_steering_message` (164) — only its definition and the `agent/__init__.py` re-export at
    41/73. It also does not call `_drain_list`; it inlines its own `r.lpop` + `json.loads` loop, so
    the parameter could not reach it without a refactor.
  - `has_steering_messages` (200) — only its definition and the `agent/__init__.py` re-export at
    38/76. An earlier draft rewrote its Room leg from an O(1) `r.llen` into an O(N) `_peek_list`
    that LRANGEs the whole list and `json.loads` every entry, justified by "the operator surface".
    That justification was wrong: `tools/valor_session.py:1031` calls `peek_steering_messages`,
    never `has_steering_messages`. Applying the plan's own exclusion rule consistently, it keeps
    its `llen` fast path and gains nothing. Its docstring records that it is deliberately unbounded
    and that a future caller wanting the bound should route through `peek_steering_messages`.

  **`peek_steering_sender` (223) also gains nothing** — it reads the legacy leg only (D1), which is
  never filtered.

### Key Elements

- **Room derivation at the call site, never inside the writer.** `push_steering_message` gains an
  optional `room_id` and does nothing but choose a key from it. It performs no lookup. This is
  the single most important shape decision in the plan: an internal
  `AgentSession.query.filter(session_id=...)` resolution measures ~2.4s per call (spike-5) on a
  path that includes the inbound-Telegram fast path.
- **The flip is selective, and the boundary is the design.** Five conversation-level originating
  sites derive a Room; three requeue sites derive one but apply it per-leg (D6); five deliberately
  pass `room_id=None` — three session-scoped diagnostics (D1), one unconditional abort (D4), one
  site that holds no session row at all (`bridge/telegram_bridge.py:2070`); and the ORM-free
  migration script is not touched. Every non-test caller passes the keyword **explicitly**, with
  the reason inline where it is `None`. Uniformity is not the goal; delivering each message to a
  session for which it is meaningful is.
- **A requeue is not an origination.** The three writers that re-push already-drained messages
  preserve the source leg via a transient `_leg` stamp (D6). Getting this wrong in either direction
  is a silent failure: uniformly-Room launders diagnostics past D1, uniformly-legacy destroys the
  durability property on the first requeue. This is the finding that reversed spike-2.
- **`room_id_for_session(session)` is free** (pure attribute reads, `models/room.py:65-100`), which
  is what makes caller-side derivation viable at all.
- **Derive only from a row known to be live.** Where the caller picked from a multi-row
  `filter(session_id=...)`, it materializes and sorts newest-first by `created_at` before
  selecting; where it holds no row, it passes `None` rather than fabricating one. Race 4 has the
  six sites — five in `bridge/telegram_bridge.py` and one in `agent/health_check.py:621`, the one
  that feeds the requeue target key — and the measured evidence that this is a *fix to pre-existing
  code*, in scope because this plan is what makes a wrong row selection load-bearing.
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
`steering_room_max_age_s` → **the steer is delivered**. That last hop is the durability
property, and the end-to-end test drives it through `_default_steering_pop` rather than
re-implementing the derivation (see Failure Path Test Strategy).

**Legacy branches**, all landing on `steering:{session_id}` and drained by the same dual-read
consumers exactly as today:

- the caller has no session row, or the session has no `project_key`, so `room_id` is `None`;
- the message is an abort, explicit or auto-detected (D4) — it dies with its target, by design;
- the write is a session-scoped diagnostic that passes `room_id=None` on purpose (D1);
- the write is a **requeue of a legacy-sourced message** — `_leg` is `"legacy"` or absent, so the
  requeue writer passes `room_id=None` (D6).

**Requeue branch (durable):** a Room-sourced message drained by a session that then wedges or dies
before injecting it is re-pushed with `_leg == "room"` → back to the Room key → still durable. This
is what keeps the durability property alive across a drain-and-requeue cycle rather than losing it
on the first one.

**Expiry branch:** a Room-key entry no session *consumed* inside the age bound is dropped at the next
drain with an `info` log naming the key and the age (D5), rather than waiting immortally for a
session that may be days newer than the instruction. The age measured is **time since origination**,
not time since last push: a requeue forwards the original `timestamp` (D5/D6), so a message that is
repeatedly drained-and-requeued without ever being injected still expires on schedule.

### Technical Approach

- **`agent/steering.py::push_steering_message`** — add `room_id: str | None = None` **and**
  `timestamp: float | None = None` (D5: a requeue must carry the original stamp forward or the age
  bound measures nothing). Change the payload line to
  `"timestamp": time.time() if timestamp is None else timestamp` — the key set is unchanged, so the
  byte-identity criterion holds. **Delete**
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
  `pop_all_steering_messages` passes it to `_drain_list` and `peek_steering_messages` passes it to
  `_peek_list` — in both cases **only** on the Room key, read from
  `TimeoutSettings.steering_room_max_age_s` rather than a literal so tests can vary it.
  **`pop_steering_message`, `has_steering_messages` and `peek_steering_sender` are untouched** —
  the first two have zero production callers (D5), and the third reads the legacy leg only, which
  is never filtered.
- **`agent/steering.py::pop_all_steering_messages`** — stamp each returned dict with a transient
  `_leg` before returning: `"legacy"` for entries from `_drain_list(_queue_key(session_id))`,
  `"room"` for entries from the Room drain (D6). Set it in this one function; do **not** set it in
  `_drain_list` (which does not know which leg it was handed) and do **not** set it in
  `_peek_list` (nothing requeues from a peek). Docstring it as transient and reader-set: it is
  never accepted by, or forwarded to, `push_steering_message`.

- **Flip table (originating writes)** — each site passes
  `room_id=room_id_for_session(<the session object in scope>)`:

  | File:line | Session object | Edit |
  |---|---|---|
  | `bridge/telegram_bridge.py:979` `_ack_steering_routed` | via 4 of 5 callers (1809, 1835, 1868, 2178) | add pass-through `room_id` param; each of those four derives from its **sorted** row |
  | `bridge/telegram_bridge.py:2617` | `session` (2593-2594) | sort before `[0]`, then pass `room_id` |
  | `bridge/telegram_bridge.py:2650` | `active_edit` (2641-2646) | sort `edit_sessions` before the `next(...)`, then pass `room_id` |
  | `tools/valor_session.py:856` | `session` | pass `room_id` (resume path only; the status peek at 1031 is untouched) |
  | `agent/session_executor.py:853` | `session` | pass `room_id` (`steer_session`, an originating PM steer) |

- **Requeue table (D6)** — each site derives a `room_id` exactly like a flipped site, then applies
  it **per message**: `room_id=room_id if msg.get("_leg") == "room" else None`. Absent `_leg` →
  legacy. Each also passes `timestamp=msg.get("timestamp")` so the requeue does not restart D5's
  clock (an absent/`None` timestamp falls back to `time.time()` inside the writer):

  | File:line | Room source | Drain it re-pushes from |
  |---|---|---|
  | `agent/health_check.py:480` `_repush_messages` | `room_id` parameter, forwarded from `_handle_steering` (already resolved) | `pop_all_steering_messages` at 514 |
  | `agent/session_runner/runner.py:610` `_default_steering_push` | `room_id_for_session(self._agent_session)`, mirroring `_default_steering_pop` at 599-601 | `_default_steering_pop` → `_pending_steers` → `_requeue_pending_steers` (616-635) |
  | `agent/session_executor.py:1933` (`_push_steering` alias, imported 1930) | `room_id_for_session(agent_session)` — already computed at 1906-1909 for the drain; reuse it, do not recompute | `_pop_all_steering` at 1908-1910, re-pushing `steering_msgs[1:]` |

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

- **`agent/session_executor.py:2468` — the terminal leftover drain reads legacy only.** Change
  `room_id=room_id_for_session(agent_session) if agent_session else None` to `room_id=None`, with an
  inline comment. This is the **only** edit to any of the five dual-read consumers, and it is one
  keyword. Rationale in Data Flow step 6a: that drain feeds `_reenqueue_leftover_steering`, which
  *spawns a continuation session*, so post-flip it would convert a shared-Room instruction into new
  work at every session teardown. A Room-leg message needs no rescuing; the next pickup or turn
  boundary in that Room delivers it.
- **`agent/session_pickup.py:224` `_drain_startup_steering` — no change.** It legitimately drains
  the Room leg at session creation and prepends the text into the new session's `message_text`.
  Post-flip that becomes the *primary* delivery path for the durability property, and it already
  works. Named here only because the plan previously described drains as happening at turn
  boundaries, which was incomplete.
- **`agent/health_check.py::_repush_messages`** — add `room_id: str | None = None` and, **inside
  the per-message loop**, forward `room_id if msg.get("_leg") == "room" else None` (D6), not a bare
  `room_id`. `_handle_steering` passes its own already-resolved `room_id` at **all four** call
  sites: 530, 536, 560, 564. Two of those (536, 564) are retries inside `except` blocks; missing
  them silently demotes to legacy. The re-pushed siblings are non-abort by construction (the abort
  is consumed, not re-pushed), so this does not conflict with D4 — but do not "helpfully" re-push
  the abort.
- **`agent/health_check.py:619-627`** — the block that produces the `room_id` all of the above
  depends on. Materialize and sort before `sessions[0]` (Race 4, sixth site). This is the one
  unsorted selection with **no status filter**, so a `superseded` row genuinely can be selected;
  and it sits inside a `try:`/`except Exception` at 620/628 that only `logger.debug`s, so both
  failure modes here are silent (a wrong Room, or an `AttributeError` that leaves
  `steering_room_id = None` and demotes the whole hook to a legacy-only drain *and* legacy-only
  re-push).
- **`agent/steering.py::peek_steering_sender` and `agent/output_handler.py:1160-1162` — no change.**
  The drafter's push stays legacy (D1), so the single-leg `LINDEX` still observes it. This is
  checked as a diff anti-criterion.
- **`config/settings.py`** — one new field, `steering_room_max_age_s`, inside **`TimeoutSettings`**
  (the class opens at line 184 and ends at 452), with the `.env.example` placeholder and its
  required comment line (D5). Place it beside the other TTL-shaped fields
  (`agent_session_retain_ttl_s`, `last_processed_ttl_s`, `dedup_record_ttl_s`) and copy the
  GRAIN-OF-SALT description style from `dedup_record_ttl_s` / `catchup_disabled_warn_hours`, which
  are in the same class. **Not `FeatureSettings`** — `bridge_msg_claim_ttl_seconds`, cited in an
  earlier draft as "the neighbouring field", is on `FeatureSettings` at line 776; landing there
  would make `TIMEOUTS__STEERING_ROOM_MAX_AGE_S` resolve to nothing while the `.env.example`
  completeness test still passed, i.e. a silently dead env override.
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
- [ ] `room_id_for_session` is called at new sites in the bridge and the runner. It is total for any
  object (`getattr`-based, returns `None` without a `project_key`), so no caller needs a guard. Pin
  with a test passing an object with no `project_key` attribute at all.
- [ ] **A requeue writer must not raise on a message with no `_leg`.** `msg.get("_leg") == "room"`
  is total over any dict, and an absent key resolves to legacy — the fail-safe direction (D6). Pin
  with a hand-built dict (no `_leg`) through `_repush_messages` and through
  `_default_steering_push`, asserting the legacy key.
- [ ] The age filter in **both** `_drain_list` and `_peek_list` must not raise on a malformed entry.
  A payload with a missing or non-numeric `timestamp` must be **kept**, not dropped/skipped and not
  crashed on — failing open loses nothing, while failing closed silently deletes steers. Pin with a
  Room-leg entry whose `timestamp` key is absent, through both functions.
- [ ] `.sort()` must be called on a materialized list, never on a `QueryBuilder`. The three bridge
  sites at 1801/1860/2169 have no enclosing `try`/`except`, so an `AttributeError` there is an
  unhandled crash on the inbound-Telegram steering path — the exact regression the round-3 blocker
  named. The sixth site, `agent/health_check.py:621`, is the mirror-image hazard: it *is* inside a
  swallow-all `except Exception` (620/628), so the same mistake there is silent, leaves
  `steering_room_id = None`, and demotes the PostToolUse hook to legacy-only. Pinned structurally by
  `test_room_derivation_sites_sort_before_selecting`'s materialization assertion (including its
  synthetic-negative self-check), because no runtime test in this repo drives those branches end to
  end.
- [ ] The sort key must be total over a `None` `created_at`. **It is not `created_at or 0`.**
  `created_at` is `SortedField(type=datetime, partition_by="project_key")`
  (`models/agent_session.py:163`), so `or 0` substitutes an `int` into a list of `datetime`s and a
  `None` row beside a populated one raises
  `TypeError: '<' not supported between instances of 'int' and 'datetime.datetime'` — reproduced in
  the venv at round 4. Use `key=lambda s: (s.created_at is not None, s.created_at)`: total, needs no
  import, and never compares `None` to a `datetime` (tuple comparison stops at the first element
  when the flags differ, and is short-circuited by equality when both are `None`). Pin it with a
  two-row list, one `created_at=None` and one populated, asserting the populated row sorts first
  under `reverse=True` and that no exception is raised. A single-row list does not exercise the
  comparison and would pass on the broken idiom.

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
  `steering_room_max_age_s` must NOT deliver (D5), and the drop must be logged.
- [ ] **Requeue twin (D6)** — session B drains the Room-sourced steer, wedges without injecting it,
  and `_requeue_pending_steers` fires. Assert the message is back on the **Room** key, not the
  legacy one, so a second sibling session C still receives it. This is the durability property
  surviving a drain-and-requeue cycle, and it is the half that a uniformly-legacy requeue would
  break.
- [ ] **Anti-laundering twin (D6)** — a diagnostic pushed to the **legacy** key (as
  `monitoring/session_watchdog.py:557` does) is drained by `_handle_steering` alongside an abort,
  re-pushed via `_repush_messages`, and must land back on the **legacy** key even though
  `_handle_steering` holds a truthy `room_id`. This is the blocker-2 regression test; without D6 it
  lands on the shared Room key.

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
- [ ] `tests/unit/test_public_api_contract.py:31-34` — **UPDATE (found at round 4; the plan
  previously missed this and the build would have gone red).** The module pins
  `push_steering_message`'s exact signature string:
  `"(session_id: 'str', text: 'str', sender: 'str', is_abort: 'bool' = False, target_agent: 'str |
  None' = None, front: 'bool' = False) -> 'None'"`. Adding `room_id` breaks the equality — and so
  does `timestamp`, so **both** must land in the same edit (D5's origination-age rule). Extend
  the pinned string with both new keywords and add the same DELIBERATE-change comment the neighbouring
  `pop_all_steering_messages` entry (line 35-40) carries, citing this plan. `pop_all_steering_messages`'s
  own pinned *signature* is unchanged by D6 — the `_leg` stamp changes the return **shape**, not the
  annotation `-> 'list[dict]'` — but check whether the module also asserts anything about return
  contents before assuming no second edit.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` — no change: it AST-walks for
  `push_steering_message` call/dedup pairing, which the added keyword does not affect. It is also
  the pattern the new census test copies.
- [ ] Tests that exercise `bridge/telegram_bridge.py::_ack_steering_routed` — VERIFY: it gains an
  optional keyword, so positional callers are unaffected, but any `autospec` mock must be
  re-checked. `monitoring/session_watchdog.py::_inject_watchdog_steer` gains **no** parameter, so
  its tests are untouched.
- [ ] Tests that assert on `bridge/telegram_bridge.py` session selection at 1803, 1864, 2174, 2594,
  2642 and on `agent/health_check.py:623` — VERIFY: inserting a newest-first sort changes *which*
  row is selected when more than one row matches. Any test relying on insertion order to pick a row
  is asserting the bug; update it to assert newest-first.
- [ ] `tests/integration/test_steering.py` tests that assert on the *contents* of a
  `pop_all_steering_messages` result — VERIFY: D6 adds a transient `_leg` key to each returned dict.
  Checked at round 4: no test in the repo asserts exact dict equality or a `.keys()` set on a
  drained message, so this is expected to be a no-op. If one turns red, assert on the named fields
  rather than deleting `_leg`.
- [ ] `tests/unit/` config tests covering `TimeoutSettings` field coverage or `.env.example`
  completeness — VERIFY: a new field plus its placeholder must keep those green.
- [ ] New test additions go into `tests/integration/test_steering.py`: the durability property test
  driven through `_default_steering_pop` plus its negative and staleness twins, the superseded-row
  safety test, the legacy-fallback matrix, the abort matrix (explicit and every auto-detected
  keyword), the Room-write happy path, sibling re-push on all four `_repush_messages` paths
  including the two retries, payload-shape invariance, the `_drain_list` age-filter cases
  (expired dropped, fresh kept, malformed kept), and the D5 origination-age set (a requeue preserves
  the original `timestamp`; a backdated entry still expires after a drain-and-requeue cycle).
- [ ] **New file `tests/unit/test_steering_writer_census.py`** — the two AST census tests
  (`test_every_flipped_writer_passes_room_id`, `test_room_derivation_sites_sort_before_selecting`)
  plus the `_repush_messages` keyword assertion. They are pure `ast.parse` walks with no Redis
  dependency, so they belong in `tests/unit/` beside the pattern they copy
  (`tests/unit/test_bridge_dispatch_contract.py`); putting them behind the integration suite's Redis
  prerequisite would make two structural invariants unrunnable without Redis. All four Verification
  rows that anchor them by node id use this path.
- [ ] `tests/integration/test_reply_delivery.py` — VERIFY: it exercises the terminal leftover drain
  at `agent/session_executor.py:2465-2473`, which changes to `room_id=None`. Every case there
  pushes without a `room_id`, so the legacy leg is what it already asserts on; expected no-op.

## Rabbit Holes

- **Persisting provenance in the payload.** D6 needs the *source leg*, and it gets it from a
  transient `_leg` key that `pop_all_steering_messages` stamps on the dicts it returns. That is the
  whole mechanism. Do **not** add a provenance field to the JSON written to Redis, do not accept
  `_leg` as a `push_steering_message` parameter, and do not touch the five consumers' unpacking.
  The persisted payload stays byte-identical, and a Verification row checks it.
- **Hardening the three drop-on-failure Room-leg drains.** `agent/session_pickup.py:224`,
  `agent/health_check.py:536`/`:564` and `agent/session_runner/runner.py:631-636` each lose drained
  messages when a subsequent operation raises. Post-flip that loss can hit another session's steers
  (Race 2, accepted for the soak). Fixing it means editing three consumers this release deliberately
  leaves byte-unmodified except for one keyword; do it as its own change if the soak produces a real
  loss, starting with the pickup drain.
- **Auditing the repo's other `created_at or 0` sorts.** This plan retires that idiom at its own six
  sites because it is unsound for a `datetime` `SortedField` (round-4 repro: `TypeError` comparing
  `int` to `datetime`). There are 26 further uses repo-wide, each a latent instance of the same
  bug — but only where a `None` row can coexist with a populated one in a single sort. Auditing them
  is a separate issue; do not sweep them here.
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
**Mitigation (corrected at round 4 — the previous wording claimed more than the design delivered).**
The previous draft said this was "structurally impossible" because the repo's only `front=True` push
is `agent/session_health.py:3473` and that writer passes `room_id=None` (D1). **That reasoning was
incomplete**: the message it writes to the legacy key is later drained by `_handle_steering` or
`_default_steering_pop` and re-pushed by a requeue writer, and the requeue writers do not carry
`front` forward — they RPUSH. Under the previous (uniform) requeue design that RPUSH targeted the
Room key, so the `front=True` message reached a shared Room leg after all, one cycle later.

What actually holds it, post-D6: the message is written with `_leg` absent from Redis and stamped
`"legacy"` on drain, so every requeue puts it back on the legacy leg. `front` therefore remains a
within-legacy-leg contract, and the Room leg never receives it. Two independent guards: the
anti-criterion that `session_health.py` gains no `room_id_for_session` import (it never originates a
Room write), and D6's leg preservation (it is never promoted to one).

**A separate, pre-existing property, stated so it is not mistaken for a regression:** a requeue
already loses `front` today — `_repush_messages` and `_default_steering_push` RPUSH regardless — so
a `front=True` message that survives a drain-and-requeue is no longer at the front of even the
legacy leg. That is current behavior on `main`, this plan neither introduces nor fixes it, and it is
out of scope. No new `front=True` push may target a Room without first resolving the cross-leg
ordering question; that constraint is recorded in the `front` docstring as part of Task 5.

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
the message is delivered exactly once, never duplicated — for an instruction the ordinary hazard is
*which* session receives it, and a misrouted instruction is recoverable by re-steering.
**Loss is also possible on the Room leg, and it is accepted for the soak — explicitly, not by
omission.** The drain is destructive and three consumers depend on a subsequent operation to survive:
`agent/health_check.py:536`/`:564` re-push inside an `except` handler with no guard of their own, so
a raising retry loses everything that drain took; `agent/session_pickup.py:224` drains, then mutates
`message_text` and `async_save`s, and its `except` at `:241` only logs a warning (it also discards
any drained entry whose text is blank, `:227`); `agent/session_runner/runner.py:631-636` catches per
message and logs `"failed to re-push pending steer (%r dropped)"` — an explicit drop. Post-flip the
blast radius of each widens from *this session's own* legacy key to the shared Room key, so an
unrelated session erroring mid-hook can delete a steer addressed to another. **Rationale for
accepting:** all three are pre-existing best-effort paths that already drop on failure; the flip
widens who is affected but adds no new failure mode, and every drop is logged at `warning`/`error`
with the message text, so a lost instruction is diagnosable and recoverable by re-steering — the
same trade D2 makes. Hardening them (moving the pickup drain after a successful save, guarding the
health-check retries) is a change to three consumers this release otherwise leaves byte-unmodified;
it is recorded as a Rabbit Hole and is the first thing to do if the soak produces one real loss.
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
**Trigger (sharpened at round 4 — the previous single trigger did not apply at half the sites).**
`models/agent_session.py:130` documents `superseded` as "Replaced by a newer session for the same
`session_id`". A superseded row can carry a different `chat_id` (→ a different addressee → a Room
the live session never drains) or no `project_key` (→ silent legacy fallback that looks like a
Room-less session but is a bug). But `superseded` **is itself a status value**
(`models/session_lifecycle.py:79`), so it cannot appear in a result set that filters on status:

| Site | Status filter | Applicable trigger |
|---|---|---|
| `bridge/telegram_bridge.py:1801` | `("running", "active")` | not superseded — **two concurrently-live rows** for one `session_id` |
| `bridge/telegram_bridge.py:1860` | `("pending", "running", "active")` | same |
| `bridge/telegram_bridge.py:2169` | `("running", "active", "pending")` | same |
| `bridge/telegram_bridge.py:2593` | **none** | superseded row, as described above |
| `bridge/telegram_bridge.py:2641` | none on the query; the `next(...)` filters in Python | superseded row |
| `agent/health_check.py:621` | **none** | superseded row |

For the three status-filtered sites the hazard is a multi-row live result, which the codebase does
not otherwise rule out but which is not directly observed. **Two honest caveats on what the sort
buys there:** (a) each of those three sites wraps its query in a `for check_status in (...)` loop
that `break`s on the first status returning any row, so a row in an earlier status bucket beats a
newer row in a later one regardless of the sort — the sort is a within-bucket tie-break only; (b)
the materialization is therefore the load-bearing half of the edit at those sites, and the sort is
defense in depth. The Success Criterion is worded accordingly. The three unfiltered sites are where
the superseded-row hazard is real, and `agent/health_check.py:621` is the worst of them because its
output is the requeue target key.
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

1. **Materialize, then sort, at all six sites.** `AgentSession.query.filter(...)` does **not**
   return a list — it returns a `popoto.models.query.QueryBuilder`, which supports `__getitem__`
   (hence the working `[0]`) but has **no `.sort` attribute**. Measured on the baseline:
   `type(AgentSession.query.filter(session_id='x'))` is `<class 'popoto.models.query.QueryBuilder'>`
   and `hasattr(q, 'sort')` is `False`. Four of the six sites bind the bare QueryBuilder —
   `sessions` at **1801**, `_live` at **1860**, `sessions` at **2169**, and `sessions` at
   **`agent/health_check.py:621`** — so a bare `sessions.sort(...)` there raises `AttributeError`.
   Only 2593 and 2641 already wrap in `list(...)`.

   So the edit is two steps, in this order, at each site:

   ```python
   sessions = list(AgentSession.query.filter(session_id=session_id, status=check_status))  # 1801
   sessions.sort(key=lambda s: (s.created_at is not None, s.created_at), reverse=True)
   matching_session = sessions[0]                                                          # 1803
   ```

   This is a *fix to pre-existing code*, in scope precisely because this plan is what makes a wrong
   row selection load-bearing.

   **The sort key is deliberately NOT the repo's `created_at or 0` idiom.** Round 4 reproduced why:
   `created_at` is `SortedField(type=datetime, partition_by="project_key")`
   (`models/agent_session.py:163`), so `or 0` substitutes an `int` and a `None` row beside a
   populated one raises `TypeError: '<' not supported between instances of 'int' and
   'datetime.datetime'`. The idiom has 26 further uses repo-wide and each is a latent instance of
   the same bug; sweeping them is out of scope (Rabbit Holes). The tuple key is total, needs no
   import, and never compares `None` to a `datetime`.

   **Why the `list(...)` is not cosmetic.** At 1801, 1860 and 2169 the selection sits in the
   inbound-Telegram steering path with **no enclosing `try`/`except`** around the query, so an
   `AttributeError` propagates straight into the Telethon handler and the steer is lost with a
   traceback. At 2593 it would be *worse than a crash*: the query sits inside `try:` with
   `except Exception` at 2595 setting `session = None`, so a missing `list(...)` there would be
   swallowed and silently disable edit-steering entirely. `agent/health_check.py:621` is the same
   silent shape — `try:` at 620, `except Exception` at 628 that only `logger.debug`s — and it is the
   worst instance, because a swallowed failure there leaves `steering_room_id = None` and demotes
   the PostToolUse hook to a legacy-only drain **and** a legacy-only re-push, i.e. the Room leg is
   simply never served on that path. Neither failure is caught by a text-matching guard, which is
   why the sort census test below asserts the **materialization**, not the presence of a sort call.
2. **Pass `room_id=None` where the live row cannot be identified.** `bridge/telegram_bridge.py:2070`
   holds no session row at all (2058-2067 bind `guard_sessions` and test only truthiness). A builder
   working the caller table mechanically would invent `guard_sessions[0]` there and reintroduce the
   defect. The rule is explicit in Task 2: **no session object in hand → `room_id=None`, never a
   fabricated selection.**
3. **The anti-criterion must be positive, not a diff grep — and the materialization guard must be
   an AST test, not a grep.** `git diff origin/main | grep -c "^+.*filter(session_id=" == 0` cannot
   catch any of this: every offending query already exists on `main`, so no `+` line appears.
   Round 2 replaced it with two positive grep rows. Round 4 deleted the second of those,
   `grep -c "list(AgentSession.query.filter" >= 6`, because **it is unsatisfiable by a correctly
   formatted build**: `bridge/telegram_bridge.py:1801` is already exactly 100 characters — the
   repo's `line-length` — so wrapping it in `list(...)` makes it 106 and `ruff format` splits it
   into `sessions = list(` / `    AgentSession.query.filter(...)` / `)`, which the substring grep
   cannot see. Reproduced at round 4 by running `ruff format --line-length 100` on the plan's own
   mandated line at that indentation. The same file already contains the wrapped shape at 2059-2061
   and 2065-2067, which is why those two are absent from the baseline of 3. A substring grep cannot
   pin a call shape the formatter is free to rewrite.

   What remains is a **sort-key** grep row (now `s.created_at is not None`, baseline 0) plus the AST
   test `test_room_derivation_sites_sort_before_selecting` as the **sole materialization guard** —
   which is the right instrument anyway: it asserts the sorted name is bound to an `ast.Call` to
   `list`, which is wrap-insensitive and covers exactly what the deleted grep was reaching for. The
   grep row alone is insufficient (it matches on code that raises `AttributeError` at runtime),
   which is why the AST test carries a synthetic-negative self-check. The success criterion is
   restated as "no caller *derives a Room from* an unsorted row selection", not "introduces one".

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
  DOCS is left `in_progress` after the doc edits land, so the router never arms its merge dispatch
  and MERGE is never dispatched (D3). Removal owner: Valor Engels (repo operator). The PR body must carry the removal condition
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

One new **optional** env key: `TIMEOUTS__STEERING_ROOM_MAX_AGE_S`, backing
`TimeoutSettings.steering_room_max_age_s` (default 21600). It needs no vault entry and no
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
  five-class table verbatim — "writers now target the Room key" is a new falsehood if written
  unqualified, and so is any description that omits the requeue class. State the D6 rule
  explicitly: a requeue writes to the leg it read from, so which leg a steer sits on is stable
  across drain-and-requeue cycles. Add a subsection documenting the same-Room delivery semantics
  from Risk 2 (a steer
  may be served to a different session in the same Room; the `system` addressee groups all chatless
  sessions of a project) and the Room-leg age bound from D5, naming
  `TIMEOUTS__STEERING_ROOM_MAX_AGE_S` and its default.
- [ ] Update `docs/plans/durability-room-job-agentrun.md` **line 673** (an earlier draft cited
  "~655") — move "the steering writer flip" out of the "Remaining, each its own release" list and
  record it as shipped *with its selective boundary*, leaving phase 2 and phase 3 as the remainder.
  Plan-doc edits commit on main.
- [ ] Add `TIMEOUTS__STEERING_ROOM_MAX_AGE_S` to
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
  (skips, never deletes), and state at both Room-leg call sites — `pop_all_steering_messages` and
  `peek_steering_messages` — why it is passed for the Room key and never for the legacy key (D5).
  Note in `pop_steering_message`'s **and** `has_steering_messages`'s docstrings that each is
  deliberately unbounded because it has no production callers, so a future caller knows to route
  through `pop_all_steering_messages` / `peek_steering_messages` instead.
- [ ] Docstring the transient `_leg` key on `pop_all_steering_messages` (D6): reader-set, values
  `"legacy"` / `"room"`, never accepted by or forwarded to `push_steering_message`, never persisted.
  State the requeue contract in the three requeue writers' docstrings —
  `agent/health_check.py::_repush_messages`, `agent/session_runner/runner.py::_default_steering_push`,
  and an inline comment at `agent/session_executor.py:1933` — including the "absent `_leg` → legacy"
  fail-safe default.

## Success Criteria

- [ ] **The durability property holds end to end:** a steer written for session A is delivered to
  a different session B serving the same Room, after A is gone. Asserted by
  `test_steer_survives_target_session_and_reaches_room_sibling`, with a `room_id=None` negative
  twin proving the test measures the Room leg.
- [ ] `push_steering_message` targets `steering:room:{room_id}` when the caller supplies a truthy
  `room_id` **and the message is not an abort**, and `steering:{session_id}` in every other case.
  **Every non-test caller passes `room_id` explicitly.** Of the thirteen: **five** originating
  callers pass a derived Room; **three** requeue callers pass a per-message expression (D6);
  **five** pass an explicit `room_id=None` with the reason inline
  (`agent/output_handler.py:1227`, `agent/session_health.py:3467`,
  `monitoring/session_watchdog.py:557`, `scripts/steer_child.py:119`,
  `bridge/telegram_bridge.py:2070`); and `scripts/migrate_steering_queue_drain.py:126` is **not
  modified at all** — ORM-free by design, the census allowlist's sole entry. (Five explicit `None`s
  is what the `>= 5` Verification row expects.)
- [ ] **No requeue laundering, in either direction (D6).** The three writers that re-push an
  already-drained message — `agent/health_check.py::_repush_messages`,
  `agent/session_runner/runner.py::_default_steering_push`, `agent/session_executor.py:1933` —
  target the leg the message came from, read from the transient `_leg` stamp
  `pop_all_steering_messages` applies, defaulting to legacy when absent. A legacy-written diagnostic
  never reaches a Room key; a Room-written steer is never demoted to a mortal legacy key on requeue.
  Each also forwards the entry's original `timestamp`, so a requeue does not restart D5's clock.
- [ ] **No abort ever lands on a Room key**, whether `is_abort` was passed explicitly or set by the
  `ABORT_KEYWORDS` auto-detect. The key selection sits *below* the auto-detect block.
- [ ] **The Room leg is age-bounded by time since origination, across every Room-leg reader that has
  a production caller.** A drain-and-requeue cycle does not reset the clock: `push_steering_message`
  takes an optional `timestamp` and the three requeue writers forward the entry's own.
  `_drain_list` discards Room-key entries older than `steering_room_max_age_s` and `_peek_list`
  skips them, so `pop_all_steering_messages` and `peek_steering_messages` agree; in particular the
  `valor-session status` peek does not advertise steers the next drain will discard. The legacy leg
  is never filtered anywhere. The bound is a named env-overridable `TimeoutSettings` field, not a
  literal, and it is on `TimeoutSettings` — not `FeatureSettings`, where the `TIMEOUTS__` prefix
  would not resolve. `pop_steering_message`, `has_steering_messages` and `peek_steering_sender` are
  deliberately untouched (the first two have zero production callers; the third reads the legacy
  leg only).
- [ ] `push_steering_message` performs no ORM query and adds no exception handler. A steering
  write costs the same Redis round trips it costs today.
- [ ] **No caller derives a Room from an unmaterialized or unsorted row selection.** All **six**
  multi-row selections — `bridge/telegram_bridge.py` 1803, 1864, 2174, 2594, 2642 and
  `agent/health_check.py:623` — are materialized with `list(...)` and sorted newest-first by
  `created_at` before selecting. **Materialization is the load-bearing half**: `query.filter(...)`
  returns a `QueryBuilder` with no `.sort`, so a sort without it is an `AttributeError` — unhandled
  on the inbound-Telegram fast path at 1801/1860/2169, and silently swallowed at 2593 and
  `agent/health_check.py:621`. The sort itself is a full fix only at the three sites with no status
  filter; at the other three it is a within-status-bucket tie-break, because the enclosing
  `for check_status in (...)` loop breaks on the first bucket that returns any row (Race 4).
  The sort key is `(s.created_at is not None, s.created_at)`, **not** `created_at or 0`, which
  raises `TypeError` on a `datetime` field. `bridge/telegram_bridge.py:2070` — which holds no row —
  passes `room_id=None` rather than fabricating one.
- [ ] **The invariant is machine-checked, and the check is not scoped to a fixed module list.** A
  census test discovers `push_steering_message` call sites by walking the repo's non-test Python
  files and fails if any call omits an explicit `room_id`, with exactly one allowlisted path. A
  writer added in a new module later is caught by the same test.
- [ ] `_repush_messages` receives and forwards `_handle_steering`'s resolved `room_id` at **all
  four** call sites (530, 536, 560, 564) — including the two retries inside `except` blocks.
- [ ] No provenance tag appears in the JSON payload persisted to Redis (the payload dict is
  byte-identical to today's). D6's `_leg` is a transient reader-set key on the dicts
  `pop_all_steering_messages` returns; it is never a `push_steering_message` parameter and a
  requeued entry read back raw from Redis does not contain it.
- [ ] **`peek_steering_sender` is unmodified and `agent/output_handler.py:1160-1162` is
  unmodified** (verifiable from the diff). The drafter self-draft guard keeps observing exactly the
  messages it observes today because the drafter's own push stays on the legacy key.
- [ ] **Four of the five dual-read drain consumers are unmodified; the fifth changes by exactly one
  keyword.** `agent/health_check.py:514`, `agent/session_runner/runner.py:599`,
  `agent/session_executor.py:1905` and `agent/session_pickup.py:224` are byte-unmodified as drains.
  `agent/session_executor.py:2468` — the terminal leftover drain that feeds
  `_reenqueue_leftover_steering`, which *spawns a continuation session* — passes `room_id=None`, so a
  shared-Room instruction is never converted into new work at another session's teardown. The
  `valor-session status` peek call at `tools/valor_session.py:1031` is unmodified (verifiable from
  the diff).
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
- [ ] **The router never routes to MERGE.** DOCS is left `in_progress` after the doc edits land, so
  `agent/sdlc_router.py`'s G3 falls to its `else` branch and routes `/do-pr-review`.
  `sdlc-tool next-skill --issue-number 2642` must not print `/do-merge`. A `completed` DOCS would
  instead loop `/do-merge` against the draft until G4 wedges the run, because the router has no
  draft awareness (D3). Neither DOCS nor MERGE can be marked `skipped` (`SKIPPABLE_STAGES` excludes
  both), so an open follow-up operator issue is the durable carrier for the un-draft-and-merge
  action.
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
- Add `room_id: str | None = None` **and** `timestamp: float | None = None` to
  `agent/steering.py::push_steering_message`, and change the payload line to
  `"timestamp": time.time() if timestamp is None else timestamp`. The persisted key set is
  unchanged. `timestamp` exists so a requeue can carry the original stamp forward (D5); an
  originating caller never passes it.
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
  | `s = sessions[0]` (`agent/health_check.py:623`) | `agent/health_check.py:621` | **no** | wrap in `list(...)`, then sort. **Sixth site, added at round 4.** |

  Then insert
  `<name>.sort(key=lambda s: (s.created_at is not None, s.created_at), reverse=True)` immediately
  before the `[0]` / `next(...)`. **Do not use the repo's `created_at or 0` idiom here**: it
  substitutes an `int` into a list of `datetime`s and raises `TypeError` the moment a `None` row
  sits beside a populated one (reproduced at round 4). The tuple key is total and needs no import.
- **Expect `ruff format` to wrap the `list(...)` lines and leave it alone.**
  `bridge/telegram_bridge.py:1801` is already exactly 100 characters, so the wrapper pushes it to
  106 and the formatter splits it across three lines. That is correct output; do not fight it, and
  do not add a `# fmt: off`. No Verification row greps for the single-line shape (the round-4
  blocker was exactly such a row, now deleted) — the AST census test is the materialization guard
  and is wrap-insensitive.
- **Do not skip the `list(...)` at 1801/1860/2169.** Those three sit on the inbound-Telegram
  steering path with no enclosing `try`/`except` around the query, so the `AttributeError`
  propagates into the Telethon handler. At 2593 the same omission would be *silently* swallowed by
  the `except Exception` at 2595 (setting `session = None`, disabling edit-steering) — a failure the
  suite would not surface. **`agent/health_check.py:621` is the same silent shape and the highest
  stakes**: its `try:`/`except Exception` at 620/628 only `logger.debug`s, so a bare `.sort()` there
  leaves `steering_room_id = None` and demotes the whole PostToolUse hook to a legacy-only drain and
  legacy-only re-push. Race 4 has the measured evidence.
- Then work the Technical Approach's **flip table** row by row: each site passes
  `room_id=room_id_for_session(<the session object already in scope>)`.
- `bridge/telegram_bridge.py::_ack_steering_routed` gains a pass-through
  `room_id: str | None = None`. Four callers (1809, 1835, 1868, 2178) derive from their sorted row.
  **Caller 2070 passes `room_id=None`.** It holds no session row — 2058-2067 bind `guard_sessions`
  and test only truthiness. Do **not** invent `guard_sessions[0]`; that is the exact defect Race 4
  forbids.
- **The three requeue writers are Task 3's, not this task's.**
  `agent/session_runner/runner.py::_default_steering_push`, `agent/health_check.py::_repush_messages`
  and `agent/session_executor.py:1933` all derive a `room_id` but apply it per-message under D6. Do
  not give them an unconditional `room_id` here; that is precisely the round-4 blocker.
- Then work the **legacy table**: `agent/output_handler.py:1227`,
  `agent/session_health.py:3467`, `monitoring/session_watchdog.py:557`, `scripts/steer_child.py:119`
  each pass an **explicit `room_id=None`** with a one-line comment naming the reason (D1 or D4).
  The explicit keyword is what the census test checks; a bare omission fails it.
  `monitoring/session_watchdog.py::_inject_watchdog_steer` gains **no** parameter.
- `scripts/migrate_steering_queue_drain.py:126` is the one site that does **not** change at all.
- Do not introduce any new `AgentSession.query.filter(session_id=...)` call anywhere.

### 3. Make every requeue write to the leg it read from

- **Task ID**: build-repush
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-2 (**reversed at round 4** — a transient source-leg tag *is* needed; four
  `_repush_messages` call sites, not three), D6, D1 (the boundary this task is what preserves)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- **First, stamp the leg at the reader.** In `agent/steering.py::pop_all_steering_messages`, set
  `_leg = "legacy"` on each dict returned by the legacy `_drain_list` and `_leg = "room"` on each
  dict returned by the Room `_drain_list`, before returning the combined list. Set it **only** here
  — not in `_drain_list` (it does not know which leg it was handed) and not in `_peek_list`
  (nothing requeues from a peek).
- `_leg` is **transient**: it is never a `push_steering_message` parameter and never reaches Redis.
  The requeue writers below build their payload from named fields, so the persisted JSON stays
  byte-identical — the "no provenance field in the payload" success criterion is what keeps this
  honest, and a Verification row checks it.
- **Then fix all three requeue writers.** Each derives a `room_id` and applies it per message as
  `room_id=room_id if msg.get("_leg") == "room" else None`. **Absent `_leg` → legacy** — the
  fail-safe default, matching "absent `room_id` → legacy". Each also passes
  `timestamp=msg.get("timestamp")` so the requeue does not restart D5's clock; an absent or `None`
  timestamp falls back to `time.time()` inside the writer, so a hand-built dict is safe.
  1. `agent/health_check.py::_repush_messages` — add `room_id: str | None = None`; apply the
     per-message expression inside the loop. `_handle_steering` passes its own already-resolved
     `room_id` at **all four** call sites: 530 (abort-sibling primary), 536 (abort-sibling retry,
     inside `except`), 560 (non-abort primary), 564 (non-abort retry, inside `except`). A mechanical
     replace on the primary call shape misses the two retries, and the miss is silent.
  2. `agent/session_runner/runner.py::_default_steering_push` — compute
     `room_id_for_session(self._agent_session)` (mirroring `_default_steering_pop` at 599-601) and
     apply the per-message expression to the `msg` it was handed. Its only caller is
     `_requeue_pending_steers` at 631, re-pushing steers that `_default_steering_pop` drained from
     both legs. **In the same edit, add `target_agent=msg.get("target_agent")`.** This writer is the
     one requeue site that does not forward it today (`:610-615` passes only `text`, `sender`,
     `is_abort`; `agent/health_check.py:479-485` and `agent/session_executor.py:1932-1938` both
     pass it), so a requeue through the runner silently strips the field. Pre-existing, but this
     plan's byte-identity criterion and D6's "rebuilt from named fields (`text`, `sender`,
     `is_abort`, `target_agent`)" both assert the opposite, and `target_agent` is the field D2 /
     Risk 2 nominate as the designed gate for the cross-delivery this release defers. Fixing it
     here is one keyword; documenting the exception would be longer. Do **not** widen the census
     test's explicit-keyword rule to `target_agent` — it is scoped to `room_id`, and the five
     legacy sites legitimately pass no `target_agent`.
  3. `agent/session_executor.py:1933` — the site the plan missed until round 4. The enclosing block
     already computed `_room_id_for_session(agent_session)` at 1906-1909 for its own drain; **reuse
     that value**, do not recompute it, and apply the per-message expression to each `_remaining` in
     `steering_msgs[1:]`.
- **Format the four `_repush_messages` calls however `ruff format` wants.** The round-4 draft
  mandated one physical line each so a substring grep could see them; that grep is deleted (round 5)
  because a plan must not dictate source formatting to keep its own gate satisfiable — the same
  coupling that made the `list(AgentSession.query.filter` row unsatisfiable. The census test asserts
  the property instead: for every `ast.Call` to `_repush_messages` in `agent/health_check.py`, a
  `room_id` keyword must be present. That covers the two retries at 536/564 without constraining
  layout.
- **Then fix the terminal leftover drain.** `agent/session_executor.py:2468` — change
  `room_id=room_id_for_session(agent_session) if agent_session else None` to `room_id=None` with an
  inline comment. It is not a requeue writer; it is a *consumer* whose survivors are turned into a
  new session by `_reenqueue_leftover_steering`, so draining the shared Room leg there converts
  another Room's instruction into spawned work. Room-leg messages are durable by construction and
  need no rescue. This is the only edit any dual-read consumer receives.
- Note the re-pushed siblings are non-abort by construction (the abort is consumed, not
  re-pushed), so D4 and this task do not conflict — but do not "helpfully" re-push the abort.
- **Do not substitute a sender-based suppression** (`room_id=None if sender in {...}`). It was
  considered and rejected in D6: a sender string is not a boundary, and a diagnostic writer that
  renames its sender would silently start laundering with no test to catch it.

### 4. Bound the Room leg's age

- **Task ID**: build-staleness
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-6 (nothing bounds a Room-key message's lifetime), D5
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `steering_room_max_age_s: int = Field(default=21600, ge=60, le=604800, ...)` to
  **`TimeoutSettings`** in `config/settings.py` (the class spans lines 184-452), beside the other
  TTL-shaped fields `agent_session_retain_ttl_s` / `last_processed_ttl_s` / `dedup_record_ttl_s`,
  and copy the GRAIN-OF-SALT description style from `dedup_record_ttl_s` or
  `catchup_disabled_warn_hours` — both in the same class. Env key:
  `TIMEOUTS__STEERING_ROOM_MAX_AGE_S`. The `_s` suffix is the class convention; `_seconds` is not.
  **Do not put it on `FeatureSettings`.** An earlier draft called `bridge_msg_claim_ttl_seconds`
  "the neighbouring field"; it is on `FeatureSettings` at line 776. Landing there would make the
  `TIMEOUTS__` env override resolve to nothing while the `.env.example` completeness test still
  passed — a silently dead knob. Add the `.env.example` placeholder with its required comment line.
- Add `max_age_seconds: float | None = None` to `agent/steering.py::_drain_list`. When set, drop
  entries whose payload `timestamp` is older than the bound, logging each drop at `info` with the
  key and the age so a missing steer stays diagnosable.
- Add the same parameter to `agent/steering.py::_peek_list`. It **skips** stale entries from the
  returned list; it must not delete anything — the peek is non-destructive by contract and the next
  drain is what removes them.
- Wire the bound at exactly **two** Room-leg read sites, **never on the legacy key** (that is what
  keeps every message that exists today behaving exactly as it does today):
  1. `pop_all_steering_messages` (line 138) → `_drain_list(_room_queue_key(room_id), max_age_seconds=...)`
  2. `peek_steering_messages` (line 208) → `_peek_list(_room_queue_key(room_id), max_age_seconds=...)`
  Site 2 exists because `valor-session status` reads through `peek_steering_messages`
  (`tools/valor_session.py:1031`); an unfiltered peek would advertise steers the next drain throws
  away.
- **Do NOT touch `pop_steering_message` (line 164).** It does not call `_drain_list` — it inlines
  its own `r.lpop` + `json.loads` loop — and it has zero production callers (only its definition and
  the `agent/__init__.py` re-export at 41/73; every other reference is in `tests/`). Refactoring it
  through `_drain_list` is unasked scope. If a builder finds themselves editing it, stop.
- **Do NOT touch `has_steering_messages` (line 200) either.** An earlier draft rewrote its Room leg
  from an O(1) `r.llen` into an O(N) `_peek_list` that LRANGEs the whole list and `json.loads` every
  entry. It has **zero production callers** by the same measure as `pop_steering_message` — only its
  definition and the `agent/__init__.py` re-export at 38/76 — so the plan's own exclusion rule
  applies. The "operator surface" justification was wrong: `tools/valor_session.py:1031` calls
  `peek_steering_messages`, never this. It keeps its `llen` fast path and gains no parameter.
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
  with different `chat_id`s; assert the steer lands on the live session's Room. Drive this through
  one of the three **unfiltered** selections (`bridge/telegram_bridge.py:2593`, `:2641`, or
  `agent/health_check.py:621`), not a status-filtered one — a `superseded` row cannot appear in a
  result set filtered on `("running","active",...)` (Race 4's per-site trigger table).
- ADD: the sort key is total — a two-row list, one `created_at=None` and one populated, sorts
  without raising and puts the populated row first under `reverse=True`. This is the test that
  fails on the retired `created_at or 0` idiom.
- ADD the **abort-routing matrix**: `is_abort=True` + truthy `room_id` → legacy; bare `"stop"` with
  `is_abort` defaulted + truthy `room_id` → legacy (this is the ordering test); each of
  `{"stop","cancel","abort","nevermind"}` → legacy; `"stop the deploy"` → Room.
- ADD the **staleness set** (three cases): (a) a stale Room-leg entry is dropped by
  `pop_all_steering_messages` while a fresh one on the same key survives; (b) a stale **legacy**-leg
  entry is NOT dropped; (c) `peek_steering_messages` **skips** the stale Room-leg entry and — this
  is the operator-surface assertion — a subsequent `peek` still finds the fresh one, proving the
  peek did not delete anything. Case (c) is what keeps `valor-session status` from advertising
  steers the next drain discards. There is deliberately **no `has_steering_messages` case**: it is
  scoped out of D5 (zero production callers) and stays unbounded.
- ADD the **D6 leg-preservation set**, the round-4 blocker's regression tests:
  (a) `pop_all_steering_messages` stamps `_leg="legacy"` on legacy-drained entries and
  `_leg="room"` on Room-drained ones, in one mixed drain;
  (b) a **legacy**-sourced message re-pushed through `_repush_messages` with a truthy `room_id`
  lands back on the **legacy** key (this is the anti-laundering test — without D6 it lands on the
  Room key);
  (c) a **Room**-sourced message re-pushed through `_repush_messages` lands back on the **Room**
  key (this is the half a uniformly-legacy requeue would break);
  (d) the same pair through `agent/session_runner/runner.py::_default_steering_push`;
  (e) a message with **no** `_leg` key at all → legacy, through both writers;
  (f) end-to-end: a Room steer drained by session B, requeued, and still delivered to session C
  (the "Requeue twin" in Failure Path Test Strategy).
- ADD: the `_leg` key never reaches Redis — after a requeue, read the raw list entry back and assert
  its JSON keys are exactly `{text, sender, timestamp, is_abort}` (plus `target_agent` when set).
- ADD: a Room-leg entry with a missing/non-numeric `timestamp` is kept by `_drain_list` **and**
  returned by `_peek_list` (fail open on both).
- UPDATE `TestRoomDualRead::test_handle_steering_drains_room_leg` (line 1748): the re-push now
  targets the Room key **for a Room-sourced message**. Fix the docstring too, and state the D6 rule
  in it rather than "the re-push targets the Room key" unqualified.
- UPDATE `tests/unit/test_public_api_contract.py:31-34`: extend `push_steering_message`'s pinned
  signature string with **both** `room_id: 'str | None' = None` and `timestamp: 'float | None' =
  None`, with a DELIBERATE-change comment citing this plan, matching the style of the neighbouring
  `pop_all_steering_messages` entry. Adding only one of the two leaves the test red.
- ADD the legacy-fallback matrix — `room_id=None`, `room_id=""`, a session with no `project_key`
  — each lands on `_queue_key`. Plus `session_id=""` with a truthy `room_id` → the Room key wins.
- ADD: abort siblings re-pushed to the Room key, exercising the retry paths (536, 564) by forcing
  the primary call to raise.
- ADD: the payload persisted to Redis contains exactly `{text, sender, timestamp, is_abort}` (plus
  `target_agent` when set) — no provenance field. **Drive one case through
  `agent/session_runner/runner.py::_default_steering_push` with a message that *has* a
  `target_agent`, and assert the round-tripped entry still carries it.** Worded only as "plus
  `target_agent` when set", the assertion is satisfied by a message that never had one — which is
  exactly how the runner's missing forward (Task 3, bullet 2) survived to round 6.
- ADD the **D5 origination-age set**: (a) a requeue through `_repush_messages` and through
  `_default_steering_push` preserves the entry's original `timestamp` — read the raw Redis entry
  back and assert the float is unchanged, not refreshed; (b) an entry pushed with a backdated
  `timestamp`, drained, requeued, and drained again is **dropped** on the second drain (this is the
  round-5 blocker's regression test — without the forward it would survive forever); (c) an
  originating caller that passes no `timestamp` gets `time.time()`, and a requeue of a dict with no
  `timestamp` key does not raise.
- **Both AST census tests live in `tests/unit/test_steering_writer_census.py`, not in the
  integration suite.** They are pure `ast.parse` walks over source files with no Redis dependency,
  and the pattern they copy (`tests/unit/test_bridge_dispatch_contract.py`) is in `tests/unit/` for
  exactly that reason. Placing them behind the integration suite's Redis prerequisite would make two
  structural invariants unrunnable on a machine without Redis. The four Verification rows that
  anchor them by node id use this path.
- ADD **`test_every_flipped_writer_passes_room_id`** (in `tests/unit/test_steering_writer_census.py`)
  — the AST census. **Discover the call sites by
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
  - **Do NOT widen the rule to `timestamp`.** Only the three requeue writers pass it; an originating
    caller that passed one would be a bug (D5).
  - **Same test module, second assertion (replaces the deleted grep row):** every `ast.Call` whose
    func is `Name(id="_repush_messages")` in `agent/health_check.py` carries a `room_id` keyword.
    That pins all four call sites — including the two retries inside `except` blocks at 536 and 564
    — without mandating that they fit on one physical line.
  Reuse the path-glob discovery step and the scope-aware walker pattern from
  `tests/unit/test_bridge_dispatch_contract.py` (`_direct_calls` 66, `_banned_calls_in` 87,
  `ast.parse` 105). A repo walk also removes the plan-table-vs-test-list drift surface entirely.
- ADD **`test_room_derivation_sites_sort_before_selecting`** (also in
  `tests/unit/test_steering_writer_census.py`) — for each of the **six** selections in
  Race 4 (five in `bridge/telegram_bridge.py`, one in `agent/health_check.py`), assert the enclosing
  function sorts by `created_at` **and** that the sorted name is bound to a materialized list.
  **This test is the sole materialization guard** — the `list(AgentSession.query.filter` grep row was
  deleted at round 4 because `ruff format` wraps the call across lines and a substring grep cannot
  see it. A text-match on the sort call alone passes on code that raises `AttributeError` at runtime,
  because `AgentSession.query.filter(...)` returns a `QueryBuilder` with no `.sort`. Concretely: for
  each `<name>.sort(...)` call found, walk back to the `ast.Assign` that binds `<name>` in the same
  function and assert its value is an `ast.Call` to `list` (or otherwise not a bare
  `.query.filter(...)` chain). AST is wrap-insensitive, so the formatter's line splitting is
  irrelevant to it. Include a self-check case asserting the walker *rejects* a synthetic
  `sessions = AgentSession.query.filter(...)` / `sessions.sort(...)` pair, so the guard cannot
  silently degrade to a text match.
- Run via `scripts/pytest-clean.sh tests/integration/test_steering.py
  tests/unit/test_steering_writer_census.py`, never bare pytest.

### 7. Validate

- **Task ID**: validate-all
- **Depends On**: build-docstrings, build-tests
- **Assigned To**: `steering-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm the diff adds no `AgentSession.query.filter(` call and no `except` inside
  `agent/steering.py::push_steering_message`.
- Confirm the key-selection expression sits **below** the `ABORT_KEYWORDS` auto-detect block.
- Confirm four of the five drain consumers are unmodified and that the fifth,
  `agent/session_executor.py:2468`, changed by exactly one keyword (`room_id=None`) and nothing else.
  Confirm `tools/valor_session.py:1031` (the status peek
  call) is unmodified, and **`peek_steering_sender` plus `agent/output_handler.py:1160-1162` are
  unmodified** — the D1 reversal makes these anti-criteria.
- Confirm `agent/session_health.py` and `monitoring/session_watchdog.py` gain no
  `room_id_for_session` import.
- Confirm **no requeue writer passes a bare `room_id`** — all three (`_repush_messages`,
  `_default_steering_push`, `agent/session_executor.py:1933`) gate on `_leg` (D6). A bare
  `room_id=room_id` reaching `push_steering_message` from any of them is the round-4 blocker
  reintroduced.
- Confirm `has_steering_messages` and `pop_steering_message` are unmodified.
- Confirm the persisted payload shape is unchanged and that `_leg` appears nowhere in a Redis
  payload — read a requeued entry back raw and inspect its JSON keys.
- Run every row of the Verification table. **Every row's Command cell is an executable shell command
  or a pytest node id** — that is now an invariant of the table, restored at round 5 by deleting the
  one row whose exclusion could not be expressed in `grep` and by giving the `_leg`-payload row a
  node id. If a future row cannot be executed verbatim, it does not belong in the table; put the
  property in a test instead.
- **Amend issue #2642's body** — `gh issue edit 2642` — recording that two of its stated
  constraints are superseded, with the evidence: (a) resolution does **not** happen inside
  `push_steering_message`; spike-5 measured `AgentSession.query.filter(session_id=...)` at
  2.55s/2.06s/2.36s because `models/agent_session.py:155` declares `session_id = Field()` and
  Popoto defaults to `indexed=False`. (b) There is **no** provenance tag; spike-2 shows forwarding
  the already-resolved `room_id` into `_repush_messages` satisfies the sibling criterion with a
  byte-identical payload, so criterion 4 is restated as "the JSON payload persisted to Redis is
  byte-identical to today's". Also restate criterion 1 as "every non-test caller passes `room_id`
  explicitly; conversation-level callers pass a derived Room" and correct "three writer sites" to
  thirteen. Without this, `/do-pr-review` grades the PR against criteria it deliberately does not
  meet.
- Confirm the PR was created with `--draft`, carries the `hold` label, and states the removal
  condition and its named owner in its body.

### 8. Documentation, then close the run without dispatching MERGE

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `steering-documentarian` for the documentation bullets; **`steering-validator` for
  the release-hold bullets below** (the DOCS marker, the hold comment, the follow-up issue). The
  pipeline exit is the highest-stakes step in the release — it is what keeps a fleet-unsafe change
  from merging — and it belongs to the agent whose Role already covers verifying the
  draft/`hold`/removal-condition state (Task 7), not to a documentarian.
- **Agent Type**: documentarian (docs) / validator (release hold)
- **Parallel**: false
- Update `docs/features/session-steering.md` with the selective-flip rule — **reproduce the Problem
  section's five-class table verbatim, all five rows**, including the *requeue* class D6 exists to
  establish (an earlier draft of this task said "three-class", a count left over from before D6, and
  a documentarian following it literally would omit exactly the class a reader debugging a
  mis-delivered steer needs). Also cover the Risk 2 same-Room delivery semantics (D2: accepted
  behavior, documented as behavior), the D5 age bound with its env key and the fact that it measures
  time since **origination** (a requeue forwards the original timestamp), and the two delivery points
  for a Room-leg steer: session pickup (`_drain_startup_steering`) and the turn boundary.
- Update the remaining-releases list at `docs/plans/durability-room-job-agentrun.md:673`.
- Add `TIMEOUTS__STEERING_ROOM_MAX_AGE_S` to `docs/features/config-timeout-catalog.md`.
- **Then hold the run at DOCS (`steering-validator` owns this half).** `agent/sdlc_router.py` has no
  notion of a held run and no draft awareness whatsoever, and it never consults the MERGE marker
  before dispatching `/do-merge` — G3 (`:381-383`) routes to it on REVIEW **and** DOCS `completed`,
  G6 (`:723-727`) fast-paths on the same DOCS-done plus `REVIEW_APPROVED`. So leaving MERGE at
  `pending` holds nothing: the router dispatches `/do-merge`, the merge predicate refuses
  fail-closed on the draft, and the run loops until `MAX_SAME_STAGE_DISPATCHES = 3` (`:86`) wedges
  it in G4. Neither DOCS nor MERGE can be marked `skipped` — `tools/sdlc_stage_marker.py:322-326`
  refuses with `STAGE_NOT_SKIPPABLE` because `agent/pipeline_state.py:83` is
  `frozenset({"PLAN", "CRITIQUE"})`. Do not attempt that call; it fails loudly and changes nothing.
  The exit is therefore to **leave DOCS `in_progress`** once the doc edits above are on the branch:
  1. Record `sdlc-tool stage-marker --stage DOCS --status in_progress` and never write
     `--status completed`. With DOCS short of `completed`, G3 falls to its `else` and routes
     `/do-pr-review`, which is idempotent against a draft PR and does not wedge.
  2. Confirm the hold holds: `sdlc-tool next-skill --issue-number 2642` must not print `/do-merge`.
  3. Post the hold as a comment on #2642: the PR number, the removal condition verbatim, and the
     named removal owner (Valor Engels, repo operator).
  4. File a follow-up operator issue — "Un-draft and merge PR #N (#2642) once fleet `/update` is
     past PR #2622" — so the action has a durable carrier that is not a stage marker. The issue
     also names `sdlc-tool stage-marker --stage DOCS --status completed` as the step that re-arms
     the merge dispatch when the gate lifts.
  5. Report the run to the supervisor with the verdict string
     `HELD: merge deliberately not dispatched`.
  The supervisor's exit condition is "draft PR open, `hold` applied, docs edits committed, DOCS
  `in_progress`, `next-skill` not printing `/do-merge`, follow-up issue filed" — not a merge.

## Verification

**Baseline commit: `e0157a0b2`** — the single baseline for this whole document, used by the
Freshness Check, the Structural Check Results and every threshold row below. (Earlier drafts cited
`e6d0e2bc7` in one section and `8afe2df22` in another; every number was re-measured on `e0157a0b2`
during the round-3 revision and all hold unchanged, so this was a citation inconsistency, never a
stale-number problem. Re-measured: `room_id` in `agent/steering.py` = 22, `_queue_key(session_id)` =
7, the `sed` span = 54 lines with 0 `except`, `query.filter` in `agent/steering.py` = 0,
`from models` = 0.)

Round-5 baseline correction: `grep -c "not is_abort" agent/steering.py` is **1**, not the 0 an
earlier draft recorded — `agent/steering.py:113` already contains `if not is_abort and
text.strip().lower() in ABORT_KEYWORDS:`. The row that depended on that number was vacuous and has
been replaced (see the abort-guard rows below). Also measured at round 5:
`"timestamp": time.time()` in `agent/steering.py` = **1** (unconditional, line 119), and
`time.time() if timestamp is None else timestamp` = **0**.

Round-4 additions to the baseline, measured on the same working tree:
`created_at is not None` in `bridge/telegram_bridge.py` = **0** and in `agent/health_check.py` =
**0**; `_leg` across `agent/steering.py`, `agent/health_check.py`,
`agent/session_runner/runner.py`, `agent/session_executor.py` = **0**; `room_id_for_session` in
`agent/health_check.py` = **2** (the import at 618 and the call at 627); `room_id=room_id` in
`agent/health_check.py` = **1**; `bridge/telegram_bridge.py:1801` is **exactly 100 characters**,
which is what makes the deleted `list(...)` grep row unsatisfiable under `ruff format`.

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

Rows deleted or replaced at round 4:

- `grep -c "list(AgentSession.query.filter" bridge/telegram_bridge.py >= 6` (baseline 3) was
  **unsatisfiable by a correct build**. `bridge/telegram_bridge.py:1801` is already exactly 100
  characters — the repo's `line-length` — so the `list(...)` wrapper makes it 106 and `ruff format`
  splits it into three lines that the substring grep cannot match. The same file already holds the
  wrapped shape at 2059-2061 and 2065-2067, which is why those are absent from the baseline of 3.
  It collided head-on with the `ruff format --check .` row. **Deleted, not re-tuned:** a substring
  grep cannot pin a call shape the formatter is free to rewrite. `test_room_derivation_sites_
  sort_before_selecting` is now the sole materialization guard — it asserts the same property via
  AST, which is wrap-insensitive.
- `grep -c "created_at or 0" bridge/telegram_bridge.py >= 5` is **replaced** by a
  `created_at is not None` row. The idiom itself was retired: `created_at` is
  `SortedField(type=datetime)`, so `or 0` raises `TypeError` the moment a `None` row sits beside a
  populated one, and the plan's own Failure-Path bullet mandated a test that would have caught it.
- The `has_steering_messages` `sed` row is **deleted and inverted into an anti-criterion** — the
  function is scoped out of D5 by the same zero-production-callers rule that scopes out
  `pop_steering_message`, so `max_age_seconds` in `agent/steering.py` drops from `>= 5` to `>= 4`.

| Check | Command | Expected |
|-------|---------|----------|
| **Durability property delivered** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_steer_survives_target_session_and_reaches_room_sibling" -q` | exit code 0 (test must exist; a missing node id fails the row) |
| **Delivered via the production drain** | `grep -c "_default_steering_pop" tests/integration/test_steering.py` | ≥ 1 (baseline 0) — the durability test must read back through the runner's drain, not a hand-rolled `pop_all_steering_messages` call |
| **Negative twin passes** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "room_sibling or superseded"` | exit code 0, ≥ 3 tests collected |
| **Abort never lands on a Room key** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "abort_rout or abort_keyword"` | exit code 0, ≥ 4 tests collected |
| **Census test exists and passes** | `scripts/pytest-clean.sh "tests/unit/test_steering_writer_census.py::test_every_flipped_writer_passes_room_id" -q` | exit code 0 (anchored by node id so deleting the test fails the gate) |
| **Census discovers by repo walk, not a fixed table** | `sed -n '/def test_every_flipped_writer_passes_room_id/,/^def \\\|^class /p' tests/unit/test_steering_writer_census.py \| grep -c "rglob\\\|glob("` | ≥ 1 — a hardcoded module list leaves a future writer in a new module silently on the legacy key, which is the failure mode this test exists to close |
| **Sort census passes** | `scripts/pytest-clean.sh "tests/unit/test_steering_writer_census.py::test_room_derivation_sites_sort_before_selecting" -q` | exit code 0 |
| Steering suite green | `scripts/pytest-clean.sh tests/integration/test_steering.py -q` | exit code 0 |
| Output-handler unit tests green | `scripts/pytest-clean.sh tests/unit/test_output_handler.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Writer takes a `room_id` and does not look one up | `grep -c "room_id" agent/steering.py` | ≥ 24 (baseline 22; +1 signature, +≥1 key selection) |
| **Writer performs no ORM query** | `grep -c "query.filter" agent/steering.py` | == 0 (baseline 0 — must stay 0) |
| **Writer imports no model layer** | `grep -c "from models" agent/steering.py` | == 0 (baseline 0 — must stay 0). A bare `grep -c "AgentSession"` is NOT usable here: the baseline is 3, from self-draft-counter docstrings at lines 252, 272, 301 that say the counter is *not* an AgentSession field. |
| **Writer adds no exception handler** | `sed -n '/^def push_steering_message/,/^def pop_all_steering_messages/p' agent/steering.py \| grep -c "except"` | == 0 (the span is **54** lines on the baseline and contains no `except`; unlike the deleted `awk` row this fails loudly if a handler is inserted) |
| **Abort guard sits below the auto-detect** | `sed -n '/^def push_steering_message/,/^def pop_all_steering_messages/p' agent/steering.py \| grep -n "ABORT_KEYWORDS\\\|_room_queue_key(room_id)"` | the `ABORT_KEYWORDS` line number is **lower** than the `_room_queue_key(room_id)` line number. Reversed order means `is_abort` is read stale and every keyword-detected abort goes to the Room. |
| **Writer names `is_abort` in the key selection** | `grep -c "_room_queue_key(room_id) if (room_id and not is_abort)" agent/steering.py` | == 1 (baseline **0**). The round-4 row here was `grep -c "not is_abort"` expecting `≥ 1` against a recorded baseline of 0 — but the measured baseline is **1**, because `agent/steering.py:113` already reads `if not is_abort and text.strip().lower() in ABORT_KEYWORDS:`. That row passed on an untouched checkout and could not fail a build that omitted D4's guard entirely — the same vacuous-gate class as the deleted `awk` row. This form pins the exact single expression Task 1 mandates and cannot be satisfied by line 113. |
| **Re-push forwards the room at all four call sites** | `scripts/pytest-clean.sh "tests/unit/test_steering_writer_census.py::test_every_flipped_writer_passes_room_id" -q` | exit code 0. The census asserts every `ast.Call` to `_repush_messages` in `agent/health_check.py` carries a `room_id` keyword, pinning 530/536/560/564 including the two retries inside `except` blocks. **This replaces the round-4 grep row** `grep -c "_repush_messages(.*room_id=room_id" … == 4`, which only matched while all four calls stayed on one physical line — a plan-mandated formatting constraint that `ruff format` is free to break, the same coupling that made the `list(AgentSession.query.filter` row unsatisfiable. AST is wrap-insensitive. |
| Re-push forwards the room internally too | `grep -c "room_id=room_id" agent/health_check.py` | ≥ 6 (baseline 1 at line 514: the existing `pop_all_steering_messages` call; +4 call sites; +1 forward inside `_repush_messages`). The internal forward is `room_id=room_id if msg.get("_leg") == "room" else None`, which still contains the substring — so this row does **not** distinguish D6 from a bare forward. The D6 rows below are what do. |
| Bridge derives the Room | `grep -c "room_id_for_session" bridge/telegram_bridge.py` | ≥ 1 (baseline 0) |
| **Bridge sorts before deriving** | `grep -c "created_at is not None" bridge/telegram_bridge.py` | ≥ 5 (baseline **0**) — one per selection at 1803, 1864, 2174, 2594, 2642. The key is `(s.created_at is not None, s.created_at)`; the repo's `created_at or 0` idiom is **not** used here (it raises `TypeError` on a `datetime` `SortedField` — round-4 repro). |
| **Health-check hook sorts before deriving** | `grep -c "created_at is not None" agent/health_check.py` | ≥ 1 (baseline **0**) — the sixth selection, `agent/health_check.py:621-623`, whose output is the requeue target key. Its enclosing `except Exception` at 628 only `logger.debug`s, so a failure here is silent. |
| **`created_at or 0` is not reintroduced at the six sites** | `git diff origin/main \| grep -c "^+.*created_at or 0"` | == 0 — the idiom is unsound for this field; the 26 pre-existing uses elsewhere are out of scope (Rabbit Holes) |
| **Sort census rejects an unmaterialized sort** | `scripts/pytest-clean.sh "tests/unit/test_steering_writer_census.py::test_room_derivation_sites_sort_before_selecting" -q` | exit code 0, and the test must include a synthetic-negative case proving it rejects a bare `sessions = AgentSession.query.filter(...)` / `sessions.sort(...)` pair |
| **Steering fast path does not raise** | `scripts/pytest-clean.sh tests/unit/test_bridge_dispatch_contract.py -q` plus the steering suite | exit code 0 — 1801/1860/2169 have no enclosing `try`/`except`, so an unmaterialized sort there propagates into the Telethon handler |
| Runner push derives the Room | `grep -c "room_id_for_session" agent/session_runner/runner.py` | ≥ 3 (baseline 2) |
| **Watchdog stays legacy (anti-criterion)** | `grep -c "room_id_for_session" monitoring/session_watchdog.py` | == 0 (baseline 0 — must stay 0; D1) |
| **Session-health stays legacy (anti-criterion)** | `grep -c "room_id_for_session" agent/session_health.py` | == 0 (baseline 0 — must stay 0; D1, and this is what keeps `front=True` from inverting, Risk 3) |
| **`steer_child` stays legacy (anti-criterion)** | `grep -c "room_id_for_session" scripts/steer_child.py` | == 0 (baseline 0 — must stay 0; D4) |
| **Legacy exceptions are explicit, not omissions** | `grep -ho "room_id=None" agent/output_handler.py agent/session_health.py monitoring/session_watchdog.py scripts/steer_child.py bridge/telegram_bridge.py \| wc -l` | ≥ 5 (baseline 0) — the census test checks the same property structurally |
| **Peek untouched (anti-criterion)** | `git diff origin/main -- agent/steering.py \| grep -c "peek_steering_sender"` | == 0 (D1 reversal) |
| **Drafter guard untouched (anti-criterion)** | `git diff origin/main --quiet -- agent/output_handler.py \|\| git diff origin/main -- agent/output_handler.py \| grep -c "peek_steering"` | == 0 — the only permitted change to this file is the `room_id=None` keyword at 1227 |
| Room-leg age bound is a named setting | `grep -c "steering_room_max_age_s" config/settings.py` | ≥ 1 (baseline 0) |
| **Bound lands on `TimeoutSettings`, not `FeatureSettings`** | `python -c "from config.settings import settings; print(settings.timeouts.steering_room_max_age_s)"` | prints the default. A `FeatureSettings` placement would `AttributeError` here while `.env.example` completeness still passed — a silently dead `TIMEOUTS__` override. |
| Age filter is Room-leg-only | `grep -c "max_age_seconds" agent/steering.py` | ≥ 4 (baseline 0). Derivation: `_drain_list` param + `_peek_list` param + **two** Room-key call sites (`pop_all_steering_messages`, `peek_steering_messages`). **`pop_steering_message` and `has_steering_messages` are NOT call sites** — both have zero production callers (D5), and the former never calls `_drain_list` anyway. |
| **Operator peek honors the bound** | `sed -n '/^def peek_steering_messages/,/^def peek_steering_sender/p' agent/steering.py \| grep -c "max_age_seconds"` | ≥ 1 (baseline 0) — `valor-session status` reads through this; an unfiltered peek advertises steers the next drain discards |
| **`has_steering_messages` untouched (anti-criterion)** | `git diff origin/main -- agent/steering.py \| grep -c "^[-+].*llen"` | == 0 — zero production callers (definition + `agent/__init__.py` 38/76 only), so the same rule that scopes out `pop_steering_message` scopes this out. Rewriting its O(1) `llen` into an O(N) `_peek_list` is unasked scope. |
| **Requeue preserves the source leg (D6)** | `grep -ho '_leg' agent/steering.py agent/health_check.py agent/session_runner/runner.py agent/session_executor.py \| wc -l` | ≥ 5 (baseline **0**) — the stamp in `pop_all_steering_messages` (×2, one per leg) plus one gate in each of the three requeue writers |
| **No requeue passes a bare room_id (D6 anti-criterion)** | `grep -ho 'room_id=room_id,' agent/health_check.py agent/session_runner/runner.py agent/session_executor.py \| wc -l` | == 0 (baseline **0**, measured at round 5). The D6 per-message form is `room_id=room_id if msg.get("_leg") == "room" else None` — never followed by a comma. Every `push_steering_message(...)` call in these three modules is multi-line, so `ruff format` gives each argument a trailing comma; a bare forward therefore reads `room_id=room_id,` and trips this row. The existing drain at `agent/health_check.py:514` reads `room_id=room_id)` (last argument, single line) and is correctly not matched, which is why the comma — not `\b` plus a prose exclusion — is the discriminator. **The round-4 form of this row was not executable**: its command was `grep -c 'room_id=room_id\b'` qualified in prose by "(excluding the `_repush_messages(...)` call sites and the `pop_all_steering_messages` drain)" — an exclusion `grep` cannot express, and one that made the row contradict its own `>= 6` neighbour on a correct build. Authoritative check remains the D6 test set (Task 6 cases b/c/e); this is a smoke test. |
| **`_leg` never reaches Redis** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "leg_not_persisted"` | exit code 0, ≥ 1 test collected — the test reads a requeued entry back with a raw `LINDEX` and asserts its JSON keys are exactly `{text, sender, timestamp, is_abort}` (+ `target_agent` when set). Name the test so this `-k` selector matches. |
| **A requeue does not restart D5's clock** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "origination_age or timestamp_preserved"` | exit code 0, ≥ 2 tests collected — a requeued entry keeps its original `timestamp`, and a backdated entry still expires on the drain *after* a drain-and-requeue cycle. This is the round-5 blocker's regression gate. |
| **Writer accepts an origination timestamp** | `grep -c "time.time() if timestamp is None else timestamp" agent/steering.py` | == 1 (baseline **0**) — the exact payload expression D5 mandates; a bare `time.time()` leaves the requeue reset in place |
| **Public API contract updated** | `scripts/pytest-clean.sh tests/unit/test_public_api_contract.py -q` | exit code 0 — the module pins `push_steering_message`'s exact signature string at 31-34 and must be extended with `room_id` |
| **`pop_steering_message` untouched (anti-criterion)** | `git diff origin/main -- agent/steering.py \| grep -c "^[-+].*def pop_steering_message"` | == 0 — zero production callers; plumbing the bound into its inlined `lpop` loop is unasked scope |
| **Terminal leftover drain is legacy-only** | `sed -n '/leftover = pop_all_steering_messages/,/^            if leftover/p' agent/session_executor.py \| grep -c "room_id=None"` | == 1 (baseline **0** — the site currently passes `room_id=room_id_for_session(agent_session) if agent_session else None`). That drain feeds `_reenqueue_leftover_steering`, which spawns a continuation session; draining the shared Room leg there converts another Room's instruction into new work at every teardown. |
| **`session_pickup` drain untouched (anti-criterion)** | `git diff origin/main --quiet -- agent/session_pickup.py` | exit code 0 — it legitimately serves the Room leg at session creation, which post-flip is the primary delivery path for the durability property |
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
| **Router never routes to MERGE** | `sdlc-tool next-skill --issue-number 2642` | Does **not** print `/do-merge` — DOCS is left `in_progress`, so G3 falls to its `else` and routes `/do-pr-review`. The round-5 form of this row (`stage-query`, expecting "MERGE still `pending`") was **vacuous**: MERGE stays `pending` exactly while `/do-merge` keeps refusing on the draft, so it passed while the run was wedged. |
| **Held action has a durable carrier** | `gh issue list --search "Un-draft and merge PR" --state open --json number` | ≥ 1 open follow-up issue naming the PR and the removal owner |
| **Issue amended for superseded criteria** | `gh issue view 2642 --json body -q .body \| grep -c "superseded"` | ≥ 1 — otherwise `/do-pr-review` grades against criteria the plan deliberately reverses |

## Critique Results

Round 6 (FULL war room: Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **NEEDS REVISION** — 1 blocker, 2 concerns, 2 nits. **All five addressed; revision applied
2026-08-07 (see the round-6 revision log below the structural table).** Every finding was measured against
the working tree at `1b9f925d0` before it was written. Structural checks all PASS this round:
all 25 referenced paths exist, tasks 1-8 with no gaps, the dependency graph is acyclic, both
prerequisites pass, and **every Verification baseline in the table was re-measured and confirmed
correct** — including the round-5 corrections (`not is_abort` = 1, `"timestamp": time.time()` = 1,
`_room_queue_key(room_id) if (room_id and not is_abort)` = 0, `room_id=room_id,` = 0 across the
three requeue modules, `bridge/telegram_bridge.py:1801` = exactly 100 chars). The round-5 fixes
landed cleanly: no struck-through row survives in the live table, and the only remaining
"three-class"/"four classes" strings are inside the historical critique-log blocks.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | D3 part 3 / Task 8's pipeline exit does not exist. `agent/sdlc_router.py` never consults the MERGE stage marker when dispatching `/do-merge`: guard G3 at `:381-383` routes to `SKILL_DO_MERGE` on `review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED`, and G6 at `:723-727` fast-paths to it on "PR is mergeable, CI green, DOCS done, review APPROVED". `grep -i draft agent/sdlc_router.py` returns **zero** hits — the router has no draft awareness. So once DOCS completes with the draft PR open, the next dispatch routes `/do-merge`, which refuses fail-closed on the draft (`tools/merge_predicate.py:465-466`, verified), advances nothing, and is routed to again — exactly the "loop `/do-merge` against a draft PR forever" D3 claims to prevent. It ends only at `MAX_SAME_STAGE_DISPATCHES = 3` (`agent/sdlc_router.py:86`) in G4, which returns `Blocked` demanding `sdlc-tool dispatch reset` — an operator wedge, not a clean close-after-DOCS. The Verification row meant to catch this is vacuous in the same direction as the rows deleted at rounds 2/4/5: `stage-query` expecting "MERGE still `pending`" **passes while the run is wedged**, because MERGE stays `pending` exactly when `/do-merge` keeps refusing. | **Fixed** — the hold moved from the MERGE marker to DOCS `in_progress` (D3 part 3, Task 8, the `[ORDERED]` No-Go, the success criterion); the vacuous `stage-query` row was replaced by `sdlc-tool next-skill --issue-number 2642` must not print `/do-merge` | Invert which marker carries the hold. G3's merge dispatch requires **both** `review_status` and `docs_status` to be `STATUS_COMPLETED` (`agent/sdlc_router.py:381`); G6 additionally requires `REVIEW_APPROVED` in the normalized review verdict (`:714`). So completing REVIEW **and** DOCS is what arms the merge dispatch. Have Task 8 perform the doc edits and then record DOCS as `in_progress`, not `completed` (DOCS is not skippable either — `agent/pipeline_state.py:83` is `frozenset({"PLAN", "CRITIQUE"})`, and `tools/sdlc_stage_marker.py:322-326` refuses it with `STAGE_NOT_SKIPPABLE`, same as MERGE). With DOCS not `completed`, G3 falls through to its `else` branch and routes `/do-pr-review`, which is idempotent against a draft and does not wedge. Then replace the vacuous row with one that discriminates: `sdlc-tool next-skill --issue-number 2642` must NOT print `/do-merge`. If the owner prefers DOCS `completed`, the plan must instead name the G4 block as the accepted terminal state and put `sdlc-tool dispatch reset --issue-number 2642` in the follow-up issue as the documented un-wedge step. Do not leave the current text, which promises an exit that does not exist. |
| CONCERN | Risk & Robustness | Race 2 states the Room-leg hazard is "*which* session receives it, not loss or double-delivery", but the flip makes destructive drains of the **shared, immortal** Room key depend on a subsequent operation succeeding, and all three sites drop the message on failure. (1) `agent/health_check.py:536` and `:564` — the retry `_repush_messages` calls sit **inside** the `except` handler and are themselves unguarded, so a raising retry loses every drained message, including Room-sourced ones belonging to another session. (2) `agent/session_pickup.py:224` `_drain_startup_steering` drains the Room leg, then mutates `session.message_text` and `await session.async_save(...)`; its `except` at `:241` only logs a warning, so a save failure loses the drained Room messages permanently. (3) `agent/session_runner/runner.py:631-636` catches per message and logs `"failed to re-push pending steer (%r dropped)"` — explicitly a drop. Today each risks only *that session's own* legacy key; post-flip an unrelated session in the same `{project}\|system` Room erroring mid-hook silently deletes steers addressed to a different session — the durability property failing in the scenario it was built for. Not in the Race Conditions section, and Race 2's "not loss" sentence becomes false for the Room leg. | **Fixed** — Race 2 now names all three drop-on-failure sites, states the widened blast radius, and accepts the risk for the soak with an explicit rationale; hardening them is a new Rabbit Hole | The narrowest real hardening is `agent/session_pickup.py:224`, because the plan itself promotes it to "the *primary* delivery path for the durability property" and it has no requeue arm at all: move the drain **after** a successful `async_save`, or on the exception path re-push with `push_steering_message(session.session_id, m["text"], m["sender"], room_id=room_id_for_session(session) if m.get("_leg") == "room" else None, timestamp=m.get("timestamp"))` — the same D6 per-message expression and D5 timestamp forwarding Task 3 already specifies, so no new mechanism is needed. Note the same function also silently discards any drained entry with blank text (`extra_texts = [m["text"] for m in steering_msgs if m.get("text", "").strip()]` at `:227`), which post-flip discards a shared-Room entry rather than this session's own. For `agent/health_check.py:536/564` the minimum is to wrap the retry so a double failure logs at `error` with the message texts rather than propagating out of the PostToolUse hook. If all three are accepted, say so explicitly in Race 2 rather than leaving the contradicting "not loss" claim standing. |
| CONCERN | History & Consistency | The plan states three times (Data Flow step 7, spike-2, D6 "Mechanism") that the requeue writers rebuild the payload from `text`, `sender`, `is_abort`, **`target_agent`**, and rests its byte-identity criterion on that. One of the three does not carry `target_agent`: `agent/session_runner/runner.py:610-615` `_default_steering_push` calls `push_steering_message(session_id, msg.get("text") or "", sender=msg.get("sender") or "runner-requeue", is_abort=bool(msg.get("is_abort")))` — no `target_agent`. So a requeue through the runner silently strips it. (`agent/health_check.py:479-485` and `agent/session_executor.py:1932-1938` both pass it; only the runner does not.) Pre-existing, but the plan asserts the opposite as a supporting fact, and Task 6's payload-shape test would pass **vacuously** through this path because the field is never set on the re-push. It matters beyond bookkeeping: `target_agent` is the field D2, Risk 2 and the Rabbit Holes nominate as "the designed gate" for the same-Room cross-delivery this release defers. | **Fixed** — Task 3 bullet 2 adds `target_agent=msg.get("target_agent")` to `_default_steering_push`; D6's Mechanism records that only two of three forward it today; Task 6's payload row now drives a `target_agent`-bearing message through that writer specifically | Fixing is cheaper than documenting the exception: Task 3 already rewrites this call to add `room_id=...` and `timestamp=msg.get("timestamp")`, so add `target_agent=msg.get("target_agent")` in the same edit. Do **not** widen the AST census test's "explicit keyword" rule to `target_agent` — it is scoped to `room_id` (and deliberately not to `timestamp`, per D5), and widening it would fail the five legacy sites that legitimately pass none. Make Task 6's payload-shape test non-vacuous by driving it with a message that **has** a `target_agent` through `_default_steering_push` specifically and asserting the round-tripped entry still carries it; as currently worded the test is satisfied by a message that never had one. |
| NIT | History & Consistency | Task 7 elevated "every row's Command cell is an executable shell command or a pytest node id" to a table invariant at round 5, but three rows pass multiple files to `grep -c` and annotate Expected with "(summed)" — "Requeue preserves the source leg (D6)", "No requeue passes a bare room_id (D6 anti-criterion)", and "Legacy exceptions are explicit, not omissions". `grep -c pattern f1 f2 f3` emits one `path:count` line per file and never a sum, so a validator running them verbatim must hand-aggregate. | **Fixed** — all three rows now use `grep -ho ... \| wc -l`, which emits the single number their Expected cell names | Give the three rows a command that emits the number their Expected cell names, e.g. `grep -ho 'pattern' f1 f2 f3 \| wc -l`, or restate Expected as the per-file counts the command really produces. |
| NIT | Scope & Value | Task 8 is assigned to `steering-documentarian`, whose declared Role is "`docs/features/session-steering.md` and the parent plan's remaining-releases list", but the task body loads three non-documentation responsibilities onto it: terminating the pipeline run, posting a hold comment on #2642, and filing a follow-up operator issue. The pipeline exit is the highest-stakes step in the release — it is what keeps a fleet-unsafe change from merging — and it is the last bullet of a docs task. | **Fixed** — Task 8's Assigned To splits: `steering-documentarian` owns the doc bullets, `steering-validator` owns the release-hold bullets | Split Task 8 into a documentation task and a separate release-hold task, or reassign the exit bullets to `steering-validator`, whose Role already covers verifying the draft/`hold`/removal-condition state in Task 7. |

### Structural Check Results (round 6)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | build-writer -> build-callers/build-repush/build-staleness -> build-docstrings/build-tests -> validate-all -> document-feature; no cycles |
| File paths exist | PASS | All 25 referenced source, test and doc paths verified present at `1b9f925d0`; `tests/unit/test_steering_writer_census.py` correctly absent (new file). Cited lines re-read and confirmed: `agent/steering.py:110`/`113`/`115-120`, `agent/health_check.py:530`/`536`/`560`/`564`/`621-627`, `agent/session_runner/runner.py:587-615`/`617-636`, `agent/session_executor.py:1930`/`1933`/`2465-2473`, `agent/session_pickup.py:212-244`, `config/settings.py` TimeoutSettings 184 / FeatureSettings 602 / `bridge_msg_claim_ttl_seconds` 776, `tests/unit/test_public_api_contract.py:31-34`, `tools/merge_predicate.py:465-466`, `agent/pipeline_state.py:83`, `tools/sdlc_stage_marker.py:322-326` |
| Prerequisites met | PASS | Redis reachable; venv on the `.python-version` pin (3.14) |
| Cross-references | FAIL | D3 part 3, Task 8, the `[ORDERED]` No-Go, the Success Criterion "The pipeline run has a terminal state" and its Verification row all assert a router behavior that `agent/sdlc_router.py` does not implement (BLOCKER 1). Race 2's "not loss" claim is contradicted by the three Room-leg drain sites (concern 1). Data Flow step 7 / spike-2 / D6 name a `target_agent` forward that `agent/session_runner/runner.py:610-615` does not perform (concern 2) |
| Verification baselines | PASS | Every threshold re-measured on `1b9f925d0` and all correct: `room_id` in `agent/steering.py` = 22, `_queue_key(session_id)` = 7, `query.filter` = 0, `from models` = 0, `not is_abort` = 1, `"timestamp": time.time()` = 1, `_room_queue_key(room_id) if (room_id and not is_abort)` = 0, `_source` = 0, `created_at is not None` = 0 in both `bridge/telegram_bridge.py` and `agent/health_check.py`, `_leg` = 0 across all four modules, `room_id=room_id` in health_check = 1, `room_id=room_id,` = 0 across the three requeue modules, `room_id_for_session` = 2 (health_check) / 2 (runner) / 0 (bridge, watchdog, session_health, steer_child), `max_age_seconds` = 0, `steering_room_max_age_s` = 0, all four stale-docstring rows = 1, the `sed` span = 54 lines with 0 `except`, `bridge/telegram_bridge.py:1801` = exactly 100 chars |
| Verification rows executable | FAIL (NIT) | Three multi-file `grep -c` rows annotate Expected as "(summed)" but the command emits per-file counts, never a sum (NIT 1) |

### Round-6 revision applied (2026-08-07)

Every finding was re-verified against live code before it was fixed. `agent/sdlc_router.py:381`
routes `SKILL_DO_MERGE` on `review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED`
and falls to `SKILL_DO_PR_REVIEW` in its `else`; `grep -ci draft agent/sdlc_router.py` = 0;
`agent/pipeline_state.py:83` is `frozenset({"PLAN", "CRITIQUE"})`;
`agent/session_runner/runner.py:610-615` passes no `target_agent` while
`agent/health_check.py:479-485` does; `agent/session_pickup.py:224-241` drains then saves under a
warning-only `except`; `agent/health_check.py:536`/`:564` re-push unguarded inside `except`;
`grep -ho ... | wc -l` was run against all three rewritten Verification rows and emits a single
number (baselines 0, unchanged).

1. **BLOCKER — the pipeline exit did not exist.** The hold moved off the MERGE marker, which the
   router never reads, and onto **DOCS `in_progress`**, which is what arms G3's and G6's merge
   dispatch. Rewritten in D3 part 3, Task 8 (now a numbered five-step exit), the `[ORDERED]` No-Go
   and the success criterion. The vacuous `stage-query` Verification row was replaced by
   `sdlc-tool next-skill --issue-number 2642` must not print `/do-merge` — a check that fails when
   the run is wedged, which the old row passed.
2. **CONCERN — Race 2's "not loss" claim.** Now false-free: Race 2 names all three destructive-drain
   sites with their line numbers, states that the flip widens each one's blast radius from this
   session's own legacy key to the shared Room key, and **accepts the risk explicitly for the soak**
   with a rationale (pre-existing best-effort paths, no new failure mode, every drop logged with the
   text, recoverable by re-steering). Hardening them is a new Rabbit Hole with a named starting
   point.
3. **CONCERN — the runner's missing `target_agent`.** Fixed rather than documented: Task 3 bullet 2
   adds `target_agent=msg.get("target_agent")` in the edit that already rewrites that call, D6's
   Mechanism records that only two of three forward it today, and Task 6's payload-shape assertion
   is no longer vacuous — it drives a `target_agent`-bearing message through `_default_steering_push`
   specifically. The census test's explicit-keyword rule stays scoped to `room_id`.
4. **NIT — three multi-file `grep -c` rows.** All three now use `grep -ho '<pattern>' f1 f2 f3 |
   wc -l`, which emits the single number the Expected cell names.
5. **NIT — Task 8 overloaded the documentarian.** Assigned To splits: `steering-documentarian` owns
   the documentation bullets, `steering-validator` owns the release-hold bullets (DOCS marker, hold
   comment, follow-up issue) — the agent whose Role already covers the draft/`hold` state in Task 7.

### Round-5 log (history)

Round 5 (FULL war room: Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **NEEDS REVISION** — 1 blocker, 4 concerns, 2 nits. **All seven addressed; revision applied
2026-08-07 (see the round-5 revision log below the structural table).** Every finding was measured against the
working tree at `ce0f454bd` before it was written: the writer's unconditional `time.time()` stamp
was read at `agent/steering.py:115-120`, the two unanalyzed drain consumers were located by a
repo-wide non-test grep for `pop_all_steering_messages`, the `not is_abort` baseline was re-measured
(it is **1**, not the 0 the table records), and the four `_repush_messages` call-site line lengths
were measured (58/58/46/46 against `line-length = 100`) before the one-line mandate was judged.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | D5's age bound is reset by D6's requeue. `push_steering_message` stamps `"timestamp": time.time()` unconditionally (`agent/steering.py:115-120`) and the three requeue writers rebuild the payload from named fields only (`text`, `sender`, `is_abort`, `target_agent`), so `timestamp` is not carried across a requeue and every drain-and-requeue cycle restarts the clock. `agent/health_check.py:553-564` re-pushes every drained message on the normal non-abort PostToolUse path, so the reset is the dominant path, not an edge case. D5 therefore bounds time-since-last-push, not time-since-origination, and a Room message that is repeatedly drained-and-requeued without being injected is as immortal as it was before D5 — reopening round-2 BLOCKER 5 through a decision added two rounds later. | D5 amended (bounds age since **origination**) + `push_steering_message` gains `timestamp` + Task 1 / Task 3 / Test Impact / Success Criteria + 2 new Verification rows | Decide at plan level which age D5 bounds and state it once. To bound origination age: add a keyword-only `timestamp: float \| None = None` to `push_steering_message` and write `"timestamp": time.time() if timestamp is None else timestamp`; the three requeue writers forward `msg.get("timestamp")`. The persisted key set is unchanged so the byte-identity criterion still holds, but this is a **second** signature change, so `tests/unit/test_public_api_contract.py:31-33` must pin `room_id` *and* `timestamp` in the same edit, and the census test's "explicit `room_id` keyword" rule must NOT be widened to `timestamp` (originating callers must not pass it). If instead the reset is kept, reword D5, the Flow "Expiry branch" and the Success Criterion to "age since last push" and add a test pinning that a requeued entry's age restarts. |
| CONCERN | Risk & Robustness | The plan asserts "the five dual-read drain consumers are unmodified" eleven times but analyzes only three (`agent/health_check.py:514`, `agent/session_runner/runner.py:599`, `agent/session_executor.py:1905`). The other two are never named: `agent/session_executor.py:2465-2473`, whose leftover drain hands survivors to `_reenqueue_leftover_steering` (`agent/session_executor.py:928`) which **spawns a new continuation AgentSession**, and `agent/session_pickup.py:224` `_drain_startup_steering`, which prepends drained texts into a brand-new session's `message_text` at pickup. Both already dual-read the Room leg (#2622) against a leg that is empty today; the flip is what populates it. Post-flip the first *creates work* from an instruction possibly aimed at a dead sibling, and the second consumes the Room leg at session **creation**, not at the turn boundary the Flow section describes. | Data Flow steps 6 and 6a + two new Technical Approach bullets + Task 3 + Test Impact + Success Criteria | `agent/session_executor.py:2468` already passes `room_id=room_id_for_session(agent_session) if agent_session else None`, so the behavior change needs no edit and is silent. If the escalation is unwanted, partition `leftover` by the transient `_leg` D6 already adds and pass only `_leg == "legacy"` entries to `_reenqueue_leftover_steering`, re-pushing Room-sourced entries through the D6 leg-preserving path instead of converting them into a session — `_reenqueue_leftover_steering` already partitions on `sender` at 955-956, so the same shape applies. `agent/session_pickup.py:224` needs no code change, but the Flow section's "served to whichever session in that Room next reaches a turn boundary" is factually wrong while that path exists, because pickup fires first. |
| CONCERN | History & Consistency | The Verification row **Writer names `is_abort` in the key selection** (`grep -c "not is_abort" agent/steering.py`, expected `>= 1`, "baseline 0") is vacuous: the measured baseline is **1**, because `agent/steering.py:113` already reads `if not is_abort and text.strip().lower() in ABORT_KEYWORDS:`. The row passes on an untouched checkout and cannot fail a build that omits D4's abort guard entirely — the same vacuous-gate class the plan deleted the `awk` row for at round 2 and the `list(AgentSession.query.filter` row for at round 4, and a direct violation of the table's own preamble that "a row cannot be satisfied by an untouched checkout". | Verification row replaced with the exact-expression grep (measured baseline 0) | Replace the row with `grep -c "_room_queue_key(room_id) if (room_id and not is_abort)" agent/steering.py` == 1 (baseline 0). It pins the exact single expression Task 1 mandates and cannot be satisfied by line 113. Keep the existing ordering row (`ABORT_KEYWORDS` line number lower than `_room_queue_key(room_id)` line number) as its complement — that one is not vacuous, because `_room_queue_key(room_id)` has baseline 0 inside the `sed` span. |
| CONCERN | History & Consistency | Task 7 instructs the validator to "Run every row of the Verification table", but three rows are not executable. (1) The D6 anti-criterion row's command is `grep -c 'room_id=room_id\b' ...` qualified by "(summed, excluding the `_repush_messages(...)` call sites and the `pop_all_steering_messages` drain)" with Expected `== 0` — the grep cannot express that exclusion and, by the plan's own adjacent row, must sum to `>= 6` on a correct build, so a row-by-row validator records a failure on a correct implementation. (2) The struck-through `~~Bridge materializes before sorting~~` row was retained inside the table with prose in its Command cell instead of moved to the deleted-rows list above it, where its three siblings live. (3) The "`_leg` never reaches Redis" row's Command cell is a prose test description, not a command. | Three rows made executable: D6 grep re-expressed, struck row removed from the table, `_leg` row given a `-k` selector; Task 7 gained the executable-row invariant | The D6 property is already pinned structurally by Task 6 test cases (b)/(c)/(e), so delete the grep row rather than re-tune it. If an executable smoke test is still wanted, `grep -c 'room_id=room_id,\|room_id=room_id)' agent/health_check.py agent/session_runner/runner.py agent/session_executor.py` == 0 works: the D6 per-message form is `room_id=room_id if msg.get("_leg") == "room" else None`, always followed by a space and `if`, never by `,` or `)`, while a bare forward inside a `push_steering_message(...)` argument list always is. Move the struck row into the round-4 deleted-rows prose list and give the `_leg` row a pytest node id like every other test-backed row. |
| CONCERN | Scope & Value | The Verification table has become the plan's largest failure surface and now dictates source formatting to stay satisfiable. Three grep rows have been deleted across rounds for being vacuous or unsatisfiable (the `awk` span at round 2, `list(AgentSession.query.filter` and `created_at or 0` at round 4), a fourth is vacuous today (`not is_abort`), and Task 3 instructs the builder to keep four call sites on one physical line purely so a substring grep matches. That is the round-4 blocker's coupling re-expressed: a grep pinning a call shape `ruff format` is free to rewrite. The plan already owns the wrap-insensitive instrument — two AST census tests plus the steering suite. | Task 3's one-line mandate deleted; the grep row replaced by the census `_repush_messages` assertion | Measured on the baseline the one-line mandate is currently satisfiable: `agent/health_check.py:530`/`:536` are 58 chars and `:560`/`:564` are 46 chars, so `, room_id=room_id` (17 chars) lands at 75 and 63 against `line-length = 100` (`pyproject.toml:117`). This is a standing tripwire, not a defect today — it fires the moment any of those four lines gains another argument or an indent level. Durable replacement: assert it in the census test instead — for each `ast.Call` whose func is `Name(id="_repush_messages")` in `agent/health_check.py`, assert a `room_id` keyword is present. That covers the two retries at 536/564 the grep exists to catch without constraining formatting, and Task 3's one-line mandate can then be deleted. |
| NIT | History & Consistency | The same table is given three cardinalities. The Problem section's prose says "Writes therefore fall into **four** classes, not two" above a table with **five** rows; the Documentation section says "Reproduce the Problem section's four-class table verbatim"; Task 8 — the task that actually writes the doc — says "reproduce the Problem section's three-class table", a count left over from before D6 added the requeue class at round 4. | Problem prose → five classes; Documentation and Task 8 both say five-class table | Task 8 is the operative instruction: a documentarian following it literally writes `docs/features/session-steering.md` with the pre-D6 three-class model and omits the requeue class, which is the one D6 exists to establish and the one a reader debugging a mis-delivered steer needs. Fix Task 8 to match the Documentation section, and either merge the last two table rows under a single "legacy" class or change the prose to "five classes". |
| NIT | Scope & Value | Both AST census tests are pure `ast.parse` walks over source files with no Redis dependency, yet Task 6 places them in `tests/integration/test_steering.py`, whose Prerequisites row requires a reachable Redis. The pattern they are told to copy, `tests/unit/test_bridge_dispatch_contract.py`, lives in `tests/unit/` for exactly that reason, so two structural invariants become unrunnable on a machine without Redis. | Both census tests moved to `tests/unit/test_steering_writer_census.py`; all four Verification rows repathed | Move both to `tests/unit/` (e.g. `tests/unit/test_steering_writer_census.py`). Four Verification rows hardcode `tests/integration/test_steering.py::<name>` — "Census test exists and passes", "Census discovers by repo walk, not a fixed table", "Sort census passes", "Sort census rejects an unmaterialized sort" — so a move without updating all four fails the gate on a correct build. |

### Structural Check Results (round 5)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | build-writer -> build-callers/build-repush/build-staleness -> build-docstrings/build-tests -> validate-all -> document-feature; no cycles |
| File paths exist | PASS | All 24 referenced source, test and doc paths verified present. Cited line numbers re-read and confirmed: `agent/session_executor.py:1933`, `agent/health_check.py:475`/`530`/`536`/`560`/`564`/`621`, `tests/integration/test_steering.py` 1678/1748/1765, `tests/unit/test_public_api_contract.py:31-33`, `docs/plans/durability-room-job-agentrun.md:673`, `config/settings.py` TimeoutSettings 184 / FeatureSettings 602 / `bridge_msg_claim_ttl_seconds` 776, `tools/merge_predicate.py:465-466`, `agent/pipeline_state.py:83` `SKIPPABLE_STAGES = {"PLAN","CRITIQUE"}`, `tools/sdlc_stage_marker.py:322-326`, `models/room.py:60-100`, `models/agent_session.py:155`/`163` |
| Prerequisites met | PASS | Redis reachable; venv on the `.python-version` pin |
| Cross-references | FAIL (fixed in this revision) | Task 8 mandated a "three-class table" the Problem and Documentation sections described as four (NIT 1); Task 7 mandated executing every Verification row while three rows were not executable (concern 4) |
| Verification baselines | FAIL (fixed in this revision) | One row was mis-baselined and therefore vacuous: `not is_abort` in `agent/steering.py` measures **1**, not the recorded 0. Every other baseline re-measured and confirmed: `room_id` = 22, `_queue_key(session_id)` = 7, `sed` span = 54 lines / 0 `except`, `query.filter` = 0, `from models` = 0, `_source` = 0, `created_at is not None` = 0 in both `bridge/telegram_bridge.py` and `agent/health_check.py`, `_leg` = 0 across all four modules, `room_id_for_session` = 2 (health_check) / 2 (runner) / 0 (bridge, watchdog, session_health, steer_child), `room_id=room_id` in health_check = 1, `list(AgentSession.query.filter` = 3, `bridge/telegram_bridge.py:1801` = exactly 100 chars, `max_age_seconds` = 0, `steering_room_max_age_s` in settings = 0, all four stale-docstring rows = 1 |

### Round-5 revision applied (2026-08-07)

All seven findings addressed; every claim was re-verified against the working tree before its fix
was written, not taken from the critique's text. Re-measured this round: `agent/steering.py:110-120`
(`key = _queue_key(session_id)` above the auto-detect, `"timestamp": time.time()` unconditional),
`grep -c "not is_abort" agent/steering.py` = **1** (confirming the vacuous row), the two unanalyzed
consumers located and read (`agent/session_executor.py:2465-2473` → `_reenqueue_leftover_steering`
at `:928`, which calls `enqueue_agent_session`; `agent/session_pickup.py:210-244`
`_drain_startup_steering`), all three requeue writers confirmed to rebuild the payload from named
fields only (`agent/health_check.py:479-485`, `agent/session_runner/runner.py:610-615`,
`agent/session_executor.py:1932-1938` — none carries `timestamp`), and the four
`_repush_messages` call-site line lengths (58/58/46/46 against `line-length = 100`).

Two of the seven were **decisions, not wording patches**:

1. **D5 bounds origination age.** The alternative — reword D5 to "age since last push" — was
   rejected: `agent/health_check.py:553-564` requeues on the ordinary non-abort PostToolUse path, so
   the reset would be the dominant path and the bound would measure nothing that matters. spike-6's
   whole finding is that an undrained Room message is otherwise immortal; a bound that resets on the
   common path does not fix that. Cost is one keyword-only parameter and three forwards.
2. **The terminal leftover drain reads legacy only.** The critique offered "no edit, accept the
   escalation" or "partition `leftover` by `_leg` and re-push Room-sourced entries". Both were
   rejected in favour of a one-keyword `room_id=None` at `agent/session_executor.py:2468`: accepting
   it lets any session teardown convert another Room's instruction into a *spawned session*, which
   is strictly heavier than the recoverable wrong-recipient hazard D2 accepts; partitioning adds a
   fourth requeue writer. Room-leg messages need no rescue — durability is what the Room key is for.

Net effect on plan size: one Verification row deleted outright, one struck row removed from the
table, one replaced by an assertion inside a test that already exists, and Task 3's
source-formatting mandate deleted. The table is smaller and every row is now executable verbatim.

### Round-4 log (history)

Round 4 (FULL war room: Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **NEEDS REVISION** — 2 blockers, 3 concerns, 2 nits. **All seven addressed; revision
applied 2026-08-07.** Every finding was independently re-verified against the working tree before
its fix was written — the `ruff format` wrap re-reproduced, the `created_at or 0` `TypeError`
re-reproduced in the venv, every cited line re-read, and `has_steering_messages`' caller set
re-grepped. The revision also surfaced **three things no critique round caught**, recorded in the
Addressed-By cells rather than folded in silently:

1. A **third** requeue path, `agent/session_executor.py:1933` — so the writer count is thirteen, not
   twelve, and spike-1's round-2 "count correction" was itself wrong.
2. `tests/unit/test_public_api_contract.py:31-34` pins `push_steering_message`'s exact signature
   string; adding `room_id` breaks it and no prior draft's Test Impact listed the file. The build
   would have gone red on a correct implementation.
3. `TimeoutSettings` names every field with an `_s` suffix, so the plan's
   `steering_room_max_age_seconds` was off-convention; renamed to `steering_room_max_age_s`.

Every finding below was verified by
direct execution or direct read against the working tree before it was written: the `ruff format`
wrap was reproduced by running the formatter on the mandated shape, the `created_at or 0` `TypeError`
was reproduced in the venv, and every cited line was read.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | History & Consistency | The Verification row **Bridge materializes before sorting** (`grep -c "list(AgentSession.query.filter" bridge/telegram_bridge.py` >= 6, baseline 3) is unsatisfiable by a correctly-formatted build, so it fails a correct implementation. `bridge/telegram_bridge.py:1801` is already exactly 100 chars — the `line-length = 100` limit — so wrapping it in `list(...)` makes it 106 and `ruff format` rewrites it to `sessions = list(\|newline\|    AgentSession.query.filter(...)`. `:1860` and `:2169` are already multi-line calls, so they wrap the same way. The existing precedent is in the same file: `:2059-2061` and `:2065-2067` are multi-line `list(` + `AgentSession.query.filter(` and are excluded from the baseline of 3 for exactly this reason. The plan therefore holds two Verification rows that cannot both pass: `ruff format --check .` == 0 and the >= 6 grep. | **Fixed — row deleted.** Reproduced independently before acting: `bridge/telegram_bridge.py:1801` measures exactly 100 characters, and `ruff format --line-length 100` on the wrapped shape at that indentation splits it into three lines. The `list(AgentSession.query.filter` Verification row is deleted, with the reason recorded in the deleted-rows list above the table; `test_room_derivation_sites_sort_before_selecting` is now named as the **sole** materialization guard, in Task 2, Task 6, Race 4 part 3 and the Success Criteria. Task 2 gains an explicit "expect the formatter to wrap this, do not fight it, do not add `# fmt: off`" bullet. | Delete the `list(AgentSession.query.filter` row rather than re-tuning it — a substring grep cannot pin a call shape the formatter is free to wrap. The plan already owns the correct instrument: `test_room_derivation_sites_sort_before_selecting` asserts via AST that the sorted name is bound to an `ast.Call` to `list`, which is wrap-insensitive and covers exactly what the grep was reaching for. Make the AST test the sole materialization guard. Verified: `ruff format --line-length 100` on the plan's own mandated line at that indentation splits it across three lines. |
| BLOCKER | Risk & Robustness | D1's boundary — session-scoped diagnostics stay on the legacy key — is defeated by the two **requeue** paths the plan deliberately flips. Both requeue functions re-push messages drained from *either* leg and, post-flip, target the Room key unconditionally, so a diagnostic written to legacy by `monitoring/session_watchdog.py:557` or `agent/session_health.py:3467` is laundered onto the shared Room leg on its first drain-and-requeue cycle. `agent/health_check.py:514` drains both legs, then `_handle_steering` re-pushes survivors via `_repush_messages` at 530/536/560/564 — Task 3 makes all four pass `room_id=room_id`. Symmetrically `agent/session_runner/runner.py:587-601` `_default_steering_pop` drains both legs into `self._pending_steers` and `_requeue_pending_steers` (616-635) pushes each back through `_default_steering_push` (603-615), which Task 2 flips. These are exactly the wedged-session paths D1 names as most likely to die undrained. | **Fixed by new decision D6 — leg-preserving requeue**, and the investigation found a **third** requeue path the critique missed: `agent/session_executor.py:1933` (`_push_steering`, alias imported at 1930), which re-pushes `steering_msgs[1:]` after a both-legs drain at 1908-1910. It had no disposition in any prior round — spike-1's round-2 "count correction" to twelve was itself wrong; the count is thirteen. **Design chosen: shape (a)**, a transient in-memory `_leg` stamp set by `pop_all_steering_messages`, never persisted (the requeue writers build payloads from named fields), with each writer gating `room_id=room_id if msg.get("_leg") == "room" else None` and **absent `_leg` defaulting to legacy**. Shape (b), sender-string suppression, was rejected in D6 and Task 3: a sender is not a boundary, and a writer that renames its sender would silently resume laundering with no test to catch it. Keeping the requeues wholly on legacy was rejected too, with the rationale stated in D6 — it destroys the durability property on the first requeue, in exactly the wedge-then-die case the feature exists for. The Problem section's class table goes from three classes to four; spike-2 carries an explicit round-4 reversal (its "upgrade is safe" reasoning held only under the uniform flip that D1 retired); Risk 3 is rewritten to withdraw the false "structurally impossible" claim and to state separately that a requeue already drops `front` today, a pre-existing property this plan neither introduces nor fixes. Task 3 is retitled and rewritten around D6; the D6 test set (six cases including the anti-laundering regression) is added to Task 6 and the Failure Path strategy; four Verification rows added. | The two requeue paths must not pass a truthy `room_id` for a legacy-sourced message. Two implementable shapes: (a) have `pop_all_steering_messages` return the legs separately, or tag each entry with a transient in-memory `_leg` key stripped before re-push so the persisted payload stays byte-identical and the no-provenance-field criterion still holds, then pass `room_id=room_id` only for Room-leg entries; or (b) suppress promotion by sender — `room_id=None if msg.get("sender") in {"watchdog", "session-health", DRAFTER_FALLBACK_SENDER} else room_id`. Either way Risk 3 must be rewritten: its claim that the `front=True` writer at `agent/session_health.py:3473` can never reach a Room key is false, because the requeue path drops `front` and RPUSHes onto the Room leg. |
| CONCERN | Risk & Robustness | Race 4 enumerates five unsorted row selections, all in `bridge/telegram_bridge.py`, but a sixth exists and is the one feeding the write path Task 3 creates: `agent/health_check.py:621-627` binds `sessions = AgentSession.query.filter(session_id=session_id)` (no status filter, no `list(...)`, no sort), takes `s = sessions[0]` at 623, derives `steering_room_id = room_id_for_session(s)` at 627, and hands it to `_handle_steering` at 653 — which after Task 3 is the re-push target key. A superseded row selected here routes the re-push to a Room the live session never drains: silent total steering loss on the PostToolUse hook path. The success criterion says "No caller *derives a Room from* an unsorted row selection", so this site is in scope by the plan's own wording, yet no task edits it and every Race-4 Verification row greps only `bridge/telegram_bridge.py`. | **Fixed.** `agent/health_check.py:621` is added as Race 4's **sixth** site — in Race 4's per-site trigger table, Task 2's materialize-then-sort table, the Technical Approach, the Success Criteria ("all six multi-row selections") and Task 6's sort-census scope (six selections, not five). A new Verification row greps `created_at is not None` in `agent/health_check.py` (baseline 0). The swallow-all `except Exception` at 620/628 is called out in three places as the reason this is the *worst* of the six: a bare `.sort()` there raises `AttributeError` that is silently logged at debug, leaving `steering_room_id = None` and demoting the hook to a legacy-only drain **and** a legacy-only re-push. | Add `agent/health_check.py:621` to Race 4's table and to Task 2 with the same materialize-then-sort edit, plus a Verification row scoped to that file. Gotcha: the block sits inside a `try:`/`except Exception` at 620/628 that only `logger.debug`s, so a bare `.sort()` on the `QueryBuilder` here raises `AttributeError` that is **swallowed**, leaving `steering_room_id = None` and silently demoting the hook to a legacy-only drain and legacy-only re-push — the same silent shape the plan already flags for `bridge/telegram_bridge.py:2593`. |
| CONCERN | Risk & Robustness | The Failure Path bullet mandates pinning the sort "with a row whose `created_at` is `None` to prove the sort does not `TypeError` comparing `None` to float", but that test cannot pass with the mandated `created_at or 0` idiom: `created_at` is not a float. `models/agent_session.py:163` declares `created_at = SortedField(type=datetime, partition_by="project_key")`, so a `None`-row (key `0`, an `int`) beside a populated row (key `datetime`) raises `TypeError: '<' not supported between instances of 'datetime.datetime' and 'int'` — reproduced directly. The repo already carries a regression test for this exact bug class at `tests/unit/test_pending_merge_window_age.py:5`. | **Fixed by retiring the idiom, keeping the test.** The `TypeError` was re-reproduced in the venv (`'<' not supported between instances of 'int' and 'datetime.datetime'`). The sort key is now `(s.created_at is not None, s.created_at)` everywhere in this plan: Race 4 part 1's code block, Task 2, the Success Criteria, and the Failure Path bullet — which is *strengthened* rather than weakened, mandating a **two-row** list (one `None`, one populated), because a single-row list never exercises the comparison and would pass on the broken idiom. The `created_at or 0` Verification row is replaced by a `created_at is not None` row plus a diff anti-criterion that the retired idiom is not reintroduced. A Rabbit Hole records that the 26 other repo-wide uses are latent instances of the same bug and are out of scope. | Either use a type-correct sentinel — `key=lambda s: s.created_at or datetime.min.replace(tzinfo=timezone.utc)`, matching the tz-aware `utc_now()` stamps the model writes, or `key=lambda s: (s.created_at is not None, s.created_at)` — or keep `or 0` for idiom consistency and rewrite the Failure-Path bullet to assert a single-row list (which never compares) or delete it. Do not ship the idiom and the contradicting test together. |
| CONCERN | Scope & Value | The plan scopes `pop_steering_message` out of Task 4 on the explicit ground that it has zero production callers, then in the same task rewrites `has_steering_messages` — which also has zero production callers — from an O(1) `r.llen()` into an O(N) `_peek_list()` that LRANGEs the whole list and `json.loads` every entry. A repo-wide grep excluding `tests/` returns only its definition at `agent/steering.py:200` and the `agent/__init__.py` re-export at 38/76. The same evidence that scopes one function out scopes the other out. | **Fixed by scoping it out**, applying the plan's own rule consistently. Verified: non-test references are its definition at `agent/steering.py:200` and the `agent/__init__.py` re-export at 38/76. D5's reader table now marks it "unchanged, deliberately" with the false operator-surface justification named and corrected; Task 4 wires **two** Room-leg sites, not three, and gains a "do NOT touch" bullet; the `max_age_seconds` Verification row drops to `>= 4`; the `has_steering_messages` `sed` row is deleted and replaced by an anti-criterion; the staleness test set drops from four cases to three; Architectural Impact, Documentation and Success Criteria all follow. | If `has_steering_messages` is scoped out, drop the Verification row `grep -c "max_age_seconds" agent/steering.py` from >= 5 to >= 4 and delete the "**`has_steering_messages` Room leg honors the bound**" `sed` row, or a correct build fails the gate. If it is kept in, the honest justification is API coherence, not the operator surface: `tools/valor_session.py:1031` calls `peek_steering_messages`, never `has_steering_messages`. |
| NIT | Scope & Value | Race 4's stated trigger is that a superseded row can carry a different `chat_id`, but three of the five sites filter on `status` and `superseded` IS a status value (`models/session_lifecycle.py:79`), so superseded rows cannot appear in those result sets: `:1801` filters over `("running","active")`, `:1860` over `("pending","running","active")`, `:2169` over `("running","active","pending")`. The hazard exists only at `:2593` and `:2641`, which have no status filter — yet the three status-filtered sites are the ones the plan flags as riskiest to edit. | **Fixed.** Race 4's trigger is now a per-site table: the three status-filtered sites (`:1801` `("running","active")`, `:1860` `("pending","running","active")`, `:2169` `("running","active","pending")`) cannot see a `superseded` row and are restated as "two concurrently-live rows for one `session_id`", explicitly flagged as not directly observed; the three unfiltered sites (`:2593`, `:2641`, `agent/health_check.py:621`) are where the superseded hazard is real. The break-on-first-bucket caveat is stated, and the sort is described there as a within-bucket tie-break with **materialization** as the load-bearing half. The Success Criterion is reworded to match, and Task 6's superseded-row test is directed at an unfiltered site so it exercises the hazard it claims to. | Restate the trigger for the three status-filtered sites as "two concurrently-live rows for one `session_id`" and say whether that is observed. Also note the sort there is only a within-status-bucket tie-break: the enclosing `for check_status in (...)` loop breaks on the first status returning any row, so a row in an earlier bucket beats a newer row in a later one regardless of the sort. The Success Criterion's "sort newest-first by `created_at` before selecting" overstates what the edit achieves. |
| NIT | History & Consistency | Task 4 says to follow "the neighbouring `bridge_msg_claim_ttl_seconds`" when adding the new field to `TimeoutSettings`, but that field is on `FeatureSettings` (`config/settings.py:602` opens the class; the field is at `:776`), not neighbouring. Separately, Race 4 part 1 cites "the ten existing uses of the `created_at or 0` idiom"; the actual count excluding `.venv`/`.worktrees` is 26 (about 20 non-test). | **Fixed, and the field is renamed.** `TimeoutSettings` spans 184-452; real neighbours named for placement are `agent_session_retain_ttl_s`, `last_processed_ttl_s` and `dedup_record_ttl_s`, with the GRAIN-OF-SALT style cited from `dedup_record_ttl_s` / `catchup_disabled_warn_hours` — both inside the same class, so `FeatureSettings` is no longer cited as a model, only as the wrong destination. Measuring for the fix surfaced a second problem: every field in `TimeoutSettings` carries an `_s` suffix, so `steering_room_max_age_seconds` was off-convention; renamed to **`steering_room_max_age_s`** / `TIMEOUTS__STEERING_ROOM_MAX_AGE_S` throughout the live plan (history blocks keep the old name as written). A new Verification row imports the setting and prints it, failing loudly on a `FeatureSettings` placement rather than passing silently. The "ten existing uses" count is corrected to 26 — and moot, since the idiom is retired. | The field must land inside `TimeoutSettings`, not `FeatureSettings`, or `TIMEOUTS__STEERING_ROOM_MAX_AGE_SECONDS` will not resolve while the `.env.example` completeness test still passes — a silently dead env override. Cite the comment style as "the `bridge_msg_claim_ttl_seconds` GRAIN-OF-SALT comment on `FeatureSettings`" and name a real `TimeoutSettings` neighbour for placement. |

### Structural Check Results (round 4)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | build-writer -> build-callers/build-repush/build-staleness -> build-docstrings/build-tests -> validate-all -> document-feature; no cycles |
| File paths exist | PASS → **amended in revision** | All referenced source paths exist, but the census was incomplete: re-grepped at revision time there are **thirteen** `push_steering_message` call sites (the missing one is `agent/session_executor.py:1933`) and **six** unsorted row selections (the missing one is `agent/health_check.py:621`). The four `_repush_messages` sites (530/536/560/564) and `docs/plans/durability-room-job-agentrun.md:673` are confirmed at the cited lines. |
| Cross-references | FAIL → **resolved in revision** | D1's "diagnostics stay legacy" boundary was contradicted by Task 2's `_default_steering_push` flip and Task 3's `_repush_messages` flip (now D6, leg-preserving, plus a third path at `agent/session_executor.py:1933` the critique did not find); Race 4's five-site list omitted `agent/health_check.py:621` (now the sixth site); Task 4's `has_steering_messages` inclusion contradicted its own zero-production-callers rule (now scoped out) |
| Verification rows executable | FAIL → **resolved in revision** | `grep -c "list(AgentSession.query.filter" >= 6` could not pass alongside `ruff format --check .` == 0; the row is deleted and the AST census test is the sole materialization guard |
| Verification baselines | PASS | Re-measured on the working tree: `room_id` in `agent/steering.py` = 22, `_queue_key(session_id)` = 7, `sed` span = 54 lines / 0 `except`, `created_at or 0` in `bridge/telegram_bridge.py` = 0, `list(AgentSession.query.filter` = 3 (2289/2593/2641), `query.filter` in `agent/steering.py` = 0, `from models` = 0, `hold` label exists, `SKIPPABLE_STAGES = {"PLAN","CRITIQUE"}` |
| Prerequisites met | PASS | Redis reachable; venv on `.python-version` pin |

### Round-3 log (history)

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
