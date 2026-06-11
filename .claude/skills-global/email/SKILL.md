---
name: email
description: "Use when reading or sending email — checking the inbox, searching mail, reading a specific message, drafting or sending a reply. Triggered by 'read my email', 'check my inbox', 'send an email', 'reply to that email', 'search my mail', or any request to read/send mail."
allowed-tools: Bash
user-invocable: true
---

# Email

Reach for the lightest tool that does the job. Walk the four-tier ladder below
top to bottom, trying each tier first and falling through to the next ONLY on
tool absence OR auth failure. **Never use BYOB browser automation for a simple
read or send** — it is slow, flaky, and burns browser context. BYOB is a last
resort for tasks no CLI/MCP path can do at all.

## Tool Ladder (priority order)

### Tier 1 — `valor-email` (preferred: Redis-cached, fastest)

The project email CLI. Reads hit a Redis history cache (fast); sends queue via
the email relay. Use this first whenever it is on PATH.

```bash
valor-email read --limit 5
valor-email read --search "deployment" --since "2 hours ago"
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
```

Fall through to Tier 2 if `valor-email` is not on PATH, or a read/send fails
because the bridge/relay is unreachable.

### Tier 2 — `gws gmail` (Google Workspace CLI, direct API)

Google's official Workspace CLI. On PATH after `npm install -g
@googleworkspace/cli`. Requires a one-time human `gws auth setup` / `gws auth
login` OAuth step — if a call fails with an auth error, fall through.

```bash
gws gmail users messages list --params '{"userId": "me", "maxResults": 5}'
gws gmail users messages get --params '{"userId": "me", "id": "MSG_ID"}'
```

Fall through to Tier 3 if `gws` is not on PATH OR every call errors with an
authentication failure (binary present but unauthenticated).

### Tier 3 — Gmail MCP (`mcp__claude_ai_Gmail__*`, interactive sessions only)

The registered Gmail MCP tools. Available only in interactive Claude sessions,
not in headless/agent runs. Use for read and draft-first composition.

```text
mcp__claude_ai_Gmail__search_threads   (search the inbox)
mcp__claude_ai_Gmail__get_thread       (read a full thread)
mcp__claude_ai_Gmail__create_draft     (draft a reply — never auto-send)
```

Fall through to Tier 4 if the MCP tools are not available in this session.

### Tier 4 — BYOB browser automation (LAST RESORT)

Only when no tier above can reach the mailbox at all (e.g. a webmail provider
with no CLI/MCP path). **Never** use BYOB for simple read or send when any tier
above is available.

## Rules

- **Fall through on absence OR auth failure**, not just absence. A present-but-
  unauthenticated `gws` must hand off to the next tier — do not stall on it.
- **Never use BYOB for a simple read or send.** If you find yourself opening a
  browser to read the inbox, stop and re-walk the ladder from Tier 1.
- **Draft-first for composition.** When sending on the user's behalf, prefer a
  draft the user reviews unless explicitly told to send.
