# Tools Reference

Complete documentation of all tools available in the Valor system.

## MCP Server Tools

Individual operations that can be composed into larger workflows:

| Tool | Purpose | Location |
|------|---------|----------|
| **Stripe** | Payment processing, subscriptions, billing | `~/clawd/skills/stripe/` |
| **Sentry** | Error monitoring, performance analysis | `~/clawd/skills/sentry/` |
| **GitHub** | Repository operations, PRs, issues | `~/clawd/skills/github/` |
| **Render** | Deployment, infrastructure management | `~/clawd/skills/render/` |
| **Notion** | Knowledge base, documentation | `~/clawd/skills/notion/` |
| **Linear** | Project management, issue tracking | `~/clawd/skills/linear/` |

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

### Link Analysis (`tools.link_analysis`)

URL extraction and metadata analysis.

```python
from tools.link_analysis import analyze_url

metadata = analyze_url("https://example.com")
```

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
- Check `~/clawd/skills/` for MCP skill implementations
