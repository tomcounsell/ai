"""Shared destructive-git shape-detection logic.

Consumers: ``validators/validate_no_destructive_git_in_worktree.py`` (#2137 --
blocks destructive git inside a DIRTY session worktree) and
``validators/validate_no_destructive_git_in_shared_checkout.py`` (#2448 --
blocks whole-tree destructive git in the shared main checkout, regardless of
dirty state). Both validators need identical command-position-anchored
parsing (control-operator splitting, `cd <path> &&` cwd resolution, the
`# allow-destructive-git` override token) and near-identical shape predicates
for `reset`/`clean`/`checkout`/`restore`; this module is the single place
that logic lives so neither validator can drift from the other.

Two shape predicates are exposed:

- ``is_destructive_git(simple_cmd)`` -- the full #2137 signature set, INCLUDING
  bare `git stash` / `git stash push` with no pathspec. Used by the worktree
  validator, whose blocked-shape set has always included stash.
- ``is_destructive_git_shared_checkout(simple_cmd)`` -- the narrower #2448
  signature set: only `reset --hard`, `clean -f...`/`--force`, whole-tree
  `checkout`, and whole-tree `restore`. Deliberately excludes stash (the
  #2448 deny-list is five whole-tree shapes; stash is out of scope for that
  guard) by composing the same per-subcommand helpers minus the stash branch,
  so there is zero duplicated shape-matching code between the two predicates.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_CONTROL_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

OVERRIDE_TOKEN = "# allow-destructive-git"


def _split_simple_commands(command: str) -> list[str]:
    """Split a shell command on control operators into simple commands.

    Not a full shell parser -- handles the common `a && b`, `a; b`, `a | b`
    shapes and otherwise treats the whole string as one simple command.
    """
    return [s.strip() for s in _CONTROL_SPLIT_RE.split(command) if s.strip()]


def _git_tokens(simple_cmd: str) -> list[str] | None:
    """Return the token list starting at `git` if `simple_cmd` is a git
    invocation (command-position match, skipping leading env assignments),
    else None.
    """
    try:
        tokens = shlex.split(simple_cmd)
    except ValueError:
        return None
    i = 0
    while i < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens) or tokens[i] != "git":
        return None
    return tokens[i:]


def _subcommand_and_args(git_tokens: list[str]) -> tuple[str | None, list[str]]:
    """From tokens starting at `git`, return (subcommand, args_after_it).

    Skips `git`-level flags (e.g. `git -C path reset`). Note: `-C`/`--git-dir`
    take a value; we skip the value too so it is not mistaken for the
    subcommand.
    """
    i = 1  # skip `git`
    while i < len(git_tokens):
        tok = git_tokens[i]
        if tok in ("-C", "--git-dir", "--work-tree", "-c"):
            i += 2  # flag + its value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, git_tokens[i + 1 :]
    return None, []


def _stash_push_has_pathspec(push_args: list[str]) -> bool:
    """Heuristic: does `git stash push <push_args>` name a pathspec?

    `-m/--message` takes a value; `-p/--patch`, `-k/--keep-index`,
    `-u/--include-untracked`, `-a/--all` are boolean flags. Any bare
    non-flag token that is not the value of `-m/--message` is treated as a
    pathspec.
    """
    i = 0
    while i < len(push_args):
        tok = push_args[i]
        if tok in ("-m", "--message"):
            i += 2  # consume the message value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return True  # a bare positional token -> pathspec
    return False


def _reset_is_destructive(args: list[str]) -> bool:
    return "--hard" in args


def _clean_is_destructive(args: list[str]) -> bool:
    # A force flag is required for `git clean` to delete anything: `-f`,
    # `-fd`, `-fdx`, or `--force`.
    for a in args:
        if a == "--force":
            return True
        if a.startswith("-") and not a.startswith("--") and "f" in a:
            return True
    return False


def _checkout_is_destructive(args: list[str]) -> bool:
    # Block the whole-tree discard `checkout -- .` / `checkout .` / a
    # whole-tree checkout FROM a ref (`checkout <ref> -- .`, the exact shape
    # from the #2448 incident). Only the pathspec portion -- what follows
    # `--`, if present -- decides destructiveness; a ref/branch named before
    # `--` is never itself a pathspec. A specific pathspec
    # (`checkout -- file.py`, `checkout <ref> -- file.py`) is allowed.
    if "--" in args:
        after = args[args.index("--") + 1 :]
        pathspecs = [a for a in after if not a.startswith("-")]
    else:
        pathspecs = [a for a in args if not a.startswith("-")]
    return pathspecs == ["."]


def _restore_is_destructive(args: list[str]) -> bool:
    pathspecs = [a for a in args if a != "--" and not a.startswith("-")]
    return "." in pathspecs


def _stash_is_destructive(args: list[str]) -> bool:
    # Bare `git stash` (no subcommand) -> block.
    if not args:
        return True
    # `git stash push` with NO pathspec -> block; with a pathspec -> allow.
    if args[0] == "push":
        push_args = args[1:]
        if "--" in push_args:
            # everything after `--` is a pathspec -> scoped, allow
            return False
        has_pathspec = _stash_push_has_pathspec(push_args)
        return not has_pathspec
    # Any other stash subcommand (list/show/pop/apply/drop/...) is allowed.
    return False


def is_destructive_git(simple_cmd: str) -> bool:
    """True if `simple_cmd` matches the #2137 worktree-guard signature set:
    reset --hard, clean -f..., whole-tree checkout, whole-tree restore, or
    bare/no-pathspec stash.
    """
    git_tokens = _git_tokens(simple_cmd)
    if git_tokens is None:
        return False
    sub, args = _subcommand_and_args(git_tokens)
    if sub is None:
        return False

    if sub == "reset":
        return _reset_is_destructive(args)
    if sub == "clean":
        return _clean_is_destructive(args)
    if sub == "checkout":
        return _checkout_is_destructive(args)
    if sub == "restore":
        return _restore_is_destructive(args)
    if sub == "stash":
        return _stash_is_destructive(args)
    return False


def is_destructive_git_shared_checkout(simple_cmd: str) -> bool:
    """True if `simple_cmd` matches the #2448 shared-checkout signature set:
    reset --hard, clean -f..., whole-tree checkout, whole-tree restore.

    Deliberately excludes stash -- the #2448 deny-list is five whole-tree
    shapes only; composes the same per-subcommand helpers as
    ``is_destructive_git`` minus the stash branch, so there is no duplicated
    shape-matching logic between the two predicates.
    """
    git_tokens = _git_tokens(simple_cmd)
    if git_tokens is None:
        return False
    sub, args = _subcommand_and_args(git_tokens)
    if sub is None:
        return False

    if sub == "reset":
        return _reset_is_destructive(args)
    if sub == "clean":
        return _clean_is_destructive(args)
    if sub == "checkout":
        return _checkout_is_destructive(args)
    if sub == "restore":
        return _restore_is_destructive(args)
    return False


def effective_dir(command: str, hook_cwd: str) -> str:
    """Resolve the effective working directory: `hook_cwd`, unless the first
    simple command is a `cd <path>` prefix, in which case that path (resolved
    against `hook_cwd`) wins. Mirrors `validate_no_uv_sync_in_worktree.py`.
    """
    simple_cmds = _split_simple_commands(command)
    if simple_cmds:
        try:
            first_tokens = shlex.split(simple_cmds[0])
        except ValueError:
            first_tokens = []
        if len(first_tokens) >= 2 and first_tokens[0] == "cd":
            path = Path(first_tokens[1])
            if not path.is_absolute():
                path = Path(hook_cwd) / path if hook_cwd else path
            return str(path)
    return hook_cwd


def is_worktree_path(path: str) -> bool:
    """Component match against `.worktrees` -- never a bare substring match,
    so `.worktrees-backup` never matches.
    """
    return ".worktrees" in Path(path).parts
