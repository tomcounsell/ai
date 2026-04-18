#!/usr/bin/env python3
"""PreToolUse hook: Block direct Redis reads and writes on Popoto-managed keys.

All Redis access to Popoto objects must go through the ORM:

  Writes:
    instance.delete()        -- removes hash + all index entries
    instance.save()          -- updates hash + all index entries
    Model.rebuild_indexes()  -- repairs indexes from surviving hashes

  Reads:
    Model.query.filter(**kw) -- safe, binary-aware hash reads
    Model.query.get(pk)      -- fetch one by primary key
    Model.query.keys(clean=True) -- diagnose orphaned refs

Direct writes (DEL, SREM, SADD, ZREM, ZADD) bypass on_save/on_delete hooks
and corrupt index integrity.

Direct reads (HGETALL, HGET, HMGET, HSCAN, SCAN) bypass Popoto's
field-aware decoding. Clients with decode_responses=True raise
UnicodeDecodeError on hashes containing binary fields like EmbeddingField
(float32 vector bytes). See issue #1038.
"""

import json
import re
import sys

_BLOCK_PATTERNS = [
    # Writes
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
    # Reads — bypass Popoto's binary-safe field decoding (see #1038)
    r"\br\.hgetall\(",
    r"\br\.hget\(",
    r"\br\.hmget\(",
    r"\br\.hscan\(",
    r"\br\.scan_iter\(",
    r"\bredis_client\.hgetall\(",
    r"\bredis_client\.hget\(",
    r"\bredis_client\.hmget\(",
    r"\bredis_client\.hscan\(",
    r"\bredis_client\.scan_iter\(",
    r"\bclient\.hgetall\(",
    r"\bclient\.hget\(",
    r"\bclient\.hmget\(",
    r"\bclient\.hscan\(",
    r"\bclient\.scan_iter\(",
    r"\bPOPOTO_REDIS_DB\.hgetall\(",
    r"\bPOPOTO_REDIS_DB\.hget\(",
    r"\bPOPOTO_REDIS_DB\.hmget\(",
    r"\bPOPOTO_REDIS_DB\.hscan\(",
    r"\bPOPOTO_REDIS_DB\.scan_iter\(",
    r"\bpipeline\.hgetall\(",
    r"\bpipeline\.hget\(",
    r"\bpipeline\.hmget\(",
    r"\bpipeline\.hscan\(",
    r"\bpipeline\.scan_iter\(",
    r"redis-cli\s+.*\bHGETALL\b",
    r"redis-cli\s+.*\bHGET\b",
    r"redis-cli\s+.*\bHMGET\b",
    r"redis-cli\s+.*\bHSCAN\b",
]

_POPOTO_CONTEXT = [
    # Explicit Popoto markers
    r"\$IndexF:",
    r"\$ClassSet:",
    r"\$SortedF:",
    "POPOTO_REDIS_DB",
    "popoto",
    # Model class names (and case variants)
    "AgentSession",
    "agent_session",
    "BridgeEvent",
    "Chat",
    "DeadLetter",
    "DedupRecord",
    "DocumentChunk",
    "KnowledgeDocument",
    "Link",
    "Memory",
    "PRReviewAudit",
    "Reflection",
    "ReflectionIgnore",
    "SessionEvent",
    "TaskTypeProfile",
    "TelegramMessage",
    "TeammateMetrics",
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
                            "BLOCKED: Direct Redis access on Popoto-managed data.\n\n"
                            "Use Popoto ORM methods instead:\n"
                            "  Reads:\n"
                            "    Model.query.filter(field=value)   # binary-safe hash reads\n"
                            "    Model.query.get(pk)                # fetch one by PK\n"
                            "    Model.query.keys(clean=True)       # diagnose orphaned refs\n"
                            "  Writes:\n"
                            "    instance.save()                    # updates hash + indexes\n"
                            "    instance.delete()                  # removes hash + indexes\n"
                            "    Model.rebuild_indexes()            # repair indexes\n\n"
                            "Raw r.hgetall/hget/scan_iter crash on binary fields "
                            "(EmbeddingField) when decode_responses=True. See #1038."
                        ),
                    }
                )
            )
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
