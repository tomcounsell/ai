"""Enumeration and parity tests for the LLM task taxonomy (#3410).

Six checks, all static: the walk parses source under ``DECLARATION_ROOTS``
(``agent/ bridge/ worker/ tools/ reflections/ scripts/``, skipping ``tests/``
directories and ``test_*.py``) and imports nothing but ``agent.llm.tasks``.

1. ``task_kwarg``: every ``Call`` whose func is ``Name(id="run_typed")`` or
   ``Attribute(attr="run_typed")`` carries a ``task`` keyword. AST, so a call
   whose kwargs sit on later lines passes on its content. The wrapper's own
   module is excluded.
2. The deleted local entry point (``run_typed`` with a ``_local`` suffix) appears
   nowhere under the roots, nor under ``tests/``, not even as text.
3. Every module whose AST carries an LLM call token declares a module-level
   ``LLMTask`` unless it is on :data:`RAW_TRANSPORT_ALLOWLIST`. The tokens are
   exactly ``messages.create(``, ``chat.completions.create(``, ``ollama.chat(``
   and a reference to ``OPENROUTER_URL``, matched as AST attribute chains,
   ``Name`` references and ``ImportFrom`` aliases, never as text, so a
   docstring or comment naming a token does not trip the check. Embeddings,
   transcription and image generation are not tokens.
4. Site ids are unique across :func:`declared_sites`, and every
   ``CLASSIFICATION`` task is reached through ``run_typed``: the module that
   declares it contains a ``run_typed`` call passing that constant as ``task=``.
5. Doc/code parity: the site table in ``docs/features/llm-task-taxonomy.md``
   lists every declared site id, and only declared ids, with the kind, backend,
   error-cost tier and §7 class the declaration carries.
6. ``hotfix_1055``, by function body and never by file: no ``asyncio.wait_for``
   call inside the four named hot-path bodies or any function under
   ``agent/llm/backends/``, and every ``run_typed`` call in the four named
   bodies passes ``hard_timeout`` as the constant ``None``. The unrelated
   ``wait_for`` around the Telegram send callback in ``agent/session_completion.py``
   and the two subprocess bounds in ``memory_quality_audit.py`` sit outside the
   named bodies, and every thinking site keeps the wrapper's default cap.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from agent.llm.tasks import DECLARATION_ROOTS, DeclaredSite, TaskKind, declared_sites

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_MODULE = "agent/llm/wrapper.py"
BACKENDS_DIR = "agent/llm/backends/"
TAXONOMY_DOC = REPO_ROOT / "docs" / "features" / "llm-task-taxonomy.md"
#: Assembled at runtime so this file is not itself a hit for the grep it mirrors.
DELETED_ENTRY_POINT = "run_typed" + "_local"

#: Modules that carry a raw transport by design and declare no task. A
#: directory entry ends with ``/`` and covers everything beneath it.
RAW_TRANSPORT_ALLOWLIST: dict[str, str] = {
    BACKENDS_DIR: "the backend legs are the transport every task rides",
    "agent/anthropic_client.py": "the shared Anthropic client, semaphore and stack loader",
    "agent/session_runner/harness/": "the claude -p harness transport, outside the taxonomy",
    "tools/ollama_client.py": "the shared Ollama client behind the leg",
    "tools/image_gen/__init__.py": "image generation: no text decision or thinking output",
    "tools/classification_eval/arms.py": (
        "reference arm for the comparison runner, not a task site"
    ),
}

#: The hot-path bodies hotfix #1055 protects: (module, function, site id).
HOT_PATH_BODIES: tuple[tuple[str, str, str], ...] = (
    ("bridge/promise_gate.py", "_evaluate_promise_async", "promise_gate.verdict"),
    ("bridge/read_the_room.py", "read_the_room", "read_the_room.verdict"),
    ("agent/session_completion.py", "_judge_completion_novelty", "session_completion.novelty"),
    ("reflections/memory/memory_quality_audit.py", "_gemma_classify", "memory_audit.classify"),
)


# ---------------------------------------------------------------------------
# Source walk
# ---------------------------------------------------------------------------


def _is_test_path(rel_parts: tuple[str, ...]) -> bool:
    return "tests" in rel_parts[:-1] or rel_parts[-1].startswith("test_")


def _walk_roots(roots: tuple[str, ...]) -> Iterator[Path]:
    for root in roots:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if not _is_test_path(path.relative_to(REPO_ROOT).parts):
                yield path


@cache
def _modules() -> tuple[tuple[str, ast.Module], ...]:
    """Every walked module as ``(repo-relative posix path, parsed tree)``."""
    return tuple(
        (path.relative_to(REPO_ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in _walk_roots(DECLARATION_ROOTS)
    )


def _tree(rel: str) -> ast.Module:
    for path, tree in _modules():
        if path == rel:
            return tree
    raise AssertionError(f"{rel} is not under the walked roots")


def _is_run_typed_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "run_typed") or (
        isinstance(func, ast.Attribute) and func.attr == "run_typed"
    )


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == name), None)


def _allowlisted(rel: str) -> bool:
    return any(
        rel.startswith(entry) if entry.endswith("/") else rel == entry
        for entry in RAW_TRANSPORT_ALLOWLIST
    )


def _function(tree: ast.Module, name: str, where: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{where} names no function {name!r}; rename the check with the code")


def _is_wait_for_call(node: ast.AST) -> bool:
    """``asyncio.wait_for(...)``, or a bare ``wait_for(...)`` imported from asyncio."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "wait_for":
        return isinstance(func.value, ast.Name) and func.value.id == "asyncio"
    return isinstance(func, ast.Name) and func.id == "wait_for"


# ---------------------------------------------------------------------------
# LLM call tokens (check 3)
# ---------------------------------------------------------------------------


def _attr_chain(node: ast.expr) -> tuple[str, ...]:
    """``a.b.c`` as ``("a", "b", "c")``; the head is a name or ``""`` for anything else."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    parts.append(node.id if isinstance(node, ast.Name) else "")
    return tuple(reversed(parts))


def _llm_call_tokens(tree: ast.Module) -> set[str]:
    """The LLM call tokens a module's AST carries, by their textual name."""
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            if chain[-2:] == ("messages", "create"):
                tokens.add("messages.create(")
            elif chain[-3:] == ("chat", "completions", "create"):
                tokens.add("chat.completions.create(")
            elif chain == ("ollama", "chat"):
                tokens.add("ollama.chat(")
        elif isinstance(node, ast.Name) and node.id == "OPENROUTER_URL":
            tokens.add("OPENROUTER_URL")
        elif isinstance(node, ast.Attribute) and node.attr == "OPENROUTER_URL":
            tokens.add("OPENROUTER_URL")
        elif isinstance(node, ast.ImportFrom) and any(
            alias.name == "OPENROUTER_URL" for alias in node.names
        ):
            tokens.add("OPENROUTER_URL")
    return tokens


# ---------------------------------------------------------------------------
# Check 1: every run_typed call carries task=
# ---------------------------------------------------------------------------


def test_every_run_typed_call_carries_task_kwarg():
    """A ``run_typed`` call without ``task=`` has no route; the wrapper refuses it at runtime,
    and this check refuses it at review time, multi-line calls included."""
    seen = 0
    untagged: list[str] = []
    for rel, tree in _modules():
        if rel == WRAPPER_MODULE:
            continue
        for node in ast.walk(tree):
            if _is_run_typed_call(node):
                seen += 1
                if _keyword(node, "task") is None:
                    untagged.append(f"{rel}:{node.lineno}")
    assert seen >= len(declared_sites()) // 2, "the walk found almost no run_typed calls"
    assert not untagged, "run_typed call(s) without task=:\n  " + "\n  ".join(untagged)


# ---------------------------------------------------------------------------
# Check 2: the deleted local entry point is gone
# ---------------------------------------------------------------------------


def test_deleted_local_entry_point_absent_everywhere():
    """The local leg is a backend behind ``run_typed`` now; the old entry point stays deleted,
    in code, in docstrings and in tests."""
    walked = [*_walk_roots(DECLARATION_ROOTS), *sorted((REPO_ROOT / "tests").rglob("*.py"))]
    hits = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in walked
        if DELETED_ENTRY_POINT in path.read_text(encoding="utf-8")
    ]
    assert not hits, f"{DELETED_ENTRY_POINT} still named in: " + ", ".join(hits)


# ---------------------------------------------------------------------------
# Check 3: raw transports declare a task or sit on the allowlist
# ---------------------------------------------------------------------------


def test_raw_transport_modules_declare_a_task():
    """A module that talks to a model directly declares what kind of task it is, so the
    taxonomy is the complete census of non-harness LLM calls."""
    declaring = {site.path for site in declared_sites()}
    matched: dict[str, set[str]] = {}
    undeclared: list[str] = []
    for rel, tree in _modules():
        tokens = _llm_call_tokens(tree)
        if not tokens:
            continue
        matched[rel] = tokens
        if _allowlisted(rel) or rel in declaring:
            continue
        undeclared.append(f"{rel} ({', '.join(sorted(tokens))})")
    assert matched, "no module carries an LLM call token; the token detection is broken"
    assert not undeclared, (
        "raw-transport module(s) with no module-level LLMTask declaration and no "
        "allowlist entry:\n  " + "\n  ".join(undeclared)
    )


@pytest.mark.parametrize("entry", sorted(RAW_TRANSPORT_ALLOWLIST))
def test_allowlist_entry_exists(entry: str):
    """An allowlist entry names a real path; a stale one would hide a future raw call."""
    assert (REPO_ROOT / entry).exists(), f"allowlist entry {entry} does not exist"


# ---------------------------------------------------------------------------
# Check 4: unique ids, classification reached through run_typed
# ---------------------------------------------------------------------------

_SITES = declared_sites()
_CLASSIFICATION_SITES = [s for s in _SITES if s.task.kind is TaskKind.CLASSIFICATION]


def test_site_ids_are_unique():
    ids = [site.task.site for site in _SITES]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"duplicate site id(s): {duplicates}"


def test_registry_is_populated():
    assert len(_SITES) >= 39, f"expected the full site list, found {len(_SITES)}"
    assert _CLASSIFICATION_SITES, "no classification site declared"


@pytest.mark.parametrize("site", _CLASSIFICATION_SITES, ids=lambda s: s.task.site)
def test_classification_site_reached_through_run_typed(site: DeclaredSite):
    """A classification declaration that no ``run_typed(task=...)`` in its module passes is a
    label with no route behind it."""
    calls = [
        node.lineno
        for node in ast.walk(_tree(site.path))
        if _is_run_typed_call(node)
        and (kw := _keyword(node, "task")) is not None
        and isinstance(kw.value, ast.Name)
        and kw.value.id == site.name
    ]
    assert calls, f"{site.path} declares {site.name} but no run_typed(task={site.name}) reaches it"


# ---------------------------------------------------------------------------
# Check 5: doc/code parity
# ---------------------------------------------------------------------------


def _split_cells(row: str) -> list[str]:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return [c.strip("`") for c in cells]


def _doc_site_rows() -> dict[str, list[str]]:
    """Site table rows keyed by site id: ``[kind, backend, error cost, §7 class, record]``."""
    rows: dict[str, list[str]] = {}
    for line in TAXONOMY_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = _split_cells(line)
        if len(cells) < 5 or "." not in cells[0] or set(cells[1]) <= {"-", ":"}:
            continue
        rows[cells[0]] = cells[1:]
    return rows


def test_taxonomy_doc_exists():
    assert TAXONOMY_DOC.is_file(), f"{TAXONOMY_DOC.relative_to(REPO_ROOT)} is missing"


def test_taxonomy_doc_lists_every_declared_site():
    """The doc table and the declarations name the same sites, in both directions."""
    documented = set(_doc_site_rows())
    declared = {site.task.site for site in _SITES}
    assert declared - documented == set(), (
        f"declared sites missing from the taxonomy doc table: {sorted(declared - documented)}"
    )
    assert documented - declared == set(), (
        f"taxonomy doc rows with no declaration: {sorted(documented - declared)}"
    )


@pytest.mark.parametrize("site", _SITES, ids=lambda s: s.task.site)
def test_taxonomy_doc_row_matches_declaration(site: DeclaredSite):
    """Kind, landed backend, tier and §7 class in the doc row are the declaration's values."""
    row = _doc_site_rows().get(site.task.site)
    assert row is not None, f"{site.task.site} has no row in the taxonomy doc table"
    expected = [
        site.task.kind.value,
        site.task.backend.value,
        site.task.error_cost.value,
        "client_only" if site.task.client_only else "eligible",
    ]
    assert row[:4] == expected, f"{site.task.site}: doc row {row[:4]} != declaration {expected}"


# ---------------------------------------------------------------------------
# Check 6: hotfix #1055 by function body
# ---------------------------------------------------------------------------

_BACKEND_MODULES = sorted(
    p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / BACKENDS_DIR).glob("*.py")
)


def _wait_for_calls(body: ast.AST) -> list[int]:
    return [node.lineno for node in ast.walk(body) if _is_wait_for_call(node)]


@pytest.mark.parametrize(
    ("rel", "func", "site"), HOT_PATH_BODIES, ids=[b[2] for b in HOT_PATH_BODIES]
)
def test_hotfix_1055_no_wait_for_in_hot_path_body(rel: str, func: str, site: str):
    """Cancelling a coroutine mid-request leaks httpx connections; the only timer around the
    live request on these bodies is the leg's SDK-level one."""
    lines = _wait_for_calls(_function(_tree(rel), func, rel))
    assert not lines, f"{site}: asyncio.wait_for inside {rel}::{func} at line(s) {lines}"


@pytest.mark.parametrize("rel", _BACKEND_MODULES)
def test_hotfix_1055_no_wait_for_in_backend_leg(rel: str):
    """Every function in a backend leg module is on the call path of every site."""
    assert _BACKEND_MODULES, "no backend leg modules found"
    offenders = [
        f"{rel}::{node.name}:{line}"
        for node in ast.walk(_tree(rel))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for line in _wait_for_calls(node)
    ]
    assert not offenders, "asyncio.wait_for inside a backend leg: " + ", ".join(offenders)


@pytest.mark.parametrize(
    ("rel", "func", "site"), HOT_PATH_BODIES, ids=[b[2] for b in HOT_PATH_BODIES]
)
def test_hotfix_1055_run_typed_passes_hard_timeout_none(rel: str, func: str, site: str):
    """The wrapper's ``hard_timeout`` (35 s by default) is a coroutine-level timeout applied
    outside the leg; dropping ``hard_timeout=None`` from one of these calls reintroduces the
    #1055 hazard while a ``wait_for``-only scan stays green."""
    calls = [
        node for node in ast.walk(_function(_tree(rel), func, rel)) if _is_run_typed_call(node)
    ]
    assert calls, f"{site}: {rel}::{func} makes no run_typed call"
    bad: list[str] = []
    for call in calls:
        kw = _keyword(call, "hard_timeout")
        if kw is None:
            bad.append(f"{rel}:{call.lineno} run_typed call omits hard_timeout=None")
        elif not (isinstance(kw.value, ast.Constant) and kw.value.value is None):
            bad.append(f"{rel}:{call.lineno} hard_timeout={ast.unparse(kw.value)} (must be None)")
    assert not bad, f"{site} (hotfix #1055):\n  " + "\n  ".join(bad)
