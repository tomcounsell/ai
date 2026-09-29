# email context — this repo (ai)

## Tier 1 — `valor-email`

This repo's mail CLI: reads hit a Redis history cache, sends queue through the email relay. Use it first whenever it is on PATH; fall through to `gws gmail` if it is missing or the bridge/relay is unreachable. `valor-email --help` covers the flags.

```bash
valor-email read --limit 5
valor-email read --search "deployment" --since "2 hours ago"
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
valor-email send --reply-to "<message-id>" "..."   # message_id from `valor-email read --json`
valor-email draft ...                              # real Gmail draft for human review
```

Repeat `--to` per recipient (comma-separated also works).

## Delivery

A successful `valor-email send` confirms queueing (`email:outbox:*`), not delivery; the relay drains over SMTP with retry and a DLQ. If delivery seems stuck, run `./scripts/valor-service.sh email-status` (also reads the relay heartbeat `email:relay:last_poll_ts`). Design: `~/src/ai/docs/features/email-bridge.md`.
