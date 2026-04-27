# Email Bridge

Email as a second transport alongside Telegram. Inbound emails are routed to agent sessions; outbound replies are delivered via SMTP with proper threading.

## Architecture

```
IMAP inbox (valor@yuda.me)
  → bridge/email_bridge.py (polls every 30s)
    → get_known_email_search_terms()  # bridge/routing.py — builds UNSEEN+FROM query
    → _poll_imap(known_senders)       # fetches only matching messages; marks SEEN immediately
    → find_project_for_email()        # bridge/routing.py
    → enqueue_agent_session()         # transport="email"
      → Worker resolves EmailOutputHandler via (project_key, "email") callback
        → EmailOutputHandler.send()   # bridge/email_bridge.py
          → SMTP reply-all: sender + original To/CC recipients, In-Reply-To header
```

### Key Modules

| File | Role |
|------|------|
| `bridge/email_bridge.py` | IMAP polling loop, email parsing (`parse_email_message` returns `from_addr`, `to_addrs`, `cc_addrs`, `subject`, `body`, `message_id`, `in_reply_to`), sender filtering, `EmailOutputHandler` (reply-all by default), history cache write-through (`_record_history`, `_record_thread`), module-level `_build_reply_mime` with attachment support |
| `bridge/email_relay.py` | Async drain of `email:outbox:*` payloads via SMTP; atomic LPOP + requeue-with-counter + DLQ after `MAX_EMAIL_RELAY_RETRIES`; heartbeat key `email:relay:last_poll_ts` for liveness probing. Runs inside `run_email_bridge()` via `asyncio.gather`. |
| `bridge/email_dead_letter.py` | Dead letter queue for failed SMTP sends |
| `bridge/routing.py` | `find_project_for_email()`, `build_email_to_project_map()`, `get_known_email_search_terms()` |
| `tools/valor_email.py` | CLI entry point (`valor-email read / send / threads`) |
| `tools/email_history/` | Pure-Redis readers for the history cache (`get_recent_emails`, `search_history`, `list_threads`) |
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

### Sender Filtering

The IMAP poller never fetches messages from unknown senders. Before each poll cycle, `get_known_email_search_terms()` (in `bridge/routing.py`) returns a list of search terms derived from all active projects' `email.contacts` and `email.domains`:

- Exact addresses from `email.contacts` (e.g. `"tom@yuda.me"`)
- Domain tokens from `email.domains` (e.g. `"@psyoptimal.com"`)

`_build_imap_sender_query()` assembles these into an IMAP OR tree, and `_poll_imap()` issues a single `UNSEEN FROM ...` UID search. Messages from senders not in any project config are never fetched — they remain `UNSEEN` in the inbox so a machine that *does* own the relevant project (now or later) can still pick them up.

**Single-machine ownership.** Each `email.contacts` entry and each `email.domains` wildcard must resolve to exactly one machine across the full config — enforced by the validator (`bridge/config_validation.py::validate_email_routing`) and gated by the update script. The cross-shape check also fails the gate when one machine owns an explicit address (`alice@psy.com`) while a different machine owns the matching domain wildcard (`psy.com`); without it, both bridges would race on the same incoming email. See [Single-Machine Ownership](single-machine-ownership.md).

Within a machine, messages that match are marked `SEEN` immediately on fetch (before parsing) to prevent duplicate processing on concurrent polls on the same machine.

### Transport-Keyed Callbacks

`AgentSession` callbacks are keyed by `(project_key, transport)`. The worker resolves the correct `OutputHandler` by looking up `(project_key, "email")` instead of the Telegram default. This keeps email and Telegram sessions fully isolated with no cross-contamination of delivery channels.

### Reply-All Behavior

`EmailOutputHandler.send()` builds the outbound recipient list by combining the original sender address with every address that appeared in the inbound email's `To` and `CC` headers — excluding the system's own `SMTP_USER` address to avoid self-copying. The result is passed as a `list[str]` to `_build_reply_mime()` and on to `_send_smtp()`.

The inbound pipeline stores the raw `To` and `CC` address lists in `extra_context_overrides` as `email_to_addrs` and `email_cc_addrs` (both `list[str]`). `send()` reads these back when composing each reply.

### Outbound Drafting (medium="email")

`EmailOutputHandler.send()` routes every outbound reply through `bridge.message_drafter.draft_message(text, session=session, medium="email")` before the body is wrapped as MIME. This is the same plumbing the Telegram handler uses — the drafter is the single wire-format compliance point for all agent output (see [message-drafter.md](message-drafter.md)).

Per-medium rules for email today:

- **Plain prose, no markdown on the wire.** Future validator work will reject markdown tables, headings, and fenced code blocks for `medium="email"` (staged in the message-drafter plan follow-ups).
- **No HTML / multipart bodies.** `text/plain` MIME only — see the drafter's No-Gos.
- **Reactions are no-ops.** `EmailOutputHandler.react()` returns early (there is no emoji-reaction analog for SMTP).

The drafter runs on every email send — there is no feature flag.

**Fail-open.** If `draft_message` raises, the handler falls back to the raw text — email delivery is never blocked by a drafter failure.

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

**IMAP-level sender filtering.** The poller constructs an `UNSEEN FROM` query using only the configured senders for active projects (via `get_known_email_search_terms()`). Unknown senders are never fetched and remain `UNSEEN`, so other machines polling the same shared inbox are not blocked. This is preferable to fetching-then-discarding, which would mark unrecognised messages as read.

**`telegram_message_id=0` sentinel.** The session queue requires a message ID for deduplication. Email sessions use `0` as a sentinel value since they have no Telegram message ID. This avoids a nullable field or a parallel code path in the queue.

**Transport stored in `extra_context`.** `extra_context["transport"] = "email"` is the discriminator the worker uses to select `EmailOutputHandler` over `TelegramRelayOutputHandler`. The same mechanism supports future transports (e.g. Slack) without changes to the core queue.

**Per-poll batch cap (`IMAP_MAX_BATCH`).** Each poll cycle fetches at most `IMAP_MAX_BATCH` unseen messages (default 20, configurable via env var). On inboxes with thousands of unread messages, this prevents the poller from hanging indefinitely on a single cycle. The most recent messages are fetched first.

**30-second poll interval.** A balance between responsiveness and IMAP connection overhead. Gmail supports IMAP IDLE for push delivery, but polling is simpler and sufficient for the current load.

## CLI (`valor-email`)

A terminal-friendly surface over the email bridge that mirrors `valor-telegram`:

```
valor-email read --limit 5
valor-email read --search "deployment" --since "2 hours ago"
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
valor-email send --to alice@example.com --to bob@example.com "CC both"
valor-email send --to alice@example.com --file ./report.pdf "See attached"
valor-email send --to alice@example.com --reply-to "<abc@host>" "Body"
valor-email threads
```

`--to` accepts multiple flags (repeat for each recipient) and comma-separated values (`--to "alice@example.com,bob@example.com"`). All three subcommands accept `--json` for machine-readable output.

### Read path

`valor-email read` queries the Redis history cache (`email:history:INBOX` sorted set keyed by UNIX timestamp, with per-message JSON blobs under `email:history:msg:{message_id}`, TTL 7 days, capped at 500 entries). The cache is populated by the IMAP poll loop via `_record_history()` / `_record_thread()` write-through calls. Filters:

- `--limit N` (default 10) — ZREVRANGE the N most recent entries.
- `--since "1 hour ago"` — ZRANGEBYSCORE floor; reuses `parse_since` from `tools.valor_telegram`.
- `--search "term"` — case-insensitive substring match over subject + body.

On cache miss (e.g. daemon hasn't populated the cache yet), the CLI opens a **read-only** IMAP session filtered by known senders (from `get_known_email_search_terms()`) so cross-machine SEEN semantics are preserved and no messages are leaked from another project's policy boundary.

### Send path — always via the relay

Sends write the **unified outbox payload** to `email:outbox:{session_id}` with a 1-hour TTL:

```json
{
  "session_id": "cli-1745200000-12345-a1b2c3d4",
  "to": ["alice@example.com", "bob@example.com"],
  "subject": "Re: Deploy",
  "body": "Looks good",
  "attachments": ["/abs/path/to/report.pdf"],
  "in_reply_to": "<abc@host>",
  "references": "<abc@host>",
  "from_addr": "valor@yuda.me",
  "timestamp": 1745200000.42
}
```

The `session_id` format (`cli-{secs}-{pid}-{token_hex(4)}`) gives 32 bits of per-call randomness so concurrent invocations in the same second collide effectively never. `tools/send_message.py::_send_via_email` emits the same shape so the relay has a single contract to drain. The `"to"` field is canonically `list[str]`; the relay's `_normalize_payload()` also accepts a single comma-separated string and splits it into `list[str]`.

The relay (`bridge/email_relay.py`) polls `email:outbox:*` every 100 ms. For each key it performs atomic `LPOP`, builds the MIME message via `_build_reply_mime()` (switching to `MIMEMultipart` when attachments are present), and dispatches over SMTP in `asyncio.to_thread`. On failure it increments `_relay_attempts`, `RPUSH`es back, and DLQs via `bridge.email_dead_letter.write_dead_letter()` after `MAX_EMAIL_RELAY_RETRIES` (default 3) attempts. The relay writes `email:relay:last_poll_ts` once per cycle (5-minute TTL) for operator liveness probes.

**`EmailOutputHandler.send()` does NOT write to the outbox** — it sends directly from the worker. The relay and the handler do not race on the same session's output (Risk 3 in the plan).

### Threads

`valor-email threads` reads the `email:threads` hash maintained by `_record_thread()`. Each inbound message contributes to its chain head (approximated by following `In-Reply-To` one link); the hash stores `{root_msgid: {subject, message_count, last_ts, participants}}`. Drift is accepted for v1 — the hash is a best-effort navigation aid, not a source of truth.

### Dead-letter surface

When the relay DLQs a CLI-originated send (after 3 failed SMTP attempts or on malformed payload), the user has no direct feedback path — the CLI already exited after enqueue. Inspect via:

```
./scripts/valor-service.sh email-dead-letter list
./scripts/valor-service.sh email-dead-letter replay --all
```

## See Also

- [Message Drafter](message-drafter.md) — the medium-aware drafting layer that owns wire-format compliance for outbound email (and Telegram)
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — how the worker resolves output handlers
- [Worker Service](worker-service.md) — standalone worker details
- [Deployment](deployment.md) — email bridge setup in the service topology
