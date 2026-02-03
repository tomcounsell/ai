#!/usr/bin/env python3
"""Hook: PreToolUse - Log before tool execution."""

import sys
import time

# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from utils.constants import (
    append_to_log,
    ensure_session_log_dir,
    get_session_id,
    read_hook_input,
)


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    session_id = get_session_id(hook_input)
    session_dir = ensure_session_log_dir(session_id)

    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})

    entry = {
        "event": "pre_tool_use",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "start_time": time.time(),
    }

    append_to_log(session_dir, "tool_use.jsonl", entry)


if __name__ == "__main__":
    main()
