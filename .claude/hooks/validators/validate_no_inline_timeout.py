#!/usr/bin/env python3
"""
Regression guard (issue #1968, Task 4): block new inline `timeout=<literal>`
magic numbers in subprocess/HTTP-client calls.

This guard exists so the centralize_config_magic_literals cleanup does not
silently grow back. It flags bare numeric `timeout=` literals in:

  - subprocess.run(...), subprocess.Popen(...), bare Popen(...)
  - requests.get/post/put/patch/delete/head/request(...)
  - httpx.get/post/put/patch/delete/head/request(...) (same call shape)

It does NOT flag:

  - timeout=settings.timeouts.<anything> (a settings reference)
  - timeout=SOME_NAMED_CONSTANT (or any other identifier/attribute reference)
    — only bare numeric literals are magic; named references are the fix.
  - Test files (tests/, test_*.py, conftest.py, fixtures/) — fixtures and
    test setup legitimately use literal timeouts.
  - A line carrying the `# timeout-guard: allow` marker comment (allowlist
    escape hatch for a genuinely local one-off that doesn't warrant a
    settings field — see module docstring for rationale).

Wired in as a PreToolUse hook (matcher: Bash) in .claude/settings.json,
mirroring validate_commit_message.py / validate_merge_guard.py: it fires only
on `git commit` Bash commands, inspects the staged Python files via
`git diff --cached`, and blocks the commit with an actionable message
(file:line + suggested fix) if any staged file introduces a bare timeout
literal.

Allowlist mechanism: append `# timeout-guard: allow` to the offending line.

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, session_id
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing (or exit 0 with no output)

Direct/manual invocation (also used by tests, and available for pre-commit
scripting outside the hook protocol):
  python validate_no_inline_timeout.py <file> [<file> ...]
Exits 1 with an actionable stderr message if any file has a violation, 0
otherwise (including for files with zero timeout literals).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "timeout-guard: allow"

# Call sites this guard cares about: subprocess process spawns and the common
# HTTP-client libraries. Matches `subprocess.run(`, `subprocess.Popen(`,
# a bare `Popen(` (imported via `from subprocess import Popen`), and the
# requests/httpx verb methods (same call shape, same "wedges the caller if
# uncapped" risk).
_CALL_START_RE = re.compile(
    r"\b(?:subprocess\.(?:run|Popen)|Popen|requests\.(?:get|post|put|patch|delete|head|request)"
    r"|httpx\.(?:get|post|put|patch|delete|head|request))\s*\("
)

# A bare numeric literal timeout: `timeout=10`, `timeout=10.0`, `timeout=-1`.
# Deliberately does NOT match `timeout=settings.timeouts.x` or
# `timeout=SOME_CONSTANT` or `timeout=some_var` — those start with a letter
# or underscore, never a digit or sign+digit, so this pattern simply never
# matches an identifier/attribute reference.
_TIMEOUT_LITERAL_RE = re.compile(r"\btimeout\s*=\s*(-?\d+(?:\.\d+)?)\b")

_TEST_DIR_COMPONENTS = ("tests", "fixtures")


def is_test_file(path: str) -> bool:
    """Return True if `path` looks like a test/fixture file we should skip.

    Checks path *components* (basename + directory segments), not a raw
    substring match — a substring check on the full path would false-positive
    on e.g. pytest's own ``tmp_path`` fixture dirs, which are themselves named
    after the test function (``.../test_something0/bad.py`` contains the
    substring ``/test_`` despite ``bad.py`` not being a test file at all).
    """
    normalized = path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return False
    basename = parts[-1]
    if basename.startswith("test_") or basename == "conftest.py":
        return True
    dir_parts = parts[:-1]
    return any(marker in dir_parts for marker in _TEST_DIR_COMPONENTS)


def _find_call_windows(content: str):
    """Yield (start, end) character spans for each relevant call, balanced on
    parens so multi-line calls are captured whole (not just their first line).
    """
    for m in _CALL_START_RE.finditer(content):
        depth = 1
        i = m.end()
        n = len(content)
        while i < n and depth > 0:
            ch = content[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        yield m.start(), i


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _line_text(content: str, offset: int) -> str:
    line_start = content.rfind("\n", 0, offset) + 1
    line_end = content.find("\n", offset)
    if line_end == -1:
        line_end = len(content)
    return content[line_start:line_end]


def find_violations(content: str, filename: str) -> list[str]:
    """Return actionable violation messages for offending bare-literal
    timeouts in `content`. Returns an empty list if there are none (this
    includes files with no timeout literals at all, and files where the only
    `timeout=` usages are settings/constant references).
    """
    violations: list[str] = []
    for start, end in _find_call_windows(content):
        window = content[start:end]
        for tm in _TIMEOUT_LITERAL_RE.finditer(window):
            abs_offset = start + tm.start()
            line_text = _line_text(content, abs_offset)
            if ALLOW_MARKER in line_text:
                continue
            line_no = _line_number(content, abs_offset)
            violations.append(
                f"{filename}:{line_no}: inline timeout literal `timeout={tm.group(1)}` "
                f"in a subprocess/HTTP-client call — {line_text.strip()}\n"
                f"  Use settings.timeouts.<field> (config/settings.py TimeoutSettings) "
                f"or a named module-level constant instead of a bare numeric literal. "
                f"If this is a genuinely local one-off, add `# {ALLOW_MARKER}` on this line."
            )
    return violations


def _staged_python_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        f
        for f in result.stdout.strip().split("\n")
        if f.endswith(".py") and f and not is_test_file(f)
    ]


def _staged_content(path: str) -> str | None:
    """Read the staged (index) version of `path`, not the working-tree copy."""
    try:
        result = subprocess.run(
            ["git", "show", f":{path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _run_hook() -> None:
    """PreToolUse (Bash) hook path: only fires on `git commit` commands,
    inspects staged Python files, blocks the commit with an actionable
    message if a bare timeout literal was introduced.
    """
    hook_input = read_stdin()
    if hook_input.get("tool_name") != "Bash":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command or "git commit" not in command:
        sys.exit(0)

    all_violations: list[str] = []
    for f in _staged_python_files():
        content = _staged_content(f)
        if content is None:
            continue
        all_violations.extend(find_violations(content, f))

    if all_violations:
        block(
            "BLOCKED: new inline timeout literal(s) in subprocess/HTTP-client calls.\n\n"
            + "\n\n".join(all_violations)
            + "\n\nUse settings.timeouts.<field> (config/settings.py TimeoutSettings) "
            "or a named constant. For a genuinely local one-off, add "
            f"`# {ALLOW_MARKER}` on the offending line."
        )

    sys.exit(0)


def _run_cli(files: list[str]) -> None:
    """Direct-invocation path: validate the given files as-is (no git
    required). Used by tests and available for manual/scripted use.
    """
    all_violations: list[str] = []
    for f in files:
        if is_test_file(f):
            continue
        try:
            content = Path(f).read_text(encoding="utf-8")
        except OSError as e:
            print(f"ERROR: cannot read {f}: {e}", file=sys.stderr)
            sys.exit(2)
        all_violations.extend(find_violations(content, f))

    if all_violations:
        print(
            "BLOCKED: new inline timeout literal(s) in subprocess/HTTP-client calls.\n\n"
            + "\n\n".join(all_violations),
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


def main():
    argv_files = sys.argv[1:]
    if argv_files:
        _run_cli(argv_files)
    else:
        _run_hook()


if __name__ == "__main__":
    main()
