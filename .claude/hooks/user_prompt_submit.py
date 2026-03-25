#!/usr/bin/env python3
"""Hook: UserPromptSubmit - Ingest user prompts into subconscious memory.

Reads the user's prompt from stdin hook input, applies quality filtering
(minimum length, trivial pattern detection), and saves qualifying prompts
as Memory records via memory_bridge.ingest().

All operations fail silently -- memory errors never block prompt submission.
"""

import sys

# Standalone script -- sys.path mutation is safe (never imported as library)
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import read_hook_input


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Extract user prompt content
    # UserPromptSubmit hook receives the prompt in "prompt" field
    prompt = hook_input.get("prompt", "")
    if not prompt or not isinstance(prompt, str):
        return

    # Ingest into memory (quality filter and dedup handled inside)
    try:
        from hook_utils.memory_bridge import ingest

        ingest(prompt)
    except Exception:
        pass  # Silent failure -- never block prompt submission


if __name__ == "__main__":
    main()
