#!/usr/bin/env python3
"""PostToolUse hook: Auto-fix lint and format on the changed file.

Reads the tool_input from stdin (Claude Code hook protocol) and runs
ruff check --fix + ruff format on just the affected file, not the
entire project directory. Uses ruff for both linting and formatting.
"""

import json
import subprocess
import sys
from pathlib import Path


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path or not file_path.endswith(".py"):
        return

    if not Path(file_path).exists():
        return

    # Run ruff check --fix then ruff format on just this file
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix", "--quiet", file_path],
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--quiet", file_path],
        capture_output=True,
        timeout=30,
    )


if __name__ == "__main__":
    main()
