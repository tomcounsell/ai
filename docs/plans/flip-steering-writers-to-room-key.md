---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2642
last_comment_id: none
revision_applied: false
revision_applied_at: 2026-08-07T06:53:48Z
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

**Desired outcome:** every steering write targets the Room key when a Room resolves, so a steer
outlives its target session and is drained by whichever session next serves that Room. The legacy
key remains the fallback when no Room resolves, and remains fully drained by the untouched
dual-read consumers.

## Freshness Check

**Baseline commit:** `e6d0e2bc7`
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

Six spikes. Five resolved by code-read; spike-5 was measured against live Redis during the revision
pass. **spike-1b and spike-5 together reversed the plan's central design choice**: the writer no
longer resolves a Room itself — the caller supplies it. Everything below reflects that reversal.

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
- **Finding**: **Eleven of twelve have a session object in hand, or one stack frame up.**

  | Site | Object in scope | Notes |
  |---|---|---|
  | `bridge/telegram_bridge.py:979` (`_ack_steering_routed`) | via callers | helper takes `session_id: str`; all 5 callers (1809, 1835, 1868, 2070, 2178) hold `matching_session` / `fresh_session` |
  | `bridge/telegram_bridge.py:2617` | `session` (loaded 2593-2594) | — |
  | `bridge/telegram_bridge.py:2650` | `active_edit` | its `.session_id` equals the pushed `new_session_id` |
  | `tools/valor_session.py:856` | `session` | resume path, distinct from the status peek at 1031 |
  | `agent/session_executor.py:853` | `session` | passes `session.session_id` |
  | `agent/session_health.py:3467` | `entry: AgentSession` | typed parameter |
  | `agent/session_runner/runner.py:610` | `self._agent_session` | sibling `_default_steering_pop` (599-601) already calls `room_id_for_session` on it |
  | `monitoring/session_watchdog.py:557` (`_inject_watchdog_steer`) | via callers | all 3 callers (1003, 1023, 1059) pass `session.session_id` from a loaded row |
  | `agent/health_check.py:480` (`_repush_messages`) | `room_id` one frame up | `_handle_steering` receives a resolved `room_id`; `watchdog_hook:627` produced it |
  | `agent/output_handler.py:1227` | `session` | `session_id` derived from it at 1153 |
  | `scripts/steer_child.py:119` | `child` | validated at 99-112 |
  | `scripts/migrate_steering_queue_drain.py:126` | **none** | raw Redis hash scan; deliberately ORM-free |

- **Confidence**: high
- **Impact on plan**: **Decisive.** It removes any need for the writer to query for a session.
  `room_id_for_session(session)` is pure `getattr` + string work with zero Redis I/O
  (`models/room.py:65-100`), so an explicit `room_id` argument from each caller is free. See
  spike-5 for why the alternative is not viable.

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
  `tests/integration/test_steering.py:1746 test_handle_steering_drains_room_leg` asserts in its
  docstring and body that a Room-drained message is re-pushed **to the legacy list** — that
  assertion inverts once `_handle_steering` forwards its `room_id`, and must be updated.
- **Confidence**: high
- **Impact on plan**: "No `room_id` → legacy key" is a hard correctness requirement, not a nicety.
  Pins the one test needing UPDATE.

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
- **Impact on plan**: Adds a mandatory in-scope regression fix. The issue's "do not touch the
  consumers" boundary is about not *retiring* the legacy read leg; giving a missed consumer the
  same dual-read leg its five siblings already have is completing #2622, not phase 3.
- **Semantics correction (critique)**: reading the Room key first and falling back to legacy only
  when the Room leg is *empty* does **not** close the guard. A Room tail from any other sender
  masks a `drafter-fallback` message still sitting on the legacy leg, and
  `agent/output_handler.py:1162` fails open exactly as Risk 3 describes. The guard is a *sentinel
  search*, not a tail read: both tails are read and either one matching
  `DRAFTER_FALLBACK_SENDER` wins. This also keeps the peek consistent with the drain, which reads
  legacy FIRST (`agent/steering.py:158-160`).

## Data Flow

1. **Room derivation at the call site (new)** — a caller that holds an `AgentSession` computes
   `room_id_for_session(session)`. That is pure `getattr` plus string formatting
   (`models/room.py:65-100`) — no Redis, no ORM query, no measurable cost. Eleven of the twelve
   non-test callers can do this (spike-1b).
2. **Entry point** — the caller invokes
   `push_steering_message(session_id, text, sender, ..., room_id=<derived or None>)`.
3. **Key selection (changed)** — `_room_queue_key(room_id)` when `room_id` is truthy,
   `_queue_key(session_id)` otherwise. No lookup, no exception handling, no fallback logic beyond
   the truthiness test. Payload JSON is byte-identical to today.
4. **Storage** — RPUSH (or LPUSH when `front=True`) onto the selected list.
5. **Drain (unchanged)** — the worker's turn-boundary `_default_steering_pop`, the watchdog hook's
   `_handle_steering`, and three other consumers each drain legacy-then-Room. A message on the Room
   key is served to whichever session in that Room next reaches a turn boundary — including a
   session created *after* the original target died. That is the durability property.
6. **Abort re-push (changed target)** — `_handle_steering` drains both legs; on an abort it
   forwards its already-resolved `room_id` into `_repush_messages`, which re-pushes siblings to the
   Room key.
7. **Output** — the steer reaches a live session instead of being stranded.

## Architectural Impact

- **New dependencies**: none. `agent/steering.py` gains **no** new import — it never touches the
  model layer. `models.room.room_id_for_session` is imported at the calling modules, which mostly
  import it already (`agent/session_runner/runner.py`, `agent/session_executor.py`,
  `agent/output_handler.py`, `tools/valor_session.py`). New importers:
  `bridge/telegram_bridge.py`, `monitoring/session_watchdog.py`, `agent/session_health.py`,
  `scripts/steer_child.py` — function-local with `# noqa: PLC0415` where the module already uses
  that pattern.
- **Interface changes**: `push_steering_message`, `_repush_messages`, `peek_steering_sender`, and
  the two thin helpers (`bridge/telegram_bridge.py::_ack_steering_routed`,
  `monitoring/session_watchdog.py::_inject_watchdog_steer`) each gain an optional
  `room_id: str | None = None` keyword. All are backward-compatible additive keywords; no caller
  is *forced* to change, but every caller that can supply a Room does.
- **Coupling**: unchanged. `agent.steering` stays a pure Redis-key module with no model-layer
  dependency — deliberately, because the alternative (an internal `AgentSession` lookup) costs
  ~2.4s per push (spike-5) and would create a new steering→model edge.
- **Data ownership**: this is the point of the change. The steering inbox moves from being owned by
  a mortal `AgentSession` to being owned by an immortal `Room`.
- **Reversibility**: high. Reverting the writer restores legacy-only writes; consumers dual-read,
  so any messages already sitting on Room keys keep draining after a revert. There is no data
  migration and no schema change.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**

- PM check-ins: 1-2 (the fleet deploy gate is a human confirmation, and the missed-consumer
  regression is a scope expansion that needs a nod)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | The steering integration suite runs against real Redis |
| On-pin venv | `.venv/bin/python -c "import sys,pathlib; assert '.'.join(map(str,sys.version_info[:2])) in pathlib.Path('.python-version').read_text()"` | `scripts/pytest-clean.sh` aborts on an off-pin venv (#2617) |

## Solution

### Decisions

Three questions were open at critique. All three are now decided; there is no Open Questions
section left to answer.

- **D1 — the `peek_steering_sender` Room leg ships in this release.** spike-4 found a sixth
  consumer #2622 missed. Shipping the writer flip while knowingly leaving the drafter self-draft
  guard fail-open is worse than touching one consumer. The issue's "do not touch the consumers"
  constraint is about not *retiring* the legacy read leg (that is phase 3); it was never about
  withholding a Room leg from a consumer the previous release overlooked. Tasks 5 and 7 are
  therefore in scope, unconditionally. The PR body states this explicitly so review does not read
  it as scope creep.
- **D2 — same-Room cross-delivery via the shared `system` addressee is accepted for the soak.**
  It is not a defect of this change; it is the Room durability model working as designed
  (see Risk 2). It is documented in `docs/features/session-steering.md` as behavior, not as a
  caveat. Consumer-side `target_agent` filtering stays a No-Go, filed as its own issue only if a
  real mis-delivery is observed.
- **D3 — the merge hold is mechanical, not prose.** A `[ORDERED]` No-Go in a plan document has no
  effect on `/do-merge`, which sees a review-clean PR and merges it. Two mechanisms, in order of
  force:
  1. **The PR is opened as a draft.** GitHub itself refuses to merge a draft PR, so this holds even
     against a merge path that ignores labels entirely. This is the actual gate.
  2. **The PR carries the `hold` label.** This is the human-visible signal. Note the label is
     `hold` — that is what exists in this repo (`gh label list`); there is **no** `do-not-merge`
     label, so the name the critique suggested would have been applied to nothing and silently
     no-opped.

  The PR body states the removal condition verbatim: *"Do not mark ready for review or merge until
  fleet-wide `/update` past PR #2622 is confirmed. Removal owner: Valor Engels (repo operator)."*

### Key Elements

- **Room derivation at the call site, never inside the writer.** `push_steering_message` gains an
  optional `room_id` and does nothing but choose a key from it. It performs no lookup. This is
  the single most important shape decision in the plan: an internal
  `AgentSession.query.filter(session_id=...)` resolution measures ~2.4s per call (spike-5) on a
  path that includes the inbound-Telegram fast path.
- **Every caller that holds a session supplies the Room.** `room_id_for_session(session)` is free
  (pure attribute reads). Eleven of twelve non-test callers can call it (spike-1b), including two
  thin helpers that gain a pass-through parameter.
- **No `room_id` → legacy key.** The fallback is structural, not defensive: a falsy `room_id`
  selects `_queue_key(session_id)`. No message is ever dropped for lack of a Room, and every
  existing test (which passes no `room_id`) is unaffected.
- **Sentinel-preferring peek.** `peek_steering_sender` reads *both* tails and returns
  `DRAFTER_FALLBACK_SENDER` if either matches, so the drafter self-draft guard cannot fail open.
- **Docstring truth-up across three modules.** `agent/steering.py` (module docstring and
  `_room_queue_key`), `agent/health_check.py:511-513`, and
  `agent/session_runner/runner.py:589-591` each currently assert that writers do not target the
  Room key. After this change all three are false.

### Flow

Supervisor sends a steer → caller derives `room_id_for_session(session)` →
`push_steering_message(session_id, ..., room_id=...)` → message lands on
`steering:room:{project|addressee}` → target session dies before its next turn boundary → a new
session opens in the same Room → its turn-boundary drain reads the Room leg → **the steer is
delivered**. That last hop is the durability property, and it is what the new end-to-end test
asserts (see Failure Path Test Strategy).

Fallback branch: caller has no session object, or the session has no `project_key`, so `room_id`
is `None` → message lands on `steering:{session_id}` → drained by the same dual-read consumers
exactly as today.

### Technical Approach

- **`agent/steering.py::push_steering_message`** — add `room_id: str | None = None`. Replace
  `key = _queue_key(session_id)` with a two-branch selection: `_room_queue_key(room_id)` when
  `room_id` is truthy, `_queue_key(session_id)` otherwise. Nothing else in the function changes;
  the payload dict, the RPUSH/LPUSH split, and the existing `logger.info` (which interpolates
  `key`, so it self-describes which leg was written) are untouched. **No import is added to this
  module and no exception handler is introduced** — there is nothing that can raise.
- **Caller threading** — each of the following passes `room_id=room_id_for_session(<session>)`:

  | File:line | Session object | Edit |
  |---|---|---|
  | `bridge/telegram_bridge.py:979` `_ack_steering_routed` | via 5 callers (1809, 1835, 1868, 2070, 2178) | add `room_id` param, forward; each caller derives from its loaded session |
  | `bridge/telegram_bridge.py:2617` | `session` | pass `room_id` |
  | `bridge/telegram_bridge.py:2650` | `active_edit` | pass `room_id` |
  | `tools/valor_session.py:856` | `session` | pass `room_id` (resume path only; the status peek at 1031 is untouched) |
  | `agent/session_executor.py:853` | `session` | pass `room_id` |
  | `agent/session_health.py:3467` | `entry` | pass `room_id` |
  | `agent/session_runner/runner.py:610` | `self._agent_session` | pass `room_id`, mirroring `_default_steering_pop` at 599-601 |
  | `monitoring/session_watchdog.py:557` `_inject_watchdog_steer` | via 3 callers (1003, 1023, 1059) | add `room_id` param, forward |
  | `agent/output_handler.py:1227` | `session` | pass `room_id` |
  | `scripts/steer_child.py:119` | `child` | pass `room_id` |
  | `scripts/migrate_steering_queue_drain.py:126` | **none** | **no change** — the script reads raw Redis hashes and deliberately avoids the ORM. It stays on the legacy leg, which is correct: it is re-pushing legacy-sourced messages that dual-read consumers still drain. |

- **`agent/health_check.py::_repush_messages`** — add `room_id: str | None = None` and forward it
  to the `push_steering_message` call inside. `_handle_steering` passes its own already-resolved
  `room_id` at **all four** call sites: 530, 536, 560, 564. Two of those (536, 564) are retries
  inside `except` blocks; missing them silently demotes to legacy.
- **`agent/steering.py::peek_steering_sender`** — add `room_id: str | None = None`. Read the tail
  of the legacy key and, when `room_id` is given, the tail of the Room key. Decode both, then
  prefer the sentinel: if *either* sender equals `DRAFTER_FALLBACK_SENDER`, return it; otherwise
  return whichever tail exists, legacy first (matching the drain order at
  `agent/steering.py:158-160`). A one-tail read is insufficient — see the spike-4 semantics
  correction. `DRAFTER_FALLBACK_SENDER` must not be imported into `agent/steering.py` (that would
  invert the dependency); the function returns a sender and the *caller* compares. So the shape
  is: return the sender that the caller's guard is looking for when present. Implement by
  accepting an optional `prefer_sender: str | None = None`, or by returning both tails — the
  builder picks; the binding requirement is that a `drafter-fallback` message on *either* leg is
  observable by `agent/output_handler.py:1160-1162`.
- **`agent/output_handler.py:1160-1162`** — derive the Room from the `session` object already in
  scope and pass it to `peek_steering_sender`.
- **Docstrings** — rewrite to the new status quo, no "formerly"/"used to" narration:
  `agent/steering.py:15-22` (module), `agent/steering.py:48-55` (`_room_queue_key`),
  `agent/health_check.py:511-513` (comment), `agent/session_runner/runner.py:589-591`
  (`_default_steering_pop` docstring: "Writers are unchanged in this release.").

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
- [ ] `room_id_for_session` is called at eleven new sites. It is total for any object
  (`getattr`-based, returns `None` without a `project_key`), so no caller needs a guard. Pin with
  a test passing an object with no `project_key` attribute at all.

### The durability property (the reason this plan exists)

Every other test below asserts *which Redis list a write lands on*. That is a proxy. The
**binding** test asserts the behavior the feature is for: a steer written for session A is
delivered to a different session B serving the same Room.

- [ ] **`test_steer_survives_target_session_and_reaches_room_sibling`** — persist two
  `AgentSession` rows sharing `project_key="test-room-durability"` with `chat_id=None` on both, so
  `models/room.py:65-92` maps each to `SYSTEM_ADDRESSEE` and `room_id_for_session` returns an
  identical composite for both. Then:
  `push_steering_message(session_a.session_id, "do X", "Tom", room_id=room_id_for_session(session_a))`,
  finalize/delete session A, and assert
  `pop_all_steering_messages(session_b.session_id, room_id=room_id_for_session(session_b))`
  returns the message. Tear down both rows via the ORM scoped by the `test-` project key
  (`instance.delete()`, never raw Redis).
- [ ] **Negative twin** — the same scenario with `room_id=None` on the push must NOT deliver to
  session B. This is what proves the test is measuring the Room leg and not an artifact.

### Empty/Invalid Input Handling

- [ ] `room_id=None` (the default, and what every existing test passes) → legacy key. Test it.
- [ ] `room_id=""` → falsy → legacy key. Test it.
- [ ] Session has no `project_key` → `room_id_for_session` returns `None` → caller passes `None` →
  legacy key. Test it (this is the issue's explicit acceptance criterion).
- [ ] `session_id=""` with a truthy `room_id` → the Room key is still the correct target; the
  legacy key would be the nonsensical `steering:`. Assert the Room key wins.

### Error State Rendering

- No user-visible output surface changes. The steering write is internal; its only rendering is the
  existing `logger.info` line, which already names the target key and therefore self-documents
  which leg was chosen.

## Test Impact

- [ ] `tests/integration/test_steering.py::TestSteeringDualRead::test_handle_steering_drains_room_leg`
  (line ~1746) — UPDATE: its docstring and final assertion both state the re-push targets the legacy
  list. Post-flip the re-push targets the Room key. Rewrite the assertion to drain the Room leg and
  correct the docstring.
- [ ] `tests/integration/test_steering.py::TestAbortSiblingPreservation::test_abort_repushes_non_abort_siblings`
  (line ~1770) — UPDATE: called with no `room_id`, so siblings still land on legacy and the test
  passes as-is. Add a **sibling case** with a `room_id` asserting Room-sourced siblings return to
  the Room key (the issue's explicit acceptance criterion).
- [ ] `tests/integration/test_steering.py` remaining ~64 tests — no change expected: none passes a
  `room_id` to `push_steering_message`, so every one keeps writing to the legacy key by
  construction. If any turns red, the fallback branch is wrong — fix the code, not the test.
- [ ] `tests/unit/test_output_handler.py` (11 `peek_steering_sender` patch sites) — UPDATE where
  needed: they patch the symbol wholesale, so the added keyword should not break the bind, but
  the call site now passes `room_id=`, which a `MagicMock` accepts and an `autospec`'d or
  hand-written stub may not. Verify each; the critique noted these patches are also why no
  *existing* test can catch a fail-open guard, so the new guard test must be additive.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` — no change: it AST-walks for
  `push_steering_message` call/dedup pairing, which the added keyword does not affect.
- [ ] Tests that exercise `bridge/telegram_bridge.py::_ack_steering_routed` or
  `monitoring/session_watchdog.py::_inject_watchdog_steer` — VERIFY: both gain a keyword-only
  optional parameter, so positional callers are unaffected, but any `autospec` mock of these
  helpers must be re-checked.
- [ ] New test additions go into `tests/integration/test_steering.py`: the durability property
  test and its negative twin, the legacy-fallback matrix, the Room-write happy path, sibling
  re-push provenance on all four `_repush_messages` paths, payload-shape invariance, and the
  `peek_steering_sender` sentinel-on-either-leg test.

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
- **Solving same-Room cross-delivery.** See Risk 2. The `target_agent` field exists and is the
  natural gate, but no consumer filters on it today. Wiring that filter is its own release.
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
is drained by whichever system session next reaches a turn boundary — possibly session B. Writers
into that Room include `monitoring/session_watchdog.py:557` and `agent/session_health.py:3467`.
**Mitigation:** This is inherent to the Room durability model — outliving the target session is the
*point*, and PR #2627 already established the system Room as a legitimate write target. It is
accepted, not fixed, in this release. Documented explicitly in `docs/features/session-steering.md`
so the next person debugging a mis-delivered steer finds the answer. The `target_agent` field is
the designed gate if this proves harmful; wiring a consumer-side filter on it is a separate issue,
filed only if a real mis-delivery is observed during the soak.

### Risk 3: The drafter self-draft guard fails open

**Impact:** Without the spike-4 fix, `peek_steering_sender` can never see the drafter's own
Room-key push, so `agent/output_handler.py:1162` stops short-circuiting and the drafter fallback can
re-enter — a message-amplification loop on a live chat.
**Mitigation:** In scope for this release, unconditionally (decision D1). The fix must read *both*
tails and prefer the `drafter-fallback` sentinel — a Room-first-then-legacy-if-empty read leaves
the guard fail-open whenever any other sender's message sits at the Room tail. Pinned by two
tests: sentinel on the Room leg only, and sentinel on the legacy leg while an unrelated message
occupies the Room tail.

### Risk 4: A session lookup lands on a latency-sensitive write path

**Impact:** `AgentSession.query.filter(session_id=...)` is an unindexed full scan measuring
~2.4s per call (spike-5). Any design that puts it inside `push_steering_message` regresses
`bridge/telegram_bridge.py:979` (inbound Telegram fast path) and `_requeue_pending_steers` (once
per leftover message) by seconds.
**Mitigation:** Eliminated by design, not mitigated: the writer performs no lookup. `room_id` is
always supplied by a caller that already holds the session, derived via the zero-I/O
`room_id_for_session`. The `[ ]` guard against regression is the Failure Path checklist item
"introduces no new exception handler" — an internal lookup cannot be added without one.

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
**Mitigation:** Accepted behavior (see Risk 2), not prevented. The drain is LPOP-based so the
message is delivered exactly once, never duplicated — the hazard is *which* session receives it,
not loss or double-delivery.

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
**Mitigation:** Structural. Every caller derives from the session object it is *already acting on*
— the row it loaded to decide to steer in the first place — not from a fresh
`filter(session_id=...)` whose unsorted `[0]` could be a superseded row. This is the second reason
the writer must not do its own lookup: the previous draft's "take the first row" would have hit
exactly this. Where a caller does re-fetch (`bridge/telegram_bridge.py:2593-2594`), it already
uses the repo's newest-by-`created_at` shape; the builder must not introduce a new unsorted
`[0]` anywhere. Pinned by a test persisting a superseded row and a live row sharing one
`session_id` and asserting the steer lands on the live session's Room.

## No-Gos (Out of Scope)

- [ORDERED] **Merging this PR.** Blocked on a human-gated event: operator confirmation that every
  machine in the fleet has run `/update` past PR #2622. Drive to review-clean, then hold.
  **Enforcement is draft state plus the `hold` label, both applied at PR-open time** (decision D3),
  not this bullet. Removal owner: Valor Engels (repo operator). The PR body must carry the removal
  condition verbatim.
- **Indexing `AgentSession.session_id`.** The repo-wide unindexed-scan problem (299 call sites,
  ~2.4s each) is real and out of scope here. This plan neither adds a scan nor fixes the existing
  ones. File separately if it becomes a priority.
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

No update system changes required. This change adds no dependency, no config file, no env key, and
no schema. It is a two-branch key selection inside `agent/steering.py` plus `room_id` arguments at
eleven call sites, propagated by the ordinary `/update` git sync. **The `/update` run itself is the
deploy gate's subject, not its target** — the fleet must already be past #2622 *before* this merges,
which is an ordering constraint on merge, not a change to the update process.

No Popoto schema migration is required: no model gains, loses, or changes a field, and no field's
`indexed` flag changes. `Room` and `AgentSession` are read-only inputs here. (This is a direct
consequence of the decision *not* to index `session_id` — see Rabbit Holes. Had the plan taken the
indexing route, this section would instead need a `rebuild_indexes()` migration registered in
`MIGRATIONS`.)

## Agent Integration

No agent integration required — this is an internal change to the steering transport. No new CLI
entry point in `pyproject.toml [project.scripts]`, no MCP surface. The bridge already imports
`push_steering_message` (`bridge/telegram_bridge.py:87`); it additionally imports
`models.room.room_id_for_session` to derive the Room at its three call sites, which is an internal
import, not a new agent surface.

The one operator-visible surface that must keep working is `valor-session status`, whose pending-
steering peek (`tools/valor_session.py:1031`) already passes a `room_id`. That call is untouched
and its continued correctness is asserted in Verification. Note this is a *different* function in
the same file from the resume steer at line 856, which does change — the anti-criterion is scoped
to the peek call, not to the whole file.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-steering.md` — replace the dual-read/writers-unchanged
  description with the new status quo: writers target the Room key, legacy is the fallback for
  Room-less sessions and remains fully drained. Add a subsection documenting the same-Room delivery
  semantics from Risk 2 (a steer may be served to a different session in the same Room; the
  `system` addressee groups all chatless sessions of a project).
- [ ] Update `docs/plans/durability-room-job-agentrun.md` line ~655 — move "the steering writer
  flip" out of the "Remaining, each its own release" list and record it as shipped, leaving phase 2
  and phase 3 as the remainder. Plan-doc edits commit on main.
- [ ] No `docs/features/README.md` index change — `session-steering.md` is already indexed.

### Inline Documentation

- [ ] Rewrite `agent/steering.py` module docstring (lines 15-22) to the new status quo.
- [ ] Rewrite `_room_queue_key` docstring (lines 48-55) — drop "No writer targets this key yet".
- [ ] Rewrite the `agent/health_check.py:511-513` comment — drop "Writers are unchanged — the
  re-pushes below always target the legacy list".
- [ ] Rewrite the `agent/session_runner/runner.py:589-591` `_default_steering_pop` docstring —
  drop "Writers are unchanged in this release."
- [ ] Docstring the `room_id` parameter on `push_steering_message`, `_repush_messages`,
  `peek_steering_sender`, `_ack_steering_routed`, and `_inject_watchdog_steer`, including the
  "no `room_id` → legacy key" contract and the note that the caller derives it via
  `room_id_for_session` because the writer deliberately does not look sessions up.

## Success Criteria

- [ ] **The durability property holds end to end:** a steer written for session A is delivered to
  a different session B serving the same Room, after A is gone. Asserted by
  `test_steer_survives_target_session_and_reaches_room_sibling`, with a `room_id=None` negative
  twin proving the test measures the Room leg.
- [ ] `push_steering_message` targets `steering:room:{room_id}` whenever the caller supplies a
  truthy `room_id`, and `steering:{session_id}` otherwise. **Every non-test caller that holds an
  `AgentSession` supplies one** — `scripts/migrate_steering_queue_drain.py:126` is the sole
  documented exception, and it is ORM-free by design.
- [ ] `push_steering_message` performs no ORM query and adds no exception handler. A steering
  write costs the same Redis round trips it costs today.
- [ ] No caller introduces an unsorted `[0]` row selection from
  `AgentSession.query.filter(session_id=...)`.
- [ ] `_repush_messages` receives and forwards `_handle_steering`'s resolved `room_id` at **all
  four** call sites (530, 536, 560, 564) — including the two retries inside `except` blocks.
- [ ] No provenance tag appears in the JSON payload persisted to Redis (the payload dict is
  byte-identical to today's).
- [ ] `peek_steering_sender` returns `drafter-fallback` when that sender is at the tail of
  *either* leg, so the guard at `agent/output_handler.py:1162` cannot fail open — including when
  an unrelated message occupies the Room tail.
- [ ] The five dual-read drain consumers are unmodified, and the `valor-session status` peek call
  at `tools/valor_session.py:1031` is unmodified (verifiable from the diff).
- [ ] The steering suite is green via `scripts/pytest-clean.sh tests/integration/test_steering.py`.
- [ ] All four stale "writers are unchanged" claims removed (`agent/steering.py` module docstring
  and `_room_queue_key`, `agent/health_check.py:511-513`,
  `agent/session_runner/runner.py:589-591`); no "formerly"/"used to" narration.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `python -m ruff check` and `python -m ruff format` clean.
- [ ] PR is review-clean and **held unmerged** pending the fleet deploy gate — opened as a draft
  (the enforcing gate; GitHub refuses to merge a draft) and labelled `hold` (the visible signal),
  with the removal condition and its named owner in the body.

## Team Orchestration

### Team Members

- **Builder (steering-writer)**
  - Name: `steering-writer-builder`
  - Role: Flip the writer, thread `room_id` through the re-push, fix the missed peek consumer,
    truth up the docstrings.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (steering-tests)**
  - Name: `steering-test-builder`
  - Role: Update the one inverted test, add the fallback matrix, Room-write happy path, sibling
    provenance, and peek-Room-leg coverage.
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

### 1. Make the writer choose a key from an argument

- **Task ID**: build-writer
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-5 (an internal lookup costs ~2.4s), spike-3 (no `room_id` → legacy is a
  hard requirement or the suite goes red)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/steering.py::push_steering_message`.
- Replace `key = _queue_key(session_id)` with `_room_queue_key(room_id)` when `room_id` is truthy,
  `_queue_key(session_id)` otherwise. Leave the payload dict, the RPUSH/LPUSH split, and the log
  line untouched.
- Add **no** import and **no** exception handler to this module. If either feels necessary, the
  design has drifted back to internal resolution — stop and re-read spike-5.

### 2. Thread room_id from every caller that holds a session

- **Task ID**: build-callers
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-1b (the caller table), Race 4 (derive from the object already in hand,
  never from a fresh unsorted `filter(session_id=...)`)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Work the Solution's caller table row by row. Each site passes
  `room_id=room_id_for_session(<the session object already in scope>)`.
- Two thin helpers gain a pass-through `room_id: str | None = None` parameter, and their callers
  supply it: `bridge/telegram_bridge.py::_ack_steering_routed` (callers 1809, 1835, 1868, 2070,
  2178) and `monitoring/session_watchdog.py::_inject_watchdog_steer` (callers 1003, 1023, 1059).
- `agent/session_runner/runner.py::_default_steering_push` **does** change — it gains
  `room_id=room_id_for_session(self._agent_session)`, mirroring `_default_steering_pop` at
  599-601, which already makes exactly that call. The read/write asymmetry there is the bug.
- `scripts/migrate_steering_queue_drain.py:126` is the one site that does **not** change.
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

### 4. Give peek_steering_sender a sentinel-preferring dual read

- **Task ID**: build-peek
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`, `tests/unit/test_output_handler.py`
- **Informed By**: spike-4 and its semantics correction (a tail read on one leg still fails open)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/steering.py::peek_steering_sender`. Read the legacy
  tail and, when `room_id` is given, the Room tail. Decode both.
- The binding requirement: a `drafter-fallback` message at the tail of **either** leg must be
  observable by the guard at `agent/output_handler.py:1160-1162`, even when an unrelated sender's
  message occupies the other tail. Whether that is expressed as a `prefer_sender` argument or by
  returning both tails is the builder's call — but `DRAFTER_FALLBACK_SENDER` must not be imported
  into `agent/steering.py` (that inverts the dependency direction).
- With no sentinel match, return the legacy tail's sender first, matching the drain order at
  `agent/steering.py:158-160`.
- Derive and pass the Room at `agent/output_handler.py:1160-1162` from the `session` object
  already in scope.
- Re-check the 11 `peek_steering_sender` patch sites in `tests/unit/test_output_handler.py`; they
  patch the symbol wholesale, but the call site now passes a keyword.

### 5. Truth up the docstrings

- **Task ID**: build-docstrings
- **Depends On**: build-writer, build-callers, build-repush, build-peek
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Rewrite all four stale "writers are unchanged" claims: `agent/steering.py` module docstring
  (15-22), `_room_queue_key` (48-55), the `agent/health_check.py:511-513` comment, and the
  `agent/session_runner/runner.py:589-591` `_default_steering_pop` docstring. No
  "formerly"/"used to" narration.
- Document the `room_id` parameter on all five changed signatures, including the "no `room_id` →
  legacy key" contract and why the writer deliberately does not look sessions up.

### 6. Update and extend the steering tests

- **Task ID**: build-tests
- **Depends On**: build-writer, build-callers, build-repush, build-peek
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-3 (`test_handle_steering_drains_room_leg` assertion inverts)
- **Assigned To**: `steering-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- **ADD the durability property test first — it is the reason the release exists.**
  `test_steer_survives_target_session_and_reaches_room_sibling`: persist two `AgentSession` rows
  with `project_key="test-room-durability"` and `chat_id=None` (both map to `SYSTEM_ADDRESSEE`,
  `models/room.py:65-92`, so both derive the same composite). Push for A with A's Room, remove A,
  assert B's drain returns the message. Tear down via the ORM scoped by the `test-` project key.
- ADD its negative twin: same scenario with `room_id=None` must NOT deliver to B.
- ADD: superseded-row safety — persist a superseded row and a live row sharing one `session_id`;
  assert the steer lands on the live session's Room.
- UPDATE `test_handle_steering_drains_room_leg` (~line 1746): the re-push now targets the Room
  key. Fix the docstring too.
- ADD the legacy-fallback matrix — `room_id=None`, `room_id=""`, a session with no `project_key`
  — each lands on `_queue_key`. Plus `session_id=""` with a truthy `room_id` → the Room key wins.
- ADD: abort siblings re-pushed to the Room key, exercising the retry paths (536, 564) by forcing
  the primary call to raise.
- ADD: the payload persisted to Redis contains exactly `{text, sender, timestamp, is_abort}` (plus
  `target_agent` when set) — no provenance field.
- ADD: `peek_steering_sender` finds a `drafter-fallback` sentinel on the Room leg, and separately
  on the legacy leg while an unrelated message sits at the Room tail.
- Run via `scripts/pytest-clean.sh tests/integration/test_steering.py`, never bare pytest.

### 7. Validate

- **Task ID**: validate-all
- **Depends On**: build-docstrings, build-tests
- **Assigned To**: `steering-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm the diff adds no `AgentSession.query.filter(` call and no `try:` to
  `agent/steering.py::push_steering_message`.
- Confirm the five drain consumers are unmodified and `tools/valor_session.py:1031` (the status
  peek call) is unmodified.
- Confirm the persisted payload shape is unchanged.
- Run every row of the Verification table.
- Confirm the PR is a draft, carries the `hold` label, and states the removal condition in its body.

### 8. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `steering-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-steering.md` including the Risk 2 same-Room delivery semantics
  (decision D2: accepted behavior, documented as behavior).
- Update the remaining-releases list at `docs/plans/durability-room-job-agentrun.md:655`.

## Verification

Every threshold row below records the **measured baseline on commit `c9dab0767`** inline, so a row
cannot be satisfied by an untouched checkout. The four vacuous rows from the previous draft
(`room_id_for_session` in `agent/steering.py`, `_room_queue_key` in `agent/steering.py`, `room_id`
in `agent/health_check.py`, `room_id` in `agent/steering.py`) are deleted, not re-tuned — their
targets are better expressed as the behavioral rows here.

| Check | Command | Expected |
|-------|---------|----------|
| **Durability property delivered** | `scripts/pytest-clean.sh "tests/integration/test_steering.py::test_steer_survives_target_session_and_reaches_room_sibling" -q` | exit code 0 (test must exist; a missing node id fails the row) |
| **Negative twin passes** | `scripts/pytest-clean.sh "tests/integration/test_steering.py" -q -k "room_sibling or superseded"` | exit code 0, ≥ 3 tests collected |
| Steering suite green | `scripts/pytest-clean.sh tests/integration/test_steering.py -q` | exit code 0 |
| Output-handler unit tests green | `scripts/pytest-clean.sh tests/unit/test_output_handler.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Writer takes a `room_id` and does not look one up | `grep -c "room_id" agent/steering.py` | ≥ 24 (baseline 22; +1 signature, +≥1 key selection) |
| **Writer performs no ORM query** | `grep -c "query.filter" agent/steering.py` | == 0 (baseline 0 — must stay 0) |
| **Writer imports no model layer** | `grep -c "from models" agent/steering.py` | == 0 (baseline 0 — must stay 0). A bare `grep -c "AgentSession"` is NOT usable here: the baseline is 3, from self-draft-counter docstrings at lines 252, 272, 301 that say the counter is *not* an AgentSession field. |
| **Writer adds no exception handler** | `awk '/^def push_steering_message/,/^def /' agent/steering.py \| grep -c "except"` | == 0 |
| Re-push forwards the room at all four sites | `grep -c "room_id=room_id" agent/health_check.py` | == 5 (baseline 1: the existing `pop_all_steering_messages` call; +4 `_repush_messages` calls) |
| Bridge derives the Room | `grep -c "room_id_for_session" bridge/telegram_bridge.py` | ≥ 1 (baseline 0) |
| Watchdog derives the Room | `grep -c "room_id_for_session" monitoring/session_watchdog.py` | ≥ 1 (baseline 0) |
| Session-health derives the Room | `grep -c "room_id_for_session" agent/session_health.py` | ≥ 1 (baseline 0) |
| `steer_child` derives the Room | `grep -c "room_id_for_session" scripts/steer_child.py` | ≥ 1 (baseline 0) |
| Runner push derives the Room | `grep -c "room_id_for_session" agent/session_runner/runner.py` | ≥ 3 (baseline 2) |
| Migration script left alone (anti-criterion) | `git diff origin/main --quiet -- scripts/migrate_steering_queue_drain.py` | exit code 0 |
| Stale module docstring gone | `grep -c "Writers still push to the legacy key only" agent/steering.py` | == 0 |
| Stale key docstring gone | `grep -c "No writer targets this key yet" agent/steering.py` | == 0 |
| Stale health_check comment gone | `grep -c "the re-pushes below always target the legacy list" agent/health_check.py` | == 0 |
| Stale runner docstring gone | `grep -c "Writers are unchanged in this release" agent/session_runner/runner.py` | == 0 (baseline 1) |
| No provenance field in payload | `grep -c "_source" agent/steering.py` | == 0 |
| No new unsorted row selection (anti-criterion) | `git diff origin/main \| grep -c "^+.*filter(session_id="` | == 0 |
| Status peek call untouched (anti-criterion) | `git diff origin/main -- tools/valor_session.py \| grep -c "^[-+].*peek_steering"` | == 0 |
| Drain helper not removed (anti-criterion) | `git diff origin/main -- agent/steering.py \| grep -c "^-.*_drain_list"` | == 0 |
| Legacy write leg still reachable | `grep -c "_queue_key(session_id)" agent/steering.py` | ≥ 7 (baseline 7 — the legacy leg is a fallback, never retired) |
| Merge gate is mechanical (draft) | `gh pr view <N> --json isDraft -q .isDraft` | `true` — GitHub refuses to merge a draft, so this is the enforcing gate, not an advisory one |
| Merge gate is visible (label) | `gh pr view <N> --json labels -q '.labels[].name'` | includes `hold` — the label that actually exists in this repo (`gh label list` has `hold`; there is no `do-not-merge` label, so the critique's suggested name would have silently no-opped) |

## Critique Results


**War room round 2, 2026-08-07, against baseline `358da4fb5`.** Depth: FULL (cross-component change on the steering critical path). Roster: Risk & Robustness, Scope & Value, History & Consistency — gate 3/3 complete, 3/3 grounded. The round-1 findings were addressed by the 2026-08-07 revision pass; this table records the round-2 findings against the revised plan. **Verdict: NEEDS REVISION (3 blockers).**

*Methodology note:* the Agent spawn tool was unavailable in the executing context, so the three critic lenses were run in-process by the critique driver rather than by three spawned subagents. Every file:line citation below was read from the working tree at `358da4fb5` and every measurement is reproducible from the command quoted inline; the roster result files and the `critique-roster-check --plan-path` grounding gate were produced and passed normally.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness + History & Consistency | Race 4's structural mitigation rests on a false claim about a named line. It asserts "Where a caller does re-fetch (`bridge/telegram_bridge.py:2593-2594`), it already uses the repo's newest-by-`created_at` shape" — the actual code is `sessions = list(AgentSession.query.filter(session_id=session_id))` then `session = sessions[0] if sessions else None`: an unsorted `[0]`, no sort key. The `:2650` push derives from `active_edit`, itself an unsorted `next((s for s in edit_sessions if s.status in (...)), None)` at 2641-2646. Both objects are named in the caller table as Room-derivation sources, so the previous round's unsorted-`[0]` BLOCKER was not dissolved — it was relocated to two callers that already carry the defect and then argued away with a wrong fact. A superseded row selected there yields a different `chat_id` (a Room the live session never drains) or no `project_key` (silent legacy fallback): total silent steering loss on the Telegram edit path. | pending | Sort before selecting at 2593-2594: `sessions.sort(key=lambda s: s.created_at or 0, reverse=True)`. The `or 0` is load-bearing — `created_at` is nullable and a bare key raises `TypeError` comparing `None` to `float`, which the enclosing `except Exception` at 2595 swallows into `session = None` (the edit is then silently ignored). Apply the same sort to `edit_sessions` before the `next(...)` at 2642. The existing anti-criterion `git diff origin/main \| grep -c "^+.*filter(session_id=" == 0` cannot catch this because both selections are pre-existing and unchanged in the diff; replace it with a positive row `grep -c "created_at or 0" bridge/telegram_bridge.py` expecting `>= 2` (measured baseline 0). Restate the success criterion as "No caller *derives a Room from* an unsorted row selection", not "introduces". Correct the Race 4 prose itself. |
| BLOCKER | Risk & Robustness | Abort steers become Room-scoped, defeating `steer_child`'s target validation and aborting the wrong session. `scripts/steer_child.py:119` is the only `push_steering_message` call in that file and is unconditionally `is_abort=True` (line 123); the caller table routes it through `room_id_for_session(child)`. An Eng child with no numeric `chat_id` maps to `SYSTEM_ADDRESSEE` (`models/room.py:65-92`), so the abort lands on `{project}\|system`, shared by every chatless session in the project. `agent/health_check.py::_handle_steering` runs on EVERY tool call and returns "You MUST stop immediately... No more tool calls." to whichever session drains it first, so the four validation gates at `scripts/steer_child.py:93-112` (`is_eng`, parent-child linkage, `status == "running"`) are bypassed entirely. Risk 2 frames the accepted hazard as "*which* session receives it, not loss or double-delivery" — true for an instruction, false for an abort: the signal is destructive, non-idempotent, and the intended target keeps running. No test and no success criterion covers abort routing. | pending | Guard inside the writer, on the same expression the plan already adds: `key = _room_queue_key(room_id) if (room_id and not is_abort) else _queue_key(session_id)`. Place it AFTER the abort-keyword auto-detect block at `agent/steering.py:113-115`, or the `text.strip().lower() in ABORT_KEYWORDS` path (how `bridge/telegram_bridge.py:979` produces most aborts) escapes the guard. Two tests: `is_abort=True` with a truthy `room_id` lands on `_queue_key(session_id)`; a bare `"stop"` text with a truthy `room_id` also lands on legacy. Add a matching success criterion — the durability criterion as written ("every steering write targets the Room key when a Room resolves") currently mandates the opposite. |
| BLOCKER | History & Consistency | The merge-hold gate is specified three incompatible ways, and the authoritative statement names a label that does not exist in this repo. Decision D3 says the builder applies the `do-not-merge` label and "The label is the gate"; the No-Go bullet repeats it; Task 7 says to confirm it. The Verification table contradicts all three, requiring `isDraft == true` as "the enforcing gate" and noting the label is `hold` because "there is no `do-not-merge` label". Measured: `gh label list --limit 100 --json name` returns `hold`; `do-not-merge` is absent. A builder follows the imperative sections and errors. Worse, the *draft* requirement — the only mechanism that actually blocks, since `tools/merge_predicate.py:466-468` refuses any PR whose `mergeStateStatus` is not `CLEAN` or `UNSTABLE` and GitHub reports `DRAFT` — appears nowhere outside the Verification table, so nothing instructs the builder to open a draft. Risk 1's mitigation depends on this gate holding. | pending | Collapse to one specification carried in D3 and restated in the No-Go bullet and Task 7: `gh pr create --draft ...` at PR-open time, `gh pr edit <N> --add-label hold`, removal condition and named owner in the body. State the mechanism explicitly: `tools/merge_predicate.py::_check_pr_state` appends `PR mergeStateStatus is 'DRAFT' (must be CLEAN)` and fails the gate, so `/do-merge` refuses fail-closed; the `hold` label is human-visible only — no code reads PR labels (`grep -rn 'label' tools/merge_predicate.py` returns nothing). Delete every occurrence of the string `do-not-merge` from the plan. |
| CONCERN | Risk & Robustness | The `front=True` priority contract silently inverts on the Room leg. `agent/session_health.py:3473` is a `front=True` push — the tool-timeout recovery advisory whose documented contract at `agent/steering.py:96-100` is "prepend to the queue (LPUSH) so this message is the next one drained, ahead of anything already queued". The caller table routes `agent/session_health.py:3467` through `room_id_for_session(entry)`, so post-flip that LPUSH prepends to the *Room* list — but every consumer drains legacy FIRST (`agent/steering.py:158-160`), so any legacy residue is consumed ahead of the urgent advisory. The plan never mentions `front` and no listed test covers ordering across legs. Cutover is precisely when legacy residue exists. | pending | Either route `front=True` pushes to the legacy leg — `key = _queue_key(session_id) if front else (_room_queue_key(room_id) if room_id else _queue_key(session_id))` — or document that `front` is a within-leg priority and legacy always precedes Room, updating the `front` docstring. This composes with the abort guard above, so write the key selection as one explicit three-branch expression rather than two independent patches. Pin either choice: `push_steering_message(sid, "old", "x")` with no `room_id`, then `push_steering_message(sid, "urgent", "health", front=True, room_id=rid)`, then assert on `pop_all_steering_messages(sid, room_id=rid)[0]["text"]`. |
| CONCERN | History & Consistency | A strict-equality Verification row will fail a correct implementation. The row `grep -c "room_id=room_id" agent/health_check.py` expects `== 5`, composed as baseline 1 plus four `_repush_messages` calls. The baseline measures correctly (one hit, `agent/health_check.py:514`), but Task 3 also requires `_repush_messages` to forward `room_id` to the `push_steering_message` call inside it — idiomatically a sixth `room_id=room_id`. A fully correct build yields 6 and the `== 5` gate fails. The equality was deliberately chosen over `>=` to pin the two `except`-block retries at 536 and 564, so the fix is to split the row, not loosen it. | pending | Two rows: `grep -c "_repush_messages(.*room_id=room_id" agent/health_check.py` expecting `== 4` (measured baseline 0 — this is the row that actually pins 530/536/560/564 including the two retries inside `except` blocks), and `grep -c "room_id=room_id" agent/health_check.py` expecting `>= 6` (baseline 1 at line 514; +4 call sites; +1 forward inside `_repush_messages`). The four-site row requires the calls be written on one line or the grep misses them — state that, or count `room_id` keywords on `_repush_messages` calls with an `ast` one-liner that is robust to line wrapping. |
| CONCERN | Scope & Value | The durability invariant is diffused across eleven call sites with no enforcement, and its failure mode is silent. The design moves the guarantee out of one function into eleven independent callers, then states the invariant only in prose ("Every non-test caller that holds an `AgentSession` supplies one"). A caller that omits `room_id` writes to the legacy key by construction — no error, no warning, indistinguishable from a legitimately Room-less session. The twelfth caller added later silently re-strands its steers, which is exactly the hole this plan exists to close. The repo already has the enforcement pattern in the file the plan marks "no change": `tests/unit/test_bridge_dispatch_contract.py` AST-walks call sites for a contract (`_direct_calls` line 66, `_banned_calls_in` line 87, `ast.parse` line 105). | pending | Add to Task 6 a census test that `ast.parse`s each module in the caller table and asserts every `ast.Call` whose func resolves to the name `push_steering_message` carries a `room_id` keyword: `any(kw.arg == "room_id" for kw in node.keywords)`. Handle both `ast.Name` and `ast.Attribute` funcs and the `_push_steering_message` alias used at `agent/session_executor.py:853` and `agent/session_health.py:3467`. Allowlist exactly one path, `scripts/migrate_steering_queue_drain.py`, with the reason inline. Anchor it in the Verification table by pytest node id so a deleted test fails the gate. |
| CONCERN | Scope & Value | The plan reverses two explicit constraints from issue #2642 without amending the issue, so the PR will be reviewed against superseded acceptance criteria. The issue states the resolution "has to happen inside the function... the no-argument path must still resolve correctly"; the plan does the exact opposite (correctly, on spike-5 evidence). Separately the issue's criterion "The provenance tag does not appear in the JSON payload persisted to Redis" presumes a provenance tag spike-2 eliminates, and the issue still says there are "three" writer sites where the plan establishes twelve. `/do-pr-review` reviews a PR against its issue as well as its plan, so a reviewer working from #2642 reads the finished PR as failing its own acceptance criteria. | pending | Add a pre-PR step (or extend Task 7): `gh issue comment 2642 --body "..."` recording (a) no internal resolution — spike-5 measured `AgentSession.query.filter(session_id=...)` at 2.55s / 2.06s / 2.36s because `models/agent_session.py:155` declares `session_id = Field()` and Popoto defaults to `indexed=False`; (b) no provenance tag — spike-2 shows forwarding the already-resolved `room_id` into `_repush_messages` satisfies the sibling criterion with a byte-identical payload. Restate issue criterion 1 as "every non-test caller that holds an `AgentSession`" and criterion 4 as "the JSON payload persisted to Redis is byte-identical to today's". |
| NIT | Scope & Value | The appetite no longer matches the shape. The issue scoped three writer sites; after spike-1b the plan is eight tasks touching ten production files, five signature changes, an in-scope consumer regression fix taken on by decision D1, and a human-gated merge hold. `Small` is also what kept this plan off the deterministic force-FULL triage path. | pending | — |

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8, no gaps |
| Dependencies valid | PASS | `build-writer` -> `build-callers`/`build-repush`/`build-peek` -> `build-docstrings`/`build-tests` -> `validate-all` -> `document-feature`; no cycles |
| File paths exist | PASS | 18 of 18 referenced paths exist; call-site counts re-verified (12 non-test `push_steering_message` callers, 4 `_repush_messages` sites at 530/536/560/564) |
| Prerequisites met | PASS | Redis reachable; venv on pin |
| Verification baselines | PASS (1 defect) | All 22 measured baselines reproduce on `358da4fb5`; the `room_id=room_id` row's `== 5` expectation is arithmetically wrong (see CONCERN above) |
| Cross-references | FAIL | Merge-gate mechanism specified three incompatible ways across D3, the No-Gos, Task 7 and Verification; Race 4 asserts a `created_at` sort at a line that has none |
