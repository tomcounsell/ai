#!/usr/bin/env python3
"""PostToolUse hook: Format only the changed file with black and ruff.

Reads the tool_input from stdin (Claude Code hook protocol) and runs
formatters on just the affected file, not the entire project directory.

This replaces the old approach of running `black $CLAUDE_PROJECT_DIR`
which spawned multiprocessing workers across ALL files, causing 10+ GB
memory usage.
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

    # Run ruff fix then black on just this file
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix", file_path],
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [sys.executable, "-m", "black", "--quiet", file_path],
        capture_output=True,
        timeout=30,
    )


if __name__ == "__main__":
    main()
