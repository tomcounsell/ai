# Email and Google Workspace Skills

Tracking issue: [#1615](https://github.com/tomcounsell/ai/issues/1615)

## What they are

Two globally-synced skills that steer agents toward the lightest available tool for email and Google Workspace tasks, preventing unnecessary BYOB browser automation.

- `/email` — guides the agent through the four-tier email tool ladder for any read/send request.
- `/google-workspace` — guides the agent through per-service tool selection for all ten Workspace services (Gmail, Calendar, Drive, Docs, Sheets, Slides, People, Chat, Forms, Keep).

Both skills live in `.claude/skills-global/` and are synced to `~/.claude/skills/` on every `/update` run, so agents in any repo (`cuttlefish`, `psyoptimal`, etc.) can reach them without project-specific configuration.

## When to reach for each

| Request type | Skill |
|---|---|
| Read inbox, search mail, send a reply, check threads | `/email` |
| Check calendar, create events, list attendees | `/google-workspace` |
| Create or read Docs / Sheets / Slides | `/google-workspace` |
| Browse Drive, copy or share files | `/google-workspace` |
| Read or reply to email AND perform Workspace tasks in the same session | `/email` for mail; `/google-workspace` for everything else |

For email specifically, `/email` is more precise — it puts `valor-email` (Redis-cached) ahead of `gws gmail`, giving the fastest path first.

## Tool hierarchy

### Email ladder (`/email`)

The `/email` skill enforces a four-tier priority order. Walk it top to bottom; fall through on **tool absence OR auth failure**:

| Tier | Tool | Notes |
|---|---|---|
| 1 | `valor-email` | Redis history cache; fastest; reads and sends via the email bridge relay |
| 2 | `gws gmail` | Google Workspace CLI; direct API; requires prior `gws auth` OAuth step |
| 3 | `mcp__claude_ai_Gmail__*` | Gmail MCP tools; interactive Claude sessions only |
| 4 | BYOB browser | Last resort only; never for simple read/send |

```bash
# Tier 1
valor-email read --limit 5
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"

# Tier 2 (fall through if valor-email is unavailable)
gws gmail users messages list --params '{"userId": "me", "maxResults": 5}'
gws gmail users messages get --params '{"userId": "me", "id": "MSG_ID"}'
```

The "never BYOB for simple read/send" rule is explicit: if an agent reaches for a browser to read the inbox, it must stop and restart from Tier 1.

### Workspace ladder (`/google-workspace`)

Each Workspace service has a two-tier priority order (three where MCP exists):

| Service | 1st: `gws` CLI | 2nd: MCP (where available) | 3rd |
|---|---|---|---|
| Gmail | `gws gmail` | `mcp__claude_ai_Gmail__*` | BYOB |
| Calendar | `gws calendar` | `mcp__claude_ai_Google_Calendar__*` | BYOB |
| Drive | `gws drive` | `mcp__claude_ai_Google_Drive__*` | BYOB |
| Docs | `gws docs` | (no MCP) | BYOB |
| Sheets | `gws sheets` | (no MCP) | BYOB |
| Slides | `gws slides` | (no MCP) | BYOB |
| People | `gws people` | (no MCP) | BYOB |
| Chat | `gws chat` | (no MCP) | BYOB |
| Forms | `gws forms` | (no MCP) | BYOB |
| Keep | `gws keep` | (no MCP) | BYOB |

The fall-through rule is the same: absent tool OR auth failure → next tier. A present-but-unauthenticated `gws` binary must not stall the agent — it hands off to MCP or BYOB and the agent continues.

## `gws` CLI: install and auth

`gws` is the official [Google Workspace CLI](https://github.com/googleworkspace/cli) (`@googleworkspace/cli` on npm), a Rust binary dynamically built from the Discovery Service API.

### Install (automatic via `/update`)

`@googleworkspace/cli` is a managed npm package in `scripts/update/npm_tools.py::MANAGED_PACKAGES`. Running `/update` installs it globally via npm if not already present. After install the `gws` binary is on PATH at `$(npm prefix -g)/bin/gws` (nvm-managed Node resolves this automatically).

```bash
# Verify presence after /update
which gws
gws --version
```

The `/update` verify check (`scripts/update/verify.py`) uses an **optional-style** gate — it surfaces `gws` version/status when the binary is present, and stays silent (no warning, exit 0) when absent. This mirrors the `sentry-cli` pattern: machines that never use Google Workspace are not nagged.

### Auth (one-time human step, surfaced by `/update`)

Installing the binary does not authenticate it. First use requires a human-completed OAuth flow:

```bash
gws auth setup   # provision a Google Cloud project and OAuth client
gws auth login   # browser-based consent; credentials stored in OS keyring
```

The OAuth *consent* itself cannot be automated (it requires clicking through a Google screen, and `gws auth setup` needs `gcloud` + a GCP project). But `/update` no longer treats this as an undocumented external footnote: the `gws_auth.py` step (`scripts/update/gws_auth.py`, run right after the `gh` auth step) **detects** the auth state on every run via `gws auth status` and surfaces the exact command to run when the binary is installed but unauthenticated — appending it to the run's warnings so the human sees it at the end of `/update`. It is detection-only and cron-safe: it never opens a browser or blocks a non-interactive (launchd) run, and stays silent once authenticated (`auth_method != "none"` → idempotent skip).

After auth, credentials are encrypted at rest in the OS keyring (or `~/.config/gws/.encryption_key`). The agent never sees or stores Google credentials.

**Important:** the `gws` entry in `CLAUDE.md` does **not** say "Pre-authenticated" — that claim was removed in this upgrade. First use always needs the OAuth step above.

## Global sync

Both skills are `user-invocable: true` and live in `.claude/skills-global/`. The hardlink sync in `scripts/update/hardlinks.py` propagates them to `~/.claude/skills/` on every `/update` run, making them available to agents in every repo on the machine.

The `google-workspace` skill was previously project-only (`.claude/skills/`) with `user-invocable: false`. This upgrade moved it to `skills-global/` and flipped the flag, so it now resolves in non-`ai` repos.

The `/email` skill is new as of this upgrade — no equivalent existed before.

## Draft-first rule (email composition)

When composing or replying to email on the user's behalf, the `/google-workspace` skill enforces three hard constraints regardless of which tool tier is used:

1. **Draft-first** — always produce a draft that the user reviews and sends manually; never call a send tool without an explicit send instruction.
2. **Async CTAs only** — never offer calls, meetings, or synchronous communication; async alternatives only.
3. **Honest representation** — represent the product or operation accurately; do not overclaim automation as human-run.

The `/email` skill echoes the draft-first rule: "prefer a draft the user reviews unless explicitly told to send."

## Related files

- `.claude/skills-global/email/SKILL.md` — `/email` skill (four-tier ladder, fall-through rules)
- `.claude/skills-global/google-workspace/SKILL.md` — `/google-workspace` skill (per-service table, behavioral guidance)
- `scripts/update/npm_tools.py` — `MANAGED_PACKAGES` entry for `@googleworkspace/cli`
- `scripts/update/verify.py` — optional-style `gws` presence check in `check_system_tools`
- `scripts/update/gws_auth.py` — detects `gws` auth state and surfaces the one-time OAuth step during `/update`
- `scripts/update/hardlinks.py` — hardlink sync that propagates both skills globally
- `docs/plans/email-google-workspace-skill-upgrade.md` — full plan with research, critiques, and acceptance criteria
- `docs/features/skills-global.md` — global skills library overview
- `docs/features/email-bridge.md` — `valor-email` CLI and the underlying email bridge
