---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-20
tracking: https://github.com/tomcounsell/ai/issues/2178
---

# Clear the Worker-Down (⚠) Reaction When the Worker Recovers

## Problem

Stretch follow-up to #1312. When the bridge reacts with the worker-down warning
(⚠) on a message that arrived while no worker was alive, that reaction lingers
forever — even after the worker recovers and drains the session. The user is
left staring at a stale ⚠ on a request that is now actually being processed.

Clearing the reaction requires two capabilities #1312 deliberately deferred:

1. **Per-message tracking** — the bridge must remember which (chat_id, message_id)
   pairs got a ⚠ so they can be un-reacted later.
2. **A worker→bridge reach-back** — the recovering worker has no Telethon client;
   only the bridge can mutate a Telegram reaction. The worker must signal the
   bridge to replace the stale ⚠.

## Root Cause

The reaction is applied by the bridge process at message-ingestion time, but the
recovery event (worker draining the session) happens in a *separate* worker
process. There is no shared record of which messages were warned, and no path
for the worker to reach back into the bridge's Telethon client.

## Approach

Reuse the existing `telegram:outbox:{session_id}` relay — the same Redis-mediated
reach-back the worker already uses to deliver text and RTR-suppress reactions
(`agent/output_handler.py`, drained by `bridge/telegram_relay.py`). No new
listener or IPC channel is introduced.

New module `agent/worker_down_reactions.py`:

- `record_worker_down_reaction(session_id, chat_id, message_id)` — the bridge (via
  #1312's ⚠-set site) records the warned message under the enqueued session's id
  in a short-TTL Redis list `bridge:worker_down_reactions:{session_id}`. These are
  ephemeral bridge-infra keys, not Popoto-managed records (same precedent as
  `worker:registered_pid:*` and `telegram:outbox:*`), so the shared Redis client
  is used directly.
- `clear_worker_down_reactions(session_id)` — when the worker drains the session
  (pending→running in `agent/session_pickup.py`), it reads the tracked messages
  and writes a **replacement** reaction (the normal "processing" emoji ✍) to
  `telegram:outbox:{session_id}` for each, then deletes the tracking key. The
  bridge relay picks these up and replaces the ⚠, making it disappear.

**Why replace, not clear-to-empty:** the relay's `_send_queued_reaction` drops
payloads with a falsy `emoji` field (`if not chat_id or not reply_to or not emoji`),
so an empty-reaction payload can't traverse the existing relay. The issue
sanctions "clear (or replace with a normal processing reaction)"; replacing with
✍ (REACTION_PROCESSING — "actively composing") is both accurate to the new state
and compatible with the relay unchanged. A single reaction from the bot account
replaces the prior one on the same message, so ✍ overwrites ⚠.

Wire `clear_worker_down_reactions(chosen.session_id)` into both pickup paths in
`agent/session_pickup.py` (`_pop_agent_session` and its sync fallback), right
after the `transition_status(..., "running")` call. Fail-silent — a reaction
cleanup must never crash session pickup.

## Success Criteria

- `record_worker_down_reaction` persists a warned (chat_id, message_id) under
  `bridge:worker_down_reactions:{session_id}` with a TTL.
- On worker pickup (pending→running), `clear_worker_down_reactions` writes one
  replacement-reaction payload per warned message to `telegram:outbox:{session_id}`
  and deletes the tracking key.
- Clearing an unknown/empty session is a no-op that returns 0 and never raises.
- Session pickup never crashes on a reaction-cleanup failure (fail-silent).
- Scoped unit tests pass; `ruff check`/`format` clean on touched files.

## No-Gos

- No new bridge listener, pub/sub channel, or watchdog hook — the outbox relay is
  the sole reach-back.
- No relay schema change — replacement reaction rides the existing payload shape.
- No Popoto model for the tracking store — ephemeral infra keys only.
- Do not implement #1312's ⚠-set path here; this PR provides the `record_` hook
  #1312 will call and owns the clear/replace lifecycle.

## Update System
No update system changes required — this feature is purely internal (Redis keys +
worker pickup wiring); no new dependencies, config, or migration steps.

## Agent Integration
No agent integration required — this is a bridge/worker-internal reach-back. No
new CLI entry point and no new bridge import surface; the worker calls the clear
function during its existing session-pickup path.

## Failure Path Test Strategy
Unit test the record→clear round-trip against Redis: record two warned messages,
call clear, assert the tracking key is gone and two replacement-reaction payloads
land on `telegram:outbox:{session_id}` with the processing emoji. Also assert
clear on an unknown session is a no-op returning 0.

## Test Impact
No existing tests affected — this is greenfield behavior (a new module and two
additive call sites in the pickup path); no prior test asserts reaction-clearing
on worker recovery.

## Rabbit Holes
- Do not try to make the relay support empty-reaction clears — replacement is the
  chosen, relay-compatible outcome.
- Do not attempt to un-react from the worker directly — the worker has no Telethon
  client; the outbox reach-back is the only sanctioned path.

## Documentation
- [ ] Add a "Worker-down reaction lifecycle" note to
      `docs/features/bridge-worker-architecture.md` describing the record→clear
      reach-back (bridge records the ⚠, worker replaces it via the outbox relay on
      pickup).
- [ ] Reference this plan from `docs/plans/bridge-worker-liveness-reaction.md`'s
      stretch note so the #1312 plan points at the shipped clear path.
