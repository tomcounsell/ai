#!/usr/bin/env python3
"""Shared SDLC context detection for user-level hooks.

This module provides the single source of truth for detecting whether the
current session is in an SDLC-managed context. All 3 SDLC hooks import
from here instead of duplicating the detection logic.

This is a STANDALONE module deployed to ~/.claude/hooks/sdlc/ by the update
system. The AgentSession import is optional — it falls back gracefully to
branch-only detection when Redis or the AI repo isn't available.
"""

# Global-scope module: runs under the oldest system python3 on the fleet
# (/usr/bin/python3 = 3.9 on macOS), so `str | None` annotations must stay
# unevaluated at import time. Enforced by tests/unit/test_hook_interpreter.py.
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Global options that take a VALUE, so the token after them is an argument and
# never the subcommand. `git -C /x commit` must not be read as running `/x`.
_GIT_VALUE_OPTS = (
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
)

# A path token that still contains shell syntax was never expanded: `shlex.split`
# hands `$(git rev-parse ...)` or `$REPO_ROOT` back verbatim, and using it as a
# directory sends every downstream git call into its fail-open handler, which
# ALLOWS. Reject the token and fall through to the next rung, where the payload
# `cwd` is a real, already-resolved directory -- the correct answer for exactly
# this command shape.
_UNEXPANDED_MARKERS = ("$(", "`", "${")


def split_simple_commands(command: str) -> list:
    """Split a shell command string on control operators into simple commands.

    Splits on `&&`, `||`, `;`, `|` and newline, but ONLY outside quotes.

    Quote-awareness is not a refinement here, it is the whole point. A regex
    that splits on a bare `\\n` shreds `git commit -m 'subject

    body'` -- the repo's normal commit shape, and the shape of every message
    carrying a `Closes #N` disposition line -- into fragments with unbalanced
    quotes. Every fragment then fails `shlex.split` with `ValueError`, the
    caller skips it, and the command is not recognized as a commit at all.
    That is a false ALLOW, the exact direction #3259 exists to close, so the
    splitter tracks quote state instead (PR #3342 review blocker 1).

    Still not a full shell parser: an unterminated quote simply carries to the
    end of the string, where `shlex` rejects it and the caller fails open, as
    it did before.
    """
    command = command or ""
    parts = []
    buf = []
    quote = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote is not None:
            buf.append(ch)
            # Inside double quotes a backslash still escapes; inside single
            # quotes it is literal, which is why this is guarded.
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in (";", "|", "\n"):
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_git_invocation(simple_cmd: str):
    """Parse ONE simple command as a git invocation.

    Returns None when `simple_cmd` does not invoke git. Otherwise a dict:

      ``subcommand``  -- the first non-option token after `git`, or None
      ``global_opts`` -- {option: value} for the value-taking global options
                         that appeared BEFORE the subcommand

    Stopping at the subcommand is load-bearing in both directions.

    Recognition: a substring test for `"git commit"` MISSES `git -C <dir>
    commit`, precisely the worktree-scoped form this guard exists to catch,
    and MATCHES `echo "run git commit"`, blocking on prose.

    Resolution: options AFTER the subcommand belong to the subcommand, not to
    git. `git commit -C HEAD` is `--reuse-message`, not a directory change,
    and scanning the whole token list for `-C` turned that commit-ish into a
    path, sent every git query into its fail-open handler, and ALLOWED
    (PR #3342 review blocker 2). The same scan also matched `-C` in commands
    that were never git at all, such as `grep -C 3 commit file`.
    """
    try:
        tokens = shlex.split(simple_cmd)
    except ValueError:
        return None
    i = 0
    # Step over leading VAR=value environment assignments.
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens) or os.path.basename(tokens[i]) != "git":
        return None
    i += 1
    global_opts = {}
    subcommand = None
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_VALUE_OPTS:
            if i + 1 < len(tokens):
                global_opts[token] = tokens[i + 1]
            i += 2
            continue
        if token.startswith("--") and "=" in token:
            name, _, value = token.partition("=")
            if name in _GIT_VALUE_OPTS:
                global_opts[name] = value
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        subcommand = token
        break
    return {"subcommand": subcommand, "global_opts": global_opts}


def _is_literal_path_token(token: str) -> bool:
    """True if `token` is a usable literal path rather than an unexpanded
    shell construct.
    """
    if not token:
        return False
    if token.startswith(("$", "~")):
        # A leading `$` is an unexpanded variable/subshell reference; a
        # leading `~` is an unexpanded home-dir reference -- tilde
        # expansion is the shell's job, not `shlex`'s, so `~/src/x` would
        # otherwise be joined onto the base as a literal `~` path segment
        # that cannot exist on disk (PR #3342 review blocker).
        return False
    return not any(marker in token for marker in _UNEXPANDED_MARKERS)


def _resolve_against(path_token: str, base: str) -> str:
    path = Path(path_token)
    if not path.is_absolute():
        path = Path(base) / path
    return str(path)


def effective_git_dir(command: str, hook_cwd: str) -> str:
    """Resolve the directory a `git commit` command will actually run in.

    Precedence, highest first:

    1. A directory-bearing GLOBAL option of the `git` invocation whose
       subcommand is ``commit`` -- ``-C``, else ``--work-tree``, else
       ``--git-dir`` (whose parent is the working tree). Only options
       appearing BEFORE the subcommand count, because `git commit -C <ref>`
       is ``--reuse-message``. Relative paths resolve against rung 3.
    2. A leading ``cd <path>`` simple command, resolved against `hook_cwd`
       when relative.
    3. The payload ``cwd`` (`hook_cwd`).
    4. ``os.getcwd()`` -- reached only when the payload carries no ``cwd``.
       This is an explicit LAST RESORT, not a default: silently falling back
       to the hook process's own cwd is the defect this function exists to
       fix, because that cwd is the main checkout no matter which worktree
       the command targets.

    Rungs 1 and 2 accept a path token only if it is literal; an unexpanded
    shell construct falls through to the next rung. Never raises: it always
    returns a directory string.
    """
    try:
        base = hook_cwd or os.getcwd()

        # Rung 1: git's own directory-bearing GLOBAL options, in the simple
        # command that actually does the commit.
        for simple_cmd in split_simple_commands(command or ""):
            parsed = parse_git_invocation(simple_cmd)
            if parsed is None or parsed["subcommand"] != "commit":
                continue
            global_opts = parsed["global_opts"]
            for name in ("-C", "--work-tree", "--git-dir"):
                candidate = global_opts.get(name)
                if not candidate or not _is_literal_path_token(candidate):
                    continue
                resolved = _resolve_against(candidate, base)
                if name == "--git-dir":
                    # `--git-dir=/r/.git` describes the repo; the working tree
                    # git will answer about is its parent. Checked last, so an
                    # explicit `--work-tree` always wins.
                    resolved = str(Path(resolved).parent)
                return resolved

        # Rung 2: a leading `cd <path>`.
        simple_cmds = split_simple_commands(command or "")
        if simple_cmds:
            try:
                first_tokens = shlex.split(simple_cmds[0])
            except ValueError:
                first_tokens = []
            if len(first_tokens) >= 2 and first_tokens[0] == "cd":
                if _is_literal_path_token(first_tokens[1]):
                    return _resolve_against(first_tokens[1], base)

        # Rungs 3 and 4.
        return base
    except Exception:
        return hook_cwd or os.getcwd()


def is_sdlc_context() -> bool:
    """Detect if we are in an SDLC-managed session.

    Two-tier check:
    1. Git branch starts with "session/" (inside do-build worktree)
    2. AgentSession model shows SDLC stages (requires Redis + AI repo)
    """
    # Check 1: On a session/ branch (inside do-build worktree)
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if branch.startswith("session/"):
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check 2: Query AgentSession model for active SDLC session
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        try:
            # Standalone script — sys.path mutation is safe (never imported as library)
            sys.path.insert(0, str(Path.home() / "src" / "ai"))
            from models.agent_session import AgentSession

            sessions = AgentSession.query.filter(session_id=session_id, status="active")
            for s in sessions:
                history = getattr(s, "history", None)
                if history and any("stage" in str(h) for h in history):
                    return True
        except Exception:
            pass  # Redis unavailable, model not importable, etc.

    return False


def read_stdin() -> dict:
    """Read and parse JSON from stdin. Returns empty dict on failure."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    """Print a block decision and exit 0 (Claude Code hook protocol)."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def allow() -> None:
    """Allow the command through and exit 0."""
    sys.exit(0)
