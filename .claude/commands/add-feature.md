---
description: Guide for extending the Valor system with new skills, tools, and capabilities
---

# Add Feature - Extension Guide

How to extend the Valor system with new capabilities.

## When to Use

- Adding a Claude Code skill (slash command)
- Adding to the Telegram bridge
- Creating new Python tools
- Adding new documentation

## Adding a Claude Code Skill

Skills live in `.claude/commands/<skill>.md`:

```markdown
---
description: One-line description for skill registry
argument-hint: <optional-arg>        # If skill takes arguments
model: sonnet                        # Optional: sonnet, opus, haiku
disallowed-tools: Write, Edit        # Optional: restrict tools
---

# Skill Name

Description of what this skill does.

## When to Use

- Trigger condition 1
- Trigger condition 2

## Variables

SOME_VAR: $1  # First argument passed to skill

## Instructions

[Step-by-step instructions for Claude Code to follow]

## Notes

[Additional context, edge cases, integration points]
```

### Examples

- `/prime` - Codebase onboarding
- `/pthread` - Parallel thread execution
- `/sdlc` - Autonomous dev workflow
- `/do-pr-review` - PR review and implementation validation
- `/setup` - New machine configuration

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
- Check `.claude/commands/` for skill examples
