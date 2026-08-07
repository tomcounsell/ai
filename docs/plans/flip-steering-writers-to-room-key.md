---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2642
last_comment_id: none
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

All four assumptions were resolvable by code-read at plan time; no agent spikes were dispatched.

### spike-1: Is `push_steering_message` the single writer funnel?

- **Assumption**: "There are three writer sites that each need an independent flip."
- **Method**: code-read (`grep -rn push_steering_message` across the repo)
- **Finding**: **The assumption is wrong in a helpful direction.** `push_steering_message` is the
  only function that RPUSHes/LPUSHes a steering payload. Thirteen production call sites route
  through it: `bridge/telegram_bridge.py:979,2617,2650`, `tools/valor_session.py:856`,
  `agent/session_executor.py:853,1930`, `agent/session_health.py:3467`,
  `agent/output_handler.py:1227`, `agent/health_check.py:480`,
  `agent/session_runner/runner.py:610`, `monitoring/session_watchdog.py:557`,
  `scripts/steer_child.py:119`, `scripts/migrate_steering_queue_drain.py:126`. Resolving the Room
  *inside* `push_steering_message` flips all thirteen at once.
- **Confidence**: high
- **Impact on plan**: Issue site 2 (`runner._default_steering_push`) needs **no change at all** —
  it is a caller, and internal resolution covers it. Site 3 needs only a `room_id` pass-through.
  The build collapses to one substantive edit plus one pass-through plus one regression fix.

### spike-2: Is the per-message provenance tag necessary?

- **Assumption**: "`pop_all_steering_messages` must tag each entry with its source leg so
  `_repush_messages` can route Room-sourced siblings back to the Room key."
- **Method**: code-read (`agent/health_check.py:490-566`, `agent/steering.py:138-161`)
- **Finding**: Unnecessary. Once the writer resolves the Room internally, *every* message with a
  resolvable Room lands on the Room key regardless of which leg it came from. A legacy-sourced
  sibling is **upgraded**, which is safe because all consumers dual-read both legs.
  `_handle_steering` already holds the resolved `room_id` (it passes it to the drain at line 514);
  forwarding it as `_repush_messages(session_id, messages, room_id=room_id)` satisfies the issue's
  acceptance criterion with zero payload change, zero consumer change, and no per-message tag.
- **Confidence**: high
- **Impact on plan**: Drops the tagging design entirely. Also avoids N per-message `AgentSession`
  loads inside a PostToolUse hot path, which the naive "resolve inside the writer, call it N times"
  shape would have introduced.

### spike-3: Do the ~66 existing steering tests survive internal Room resolution?

- **Assumption**: "Flipping the writer will break the existing suite, which pushes to synthetic
  session ids like `test_dualread_order`."
- **Method**: code-read (`tests/integration/test_steering.py`, `models/room.py:95-100`)
- **Finding**: They survive **if and only if** an unresolvable session falls back to the legacy
  key. Every synthetic id in the suite has no persisted `AgentSession`, so the lookup returns
  nothing, `room_id_for_session` is never reached (or is handed `None`), and the write must land on
  legacy. One test is a genuine exception:
  `tests/integration/test_steering.py:1746 test_handle_steering_drains_room_leg` asserts in its
  docstring and body that a Room-drained message is re-pushed **to the legacy list** — that
  assertion inverts under this change and must be updated.
- **Confidence**: high
- **Impact on plan**: Makes "unresolvable session → legacy" a hard correctness requirement, not a
  nicety. Pins the one test needing UPDATE.

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

## Data Flow

1. **Entry point** — any of thirteen call sites invokes
   `push_steering_message(session_id, text, sender, ...)`. Most pass only a session id.
2. **Room resolution (new)** — inside `push_steering_message`: if the caller supplied `room_id`,
   use it verbatim; otherwise load the `AgentSession` by `session_id` and call
   `room_id_for_session(session)`. Any failure (session absent, no `project_key`, Redis error)
   yields `None`.
3. **Key selection (changed)** — `_room_queue_key(room_id)` when a Room resolved,
   `_queue_key(session_id)` otherwise. Payload JSON is byte-identical to today.
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

- **New dependencies**: none. `models.room` and `models.agent_session` are already imported
  elsewhere in `agent/`; the new imports inside `agent/steering.py` are function-local
  (`# noqa: PLC0415`) to match the existing pattern in `runner.py:594` and avoid an import cycle
  between `agent.steering` and the model layer.
- **Interface changes**: `push_steering_message` gains an optional `room_id: str | None = None`
  keyword. `_repush_messages` gains an optional `room_id`. `peek_steering_sender` gains an optional
  `room_id`. All three are backward-compatible additive keywords; no caller is forced to change.
- **Coupling**: increases slightly — `agent.steering` now depends on the model layer to resolve a
  Room. Mitigated by the optional `room_id` escape hatch, which lets any caller that already holds
  a Room skip the lookup entirely.
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

### Key Elements

- **Internal Room resolution in the writer** — `push_steering_message` learns to derive its own
  target Room from the session id, so every existing caller flips without a signature change at the
  call site.
- **Explicit `room_id` fast path** — an optional keyword lets callers that already hold a resolved
  Room (notably `_handle_steering`) skip a redundant `AgentSession` load.
- **Fail-open-to-legacy fallback** — any resolution failure writes to the legacy key. No message is
  ever dropped for lack of a Room, and the existing suite's synthetic session ids keep working.
- **Room leg for `peek_steering_sender`** — the one consumer #2622 missed, whose absence would
  silently break the drafter self-draft guard the moment writers flip.
- **Docstring truth-up** — the module docstring and `_room_queue_key`'s docstring both currently
  assert that no writer targets the Room key. After this change they are lies.

### Flow

Supervisor sends a steer → `push_steering_message(session_id, ...)` → **Room resolves** → message
lands on `steering:room:{project|addressee}` → target session dies before its next turn boundary →
a new session opens in the same Room → its turn-boundary drain reads the Room leg → **the steer is
delivered**.

Fallback branch: Supervisor sends a steer → `push_steering_message` → **no Room resolves** (session
absent or has no `project_key`) → message lands on `steering:{session_id}` → drained by the same
consumers exactly as today.

### Technical Approach

- **`agent/steering.py::push_steering_message`** — add `room_id: str | None = None`. Before key
  selection, when `room_id is None`, resolve it: load the `AgentSession` via
  `AgentSession.query.filter(session_id=session_id)` (the established lookup shape, see
  `agent/session_executor.py:290`), take the first row, and call
  `models.room.room_id_for_session`. Wrap the whole resolution in a broad `except Exception` that
  logs at debug and returns `None` — a steering write must never fail because a Room could not be
  derived. Select `_room_queue_key(resolved)` when truthy, else `_queue_key(session_id)`. The
  existing log line already interpolates `key`, so it self-describes which leg was written.
- **`agent/health_check.py::_repush_messages`** — add `room_id: str | None = None` and forward it
  to every `push_steering_message` call. `_handle_steering` passes its own already-resolved
  `room_id` at all three call sites (lines 530, 536, 560/564). This is the provenance mechanism:
  explicit and free, rather than a per-message tag.
- **`agent/steering.py::peek_steering_sender`** — add `room_id: str | None = None`. Read
  `LINDEX -1` on the Room key first when a `room_id` is given (the Room key holds the *newer*
  writes post-flip), fall back to the legacy key. Update `agent/output_handler.py:1160-1162` to
  resolve and pass the Room. This is a *dual-read completion*, not a consumer retirement — call it
  out explicitly in the PR body so review does not read it as scope creep.
- **`agent/session_runner/runner.py::_default_steering_push`** — **no change.** It calls
  `push_steering_message` with a session id; internal resolution covers it. Verified by spike-1.
  Resist the urge to "also flip" it — a redundant explicit resolution there would duplicate logic
  and drift.
- **Docstrings** — rewrite `agent/steering.py:15-22` (module) and `:48-55` (`_room_queue_key`) to
  describe the new status quo: writers target the Room key, legacy is the fallback and remains
  fully drained, phase 3 (legacy read-leg retirement) is still pending. No "formerly" narration —
  describe only what is true now.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] The new Room-resolution block in `push_steering_message` uses a broad `except Exception` by
  design (a steer must never be lost to a lookup failure). It must log at DEBUG with the session id
  and exception, and the test asserts the observable behavior: **the message still lands on the
  legacy key**. Not a silent `pass`.
- [ ] `agent/health_check.py:531-536` already has an `except Exception` around `_repush_messages`
  with a retry; the added `room_id` argument must be present on the retry call too, or the retry
  silently demotes to legacy. Pin with a test.

### Empty/Invalid Input Handling

- [ ] `session_id=""` → resolution short-circuits to `None` → legacy key. Test it.
- [ ] Session exists but `project_key` is `None`/empty → `room_id_for_session` returns `None` →
  legacy key. Test it (this is the issue's explicit acceptance criterion).
- [ ] Session id refers to no persisted `AgentSession` → legacy key. This is the path every
  existing test in the suite takes; it is covered by the suite staying green.
- [ ] `room_id=""` passed explicitly by a caller → falsy → treat as unresolved, legacy key.

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
- [ ] `tests/integration/test_steering.py` remaining ~64 tests — no change expected: all use
  synthetic session ids with no persisted `AgentSession`, which fall back to legacy. If any turn
  red, the fallback path is wrong — fix the code, not the test.
- [ ] `tests/unit/test_output_handler.py` (11 `peek_steering_sender` patch sites) — no change
  expected: they patch the symbol wholesale. Verify the added keyword does not break the patched
  call shape.
- [ ] `tests/unit/test_bridge_dispatch_contract.py` — no change: it AST-walks for
  `push_steering_message` call/dedup pairing, which the added keyword does not affect.
- [ ] New test file additions go into `tests/integration/test_steering.py` (fallback matrix,
  Room-write happy path, sibling provenance, `peek_steering_sender` Room leg).

## Rabbit Holes

- **Building a provenance-tagging mechanism.** spike-2 proved it unnecessary. Threading the
  already-resolved `room_id` into `_repush_messages` satisfies the acceptance criterion. Do not
  invent a `_source` marker, do not change `pop_all_steering_messages`' return shape, do not touch
  the five consumers' unpacking.
- **Caching the session→Room lookup.** Tempting for the hot PostToolUse path, but the explicit
  `room_id` parameter already removes the only N-times-per-hook call site. A cache introduces a
  staleness class (a session's `project_key` set after first push) for no measured win.
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
**Mitigation:** The issue's deploy gate. The PR is driven to review-clean and then **held**, not
merged, until an operator confirms fleet-wide `/update` past PR #2622. This is not machine-
verifiable from inside a single checkout. Recorded as an `[ORDERED]` No-Go.

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
**Mitigation:** In scope for this release (see Solution). Pinned by a test asserting
`peek_steering_sender(session_id, room_id=...)` observes a Room-key write.

### Risk 4: A per-message `AgentSession` load lands in a hot path

**Impact:** `_repush_messages` loops over N messages inside a PostToolUse hook that fires on every
tool call. Naive internal resolution would issue N Redis lookups per abort.
**Mitigation:** `_handle_steering` already holds the resolved `room_id`; forwarding it means zero
extra lookups on that path. The internal-resolution branch only runs for callers that supply
nothing.

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

**Location:** `push_steering_message` resolution branch.
**Trigger:** Push 1 happens before the session's `project_key` is persisted (resolves `None` →
legacy); push 2 happens after (resolves → Room). The two messages split across legs.
**Data prerequisite:** None.
**State prerequisite:** None.
**Mitigation:** Harmless by construction — consumers dual-read both legs and drain legacy first, so
FIFO order across the split is preserved for the common case and neither message is lost. This is
also precisely why a resolution cache is a rabbit hole: it would freeze the pre-assignment answer.

## No-Gos (Out of Scope)

- [ORDERED] **Merging this PR.** Blocked on a human-gated event: operator confirmation that every
  machine in the fleet has run `/update` past PR #2622. Drive to review-clean, then hold.
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
no schema. It is pure key-selection logic inside `agent/steering.py` plus two call-site
pass-throughs, propagated by the ordinary `/update` git sync. **The `/update` run itself is the
deploy gate's subject, not its target** — the fleet must already be past #2622 *before* this merges,
which is an ordering constraint on merge, not a change to the update process.

No Popoto schema migration is required: no model gains, loses, or changes a field. `Room` and
`AgentSession` are read-only inputs here.

## Agent Integration

No agent integration required — this is an internal change to the steering transport. No new CLI
entry point in `pyproject.toml [project.scripts]`, no new bridge import, no MCP surface. The bridge
already imports `push_steering_message` (`bridge/telegram_bridge.py:87`) and its call sites are
unchanged; they inherit the flip through the function's internals.

The one operator-visible surface that must keep working is `valor-session status`, whose pending-
steering peek (`tools/valor_session.py:1031`) already passes a `room_id`. It is untouched and its
continued correctness is asserted in Verification.

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
- [ ] Docstring the `room_id` parameter on `push_steering_message`, `_repush_messages`, and
  `peek_steering_sender`, including the fail-open-to-legacy contract.
- [ ] Comment the broad `except Exception` in the resolution branch explaining why fail-open is
  correct here (a steer must never be lost to a Room lookup failure).

## Success Criteria

- [ ] `push_steering_message` targets `steering:room:{room_id}` whenever a Room resolves, for all
  thirteen production call sites, with no call-site signature changes required.
- [ ] The legacy key is the fallback when the session is absent, has no `project_key`, or the
  lookup raises. No message is ever dropped for lack of a Room.
- [ ] `_repush_messages` receives and forwards `_handle_steering`'s resolved `room_id`, on the
  primary call and the retry — a Room-sourced sibling returns to the Room key.
- [ ] No provenance tag appears in the JSON payload persisted to Redis (the payload dict is
  byte-identical to today's).
- [ ] `peek_steering_sender` observes Room-key writes, so the drafter self-draft guard at
  `agent/output_handler.py:1162` keeps short-circuiting.
- [ ] `agent/session_runner/runner.py` is unmodified.
- [ ] The five dual-read drain consumers and the `valor-session status` peek are unmodified
  (verifiable from the diff).
- [ ] The steering suite is green via `scripts/pytest-clean.sh tests/integration/test_steering.py`.
- [ ] Both stale docstrings updated; no "formerly"/"used to" narration.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `python -m ruff check` and `python -m ruff format` clean.
- [ ] PR is review-clean and **held unmerged** pending the fleet deploy gate.

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

### 1. Flip the writer with internal Room resolution

- **Task ID**: build-writer
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-1 (single funnel — thirteen call sites flip at once), spike-3 (fallback to
  legacy is a hard requirement or the suite goes red)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/steering.py::push_steering_message`.
- When `room_id` is falsy, resolve it: load the session via
  `AgentSession.query.filter(session_id=session_id)`, take the first row, call
  `models.room.room_id_for_session`. Function-local imports with `# noqa: PLC0415`, matching
  `runner.py:594`.
- Wrap the resolution in a broad `except Exception` that logs at DEBUG and yields `None`. Comment
  why fail-open is correct.
- Select `_room_queue_key(resolved)` when truthy, else `_queue_key(session_id)`. Leave the payload
  dict untouched.
- Do **not** modify `agent/session_runner/runner.py`.

### 2. Thread room_id through the abort re-push

- **Task ID**: build-repush
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-2 (no per-message tag needed), Risk 4 (avoid N lookups in the hook path)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/health_check.py::_repush_messages`; forward it to every
  `push_steering_message` call inside.
- Pass `_handle_steering`'s already-resolved `room_id` at all three call sites — lines 530, 536
  (the retry), and 560/564. Missing the retry silently demotes to legacy.
- Update the stale comment at `agent/health_check.py:511-513` ("Writers are unchanged — the
  re-pushes below always target the legacy list").

### 3. Give peek_steering_sender its Room leg

- **Task ID**: build-peek
- **Depends On**: build-writer
- **Validates**: `tests/integration/test_steering.py`, `tests/unit/test_output_handler.py`
- **Informed By**: spike-4 (missed consumer; drafter self-draft guard fails open without this)
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `room_id: str | None = None` to `agent/steering.py::peek_steering_sender`; `LINDEX -1` the
  Room key first when given, fall back to legacy.
- Resolve and pass the Room at `agent/output_handler.py:1160-1162`.
- Verify the 11 `peek_steering_sender` patch sites in `tests/unit/test_output_handler.py` still
  bind (they patch the symbol wholesale, so they should).

### 4. Truth up the docstrings

- **Task ID**: build-docstrings
- **Depends On**: build-writer, build-repush, build-peek
- **Assigned To**: `steering-writer-builder`
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `agent/steering.py` module docstring (15-22) and `_room_queue_key` docstring (48-55) to
  the new status quo. No "formerly"/"used to" narration.
- Document the `room_id` parameter and the fail-open-to-legacy contract on all three changed
  functions.

### 5. Update and extend the steering tests

- **Task ID**: build-tests
- **Depends On**: build-writer, build-repush, build-peek
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: spike-3 (`test_handle_steering_drains_room_leg` assertion inverts)
- **Assigned To**: `steering-test-builder`
- **Agent Type**: test-engineer
- **Parallel**: false
- UPDATE `test_handle_steering_drains_room_leg` (~line 1746): the re-push now targets the Room key.
  Fix the docstring too.
- ADD: a real `AgentSession` with a `project_key` → `push_steering_message` lands on
  `_room_queue_key`.
- ADD the fallback matrix — session absent, `project_key` empty, `session_id=""`, `room_id=""`,
  resolution raises — each lands on `_queue_key`.
- ADD: abort siblings drained off the Room key are re-pushed to the Room key.
- ADD: the payload persisted to Redis contains exactly `{text, sender, timestamp, is_abort}` (plus
  `target_agent` when set) — no provenance field.
- ADD: `peek_steering_sender(session_id, room_id=...)` observes a Room-key write.
- Run via `scripts/pytest-clean.sh tests/integration/test_steering.py`, never bare pytest.

### 6. Validate

- **Task ID**: validate-all
- **Depends On**: build-docstrings, build-tests
- **Assigned To**: `steering-validator`
- **Agent Type**: validator
- **Parallel**: false
- Confirm `agent/session_runner/runner.py` is unmodified.
- Confirm the five drain consumers and `tools/valor_session.py`'s peek are unmodified.
- Confirm the persisted payload shape is unchanged.
- Run every row of the Verification table.

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `steering-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-steering.md` including the Risk 2 same-Room delivery semantics.
- Update the remaining-releases list at `docs/plans/durability-room-job-agentrun.md:655`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Steering suite green | `scripts/pytest-clean.sh tests/integration/test_steering.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Writer resolves a Room | `grep -c "room_id_for_session" agent/steering.py` | output > 0 |
| Writer targets the Room key | `grep -c "_room_queue_key" agent/steering.py` | output > 1 |
| Re-push forwards the room | `grep -c "room_id" agent/health_check.py` | output > 3 |
| Peek has a Room leg | `grep -c "room_id" agent/steering.py` | output > 5 |
| Stale module docstring gone | `grep -c "Writers still push to the legacy key only" agent/steering.py` | match count == 0 |
| Stale key docstring gone | `grep -c "No writer targets this key yet" agent/steering.py` | match count == 0 |
| Stale health_check comment gone | `grep -c "the re-pushes below always target the legacy list" agent/health_check.py` | match count == 0 |
| No provenance field in payload | `grep -c "_source" agent/steering.py` | match count == 0 |
| Runner untouched (anti-criterion) | `git diff origin/main --quiet -- agent/session_runner/runner.py` | exit code 0 |
| Status peek untouched (anti-criterion) | `git diff origin/main --quiet -- tools/valor_session.py` | exit code 0 |
| Drain helper not removed (anti-criterion) | `git diff origin/main -- agent/steering.py \| grep -c "^-.*_drain_list"` | match count == 0 |
| Legacy read leg not retired | `grep -c "_queue_key(session_id)" agent/steering.py` | output > 4 |

## Critique Results

**War room, 2026-08-07, against baseline `bc3f682a5`.** Depth: FULL (cross-component change on
the steering critical path). Critics: Risk & Robustness, Scope & Value, History & Consistency,
plus driver structural checks and measured source verification. Roster gate: 3/3 complete,
3/3 grounded. **Verdict: NEEDS REVISION (4 blockers).**

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The plan makes an unindexed full-scan Redis query unconditional on the hottest steering write path. `models/agent_session.py:155` declares `session_id = Field()` and Popoto's `Field()` defaults to `indexed=False`, so `AgentSession.query.filter(session_id=...)` is a scan. Measured on live Redis: three consecutive warm calls returning zero rows took 1.9s / 3.4s / 4.3s. Risk 4 reasons only about `_repush_messages`; 11 of the 12 non-test callers pass no `room_id` and would each pay seconds per push, including `bridge/telegram_bridge.py:979` (inline on inbound Telegram) and `agent/session_runner/runner.py:610`, which `_requeue_pending_steers` calls once per leftover message. | pending | Only `KeyField`/`indexed=True` fields resolve via set intersection; everything else scans every `AgentSession` hash. Benchmark the chosen resolution and assert under ~50ms. If it fails, pass `room_id` explicitly from the callers that already hold a session object (bridge, watchdog, drafter, `session_executor`) and resolve internally only for the genuinely id-only callers (`scripts/steer_child.py:119`, `scripts/migrate_steering_queue_drain.py:126`). If indexing is chosen instead, `Field(indexed=True)` needs a `rebuild_indexes()` migration registered in `MIGRATIONS` — the "Update System" section must then be rewritten, not left as "no changes required". |
| BLOCKER | Risk & Robustness | Task 1 says "take the first row" from `AgentSession.query.filter(session_id=session_id)`, but multiple rows legitimately share one `session_id` (`models/agent_session.py:130` documents `superseded` as "Replaced by a newer session for the same session_id"). An unsorted `[0]` can return a superseded row with no `project_key` (→ silent legacy fallback that looks like a Room-less session but is a bug) or a different `chat_id` (→ a different Room the live session never drains). The cited shape at `agent/session_executor.py:290` sorts first; the plan dropped the sort. | pending | Reproduce the full shape: `sessions = list(AgentSession.query.filter(session_id=session_id)); if not sessions: return None; sessions.sort(key=lambda s: s.created_at or 0, reverse=True); sessions[0]`. The `or 0` is load-bearing — `created_at` is nullable and a bare key raises `TypeError` comparing `None` to float, which the plan's broad `except Exception` would swallow into a permanent silent legacy fallback. Add a test persisting a superseded row plus a live row sharing a `session_id`. |
| BLOCKER | Scope & Value | Nothing in the plan validates the property the plan exists to deliver. Every planned test and success criterion asserts only which Redis list a write lands on. There is no test and no criterion for the actual durability behavior: write a steer for session A, terminate A, open session B in the same Room, assert B's turn-boundary drain delivers it. A build landing every listed test green can still ship a feature that delivers no steer. | pending | Two persisted `AgentSession` rows need the SAME `project_key` and an equivalent `chat_id` mapping so `room_id_for_session` returns an identical composite — cheapest shape is `project_key="test-room-durability"` with `chat_id=None` on both (`models/room.py:65-92` maps to `SYSTEM_ADDRESSEE`). Then `push_steering_message(session_a.session_id, "do X", "Tom")` followed by `pop_all_steering_messages(session_b.session_id, room_id=room_id_for_session(session_b))` must return the message. Delete both via the ORM, scoped by the `test-` project key. |
| BLOCKER | History & Consistency | Four of the five positive-signal Verification rows already pass on unmodified `main`, so the table cannot distinguish a completed build from an untouched checkout. Measured at baseline: `grep -c "room_id_for_session" agent/steering.py` = 1 (row expects `> 0`); `grep -c "_room_queue_key" agent/steering.py` = 6 (expects `> 1`); `grep -c "room_id" agent/health_check.py` = 6 (expects `> 3`); `grep -c "room_id" agent/steering.py` = 22 (expects `> 5`). All four are satisfied today by the dual-read code #2622 already landed. | pending | Thresholds were tuned as if the baseline were zero. Use behavior-anchored checks: `grep -c "room_id: str \| None = None" agent/steering.py` expecting `== 2` (the two new keywords), `grep -c "room_id=room_id" agent/health_check.py` expecting `>= 4` (one per `_repush_messages` call site), and a pytest node-id row naming the new Room-write happy-path test. Any surviving threshold row must be stated as `> <measured baseline>` with the baseline recorded inline. |
| CONCERN | Risk & Robustness | The prescribed `peek_steering_sender` semantics do not close the fail-open guard. Reading the Room key first and falling back to legacy only when the Room leg is *empty* means a Room tail from any other sender masks a `drafter-fallback` still on the legacy leg, and `agent/output_handler.py:1162` fails open exactly as Risk 3 describes. The 11 patch sites in `tests/unit/test_output_handler.py` patch the symbol wholesale, so no existing test can catch it. | pending | Read both tails and prefer the sentinel: `room_tail = r.lindex(_room_queue_key(room_id), -1) if room_id else None; legacy_tail = r.lindex(_queue_key(session_id), -1)`, decode each, then `for s in (room_sender, legacy_sender): if s == DRAFTER_FALLBACK_SENDER: return s` before falling back to `room_sender or legacy_sender`. Note the drain order is the opposite of the plan's peek order (`agent/steering.py:158-160` drains legacy FIRST), so Room-first is also semantically inconsistent with what the next turn consumes. |
| CONCERN | History & Consistency | The plan simultaneously requires `agent/session_runner/runner.py` be byte-unmodified (a success criterion plus the `git diff --quiet` anti-criterion row) and that every stale "writers are unchanged" claim be truthed up. `agent/session_runner/runner.py:589-591` contains exactly such a claim: "Writers are unchanged in this release." Both cannot hold, and the no-historical-artifacts doctrine sides with fixing the docstring. | pending | Replace the `git diff origin/main --quiet -- agent/session_runner/runner.py` row with a targeted behavioral assertion — no `room_id` added inside the `_default_steering_push` body — plus `grep -c "Writers are unchanged in this release" agent/session_runner/runner.py` expecting `0`. Both can hold at once; `--quiet` cannot. Add the runner docstring to the Task 4 list. |
| CONCERN | History & Consistency | Two load-bearing counts are wrong, which matters because the risk argument rests on "we enumerated every site". spike-1 claims "Thirteen production call sites" and cites `agent/session_executor.py:853,1930`; grep finds twelve non-test sites and `session_executor.py` has exactly one (853, via the `_push_steering_message` alias imported at 851) — line 1930 does not call it. Task 2 says to pass `room_id` "at all three call sites" of `_repush_messages`, but there are four (`agent/health_check.py:530, 536, 560, 564`) — including two retries, not one. | pending | The four calls are: 530 (abort-sibling primary), 536 (abort-sibling retry inside `except`), 560 (non-abort primary), 564 (non-abort retry inside `except`). All four become `_repush_messages(session_id, <msgs>, room_id=room_id)`. The two inside `except` blocks are what a mechanical replace on the primary call shape misses, and a miss is silent (the message lands, just on the legacy leg). Restate Success Criterion 1 as "every non-test caller" rather than a count. |
| CONCERN | Scope & Value | The plan enters build with three unresolved Open Questions, and at least two are scope/merge decisions rather than clarifications. Q1 asks whether the `peek_steering_sender` fix belongs in this release at all, which changes Tasks 3 and 5 and Risk 3. Q3 admits nobody owns the deploy-gate confirmation that the plan's own top No-Go depends on. A builder cannot know whether Task 3 is in scope, and the finished PR has no defined path to merge. | pending | A prose "hold, do not merge" is not enforceable against this repo's merge path. Make the hold mechanical: apply the `do-not-merge` label at PR-open time and state the removal condition in the PR body ("remove only after fleet-wide `/update` past #2622 is confirmed by <named owner>"). Without a label, `/do-merge` sees a review-clean PR with no blocking signal and the `[ORDERED]` No-Go has no effect. Answer Q1 as a decision in the Solution, not a question. |
| NIT | Scope & Value | The Technical Approach dictates implementation details the builder should own — exact exception shape, exact log level, function-local imports with a specific `# noqa` code, and a "resist the urge" directive. "Wrap the whole resolution in a broad `except Exception` that logs at debug" pre-commits to swallow-everything-at-DEBUG, which is what would hide the `TypeError` in the unsorted-`[0]` blocker above. | pending | — |

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-7, no gaps |
| Dependencies valid | PASS | All `Depends On` ids resolve; no cycles |
| File paths exist | PASS | 18 of 18 referenced paths exist; one referenced *line* (`agent/session_executor.py:1930`) does not hold a call |
| Prerequisites met | PASS | Redis reachable; venv on pin |
| Cross-references | FAIL | Success criterion "runner.py is unmodified" contradicts the Documentation section's docstring truth-up mandate; four Verification rows are vacuous against the measured baseline |

---

## Open Questions

1. **Scope expansion — `peek_steering_sender`.** spike-4 found a sixth consumer PR #2622 missed,
   whose absence silently breaks the drafter self-draft loop guard the moment writers flip. This
   plan folds the fix into this release, which technically touches a consumer the issue said not to
   touch. Confirm that reading is right — the alternative is shipping the flip with a known
   fail-open guard and filing the peek fix separately, which seems clearly worse.
2. **Same-Room cross-delivery (Risk 2).** All chatless sessions of a project share
   `{project}|system`, so a steer aimed at system-session A can be drained by system-session B.
   This plan accepts and documents it rather than gating on `target_agent`. Is that acceptable for
   the soak, or should consumer-side `target_agent` filtering land in the same release?
3. **Deploy gate ownership.** The plan holds the PR unmerged pending an operator confirmation that
   the whole fleet is past #2622. Who issues that confirmation, and should the PR carry a
   `do-not-merge` label so an automated merge sweep cannot pick it up?
