#!/usr/bin/env python3
"""Hook: SubagentStop - Track subagent completions."""

import sys

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

    entry = {
        "event": "subagent_stop",
        "subagent_type": hook_input.get("subagent_type", "unknown"),
        "subagent_id": hook_input.get("subagent_id", "unknown"),
    }

    append_to_log(session_dir, "subagents.jsonl", entry)


if __name__ == "__main__":
    main()
