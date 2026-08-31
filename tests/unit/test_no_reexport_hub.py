"""Guard: `agent/agent_session_queue.py` imports only what it uses.

Issue #2876 removed this module's role as a re-export hub. It used to import 40
symbols from eight sibling modules purely so callers could reach them through
this one path, which made it the module everybody imported from regardless of
what they actually needed, and hid where each symbol really lived.

The pattern is easy to reintroduce and hard to notice in review: one name added
to an existing `from agent.session_health import (...)` block, never referenced
in this file, reads as an ordinary import. So the invariant is checked
mechanically rather than left to reviewer attention.

**The invariant.** Every name `agent/agent_session_queue.py` imports from a
sibling `agent.*` module must be referenced somewhere in its own body. A name
that is imported and never used is, by definition, imported for someone else to
read off this module — that is what a re-export *is*, whatever the comment above
it says.

**Why this is phrased as "unused import" and not "no F401".** A `# noqa: F401`
suppression is how the old hub silenced ruff, so banning the suppression string
would catch the historical form. But it only catches that form: a re-export with
no suppression at all, in a file ruff does not lint, is equally a re-export and
would pass a string check. Deriving the property from the AST catches the shape
regardless of how it is spelled — and it also means this guard cannot be
satisfied by deleting a comment.

Ruff's own F401 is not a substitute either: it is configured per-file and can be
disabled, and it counts an `__all__` entry as a use. This guard does not — an
`__all__` string is not a `Name` load, so re-exporting via `__all__` is still
caught. Verified, along with the two limits below.

**A name used only in an annotation or under `TYPE_CHECKING` is not flagged.**
That is deliberate rather than a gap. Such an import does not exist at runtime,
so no caller can reach a symbol through it — there is nothing to re-export. The
rule this guard enforces is about runtime reachability.

Scope is one file, read directly. This guard does not walk the tree or shell out
to git: the property it enforces belongs to `agent/agent_session_queue.py`
alone, and a narrower guard is one that keeps meaning what it says.
"""

import ast
from pathlib import Path

import pytest

HUB_PATH = Path(__file__).resolve().parents[2] / "agent" / "agent_session_queue.py"

# Only sibling application modules are in scope. Third-party and stdlib imports
# are not re-export hazards -- nobody reaches `datetime` through the queue
# module -- and `models.*` / `config.*` are separate packages whose ownership
# this issue did not touch.
SIBLING_PREFIX = "agent."


def _hub_tree() -> ast.Module:
    return ast.parse(HUB_PATH.read_text(encoding="utf-8"))


def _sibling_imports(tree: ast.Module) -> dict[str, tuple[int, str]]:
    """`{bound_name: (lineno, source_module)}` for names imported from `agent.*`.

    Covers both `from agent.x import y` and `import agent.x as z`. A relative
    `from .x import y` inside the `agent` package resolves to a sibling too, so
    it is included rather than silently skipped.
    """
    found: dict[str, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_sibling = module.startswith(SIBLING_PREFIX) or node.level > 0
            if not is_sibling:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                found[alias.asname or alias.name] = (node.lineno, module or ".")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith(SIBLING_PREFIX):
                    continue
                found[alias.asname or alias.name.split(".")[0]] = (node.lineno, alias.name)
    return found


def _referenced_names(tree: ast.Module) -> set[str]:
    """Every name loaded in the hub's body, excluding its own import statements.

    Attribute bases count: `_session_state.foo` is a use of `_session_state`,
    which is how the module-object import form stays legitimate. Import
    statements are skipped so a name is never counted as using itself.
    """
    referenced: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Import(self, node):  # noqa: N802 - visitor protocol
            pass

        def visit_ImportFrom(self, node):  # noqa: N802 - visitor protocol
            pass

        def visit_Name(self, node):  # noqa: N802 - visitor protocol
            referenced.add(node.id)

    _Visitor().visit(tree)
    return referenced


def unused_sibling_imports() -> list[tuple[str, int, str]]:
    """`[(name, lineno, source_module)]` -- imported from a sibling, never used."""
    tree = _hub_tree()
    imports = _sibling_imports(tree)
    referenced = _referenced_names(tree)
    return sorted(
        (name, lineno, module)
        for name, (lineno, module) in imports.items()
        if name not in referenced
    )


def test_hub_file_exists():
    """A rename would otherwise turn every assertion below into a vacuous pass."""
    assert HUB_PATH.is_file(), f"{HUB_PATH} not found -- update this guard's path"


def test_hub_imports_nothing_it_does_not_use():
    """The invariant: no symbol is imported here purely for someone else to read off."""
    offenders = unused_sibling_imports()
    if offenders:
        listed = "\n".join(
            f"  {HUB_PATH.name}:{lineno}  {name}  (from {module})"
            for name, lineno, module in offenders
        )
        pytest.fail(
            f"agent/agent_session_queue.py re-exports {len(offenders)} symbol(s) it never "
            f"uses:\n{listed}\n\n"
            "Issue #2876 removed this module's re-export-hub role. Import each symbol "
            "from the module that defines it instead of adding it here. If the hub "
            "genuinely needs the symbol, reference it in this file's body and the guard "
            "will pass."
        )


def test_guard_detects_a_reintroduced_reexport():
    """The guard's own demonstrated red, so a broken detector cannot pass silently.

    Without this, `unused_sibling_imports()` returning `[]` because its AST walk
    was subtly wrong is indistinguishable from returning `[]` because the file is
    clean -- the exact failure mode that makes an architectural guard worthless.
    """
    injected = ast.parse(
        "from agent.session_health import _agent_session_health_check\n"
        "from agent.session_state import _active_workers\n"
        "_active_workers.clear()\n"
    )
    imports = _sibling_imports(injected)
    referenced = _referenced_names(injected)
    unused = {name for name in imports if name not in referenced}

    assert unused == {"_agent_session_health_check"}, (
        "the guard failed to flag an unused sibling import, or flagged a used one"
    )


def _flagged(source: str) -> set[str]:
    tree = ast.parse(source)
    imports = _sibling_imports(tree)
    referenced = _referenced_names(tree)
    return {name for name in imports if name not in referenced}


def test_all_entry_does_not_count_as_a_use():
    """Re-exporting through `__all__` is still a re-export, and still caught.

    An `__all__` entry is a string literal, not a `Name` load. Ruff's F401 honours
    it; this guard does not, which is the whole reason it is not just F401.
    """
    assert _flagged("from agent.session_health import X\n__all__ = ['X']\n") == {"X"}


def test_typing_only_reference_is_not_flagged():
    """A name reachable only at type-check time is not a runtime re-export.

    Nothing can read such a symbol off this module at runtime, so there is
    nothing for a caller to route through. Asserted rather than assumed, because
    the docstring makes this claim and an untested claim drifts.
    """
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from agent.session_health import X\n"
        "v: X = None\n"
    )
    assert _flagged(source) == set()


def test_aliased_import_is_tracked_by_its_bound_name():
    """`import ... as` must be judged on the name it binds, not the name it names."""
    assert _flagged("from agent.session_health import X as Y\n") == {"Y"}
    assert _flagged("from agent.session_health import X as Y\nY()\n") == set()
