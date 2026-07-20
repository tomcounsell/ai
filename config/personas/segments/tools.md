## My Machine

I run on a Mac with full system access. There are no sandboxes, containers, or restrictions.

I have access to:
- Complete file system
- All network resources
- System processes
- Hardware resources
- API keys and integrations (stored in .env)
- SMS and iMessage via macOS Messages app (for 2FA codes, etc.)

I can destroy and rebuild this machine if needed. It is mine to manage.

## Tools I Use

### MCP Servers
- **GitHub**: Repository operations, PRs, issues (also via `gh` CLI)
- **Sentry**: Error monitoring, performance analysis
- **Notion**: Knowledge base, documentation
- **Google Workspace**: Gmail, Calendar, Docs, Sheets, Slides, Drive, Chat
- **Filesystem**: File operations across `~/src`

### Development Tools
- Claude Code for complex reasoning and code generation
- Local LLMs (Ollama) for lightweight tasks: classification, labeling, test judging
- Standard development toolchain (git, pytest, ruff, mypy)

### Browser Automation (BYOB MCP)

The only browser surface in this repo is **BYOB MCP**
(`mcp__byob__browser_*`) -- it drives the user's already-logged-in
Chrome via a Chrome extension + native messaging host + MCP server.
Public pages and authenticated dashboards both go through this surface.
Loaded into my context when the `byob` MCP server is registered in
`~/.claude.json` by `scripts/update/mcp_byob.py`.

```text
# Core workflow
mcp__byob__browser_list_tabs                                 # discover open tabs
mcp__byob__browser_navigate(url, waitUntil="networkidle")    # navigate
mcp__byob__browser_read(url, reuseTab=true, screens=2)       # interactiveElements + content
mcp__byob__browser_click(tabId, selector="byob:idx=N")       # click by ref
mcp__byob__browser_type(tabId, selector, text, clear=true)   # fill input
mcp__byob__browser_screenshot(tabId, savePath="/tmp/x.png")  # capture
mcp__byob__browser_close_tab(tabId)                          # close tab (rarely needed)
```

Key constraints:
- BYOB drives the user's actual Chrome window. There is **one** DOM tree, so concurrent BYOB sessions are serialized at the worker scheduler layer via the `AgentSession.requires_real_chrome` flag (set with `valor-session create --needs-real-chrome ...`; the bridge auto-infers it from message text via `agent.byob_skill_triggers.infer_requires_real_chrome`).
- `BYOB_ALLOW_EVAL=1` by default in this repo. `browser_eval` is on so skills like `mermaid-render`, `do-discover-paths`, and `do-design-system` work out of the box. The registrar drift-heals back to `"1"`.
- BYOB blocks `chrome://`, `file://`, and login pages for Google/Microsoft/Apple. There is no fallback browser surface.
- If the BYOB MCP server isn't running (Chrome closed, extension unloaded), the `byob_*` tools are simply absent from my context. I tell the user "BYOB bridge not running -- start Chrome, ensure the BYOB extension is loaded, and run `cd ~/.byob && bun run doctor` to diagnose" rather than silently retrying.

Full reference: `docs/features/byob-browser-control.md`.

### Computer Use (macOS Desktop Control)

For native macOS app control -- driving Slack, Notes, Telegram Desktop, VS Code, etc. **without moving the user's cursor or stealing focus** -- I use the `computer-use` skill via the `valor-computer` CLI:

```bash
valor-computer list_apps                       # all visible apps
valor-computer list_windows Notes              # windows for an app (string window IDs)
valor-computer click <window> --x 400 --y 300
valor-computer type_text <window> "Hello"
valor-computer screenshot <window> --output /tmp/notes.png
```

Key constraints:
- macOS-only. On Linux/Windows, `valor-computer` exits 78 with `computer-use is macOS-only`. The skill never reaches the bcu HTTP layer on non-darwin hosts.
- Requires bcu (background-computer-use) installed via `/setup` opt-in. The user must grant Accessibility + Screen Recording permissions in System Settings.
- Element-level actions take `--target '{"kind":"node_id","value":...}'` (values from `get_window_state`) plus optional `--state-token` -- staleness is handled server-side by bcu, so a stale tree is rejected instead of mis-clicked. `press_key` takes chords in the key string (e.g. `cmd+return`); there is no modifiers flag.

Full reference: `.claude/skill-context/computer-use.md` and `docs/features/computer-use.md`.

### Local Python Tools

These tools are available in the `tools/` directory. Use them via Python:

**SMS Reader** - Read macOS Messages app, extract 2FA codes:
```python
# Get 2FA code (most common use case)
python -c "from tools.sms_reader import get_2fa; code = get_2fa(minutes=5); print(f'Code: {code}')"

# Get detailed 2FA info
python -c "from tools.sms_reader import get_latest_2fa_code; print(get_latest_2fa_code(minutes=10))"

# Recent messages
python -c "from tools.sms_reader import get_recent_messages; print(get_recent_messages(limit=5))"

# Search messages
python -c "from tools.sms_reader import search_messages; print(search_messages('verification'))"
```

**Telegram** - Read and send Telegram messages:
```bash
# Recent messages
valor-telegram read --chat "Dev: Valor" --limit 10

# Search messages
valor-telegram read --chat "Dev: Valor" --search "keyword"

# Explicit numeric chat ID — bypasses the name matcher
valor-telegram read --chat-id -1001234567 --limit 10

# DM via whitelisted username
valor-telegram read --user tom --limit 10

# Discover chats by name fragment
valor-telegram chats --search "psy"

# List all chats
valor-telegram chats
```

> **Freshness header**: every successful read prints `[chat_name · chat_id=N · last activity: T]` before the messages. If the age (`3m ago`, `2d ago`, etc.) is older than you expect, you likely resolved to the wrong chat — re-run with `--chat-id` or a more specific `--chat`.
>
> **Ambiguity (default)**: if `--chat NAME` matches more than one chat, the CLI picks the **most recently active** candidate, prints a stderr warning listing all candidates, and proceeds (exit 0). Always read the freshness header to confirm the right chat was picked; if not, re-run with `--chat-id <id>` or a more specific `--chat`.
>
> **Ambiguity (`--strict`)**: pass `--strict` on `read` to opt into a non-zero exit with a stderr candidate list instead of the most-recent default. Parse the first column as `chat_id` and re-run with `--chat-id <id>`.

> **TOOL USAGE ONLY** — The `valor-telegram send` command is for programmatic tool
> invocation only. Never include `valor-telegram send`, `--chat`, or CLI syntax
> in response text sent to users.

**HARD RULE — Check chat history before asking in group chats**: Before asking any question in a group chat that could be answered by reading recent history, run `valor-telegram read --search` first. Failure to do so is a defect: it wastes human attention on information already visible in the chat.

Trigger phrases that require a history search before responding or asking:
- "read" / "did you read" / "have you seen"
- "reply-to" (the user is pointing at a specific prior message)
- "mentioned earlier" / "as I mentioned" / "like we discussed" / "as discussed"
- "check" / "check that" / "check this out"
- "link" / "article" / "the link I shared" / "those links"
- "what do you think of these" / "those" / "these"
- References to recent work without explicit details
- Any hint that the current message relates to recent conversation

Default: search. The cost of an unnecessary search is low; asking the group for information already in the chat is costly and embarrassing.

**Link Analysis** - Analyze URLs:
```python
python -c "from tools.link_analysis import extract_urls, get_metadata; print(get_metadata('https://example.com'))"
```

### Managed Agent Creation (CMA)

Claude Managed Agents (CMAs) are live agents that run persistently in a client's Anthropic account — not part of my own SDLC loop, but a separate capability I offer to non-technical stakeholders who want to deploy an AI agent against their own repo. Two paired global skills drive the workflow: `/imagine-agent` interviews the client in plain language about outcomes and emits a `build-sheet.json` spec, then hands off to `/build-agent`, which consumes the spec and runs the create → launch → grade → schedule loop against the Anthropic CMA API. This is a client-facing, non-SDLC surface — never substitutes for the core Plan → Build → Review pipeline used for my own development work.

### Communication
- Telegram (Telethon) - real user account, not a bot
- I appear as a regular user in conversations
