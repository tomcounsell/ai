"""Structural census of the steering writers (issue #2642).

Two invariants that no runtime test in this repo can reach:

1. **Every non-test caller of ``push_steering_message`` passes ``room_id``
   explicitly.** A bare omission silently defaults to the legacy session key,
   which is a *valid* call that quietly opts out of the durability property.
   The census discovers call sites by walking the repo rather than iterating a
   fixed module table, so a writer added in a new module later is caught by the
   same test.

2. **Every row selection a Room is derived from is materialized before it is
   sorted.** ``AgentSession.query.filter(...)`` returns a
   ``popoto.models.query.QueryBuilder``, which supports ``__getitem__`` but has
   no ``.sort``. A sort on the bare QueryBuilder raises ``AttributeError`` — an
   unhandled crash on the inbound-Telegram path, and a *silent* demotion to a
   legacy-only drain inside the PostToolUse hook's swallow-all handler. A
   substring grep cannot pin this: ``ruff format`` is free to wrap the
   ``list(...)`` call across lines. AST is wrap-insensitive.

Pure ``ast.parse`` walks with no Redis dependency, so they live in
``tests/unit/`` beside the pattern they copy
(``tests/unit/test_bridge_dispatch_contract.py``) rather than behind the
integration suite's Redis prerequisite.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The one call site that legitimately passes no room_id: a raw Redis hash scan
# that is deliberately ORM-free and holds no session row it could derive a Room
# from. Fabricating one would reintroduce the unsorted-`[0]` defect.
ROOM_ID_ALLOWLIST = {"scripts/migrate_steering_queue_drain.py"}

# Floor taken from the plan's thirteen-row caller table. A floor rather than an
# equality so it cannot drift with the prose — but high enough that a discovery
# bug matching nothing fails the test instead of passing vacuously.
MIN_WRITER_CALL_SITES = 13

_SKIP_DIR_NAMES = {"tests", "node_modules", "__pycache__", "build", "dist"}


def _repo_python_files() -> list[pathlib.Path]:
    """Every non-test Python file in the repo, excluding vendored trees."""
    files: list[pathlib.Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(REPO_ROOT).parts
        if any(p.startswith(".") for p in rel_parts):
            continue
        if any(p in _SKIP_DIR_NAMES for p in rel_parts[:-1]):
            continue
        files.append(path)
    return files


def _writer_names(tree: ast.Module) -> set[str]:
    """Local names bound to ``agent.steering.push_steering_message``.

    Covers the plain import and both alias forms in use
    (``as _push_steering_message``, ``as _push_steering``), so a future alias
    is picked up without editing this test.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agent.steering":
            for alias in node.names:
                if alias.name == "push_steering_message":
                    names.add(alias.asname or alias.name)
    return names


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _has_keyword(call: ast.Call, keyword: str) -> bool:
    return any(kw.arg == keyword for kw in call.keywords)


class TestWriterCensus:
    def test_every_flipped_writer_passes_room_id(self):
        """Every non-test ``push_steering_message`` call names ``room_id``."""
        found: list[tuple[str, int]] = []
        violations: list[str] = []

        for path in _repo_python_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            names = _writer_names(tree)
            if not names:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _called_name(node) not in names:
                    continue
                found.append((rel, node.lineno))
                if rel in ROOM_ID_ALLOWLIST:
                    continue
                if not _has_keyword(node, "room_id"):
                    violations.append(f"{rel}:{node.lineno}")

        assert len(found) >= MIN_WRITER_CALL_SITES, (
            f"census discovered only {len(found)} push_steering_message call site(s); "
            f"expected at least {MIN_WRITER_CALL_SITES}. A discovery bug that matches "
            f"nothing would otherwise pass this test vacuously. Found: {sorted(found)}"
        )
        assert not violations, (
            "every non-test push_steering_message call must pass room_id EXPLICITLY — "
            "a bare omission silently writes to the legacy session key, opting the "
            "message out of Room durability with no signal. Pass a derived Room for a "
            "conversation-level write, or room_id=None with the reason inline. "
            f"Missing at: {violations}"
        )

    def test_repush_messages_calls_forward_the_room(self):
        """All four ``_repush_messages`` calls forward ``room_id``.

        Two of them are retries inside ``except`` blocks. A mechanical replace on
        the primary call shape misses those, and the miss is silent — the message
        still lands, just on the wrong leg.
        """
        path = REPO_ROOT / "agent" / "health_check.py"
        tree = ast.parse(path.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_repush_messages"
        ]
        assert len(calls) >= 4, (
            f"expected at least 4 _repush_messages call sites in agent/health_check.py "
            f"(abort-sibling primary + retry, non-abort primary + retry); found {len(calls)}"
        )
        missing = [c.lineno for c in calls if not _has_keyword(c, "room_id")]
        assert not missing, (
            "every _repush_messages call must forward the resolved room_id, including "
            f"the retries inside except blocks. Missing at lines: {missing}"
        )


# ── Materialize-before-sort census ────────────────────────────────────────────

# Every single-row read by session_id goes through the model's newest-wins
# resolver (#3091): ``AgentSession.newest_for_session_id(...)`` or
# ``rows_for_session_id(...)``, which materialize and order newest-first in one
# place (ordering pinned by ``tests/unit/test_agent_session_newest_wins.py``).
# The walker counts a name bound to either call as a newest-first selection of
# that name; a direct ``<name>.sort(key=... created_at ...)`` still counts too
# and is still held to the list(...) binding rule.
RESOLVER_METHODS = frozenset({"newest_for_session_id", "rows_for_session_id"})

# (relative path, enclosing function, selected name, minimum selections).
# Each entry is a selection a Room is derived from.
SORT_SITES = [
    ("bridge/answer_routing.py", "resolve_answer_target", "live", 1),
    ("bridge/answer_routing.py", "resolve_answer_target", "pending", 1),
    ("bridge/answer_routing.py", "resolve_answer_target", "guard", 1),
    # The bridge's re-check that a resolved target is still live before acking.
    ("bridge/telegram_bridge.py", "handler", "fresh_session", 1),
    ("bridge/telegram_bridge.py", "edit_handler", "session", 1),
    ("bridge/telegram_bridge.py", "edit_handler", "edit_sessions", 1),
    ("agent/health_check.py", "watchdog_hook", "s", 1),
    ("agent/session_executor.py", "steer_session", "session", 1),
]


def _own_nodes(fn: ast.AST):
    """Yield every node in ``fn``'s body without descending into nested defs.

    Scope-aware so the bridge's nested ``handler`` does not inherit its
    enclosing ``main``'s statements, or vice versa.
    """

    def _walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            yield from _walk(child)

    yield from _walk(fn)


def _functions(tree: ast.Module) -> dict[str, list[ast.AST]]:
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def _is_resolver_call(value: ast.expr | None) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in RESOLVER_METHODS
    )


def _resolver_bindings(fn: ast.AST) -> list[tuple[str, int]]:
    """``(name, lineno)`` for every ``<name> = AgentSession.<resolver>(...)`` in ``fn``.

    The resolver materializes and orders in one place, so a name bound this
    way is a newest-first selection with nothing left for the caller to sort.
    """
    hits: list[tuple[str, int]] = []
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Assign) or not _is_resolver_call(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                hits.append((target.id, node.lineno))
    return hits


def _created_at_sorts(fn: ast.AST) -> list[tuple[str, int]]:
    """``(sorted_name, lineno)`` for every direct created_at sort of a name in ``fn``."""
    hits: list[tuple[str, int]] = []
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "sort"):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        key = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
        if key is None:
            continue
        if "created_at" not in ast.dump(key):
            continue
        hits.append((func.value.id, node.lineno))
    return hits


def _parameters(fn: ast.AST) -> set[str]:
    args = fn.args
    named = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
    return {a.arg for a in named if a is not None}


def _binding_before(fn: ast.AST, name: str, lineno: int) -> ast.expr | None:
    """Value of the nearest ``ast.Assign`` binding ``name`` above ``lineno``."""
    best: tuple[int, ast.expr] | None = None
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Assign) or node.lineno >= lineno:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                if best is None or node.lineno > best[0]:
                    best = (node.lineno, node.value)
    return best[1] if best else None


def _is_list_call(value: ast.expr | None) -> bool:
    return (
        isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "list"
    )


def _unmaterialized_sorts(tree: ast.Module) -> list[str]:
    """Directly sorted names whose nearest binding is not a ``list(...)`` call.

    Names bound to the resolver are never sorted by the caller, so they cannot
    appear here; only a hand-rolled ``.sort`` on a QueryBuilder can.
    """
    bad: list[str] = []
    for fn_name, fns in _functions(tree).items():
        for fn in fns:
            for name, lineno in _created_at_sorts(fn):
                if not _is_list_call(_binding_before(fn, name, lineno)):
                    bad.append(f"{fn_name}:{name}:{lineno}")
    return bad


class TestRoomDerivationSortCensus:
    def test_room_derivation_sites_sort_before_selecting(self):
        """Each Room-derivation selection is newest-first: bound to the resolver,
        or materialized and then sorted by created_at."""
        for rel, fn_name, var, min_sorts in SORT_SITES:
            tree = ast.parse((REPO_ROOT / rel).read_text())
            fns = _functions(tree).get(fn_name)
            assert fns, f"{rel}: function {fn_name!r} not found — did it get renamed?"
            sorts = [
                s
                for fn in fns
                for s in (*_created_at_sorts(fn), *_resolver_bindings(fn))
                if s[0] == var
            ]
            assert len(sorts) >= min_sorts, (
                f"{rel}::{fn_name} must select {var!r} newest-first (through "
                f"AgentSession.newest_for_session_id / rows_for_session_id, or a "
                f"created_at sort of a materialized list) before deriving a Room from "
                f"it (expected >= {min_sorts}, found {len(sorts)}). Without it a "
                f"superseded or stale row can derive a Room the live session never drains."
            )
            for fn in fns:
                for name, lineno in _created_at_sorts(fn):
                    if name != var:
                        continue
                    value = _binding_before(fn, name, lineno)
                    assert _is_list_call(value), (
                        f"{rel}::{fn_name} line {lineno}: {name!r} is sorted but its "
                        f"nearest binding is not a list(...) call. "
                        f"AgentSession.query.filter(...) returns a QueryBuilder with no "
                        f".sort, so this raises AttributeError at runtime."
                    )

    def test_sort_census_rejects_an_unmaterialized_sort(self):
        """Self-check: the walker must reject a bare QueryBuilder sort.

        Without this, a walker bug that silently matched nothing would leave the
        census above green on code that crashes at runtime.
        """
        bad_src = (
            "def f(session_id):\n"
            "    sessions = AgentSession.query.filter(session_id=session_id)\n"
            "    sessions.sort(key=lambda s: (s.created_at is not None, s.created_at),"
            " reverse=True)\n"
            "    return sessions[0]\n"
        )
        assert _unmaterialized_sorts(ast.parse(bad_src)) == ["f:sessions:3"]

        good_src = (
            "def f(session_id):\n"
            "    sessions = list(AgentSession.query.filter(session_id=session_id))\n"
            "    sessions.sort(key=lambda s: (s.created_at is not None, s.created_at),"
            " reverse=True)\n"
            "    return sessions[0]\n"
        )
        assert _unmaterialized_sorts(ast.parse(good_src)) == []

    def test_sort_census_counts_a_resolver_binding(self):
        """Self-check for the resolver shape: a name bound to
        ``AgentSession.newest_for_session_id(...)`` or ``rows_for_session_id(...)``
        counts as a newest-first selection and needs no list(...) binding, while
        a bare QueryBuilder sort next to it is still rejected."""
        src = (
            "def f(session_id):\n"
            "    session = AgentSession.newest_for_session_id(session_id, status='running')\n"
            "    rows = AgentSession.rows_for_session_id(session_id)\n"
            "    other = AgentSession.query.filter(session_id=session_id)\n"
            "    other.sort(key=lambda s: s.created_at or 0, reverse=True)\n"
            "    return session, rows, other[0]\n"
        )
        (fn,) = _functions(ast.parse(src))["f"]
        assert _resolver_bindings(fn) == [("session", 2), ("rows", 3)]
        assert _created_at_sorts(fn) == [("other", 5)]
        assert _unmaterialized_sorts(ast.parse(src)) == ["f:other:5"]

        # A lookalike method name on the model does not count as the resolver.
        lookalike = src.replace("newest_for_session_id", "newest_for_chat")
        (fn2,) = _functions(ast.parse(lookalike))["f"]
        assert _resolver_bindings(fn2) == [("rows", 3)]

    def test_no_unmaterialized_created_at_sort_in_the_touched_modules(self):
        """Repo-wide backstop over the modules that derive a Room from a sort."""
        for rel in (
            "bridge/telegram_bridge.py",
            "bridge/answer_routing.py",
            "agent/health_check.py",
            "agent/session_executor.py",
        ):
            tree = ast.parse((REPO_ROOT / rel).read_text())
            assert _unmaterialized_sorts(tree) == [], (
                f"{rel} sorts a name bound to something other than a materialized list"
            )
