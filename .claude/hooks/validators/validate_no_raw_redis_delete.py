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

1. **Other-repo exemption.** Raw Redis on Popoto-managed keys is wrong *here*
   because Popoto is this repo's ORM. It is meaningless in ``~/src/popoto``,
   where Popoto *is* the library under development and its own tests
   legitimately need raw Redis to construct states the ORM cannot produce. The
   hook is registered project-scoped, but the dispatcher still fires it for a
   Bash call an ai-repo session makes with a cwd inside another repo, which is
   exactly how it blocked popoto work for #2636.

   The exemption is being inside a *different git repository*, not merely
   being outside this one. The Redis these keys live in is machine-global, so
   a cwd like ``/tmp`` is not a reason to stand down -- it has nothing to do
   with the popoto rationale and everything to do with production keys.
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
#
# The leading class must include `/`: the house idiom invokes the interpreter
# by path (`.venv/bin/python -c ...`, and CLAUDE.md documents the
# `.venv/bin/valor-*` form), so a class of only whitespace and shell
# metacharacters left the primary vector unmatched -- it blocked on main and
# passed here. The class exists to stop `python` matching inside a longer word
# like `mypython`; a path separator before it is exactly the case it must
# admit. `/` alone covers every path form, including `~/...` and `./...`, since
# a separator always precedes the interpreter name. Do NOT add `.` to the
# class: it makes prose naming `.python-version` read as executable and
# reopens the #2638 false positive.
_EXECUTABLE_CONTEXT = re.compile(
    r"(?:^|[\s;|&(<`$'\"/])"
    r"(?:python[\d.]*|ipython|pytest|redis-cli|uvx|uv\s+run|[\w./~-]+\.py)"
    r"(?:\b|$)"
)


def _guard_applies(cwd: str) -> bool:
    """True unless `cwd` is demonstrably inside a *different* git repository.

    The rule this validator enforces is scoped to a repo, but the Redis it
    protects is machine-global: a raw delete run from anywhere on this machine
    reaches the same production keys. So the exemption has to be as narrow as
    its rationale, which is specifically "popoto is its own repo and raw Redis
    is legitimate there" (#2638). Being merely *outside* this checkout is not
    that rationale -- `cd /tmp && python -c '<raw delete>'` has nothing to do
    with popoto and every bit to do with production keys.

    Walking up from `cwd`, whichever comes first decides:

    - this repo's marker  -> in an ai checkout, guard applies
    - a `.git`            -> a different repo, guard is exempt
    - neither, to the root, or the path cannot be resolved -> guard applies

    That last clause is what makes the fail-closed claim true. A missing path
    resolves happily on macOS (`Path.resolve()` is non-strict), so "walked to
    the root without finding anything" covers `/nonexistent/...`, `/tmp` and
    `$HOME` alike, and all three now keep the guard armed.

    `.git` is checked with `exists()`, not `is_dir()`: a worktree's `.git` is a
    file, and a worktree of another repo is still another repo.
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
            if (current / ".git").exists():
                return False
        except OSError:
            return True
        if current.parent == current:
            return True
        current = current.parent
    return True


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
    # Redundant: the "Reflection" entry above already matches this as a
    # substring, so adding it changed no behavior. Listed anyway so the set
    # reads as the model roster rather than as a puzzle, and so the
    # completeness test below is satisfied by name rather than by coincidence.
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

    # Gate 1: exempt only inside a different git repo -- the popoto case (#2638).
    if not _guard_applies(cwd):
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
