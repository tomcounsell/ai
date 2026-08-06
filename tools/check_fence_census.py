#!/usr/bin/env python3
"""Anti-criterion: every consumer of a fenced pid must guard it (issue #2518).

PR #2516 introduced ``agent.pid_fence.fence_is_live`` and applied it at nine
decision sites. At six others it rebound the pid source to ``fence.get("pid")``
and discarded the ``create_time`` sitting in the same dict. Every one of those
six kept compiling and kept passing tests, because the unguarded shape is still
valid code — just wrong. Review APPROVEd the PR twice.

Nothing mechanically enumerated "every consumer of a fenced pid", so this does.

What it checks
--------------
**Per-site, function-scoped adjacency — never an occurrence count.** A count
threshold (``grep -c 'fence_is_live' … > 11``) is satisfied by any fenced site,
so a future change that adds one unguarded consumer while removing one guarded
site leaves the count unchanged and green. That is the #2516 failure mode
verbatim. Adjacency is the load-bearing property a count cannot express.

For each function body (and module top level) the checker:

1. finds every read of ``.get("pid")`` off an expression rooted in
   ``live_fence`` — either directly, e.g.
   ``(getattr(entry, "live_fence", None) or {}).get("pid")``, or through a
   local name bound to such an expression, e.g.
   ``fence = getattr(entry, "live_fence", None)`` … ``fence.get("pid")``;
2. requires that the SAME function body either calls a guard predicate
   (``fence_is_live`` / ``create_times_match``) **or** reads
   ``.get("create_time")`` off that same fence;
3. reports the ``file:line`` of every read that satisfies neither.

Why "or reads create_time" counts
---------------------------------
The defect class is precise: #2516 rebound the pid source to
``fence.get("pid")`` and **discarded the ``create_time`` sitting in the same
dict**. All six unguarded consumers had that exact shape. A scope that keeps
both halves is by construction not in that class — it is plumbing, forwarding
identity onward (``ui/data/sdlc.py::_session_to_pipeline`` hands both to
``_check_process_alive``). The decision that pid ultimately feeds lives in some
other function, and that function is checked independently by this same rule,
so nothing escapes by being passed one frame down.

The limitation is real and worth stating: a scope could read ``create_time``
and then ignore it. That is a weaker failure than dropping it on the floor, it
is visible in review because the value is bound and unused, and ruff's unused
-variable rules catch the simple form. Tightening further means dataflow
analysis, which this checker deliberately is not.

Exemption
---------
The single exemption mechanism is the exact marker

    # fence-census: log-only, not a decision consumer

on the read's own line or the line immediately above it. It is for reads that
interpolate a pid into a log or reason string and drive no kill, reprieve, or
ownership claim. Any new exemption is a deliberate annotation at the site,
visible in the diff and reviewable — not a silently rising threshold.

Usage:
    python tools/check_fence_census.py            # exit 0 clean, 1 on violations
    python tools/check_fence_census.py --list     # also list the guarded sites
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories scanned for fenced-pid consumers. Tests are excluded on purpose:
#: a test may construct an unguarded fence read as a fixture.
SCAN_DIRS = ("agent", "models", "scripts", "ui", "worker", "bridge", "tools")

#: Predicates that constitute a guard. Both live in ``agent/pid_fence.py``;
#: ``create_times_match`` is the seam for a consumer that already holds an
#: observed ``create_time`` and must not re-read psutil.
GUARD_FUNCS = frozenset({"fence_is_live", "create_times_match"})

EXEMPTION_MARKER = "fence-census: log-only, not a decision consumer"

FENCE_ATTR = "live_fence"


def _expr_mentions_fence(node: ast.AST) -> bool:
    """True if ``node`` reads ``live_fence`` anywhere inside it."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == FENCE_ATTR:
            return True
        # getattr(entry, "live_fence", None)
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "getattr"
            and len(sub.args) >= 2
            and isinstance(sub.args[1], ast.Constant)
            and sub.args[1].value == FENCE_ATTR
        ):
            return True
    return False


def _is_dict_get(node: ast.AST, key: str) -> bool:
    """True if ``node`` is a ``<something>.get("<key>")`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) >= 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == key
    )


def _direct_children(body: list[ast.stmt]) -> list[ast.AST]:
    """Every node under ``body``, excluding nested function/class bodies.

    Nested scopes are checked as their own units, so a guard inside a closure
    does not vouch for a read in the enclosing function (or vice versa).
    """
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    out: list[ast.AST] = []
    stack: list[ast.AST] = [n for n in body if not isinstance(n, nested)]
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, nested):
                continue
            stack.append(child)
    return out


def _scope_bodies(tree: ast.Module) -> list[tuple[str, list[ast.stmt]]]:
    """(scope_name, body) for the module top level and every function."""
    scopes: list[tuple[str, list[ast.stmt]]] = [("<module>", tree.body)]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scopes.append((node.name, node.body))
    return scopes


def _exempt_lines(source: str) -> set[int]:
    """Line numbers covered by the exemption marker (its own line and the next)."""
    covered: set[int] = set()
    for i, line in enumerate(source.splitlines(), start=1):
        if EXEMPTION_MARKER in line:
            covered.add(i)
            covered.add(i + 1)
    return covered


def check_file(path: Path) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """Return (violations, guarded_sites) for one file.

    violations: (lineno, scope_name, source_line)
    guarded_sites: (lineno, scope_name)
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    if FENCE_ATTR not in source:
        return [], []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # a file that does not parse is not this tool's problem
        print(f"WARN: skipping unparseable {path}: {exc}", file=sys.stderr)
        return [], []

    lines = source.splitlines()
    exempt = _exempt_lines(source)
    violations: list[tuple[int, str, str]] = []
    guarded: list[tuple[int, str]] = []

    for scope_name, body in _scope_bodies(tree):
        nodes = _direct_children(body)

        # Names bound to a fence expression within this scope.
        fence_names: set[str] = set()
        for node in nodes:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            if not targets or getattr(node, "value", None) is None:
                continue
            if not _expr_mentions_fence(node.value):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    fence_names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            fence_names.add(elt.id)

        def _rooted_in_fence(receiver: ast.AST, _names: set[str] = fence_names) -> bool:
            return _expr_mentions_fence(receiver) or (
                isinstance(receiver, ast.Name) and receiver.id in _names
            )

        calls_guard = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in GUARD_FUNCS)
                or (isinstance(node.func, ast.Attribute) and node.func.attr in GUARD_FUNCS)
            )
            for node in nodes
        )
        # Keeping the other half of the fence is the alternative to guarding:
        # the defect class is discarding it, not forwarding it. See the module
        # docstring for why this is sound and where it stops.
        keeps_create_time = any(
            _is_dict_get(node, "create_time") and _rooted_in_fence(node.func.value)  # type: ignore[union-attr]
            for node in nodes
        )
        has_guard = calls_guard or keeps_create_time

        for node in nodes:
            if not _is_dict_get(node, "pid"):
                continue
            receiver = node.func.value  # type: ignore[union-attr]
            if not _rooted_in_fence(receiver):
                continue
            lineno = node.lineno
            if lineno in exempt:
                continue
            if has_guard:
                guarded.append((lineno, scope_name))
                continue
            src_line = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            violations.append((lineno, scope_name, src_line))

    return violations, guarded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="also list the guarded consumer sites")
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="repository root to scan (default: this script's repo)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    all_violations: list[tuple[Path, int, str, str]] = []
    all_guarded: list[tuple[Path, int, str]] = []

    for scan_dir in SCAN_DIRS:
        base = root / scan_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            violations, guarded = check_file(path)
            rel = path.relative_to(root)
            all_violations.extend((rel, ln, scope, src) for ln, scope, src in violations)
            all_guarded.extend((rel, ln, scope) for ln, scope in guarded)

    if args.list:
        print(f"Guarded fenced-pid consumers ({len(all_guarded)}):")
        for rel, lineno, scope in all_guarded:
            print(f"  {rel}:{lineno}  in {scope}()")
        print()

    if all_violations:
        print(f"FENCE CENSUS FAILED — {len(all_violations)} unguarded fenced-pid consumer(s).")
        print()
        print("Each site below reads a pid off a live_fence and DISCARDS the create_time")
        print("sitting in the same dict — no call to any of")
        print(f"  {', '.join(sorted(GUARD_FUNCS))}")
        print("in the same function body, and no read of that fence's create_time either.")
        print("A fenced pid that drives a kill, a reprieve, or an ownership claim must")
        print("consult the fence; see the legacy-row rule in agent/pid_fence.py.")
        print()
        for rel, lineno, scope, src in all_violations:
            print(f"  {rel}:{lineno}  in {scope}()")
            print(f"      {src}")
        print()
        print("If a site genuinely drives no decision (a pid interpolated into a log or")
        print("reason string), annotate it at the call site with:")
        print(f"    # {EXEMPTION_MARKER}")
        return 1

    print(f"Fence census clean — {len(all_guarded)} fenced-pid consumer(s), all guarded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
