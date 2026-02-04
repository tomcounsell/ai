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

# File-specific reminders: when these files are modified, print a reminder
FILE_REMINDERS = {
    "SOUL.md": (
        "REMINDER: SOUL.md was modified. Review bridge/summarizer.py to ensure "
        "SUMMARIZER_SYSTEM_PROMPT still matches Valor's voice (senior dev → PM style)."
    ),
}


def check_file_reminders(hook_input: dict) -> None:
    """Print reminders when specific files are modified."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    for filename, reminder in FILE_REMINDERS.items():
        if filename in file_path:
            print(reminder)


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Check for file-specific reminders
    check_file_reminders(hook_input)

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
