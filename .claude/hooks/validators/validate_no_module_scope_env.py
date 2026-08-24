#!/usr/bin/env python3
"""
Regression guard (issue #2866, slice 0): block NEW module-scope environment
reads from being introduced while the existing 190 are drained.

A *module-scope env read* is a call to `os.environ.get`, `os.getenv`,
`os.environ.setdefault`, or `os.environ.pop` written at the top level of a
`.py` file, so it executes the moment the module is first imported. The value
such a module sees is a property of *when the process started*, not of *who is
configuring it*. The fix is a `config/settings.py` field read lazily at call
time — the pattern #1968 established with `TimeoutSettings`.

Detection is AST-based, not regex, and is NOT implemented here: this guard
imports `find_module_scope_env_calls` from `scripts/scan_module_scope_env.py`,
the same function the committed census script runs. One implementation, two
consumers — the guard and the "72 -> 0" number can never disagree about what
counts.

This guard flags:

  - A call to one of the four functions above at module top level, including
    inside a module-level `if` / `try` / `with` / `for` / `while` body (those
    execute at import time too).
  - Only on lines the staged commit actually ADDS or MODIFIES (see below).
    Renamed files are included: `git mv old.py new.py` plus a new read in the
    same commit is a single git `R` entry, and the scan is rename-aware so the
    new read is caught while the moved file's pre-existing sites are not.

It does NOT flag:

  - Reads inside a `def` / `async def` / `class` body — a function body runs
    when called, not when imported, which is the whole point of the fix.
  - Pre-existing module-scope reads on lines the commit does not touch. This
    scoping is load-bearing, not a softening: 188 unmigrated sites live across
    72 modules today, and slices 1-9 of #2866 must edit exactly those files.
    A whole-file guard would block every one of its own migration commits.
    The backlog is tracked by `python scripts/scan_module_scope_env.py`, which
    reports the full census; this guard's job is only to stop the count from
    growing. (The pure `find_violations(content, filename)` core reports every
    site in the file; the diff scoping is applied by the caller.)
  - Test files (tests/, test_*.py, conftest.py, fixtures/) — test setup
    legitimately pokes at the process environment at import time.
  - A call whose source span carries the `# env-scope-guard: allow` marker
    comment.

Allowlist mechanism: append `# env-scope-guard: allow` to the offending line,
with a one-line comment saying why. The escape hatch is narrow by design. Per
the triage criterion in `docs/plans/module-scope-env-reads-migration.md`, a
read may stay at import time only if ALL THREE hold:

  1. pre-config — it runs before `config.settings` is importable, or it
     determines whether/how config loads at all;
  2. launcher-owned — it is set by launchd/systemd/a shell wrapper, not by a
     human tuning `.env`;
  3. cannot vary per-instance by construction.

Three sites in this repo carry the marker today, but only TWO of them are
inside the 190-site census: the `VALOR_LAUNCHD` dotenv-bootstrap gates in
`worker/__main__.py` and `bridge/telegram_bridge.py`. The third,
`config/settings.py`'s `model_config.env_file`, is NOT in the census and never
could be — it sits in a class body (the scan does not descend into those) and
is spelled `__import__("os").environ.get(...)`. It is marked anyway so a later
refactor that lifts it to module scope cannot lose the verdict. So the census
reads 190 sites / 2 allowlisted / 188 to migrate, while `grep` for the marker
finds three. If a read you are
adding does not clear all three, it belongs in `config/settings.py`.

Registration: NOT a `manifest.toml` entry. #2435 consolidated the PreToolUse
Bash validators into one in-process dispatcher; this guard is registered by
appending a tuple to `.claude/hooks/dispatch/pre_tool_use_bash.py`'s
`_PREDICATES`/`_VALIDATORS` list, which calls `find_violation_for_command`
directly.

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, session_id
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing (or exit 0 with no output)

Direct/manual invocation (also used by tests, and available for pre-commit
scripting outside the hook protocol):
  python validate_no_module_scope_env.py <file> [<file> ...]
Exits 1 with an actionable stderr message if any file has a violation, 0
otherwise. The CLI path is deliberately whole-file, not diff-scoped: pointed
at a single module it answers "is this module clean yet?", which is what a
migration slice wants to ask.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# The detector lives with the census script so both consumers share one
# implementation (issue #2866). `scripts/` is added to sys.path rather than the
# repo root on purpose: importing the `scripts` package is cheap, but importing
# the `tools` package arms the Redis flush guard, and a hook has no business
# paying for that.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from scan_module_scope_env import (  # noqa: E402
    ALLOW_MARKER as _SCAN_ALLOW_MARKER,
)
from scan_module_scope_env import (  # noqa: E402
    find_module_scope_env_calls,
    is_test_file,
)

ALLOW_MARKER = _SCAN_ALLOW_MARKER

_SUBPROCESS_TIMEOUT_S = 10

# `@@ -old,count +new,count @@` — we only care about the new-side range, which
# is the set of lines the commit adds or rewrites.
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# `+++ b/<path>` — the new-side path of the file the following hunks belong to.
# A deletion emits `+++ /dev/null`, which this deliberately does not match.
_DIFF_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$")

__all__ = [
    "ALLOW_MARKER",
    "find_violations",
    "find_violation_for_command",
    "is_test_file",
]


def find_violations(
    content: str,
    filename: str,
    changed_lines: set[int] | None = None,
) -> list[str]:
    """Return actionable violation messages for module-scope env reads.

    Pure: no git, no filesystem, no environment. Returns an empty list for a
    compliant file, a file with no env reads at all, and a file that does not
    parse.

    `changed_lines`, when given, restricts the result to reads on those
    (1-based) line numbers — the hook path passes the staged diff's new-side
    ranges so pre-existing sites in a touched file are not re-litigated. When
    None (the CLI path, and every direct test), every module-scope read in the
    file is reported.
    """
    violations: list[str] = []
    for call in find_module_scope_env_calls(content, filename):
        if call.allowed:
            continue
        if changed_lines is not None and call.line not in changed_lines:
            continue
        key = f'"{call.key}"' if call.key else "<computed>"
        violations.append(
            f"{filename}:{call.line}: module-scope env read `{call.func}({key})` "
            f"executes at import time — {call.source_line}\n"
            f"  Add a field to config/settings.py (see TimeoutSettings and "
            f"docs/features/config-timeout-catalog.md) and read it at call time via "
            f"`settings.<section>.<field>`, or move the read inside the function that "
            f"needs it. If this read genuinely must run before config is loadable and "
            f"is set by the launcher (not a human tuning .env), add "
            f"`# {ALLOW_MARKER}` on this line with a one-line justification."
        )
    return violations


def _git(args: list[str]) -> str | None:
    """Run a git command, returning stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _staged_python_files() -> list[str]:
    """Non-test `*.py` paths this commit adds, copies, modifies, or renames.

    `R` is in the filter deliberately. Git detects a rename whenever the
    content is similar enough, so `git mv old.py new.py` plus a newly added
    module-scope env read in the same commit lands as a single `R` entry — and
    an `ACM`-only filter would not scan that file at all, letting the read
    through silently.
    """
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    if out is None:
        return []
    return [f for f in out.strip().split("\n") if f.endswith(".py") and not is_test_file(f)]


def _staged_content(path: str) -> str | None:
    """Read the staged (index) version of `path`, not the working-tree copy."""
    return _git(["show", f":{path}"])


def _staged_added_lines_map() -> dict[str, set[int]]:
    """Map new-side path -> line numbers this commit adds or rewrites.

    Parsed in ONE rename-aware pass (`git diff --cached -U0 -M`) rather than a
    per-path call, because rename detection and path limiting are mutually
    exclusive in git: passing `-- <newpath>` makes git forget the file was
    renamed and report the whole file as freshly added, which would flag every
    pre-existing site in a file that was merely moved. Diffing the whole staged
    set keeps the rename linked to its source, so a pure `git mv` yields no
    changed lines at all and only genuinely new lines are reported.

    On any git failure the caller gets an empty map, which fails OPEN (no
    violations reported) — consistent with the dispatcher's fail-open posture
    for this validator.
    """
    out = _git(["diff", "--cached", "-U0", "-M", "--diff-filter=ACMR"])
    if out is None:
        return {}
    per_path: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in out.splitlines():
        header = _DIFF_HEADER_RE.match(line)
        if header:
            # `+++ b/<path>` names the new side; `/dev/null` for a deletion.
            current = per_path.setdefault(header.group(1), set())
            continue
        match = _HUNK_RE.match(line)
        if not match or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        current.update(range(start, start + count))
    return per_path


def read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _header() -> str:
    return "BLOCKED: new module-scope env read(s) — these freeze config at import time.\n\n"


def _footer() -> str:
    return (
        "\n\nPromote the value to a config/settings.py field read lazily at call time "
        "(the #1968 TimeoutSettings pattern), or move the read into the function that "
        f"needs it. For a genuine pre-config, launcher-owned bootstrap read, add "
        f"`# {ALLOW_MARKER}` on the offending line with a one-line justification.\n"
        "Run `python scripts/scan_module_scope_env.py` to see the full remaining census."
    )


def find_violation_for_command(command: str) -> str | None:
    """Pure predicate: given the Bash `command` string, return a block-reason
    string if it is a `git commit` that introduces a new module-scope env read
    in a staged Python file, else None. Called directly by the in-process
    dispatcher (.claude/hooks/dispatch/pre_tool_use_bash.py), bypassing the
    stdin/exit protocol. Never raises for well-formed input.
    """
    if not command or "git commit" not in command:
        return None

    all_violations: list[str] = []
    added_lines = _staged_added_lines_map()
    for path in _staged_python_files():
        content = _staged_content(path)
        if content is None:
            continue
        all_violations.extend(find_violations(content, path, added_lines.get(path, set())))

    if all_violations:
        return _header() + "\n\n".join(all_violations) + _footer()
    return None


def _run_hook() -> None:
    """PreToolUse (Bash) hook path, retained for standalone invocation even
    though the dispatcher normally calls `find_violation_for_command` directly.
    """
    hook_input = read_stdin()
    if hook_input.get("tool_name") != "Bash":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    reason = find_violation_for_command(command)
    if reason:
        block(reason)

    sys.exit(0)


def _run_cli(files: list[str]) -> None:
    """Direct-invocation path: validate the given files whole (no git, no diff
    scoping). Used by tests and by migration slices asking "is this module
    clean yet?".
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
        print(_header() + "\n\n".join(all_violations), file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    argv_files = sys.argv[1:]
    if argv_files:
        _run_cli(argv_files)
    else:
        _run_hook()


if __name__ == "__main__":
    main()
