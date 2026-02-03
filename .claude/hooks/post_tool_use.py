#!/usr/bin/env python3
"""Hook: PostToolUse - Log after tool execution."""

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
    tool_output = hook_input.get("tool_output", "")

    # Truncate large outputs to avoid bloating logs
    if isinstance(tool_output, str) and len(tool_output) > 2000:
        tool_output = tool_output[:2000] + "... [truncated]"

    entry = {
        "event": "post_tool_use",
        "tool_name": tool_name,
        "tool_output_preview": tool_output,
        "end_time": time.time(),
    }

    append_to_log(session_dir, "tool_use.jsonl", entry)


if __name__ == "__main__":
    main()
