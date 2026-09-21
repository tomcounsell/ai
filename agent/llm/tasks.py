"""The LLM task taxonomy (#3410).

Every non-harness LLM call site declares one :class:`LLMTask` as a module
constant next to its output type and passes it as ``task=`` to
:func:`agent.llm.run_typed`. The declaration is the only per-site backend
choice in the repo: ``agent/llm/router.py::resolve`` reads it together with
the call's ``project_key`` and returns the backend leg to run, so moving a
site between backends is a one-word diff on its declaration.

Protocol:

* ``kind`` separates the two populations. A ``CLASSIFICATION`` task returns
  one of a closed set of labels (``bool`` / ``Literal`` fields); a
  ``THINKING`` task writes prose or extracts structure. Thinking tasks never
  leave the subscription backend under this taxonomy.
* ``backend`` is the backend the site lands on for eligible context. There
  is no separate "incumbent" concept: the value in code is the truth, and
  the comparison record in the site's row of
  ``docs/features/llm-task-taxonomy.md`` is the argument for it.
* ``error_cost`` is the tier the PR reviewer applies when a site lands on a
  local backend (``high`` 95%, ``medium`` 90%, ``low`` 85% agreement with
  the reference arm).
* ``client_only`` pins a site to the subscription backend for every project
  key (charter §7: client work stays on the Claude and Codex subscriptions).

Fail-closed rule: the router routes a local-backend task to its declared
backend only for context it can prove eligible (``valor`` is pinned; other
keys are a cache-only read). A ``None`` key, a cache miss, or a client key
resolves to the subscription backend. The wrapper never invents a default
answer: on :class:`agent.llm.LLMCallError` every call site applies its own
conservative fail-safe, exactly as before this taxonomy existed.

Site registry (:func:`declared_sites`): the one list of declared sites that
``tools/doctor``, ``python -m tools.classification_eval --audit`` and the
enumeration test read. It is a static AST walk over
:data:`DECLARATION_ROOTS`, never an import: a declaring module is read as
source, so listing the sites has no side effects, needs no daemon, and
covers modules this process would never import (a script, a reflection
job). The walk therefore needs every declaration to be literal, and that
is the declaration convention:

* one module-level assignment per site, ``NAME = LLMTask(...)``;
* ``site`` a string literal; ``kind``, ``backend`` and ``error_cost`` an
  attribute on the enum class (``TaskKind.CLASSIFICATION``,
  ``Backend.OLLAMA``, ``ErrorCost.HIGH``); ``client_only`` ``True`` or
  ``False``; positional arguments in field order are accepted;
* anything else (a name, a call, an f-string) raises ``ValueError`` naming
  the file and line, so a declaration the walk cannot read fails loudly in
  doctor, in the audit and in the test rather than going unlisted.

The :class:`Decision` marker (lane C, #3421): per-field metadata for the
decisions leg (``agent/llm/backends/decisions.py``), attached to a ``bool``
or ``Literal`` field of an output type as ``Annotated[<type>, Decision(...)]``.
``question`` becomes the wire ``instructions``; ``criteria`` maps each
option (the literal values, or ``"true"`` / ``"false"`` for a ``bool``) to
its rubric string; ``threshold`` is the ``noul`` cut for a ``bool``;
``min_confidence`` is the abstain floor for a ``Literal`` (an answer under it
is a ``validation`` failure and the fallback leg answers instead). Only the
decisions leg reads it, from ``output_type.model_fields[name].metadata``;
the Anthropic and Ollama legs never see it because pydantic keeps
``Annotated`` metadata out of the JSON schema. A ``bool`` or ``Literal``
field with no marker still becomes a question with default rubrics. The
marker is metadata on the output type, never a field of :class:`LLMTask`,
so the site walk below is untouched by it.

Lane B (#3420) appends its own :class:`Backend` member and its own keyword
fields with defaults; nothing for it lives here.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path


class TaskKind(StrEnum):
    """The two populations of non-harness LLM calls."""

    CLASSIFICATION = "classification"
    THINKING = "thinking"


class Backend(StrEnum):
    """A backend leg under ``agent/llm/backends/``; the value is its log token."""

    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    DECISIONS = "decisions"


class ErrorCost(StrEnum):
    """What a wrong answer costs at the site; sets the acceptance-bar tier."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class LLMTask:
    """One call site's declaration, read by the router and the taxonomy tests.

    ``site`` is a stable dotted id (``routing.needs_response``,
    ``job_router.route``) that keys the comparison records and the doc table.
    """

    site: str
    kind: TaskKind
    backend: Backend
    error_cost: ErrorCost = ErrorCost.MEDIUM
    client_only: bool = False


@dataclass(frozen=True)
class Decision:
    """Per-field question metadata for the decisions leg (#3421).

    Use as ``Annotated[bool, Decision(...)]`` or
    ``Annotated[Literal[...], Decision(...)]`` on an output type's field;
    see the module docstring for the contract.
    """

    question: str
    """The wire ``instructions`` for the question."""
    criteria: Mapping[str, str] | None = None
    """Option to rubric text (``"true"`` / ``"false"`` for a ``bool``)."""
    threshold: float = 0.5
    """A ``bool`` is ``True`` when the ``noul`` probability is at or above this."""
    min_confidence: float = 0.0
    """A ``Literal`` answer under this confidence abstains to the fallback leg."""


#: The directories the site walk reads, repo-relative. ``tests/`` directories
#: and ``test_*.py`` files under them are skipped.
DECLARATION_ROOTS: tuple[str, ...] = (
    "agent",
    "bridge",
    "worker",
    "tools",
    "reflections",
    "scripts",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_ENUM_FIELDS: dict[str, type[StrEnum]] = {
    "kind": TaskKind,
    "backend": Backend,
    "error_cost": ErrorCost,
}


@dataclass(frozen=True)
class DeclaredSite:
    """One module-level ``NAME = LLMTask(...)`` found by :func:`declared_sites`."""

    task: LLMTask
    path: str
    """Repo-relative POSIX path of the declaring module."""
    name: str
    """The module constant's name (``JOB_ROUTE``)."""
    lineno: int


def _is_llm_task_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "LLMTask") or (
        isinstance(func, ast.Attribute) and func.attr == "LLMTask"
    )


def _literal_field(field_name: str, value: ast.expr, where: str):
    """Read one declaration argument under the literal convention."""
    enum_cls = _ENUM_FIELDS.get(field_name)
    if enum_cls is not None:
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == enum_cls.__name__
            and value.attr in enum_cls.__members__
        ):
            return enum_cls[value.attr]
        raise ValueError(
            f"{where}: {field_name}= must be a literal {enum_cls.__name__}.<MEMBER> "
            "so the site walk can read it without importing the module"
        )
    if isinstance(value, ast.Constant):
        if field_name == "site" and isinstance(value.value, str):
            return value.value
        if field_name == "client_only" and isinstance(value.value, bool):
            return value.value
    raise ValueError(
        f"{where}: {field_name}= must be a literal "
        f"{'string' if field_name == 'site' else 'True/False'}"
    )


def _task_from_call(call: ast.Call, where: str) -> LLMTask:
    names = [f.name for f in fields(LLMTask)]
    if len(call.args) > len(names):
        raise ValueError(f"{where}: too many positional arguments to LLMTask")
    kwargs: dict[str, object] = {}
    for field_name, value in zip(names, call.args, strict=False):
        kwargs[field_name] = _literal_field(field_name, value, where)
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in names:
            raise ValueError(f"{where}: LLMTask keyword {keyword.arg!r} is not a declared field")
        if keyword.arg in kwargs:
            raise ValueError(f"{where}: LLMTask field {keyword.arg!r} given twice")
        kwargs[keyword.arg] = _literal_field(keyword.arg, keyword.value, where)
    missing = [n for n in ("site", "kind", "backend") if n not in kwargs]
    if missing:
        raise ValueError(f"{where}: LLMTask declaration is missing {missing}")
    return LLMTask(**kwargs)  # type: ignore[arg-type]


def _module_declarations(path: Path, rel: str) -> Iterable[DeclaredSite]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if not isinstance(target, ast.Name) or not _is_llm_task_call(value):
            continue
        where = f"{rel}:{node.lineno}"
        yield DeclaredSite(
            task=_task_from_call(value, where), path=rel, name=target.id, lineno=node.lineno
        )


def _is_test_path(rel_parts: tuple[str, ...]) -> bool:
    return "tests" in rel_parts[:-1] or rel_parts[-1].startswith("test_")


def declared_sites(
    roots: Iterable[str] = DECLARATION_ROOTS, *, repo_root: Path | None = None
) -> list[DeclaredSite]:
    """Every module-level ``LLMTask`` declaration under ``roots``, sorted by site.

    A static AST walk (see the module docstring): nothing is imported, so
    the list is complete for the checkout regardless of what this process
    has loaded, and a declaration that breaks the literal convention raises
    ``ValueError`` naming ``path:line``. Duplicate site ids are returned as
    found; the enumeration test asserts uniqueness.
    """
    base = repo_root if repo_root is not None else _REPO_ROOT
    found: list[DeclaredSite] = []
    for root in roots:
        for path in sorted((base / root).rglob("*.py")):
            rel_parts = path.relative_to(base).parts
            if _is_test_path(rel_parts):
                continue
            found.extend(_module_declarations(path, "/".join(rel_parts)))
    return sorted(found, key=lambda d: (d.task.site, d.path, d.lineno))
