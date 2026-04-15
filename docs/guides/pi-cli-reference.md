# Pi CLI Reference

> Source: `pi --help`, https://pi.dev/docs, https://github.com/badlogic/pi-mono  
> Retrieved: 2026-04-15  
> Version: v0.66.1 (installed at `/usr/local/bin/pi` or via npm)

## Overview

Pi is a minimal terminal coding harness designed for developers. It integrates with multiple LLM providers and is extensible via TypeScript plugins.

```
pi [options] [@files...] [messages...]
```

## Operational Modes

| Mode | Flag | Purpose |
|------|------|---------|
| Interactive | (default) | Real-time terminal interface with editor |
| Print | `-p`, `--print` | Execute prompt and exit |
| JSON | `--mode json` | Emit all events as JSONL |
| RPC | `--mode rpc` | Process integration via stdin/stdout (for non-Node integrations) |

## Session Management

| Flag | Description | Example |
|------|-------------|---------|
| `--continue`, `-c` | Resume most recent session | `pi -c "What did we discuss?"` |
| `--resume`, `-r` | Browse and select from past sessions interactively | `pi -r` |
| `--session <path>` | Load specific session file or partial UUID | `pi --session ~/.pi/agent/sessions/.../session.jsonl` |
| `--fork <path>` | Fork specific session file into a new session | `pi --fork <path>` |
| `--session-dir <dir>` | Custom session storage directory | `pi --session-dir ./sessions` |
| `--no-session` | Ephemeral mode, no persistence | `pi --no-session -p "quick query"` |

Sessions are stored as JSONL at `~/.pi/agent/sessions/<encoded-path>/`. Pi supports branching history — `/fork` and `--fork` create new sessions from existing ones.

## Model & Provider

| Flag | Description | Example |
|------|-------------|---------|
| `--provider <name>` | LLM provider (default: `google`) | `--provider anthropic` |
| `--model <pattern>` | Model ID or pattern. Supports `provider/id` and `:thinking` suffix | `--model sonnet:high` |
| `--api-key <key>` | Override environment API key | — |
| `--thinking <level>` | Thinking level: `off`, `minimal`, `low`, `medium`, `high`, `xhigh` | `--thinking high` |
| `--models <patterns>` | Comma-separated model patterns for Ctrl+P cycling | `--models "claude-*,gpt-4o"` |
| `--list-models [search]` | List available models with optional fuzzy search | `pi --list-models sonnet` |

**Model shorthand**: `pi --model openai/gpt-4o` (no `--provider` needed)  
**Thinking shorthand**: `pi --model sonnet:high`

## Tools

| Flag | Description |
|------|-------------|
| `--tools <list>` | Comma-separated tools: `read,bash,edit,write,grep,find,ls` |
| `--no-tools` | Disable all built-in tools |

Default: `read,bash,edit,write`. `grep`, `find`, `ls` are off by default (read-only, explicit opt-in).

## System Prompt

| Flag | Description |
|------|-------------|
| `--system-prompt <text>` | Replace default system prompt |
| `--append-system-prompt <text>` | Append to existing prompt |

Context files (`AGENTS.md`, `CLAUDE.md`) are always concatenated regardless of replacement.

## File Input

Files prefixed with `@` are included in messages:

```bash
pi @prompt.md "Answer this"
pi -p @screenshot.png "What's in this image?"
pi @code.ts @test.ts "Review these files"
```

## Extensions & Skills

| Flag | Description |
|------|-------------|
| `-e`, `--extension <path>` | Load extension file (repeatable; accepts path, npm, or git source) |
| `--no-extensions`, `-ne` | Disable extension discovery (explicit `-e` paths still work) |
| `--skill <path>` | Load skill file or directory (repeatable) |
| `--no-skills`, `-ns` | Disable skill discovery |
| `--prompt-template <path>` | Load prompt template (repeatable) |
| `--no-prompt-templates`, `-np` | Disable prompt template discovery |
| `--theme <path>` | Load theme (repeatable) |
| `--no-themes` | Disable theme discovery |

## Export & Utility

| Flag | Description |
|------|-------------|
| `--export <file>` | Export session to HTML and exit |
| `--offline` | Disable startup network operations (same as `PI_OFFLINE=1`) |
| `--verbose` | Force verbose startup |
| `--version`, `-v` | Show version number |
| `--help`, `-h` | Show help |

## Extension CLI Flags (from installed packages)

| Flag | Description |
|------|-------------|
| `--lens-verbose` | Enable verbose pi-lens logging |
| `--no-biome` | Disable Biome linting/formatting |
| `--no-oxlint` | Disable Oxlint fast JS/TS linter |
| `--no-ast-grep` | Disable ast-grep structural analysis |
| `--no-ruff` | Disable Ruff Python linting |
| `--no-shellcheck` | Disable shellcheck for shell scripts |
| `--no-lsp` | Disable unified LSP diagnostics |
| `--no-madge` | Disable circular dependency checking |
| `--no-autoformat` | Disable automatic formatting on file write |
| `--no-autofix` | Disable auto-fixing of lint issues |
| `--no-tests` | Disable test runner on write |
| `--error-debt` | Track test failures and block if tests start failing |

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `ANTHROPIC_OAUTH_TOKEN` | Anthropic OAuth token (alternative to API key) |
| `OPENAI_API_KEY` | OpenAI API key |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_BASE_URL` | Azure OpenAI base URL |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GROQ_API_KEY` | Groq API key |
| `CEREBRAS_API_KEY` | Cerebras API key |
| `XAI_API_KEY` | xAI Grok API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `MISTRAL_API_KEY` | Mistral API key |
| `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS Bedrock credentials |
| `AWS_REGION` | AWS region for Bedrock |
| `PI_CODING_AGENT_DIR` | Session storage directory (default: `~/.pi/agent`) |
| `PI_PACKAGE_DIR` | Override package directory (for Nix/Guix) |
| `PI_OFFLINE` | Disable startup network operations when `1`/`true`/`yes` |
| `PI_SHARE_VIEWER_URL` | Base URL for `/share` command |
| `PI_CACHE_RETENTION` | Set to `long` for extended prompt caching |

## Package Management

```bash
pi install <source> [-l]     # Install extension (project-local with -l)
pi remove <source> [-l]      # Uninstall
pi update [source]           # Update packages (skips pinned versions)
pi list                      # List installed packages
pi config                    # Toggle package resources (TUI)
```

Sources: `npm:@foo/pi-tools`, `git:github.com/user/repo`, HTTPS URLs.

## Interactive Mode Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+C | Clear editor / Quit (twice) |
| Escape | Cancel/abort |
| Escape (twice) | Open session tree navigation |
| Ctrl+L | Open model selector |
| Ctrl+P / Shift+Ctrl+P | Cycle models forward/backward |
| Shift+Tab | Adjust thinking level |
| Ctrl+O | Toggle tool output visibility |
| Ctrl+T | Collapse/expand reasoning blocks |
| Shift+Enter | Multi-line input |
| Ctrl+V | Paste images |
| Alt+Enter | Queue follow-up message |
| Enter | Send steering message (interrupts remaining tools) |

## Interactive Commands (Slash)

| Command | Function |
|---------|----------|
| `/model` | Switch models mid-session |
| `/scoped-models` | Configure Ctrl+P cycling |
| `/settings` | Adjust thinking, theme, delivery options |
| `/resume` | Select from previous sessions |
| `/new` | Start fresh session |
| `/name <name>` | Rename current session |
| `/session` | Display session metadata |
| `/tree` | Navigate branching history |
| `/fork` | Branch current session |
| `/compact [prompt]` | Manual context compaction |
| `/copy` | Clipboard export of last response |
| `/export [file]` | HTML session export |
| `/share` | Upload to GitHub gist |
| `/reload` | Refresh extensions and resources |
| `/hotkeys` | Display complete keybinding list |
| `/quit` | Exit |

## Configuration Files

| Path | Purpose |
|------|---------|
| `~/.pi/agent/settings.json` | Global settings |
| `.pi/settings.json` | Project settings (overrides global) |
| `AGENTS.md` or `CLAUDE.md` | Context files (project and parent dirs) |
| `.pi/SYSTEM.md` | Project system prompt override |
| `~/.pi/agent/SYSTEM.md` | Global system prompt override |
| `~/.pi/agent/keybindings.json` | Custom keybindings |
| `~/.pi/agent/models.json` | Custom model definitions |

## JSONL Event Types (JSON/RPC mode)

Pi's `--mode json` emits newline-delimited JSON objects linked by `parentId`:

| Event type | Description |
|------------|-------------|
| `session` | Session start/metadata |
| `model_change` | Model switched mid-session |
| `thinking_level_change` | Thinking level changed |
| `message` | Content block (text, tool calls, tool results) |

RPC mode uses LF-delimited JSONL. Split records on `\n` only — not Unicode line boundaries.

## Key Patterns for Programmatic Use

```bash
# Non-interactive JSON output
pi -p --mode json "query"

# Resume specific session non-interactively
pi -p --session ~/.pi/agent/sessions/.../session.jsonl "next message"

# Continue most recent session non-interactively
pi -p --continue "next message"

# Multi-provider routing
pi -p --provider openrouter --model anthropic/claude-opus-4-6 "query"

# Groq for fast inference
pi -p --provider groq --model llama-3.3-70b-versatile "query"

# Read-only audit
pi --tools read,grep,find,ls -p "Review the code in src/"

# Ephemeral (no session saved)
pi --no-session -p "quick one-off query"
```

## Compared to Claude CLI

| Feature | Claude CLI | Pi CLI |
|---------|-----------|--------|
| Session continuity | `--resume <uuid>` or `--continue` | `--session <path>` or `--continue` |
| Session storage | `~/.claude/projects/<cwd>/<uuid>.jsonl` | `~/.pi/agent/sessions/<cwd>/` |
| Multi-provider | No (Anthropic only) | Yes (OpenAI, Groq, Gemini, Bedrock, etc.) |
| Programmatic output | `--output-format stream-json` | `--mode json` |
| Context compaction | `/compact` (interactive) | `/compact [prompt]` (interactive) |
| Thinking levels | Via model config | `--thinking off/minimal/low/medium/high/xhigh` |
| Bare/minimal mode | `--bare` | `--no-extensions --no-skills --no-tools` |
| Hook system | Claude Code hooks (`.claude/hooks/`) | Extension API (`pi.on("tool_call", ...)`) |
