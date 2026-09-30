"""Resolve which file a hook invocation is about, from the hook payload alone.

The contract, in one line: ``None`` means "nothing to validate", never "go find
something to validate". Working-tree state — ``git status``, mtimes, directory
globs — is never an input to target selection. Neither is any cwd: not the hook
process's, and not the payload's.

That rule exists because every validator in this family got it wrong the same
way. Each carried a private newest-plan-doc guesser that shelled out to
``git status`` over ``docs/plans/`` and judged whichever untracked ``.md`` had
the newest mtime. The query ran in whatever checkout the hook process
started in, so a ``Write`` to ``docs/features/foo.md`` in one worktree was
blocked by another lane's in-progress plan doc (#2682, fixed for one validator
by PR #2688; #2689 carried the remaining four). Concurrent SDLC lanes hit this
constantly, because writing a plan doc is exactly what two lanes do at once.

Both functions are separately callable and both guard a non-dict payload:
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
    except (OSError, ValueError):
        # ValueError covers json.JSONDecodeError, which subclasses it.
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
    # The `or` collapses an empty `file_path` to the next candidate, but not to
    # None: `"" or ""` is `""`, so a payload carrying both keys empty would hand
    # back an empty string. The explicit `and path` is what makes the documented
    # contract — empty string means nothing to validate — actually hold.
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return path if isinstance(path, str) and path else None


def in_scope(path: str, directory: str, extension: str = ".md") -> bool:
    """Whether ``path`` falls inside the directory/extension pair a hook watches.

    The path is judged **as given**. An absolute payload path is never rewritten
    against a cwd first: the hook process's cwd is not necessarily the payload's
    (a lane's hook can run from another checkout), the payload need not carry a
    ``cwd`` key at all (`.opencode/plugins/valor-bridge.ts` sends none), and on
    macOS the two disagree over ``/tmp`` vs ``/private/tmp`` even when both are
    present. Any such rewrite makes working-tree/process state an input to
    target selection again, which is the whole defect (#2682, #2689).

    So the directory test is *containment of an anchored segment*, not a
    ``startswith`` on a relative form. ``docs/plans`` matches
    ``docs/plans/a.md`` and ``/repo/docs/plans/a.md``, and does not match
    ``docs/plans_archive/old.md`` or ``docs/plansomething/a.md``. The extension
    is required too, so ``docs/plans/helper.py`` is out of scope for a hook
    watching ``.md``.
    """
    if not path or not directory:
        return False
    normalized = path.replace("\\", "/")

    if extension:
        ext = extension if extension.startswith(".") else f".{extension}"
        if not normalized.endswith(ext):
            return False

    anchored = directory.replace("\\", "/").strip("/")
    if not anchored:
        return False
    return normalized.startswith(f"{anchored}/") or f"/{anchored}/" in normalized
