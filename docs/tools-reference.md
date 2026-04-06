# Tools Reference

Complete documentation of all tools available in the Valor system.

## MCP Server Tools

Individual operations that can be composed into larger workflows:

| Tool | Purpose | Configuration |
|------|---------|---------------|
| **Sentry** | Error monitoring, performance analysis | `.mcp.json` |
| **GitHub** | Repository operations, PRs, issues | `gh` CLI (pre-authenticated) |

## Local Python Tools

Located in `tools/` directory, use via Python imports.

### SMS Reader (`tools.sms_reader`)

Read macOS Messages app, extract 2FA codes.

```bash
# Get recent 2FA codes
python -c "from tools.sms_reader import get_2fa; print(get_2fa(minutes=5))"

# CLI interface
python -m tools.sms_reader.cli recent --limit 5
```

### Telegram History (`tools.telegram_history`)

Search stored message history from Telegram conversations.

```python
from tools.telegram_history import search_messages

results = search_messages(query="deployment", limit=10)
```

### Memory Search (`tools.memory_search`)

Search, save, inspect, and forget memories from the Memory model.

```python
from tools.memory_search import search, save, inspect, forget

results = search("deploy patterns", project_key="dm", limit=5)
saved = save("API X requires auth header Y", importance=6.0)
details = inspect(memory_id="abc123")
deleted = forget("abc123")
```

```bash
# CLI
python -m tools.memory_search search "deploy patterns"
python -m tools.memory_search save "important note" --importance 6.0
python -m tools.memory_search inspect --stats --project dm
python -m tools.memory_search forget --id abc123 --confirm
```

### Code Impact Finder (`tools.code_impact_finder`)

Semantic search for code, configs, and docs coupled to a proposed change. Two-stage pipeline: embedding recall + Claude Haiku reranking. Used during `/do-plan` Phase 1 for blast radius analysis.

```bash
# CLI: find code affected by a change
.venv/bin/python -m tools.code_impact_finder "change session ID derivation"

# CLI: check index status
.venv/bin/python -m tools.code_impact_finder --status
```

```python
from tools.code_impact_finder import find_affected_code, index_code

index_code()  # Build/refresh the embedding index
results = find_affected_code("change session ID derivation")
for r in results:
    print(f"{r.relevance:.2f} | {r.path} | {r.section} | {r.impact_type}")
```

### Emoji Embedding (`tools.emoji_embedding`)

Embedding-based emoji selection for Telegram reactions. Maps feeling words to the nearest emoji via cosine similarity, searching both the 73 standard Telegram reaction emojis and any available Premium custom emoji packs.

```python
from tools.emoji_embedding import find_best_emoji, find_best_emoji_for_message, EmojiResult

result = find_best_emoji("excited")        # -> EmojiResult
str(result)                                 # -> "🔥" (backward compatible)
result.is_custom                            # -> True if custom emoji matched
result.document_id                          # -> Telegram document_id for custom emoji

result = find_best_emoji_for_message(text)  # -> EmojiResult for message context
```

Returns an `EmojiResult` dataclass carrying both standard emoji and optional custom emoji data. `str(result)` preserves backward compatibility. Custom emoji wins only when its score exceeds the standard match by a 0.05 delta.

Used by the bridge for automatic reaction selection and by `send_telegram --react` / `--emoji` for agent-initiated reactions and messages. See `docs/features/emoji-embedding-reactions.md` for full documentation.

### Send Telegram Reactions (`tools.send_telegram --react`)

Set an emoji reaction on a Telegram message by describing a feeling word. The feeling is resolved to the nearest emoji (standard or custom) via the embedding index.

```bash
python tools/send_telegram.py --react "excited"
python tools/send_telegram.py --react "great work"
python tools/send_telegram.py --react "thinking"
```

Requires `TELEGRAM_REPLY_TO` to be set (identifies the message to react to). The reaction payload is queued to the Redis outbox and delivered by `bridge/telegram_relay.py`. When a custom emoji is matched, the payload includes `custom_emoji_document_id` for the relay to dispatch via `ReactionCustomEmoji`.

### Send Telegram Custom Emoji (`tools.send_telegram --emoji`)

Send a standalone custom emoji message by describing a feeling word. The feeling is resolved to the best emoji via the embedding index, preferring custom emoji when available.

```bash
python tools/send_telegram.py --emoji "celebration"
python tools/send_telegram.py --emoji "excited"
python tools/send_telegram.py --emoji "sad"
```

Requires `TELEGRAM_CHAT_ID` and `VALOR_SESSION_ID`. Queues a `custom_emoji_message` payload to the Redis outbox. The relay renders it using `MessageEntityCustomEmoji` for Premium custom emoji, falling back to plain text if the send fails.

### Link Analysis (`tools.link_analysis`)

URL extraction and metadata analysis.

```python
from tools.link_analysis import analyze_url

metadata = analyze_url("https://example.com")
```

### Session Tags (`tools.session_tags`)

CRUD and auto-tagging for session categorization.

```python
from tools.session_tags import add_tags, get_tags, sessions_by_tag, auto_tag_session

add_tags("session-123", ["hotfix", "urgent"])
tags = get_tags("session-123")
bug_sessions = sessions_by_tag("bug")
auto_tag_session("session-123")  # called automatically at session completion
```

### Agent Session Scheduler (`tools.agent_session_scheduler`)

Agent-initiated queue operations. Schedule SDLC sessions, push arbitrary messages,
and manage queue state mid-conversation.

```bash
# Schedule SDLC work for a GitHub issue
python -m tools.agent_session_scheduler schedule --issue 113
python -m tools.agent_session_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"

# Push arbitrary session
python -m tools.agent_session_scheduler push --message "What is the architecture?"

# Queue status
python -m tools.agent_session_scheduler status

# Queue manipulation
python -m tools.agent_session_scheduler bump --agent-session-id <ID>
python -m tools.agent_session_scheduler pop --project valor
python -m tools.agent_session_scheduler cancel --agent-session-id <ID>

# Kill a running or pending session (terminates subprocess, sets status="killed")
python -m tools.agent_session_scheduler kill --agent-session-id <ID>
python -m tools.agent_session_scheduler kill --session-id <SESSION_ID>
python -m tools.agent_session_scheduler kill --all

# List sessions by status
python -m tools.agent_session_scheduler list --status killed,abandoned
python -m tools.agent_session_scheduler list --status completed --limit 5

# Clean up stale sessions (deletes killed/abandoned/failed older than N minutes)
python -m tools.agent_session_scheduler cleanup --age 30 --dry-run   # Preview
python -m tools.agent_session_scheduler cleanup --age 30              # Delete
```

See `docs/features/agent-session-scheduling.md` for full documentation.

### Session Steering CLI (`tools.valor_session`)

Create, steer, monitor, and kill `AgentSession` records. The primary external interface for session steering — any process can write messages to a running session's inbox.

```bash
# List all sessions
python -m tools.valor_session list
python -m tools.valor_session list --status running
python -m tools.valor_session list --role pm

# Inspect a session
python -m tools.valor_session status --id <SESSION_ID>

# Inject a steering message into a running session
python -m tools.valor_session steer --id <SESSION_ID> --message "Stop after critique"

# Create a new session
python -m tools.valor_session create --role pm --message "Plan issue #735"
python -m tools.valor_session create --role dev --message "Fix the bug" --parent <PARENT_ID>

# Kill sessions
python -m tools.valor_session kill --id <SESSION_ID>
python -m tools.valor_session kill --all

# JSON output for scripting
python -m tools.valor_session status --id <SESSION_ID> --json
```

See `docs/features/session-steering.md` for full documentation.

### SDLC Stage Query (`tools.sdlc_stage_query`)

Query SDLC pipeline `stage_states` from a PM session. Used by the SDLC router skill as the primary signal for routing decisions (which sub-skill to dispatch next). Returns JSON mapping stage names to statuses.

```bash
# Query by session ID
python -m tools.sdlc_stage_query --session-id tg_project_123_456

# Query by GitHub issue number (finds the PM session tracking that issue)
python -m tools.sdlc_stage_query --issue-number 704

# No args — falls back to VALOR_SESSION_ID / AGENT_SESSION_ID env vars
python -m tools.sdlc_stage_query
```

```python
from tools.sdlc_stage_query import query_stage_states

states = query_stage_states(session_id="tg_project_123_456")
# {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress", ...}

states = query_stage_states(issue_number=704)
```

Always exits 0 and returns `{}` on any error (missing session, Redis down, malformed data). See `docs/features/pipeline-state-machine.md` for how the router uses this tool.

## OfficeCLI

Standalone binary at `~/.local/bin/officecli` for creating, reading, and editing Office documents (.docx, .xlsx, .pptx). No dependencies, no Office installation needed. Installed and updated automatically by the update system (`scripts/update/officecli.py`).

```bash
# Create files
officecli create report.docx
officecli create data.xlsx
officecli create slides.pptx

# Read and inspect
officecli view report.docx outline       # Document structure
officecli view report.docx stats         # Page/word/shape counts
officecli get report.docx '/body/p[1]' --json

# Edit
officecli set data.xlsx /Sheet1/A1 --prop value="Name" --prop bold=true
officecli add slides.pptx /slide[1] --type shape --prop text="Revenue grew 25%"
```

Strategy: L1 (read) then L2 (DOM edit) then L3 (raw XML). Use `--json` for structured output. Run `officecli <format> set` for help on available properties.

See `.claude/skills/officecli/SKILL.md` for the full agent reference.

## Image Tools

Installed CLI commands via `pip install -e .`

### Image Generation (`valor-image-gen`)

Generate images from text prompts using AI.

```bash
valor-image-gen "a cat wearing a space helmet"           # Square 1:1
valor-image-gen "sunset over mountains" 16:9             # Landscape
valor-image-gen "mobile app mockup" 9:16                 # Portrait/stories
valor-image-gen --help                                   # Show all ratios
```

**Supported aspect ratios:**
- `1:1` - Square (default)
- `16:9` - Landscape/widescreen
- `9:16` - Portrait/stories
- `4:3` - Standard
- `3:4` - Portrait standard

Images saved to `generated_images/` and automatically sent via Telegram bridge.

### Image Analysis (`valor-image-analyze`)

Analyze images with AI vision capabilities.

```bash
valor-image-analyze photo.jpg                            # General analysis
valor-image-analyze screenshot.png text                  # OCR/text extraction
valor-image-analyze diagram.png description objects      # Multiple analyses
valor-image-analyze --help                               # Show options
```

**Analysis types:**
- `description` - General description of image contents
- `objects` - Object detection and identification
- `text` - OCR/text extraction
- `tags` - Keywords and categories
- `safety` - Content safety analysis

## Browser Automation

`agent-browser` - Installed globally via npm.

Headless browser automation for web testing, form filling, screenshots, and data extraction.

### Core Workflow

```bash
# 1. Navigate to page
agent-browser open https://example.com

# 2. Get interactive snapshot with element refs (@e1, @e2, etc.)
agent-browser snapshot -i

# 3. Interact using refs
agent-browser click @e1
agent-browser fill @e2 "search text"

# 4. Re-snapshot after page changes
agent-browser snapshot -i
```

### Common Commands

| Command | Description |
|---------|-------------|
| `agent-browser open <url>` | Navigate to URL |
| `agent-browser snapshot -i` | Interactive snapshot with element refs |
| `agent-browser click @e1` | Click element by reference |
| `agent-browser fill @e2 "text"` | Fill input field |
| `agent-browser screenshot output.png` | Take screenshot |
| `agent-browser extract` | Extract page content |
| `agent-browser --help` | Full command list |

### Screenshot Naming Convention

For `/do-pr-review` workflow, screenshots follow this pattern:

```
generated_images/{workflow_id}/{nn}_{descriptive_name}.png

Examples:
generated_images/review-auth/01_main_dashboard.png
generated_images/review-auth/02_user_profile.png
generated_images/review-auth/03_error_state.png
```

## Workflows

Multi-step processes that combine tools for common tasks.

### PR Review Workflow (`/do-pr-review`)

Validate implementation against spec with screenshots.

```
/do-pr-review [workflow_id] [spec_file]
```

Steps:
1. Detect branch and workflow ID
2. Find matching spec in `specs/*.md`
3. Start app with `/prepare_app`
4. Capture screenshots via `agent-browser`
5. Compare implementation vs spec
6. Classify issues (blocker/tech_debt/skippable)
7. Generate report at `agents/{workflow_id}/review/report.json`

### Code Review Workflow

```
fetch PR -> analyze changes -> check tests -> post review
```

### Incident Response

```
check Sentry -> identify cause -> create fix -> deploy
```

### Research Workflow

```
search web -> summarize -> store in Notion
```

## Google Workspace Tools

Located in `tools/google_workspace/`.

### Calendar (`valor-calendar`)

Google Calendar integration for work time tracking.

```bash
valor-calendar test                    # Test OAuth connection
valor-calendar list                    # List upcoming events
valor-calendar create "Meeting" start end  # Create event
```

## Tool Development

See `/add-feature` skill for how to create new tools.

### Permission Model

| Pattern | Behavior | Use For |
|---------|----------|---------|
| `accept` | Auto-approve | Read operations (list, get, search) |
| `prompt` | Ask user | Write operations (create, update) |
| `reject` | Block | Dangerous operations (delete, destroy) |

## See Also

- Run `/add-feature` for creating new tools
- Check `.claude/skills/` for skill implementations
