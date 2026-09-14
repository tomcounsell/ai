# Plan: Fix duplicate Telegram delivery when message_id is null (#2179)

## Root cause

A single logical reply reaches the "Eng: Valor" chat through two emit paths:
the relay's `pm_direct` send (sender `system`) and the executor's `response`
send (sender "Valor"). The relay is supposed to collapse these: after it ships
the `pm_direct` copy it records that text in `AgentSession.recent_sent_drafts`
via `_record_relay_sent_draft`, so `bridge.redundancy_filter` suppresses the
executor's follow-up `send_cb`.

That reconciliation is gated inside `if success:` in
`bridge/telegram_relay.py::process_outbox`, where
`success = (msg_id is not None)`. The send helper `_send_queued_message`
returns `int | None`, and **`None` is overloaded to mean both "send failed" and
"send delivered but Telegram/Telethon returned no message id"** (e.g.
`send_markdown` returns a message object whose `.id` is `None`). When a
`pm_direct` send delivers with a null `msg_id`:

1. `success` is `False`, so `_record_relay_sent_draft` never runs — the
   executor's `response` copy is not suppressed and ships ~2s later (the
   observed double post).
2. `success` is `False`, so the already-delivered message is re-queued for
   retry, risking a second `pm_direct` copy too.

Both 07-18 copies carried a null `message_id`, matching this path exactly.

## Approach

Decouple "delivery reached Telegram" from "message_id captured." Introduce a
module sentinel `DELIVERED_NO_ID` returned by `_send_queued_message` on its
delivered-but-no-id branches (voice-note, file+text, text-only). `process_outbox`
treats the sentinel as `success=True` with `msg_id=None`, so:

- `_record_relay_sent_draft` fires (dedup registered) even with a null id —
  the executor `response` path is now suppressed → exactly one delivery.
- The delivered message is not re-queued.
- `_record_sent_message` and the Redis history store still correctly require a
  real `msg_id` (they gate on `msg_id is not None`).

Failure (exception) and drop (malformed / no chat_id) branches keep returning
plain `None`, so genuine failures still retry/dead-letter unchanged.

## Success Criteria
- [ ] Root cause documented: `success = (msg_id is not None)` conflates delivery
  failure with delivered-but-null-id.
- [ ] A `pm_direct` reply delivered with a null `message_id` records the dedup
  draft, so the executor `response` copy is suppressed — exactly one delivery.
- [ ] A delivered-but-null-id message is not re-queued.
- [ ] Genuine send failure still skips dedup and re-queues (unchanged).
- [ ] Regression test in `tests/unit/test_bridge_relay.py` covers the null-id
  double-send path.

## No-Gos

- No change to `_send_queued_message`'s failure/drop semantics (still `None`).
- No message splitting; no LLM calls added to the relay path.
- No change to `redundancy_filter` scoring.

## Update System
No update system changes required — this is a purely internal bridge relay fix;
no new deps, config, or migration.

## Agent Integration
No agent integration required — bridge-internal change to an existing relay path.
No new CLI entry point or bridge import.

## Failure Path Test Strategy
Regression test drives `process_outbox` with a `pm_direct` text message whose
send delivers a null `message_id`, asserting the dedup draft is recorded and the
message is not re-queued. A second case asserts a genuine send failure still
skips dedup and re-queues.

## Test Impact
- [ ] `tests/unit/test_bridge_relay.py` — ADD: null-msg_id dedup regression cases
  (no existing case exercises the delivered-but-null-id branch).

## Rabbit Holes
- Do not refactor `_send_queued_message`'s multi-branch return structure beyond
  the sentinel; the drop/failure returns are intentionally `None`.

## Documentation
- [ ] Add a note to `docs/features/bridge-worker-architecture.md` in the relay
  section stating that the `#1205-style` dedup registration
  (`_record_relay_sent_draft`) fires on delivery success and is independent of
  whether a Telegram `message_id` was captured, so a null `message_id` no longer
  defeats response/pm_direct reconciliation.
</content>
</invoke>
