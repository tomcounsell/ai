"""Regression guard: forbid positional `AgentSession.query.get(<string>)` calls.

Popoto's `query.get()` requires a key kwarg (`db_key=`, `redis_key=`, or full
KeyField kwargs). Passing a raw string positionally raises ``AttributeError``
which most call sites silently swallow, masking real lookup failures.

The canonical helper for raw-string lookups is ``AgentSession.get_by_id(...)``.
This test scans the source tree for any new violations and fails CI before
they can land. See issue #765 and `models/agent_session.py:get_by_id`.

Detection is **AST-based, not regex** (#3297). The line-regex this started as
could not tell a call from a mention: it fired on a module docstring in
``tests/unit/test_worker_loop_completion_conflict.py`` that explains this very
hazard in prose, and main sat red on it. A regex also misses the inverse — a
call whose opening paren and first argument land on different lines. Both
problems are the same problem, and the parser does not have either.
"""

from __future__ import annotations

import ast
from pathlib import Path

# The attribute chain that identifies the forbidden receiver.
TARGET_CHAIN = "AgentSession.query.get"


def _find_repo_root() -> Path:
    """Walk up from this file looking for a ``pyproject.toml`` marker."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    # Fallback: original heuristic
    return here.parents[2]


REPO_ROOT = _find_repo_root()

# Scan only first-party source directories. We deliberately do NOT use
# ``REPO_ROOT.rglob("*.py")`` because the repo contains sibling worktrees
# under ``.worktrees/`` AND ``.claude/worktrees/`` (skill-isolation worktrees)
# which would surface stale copies of the same files as false positives.
SCAN_DIRS = (
    "agent",
    "bridge",
    "models",
    "scripts",
    "tests",
    "tools",
    "ui",
    "worker",
)


def _iter_python_files() -> list[Path]:
    skip_dirs = {".git", ".venv", "venv", "__pycache__", "node_modules", "worktrees"}
    files: list[Path] = []
    for top in SCAN_DIRS:
        root = REPO_ROOT / top
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(REPO_ROOT).parts)
            if parts & skip_dirs:
                continue
            # Defensive: skip anything under any worktrees dir (e.g. .worktrees,
            # .claude/worktrees) regardless of leading dot.
            if any(p == "worktrees" or p == ".worktrees" for p in path.parts):
                continue
            files.append(path)
    return files


def _dotted_name(node: ast.AST) -> str | None:
    """Render an attribute/name chain as a dotted string, else None."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def find_positional_calls(source: str, rel: str) -> list[str]:
    """Return one ``rel:line`` entry per positional ``AgentSession.query.get`` call.

    A call with only keyword arguments is the correct form and is not a
    violation; a call with no arguments at all is nonsense but not *this*
    bug. Only a positional argument is reported.

    An unparseable file yields nothing: a syntax error is the type checker's
    problem, and a guard that raised on one would fail on every mid-edit save.
    """
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dotted_name(node.func) != TARGET_CHAIN:
            continue
        # `*args` unpacking arrives as an ast.Starred inside node.args, so this
        # one check covers both the literal and the splatted positional form.
        if node.args:
            violations.append(f"{rel}:{node.lineno}")
    return violations


def test_no_positional_agent_session_query_get():
    violations: list[str] = []
    for path in _iter_python_files():
        rel = str(path.relative_to(REPO_ROOT))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TARGET_CHAIN not in text:
            continue
        violations.extend(find_positional_calls(text, rel))

    assert not violations, (
        "Found positional AgentSession.query.get(<string>) calls. "
        "Use AgentSession.get_by_id(...) instead. See issue #765.\n  " + "\n  ".join(violations)
    )


def test_detector_ignores_a_prose_mention():
    """The #3297 false positive: the pattern named in a docstring, not called."""
    source = '"""Calls `AgentSession.query.get(...)` -- the phantom-attr hazard."""\n'
    assert find_positional_calls(source, "m.py") == []


def test_detector_fires_on_a_real_positional_call():
    """Known-bad, so the guard is proven red rather than merely green."""
    source = "AgentSession.query.get(session_id)\n"
    assert find_positional_calls(source, "m.py") == ["m.py:1"]


def test_detector_allows_the_kwarg_form():
    source = 'AgentSession.query.get(redis_key="x")\n'
    assert find_positional_calls(source, "m.py") == []


def test_detector_sees_a_call_split_across_lines():
    """The shape the old line-regex structurally could not catch."""
    source = "AgentSession.query.get(\n    session_id,\n)\n"
    assert find_positional_calls(source, "m.py") == ["m.py:1"]
