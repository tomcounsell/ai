#!/usr/bin/env python3
"""
Merge guard: blocks direct `g h  p r  m e r g e` calls unless the live merge
predicate passes (issue #2003).

Enforcement contract (replaces the pre-#2003 "auth file exists" check, which
proved someone created a file, not that the gate ran — see the PR #2005
bypass incident):

1. A detected real merge command with a PR number FIRST checks the
   break-glass override file ``data/merge_authorized_{pr}``: if it exists AND
   contains a line matching ``override: <reason>`` (non-empty reason), the
   merge is ALLOWED, logged at WARNING, and a ``merge_guard.override_used``
   metric is emitted. An empty or legacy-format file (no ``override:`` line)
   is treated as ABSENT — it never authorizes anything.
2. With no valid override, the hook evaluates the shared terminal merge
   predicate (``tools.merge_predicate.evaluate_merge_predicate``) — the same
   helper the /do-merge skill consumes, so hook and skill cannot drift. The
   predicate covers PR state (OPEN/MERGEABLE/CLEAN/CI-green/issue link), the
   DOCS stage gate, and REVIEW-verdict freshness against the PR head commit.
3. Predicate allowed → ALLOW silently. Predicate failed → BLOCK, naming the
   exact failed leg(s). Predicate evaluation raises → BLOCK (fail-closed)
   with an actionable message.
4. A merge command with no extractable PR number → BLOCK with the generic
   /do-merge message (the predicate cannot be evaluated without a PR number).

/do-merge no longer creates/deletes the auth file on the happy path — the
hook allows the merge because the predicate passes live. This hook does NOT
require human-in-the-loop; it requires the predicate to hold.

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

# Repo root (hook lives at .claude/hooks/validators/) and the break-glass
# override directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _REPO_ROOT / "data"

# Break-glass override format: a line containing `override: <reason>` with a
# non-empty reason. Anything else (empty file, legacy touch-file content) is
# treated as absent.
_OVERRIDE_LINE_RE = re.compile(r"override:\s*(\S[^\n]*)")


def _read_override(pr_number: int) -> tuple[str, str | None]:
    """Classify the override file for a PR.

    Returns ``(status, reason)`` where status is one of:
    - ``"absent"``: no file
    - ``"valid"``: file contains an ``override: <reason>`` line (reason returned)
    - ``"invalid"``: file exists but has no override line (empty/legacy format)
    """
    auth_file = _DATA_DIR / f"merge_authorized_{pr_number}"
    if not auth_file.exists():
        return "absent", None
    try:
        content = auth_file.read_text()
    except OSError:
        return "invalid", None
    match = _OVERRIDE_LINE_RE.search(content)
    if match:
        return "valid", match.group(1).strip()
    return "invalid", None


def _load_metric_recorder():
    """Lazily import analytics.collector.record_metric. Raises on failure."""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from analytics.collector import record_metric

    return record_metric


def _emit_override_metric(pr_number: int, reason: str) -> None:
    """Emit ``merge_guard.override_used``. Metric failure never crashes the hook."""
    try:
        recorder = _load_metric_recorder()
        recorder(
            "merge_guard.override_used",
            1,
            dimensions={"pr_number": str(pr_number), "reason": reason},
        )
    except Exception as exc:
        logger.warning("merge_guard: override metric emission failed: %s", exc)


def _evaluate_predicate(pr_number: int):
    """Evaluate the shared merge predicate. Raises on import/eval failure.

    Imports via sys.path insertion of the repo root — robust to hooks running
    with cwd anywhere in the repo. ``tools.merge_predicate`` is stdlib-only at
    module level, so the import works under any interpreter.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from tools.merge_predicate import evaluate_merge_predicate

    return evaluate_merge_predicate(pr_number)


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


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

    if not _merge_cmd_in_command(command):
        return
    if _command_has_help_flag(command):
        return

    match = _PR_NUMBER_RE.search(command)
    if not match:
        # Fail-closed: without a PR number the predicate cannot be evaluated.
        _block(
            "Direct merge call is blocked -- run /do-merge first. "
            "/do-merge drives the gate; the merge-guard hook evaluates the "
            "live merge predicate and could not extract a PR number from "
            "this command. Invoke: /do-merge {pr_number}"
        )
        return
    pr_number = int(match.group(1))

    override_status, override_reason = _read_override(pr_number)
    if override_status == "valid":
        logger.warning(
            "merge_guard: break-glass override accepted for PR #%s (reason: %s)",
            pr_number,
            override_reason,
        )
        _emit_override_metric(pr_number, override_reason or "")
        return

    override_note = ""
    if override_status == "invalid":
        override_note = (
            f" Note: data/merge_authorized_{pr_number} exists but has no"
            " 'override: <reason>' line — empty/legacy auth files are treated"
            " as absent and authorize nothing."
        )

    remediation = (
        f" Run /do-merge {pr_number} to drive the gate, or break-glass:"
        f" write 'override: <reason>' to data/merge_authorized_{pr_number}."
    )

    try:
        result = _evaluate_predicate(pr_number)
    except Exception as exc:
        _block(
            "Merge blocked (fail-closed): merge-predicate evaluation failed"
            f" ({exc.__class__.__name__}: {exc})." + remediation + override_note
        )
        return

    if result.allowed:
        return
    legs = "; ".join(result.failed_checks) or "unspecified predicate failure"
    _block(f"Merge blocked — failed predicate check(s): {legs}." + remediation + override_note)


if __name__ == "__main__":
    main()
