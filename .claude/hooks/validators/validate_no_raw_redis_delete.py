#!/usr/bin/env python3
"""PreToolUse hook: Block direct Redis writes on Popoto-managed keys and indexes.

All Redis writes on Popoto objects must go through the ORM:
  - instance.delete()        -- removes hash + all index entries
  - instance.save()          -- updates hash + all index entries
  - Model.rebuild_indexes()  -- repairs indexes from surviving hashes

Direct writes (DEL, SREM, SADD, ZREM, ZADD) bypass on_save/on_delete hooks
and corrupt index integrity.
"""

import json
import re
import sys

_BLOCK_PATTERNS = [
    r"\br\.delete\(",
    r"\bredis_client\.delete\(",
    r"\bclient\.delete\(",
    r"\bPOPOTO_REDIS_DB\.delete\(",
    r"\bpipeline\.delete\(",
    r"redis-cli\s+.*\bDEL\b",
    r"\br\.srem\(",
    r"\br\.sadd\(",
    r"\bpipeline\.srem\(",
    r"\bpipeline\.sadd\(",
    r"\bPOPOTO_REDIS_DB\.srem\(",
    r"\bPOPOTO_REDIS_DB\.sadd\(",
    r"\br\.zrem\(",
    r"\br\.zadd\(",
    r"\bpipeline\.zrem\(",
    r"\bpipeline\.zadd\(",
]

_POPOTO_CONTEXT = [
    "AgentSession",
    "agent_session",
    r"\$IndexF:",
    r"\$ClassSet:",
    r"\$SortedF:",
    "POPOTO_REDIS_DB",
    "popoto",
    "TelegramMessage",
]


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        hook_input = json.loads(raw)
    except Exception:
        sys.exit(0)

    if hook_input.get("tool_name") != "Bash":
        sys.exit(0)

    command = hook_input.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    has_popoto = any(re.search(p, command, re.IGNORECASE) for p in _POPOTO_CONTEXT)
    if not has_popoto:
        sys.exit(0)

    for pattern in _BLOCK_PATTERNS:
        if re.search(pattern, command):
            print(
                json.dumps(
                    {
                        "decision": "block",
                        "reason": (
                            "BLOCKED: Direct Redis write on Popoto-managed data.\n\n"
                            "Use Popoto ORM methods instead:\n"
                            "  instance.delete()            # removes hash + all indexes\n"
                            "  instance.save()              # updates hash + all indexes\n"
                            "  Model.rebuild_indexes()      # repair indexes from hashes\n"
                            "  Model.query.keys(clean=True) # diagnose orphaned refs\n"
                        ),
                    }
                )
            )
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
