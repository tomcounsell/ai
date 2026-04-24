#!/usr/bin/env python3
"""
Merge guard: blocks direct `g h  p r  m e r g e` calls that bypass the /do-merge gate.

The /do-merge skill is the authorization mechanism -- it runs the gate checks
(TEST, REVIEW, DOCS, lockfile, full suite, plan completion) and, if they all
pass, creates a short-lived authorization file that this hook checks before
allowing the merge. This prevents any caller from skipping the gate and
directly invoking the merge command.

Gate flow (works the same for autonomous PM sessions and humans):
1. /do-merge is invoked with a PR number
2. /do-merge runs all gate checks
3. If all gates pass, /do-merge creates data/merge_authorized_{pr_number}
4. /do-merge calls the merge command -- this hook sees the auth file
   and allows the call through
5. /do-merge cleans up the authorization file after the merge

This hook does NOT require human-in-the-loop. It requires that the gate ran
and passed. A PM session that runs /do-merge is a fully valid authorizer.

Exit codes:
- 0: Always (Claude Code hook protocol)

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout
- To ALLOW: print nothing or exit silently
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The merge command pattern. We keep the regex authoritative (built once) but
# reference its source via string concatenation in this module docstring so
# doc text never embeds the literal trigger.
_MERGE_CMD_RE = re.compile(r"\bgh\s+pr\s+" + "merge" + r"\b")
_HELP_FLAG_RE = re.compile(r"(?:^|\s)--help(?:\s|$)")
# Extract PR number from the merge command
_PR_NUMBER_RE = re.compile(r"\bgh\s+pr\s+" + "merge" + r"\s+(\d+)")

# Authorization files live in data/ relative to project root
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _is_authorized(command: str) -> bool:
    """Check if a merge authorization file exists for the PR number in the command."""
    match = _PR_NUMBER_RE.search(command)
    if not match:
        return False
    pr_number = match.group(1)
    auth_file = _DATA_DIR / f"merge_authorized_{pr_number}"
    return auth_file.exists()


# --- Command tokenizer (item 7 of sdlc-1155) ------------------------------
#
# Identifies which positions in a Bash command string are "actual executed
# commands" versus positions inside heredoc bodies, quoted arguments, or
# backtick substitutions. This prevents the bare regex check from firing on
# diagnostic text (commit-message bodies, issue-body flags, etc.) that
# happens to mention the merge command.
#
# Fail-closed contract: On ambiguous tokenisation, the span is treated as
# an actual command position. Any tokenizer exception triggers fallback to
# the bare-match behavior on the entire command string. This preserves the
# block on real merge calls even if the tokenizer is broken.

_COMMAND_SEPARATORS = ("&&", "||", ";;", ";", "|", "\n")


def _skip_heredoc(command: str, i: int) -> int:
    """Given the index of the first ``<`` of ``<<``, return the index at which
    scanning should resume (i.e. after the heredoc body). If the heredoc is
    malformed or unterminated, scanning resumes mid-body (fail-closed: the
    rest of the command is still scanned). Returns ``i + 2`` if this is not
    actually a heredoc.
    """
    import re as _re

    n = len(command)
    if not (i + 1 < n and command[i + 1] == "<"):
        return i + 1
    j = i + 2
    if j < n and command[j] == "-":
        j += 1
    while j < n and command[j] in " \t":
        j += 1
    delim_quote: str | None = None
    if j < n and command[j] in ("'", '"'):
        delim_quote = command[j]
        j += 1
    delim_start = j
    while j < n and command[j] not in " \t\n;|&" and command[j] != delim_quote:
        j += 1
    delim = command[delim_start:j]
    if delim_quote and j < n and command[j] == delim_quote:
        j += 1
    line_end = command.find("\n", j)
    if line_end == -1 or not delim:
        return j
    body_start = line_end + 1
    end_pattern = _re.compile(r"(?m)^[ \t]*" + _re.escape(delim) + r"[ \t]*$")
    m = end_pattern.search(command, body_start)
    if m is None:
        return n
    return m.end()


def _find_dollar_paren_close(command: str, start: int) -> int:
    """Given the index just after ``$(``, return the index of the matching ``)``.

    Respects nested ``$(...)``, ``` ` ... ` ```, single/double quotes,
    backslash escapes, and heredocs inside the substitution body. Returns
    ``-1`` if no matching close is found (unterminated substitution).

    Heredoc handling is essential: when the outer substitution contains
    ``cat <<EOF ... EOF`` with a body that happens to include literal ``$(``
    or ``)`` characters, failing to skip the body would cause us to pick a
    bogus close inside the body. That would collapse the outer span and
    let content after the real close be mis-tokenised.
    """
    n = len(command)
    i = start
    depth = 1
    in_single = False
    in_double = False
    in_backtick = False
    while i < n:
        ch = command[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_backtick:
            if ch == "`":
                in_backtick = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
                i += 1
                continue
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                depth += 1
                i += 2
                continue
            if ch == ")" and depth > 1:
                depth -= 1
                i += 1
                continue
            if ch == "`":
                in_backtick = True
                i += 1
                continue
            i += 1
            continue
        # Not in any quote/backtick
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "`":
            in_backtick = True
            i += 1
            continue
        if ch == "<" and i + 1 < n and command[i + 1] == "<":
            i = _skip_heredoc(command, i)
            continue
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            depth += 1
            i += 2
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return i
            i += 1
            continue
        i += 1
    return -1


def _find_backtick_close(command: str, start: int) -> int:
    """Given the index just after an opening ``` ` ```, return the index of the
    matching closing backtick. Respects backslash escapes. Returns ``-1`` if
    unterminated.
    """
    n = len(command)
    i = start
    while i < n:
        ch = command[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "`":
            return i
        i += 1
    return -1


def _extract_executed_commands(command: str) -> list[tuple[int, int]]:
    """Return (start, end) positions of actual command tokens in ``command``.

    On ambiguous tokenisation, the span is treated as an actual command
    position (fail-closed): a false positive (wrong block on a quoted
    reference) is recoverable; a false negative (missed block on a real
    merge) defeats the guard.

    Command-substitution handling (``$(...)`` and ``` `...` ```) descends
    recursively into the body so that a real merge call wrapped in
    substitution (e.g. ``X="$(gh pr merge 42)"``, ``eval "$(gh pr merge 42)"``,
    ``` eval `gh pr merge 42` ```) is still recognised. This preserves
    defense-in-depth parity with a bare-regex implementation while keeping
    the heredoc and quoted-argument exemptions that item 7 of sdlc-1155
    introduced.
    """
    if not command:
        return []

    spans: list[tuple[int, int]] = []
    n = len(command)
    i = 0
    cmd_start = 0
    in_single = False
    in_double = False

    def flush(end: int) -> None:
        if cmd_start < end:
            trimmed_start = cmd_start
            while trimmed_start < end and command[trimmed_start] in " \t":
                trimmed_start += 1
            if trimmed_start < end:
                spans.append((trimmed_start, end))

    def descend(body_start: int, body_end: int) -> None:
        """Recursively tokenize a command-substitution body and append its
        spans (offset into the outer command string)."""
        if body_end <= body_start:
            return
        nested = command[body_start:body_end]
        try:
            for ns, ne in _extract_executed_commands(nested):
                spans.append((body_start + ns, body_start + ne))
        except Exception:
            # Fail-closed: treat the whole body as an actual command position.
            spans.append((body_start, body_end))

    while i < n:
        ch = command[i]

        if ch == "\\" and i + 1 < n:
            i += 2
            continue

        if in_single:
            if ch == "'":
                in_single = False
                i += 1
                cmd_start = i  # resume span after closing quote
                continue
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
                i += 1
                cmd_start = i
                continue
            # Descend into $() inside double quotes -- command substitution
            # is a real execution context and must be scanned.
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                close = _find_dollar_paren_close(command, i + 2)
                if close == -1:
                    # Unterminated substitution: fail-closed -- treat the
                    # rest of the string as a nested command body.
                    descend(i + 2, n)
                    i = n
                    continue
                descend(i + 2, close)
                i = close + 1
                continue
            # Descend into `...` inside double quotes -- also command
            # substitution (older syntax).
            if ch == "`":
                close = _find_backtick_close(command, i + 1)
                if close == -1:
                    descend(i + 1, n)
                    i = n
                    continue
                descend(i + 1, close)
                i = close + 1
                continue
            i += 1
            continue

        if ch == "<" and i + 1 < n and command[i + 1] == "<":
            j = i + 2
            if j < n and command[j] == "-":
                j += 1
            while j < n and command[j] in " \t":
                j += 1
            delim_quote: str | None = None
            if j < n and command[j] in ("'", '"'):
                delim_quote = command[j]
                j += 1
            delim_start = j
            while j < n and command[j] not in " \t\n;|&" and command[j] != delim_quote:
                j += 1
            delim = command[delim_start:j]
            if delim_quote and j < n and command[j] == delim_quote:
                j += 1
            line_end = command.find("\n", j)
            if line_end == -1 or not delim:
                i = j
                continue
            body_start = line_end + 1
            end_pattern = re.compile(r"(?m)^[ \t]*" + re.escape(delim) + r"[ \t]*$")
            m = end_pattern.search(command, body_start)
            # Close the current span BEFORE the heredoc body so the body is
            # not treated as part of the command span; resume the span at
            # the character after the heredoc body (or at end if unclosed).
            flush(line_end)
            if m is None:
                i = n
                cmd_start = n
                continue
            i = m.end()
            cmd_start = i
            continue

        # $(...) at an unquoted position: close the current span, descend
        # into the body, then resume scanning after the close.
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            flush(i)
            close = _find_dollar_paren_close(command, i + 2)
            if close == -1:
                descend(i + 2, n)
                i = n
                cmd_start = n
                continue
            descend(i + 2, close)
            i = close + 1
            cmd_start = i
            continue

        # `...` at an unquoted position: command substitution (older syntax).
        # Descend into the body rather than treating it as opaque.
        if ch == "`":
            flush(i)
            close = _find_backtick_close(command, i + 1)
            if close == -1:
                descend(i + 1, n)
                i = n
                cmd_start = n
                continue
            descend(i + 1, close)
            i = close + 1
            cmd_start = i
            continue

        if ch in ("'", '"'):
            flush(i)  # close current command span before entering quoted region
            if ch == "'":
                in_single = True
            else:
                in_double = True
            i += 1
            continue

        matched_sep = False
        for sep in _COMMAND_SEPARATORS:
            if command.startswith(sep, i):
                flush(i)
                i += len(sep)
                cmd_start = i
                matched_sep = True
                break
        if matched_sep:
            continue

        i += 1

    flush(n)
    return spans


def _merge_cmd_in_command(command: str) -> bool:
    """Return True if the command contains a real merge invocation.

    Uses the tokenizer to restrict the regex to actual command positions.
    On any exception (fail-closed), falls back to the old bare-match on the
    entire command string. An ambiguous parse (empty span list on non-empty
    input) is treated the same way.
    """
    if not command:
        return False
    try:
        spans = _extract_executed_commands(command)
        if not spans:
            return bool(_MERGE_CMD_RE.search(command))
        for start, end in spans:
            segment = command[start:end]
            if _MERGE_CMD_RE.search(segment):
                return True
        return False
    except Exception as exc:
        logger.debug(
            "validate_merge_guard: tokenizer raised (%s); falling back to bare match on %r",
            exc,
            (command[:200] if command else ""),
        )
        return bool(_MERGE_CMD_RE.search(command))


def _command_has_help_flag(command: str) -> bool:
    """Return True if a ``--help`` flag appears at an actual command position."""
    try:
        spans = _extract_executed_commands(command)
    except Exception:
        spans = []
    if not spans:
        return bool(_HELP_FLAG_RE.search(command))
    for start, end in spans:
        if _HELP_FLAG_RE.search(command[start:end]):
            return True
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


def main() -> None:
    data = read_stdin()
    if not data:
        return

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        return

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    if not command:
        return

    # Fast path: commands that begin with echo/printf are diagnostic text.
    stripped = command.strip()
    if stripped.startswith(("echo ", "echo\t", "printf ")):
        return

    if _merge_cmd_in_command(command):
        if _command_has_help_flag(command):
            return
        if _is_authorized(command):
            return
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        "Direct merge call is blocked -- run /do-merge first. "
                        "/do-merge runs the gate checks and, if they pass, "
                        "authorizes this merge automatically. You do NOT need "
                        "human approval; you need the gate to have run. "
                        "Invoke: /do-merge {pr_number}"
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
