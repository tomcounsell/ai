"""Resolve which file a hook invocation is about, from the hook payload alone.

The contract, in one line: ``None`` means "nothing to validate", never "go find
something to validate". Working-tree state — ``git status``, mtimes, directory
globs — is never an input to target selection.

That rule exists because every validator in this family got it wrong the same
way. Each carried a private newest-plan-doc guesser that shelled out to
``git status`` over ``docs/plans/`` and judged whichever untracked ``.md`` had
the newest mtime. The query ran in whatever checkout the hook process
started in, so a ``Write`` to ``docs/features/foo.md`` in one worktree was
blocked by another lane's in-progress plan doc (#2682, fixed for one validator
by PR #2688; #2689 carried the remaining four). Concurrent SDLC lanes hit this
constantly, because writing a plan doc is exactly what two lanes do at once.

Both functions are separately callable and both guard a non-dict argument:
stdin of ``null``, ``[1, 2]``, or ``"str"`` parses cleanly into a non-dict, and
an unguarded ``.get`` on it raises ``AttributeError`` out of a hook that gates
every ``Write``.
"""

import json
import sys


def read_hook_input() -> dict:
    """Parse the hook's JSON payload from stdin. Never raises.

    Returns ``{}`` for empty stdin, malformed JSON, an unreadable stream, or a
    payload that parses to anything other than an object.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
    except (json.JSONDecodeError, OSError, EOFError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def target_from_hook_input(hook_input: dict) -> str | None:
    """The path the triggering Write actually targeted, or None.

    None means "nothing to validate", never "go find something to validate".
    The predecessor resolved the target by scanning ``git status docs/plans/``
    and taking the newest dirty file, which gated a lane on a plan it had never
    touched: a Write to ``docs/features/foo.md`` in one worktree was blocked by
    another lane's in-progress plan, and the git query ran against whatever
    checkout the hook process started in (#2682).
    """
    if not isinstance(hook_input, dict):
        return None
    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    # The `or` already collapses an empty string to the next candidate, and
    # then to None — so an empty path can never reach the caller as a target.
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return path if isinstance(path, str) else None
