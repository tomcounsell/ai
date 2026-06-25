# Customer Resolver — Dynamic Email to Customer ID Routing

## Overview

The customer resolver feature replaces the static email allow-list with a
dynamic per-project hook. When an inbound email arrives, the bridge calls
a configurable resolver to identify the sender as a known customer. If the
resolver returns a customer ID, the bridge spawns a `customer-service` persona
session with that ID available in the system prompt and as the `CUSTOMER_ID`
environment variable. If the resolver returns None, the message is dropped
cleanly with no session created.

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
  (increment failure counter, apply `valor-retry` label, return None)
- Must match `[A-Za-z0-9_\-:.]{1,128}` after stripping
- Empty or whitespace-only = not a customer (no failure counter increment)

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

## Fail-Closed Behavior

On resolver failure (subprocess exits non-zero, timeout, or malformed output):

1. `resolver:failures:{project_key}` Redis counter is incremented
2. Gmail `valor-retry` label is applied to the IMAP message (best-effort,
   skipped if IMAP context is unavailable — e.g., in unit tests)
3. The function returns None: no session is created, message stays `\Seen`

The `valor-retry` label preserves the message archive so a future retry
mechanism can find it. Search Gmail for `label:valor-retry` to see archived
failures. Check `redis-cli GET resolver:failures:{project_key}` for the
current failure count.

### Known Failure Mode (Seen-Before-Resolver)

The `\Seen` flag is applied before resolver dispatch in the current IMAP
polling flow. If the process crashes between `\Seen` and the resolver failure
label-STORE, the message is marked Seen but not labeled `valor-retry`. The
`resolver:failures:*` counter detects sustained failure even in this case.
This gap will be addressed in a future watchdog feature.

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
  — incremented on every resolver error, deleted on success.
- **Retry archive**: Gmail search `label:valor-retry` shows messages
  with resolver failures.
- **Bridge log**: `tail -f logs/bridge.log | grep resolver` shows all
  resolution events at INFO/WARNING level.

## Followup Issues

- Email bridge watchdog with Telegram alert when `resolver:failures:*`
  exceeds threshold (planned)
- `resolver_health` reflection (planned)
- Active retry loop consuming `label:valor-retry` Gmail messages (planned)
- Cuttlefish-side resolver script, CLAUDE.md, and customer tools (Cuttlefish
  repo)

## Downstream

Once the resolver returns a `customer_id`, the
[Email CS Auto-Reply](email-cs-auto-reply.md) layer triages the inbound email
(two-tier classification + structural escalation gate) and decides whether to
auto-handle, draft for a human, or escalate — before any fallback `AgentSession`
is spawned. The resolver is the upstream gate: triage only runs for senders the
resolver identifies as customers.
