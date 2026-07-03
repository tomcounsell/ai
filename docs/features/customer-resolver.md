# Customer Resolver — Dynamic Email to Customer ID Routing

## Overview

The customer resolver feature replaces the static email allow-list with a
dynamic per-project hook. When an inbound email arrives, the bridge calls
a configurable resolver to identify the sender as a known customer. The
resolver's outcome falls into one of three cases (issue #1817 A2):

1. **Known customer** — returns a `customer_id` string. The bridge spawns a
   `customer-service` persona session with that ID available in the system
   prompt and as the `CUSTOMER_ID` environment variable.
2. **Definitively not a customer** — the resolver ran to completion and
   returned `None`. The message is dropped cleanly (stays `\Seen`), no
   session is created.
3. **Resolver unavailable** — the resolver failed to run to completion
   (subprocess crash/timeout, malformed output, OAuth/gws error). This is
   NOT case 2: `resolve_customer()` raises `ResolverUnavailable` instead of
   returning `None`, so the caller never conflates an infrastructure outage
   with "not a customer." See "Fail-Closed Behavior" below.

This is used by projects like Cuttlefish where Valor acts as a customer
service agent: the resolver calls the Cuttlefish API to look up the sender,
and the spawned Claude Code session reads the customer profile via
Cuttlefish's own tools.

## Config Schema

Add a `customer_resolver` block to the project in `projects.json`:

```json
{
  "projects": {
    "my-project": {
      "email": {
        "contacts": { "customer@example.com": {} }
      },
      "customer_resolver": {
        "type": "subprocess",
        "command": ["uv", "run", "/path/to/resolver.py"],
        "timeout_seconds": 5,
        "cache_ttl_seconds": 300
      }
    }
  }
}
```

Or for a Python callable form:

```json
{
  "customer_resolver": {
    "type": "callable",
    "callable": "myapp.resolvers.resolve_customer_email",
    "cache_ttl_seconds": 300
  }
}
```

### Fields

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `type` | yes | — | `"subprocess"` or `"callable"` |
| `command` | subprocess only | — | Argv list (no shell expansion) |
| `callable` | callable only | — | Dotted Python path to a function |
| `timeout_seconds` | no | 5.0 | Hard timeout for subprocess dispatch |
| `cache_ttl_seconds` | no | 300 | TTL for Redis resolver result cache |

## Resolver Interface Contract

### Subprocess form

The resolver is invoked as a subprocess (argv, never shell). It receives the
sender email address as the first argument and must print a single-line
customer ID to stdout, then exit 0. Empty stdout means "not a customer".

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["my-crm-sdk"]
# ///

import sys
from my_crm import lookup_customer

sender = sys.argv[1]
customer = lookup_customer(sender)
if customer:
    print(customer.id)  # prints e.g. "cust_42" — no newline needed
# exit 0 in all cases; exit non-zero = resolver failure
```

Output sanitization rules:
- Single line only — multi-line output is treated as a resolver failure
  (increment failure counter, apply `valor-retry` label, raise `ResolverUnavailable`)
- Must match `[A-Za-z0-9_\-:.]{1,128}` after stripping (garbage output is
  also a resolver failure, same treatment as multi-line)
- Empty or whitespace-only = definitively not a customer, returns `None`
  (no failure counter increment — the resolver ran successfully)

### Callable form

The resolver must be a Python callable that accepts one positional argument
(the sender email address) and returns a customer ID string or None:

```python
def resolve_customer_email(email_from: str) -> str | None:
    customer = CRM.lookup(email_from)
    return customer.id if customer else None
```

## Caching Semantics

Results are cached in Redis under `customer_resolver:{project_key}:{sender}`:
- Cache hit with non-empty value: return the cached customer_id (no dispatch)
- Cache hit with empty string: return None (cached "not a customer")
- Cache miss: dispatch resolver, cache result with `cache_ttl_seconds` TTL

Call `invalidate_customer_cache(project_key, sender_email)` to force a
fresh resolver dispatch (e.g., when the CRM changes for a specific customer).

## Fail-Closed Behavior (issue #1817 A2)

On resolver failure (subprocess exits non-zero, timeout, or malformed output),
`resolve_customer()` raises `ResolverUnavailable` rather than returning `None`
— this is deliberately NOT the same code path as "not a customer":

1. `resolver:failures:{project_key}` Redis counter is incremented
2. Gmail `valor-retry` label is applied to the IMAP message (best-effort,
   skipped if IMAP context is unavailable — e.g., in unit tests)
3. `resolve_customer()` raises `ResolverUnavailable`

The caller (`bridge/email_bridge.py::_process_inbound_email`) catches
`ResolverUnavailable` specifically:

1. `logger.warning`s with the message id
2. Un-marks `\Seen` on the message (`_unmark_seen()`) so the next IMAP poll
   retries it — a resolver outage never permanently drops a customer email
3. Once `resolver:failures:{project_key}` crosses `EMAIL_RESOLVER_ALERT_AFTER`
   consecutive failures (default 3, env-overridable), arms the
   `email:resolver_unavailable` operator alert (`logger.critical`), surfaced
   on the dashboard's `email` health field — see
   [Email Bridge — Operator Alerts](email-bridge.md#operator-alerts-issue-1817)

Only a genuinely resolved "not a customer" (resolver ran successfully,
returned `None`) keeps the original stays-`\Seen`, dropped-cleanly behavior.

The `valor-retry` label preserves the message archive so a future retry
mechanism can find it. Search Gmail for `label:valor-retry` to see archived
failures. Check `redis-cli GET resolver:failures:{project_key}` for the
current failure count.

### Seen-Before-Resolver Race Window

The `\Seen` flag is applied before resolver dispatch in the IMAP polling
flow (`_fetch_unseen`, a concurrency guard against re-processing on
overlapping polls). If the process crashes between the original `\Seen` mark
and `_unmark_seen()`'s un-mark STORE, that one message stays stuck `\Seen`
without being un-marked for retry. This is a narrow, best-effort race window
— it does not defeat the `email:resolver_unavailable` alert, since that alert
is armed from the `resolver:failures:*` counter (a distinct outage-level
signal), not from any single message's retry outcome.

## Subject-Line Coalescing

For customer-resolver sessions, inbound emails with matching subjects are
coalesced into the same session ID to prevent parallel conflicting replies.

Coalescing precedence:
1. In-Reply-To header (existing behavior, unchanged)
2. Normalized subject + same `(project_key, customer_id)` within 48 hours
3. New session ID

Subject normalization strips: `Re:`, `Fwd:`, `Fw:`, `AW:`, `Antw:`,
numbered prefixes like `Re[3]:`, bracket ticket tags like `[ticket-123]`,
collapses whitespace, lowercases.

The 48-hour age bound (`COALESCE_MAX_AGE_SECONDS`) prevents stale sessions
from resurrecting. Every coalescing decision is logged at INFO:

```
[email] coalescing matched session=email_proj_cust42_111 age=2.1h limit=48h
```

## Customer-Service Persona

When a customer_id is resolved, the session uses the `customer-service`
persona. The persona file is resolved in order:
1. `~/Desktop/Valor/personas/customer-service.md` (private, iCloud-synced)
2. `config/personas/customer-service.md` (in-repo minimal fallback)

The persona file may contain `{customer_id}` as a placeholder — it is
substituted with the resolved customer ID before the session starts.

## CUSTOMER_ID Environment Variable

The resolved customer ID is injected as `CUSTOMER_ID` in the Claude Code
subprocess environment. Target-repo tools can read it without parsing the
system prompt:

```python
import os
customer_id = os.environ.get("CUSTOMER_ID")
```

## Single-Machine Operating Assumption

The customer resolver assumes one bridge machine polls any given Gmail
account at a time. This is enforced by `projects.<key>.machine` in
`projects.json` (each project is owned by exactly one machine). With
single-machine operation there are no concurrent-poll races on `\Seen`
marking or resolver cache stampedes.

## Monitoring

- **Failure counter**: `redis-cli GET resolver:failures:{project_key}`
  — incremented on every resolver error, deleted (along with any armed
  `email:resolver_unavailable` alert) on the next successful dispatch.
- **Persistent-outage alert**: `redis-cli GET email:resolver_unavailable`
  — armed once the failure counter crosses `EMAIL_RESOLVER_ALERT_AFTER`
  consecutive failures; value is `"{first_seen_ts}:{last_message_id}"`.
  Surfaced on the dashboard (`GET /health`, `GET /dashboard.json` —
  `health.email_alert == "resolver_unavailable"`). See
  [Email Bridge — Operator Alerts](email-bridge.md#operator-alerts-issue-1817).
- **Retry archive**: Gmail search `label:valor-retry` shows messages
  with resolver failures.
- **Bridge log**: `tail -f logs/bridge.log | grep resolver` shows all
  resolution events at INFO/WARNING/CRITICAL level.

## Followup Issues

- `resolver_health` reflection (planned)
- Active retry loop consuming `label:valor-retry` Gmail messages (planned)
- Pipe the dashboard `email:resolver_unavailable` / `email:auth_failed`
  alerts to a Telegram notification, not just the dashboard (planned)
- Cuttlefish-side resolver script, CLAUDE.md, and customer tools (Cuttlefish
  repo)

## Downstream

Once the resolver returns a `customer_id`, the
[Email CS Auto-Reply](email-cs-auto-reply.md) layer triages the inbound email
(two-tier classification + structural escalation gate) and decides whether to
auto-handle, draft for a human, or escalate — before any fallback `AgentSession`
is spawned. The resolver is the upstream gate: triage only runs for senders the
resolver identifies as customers.
