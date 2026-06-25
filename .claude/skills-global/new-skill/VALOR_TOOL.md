---
name: valor-tool
description: Reference for creating a Python tool in the Valor tools/ directory.
---

# Creating a Valor Python Tool

Python tools live in `tools/<tool_name>/` and are registered as CLI entry points.

## Structure

```
tools/<tool_name>/
├── __init__.py       # Main implementation (REQUIRED)
├── README.md         # Documentation (REQUIRED)
├── manifest.json     # Tool metadata (recommended)
└── tests/
    ├── __init__.py
    └── test_<tool>.py
```

## __init__.py pattern

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

## Register CLI

Add to `[project.scripts]` in `pyproject.toml`:

```toml
valor-tool-name = "tools.tool_name:main"
```

Then run: `uv pip install -e .`

## Bridge integration

- Files are auto-detected by `extract_files_from_response()` in the bridge
- For explicit file sending, use: `<<FILE:/path/to/file>>`
- For AI models, import from `config/models.py`: `MODEL_FAST`, `MODEL_REASONING`, `MODEL_IMAGE_GEN`, `MODEL_VISION`

## Document in CLAUDE.md

Add to the Quick Commands table or appropriate tools section:

```markdown
| `valor-tool-name arg1` | Brief description |
```

## Checklist

- [ ] `tools/<name>/` created with `__init__.py` and `README.md`
- [ ] Main function returns `{"result": ...}` or `{"error": ...}`
- [ ] CLI entry point added to `pyproject.toml`, ran `uv pip install -e .`
- [ ] CLAUDE.md updated
- [ ] Tests written and passing
- [ ] `black` and `ruff` pass
- [ ] Committed and pushed

## Reference implementations

| Tool | Pattern |
|------|---------|
| `tools/image_gen/` | API + file output |
| `tools/image_analysis/` | Vision + multi-mode |
| `tools/sms_reader/` | System access, CLI subcommands |
| `tools/telegram_history/` | Database query |
