# Email Bridge

Email as a second transport alongside Telegram. Inbound emails are routed to agent sessions; outbound replies are delivered via SMTP with proper threading.

## Architecture

```
IMAP inbox (valor@yuda.me)
  → bridge/email_bridge.py (polls every 30s)
    → find_project_for_email()   # bridge/routing.py
    → enqueue_agent_session()    # transport="email"
      → Worker resolves EmailOutputHandler via (project_key, "email") callback
        → EmailOutputHandler.send()  # bridge/email_bridge.py
          → SMTP reply with In-Reply-To header
```

### Key Modules

| File | Role |
|------|------|
| `bridge/email_bridge.py` | IMAP polling loop, email parsing, `EmailOutputHandler` |
| `bridge/email_dead_letter.py` | Dead letter queue for failed SMTP sends |
| `bridge/routing.py` | `find_project_for_email()`, `build_email_to_project_map()` |
| `agent/agent_session_queue.py` | Transport-keyed callbacks via `register_callbacks(transport=...)` and `_resolve_callbacks()`; `extra_context_overrides` parameter |

### Session Identity

Email sessions use an `email_` prefix for their session IDs:

```
email_{project_key}_{normalized_sender}_{unix_timestamp}
# e.g. email_myproject_alice_at_example_com_1744000000
```

`telegram_message_id=0` is used as a sentinel (email sessions have no Telegram message ID).

### Thread Continuation

When an inbound email carries an `In-Reply-To` header, the bridge looks up `email:msgid:{message_id}` in Redis to find the existing `session_id`. Replies resume the original session rather than starting a new one.

Each outbound SMTP send records the sent `Message-ID` in Redis so future replies can be correlated.

### Transport-Keyed Callbacks

`AgentSession` callbacks are keyed by `(project_key, transport)`. The worker resolves the correct `OutputHandler` by looking up `(project_key, "email")` instead of the Telegram default. This keeps email and Telegram sessions fully isolated with no cross-contamination of delivery channels.

### Worker Registration

At startup, `worker/__main__.py` registers `EmailOutputHandler` for each project that has **any** email routing configured — either `email.contacts` or `email.domains` (or both). The gate condition is:

```python
def _should_register_email_handler(project_cfg: dict) -> bool:
    email_cfg = project_cfg.get("email", {}) or {}
    return bool(email_cfg.get("contacts") or email_cfg.get("domains"))
```

This matches the inbound routing logic in `bridge/routing.py`, which builds both a contact address map (`EMAIL_TO_PROJECT`) and a domain map (`EMAIL_DOMAIN_TO_PROJECT`). A project must have `EmailOutputHandler` registered for either routing strategy to produce outbound SMTP replies — without it, the worker falls back to `FileOutputHandler` and silently discards the reply to a log file.

## Configuration

### projects.json

Two routing strategies are supported: contact-based (exact-match) and domain-based (wildcard). A project can use either or both.

**Contact-based routing** — only senders explicitly listed in `email.contacts` are routed:

```json
{
  "projects": {
    "my-project": {
      "email": {
        "contacts": {
          "alice@example.com": {"name": "Alice", "persona": "teammate"},
          "bob@example.com":   {"name": "Bob",   "persona": "teammate"}
        }
      }
    }
  }
}
```

**Domain-based routing** — all senders from a domain are routed to the project:

```json
{
  "projects": {
    "psyoptimal": {
      "email": {
        "domains": ["psyoptimal.com"]
      }
    }
  }
}
```

Any sender `@psyoptimal.com` is matched to the `psyoptimal` project, regardless of the specific address.

**Combined** — a project can have both; exact-match (`contacts`) takes priority over domain fallback:

```json
{
  "projects": {
    "my-project": {
      "email": {
        "contacts": {
          "alice@example.com": {"name": "Alice", "persona": "teammate"}
        },
        "domains": ["example.com"]
      }
    }
  }
}
```

Unrecognized senders (no contact match and no domain match) are ignored.

### Environment Variables

```bash
# IMAP (inbound)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_USER=valor@yuda.me
IMAP_PASSWORD=<gmail-app-password>
IMAP_MAX_BATCH=20        # max unseen messages fetched per poll cycle (default: 20)

# SMTP (outbound)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=valor@yuda.me
SMTP_PASSWORD=<gmail-app-password>
```

See `.env.example` for the full list with comments.

For Gmail, generate an App Password at <https://myaccount.google.com/apppasswords> (requires 2FA enabled).

## Operations

```bash
# Lifecycle
./scripts/valor-service.sh email-start
./scripts/valor-service.sh email-stop
./scripts/valor-service.sh email-restart

# Status (shows last poll age and warns if > 5 minutes stale)
./scripts/valor-service.sh email-status

# Dead letter queue
./scripts/valor-service.sh email-dead-letter list
./scripts/valor-service.sh email-dead-letter replay --all
```

## Health Monitoring

After each successful IMAP poll, the bridge writes the current timestamp to:

```
Redis key: email:last_poll_ts
```

`email-status` reads this key and warns if the last poll is older than 5 minutes, indicating the IMAP poller has stalled or crashed.

## Dead Letter Queue

When an SMTP send fails, the message is written to a dead letter entry in Redis:

```
Redis key: email:dead_letter:{session_id}
```

Inspect and replay via CLI:

```bash
python -m bridge.email_dead_letter list          # view all dead-lettered messages
python -m bridge.email_dead_letter replay --all  # retry all failed sends
```

Or via the service script:

```bash
./scripts/valor-service.sh email-dead-letter list
./scripts/valor-service.sh email-dead-letter replay --all
```

## Design Decisions

**stdlib only.** `imaplib`, `smtplib`, and `email` from the Python standard library — no third-party dependencies. This keeps the email bridge installable anywhere Python runs.

**Single inbox, sender-based routing.** All projects share one inbox (`valor@yuda.me`). The sender address determines which project the message is routed to — either by exact contact match or by domain wildcard. This avoids per-project mailboxes while keeping routing deterministic.

**`telegram_message_id=0` sentinel.** The session queue requires a message ID for deduplication. Email sessions use `0` as a sentinel value since they have no Telegram message ID. This avoids a nullable field or a parallel code path in the queue.

**Transport stored in `extra_context`.** `extra_context["transport"] = "email"` is the discriminator the worker uses to select `EmailOutputHandler` over `TelegramRelayOutputHandler`. The same mechanism supports future transports (e.g. Slack) without changes to the core queue.

**Per-poll batch cap (`IMAP_MAX_BATCH`).** Each poll cycle fetches at most `IMAP_MAX_BATCH` unseen messages (default 20, configurable via env var). On inboxes with thousands of unread messages, this prevents the poller from hanging indefinitely on a single cycle. The most recent messages are fetched first.

**30-second poll interval.** A balance between responsiveness and IMAP connection overhead. Gmail supports IMAP IDLE for push delivery, but polling is simpler and sufficient for the current load.

## See Also

- [Bridge/Worker Architecture](bridge-worker-architecture.md) — how the worker resolves output handlers
- [Worker Service](worker-service.md) — standalone worker details
- [Deployment](deployment.md) — email bridge setup in the service topology
