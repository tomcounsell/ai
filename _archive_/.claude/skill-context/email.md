# email context — this repo (ai)

This repo provides a fast, Redis-cached email CLI — **`valor-email`** — that sits *above* the
generic tool ladder in the global `email` skill. When present, prefer it as **Tier 1** (try it
first), falling through to the generic Tier 2 (`gws gmail`) / Tier 3 (Gmail MCP) / Tier 4 (BYOB)
ladder on absence OR failure exactly as the skill body describes.

## Tier 1 — `valor-email` (preferred: Redis-cached, fastest)

The project email CLI. Reads hit a Redis history cache (fast); sends queue via the email relay.
Use this first whenever it is on PATH.

```bash
valor-email read --limit 5
valor-email read --search "deployment" --since "2 hours ago"
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
```

Fall through to the generic Tier 2 (`gws gmail`) if `valor-email` is not on PATH, or a
read/send fails because the bridge/relay is unreachable.

## Draft / reply specifics

- `valor-email send --to a@x.com --to b@y.com "..."` — repeat `--to` per recipient (also accepts comma-separated).
- `valor-email send --reply-to "<message-id>" "..."` — reply to a specific message; get the `message_id` from `valor-email read --json`.
- `valor-email draft ...` — create a real Gmail draft (visible in the Drafts folder) for human review before sending.

## Delivery troubleshooting

Sends queue via `email:outbox:*` and the relay drains them over SMTP with retry + DLQ, so a
successful `valor-email send` confirms *queueing*, not delivery. If delivery seems stuck, check
`./scripts/valor-service.sh email-status` (also reads the relay heartbeat under
`email:relay:last_poll_ts`).

See `~/src/ai/docs/features/email-bridge.md` for the full bridge/relay design.
