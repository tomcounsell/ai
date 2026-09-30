# Claude CLI Reference

> Source: https://code.claude.com/docs/en/cli-reference  
> Retrieved: 2026-04-15

## Overview

Claude Code starts an interactive session by default. Use `-p`/`--print` for non-interactive output.

```
claude [options] [command] [prompt]
```

## CLI Commands

| Command | Description | Example |
|---------|-------------|---------|
| `claude` | Start interactive session | `claude` |
| `claude "query"` | Start interactive session with initial prompt | `claude "explain this project"` |
| `claude -p "query"` | Query via SDK, then exit | `claude -p "explain this function"` |
| `cat file \| claude -p "query"` | Process piped content | `cat logs.txt \| claude -p "explain"` |
| `claude -c` | Continue most recent conversation in current directory | `claude -c` |
| `claude -c -p "query"` | Continue via SDK (non-interactive) | `claude -c -p "Check for type errors"` |
| `claude -r "<session>" "query"` | Resume session by ID or name | `claude -r "auth-refactor" "Finish this PR"` |
| `claude update` | Update to latest version | `claude update` |
| `claude auth login` | Sign in to Anthropic account | `claude auth login --console` |
| `claude auth logout` | Log out | `claude auth logout` |
| `claude auth status` | Show auth status as JSON | `claude auth status` |
| `claude agents` | List configured subagents | `claude agents` |
| `claude auto-mode defaults` | Print built-in auto mode classifier rules as JSON | `claude auto-mode defaults > rules.json` |
| `claude mcp` | Configure MCP servers | — |
| `claude plugin` | Manage plugins (alias: `claude plugins`) | — |
| `claude remote-control` | Start a Remote Control server | `claude remote-control --name "My Project"` |
| `claude setup-token` | Generate a long-lived OAuth token for CI/scripts | `claude setup-token` |

## CLI Flags

### Session Continuity

| Flag | Description | Example |
|------|-------------|---------|
| `--continue`, `-c` | Load the most recent conversation in the current directory | `claude --continue` |
| `--resume`, `-r` | Resume a specific session by ID or name, or show interactive picker | `claude --resume auth-refactor` |
| `--session-id <uuid>` | Use a specific session ID (must be a valid UUID) | `claude --session-id "550e8400-..."` |
| `--fork-session` | When resuming, create a new session ID instead of reusing the original | `claude --resume abc123 --fork-session` |
| `--from-pr` | Resume sessions linked to a specific GitHub PR | `claude --from-pr 123` |
| `--name`, `-n` | Set a display name for the session | `claude -n "my-feature-work"` |
| `--no-session-persistence` | Disable session persistence (print mode only) | `claude -p --no-session-persistence "query"` |
| `--teleport` | Resume a web session in your local terminal | `claude --teleport` |

### Output & Format

| Flag | Description | Example |
|------|-------------|---------|
| `--print`, `-p` | Print response without interactive mode | `claude -p "query"` |
| `--output-format <format>` | Output format: `text`, `json`, `stream-json` (print mode only) | `claude -p "query" --output-format json` |
| `--input-format <format>` | Input format: `text`, `stream-json` (print mode only) | `claude -p --input-format stream-json` |
| `--include-partial-messages` | Include partial streaming events (requires `--print` and `stream-json`) | — |
| `--include-hook-events` | Include hook lifecycle events in output stream (requires `stream-json`) | — |
| `--replay-user-messages` | Re-emit user messages from stdin back on stdout | — |
| `--json-schema <schema>` | Get validated JSON output matching a schema (print mode only) | — |
| `--verbose` | Enable verbose logging, shows full turn-by-turn output | `claude --verbose` |

### Model & Effort

| Flag | Description | Example |
|------|-------------|---------|
| `--model <model>` | Set model: alias (`sonnet`, `opus`) or full name | `claude --model claude-sonnet-4-6` |
| `--effort <level>` | Effort level: `low`, `medium`, `high`, `max` (Opus 4.6 only) | `claude --effort high` |
| `--fallback-model <model>` | Fallback model when default is overloaded (print mode only) | `claude -p --fallback-model sonnet "query"` |
| `--betas <betas...>` | Beta headers to include in API requests (API key users only) | `claude --betas interleaved-thinking` |

### System Prompt

| Flag | Behavior | Example |
|------|----------|---------|
| `--system-prompt <prompt>` | Replace entire default system prompt | `claude --system-prompt "You are a Python expert"` |
| `--system-prompt-file <path>` | Replace with file contents | `claude --system-prompt-file ./prompts/review.txt` |
| `--append-system-prompt <prompt>` | Append to the default system prompt | `claude --append-system-prompt "Always use TypeScript"` |
| `--append-system-prompt-file <path>` | Append file contents to the default prompt | `claude --append-system-prompt-file ./style-rules.txt` |

`--system-prompt` and `--system-prompt-file` are mutually exclusive. Append flags can combine with either replacement flag.

### Permissions & Tools

| Flag | Description | Example |
|------|-------------|---------|
| `--permission-mode <mode>` | Permission mode: `default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions` | `claude --permission-mode plan` |
| `--dangerously-skip-permissions` | Skip all permission prompts (equivalent to `bypassPermissions`) | — |
| `--allow-dangerously-skip-permissions` | Add `bypassPermissions` to Shift+Tab cycle without starting in it | — |
| `--tools <tools...>` | Restrict built-in tools (`""` disables all, `"default"` enables all) | `claude --tools "Bash,Edit,Read"` |
| `--allowedTools <tools...>` | Tools that execute without prompting | `"Bash(git log *)" "Read"` |
| `--disallowedTools <tools...>` | Tools removed from model context entirely | `"Bash(git log *)" "Edit"` |
| `--permission-prompt-tool` | MCP tool to handle permission prompts in non-interactive mode | — |
| `--enable-auto-mode` | Unlock auto mode in Shift+Tab cycle (Team/Enterprise/API plans only) | — |

### Agents & Extensions

| Flag | Description | Example |
|------|-------------|---------|
| `--agent <agent>` | Specify agent for current session (overrides `agent` setting) | `claude --agent my-custom-agent` |
| `--agents <json>` | Define custom subagents dynamically via JSON | `claude --agents '{"reviewer":{"description":"...","prompt":"..."}}'` |
| `--plugin-dir <path>` | Load plugins from directory (repeatable) | `claude --plugin-dir ./my-plugins` |
| `--disable-slash-commands` | Disable all skills and commands | — |

### MCP & Settings

| Flag | Description | Example |
|------|-------------|---------|
| `--mcp-config <configs...>` | Load MCP servers from JSON files or strings | `claude --mcp-config ./mcp.json` |
| `--strict-mcp-config` | Only use MCP servers from `--mcp-config` | — |
| `--settings <file-or-json>` | Load additional settings from file or JSON string | `claude --settings ./settings.json` |
| `--setting-sources <sources>` | Comma-separated setting sources to load: `user`, `project`, `local` | `claude --setting-sources user,project` |

### Worktrees & IDE

| Flag | Description | Example |
|------|-------------|---------|
| `--worktree`, `-w` | Start in isolated git worktree at `<repo>/.claude/worktrees/<name>` | `claude -w feature-auth` |
| `--tmux` | Create a tmux session for the worktree (requires `--worktree`) | `claude -w feature-auth --tmux` |
| `--ide` | Automatically connect to IDE on startup | `claude --ide` |
| `--chrome` / `--no-chrome` | Enable/disable Chrome browser integration | `claude --chrome` |
| `--teammate-mode` | Agent team display: `auto`, `in-process`, `tmux` | `claude --teammate-mode in-process` |

### Performance & Caching

| Flag | Description | Example |
|------|-------------|---------|
| `--bare` | Minimal mode: skip hooks, LSP, plugins, CLAUDE.md discovery, MCP. Sets `CLAUDE_CODE_SIMPLE=1` | `claude --bare -p "query"` |
| `--exclude-dynamic-system-prompt-sections` | Move per-machine sections to first user message (improves cache reuse) | `claude -p --exclude-dynamic-system-prompt-sections "query"` |
| `--max-budget-usd <amount>` | Maximum spend on API calls (print mode only) | `claude -p --max-budget-usd 5.00 "query"` |
| `--max-turns <n>` | Limit agentic turns (print mode only, no limit by default) | `claude -p --max-turns 3 "query"` |
| `--add-dir <directories...>` | Add directories for tool access (grants file access, not config discovery) | `claude --add-dir ../apps ../lib` |

### Other

| Flag | Description |
|------|-------------|
| `--debug [filter]` | Enable debug mode with optional category filtering (e.g., `"api,hooks"`) |
| `--debug-file <path>` | Write debug logs to a file (implicitly enables debug mode) |
| `--remote` | Create a new web session on claude.ai |
| `--remote-control`, `--rc` | Start interactive session with Remote Control enabled |
| `--remote-control-session-name-prefix` | Prefix for auto-generated Remote Control session names |
| `--init` | Run initialization hooks and start interactive mode |
| `--init-only` | Run initialization hooks and exit |
| `--maintenance` | Run maintenance hooks and start interactive mode |
| `--version`, `-v` | Output the version number |
| `--help`, `-h` | Display help |

## Key Patterns for Programmatic Use

```bash
# Non-interactive, stream JSON output
claude -p --output-format stream-json --include-partial-messages "query"

# Resume a specific session non-interactively
claude -p -r <session-uuid> "next message"

# Continue most recent session non-interactively
claude -p -c "next message"

# Resume + stream JSON (harness pattern)
claude -p --resume <uuid> --output-format stream-json --include-partial-messages --permission-mode bypassPermissions "query"

# Bare mode for fast scripted calls (no hooks, MCP, CLAUDE.md)
claude --bare -p "query"

# Improve prompt cache reuse across machines
claude -p --exclude-dynamic-system-prompt-sections "query"
```

## Session Files

Sessions are stored as JSONL files at:
```
~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl
```

The `--resume <uuid>` flag loads a specific session file. Both interactive and `-p` modes write to the same format, so sessions are interchangeable between modes.
