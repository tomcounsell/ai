#!/usr/bin/env python3
"""Reproducible AST scan for module-scope environment reads (issue #2866).

A *module-scope env read* is a call to one of::

    os.environ.get   os.getenv   os.environ.setdefault   os.environ.pop

written at the **top level** of a ``.py`` file, so it executes the moment the
module is first imported rather than each time a function runs. The value such
a module sees is a property of *when the process started*, not of *who is
configuring it* — which is why the whole class is being migrated onto
``config/settings.py`` fields read lazily at call time (the #1968 precedent).

This module is the single implementation of the detector. It has exactly two
consumers:

  1. this file's own CLI (``python scripts/scan_module_scope_env.py``), which
     prints the repo-wide census so "72 -> 0" is a number anyone can
     regenerate at any point in the migration;
  2. ``.claude/hooks/validators/validate_no_module_scope_env.py``, the
     PreToolUse regression guard, which imports
     :func:`find_module_scope_env_calls` so the guard and the census can never
     disagree about what counts.

Detection is **AST-based, not regex.** The module-scope vs. ``def``/``class``
-body distinction is precisely what a regex cannot express: a regex sees
``os.environ.get("X")`` identically whether it sits at column 0 or nested four
frames deep inside a method. ``validate_no_inline_timeout.py`` (the shape this
scan's sibling guard is modelled on) concedes an AST check as future work for
exactly this reason.

What the walk descends into and what it skips
---------------------------------------------
Descends into module-level ``if`` / ``try`` / ``with`` / ``for`` / ``while``
bodies: those execute at import time, so a read inside one is still frozen at
import. Does **not** descend into ``def`` / ``async def`` / ``class`` bodies:
a function body runs when called, and a class body's reads are a different
(rarer) shape that the baseline census deliberately excludes.

Known limitation — indirect import-time reads
---------------------------------------------
The scan is **syntactic**. It cannot see an import-time env read made
*indirectly* through a function call. ``config/settings.py`` calls
``stale_granite_env_keys()`` at module scope; that function reads
``os.environ`` internally, so the read genuinely happens at import time and
this scan is blind to it. A future "0 module-scope reads" result therefore
proves the *syntactic* class is drained, NOT that no import-time env read
remains. Do not present ``72 -> 0`` as proof the defect class is eliminated.

Corpus
------
Git-tracked ``*.py`` only, via ``git ls-files``. This is load-bearing: walking
the filesystem instead sweeps ``.worktrees/`` and ``.claude/worktrees/`` and
inflates the census from 72 modules to 4768.

Baseline at ``22cb19025``: 72 non-test modules / 190 call sites (79 / 202
including tests). Treat a mismatch as a bug in this script, not in the
baseline.

Usage::

    python scripts/scan_module_scope_env.py              # non-test census
    python scripts/scan_module_scope_env.py --tests      # include test files
    python scripts/scan_module_scope_env.py --by-file    # per-file breakdown
    python scripts/scan_module_scope_env.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The four call shapes that read the process environment. `setdefault` and
# `pop` currently have zero module-scope occurrences, but they are the same
# defect (and `setdefault` in particular is the shape CLAUDE.md already calls
# out as silently keeping a production REDIS_URL), so the detector covers all
# four rather than only the two in active use.
ENV_READ_FUNCS: tuple[str, ...] = (
    "os.environ.get",
    "os.getenv",
    "os.environ.setdefault",
    "os.environ.pop",
)

# Escape hatch for a read that legitimately belongs at import time. See the
# triage criterion in docs/plans/module-scope-env-reads-migration.md: all three
# of pre-config / launcher-owned / cannot-vary-per-instance must hold.
ALLOW_MARKER = "env-scope-guard: allow"

# Node types whose bodies do NOT execute at import time.
_DEFERRED_BODY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_TEST_DIR_COMPONENTS = ("tests", "fixtures")

# `git ls-files` over a repo this size returns in well under a second; the cap
# exists only so a wedged git (index.lock contention with a parallel lane)
# surfaces as an error instead of hanging the census. Sized as "absurdly
# generous", not tuned — no one should ever need to change it.
_GIT_LS_FILES_TIMEOUT_S = 60


@dataclass(frozen=True)
class EnvCall:
    """One module-scope environment read."""

    filename: str
    line: int
    col: int
    func: str
    key: str | None
    allowed: bool
    source_line: str


def is_test_file(path: str) -> bool:
    """Return True if `path` looks like a test/fixture file.

    Checks path *components* (basename + directory segments), not a raw
    substring match — a substring check false-positives on e.g. pytest's
    ``tmp_path`` dirs, which are named after the test function
    (``.../test_something0/bad.py`` contains ``/test_`` despite ``bad.py`` not
    being a test file at all).
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    basename = parts[-1]
    if basename.startswith("test_") or basename == "conftest.py":
        return True
    return any(marker in parts[:-1] for marker in _TEST_DIR_COMPONENTS)


def _dotted_name(node: ast.AST) -> str | None:
    """Render an attribute/name chain as a dotted string, else None.

    ``os.environ.get`` -> ``"os.environ.get"``. Anything with a non-Name,
    non-Attribute base (a subscript, a call, a literal) returns None.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _literal_key(call: ast.Call) -> str | None:
    """Return the env var name if the first positional arg is a string literal."""
    if call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        if isinstance(value, str):
            return value
    return None


class _ModuleScopeEnvVisitor(ast.NodeVisitor):
    """Collect env-read calls reachable at import time.

    Descends into module-level control flow (``if``/``try``/``with``/``for``/
    ``while``) because those bodies run at import; refuses to descend into
    ``def``/``async def``/``class`` bodies because those do not.
    """

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _dotted_name(node.func) in ENV_READ_FUNCS:
            self.calls.append(node)
        self.generic_visit(node)


def find_module_scope_env_calls(content: str, filename: str = "<unknown>") -> list[EnvCall]:
    """Return every module-scope env read in `content`, in source order.

    Returns an empty list for a file with no such reads, and also for a file
    that does not parse — a syntax error is the type checker's problem, not
    this detector's, and a guard that blocked on unparseable input would fire
    on every mid-edit save.

    Results include allowlisted sites, flagged via ``EnvCall.allowed``. The
    census counts them (so the published baseline stays comparable across the
    migration); the regression guard filters them out.
    """
    try:
        tree = ast.parse(content, filename=filename)
    except SyntaxError:
        return []

    visitor = _ModuleScopeEnvVisitor()
    visitor.visit(tree)

    lines = content.splitlines()
    results: list[EnvCall] = []
    for call in visitor.calls:
        source_line = lines[call.lineno - 1] if 0 < call.lineno <= len(lines) else ""
        # A multi-line call may carry the marker on any of its lines; the
        # natural place a human puts it is the line with the closing paren or
        # the line with the key, so check the whole span.
        end_line = getattr(call, "end_lineno", call.lineno) or call.lineno
        span = "\n".join(lines[call.lineno - 1 : end_line])
        results.append(
            EnvCall(
                filename=filename,
                line=call.lineno,
                col=call.col_offset,
                func=_dotted_name(call.func) or "",
                key=_literal_key(call),
                allowed=ALLOW_MARKER in span,
                source_line=source_line.strip(),
            )
        )
    results.sort(key=lambda c: (c.line, c.col))
    return results


def git_tracked_python_files(repo_root: Path) -> list[str]:
    """Git-tracked ``*.py`` paths, repo-relative.

    ``git ls-files`` rather than a filesystem walk: nested worktrees under
    ``.worktrees/`` and ``.claude/worktrees/`` are untracked full checkouts of
    this same repo, and sweeping them inflates the census ~66x.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
        timeout=_GIT_LS_FILES_TIMEOUT_S,
    )
    return [p for p in result.stdout.split("\0") if p]


@dataclass
class ScanResult:
    """Aggregate census over a corpus of files."""

    calls: list[EnvCall] = field(default_factory=list)

    @property
    def modules(self) -> list[str]:
        return sorted({c.filename for c in self.calls})

    @property
    def module_count(self) -> int:
        return len(self.modules)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def by_package(self) -> dict[str, tuple[int, int]]:
        """``{top_level_package: (module_count, call_count)}``."""
        packages: dict[str, set[str]] = {}
        counts: dict[str, int] = {}
        for call in self.calls:
            pkg = call.filename.split("/")[0] if "/" in call.filename else "."
            packages.setdefault(pkg, set()).add(call.filename)
            counts[pkg] = counts.get(pkg, 0) + 1
        return {pkg: (len(files), counts[pkg]) for pkg, files in sorted(packages.items())}

    def by_function(self) -> dict[str, int]:
        counts = {name: 0 for name in ENV_READ_FUNCS}
        for call in self.calls:
            counts[call.func] = counts.get(call.func, 0) + 1
        return counts

    def by_file(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for call in self.calls:
            counts[call.filename] = counts.get(call.filename, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def scan_repo(repo_root: Path, include_tests: bool = False) -> ScanResult:
    """Run the detector over every git-tracked ``*.py`` file in `repo_root`."""
    result = ScanResult()
    for rel_path in git_tracked_python_files(repo_root):
        if not include_tests and is_test_file(rel_path):
            continue
        try:
            content = (repo_root / rel_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        result.calls.extend(find_module_scope_env_calls(content, rel_path))
    return result


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _format_report(result: ScanResult, by_file: bool) -> str:
    lines: list[str] = []
    allowlisted = sum(1 for c in result.calls if c.allowed)
    lines.append(
        f"module-scope env reads: {result.module_count} modules / "
        f"{result.call_count} call sites "
        f"({allowlisted} allowlisted, {result.call_count - allowlisted} to migrate)"
    )
    lines.append("")
    lines.append("by package:")
    for pkg, (mods, calls) in result.by_package().items():
        lines.append(f"  {pkg:<12} {mods:>3} modules / {calls:>3} calls")
    lines.append("")
    lines.append("by function:")
    for name, count in result.by_function().items():
        lines.append(f"  {name:<24} {count:>3}")
    if by_file:
        lines.append("")
        lines.append("by file:")
        for filename, count in result.by_file().items():
            lines.append(f"  {count:>3}  {filename}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Census of module-scope environment reads (issue #2866).",
    )
    parser.add_argument(
        "--tests", action="store_true", help="include test files (default: exclude)"
    )
    parser.add_argument("--by-file", action="store_true", help="print a per-file breakdown")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    result = scan_repo(_repo_root(), include_tests=args.tests)

    if args.json:
        print(
            json.dumps(
                {
                    "modules": result.module_count,
                    "calls": result.call_count,
                    "allowlisted": sum(1 for c in result.calls if c.allowed),
                    "by_package": {k: list(v) for k, v in result.by_package().items()},
                    "by_function": result.by_function(),
                    "by_file": result.by_file(),
                    "sites": [
                        {
                            "file": c.filename,
                            "line": c.line,
                            "func": c.func,
                            "key": c.key,
                            "allowed": c.allowed,
                        }
                        for c in result.calls
                    ],
                },
                indent=2,
            )
        )
    else:
        print(_format_report(result, by_file=args.by_file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
