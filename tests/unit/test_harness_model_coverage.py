"""A-1 regression guard: every `get_response_via_harness(...)` call site must
pass ``model=`` as a keyword argument (or live in a documented whitelist).

Prevents the exact re-regression pattern that made PR #909 dormant for 10
days: a new call site added without the ``model`` kwarg silently bypasses
per-session model routing. This test AST-walks all ``agent/*.py`` modules
and asserts the invariant.

See plan ``docs/plans/session-model-routing-fallback.md`` §Test Impact A-1.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Files that are permitted to call get_response_via_harness WITHOUT model=.
# Reserved for in-progress work. Should stay empty; if adding a new entry,
# include a comment explaining why.
_WHITELIST: set[str] = set()


_TARGET_FUNC_NAME = "get_response_via_harness"


def _call_func_name(node: ast.Call) -> str | None:
    """Return the simple name of the function being called (handles ``fn()`` and ``mod.fn()``)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_has_model_kwarg(node: ast.Call) -> bool:
    """True iff the Call node has ``model=`` in its keywords (or **kwargs splat)."""
    for kw in node.keywords:
        # Explicit `model=...`
        if kw.arg == "model":
            return True
        # `**some_kwargs` — we can't tell statically, be permissive.
        if kw.arg is None:
            return True
    return False


def _agent_py_files() -> list[Path]:
    """Collect all ``agent/*.py`` files (excluding __init__.py and subdirs).

    The harness function is invoked only from the agent layer; this keeps
    the AST walk fast and focused.
    """
    repo_root = Path(__file__).resolve().parents[2]
    agent_dir = repo_root / "agent"
    if not agent_dir.is_dir():
        return []
    # Only top-level .py files; sub-packages are unlikely to call harness directly
    # but include them too for safety (rglob).
    return sorted(p for p in agent_dir.rglob("*.py") if p.is_file())


def _find_violations() -> list[str]:
    violations: list[str] = []
    repo_root = Path(__file__).resolve().parents[2]
    for path in _agent_py_files():
        rel = str(path.relative_to(repo_root))
        if rel in _WHITELIST:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_func_name(node) != _TARGET_FUNC_NAME:
                continue
            if _call_has_model_kwarg(node):
                continue
            violations.append(
                f"{rel}:{node.lineno}: {_TARGET_FUNC_NAME}(...) called without model= kwarg"
            )
    return violations


def test_all_harness_call_sites_pass_model_kwarg() -> None:
    """Regression guard: every production call site must pass ``model=``.

    If a new call site is added without model=, the linter-style walk below
    fails with file:line coordinates. Either add ``model=`` or — if the caller
    is in an in-progress branch — add the file to ``_WHITELIST`` with a
    justification comment AND a follow-up issue.
    """
    violations = _find_violations()
    assert not violations, (
        "get_response_via_harness must receive a model= kwarg at every call "
        "site (plan #1129 A-1 regression guard):\n  " + "\n  ".join(violations)
    )
