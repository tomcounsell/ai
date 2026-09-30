---
name: email
description: "Use when reading, searching, drafting, or sending email. Triggered by 'read my email', 'check my inbox', 'send an email', 'reply to that email', 'search my mail', or any request to read/send mail."
allowed-tools: Bash
user-invocable: true
---

# Email

Reach for the lightest tool that does the job. Walk the tool ladder below
top to bottom, trying each tier first and falling through to the next ONLY on
tool absence OR auth failure.

## Repo Context Probe

If `.claude/skill-context/email.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares a faster project-local mail CLI (e.g. a Redis-cached mail CLI) to try as **Tier 1**, above the generic ladder. When the file is absent (the common case in a foreign repo), start the ladder at Tier 2 (`gws gmail`) — the generic tiers below need nothing beyond a Google Workspace login or an interactive MCP session.

## Tool Ladder (priority order)

### Tier 1 — project mail CLI (only if the context file declares one)

If the context file declares a fast project email CLI, try it first for both read and send.
Fall through to Tier 2 if it is not on PATH, or a read/send fails because its backing
service is unreachable. If no context file is present, skip this tier entirely.

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
with no CLI/MCP path). BYOB is slow, flaky, and burns browser context.

## Rules

- **Fall through on absence OR auth failure**, not just absence. A present-but-
  unauthenticated `gws` must hand off to the next tier — do not stall on it.
- **Never use BYOB for a simple read or send.** If you find yourself opening a
  browser to read the inbox, stop and re-walk the ladder from the top.
- **Draft-first for composition.** When sending on the user's behalf, prefer a
  draft the user reviews unless explicitly told to send.
- **De-slop gate before anything leaves.** Any composed email to an external
  recipient — send or finalized draft — must first PASS `Skill('de-slop')`,
  invoked as a fresh-context review of the draft text only (never the drafting
  conversation). On BLOCK, revise per the diagnosis and re-run; after 2 BLOCKs,
  surface to the user instead of sending. Skip the gate only for trivial
  logistical one-liners ("confirmed, see you at 3").
