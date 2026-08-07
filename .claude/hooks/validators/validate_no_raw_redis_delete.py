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

Two gates run before any pattern matching (#2638):

1. **Repo scope.** This is an ai-repo rule -- raw Redis on Popoto-managed keys
   is wrong *here* because Popoto is this repo's ORM. It is meaningless in
   ``~/src/popoto``, where Popoto *is* the library under development and its
   own tests legitimately need raw Redis to construct states the ORM cannot
   produce. The hook is registered project-scoped, but the dispatcher still
   fires it for a Bash call an ai-repo session makes with a cwd inside another
   repo, which is exactly how it blocked popoto work for #2636.
2. **Executable context.** Matching on command text means prose describing the
   rule trips the rule -- filing #2638 was itself blocked because the issue
   body quoted the offending call. A command with no interpreter in it cannot
   execute a Redis call, so there is nothing to block.
"""

import json
import re
import sys
from pathlib import Path

# Walking up from an arbitrary cwd needs a bound; 40 is far past any real
# checkout depth and terminates on pathological inputs (symlink loops resolve
# first, but a very long path should not cost 4000 stat calls).
_MAX_PARENT_WALK = 40

# A checkout of this repo is the directory containing this very validator.
# Self-identifying, so it holds for the main checkout, `.worktrees/{slug}/`,
# `.claude/worktrees/{agent}/`, and a worktree parked anywhere else on disk --
# all of which have a full working tree. `git rev-parse` would also work but
# costs a subprocess on every Bash call the dispatcher sees.
_REPO_MARKER = Path(".claude") / "hooks" / "validators" / "validate_no_raw_redis_delete.py"

# Tokens that mean the command can actually run code. `redis-cli` counts: the
# CLI forms below are real execution. A bare `.py` path counts because
# `./scripts/thing.py` executes without the word "python" appearing.
_EXECUTABLE_CONTEXT = re.compile(
    r"(?:^|[\s;|&(<`$'\"])"
    r"(?:python[\d.]*|ipython|pytest|redis-cli|uvx|uv\s+run|[\w./~-]+\.py)"
    r"(?:\b|$)"
)


def _is_ai_repo_cwd(cwd: str) -> bool:
    """True when `cwd` sits inside a checkout of this repo.

    Fails CLOSED on anything it cannot resolve (empty, missing, unreadable):
    an unknown cwd keeps the guard on, because the cost of a false block is a
    retry and the cost of a false allow is index corruption.
    """
    if not cwd:
        return True
    try:
        current = Path(cwd).resolve()
    except (OSError, ValueError):
        return True
    for _ in range(_MAX_PARENT_WALK):
        try:
            if (current / _REPO_MARKER).is_file():
                return True
        except OSError:
            return True
        if current.parent == current:
            return False
        current = current.parent
    return False


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

# Popoto's field metaclass builds each prefix as `f"${name.strip('Field')}F"`
# (popoto/fields/field.py). `str.strip` takes a *character set*, not a suffix,
# so "SortedField".strip("Field") is "Sort" -- the emitted prefix is `$SortF`,
# never `$SortedF`. Guarding the latter guarded nothing: a live production
# read returns `$SortF:FencedMemory:importance:<id>` and zero `$SortedF:` keys
# exist (#2641). `tests/unit/test_memory_retrieval.py` already depends on the
# real `$DecayingSortF` spelling.
_POPOTO_CONTEXT = [
    # Explicit Popoto markers
    r"\$IndexF:",
    r"\$ClassSet:",
    r"\$SortF:",
    r"\$DecayingSortF:",
    "POPOTO_REDIS_DB",
    "popoto",
    # Model class names (and case variants). Kept in sync with the real set by
    # tests/unit/test_validate_no_raw_redis_delete.py::test_model_list_is_complete,
    # which enumerates popoto.Model subclasses across the first-party packages
    # and fails when one is missing. The list drifted silently twice before
    # that test existed (#2641).
    "AgentSession",
    "agent_session",
    "BridgeEvent",
    "Chat",
    "CorpusSizeBaseline",
    "CrashSignature",
    "DeadLetter",
    "DedupRecord",
    "DocumentChunk",
    "Job",
    "KnowledgeDocument",
    "LastProcessedRecord",
    "Link",
    "Memory",
    "PipelineLedger",
    "PRReviewAudit",
    "Reflection",
    "ReflectionIgnore",
    "ReflectionRun",
    "Room",
    # Not a popoto.Model (it is a pydantic BaseModel), so the completeness test
    # does not require it. Kept because the context gate is a widener: an extra
    # name only makes the guard fire more often, and dropping one is a
    # fail-open change with no evidence behind it.
    "SessionEvent",
    "TaskTypeProfile",
    "TelegramMessage",
    "TeammateMetrics",
]


def find_violation(command: str, cwd: str = "") -> str | None:
    """Pure predicate: return a block-reason string if `command` performs
    direct Redis access on Popoto-managed data, else None. Extracted from
    `main()` so the in-process dispatcher can call it directly. Never raises
    for well-formed input.

    `cwd` is the hook payload's working directory. It defaults to `""`, which
    means "unknown" and keeps the guard armed, so an older caller that passes
    only the command behaves exactly as before.
    """
    if not command:
        return None

    # Gate 1: an ai-repo rule, enforced only in an ai-repo checkout (#2638).
    if not _is_ai_repo_cwd(cwd):
        return None

    # Gate 2: text that cannot execute is prose about the rule, not a
    # violation of it (#2638).
    if not _EXECUTABLE_CONTEXT.search(command):
        return None

    has_popoto = any(re.search(p, command, re.IGNORECASE) for p in _POPOTO_CONTEXT)
    if not has_popoto:
        return None

    for pattern in _BLOCK_PATTERNS:
        if re.search(pattern, command):
            return (
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
            )

    return None


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
    reason = find_violation(command, hook_input.get("cwd", "") or "")
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}))

    sys.exit(0)


if __name__ == "__main__":
    main()
