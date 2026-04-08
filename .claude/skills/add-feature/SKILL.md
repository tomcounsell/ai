---
name: add-feature
description: "Use when adding a new feature, skill, tool, or MCP server to the Valor system. Triggered by 'add a feature', 'create a new tool', 'extend the system', or 'how do I add...'."
argument-hint: "<feature-name> or description"
---

# Add Feature - Extension Guide

How to extend the Valor system with new capabilities.

## When to Use

- Adding a Claude Code skill (slash command)
- Adding to the Telegram bridge
- Creating new Python tools
- Adding new documentation

## Adding a Claude Code Skill

Skills live in `.claude/skills/<skill>/SKILL.md`:

```markdown
---
name: skill-name
description: "Use when [trigger condition]. Also use when [secondary trigger]."
allowed-tools: Read, Write, Edit, Bash
---

# Skill Name

## What this skill does
Description of what this skill does.

## When to load sub-files
- [Condition A] -> read [SUB_FILE_A.md](SUB_FILE_A.md)

## Quick start
Step-by-step instructions for the most common use of this skill.
```

### Examples (in `.claude/skills/`)

- `prime/SKILL.md` - Codebase onboarding
- `pthread/SKILL.md` - Parallel thread execution
- `sdlc/SKILL.md` - Autonomous dev workflow
- `do-pr-review/SKILL.md` - PR review and implementation validation
- `setup/SKILL.md` - New machine configuration

## Adding a Claude Code Agent

Agents live in `.claude/agents/<name>.md`:

```markdown
---
name: agent-name
description: "One-sentence description. Used by the harness to select this agent — be specific about when to invoke it."
model: sonnet
color: cyan
tools: ['*']
---

# Agent Name

## Purpose

What this agent does and when it's used.

## Instructions

- Focused behavioral instructions
- What to do, what NOT to do
- How to signal completion
```

### Read-only agents

Use `disallowedTools` instead of restricting `tools`:

```markdown
---
name: auditor
description: Read-only agent that inspects and reports without modifying anything.
disallowedTools: Write, Edit, NotebookEdit
---
```

Do NOT use a specific tool list (e.g. `tools: [Read, Grep]`) — those names don't match Claude Code's internal tool identifiers and will be silently ignored.

### Optional frontmatter fields

| Field | Example | When to use |
|-------|---------|-------------|
| `model` | `sonnet`, `haiku` | Override default model |
| `color` | `cyan`, `yellow`, `red` | Visual distinction in UI |
| `disallowedTools` | `Write, Edit, NotebookEdit` | Read-only agents |
| `hooks` | See builder.md | Post-tool side effects |

### Examples (in `.claude/agents/`)

- `builder.md` — full tool access, PostToolUse format hook
- `validator.md` — read-only via `disallowedTools`
- `dev-session.md` — SDK-driven session agent

## Adding to the Telegram Bridge

The bridge is in `bridge/telegram_bridge.py`.

### Adding Message Handling

1. Add pattern matching in the message handler:

```python
async def handle_message(self, event):
    text = event.message.text

    # Add your pattern
    if text.startswith('/mycommand'):
        return await self.handle_my_command(event)
```

2. Implement the handler:

```python
async def handle_my_command(self, event):
    # Extract arguments
    args = event.message.text.split()[1:]

    # Process and respond
    result = do_something(args)
    await event.reply(result)
```

3. Restart the bridge after changes:

```bash
./scripts/valor-service.sh restart
```

## Adding Python Tools

Local tools live in `tools/<tool_name>/`.

### Structure

```
tools/my_tool/
├── __init__.py       # Exports main functions
├── core.py           # Core implementation
├── cli.py            # Optional CLI interface
└── README.md         # Documentation
```

### Example __init__.py

```python
from .core import my_function, MyClass

__all__ = ['my_function', 'MyClass']
```

### Adding CLI

```python
# tools/my_tool/cli.py
import argparse

def main():
    parser = argparse.ArgumentParser(description='My tool')
    parser.add_argument('action', choices=['do', 'list'])
    args = parser.parse_args()

    if args.action == 'do':
        from .core import my_function
        result = my_function()
        print(result)

if __name__ == '__main__':
    main()
```

Register in `pyproject.toml`:

```toml
[project.scripts]
my-tool = "tools.my_tool.cli:main"
```

Then install: `uv pip install -e .`

## Adding Documentation

### Where to Put It

| Content Type | Location |
|--------------|----------|
| Development workflow | `CLAUDE.md` (if essential) |
| Feature documentation | `docs/features/<name>.md` |
| Architecture details | `docs/architecture/<name>.md` |
| Operations guides | `docs/operations/<name>.md` |
| Tool documentation | `docs/tools-reference.md` |
| Plans (in progress) | `docs/plans/<name>.md` |

### Documentation Template

```markdown
# Feature Name

Brief description.

## Overview

What this feature does and why it exists.

## Usage

How to use it with examples.

## Implementation Details

How it works internally.

## See Also

- Related features
- Related skills
```

### Updating the Index

Add new docs to `docs/README.md` index.

## Workflow for New Features

Follow the SDLC pattern:

1. **Plan**: Understand what you're building and where it fits
2. **Build**: Create the skill/tool/feature
3. **Test**: Verify it works (manually or with tests)
4. **Document**: Update relevant docs
5. **Ship**: `git add . && git commit -m "Add feature X" && git push`
6. **Restart**: If bridge code changed, restart the service

## Checklist

Before marking a new feature complete:

- [ ] Code implemented and working
- [ ] Tests added (if applicable)
- [ ] Documentation updated
- [ ] CLAUDE.md updated (if affects core workflow)
- [ ] Changes committed and pushed
- [ ] Service restarted (if runtime code changed)

## See Also

- Run `/prime` for codebase orientation
- See `docs/tools-reference.md` for tool documentation
- Check `.claude/skills/` for skill examples
