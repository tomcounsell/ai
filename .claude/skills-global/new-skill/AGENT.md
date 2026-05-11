---
name: agent
description: Reference for creating a Claude Code subagent definition.
---

# Creating a Claude Code Agent

Agents live in `.claude/agents/<name>.md`.

## Template

```markdown
---
name: agent-name
description: "One-sentence description. Be specific about when to invoke it."
model: sonnet
color: cyan
---

# Agent Name

## Purpose

What this agent does and when it's used.

## Instructions

- Focused behavioral instructions
- What to do, what NOT to do
- How to signal completion
```

## Read-only agents

Use `disallowedTools` to restrict writes without breaking tool discovery:

```markdown
---
name: auditor
description: Read-only agent that inspects and reports without modifying anything.
disallowedTools: Write, Edit, NotebookEdit
---
```

Do NOT use `tools: [Read, Grep]` — those names don't match Claude Code's internal tool identifiers and are silently ignored.

## Optional frontmatter fields

| Field | Example | When to use |
|-------|---------|-------------|
| `model` | `sonnet`, `haiku` | Override default model |
| `color` | `cyan`, `yellow`, `red` | Visual distinction in UI |
| `disallowedTools` | `Write, Edit, NotebookEdit` | Read-only agents |
| `hooks` | YAML block | Post-tool side effects |

## Examples in `.claude/agents/`

- `builder.md` — full tool access, PostToolUse format hook
- `validator.md` — read-only via `disallowedTools`
