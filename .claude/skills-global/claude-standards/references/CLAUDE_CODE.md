# Claude Code Reference

Practical guide to Claude Code features — context management, workflow modes, conversation controls, custom commands, hooks, the SDK, and GitHub integration. For the underlying tool-use mechanics, see [`TOOL_USE.md`](TOOL_USE.md). For adding external capabilities via MCP servers, see [`MCP.md`](MCP.md).

---

## How Claude Code works

Claude Code is a coding assistant built on Claude's tool use system. The model itself only processes text — it cannot read files or run commands directly. Instead, Claude Code wraps every action in a tool-call loop:

1. Claude emits a formatted tool request (e.g., read this file, run this command).
2. The CLI executes the action and returns results as text.
3. Claude reads the result and decides what to do next.

This loop continues until the task is done. Tool-use quality is the core determinant of coding assistant effectiveness — Claude's strength here is why Claude Code handles complex, multi-step development tasks well, and why it extends cleanly to new tools without architectural changes.

---

## Context management

### CLAUDE.md files

The `/init` command analyzes the codebase and creates a `CLAUDE.md` file with project structure, architecture notes, and key files. Its contents are included in every request — it's the always-on context.

Three scopes:

| Scope | Location | Committed? | Use for |
|-------|----------|-----------|---------|
| Project | `./CLAUDE.md` | Yes | Shared team context, architecture, conventions |
| Local | `./.claude/CLAUDE.md` | No | Personal instructions for this project |
| Global | `~/.claude/CLAUDE.md` | — | Instructions that apply to every project |

**Memory mode** (`#` shortcut): edit any CLAUDE.md file in natural language without opening the file manually. Claude interprets the instruction and applies it.

### Targeted context with @mentions

Typing `@filename` in a request includes that file's contents directly. Use it to point Claude at a specific schema, interface, or config rather than making Claude search.

Rule of thumb: reference the files Claude almost always needs (database schema, core types, API contracts) in CLAUDE.md so they're always present. Use `@` for files needed only in specific tasks.

Too much irrelevant context degrades performance. Prefer targeted inclusion over loading everything.

---

## Workflow modes

### Plan Mode

Activated with **Shift + Tab** (twice). Claude reads more files and builds a detailed implementation plan before writing any code. Use for:

- Multi-step tasks requiring wide codebase understanding
- Changes touching many files or layers
- Tasks where getting the approach wrong is expensive

Plan Mode increases token usage and latency. Reserve it for breadth problems — tasks with many moving parts.

### Thinking Mode

Triggered with phrases like "ultra think" or "think deeply." Gives Claude an extended reasoning budget before responding. Use for:

- Tricky logic or algorithm design
- Debugging a specific, hard-to-isolate issue
- Cases where you need depth, not breadth

Both modes can be combined on the same request when a task needs both wide understanding and deep reasoning. Both consume additional tokens.

### Screenshots

**Control-V** (not Command-V on macOS) pastes a screenshot into the chat. Claude reads it and can make UI changes targeting what's shown. Useful for describing visual bugs or specifying design changes without writing lengthy descriptions.

---

## Conversation management

| Action | How | When to use |
|--------|-----|-------------|
| **Escape** | Press once | Stop Claude mid-response to redirect |
| **Escape + Memory** | Stop, then `#` to save | Prevent a repeated mistake by saving a correction to CLAUDE.md immediately |
| **Double Escape** | Press twice | Rewind conversation to an earlier point, skipping failed debugging detours |
| **Compact** | `/compact` | Summarize conversation history while preserving Claude's learned task context |
| **Clear** | `/clear` | Delete entire conversation history; start fresh |

The compact/clear distinction matters: compact keeps the task knowledge Claude has built up; clear discards everything. Use compact when switching subtasks within the same project. Use clear when switching to an unrelated project entirely.

---

## Custom commands

Custom commands are markdown files in `.claude/commands/` that become slash commands.

```
.claude/commands/audit.md   →   /audit
.claude/commands/fix-types.md   →   /fix-types
```

**Creating a command:**
1. Write a markdown file with instructions Claude should follow when the command runs.
2. Restart Claude Code for the command to appear.
3. Invoke with `/commandname` or `/commandname some arguments`.

**Arguments:** use `$ARGUMENTS` as a placeholder in the command file. At runtime, everything after the command name is substituted in — file paths, descriptions, ticket IDs, whatever the command needs.

```markdown
# audit.md
Review the following file for security vulnerabilities and suggest fixes: $ARGUMENTS
```

Good candidates for custom commands: dependency audits, test generation patterns, vulnerability fix workflows, standard code review checklists — anything repetitive that benefits from a consistent procedure.

---

## Hooks

Hooks are shell commands that run before or after Claude executes a tool. They give you automated feedback loops: catch errors immediately, enforce conventions, prevent sensitive access.

### Hook types

| Type | When it runs | Can block? |
|------|-------------|-----------|
| Pre-tool use | Before the tool executes | Yes (exit code 2) |
| Post-tool use | After the tool executes | No |

### Exit codes

- **Exit 0** — allow the tool call to proceed (or acknowledge completion for post-hooks).
- **Exit 2** — block the tool call (pre-hooks only). Anything written to stderr is sent to Claude as feedback — Claude sees the message and can adjust.

### Hook data

Claude passes tool call data as JSON via stdin:

```json
{
  "session_id": "abc123",
  "tool_name": "read",
  "tool_input": {
    "path": "/path/to/file"
  }
}
```

Parse `tool_name` to dispatch, then inspect `tool_input` for the relevant arguments.

### Configuration

Hooks are defined in `.claude/settings.json` (project-scoped) or `~/.claude/settings.json` (global). Structure:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "read|grep",
        "hooks": [{ "type": "command", "command": "node ./hooks/read_hook.js" }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "write|edit",
        "hooks": [{ "type": "command", "command": "node ./hooks/post_edit.js" }]
      }
    ]
  }
}
```

The `matcher` is a pipe-separated list of tool names. Restart Claude Code after any hook changes.

### Practical hook patterns

**Block sensitive file access (pre-tool use)**

```js
// hooks/read_hook.js
const input = JSON.parse(require("fs").readFileSync("/dev/stdin", "utf8"));
const path = input.tool_input?.path ?? "";
if (path.includes(".env")) {
  console.error("Blocked: .env files are off-limits.");
  process.exit(2);
}
process.exit(0);
```

**TypeScript type checking after edits (post-tool use)**

Run `tsc --no-emit` after any TypeScript file is written. If type errors are found, write them to stderr — Claude reads the errors and fixes the call sites immediately. Adapt to any typed language with a type checker, or use your test suite for untyped languages.

**Duplicate code prevention (post-tool use)**

When Claude edits a directory containing shared utilities or queries, launch a secondary Claude instance (via the SDK) to compare the new code against existing code in that directory. If a duplicate is detected, exit 2 with feedback naming the existing function. The primary Claude receives the feedback and reuses the existing code instead.

This costs extra time and tokens. Apply only to directories where duplication is consistently problematic — shared query files, utility modules, type definitions.

---

## SDK

The Claude Code SDK exposes Claude Code programmatically via CLI, TypeScript, or Python. It contains the same tools as the terminal version.

**Default permissions: read-only.** File reads, directory listings, grep. Write tools (edit, write, bash) require explicit opt-in:

```python
# Python SDK — enable write tools
result = await query(
    prompt="Refactor this function...",
    options={"allowTools": ["read", "edit", "bash"]}
)
```

**Primary use case:** integration into larger pipelines. The SDK lets you embed Claude Code intelligence into scripts, CI steps, and hooks — including the duplicate-code hook described above. Raw conversation output is returned message-by-message; the final assistant message is the response.

The SDK is most natural as a helper inside existing projects — hooks, utility scripts, automation steps — rather than as the entry point of a standalone application.

---

## GitHub integration

Claude Code runs inside GitHub Actions via an official integration.

### Setup

Run `/install-github-app` inside Claude Code. This installs the Claude Code GitHub App on your repository and auto-generates two workflow files in `.github/workflows/`.

### Default behaviors

- **Mention support:** `@claude` in any issue or PR comment assigns the task to Claude. Claude responds with analysis or code.
- **Automatic PR review:** Claude reviews every new pull request and posts a code review comment.

### Customization

The generated workflow files are standard GitHub Actions YAML — edit them for custom behavior:

- **Custom instructions:** pass context or project-specific directions directly to Claude in the workflow.
- **MCP server integration:** add MCP server startup steps before Claude runs. Claude then has access to those tools during GitHub Action execution.
- **Permissions:** every tool Claude may use must be listed explicitly in the workflow's permissions block. MCP server tools require individual entries — no wildcards.

### Example: automated browser testing in CI

1. Start the dev server as a workflow step.
2. Add the Playwright MCP server to Claude's toolset.
3. Claude visits the running app in a headless browser, runs through functionality, and posts a test checklist on the PR.

This gives automated functional verification on every PR without writing explicit test scripts — Claude derives the test plan from the code and the running app.

### Security: PII and data exposure

Claude Code can trace data flow through infrastructure definitions (Terraform, CloudFormation). In PR review mode, Claude automatically flags when a code change causes sensitive data (PII, credentials) to flow through a new path — e.g., a Lambda function adding a user email to its output that gets shared with an external partner. This review happens automatically without adding any special instructions.
