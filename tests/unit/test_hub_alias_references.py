"""Guard: nothing reaches a re-exported symbol through an `agent_session_queue` module alias.

Issue #2876 is removing `agent/agent_session_queue.py`'s role as a re-export hub.
Phases 3 and 4 repointed callers at the modules that actually own each symbol, but
the sweep that drove those phases matched only ``from agent.agent_session_queue
import X``. Attribute access through a module alias — ``_queue.X``, where ``_queue``
is the hub module object — is a second reference class, and it was missed. PR #3048's
review found 17 such sites.

Those sites fail in two distinct ways, which is why this guard is worth its weight:

1. **They break at phase 5.** The hub's re-exports are deleted there, so
   ``_queue.<re-export>`` becomes ``AttributeError``.
2. **Writes through them are already vacuous.** ``from X import y`` binds a *copy*
   of ``y`` onto the importing module at import time. Assigning ``_queue.y = z``
   rebinds that copy; the module that owns ``y``, and every reader that resolves it
   from the owner, never sees the change. Fifteen of the seventeen sites were of
   this shape, and the concurrency ceiling those tests existed to verify was never
   actually installed.

The hazard set is derived from the hub's own AST rather than pinned as a name list.
A name is a *pure re-export* when the hub imports it and never references it in its
own body — the plan's Finding 1 definition. Names the hub actually uses are working
imports: they survive phase 5, and an alias reaching them resolves the same object
the hub does. Deriving rather than pinning is what keeps this guard honest as the
hub changes, and it still fires if a re-export is reintroduced and reached this way
later.

Two constraints inherited from `docs/features/legacy-artifact-guard.md`:

- **Tracked content only.** File discovery goes through ``git ls-files``, never a
  filesystem walk. Compiled bytecode caches embed their module's string literals
  verbatim, so a stale cache produces a phantom match that is unreproducible in a
  fresh checkout (#2807).
- **No positional exemptions.** This guard has no exemption set at all. It does not
  need one: it matches on AST shape, so its own source — which necessarily names the
  hub as a string — is structurally invisible to it.
"""

import ast
import subprocess
from pathlib import Path

import pytest

HUB_MODULE = "agent.agent_session_queue"
HUB_PATH = "agent/agent_session_queue.py"

# Nodes that open a new name-binding scope. A binding made inside one of these
# shadows the enclosing scope's binding of the same name, which matters here:
# test files routinely rebind a short alias like `q` to a different module in each
# test function, so a module-wide alias table would report false positives.
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _tracked_python_files() -> list[str]:
    """Tracked ``*.py`` paths, via git. Never walks the filesystem."""
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
    )
    # An empty match set is signalled by exit status, not by a printed count, so a
    # broken invocation must never read as "nothing found".
    assert result.returncode == 0, f"git ls-files failed: {result.stderr.strip()}"
    files = result.stdout.split()
    assert files, "git ls-files returned no Python files — invocation is broken"
    return files


def _hub_reexports() -> set[str]:
    """Names the hub imports and never references in its own body.

    This is the plan's Finding 1 definition of a *pure re-export*, and the
    distinction is load-bearing rather than pedantic. A name the hub actually uses
    is a working import: it survives phase 5, and reaching it through the alias
    resolves to the same object the hub itself resolves. A name the hub never
    touches exists solely to be re-exported — that is the one that disappears at
    phase 5, and the one whose write-through rebinds a copy nothing reads.

    ``agent.session_state`` (bound as ``_session_state``) shows why the narrower
    rule is the correct one: the hub uses it, and it is a *module object*, so an
    alias reaching through it sees live attributes rather than an import-time copy.
    A rule keyed only on "imported but not defined here" would flag it and every
    other working import, and a guard that fires on safe code is one people learn
    to route around.

    Derived, not pinned: no name list to fall out of date as the hub changes.
    """
    tree = ast.parse((_repo_root() / HUB_PATH).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)

    # Every Name load in the hub outside its own import statements.
    referenced: set[str] = set()

    class _Referenced(ast.NodeVisitor):
        def visit_Import(self, node):  # noqa: N802 - visitor protocol
            pass

        def visit_ImportFrom(self, node):  # noqa: N802 - visitor protocol
            pass

        def visit_Name(self, node):  # noqa: N802 - visitor protocol
            referenced.add(node.id)

    _Referenced().visit(tree)
    return imported - referenced


def _own_scope_nodes(node: ast.AST):
    """Descendants belonging to ``node``'s own scope, not descending into nested ones.

    Both the binding pass and the reference pass go through this one helper, which
    is what makes the walk order-independent. Reaching for ``ast.walk`` here instead
    descends into a ``FunctionDef`` nested under an ``if`` / ``with`` / ``try``, so
    that function's bindings leak outward and its references get evaluated against
    the enclosing scope's aliases — a verdict that then flips with source ordering,
    in both the false-negative and false-positive directions.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_NODES):
            continue  # a nested scope owns its own bindings
        yield child
        yield from _own_scope_nodes(child)


def _nested_scopes(node: ast.AST):
    """Scope nodes directly owned by ``node``, at any statement depth beneath it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_NODES):
            yield child
        else:
            yield from _nested_scopes(child)


def _scope_bindings(node: ast.AST) -> dict[str, str]:
    """``{bound_name: dotted_path}`` for imports in this scope.

    ``import a.b.c`` binds only ``a``, so the name maps to ``a`` — not to ``a.b.c``,
    which is a different module and would make the fully-dotted access form resolve
    against the wrong entry.
    """
    bindings: dict[str, str] = {}
    for sub in _own_scope_nodes(node):
        if isinstance(sub, ast.Import):
            for alias in sub.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    bindings[top] = top
        elif isinstance(sub, ast.ImportFrom) and sub.module:
            for alias in sub.names:
                bindings[alias.asname or alias.name] = f"{sub.module}.{alias.name}"
    return bindings


def _resolve_dotted(node: ast.AST, env: dict[str, str]) -> str | None:
    """Resolve an expression to the dotted path it names, or ``None``.

    Handles every binding form that can reach the hub: ``import ... as asq`` then
    ``asq.X``; ``from agent import agent_session_queue as q`` then ``q.X``; and the
    bare ``import agent.agent_session_queue`` then fully-dotted
    ``agent.agent_session_queue.X``, which parses as an Attribute *of an Attribute*
    and is invisible to a matcher keyed on ``Name`` alone.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    base = env.get(current.id)
    if base is None:
        return None
    parts.reverse()
    return ".".join([base, *parts])


def _alias_hits(node: ast.AST, inherited: dict[str, str], reexports: set[str]) -> list[tuple]:
    """Collect hits in this scope, then recurse into nested scopes with its env.

    Bindings for the whole scope are gathered before any reference is evaluated, so
    a hit does not depend on whether the import happens to sit above or below it.
    """
    env = {**inherited, **_scope_bindings(node)}
    hits: list[tuple] = []
    for sub in _own_scope_nodes(node):
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr in reexports
            and _resolve_dotted(sub.value, env) == HUB_MODULE
        ):
            hits.append((sub.lineno, ast.unparse(sub.value), sub.attr, type(sub.ctx).__name__))
    for scope in _nested_scopes(node):
        hits.extend(_alias_hits(scope, env, reexports))
    return hits


def find_hub_alias_reexport_references() -> dict[str, list[tuple]]:
    """Every tracked-Python site reaching a hub re-export through a module alias."""
    reexports = _hub_reexports()
    root = _repo_root()
    found: dict[str, list[tuple]] = {}
    for rel in _tracked_python_files():
        if rel == HUB_PATH:
            continue  # the hub's own body legitimately uses what it imports
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        hits = sorted(set(_alias_hits(tree, {}, reexports)))
        if hits:
            found[rel] = hits
    return found


def _analyze(source: str, reexports: set[str]) -> list[tuple]:
    """Run the analyzer over a source string. Used to test the guard's own machinery."""
    return sorted(set(_alias_hits(ast.parse(source), {}, reexports)))


# The analyzer is the artifact phase 5 leans on, so it carries its own tests. Both
# cases below are regressions: PR #3048's round-2 review reproduced each as a real
# defect in the first version of this file.

_REORDER_HIT = """
import agent.agent_session_queue as q
q._slot_registry = None
"""

_REORDER_SHADOW = """
if True:
    def inner():
        from agent import session_state as q

        return q._slot_registry
"""


def test_scope_walk_does_not_depend_on_source_ordering():
    """A nested scope's alias must not leak out, in either order.

    The first version walked non-scope children with ``ast.walk``, which descends
    into a function nested under an ``if``. That made the verdict depend on which
    block came first: hub-import-first hid a real hit, ``if``-first reported a
    perfectly safe ``agent.session_state`` line as a violation.
    """
    reexports = {"_slot_registry"}

    hit_first = _analyze(_REORDER_HIT + _REORDER_SHADOW, reexports)
    shadow_first = _analyze(_REORDER_SHADOW + _REORDER_HIT, reexports)

    assert [(a, s, c) for _, a, s, c in hit_first] == [("q", "_slot_registry", "Store")]
    assert [(a, s, c) for _, a, s, c in shadow_first] == [("q", "_slot_registry", "Store")]

    # The shadowed inner read of agent.session_state is safe and must never appear,
    # and the real hub write must never disappear — whichever block is written first.
    assert len(hit_first) == len(shadow_first) == 1


def test_bare_dotted_import_form_is_detected():
    """``import agent.agent_session_queue`` + fully-dotted access is Attribute-of-Attribute.

    A matcher keyed on ``Attribute.value`` being a ``Name`` never sees this form.
    """
    source = """
import agent.agent_session_queue

agent.agent_session_queue._slot_registry = None
print(agent.agent_session_queue.steer_session)
"""
    hits = _analyze(source, {"_slot_registry", "steer_session"})
    assert [(a, s) for _, a, s, _ in hits] == [
        ("agent.agent_session_queue", "_slot_registry"),
        ("agent.agent_session_queue", "steer_session"),
    ]


def test_working_imports_are_not_flagged():
    """Reaching a module the hub genuinely uses is safe and must stay silent."""
    source = """
import agent.agent_session_queue as asq

asq._session_state._slot_registry = None
print(asq.AgentSession)
"""
    # `_session_state` and `AgentSession` are hub-used, so they are not re-exports.
    assert _analyze(source, {"_slot_registry", "steer_session"}) == []


def test_hub_reexport_set_is_derivable_and_non_empty():
    """The hazard set must actually resolve, or the main check passes vacuously.

    Until phase 5 lands, the hub still carries re-exports, so this is non-empty.
    Phase 5 empties it by deleting them — at which point this assertion is the one
    that must be updated, deliberately, in the same diff that does the deleting.
    """
    reexports = _hub_reexports()
    assert reexports, (
        "No pure re-exports derived from the hub. Either phase 5 has landed (in "
        "which case update this test alongside it) or the AST derivation is broken."
    )
    # A symbol the hub defines itself is not a re-export.
    assert "_push_agent_session" not in reexports
    # Nor is one the hub imports and actually uses — see _hub_reexports' docstring.
    assert "AgentSession" not in reexports
    assert "_session_state" not in reexports


def test_no_module_alias_reaches_a_hub_reexport():
    """No tracked Python may reach a hub re-export through a module alias."""
    found = find_hub_alias_reexport_references()
    if found:
        detail = "\n".join(
            f"  {path}\n"
            + "\n".join(f"    L{ln}  {alias}.{attr}  [{ctx}]" for ln, alias, attr, ctx in hits)
            for path, hits in sorted(found.items())
        )
        pytest.fail(
            "Module-alias access to a symbol the hub only re-exports:\n"
            f"{detail}\n\n"
            "Reach the symbol through the module that owns it. Reading through the "
            "hub alias breaks when the re-export is deleted; writing through it "
            "rebinds an import-time copy that nothing reads."
        )
