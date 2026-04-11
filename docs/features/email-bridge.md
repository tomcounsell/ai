# Email Bridge

The email bridge adds IMAP/SMTP as a second transport alongside Telegram. Contacts can reach Valor via email; Valor replies to their email thread.

## Architecture

```
IMAP poll → parse → find_project_for_email() → enqueue_agent_session()
Worker executes → EmailOutputHandler.send() → SMTP reply → sender's inbox
```

The bridge is a standalone process (`python -m bridge.email_bridge`) that runs alongside the existing Telegram bridge. The worker is fully transport-agnostic — it pops sessions from Redis and routes output via the registered `OutputHandler` for that session's transport.

## Configuration

Add to `.env` (see `.env.example` for full template):

```bash
# IMAP (inbound)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=valor@yuda.me
IMAP_PASSWORD=your-gmail-app-password

# SMTP (outbound)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=valor@yuda.me
SMTP_PASSWORD=your-gmail-app-password

EMAIL_ADDRESS=valor@yuda.me
EMAIL_POLL_INTERVAL=30  # seconds, default
```

**Gmail App Password setup:** Google Account → Security → 2-Step Verification → App passwords. Name it "Valor Email Bridge".

## Contact Mapping

Add an `email.contacts` section to each project in `projects.json`:

```json
{
  "projects": {
    "my-project": {
      "email": {
        "contacts": {
          "alice@example.com": {"name": "Alice", "persona": "teammate"},
          "bob@corp.com": {"name": "Bob", "persona": "project-manager"}
        }
      }
    }
  }
}
```

Inbound emails from listed addresses are routed to the matching project. Unknown senders are discarded. Matching is exact (case-insensitive), no domain wildcards.

## Session IDs

Email sessions use `email_` prefix: `email_{project_key}_{sender}_{timestamp_ms}`.

The `transport` field is stored in `AgentSession.extra_context["transport"] = "email"`.

## Thread Continuation

Reply threads are tracked via Redis reverse mapping:

- Inbound: `Message-ID` header → stored as `email:msgid:{message_id} → session_id`
- Outbound reply: `In-Reply-To` header set from `extra_context["email_message_id"]`
- On next inbound reply: `In-Reply-To` looked up in Redis → resumes same session

TTL: 30 days.

## Dead Letter Queue

Failed SMTP sends (3 retries with exponential backoff) are persisted to Redis:

```
email:dead_letter:{session_id}  →  {recipient, subject, body, headers, failed_at, retry_count}
```

Manage via `valor-service.sh`:
```bash
./scripts/valor-service.sh email-dead-letter list
./scripts/valor-service.sh email-dead-letter replay <session_id>
./scripts/valor-service.sh email-dead-letter replay --all
```

Or directly: `python -m bridge.email_dead_letter list`

## Health Monitoring

On each successful IMAP poll, `email:last_poll_ts` is updated in Redis.

```bash
./scripts/valor-service.sh email-status
```

Reports:
- Process running / stopped
- Last IMAP poll age (warns if >5 minutes stale)

## Service Commands

```bash
./scripts/valor-service.sh email-start    # Start email bridge
./scripts/valor-service.sh email-stop     # Stop email bridge
./scripts/valor-service.sh email-restart  # Restart email bridge
./scripts/valor-service.sh email-status   # Status + last poll age
```

The email bridge runs as a background process (no launchd plist in v1 — manual start is sufficient for validation).

## Transport-Keyed Callbacks

The worker registers `EmailOutputHandler` per project under a composite `(project_key, "email")` key alongside the existing `TelegramRelayOutputHandler` under `project_key`. Lookup is:

1. Try `(project_key, transport)` — transport-specific handler
2. Fall back to `project_key` — default Telegram handler  
3. Fall back to `FileOutputHandler` — if nothing registered

This is backward compatible — existing Telegram callers pass no `transport=` argument and continue to work.

## Files

| File | Purpose |
|------|---------|
| `bridge/email_bridge.py` | IMAP poller, EmailOutputHandler, SMTP send |
| `bridge/email_dead_letter.py` | Dead letter queue management |
| `bridge/routing.py` | `find_project_for_email()`, `load_email_contacts()` |
| `agent/agent_session_queue.py` | Transport-keyed callback registration |
| `models/agent_session.py` | `transport` property on AgentSession |
| `worker/__main__.py` | EmailOutputHandler registration at startup |

## Scope Limits (v1)

- Single inbox: `valor@yuda.me`
- Text-only emails (no attachment handling)
- Plain text replies (no HTML composition)
- Exact sender match (no domain wildcards)
- No launchd plist (manual start)
- IMAP/SMTP auth only (no OAuth)
