"""Regression guard: forbid positional `AgentSession.query.get(<string>)` calls.

Popoto's `query.get()` requires a key kwarg (`db_key=`, `redis_key=`, or full
KeyField kwargs). Passing a raw string positionally raises ``AttributeError``
which most call sites silently swallow, masking real lookup failures.

The canonical helper for raw-string lookups is ``AgentSession.get_by_id(...)``.
This test scans the source tree for any new violations and fails CI before
they can land. See issue #765 and `models/agent_session.py:get_by_id`.
"""

from __future__ import annotations

import re
from pathlib import Path

# Match `AgentSession.query.get(` followed by anything that is NOT a kwarg
# (kwargs would start with an identifier followed by `=`). We allow:
#   - kwarg form:        AgentSession.query.get(redis_key=...)
#   - empty parens:      AgentSession.query.get()  (nonsense but not the bug)
# We forbid:
#   - positional string: AgentSession.query.get(some_id)
#   - positional literal: AgentSession.query.get("foo")
PATTERN = re.compile(r"AgentSession\.query\.get\(\s*(?![\)a-zA-Z_][a-zA-Z_0-9]*\s*=)")

# Files allowed to mention the bad pattern (this test, the plan, etc.).
# Plain substring match against the relative path.
ALLOWLIST = (
    "tests/unit/test_no_positional_query_get.py",
    "docs/plans/",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _iter_python_files() -> list[Path]:
    skip_dirs = {".git", ".venv", "venv", "__pycache__", ".worktrees", "node_modules"}
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        parts = set(path.relative_to(REPO_ROOT).parts)
        if parts & skip_dirs:
            continue
        files.append(path)
    return files


def _is_allowlisted(rel: str) -> bool:
    return any(token in rel for token in ALLOWLIST)


def test_no_positional_agent_session_query_get():
    violations: list[str] = []
    for path in _iter_python_files():
        rel = str(path.relative_to(REPO_ROOT))
        if _is_allowlisted(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "AgentSession.query.get(" not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found positional AgentSession.query.get(<string>) calls. "
        "Use AgentSession.get_by_id(...) instead. See issue #765.\n  " + "\n  ".join(violations)
    )
