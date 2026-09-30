# google-workspace context — this repo (ai)

This repo provides a fast, Redis-cached email CLI — **`valor-email`** — and a dedicated `/email`
skill whose ladder puts it *above* the generic `gws gmail` mail tier. The global
`google-workspace` skill body runs a generic baseline (start the mail ladder at `gws gmail`);
this file supplies the project-local Tier 1.

## Mail: prefer the `/email` skill's `valor-email` Tier 1

For reading or sending mail, prefer the `/email` skill, whose ladder is:

```
valor-email (Redis-cached, fastest)  →  gws gmail  →  Gmail MCP (mcp__claude_ai_Gmail__*)  →  BYOB
```

`valor-email` reads hit a Redis history cache and sends queue via the email relay, so it is
faster and more reliable than `gws gmail` when the bridge/relay is up. Fall through to the
generic `gws gmail` tier if `valor-email` is not on PATH or its backing service is unreachable.

```bash
valor-email read --limit 5
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
```

See `~/src/ai/docs/features/email-bridge.md` for the full bridge/relay design. All non-mail
Google Workspace services (Calendar, Drive, Docs, Sheets, Slides, People, Chat) use the generic
`gws` → MCP → BYOB ladder in the skill body unchanged.
