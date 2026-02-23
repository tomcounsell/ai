---
name: new-valor-skill
description: Use when creating a Valor-specific tool that integrates with the Telegram bridge, uses SOUL.md persona, or needs CLAUDE.md documentation. Also use when the user says 'create a valor tool', 'new valor skill', or 'build a tool for this project'. Handles tool directory structure, CLI registration, bridge integration, and validation hooks.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
hooks:
  Stop:
    - hooks:
        - type: command
          command: >-
            $CLAUDE_PROJECT_DIR/.venv/bin/python $CLAUDE_PROJECT_DIR/.claude/hooks/validators/validate_tool_structure.py
            --tools-dir tools
        - type: command
          command: >-
            $CLAUDE_PROJECT_DIR/.venv/bin/python $CLAUDE_PROJECT_DIR/.claude/hooks/validators/validate_claude_md_updated.py
            --tools-dir tools
---

# New Valor Skill

## What this skill does

Creates a new tool specifically for Valor's AI system, following established project patterns. This wraps the generic [new-skill](../new-skill/SKILL.md) with Valor-specific conventions: Telegram bridge integration, SOUL.md persona, `tools/` directory layout, CLAUDE.md documentation, pyproject.toml CLI registration, and automatic validation hooks.

## When to load sub-files

- For the canonical SKILL.md template structure → read [../new-skill/SKILL_TEMPLATE.md](../new-skill/SKILL_TEMPLATE.md)
- For generic skill creation rules (description format, field constraints, debugging) → read [../new-skill/SKILL.md](../new-skill/SKILL.md)

## Valor-specific vs shared

| Valor-Specific (use this skill) | Shared/Generic (use new-skill instead) |
|---------------------------------|----------------------------------------|
| Uses SOUL.md persona | Works in any Claude Code context |
| Integrates with Telegram bridge | Standalone utility |
| Lives in `tools/` directory | No Valor dependencies |
| Registered in `pyproject.toml` | Self-contained package |
| Documented in CLAUDE.md | Self-contained docs |

## Quick start

### 1. Plan

Before writing code, answer:
- What does this tool do?
- Who uses it? (Valor via Telegram, developer via CLI, both?)
- Does it need Telegram? External APIs? AI models?
- Check `tools/` for similar patterns and `config/models.py` for available models

### 2. Create the tool directory

```
tools/<tool_name>/
├── __init__.py       # Main implementation (REQUIRED)
├── README.md         # Documentation (REQUIRED)
├── manifest.json     # Tool metadata (recommended)
└── tests/
    ├── __init__.py
    └── test_<tool>.py
```

### 3. Implement

The `__init__.py` must follow this pattern:

```python
"""
Tool Name - Brief description.

Usage:
    from tools.tool_name import main_function
    result = main_function(arg1, arg2)

CLI:
    valor-tool-name arg1 arg2
"""

import argparse
import sys


def main_function(arg1: str, arg2: str | None = None) -> dict:
    """Main tool function. Returns dict with 'result' or 'error' key."""
    try:
        result = do_work(arg1, arg2)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Tool description")
    parser.add_argument("arg1", help="First argument")
    parser.add_argument("arg2", nargs="?", help="Optional second argument")
    args = parser.parse_args()

    result = main_function(args.arg1, args.arg2)
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(result["result"])


if __name__ == "__main__":
    main()
```

### 4. Register CLI

Add to `[project.scripts]` in `pyproject.toml`:

```toml
valor-tool-name = "tools.tool_name:main"
```

Then run: `pip install -e .`

### 5. Integrate with bridge

- Files are auto-detected by `extract_files_from_response()` in the bridge
- For explicit file sending, use: `<<FILE:/path/to/file>>`
- Check `ABSOLUTE_PATH_PATTERN` in `bridge/telegram_bridge.py` covers your file types
- For AI models, import from `config/models.py`: `MODEL_FAST`, `MODEL_REASONING`, `MODEL_IMAGE_GEN`, `MODEL_VISION`

### 6. Document in CLAUDE.md

Add to the appropriate tools section:

```markdown
- **Tool Name** (`valor-tool-name`): Brief description
  ```bash
  valor-tool-name arg1 arg2    # Example usage
  valor-tool-name --help       # Show options
  ```
```

### 7. Test, lint, deploy

```bash
pytest tools/<tool_name>/tests/ -v
black tools/<tool_name>/
ruff check tools/<tool_name>/
git add tools/<tool_name>/ pyproject.toml CLAUDE.md
git commit -m "Add valor-<name> tool for <purpose>"
git push
pip install -e .
```

If bridge-integrated, restart: `./scripts/valor-service.sh restart`

## Validation

This skill has automatic hooks that run on Stop events:

1. **Tool structure** — Verifies `tools/<name>/` has `__init__.py` and `README.md`
2. **CLAUDE.md updated** — Verifies the tool is documented for agent awareness

If validation fails, you receive specific instructions on what is missing.

## Reference implementations

| Tool | Pattern | Key Features |
|------|---------|--------------|
| `tools/image_gen/` | API + file output | Gemini API, saves to `generated_images/` |
| `tools/image_analysis/` | Vision + multi-mode | Claude vision, multiple analysis types |
| `tools/sms_reader/` | System access | macOS database, CLI subcommands |
| `tools/telegram_history/` | Database query | SQLite, search patterns |

## Checklist

- [ ] **Planning**: Defined capability, integration points, dependencies
- [ ] **Structure**: Created `tools/<name>/` with `__init__.py`, `README.md`
- [ ] **Implementation**: Main function with error handling, CLI entry point
- [ ] **CLI Registration**: Added to `pyproject.toml`, ran `pip install -e .`
- [ ] **CLAUDE.md**: Added documentation in appropriate section
- [ ] **Tests**: Created test file, tests pass
- [ ] **Lint**: `black` and `ruff` pass
- [ ] **Committed**: Changes pushed to git
- [ ] **Verified**: CLI works, tool functions correctly

## Version history

- v2.0.0 (2026-02-22): Refactored as thin Valor wrapper; generic content extracted to new-skill
- v1.0.0: Original monolithic skill (337 lines)
