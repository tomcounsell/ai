---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2629
last_comment_id:
---

# react() transport derivation — stop chatless sessions enqueueing reactions to `telegram:outbox:0`

## Problem

Every session that finishes fires a completion reaction. `agent/session_executor.py:2508`
calls `react_cb(session.chat_id, session.telegram_message_id, emoji)` unconditionally, and in
the worker that callback is `TelegramRelayOutputHandler.react`, which hardcodes
`session_id = chat_id` and RPUSHes to `telegram:outbox:{chat_id}`.

For a reflection session — `chat_id="0"`, no `telegram_message_id`
(`agent/reflection_scheduler.py:626-628`) — that is the literal key `telegram:outbox:0`: a
shared collision bucket that every chatless session on the machine writes into, drained by the
relay's `telegram:outbox:*` scan, and discarded at `bridge/telegram_relay.py:141` with
`"Relay: skipping malformed reaction payload"`.

PR #2627 fixed exactly this for *text* output by deriving a `"system"` transport from the
session's Room addressee and routing it to a durable null sink. It could not fix reactions:
`react()` takes no session parameter, so `_resolve_transport(session, chat_id)` is
unreachable from it. That was left as the deliberate residual half — this issue.

**Current behavior:**
- Reflection (and any other chatless) session completes → wasted Redis write to
  `telegram:outbox:0` → relay drops it → WARNING in `logs/bridge.log` on every reflection run.
- The `telegram:outbox:0` key is created and TTL-refreshed by unrelated sessions sharing one
  bucket, which is a genuine cross-session key collision, not just noise.
- `tools/react_with_emoji.py --standalone` in a chatless harness (where
  `agent/session_executor.py:2080` exports `TELEGRAM_CHAT_ID="0"`) enqueues an undeliverable
  `custom_emoji_message` with `chat_id="0"` for the same reason.

**Desired outcome:**
- `react()` can see the session, derives `"system"` through the same `_resolve_transport`
  path `send()` already uses, and does not touch the telegram outbox.
- No `telegram:outbox:0` key is ever created.
- No `"skipping malformed reaction payload"` WARNING from a chatless session.
- The reaction is still visible in the per-session file log, so the audit trail survives.
- The `react_with_emoji` CLI no-ops the same way instead of enqueueing a dead payload.

## Freshness Check

**Baseline commit:** `e6d0e2bc7`
**Issue filed at:** 2026-08-07T04:05:39Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/output_handler.py:1410-1442` — issue claims `react()` lives here and takes no session
  parameter — **drifted to 1443-1476**; claim still holds verbatim (`session_id = chat_id` at
  line 1460).
- `tools/react_with_emoji.py` — issue claims a chatless session calling it RPUSHes to
  `telegram:outbox:0` — **partially wrong, corrected in the Recon Summary**: the CLI writes its
  own RPUSH to `telegram:outbox:{VALOR_SESSION_ID}` (right queue, wrong `chat_id="0"`), and its
  `react()` path hard-errors first because `TELEGRAM_REPLY_TO` is unset
  (`tools/react_with_emoji.py:63-68`). Only `--standalone` leaks. The literal
  `telegram:outbox:0` key comes from `TelegramRelayOutputHandler.react`, not the CLI.
- The `"invalid Telegram peer"` WARNING string is `bridge/telegram_relay.py:456`, on the
  **message** path. The reaction path (`_send_queued_reaction`, line 117) has no zero-guard and
  fails earlier on the falsy `reply_to` at line 141 with `"skipping malformed reaction payload"`.
  Tests must assert against that string, not the one in the issue title.

**Cited sibling issues/PRs re-checked:**
- #2497 — CLOSED 2026-08-07T04:11:05Z by PR #2627. Its resolution supplies every primitive this
  plan builds on: `_resolve_transport`, `_send_to_system_room`, `_deliverable_telegram_peer`,
  `SYSTEM_ADDRESSEE`, and the `chat_id="0"` → system mapping in `models/room.py`.
- #2627 — MERGED `68480ac08`, 2026-08-07T04:11:04Z. On `main`.

**Commits on main since issue was filed (touching referenced files):**
- `68480ac08` Route chatless reflection output to the system Room (#2627) — **prerequisite,
  already accounted for.**
- `ed8e3a65d` Remove the dead Python 3.12 user-site shim — irrelevant.
- `f08ab6d3d` Durability M3 (#2631), whose commit message advertises "durable reactions" —
  **checked the diff directly**: it added `_record_sent_reaction` to `bridge/telegram_relay.py`
  (a message-log write at the send-success site) and a promise-advisory metric to
  `agent/output_handler.py`. It did **not** touch `react()`, `_build_reaction_payload`, the
  reaction payload schema, or `_send_queued_reaction`'s validation. No drift to the root cause.

**Active plans in `docs/plans/` overlapping this area:**
`durability-room-job-agentrun.md` (#2494) is actively editing `bridge/telegram_relay.py` and
`agent/output_handler.py`. Overlap is adjacent, not conflicting — it works on the *relay-side
record* of a sent reaction and the drafter, this plan works on the *source-side decision* not
to enqueue one. Coordination signal for merge ordering only; no shared function is modified by
both.

## Prior Art

- **#2497 / PR #2627**: "Reflection sessions route output to the telegram transport with a
  placeholder `chat_id="0"` that is always dropped." Succeeded, for text. Introduced the
  `"system"` transport, the system-Room null sink, and the `_deliverable_telegram_peer` guard.
  Explicitly scoped reactions out. This plan is its stated residual half.
- **#1369 / PR #1382**: "Stop-hook tool-call delivery path bypasses drafter, RTR, redundancy
  filter." Established the principle this plan follows: an output path that reimplements
  delivery instead of routing through the canonical handler drifts away from it. `react()` is
  the last output method that cannot consult the transport resolver.
- **#847**: Email as a secondary transport. Set the `extra_context["transport"]` explicit-wins
  precedent that `_resolve_transport` still honors first, and gave `EmailOutputHandler.react`
  its no-op body — the precedent for "a transport with no reaction concept just returns".
- **PR #1223**: `fix(worker): route default reply via the spawning-bridge medium`. Same family
  of bug (output routed to the wrong medium), fixed at the callback-resolution layer.

No prior attempt at reaction-transport derivation exists. This is a first fix, not a repeat.

## Research

No relevant external findings — proceeding with codebase context. The change is purely internal
(no external libraries, APIs, or ecosystem patterns involved); Phase 0.7 is skipped per the
skill's own skip condition.

## Data Flow

Current, for a reflection session:

1. **Entry point**: `agent/reflection_scheduler.py:626` creates an `AgentSession` with
   `chat_id="0"`, `session_type="eng"`, no `telegram_message_id`.
2. **`agent/session_executor.py:1452`**: `_resolve_callbacks(session.project_key, None)` →
   `react_cb = TelegramRelayOutputHandler.react` (registered at `worker/__main__.py:727`).
3. **`agent/session_executor.py:2508`**: `await react_cb("0", None, "✅")` — three positional
   args, no session.
4. **`agent/output_handler.py:1460`**: `session_id = chat_id` → `"0"`.
5. **`agent/output_handler.py:1462-1470`**: `_build_reaction_payload("0", None, "✅", "0")` →
   `{"chat_id": "0", "reply_to": None, ...}`, RPUSH to `telegram:outbox:0`, `EXPIRE`.
6. **`bridge/telegram_relay.py:853`**: relay scans `telegram:outbox:*`, picks up the key.
7. **`bridge/telegram_relay.py:141`**: `not reply_to` → `"Relay: skipping malformed reaction
   payload"` WARNING, dropped.
8. **Output**: nothing delivered, one wasted key, one misleading WARNING.

After this plan, steps 4-8 are replaced by: `react()` calls `_resolve_transport(session, "0")`
→ `"system"` → `logger.debug` + file dual-write + return. No Redis write, no relay traffic.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `ReactionCallback`, the `OutputHandler` protocol's `react()`, and all
  four implementations gain a fourth `session` parameter. This is the change the issue asks
  for. It makes `react()` symmetric with `send()`, which has carried `session` since #847 —
  `SendCallback = Callable[[str, str, int, Any], Awaitable[None]]` is the exact precedent
  `ReactionCallback` now follows.
- **Coupling**: net *decrease*. Today two output methods on the same handler disagree about
  whether transport is knowable; after this they share one resolver. Moving
  `_deliverable_telegram_peer` into `models/room.py` removes a duplicated notion of "is this a
  real Telegram peer" rather than adding a third copy in the CLI.
- **Data ownership**: unchanged. No new Redis keys, no schema change, no Popoto model touched —
  therefore **no `scripts/update/migrations.py` entry is required**.
- **Reversibility**: high. The change is additive-with-a-guard; reverting is a single revert
  commit with no data to unwind.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully pinned by the issue and the #2627 precedent)
- Review rounds: 1

## Prerequisites

No prerequisites — every primitive this plan consumes (`_resolve_transport`,
`_send_to_system_room`, `_deliverable_telegram_peer`, `SYSTEM_ADDRESSEE`) is already merged on
`main` via #2627. No secrets, services, or external access required.

## Solution

### Key Elements

- **`ReactionCallback` gains a session parameter** — `agent/session_state.py:17` becomes
  `Callable[[str, int, str | None, Any], Awaitable[None]]`, matching `SendCallback`'s shape one
  line above it. This is the signature change the issue names as the required fix.
- **`react()` consults the transport resolver** — `TelegramRelayOutputHandler.react` calls the
  same `_resolve_transport(session, chat_id)` that `send()` uses, and short-circuits on
  `"system"` before any Redis write.
- **System reactions drop with a debug log, not a Room append** — a bare emoji written into the
  system Room's inbox is an entry with no text and no reader. The issue offers both options;
  this plan takes the drop, and keeps the `FileOutputHandler` dual-write so the reaction still
  appears in `logs/worker/{session_id}.log` as the audit record.
- **The executor passes the session and skips anchorless reactions** — `session_executor.py`
  forwards `agent_session` as the fourth argument, and skips the call entirely when
  `telegram_message_id` is None (there is no message to react to; this also covers the
  no-`project_key` fallback where `_resolve_transport` cannot reach a Room).
- **`_deliverable_telegram_peer` moves to `models/room.py`** — becomes module-level
  `deliverable_telegram_peer()` next to `SYSTEM_ADDRESSEE` and `addressee_for_session`, which
  already own the "`0` is not an address" semantics. The handler's classmethod delegates.
- **The CLI reuses that helper** — `tools/react_with_emoji.py::_resolve_transport` returns
  `"system"` when `TELEGRAM_CHAT_ID` is not a deliverable peer, and both `react()` and
  `standalone()` no-op on `"system"` exactly as they already no-op on `"email"`.

### Flow

Reflection session completes → executor resolves `react_cb` → executor sees
`telegram_message_id is None` → **no call at all** (fast path)

Chatless session *with* an anchor message → executor calls `react_cb(chat_id, msg_id, emoji,
agent_session)` → `react()` resolves `"system"` → debug log + file dual-write → **no outbox
write**

Normal Telegram session → `react()` resolves `"telegram"` → unchanged RPUSH to
`telegram:outbox:{chat_id}` → relay sets the reaction

Chatless harness runs `react_with_emoji.py` → `_resolve_transport()` sees non-deliverable
`TELEGRAM_CHAT_ID` → `"system"` → prints a no-op notice, exits 0

### Technical Approach

Corrected line references (post-drift, baseline `e6d0e2bc7`):

- `agent/session_state.py:17` — widen `ReactionCallback` to four positional parameters. Keep
  the alias a `Callable`, not a `Protocol`: `SendCallback` on line 16 is already a four-arg
  `Callable` with a trailing `Any` session and this must read as its twin.
- `agent/output_handler.py:91-104` — `OutputHandler` protocol `react()` gains
  `session: Any = None`, docstring mirroring `send()`'s.
- `agent/output_handler.py:148` — `FileOutputHandler.react` accepts and ignores `session`, but
  prefers `getattr(session, "session_id", None) or chat_id` for the log filename, replacing the
  `session_id = chat_id  # Best effort` line. This is the one place the session genuinely
  improves existing behavior for free: today a chatless session's reaction lands in
  `logs/worker/0.log`.
- `agent/output_handler.py:1443` — `TelegramRelayOutputHandler.react(chat_id, msg_id,
  emoji=None, session=None)`:
  - `if self._resolve_transport(session, chat_id) == "system":` → `logger.debug(...)`, perform
    the `_file_handler` dual-write, `return`.
  - Otherwise unchanged. **`session_id = chat_id` stays as-is on the telegram path** — see
    Rabbit Holes; changing it to the session's own id is a separate, riskier change that
    `_rtr_queue_reaction`'s Implementation Note F7 already documents.
- `bridge/email_bridge.py:1012` — `EmailOutputHandler.react` accepts `session=None`; body stays
  a no-op.
- `bridge/telegram_bridge.py:3011` — the Telethon `_react` closure gains `session=None` and the
  same `"system"` short-circuit (in-bridge execution hits `set_reaction(client, int("0"), ...)`
  → `PeerIdInvalidError`; it is the same bug wearing a different coat).
- `agent/session_executor.py:2479` — extend the existing `if react_cb and not
  chat_state.defer_reaction:` guard with `and session.telegram_message_id is not None`.
- `agent/session_executor.py:2508` — `await react_cb(session.chat_id,
  session.telegram_message_id, emoji, agent_session)`.
- `models/room.py` — add module-level `deliverable_telegram_peer(chat_id) -> bool`, moved
  verbatim (including the `"--5"` multi-hyphen `try/except ValueError` guard) from
  `agent/output_handler.py:455-463`. `TelegramRelayOutputHandler._deliverable_telegram_peer`
  becomes a one-line delegation so existing call sites and tests keep working.
- `tools/react_with_emoji.py:33` — `_resolve_transport()` gains, after the `VALOR_TRANSPORT`
  and `EMAIL_REPLY_TO` checks and before the `TELEGRAM_CHAT_ID` check, a
  `deliverable_telegram_peer` test on `TELEGRAM_CHAT_ID` returning `"system"` when it fails.
  Import lazily inside the function (repo convention; the CLI is already on a lazy-import diet).
- `tools/react_with_emoji.py:51,113` — add a `"system"` branch to both `react()` and
  `standalone()` alongside the existing `"email"` no-op, printing a one-line notice and
  returning.
- **`tools/send_message.py` is deliberately untouched**: `_send_via_telegram` already passes
  `session=session` into `handler.send`, so chatless sends already resolve to `"system"` and
  reach `_send_to_system_room`. Injecting a `VALOR_TRANSPORT="system"` env var (a tempting
  shortcut for the CLI leg) would *break* that working path by tripping `send_message.py:395`'s
  unsupported-transport exit. Recorded here so a reviewer does not propose it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/output_handler.py:1471-1472` — the `except Exception` around the outbox RPUSH logs
  at `logger.error`. Existing coverage keeps it; the new system branch returns before it, so a
  test asserts the error log is **not** emitted on the system path.
- [ ] `agent/session_executor.py:2509-2510` — `except Exception` around `react_cb` logs
  `"Failed to set reaction"`. A test passes a `react_cb` that rejects the fourth argument
  (`TypeError`) and asserts the WARNING is logged and the session still finalizes — this is the
  exact failure mode of a stale out-of-tree callback and must degrade, not crash.
- [ ] `models/room.py::deliverable_telegram_peer` has no exception handler beyond the moved
  `try/except ValueError`; the `"--5"` case is covered by the migrated test.

### Empty/Invalid Input Handling
- [ ] `react(chat_id="", ...)`, `react(chat_id=None, ...)` — `_deliverable_telegram_peer`
  already returns False for both; assert they do not produce a `telegram:outbox:` write when
  the session resolves to system, and that the pre-existing `chat_id`-keyed behavior is
  unchanged when `session is None` (back-compat).
- [ ] `emoji=None` (the Teammate clear-reaction case at `session_executor.py:2484`) must still
  clear on the telegram path and still no-op on the system path.
- [ ] `tools/react_with_emoji.py` with `TELEGRAM_CHAT_ID=""` / unset — must not regress into
  the `"system"` branch and swallow a genuine misconfiguration; unset stays the existing
  `"telegram"` default that then errors on the missing-env guard.

### Error State Rendering
- [ ] The system path's user-visible surface is the log. Assert the `logger.debug` message
  names the session id and the emoji, and assert the `FileOutputHandler` entry is written, so
  a dropped reaction is observable rather than silent.
- [ ] `tools/react_with_emoji.py` prints its no-op notice to **stdout** and exits 0 (matching
  the email no-op at line 54), so the agent reading the tool output sees the outcome.

## Test Impact

- [ ] `tests/unit/test_output_handler.py:164,275,304,335` — `handler.react("chat-1", 42, "👍")`
  three-arg calls — UPDATE (no change needed to the calls themselves; `session` defaults to
  None → `_resolve_transport(None, ...)` returns `"telegram"` → existing assertions hold). Add
  an explicit assertion in one of them that the telegram path is unchanged when `session=None`,
  pinning back-compat.
- [ ] `tests/unit/test_output_handler.py:50` — the fake handler's
  `async def react(self, chat_id, msg_id, emoji=None)` — UPDATE: add `session=None` so it
  satisfies the widened protocol.
- [ ] `tests/unit/test_output_handler.py:374-428` — the five `_resolve_transport` tests from
  #2627 — no change; they exercise the classmethod directly and stay green.
- [ ] `tests/unit/test_email_bridge.py:544,550` — `EmailOutputHandler.react` calls — UPDATE:
  add a case passing a session to prove the no-op is session-agnostic.
- [ ] `tests/unit/test_agent_session_queue.py:1020,1051,1073,1080` — fake handlers already use
  `async def react(self, *args, **kwargs)` — no change required (verified).
- [ ] `tests/unit/test_react_with_emoji.py` — UPDATE: add `"system"`-transport cases for both
  `react()` and `standalone()`; confirm no existing case sets `TELEGRAM_CHAT_ID="0"` such that
  it would flip disposition.
- [ ] `tests/unit/test_bridge_worker_liveness_reaction.py`,
  `tests/unit/test_worker_down_reactions.py`, `tests/unit/test_reaction_never_hostile.py` —
  audit only; they exercise `_build_reaction_payload` and the worker-down path, neither of
  which changes. Expect no edits; the build must confirm rather than assume.
- [ ] NEW `tests/unit/test_output_handler.py::TestReactTransportDerivation` — the red-first
  suite for this fix (see Success Criteria).

## Rabbit Holes

- **Changing `session_id = chat_id` to the session's own id on the telegram path.** Tempting
  while the session is finally in scope, and `_rtr_queue_reaction`'s docstring (Implementation
  Note F7) even complains about it. But the relay drains `telegram:outbox:*` wholesale, so the
  key choice is not a delivery bug today, and flipping it silently re-homes every in-flight
  reaction across a deploy. Separate change, separate risk budget.
- **Adding a zero-guard to `bridge/telegram_relay.py::_send_queued_reaction` for symmetry with
  the message path at line 456.** A real gap, but #2497 ruled the relay guard out of scope as
  defence-in-depth and the source-side fix makes it unreachable from this path. Filed, not
  built (see No-Gos).
- **Appending reactions to the system Room inbox via `_send_to_system_room`.** Reuses existing
  machinery and *looks* like the tidier symmetry, but produces inbox entries with a null `text`
  and no reader, polluting the same durable list the Telegram intake shadow-writes to. The
  debug-log drop is the honest sink for a signal that has no audience.
- **Unifying the four reaction-payload builders** (`_build_reaction_payload`,
  `_rtr_queue_reaction`, `session_completion.py:591`, `worker_down_reactions.py:137`). Genuine
  duplication, genuinely out of scope for a signature fix.
- **Injecting `VALOR_TRANSPORT="system"` into the harness env** to fix the CLI leg without
  touching `react_with_emoji.py`. Actively harmful — it would break `tools/send_message.py`,
  which currently routes chatless sends correctly. Explained in Technical Approach.

## Risks

### Risk 1: An out-of-tree or stale reaction callback rejects the fourth positional argument
**Impact:** `TypeError` at `session_executor.py:2508` on every session completion.
**Mitigation:** All four in-repo implementations are updated in the same commit, and the call
site is already wrapped in `try/except Exception` logging `"Failed to set reaction"` — a stale
callback degrades to a missing reaction, never a failed session. A test asserts exactly this
degradation. `register_callbacks` accepts arbitrary callables and cannot type-check them, which
is why the runtime-degradation test is the real guard, not the type alias.

### Risk 2: The new `telegram_message_id is None` skip suppresses a reaction that used to land
**Impact:** A real user stops seeing completion emoji.
**Mitigation:** A `None` anchor means `_build_reaction_payload` produces `reply_to: None`, which
`_send_queued_reaction` already rejects at `bridge/telegram_relay.py:141` — so nothing that
currently reaches a user is suppressed. The skip removes only writes that were already dead on
arrival. A test asserts a non-None `telegram_message_id` on a telegram session still reacts.

### Risk 3: `_resolve_transport` hits Redis (`room_id_for_session` / `addressee_for_session`) on
every session completion
**Impact:** A Redis outage during the reaction step raises inside `react()`, which — unlike
`send()` — has no outer try/except around the resolution call.
**Mitigation:** Wrap the resolution in a try/except that falls back to `"telegram"` (the
status-quo behavior), matching `_send_to_system_room`'s "never raises" contract. Test with a
`_resolve_transport` that raises, asserting the telegram path still runs.

### Risk 4: Merge collision with the active `durability-room-job-agentrun.md` (#2494) work
**Impact:** Conflicts in `agent/output_handler.py` and `bridge/telegram_relay.py`.
**Mitigation:** This plan does not modify `bridge/telegram_relay.py` at all, and touches a
disjoint region of `agent/output_handler.py` (`react()` at 1443+; #2631 touched the drafter
region near 800). Rebase before merge; no functional interaction.

## Race Conditions

No race conditions identified. The reaction path is a single synchronous decision inside one
awaited coroutine on the session-completion path: read session fields → resolve transport →
either RPUSH or return. There is no shared mutable state, no cross-process handoff introduced,
and no ordering dependency created. The one pre-existing concurrency artifact — multiple
chatless sessions writing into the shared `telegram:outbox:0` bucket — is *removed* by this
change, not managed.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2644] Zero-guard in `bridge/telegram_relay.py::_send_queued_reaction` for
  symmetry with the message-path guard at line 456. Real latent gap (a chatless session with a
  non-null `telegram_message_id` would reach `set_reaction(client, 0, ...)` and raise
  `PeerIdInvalidError`), ruled out of scope by #2497 as defence-in-depth and made unreachable
  from this path by the source-side fix.
- Re-keying the telegram reaction outbox from `chat_id` to the session's own `session_id`
  (Implementation Note F7 / `_rtr_queue_reaction`'s complaint) — **not deferred, judged not a
  defect**: the relay drains `telegram:outbox:*` wholesale, so the key choice causes no delivery
  loss today. It stays a Rabbit Hole (avoid touching it), not a promised follow-up.
- Consolidating the four reaction-payload construction sites — **not deferred, judged not a
  defect**: `session_completion.py:591` and `worker_down_reactions.py:137` are both anchored to
  a real inbound human message and provably cannot carry `chat_id="0"`. Verified during recon;
  no action needed rather than action postponed.

## Update System

No update system changes required — this feature is purely internal. No new dependency, no new
config file, no new env var, no Popoto schema change (therefore no
`scripts/update/migrations.py` entry). The change ships with the normal
`./scripts/valor-service.sh restart` that `/update` already performs, which is required here
because both the worker (`worker/__main__.py` callback registration) and the bridge
(`bridge/telegram_bridge.py::_react`) hold the modified callbacks in memory.

## Agent Integration

No new agent surface. `tools/react_with_emoji.py` is already wired as a CLI the harness invokes
via Bash and is already declared where it needs to be; this plan changes its behavior on one
branch, not its entry point. The bridge does not need a new import — it already imports
`TelegramRelayOutputHandler` at `bridge/telegram_bridge.py:2909` and registers the callbacks at
line 3016; only the closure signature changes.

Integration coverage: a test that drives a chatless `AgentSession` through the executor's
reaction step with a registered `TelegramRelayOutputHandler` and asserts no `telegram:outbox:*`
key is created — the end-to-end proof the agent-invoked path is actually fixed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-message-delivery.md` — #2627 added transport resolution as
  step 0 of the filter list for `send()`; add the reaction path to the same section, stating
  that `react()` resolves the same way and that system-transport reactions are dropped with a
  debug log rather than sunk into the Room inbox (and why).
- [ ] Update `docs/features/bridge-worker-architecture.md` — the handler table gained a
  system-Room inbox row in #2627; add the reaction row, and note that `ReactionCallback` is now
  four-arg symmetric with `SendCallback`.
- [ ] No new `docs/features/` page and no `docs/features/README.md` index entry — this closes a
  gap in an existing documented feature rather than introducing one.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site.

### Inline Documentation
- [ ] `TelegramRelayOutputHandler.react` docstring gains the `session` arg and states the
  drop-not-sink decision with its rationale.
- [ ] `agent/session_state.py` `ReactionCallback` gains a trailing comment naming the four
  positional args, matching `SendCallback`'s existing comment on the line above.
- [ ] `_rtr_queue_reaction`'s docstring reference to "``react()`` derives ``session_id =
  chat_id`` (line 411)" is stale on two counts (wrong line number, and now an incomplete
  description) — correct it.
- [ ] `models/room.py::deliverable_telegram_peer` carries the moved docstring including the
  `"--5"` rationale; `agent/output_handler.py`'s delegating classmethod points at it.

## Success Criteria

- [ ] `ReactionCallback` in `agent/session_state.py` takes four positional parameters.
- [ ] `TelegramRelayOutputHandler.react` calls `_resolve_transport` and returns before any
  Redis write when it yields `"system"`.
- [ ] Red-first proof: `TestReactTransportDerivation` fails on `main` before the fix and passes
  after, covering — (a) chatless session → no `telegram:outbox:*` write, (b) `session=None` →
  unchanged telegram RPUSH (back-compat), (c) real peer + system-addressee session → still
  telegram (explicit-peer-wins, mirroring `_resolve_transport`'s own precedence), (d)
  `_resolve_transport` raising → telegram fallback, (e) file dual-write still occurs on the
  system path, (f) `emoji=None` clear still works on both paths.
- [ ] Integration: a chatless session driven through `session_executor`'s reaction step creates
  **zero** `telegram:outbox:*` keys.
- [ ] `tools/react_with_emoji.py` `react()` and `--standalone` both no-op and exit 0 when
  `TELEGRAM_CHAT_ID` is not a deliverable peer.
- [ ] `agent/output_handler.py` contains no second copy of the peer-parsing logic — it
  delegates to `models/room.py`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions needed — the recon scan found zero xfail markers in `tests/`.

## Team Orchestration

### Team Members

- **Builder (handler + callback signature)**
  - Name: `react-signature-builder`
  - Role: Widen the reaction callback contract and implement the system short-circuit in
    `agent/output_handler.py`, `agent/session_state.py`, `bridge/email_bridge.py`,
    `bridge/telegram_bridge.py`, `agent/session_executor.py`, `models/room.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (CLI leg)**
  - Name: `react-cli-builder`
  - Role: `tools/react_with_emoji.py` system-transport no-op, reusing the `models/room.py`
    helper.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `react-test-engineer`
  - Role: Red-first `TestReactTransportDerivation`, the executor integration test, and the
    Test Impact updates.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `react-documentarian`
  - Role: The two `docs/features/` updates and the stale-docstring corrections.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `react-validator`
  - Role: Verify every Success Criteria row and run the Verification table.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Red-first reaction transport tests
- **Task ID**: test-react-transport
- **Depends On**: none
- **Validates**: `tests/unit/test_output_handler.py::TestReactTransportDerivation` (create)
- **Informed By**: recon (react() at `agent/output_handler.py:1443-1476`; relay rejects at
  `bridge/telegram_relay.py:141` with `"skipping malformed reaction payload"`, NOT
  `"invalid Telegram peer"`)
- **Assigned To**: react-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Write the six cases from Success Criteria (a)-(f) against the current three-arg `react()`
  where possible, four-arg where the signature change is the point.
- Run them and capture the RED output verbatim for the PR description.
- Do not implement any production change in this task.

### 2. Move the peer helper to `models/room.py`
- **Task ID**: build-room-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_room_resolution.py`, `tests/unit/test_output_handler.py`
- **Assigned To**: react-signature-builder
- **Agent Type**: builder
- **Parallel**: true
- Add module-level `deliverable_telegram_peer(chat_id) -> bool` to `models/room.py`, moving the
  body verbatim from `agent/output_handler.py:455-463` including the `"--5"` guard.
- Make `TelegramRelayOutputHandler._deliverable_telegram_peer` a one-line delegation so the
  #2627 tests at `tests/unit/test_output_handler.py:374-428` stay green untouched.

### 3. Widen the reaction contract and short-circuit system transport
- **Task ID**: build-react-signature
- **Depends On**: build-room-helper
- **Validates**: `tests/unit/test_output_handler.py`, `tests/unit/test_email_bridge.py`,
  `tests/unit/test_agent_session_queue.py`
- **Informed By**: recon (call site `agent/session_executor.py:2508`; registration
  `worker/__main__.py:727`; bridge closure `bridge/telegram_bridge.py:3011`)
- **Assigned To**: react-signature-builder
- **Agent Type**: builder
- **Domain**: async/concurrency — the resolution call is inside an awaited coroutine on the
  session-completion path; it must never raise (Risk 3) and must not add a blocking Redis call
  to the hot path beyond what `send()` already pays.
- **Parallel**: false
- `agent/session_state.py:17` — widen `ReactionCallback` to four positional params with a
  trailing comment mirroring `SendCallback`'s.
- `agent/output_handler.py:91` — protocol `react()` gains `session: Any = None`.
- `agent/output_handler.py:148` — `FileOutputHandler.react` accepts `session`, uses
  `getattr(session, "session_id", None) or chat_id` for the log filename.
- `agent/output_handler.py:1443` — `TelegramRelayOutputHandler.react` gains `session`, resolves
  transport inside a try/except falling back to `"telegram"`, and on `"system"` emits
  `logger.debug`, performs the file dual-write, and returns before the RPUSH.
- Leave `session_id = chat_id` on the telegram path unchanged (Rabbit Holes).
- `bridge/email_bridge.py:1012` — accept `session=None`, body stays a no-op.
- `bridge/telegram_bridge.py:3011` — `_react` gains `session=None` and the same system
  short-circuit.
- `agent/session_executor.py:2479` — add `and session.telegram_message_id is not None` to the
  guard; `:2508` — pass `agent_session` as the fourth argument.

### 4. CLI system-transport no-op
- **Task ID**: build-react-cli
- **Depends On**: build-room-helper
- **Validates**: `tests/unit/test_react_with_emoji.py`
- **Assigned To**: react-cli-builder
- **Agent Type**: builder
- **Parallel**: true
- `tools/react_with_emoji.py:33` — `_resolve_transport()` returns `"system"` when
  `TELEGRAM_CHAT_ID` is set but fails `deliverable_telegram_peer` (lazy import inside the
  function). Unset `TELEGRAM_CHAT_ID` must keep falling through to the existing `"telegram"`
  default so a genuine misconfiguration still errors.
- Add a `"system"` no-op branch to `react()` (line 51) and `standalone()` (line 113), printing
  to stdout and returning 0, mirroring the existing `"email"` branches.
- Do NOT set or consume `VALOR_TRANSPORT="system"` anywhere — it would break
  `tools/send_message.py` (Technical Approach).

### 5. Turn the tests green and close Test Impact
- **Task ID**: test-green
- **Depends On**: build-react-signature, build-react-cli
- **Validates**: `tests/unit/test_output_handler.py`, `tests/unit/test_react_with_emoji.py`,
  `tests/unit/test_email_bridge.py`, `tests/unit/test_agent_session_queue.py`,
  `tests/unit/test_bridge_worker_liveness_reaction.py`,
  `tests/unit/test_worker_down_reactions.py`, `tests/unit/test_reaction_never_hostile.py`
- **Assigned To**: react-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Apply every Test Impact disposition; confirm (do not assume) the three audit-only files need
  no edits.
- Add the executor-level integration assertion that a chatless session creates zero
  `telegram:outbox:*` keys.
- Add the Failure Path tests: callback rejecting the fourth arg degrades with a WARNING;
  `_resolve_transport` raising falls back to telegram.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: test-green
- **Assigned To**: react-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-message-delivery.md` and
  `docs/features/bridge-worker-architecture.md` per the Documentation section.
- Fix the stale line reference in `_rtr_queue_reaction`'s docstring.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: test-green, document-feature
- **Assigned To**: react-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm each Success Criteria checkbox against the actual diff.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reaction unit tests pass | `./scripts/pytest-clean.sh tests/unit/test_output_handler.py tests/unit/test_react_with_emoji.py tests/unit/test_email_bridge.py -q` | exit code 0 |
| Reaction-adjacent suites pass | `./scripts/pytest-clean.sh tests/unit/test_agent_session_queue.py tests/unit/test_worker_down_reactions.py tests/unit/test_reaction_never_hostile.py tests/unit/test_bridge_worker_liveness_reaction.py tests/unit/test_room_resolution.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| react() consults the resolver | `grep -c "_resolve_transport" agent/output_handler.py` | output > 2 |
| ReactionCallback is four-arg | `grep -c "ReactionCallback = Callable\[\[str, int, str \| None, Any\]" agent/session_state.py` | output contains 1 |
| Executor passes the session | `grep -c "react_cb(session.chat_id, session.telegram_message_id, emoji, agent_session)" agent/session_executor.py` | output contains 1 |
| Peer helper has one home | `grep -c "lstrip(\"-\").isdigit()" agent/output_handler.py` | match count == 0 |
| Anti-criterion: no relay zero-guard added (No-Go #2644) | `git diff origin/main...HEAD -- bridge/telegram_relay.py \| grep -c "chat_id_int == 0"` | match count == 0 |
| Anti-criterion: telegram outbox key not re-homed (Rabbit Hole) | `grep -c "session_id = getattr(session, \"session_id\", None) or chat_id" agent/output_handler.py` | match count == 0 |
| Anti-criterion: no VALOR_TRANSPORT=system injection | `grep -rn "VALOR_TRANSPORT.*system" agent/ tools/ bridge/ \| wc -l` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Drop vs. sink for system-transport reactions.** The issue offers both. This plan drops
   with a debug log (plus the file dual-write) on the reasoning that a bare emoji with no text
   and no reader pollutes the same Room inbox the Telegram intake shadow-writes to. If the
   durability work (#2494) later wants reaction telemetry in the Room, this is the decision to
   revisit — confirm the drop is the right call now.
2. **The `telegram_message_id is None` skip at `session_executor.py:2479`.** It closes the
   symptom for *all* chatless paths independently of Room resolution, including the
   no-`project_key` case where `_resolve_transport` falls back to telegram. It is belt-and-
   braces alongside the principled fix. Keep both, or is the redundancy unwelcome?
3. **Merge ordering against `durability-room-job-agentrun.md` (#2494).** The files overlap but
   the regions do not. Confirm this can land independently rather than queueing behind the M3
   follow-ons.
