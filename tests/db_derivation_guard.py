"""Static recurrence guard: no test may derive its own Redis ``db=`` (#2655).

Every pytest process on this machine must own a private Redis logical database,
claimed via an ``flock`` over the pool ``[1..TEST_DB_POOL_MAX]`` in
``tests/db_claim.py``. A test that *computes* its own database number instead of
asking the claim API can issue a destructive command against a database a
different live process owns. That defect has been fixed three times (#2117,
#2606, #2624) and re-emerged each time at a new call site.

Why this module inverts the usual polarity
------------------------------------------
Each prior round enumerated ACCEPTED shapes: this constructor name, that helper.
An enumeration of accepted shapes means everything unenumerated passes silently,
and the next call site was always written in a shape nobody had enumerated. The
check stayed green while the defect shipped.

So this guard enumerates nothing about *what is being called*. It flags every
argument-passing SHAPE that can carry a ``db=``, judges the VALUE, and requires
it to match one of the sanctioned shapes. Anything else is a violation that
must be dispositioned in writing. That enumeration moved from callee names to
argument-passing syntax, and #2764 closed the two mirror-image holes the first
version of that enumeration left: a route that read only keywords never saw a
positional argument, and a route gated on positional arguments never saw a
keyword. Stated precisely, per route, so the next author does not have to
re-derive it by reading the walk:

- **Route 1** (any callee) reads a ``db=`` keyword at any position, and — only
  for a callee named ``Redis``/``StrictRedis`` — a bare positional argument at
  :data:`REDIS_DB_POSITIONAL_INDEX`. It does **not** read a positional ``db``
  on any other callee (see "What this guard still cannot see", gap 2).
- **Route 2** (``from_url`` only) reads the URL from the first positional
  argument if present, otherwise from a ``url=`` keyword. It does not read a
  URL under any other keyword name, and it never resolves more than the two
  accept shapes below (see gap 6).

Both routes then require the value to be a direct claim-API call, a one-hop
local alias to one, or the sanctioned fixture parameter used directly and
unshadowed. Anything else is a violation that must be dispositioned in
writing.

Measured on the tree at the time of writing: all 17 ``db=`` keyword arguments in
``tests/`` are Redis constructions, so ignoring the callee name costs zero false
positives while making the guard immune to a constructor name nobody thought of.
All 17 ``Redis`` calls parse as ``ast.Attribute`` (``redis.Redis(...)``) and
ZERO as ``ast.Name`` — a matcher reading only ``node.func.id`` would match
nothing and be vacuously green forever. :func:`_terminal_name` reads ``.attr``
for ``ast.Attribute`` and ``.id`` for ``ast.Name`` for exactly that reason.

What this guard still cannot see
---------------------------------
Naming every residual gap in one place, so the next author inherits a known
boundary rather than an assumed guarantee:

1. A Redis client constructed through an alias outside ``REDIS_CONSTRUCTORS``
   and receiving an opaque ``**`` splat with no visible ``"db"`` key.
2. A ``db`` passed positionally to a constructor alias outside
   ``{"Redis", "StrictRedis"}`` (#2764) — the honest cost of the positional
   leg's callee scoping: a bare third positional argument means nothing
   without knowing the callee, so the leg cannot be callee-agnostic the way
   the rest of this module is. Scoped narrower than ``REDIS_CONSTRUCTORS``
   deliberately: ``from_url``'s signature is ``(url, **kwargs)`` with no
   positional ``db`` slot, so including it in this leg's callee set would
   invent a shape that cannot exist at runtime.
3. A ``db`` computed inside a helper the guard cannot see through, more than
   one binding hop from the call site.
4. :func:`_matches` disposition matching is per-file-per-expression and
   kind-agnostic, so one ``ALLOWLIST`` entry can cover the same expression
   across kinds. Bounded by the db-0-only invariant and by
   :func:`apply_dispositions`'s refusal to let ``ALLOWLIST`` cover any
   candidate with a ``pool_db``.
5. A ``db`` arriving inside a starred unpack at the positional index
   (``redis.Redis("h", 6379, *rest)``) yields no candidate (#2764). The
   positional leg suppresses ``ast.Starred`` deliberately rather than
   reporting ``*rest`` as a derived db, so the contents of ``rest`` are
   unexamined. Direct cost of fix 1's Starred suppression, and the positional
   mirror of gap 1's opaque ``**`` splat.
6. Route 2 carries **no one-hop alias leg** (#2764). Route 1 resolves
   ``d = claim_test_db(); redis.Redis(db=d)`` through :func:`_resolve_one_hop`
   and accepts it; route 2's only accept legs are a direct call to a
   ``CLAIM_URL_NAMES`` name and an unshadowed bare parameter, so
   ``url = redis_test_url(); redis.Redis.from_url(url)`` is reported as a
   violation. That is a documented false positive, not a hole: the failure
   direction is loud, and an author who hits it can inline the call or write
   a disposition.

``REDIS_CONSTRUCTORS`` (below) is the residual permit list the opaque-splat
leg scopes itself to, and it is exactly that: an enumeration, kept short
because every name added is a guess about the future that buys nothing
measurable against the tree today. The positional leg (gap 2) scopes to its
own narrower ``{"Redis", "StrictRedis"}`` literal instead of this constant --
see gap 2 for why the two lists cannot be merged into one.

**Accepted, disclosed limitation.** The unshadowed-fixture-parameter check's
rebinding sweep (:func:`_rebound_names`) is two mechanisms: a structurally
version-proof sweep of ``ast.Name`` nodes with ``ctx=ast.Store``, plus a
hand-written list of binders whose bound name is a bare ``str`` rather than a
``Name`` node (``ast.ExceptHandler.name``, ``ast.alias``, a nested
``def``/``class``'s own ``.name``, and the ``match`` capture forms). That list
is enumerated by hand and nothing pins its completeness against Python's
grammar, so it is a third irreducible enumeration this guard carries,
alongside the positional leg's callee scoping (gap 2) and
``REDIS_CONSTRUCTORS`` at the splat layer (gap 1). Checked at the time of
writing and found safe: ``ast.TypeAlias`` binds through a ``Name`` with
``ctx=ast.Store`` and is already covered by the sweep; PEP 695's
``ast.TypeVar``/``ast.ParamSpec``/``ast.TypeVarTuple`` carry bare-``str``
names that match neither mechanism but were confirmed by execution not to
rebind a runtime parameter. No cheap, correct, grammar-level completeness
tripwire exists for this list, which is itself worth saying here.

Two dispositions, deliberately distinct
---------------------------------------
``ALLOWLIST`` is permanent and machine-constrained: no allowlist entry may name
a database in ``[1..TEST_DB_POOL_MAX]``. In practice that means db 0 only.

``DEFERRED`` is temporary, dated, and issue-linked, so that a site which cannot
be fixed this round is never laundered through ``ALLOWLIST``. Deferred entries
are printed on every run and hard-fail after ``expires``.

**Which layer enforces the invariant, precisely.** Two layers do different
halves of the job, and it matters which is which:

- :func:`check_dispositions` catches pool-slot **literals** written into an
  exemption expression. It receives only the ``Exemption`` dataclass and calls
  ``ast.parse(entry.expr)``, so it has no tree and no bindings. It rejects
  ``'14'``, ``"'redis://localhost:6379/14'"`` and ``'15 if base != 15 else 14'``
  — and it would **accept** a bare name, because a name in isolation has no
  integer in it to find.
- :func:`apply_dispositions` is what actually stops a bare name from
  laundering a pool slot through ``ALLOWLIST``. Its
  ``cand.pool_db is None or i >= len(allowlist)`` condition refuses to let any
  ``ALLOWLIST`` entry cover a candidate the *scan* proved names a pool slot.

As of #2628, this scenario has no live example: the one bare-name exemption
this file ever carried — ``unit/test_conftest_isolation_guards.py`` /
``divergent_db`` — is gone. :func:`_resolve_one_hop` now recognises
``divergent_db = scratch_test_db`` structurally (``scratch_test_db`` is a
sanctioned fixture parameter in ``CLAIM_FIXTURE_NAMES``, mirroring the
``redis_test_url`` leg of Route 2), so the scan marks that site ``ok=True``
directly and it needs no disposition of any kind. ``ALLOWLIST`` is back to
db-0 literals only. The ``apply_dispositions`` gate above is kept
defensively: it is what would stop the *next* bare-name site from being
laundered if a future author adds an ``ALLOWLIST`` entry for one without a
matching structural leg.

An earlier version of this docstring credited the whole protection to
:func:`check_dispositions`. That was wrong and worth correcting rather than
quietly fixing: a maintainer who believed it, and who simplified away the one
condition in :func:`apply_dispositions`, would see a single test fail, read it
as noise, and delete the only thing holding the invariant up.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Single source of truth for the pool ceiling. Imported rather than re-declared
# so the invariant below can never drift from the claim it protects. #2628 keeps
# this name private and its semantics unchanged, confirmed against that branch,
# so the import stays valid as written.
from tests.db_claim import _TEST_DB_POOL_MAX as TEST_DB_POOL_MAX

TESTS_ROOT = Path(__file__).resolve().parent

# The ONLY sanctioned sources of a db number. Matched on the terminal name, so
# both ``claim_test_db()`` and ``_db_claim.claim_test_db()`` qualify.
#
# ``claim_scratch_test_db`` is called directly inside the ``scratch_test_db``
# fixture body in tests/conftest.py (#2628). The guard cannot see through a
# fixture body, so a test that receives ``scratch_test_db`` as a fixture
# PARAMETER and rebinds it to a local name is recognised instead through
# CLAIM_FIXTURE_NAMES -- see that frozenset's docstring, and the
# ``redis_test_url`` fixture-parameter leg of Route 2, which this mirrors.
CLAIM_FUNCS = frozenset({"claim_test_db", "claim_scratch_test_db"})

# The sanctioned source of a Redis URL: ``tests.db_claim.redis_test_url()`` and
# the same-named pytest fixture in conftest, which returns its value. The bare
# ``ast.Name`` leg (route 2) accepts this identifier used directly ONLY when
# ``_is_unshadowed_fixture_parameter`` holds -- it is a genuine parameter of
# the enclosing function, carries no default, and is rebound nowhere in that
# function's own scope (#2764). Before #2764 the leg matched the identifier
# alone, with no scope check at all, and accepted a local variable or a
# defaulted parameter that merely shared the name -- laundering a hardcoded
# pool-slot URL to green.
CLAIM_URL_NAMES = frozenset({"redis_test_url"})

# The sanctioned source of a raw db NUMBER returned by a pytest fixture whose
# body the guard cannot see into. ``scratch_test_db`` (tests/conftest.py) calls
# claim_scratch_test_db() and returns the int; a test that receives it as a
# fixture PARAMETER and rebinds it to a local name (``divergent_db =
# scratch_test_db``) cannot be resolved by :func:`_resolve_one_hop` the way
# ``local = claim_test_db()`` can, because the bound value is a bare
# ``ast.Name`` referencing a function argument, not a call. This mirrors
# CLAIM_URL_NAMES / the ``redis_test_url`` leg of Route 2 exactly (#2764 makes
# that true rather than aspirational): route 1 also accepts the sanctioned
# identifier used DIRECTLY as the fixture parameter itself, not only through a
# one-hop alias, gated by the same ``_is_unshadowed_fixture_parameter`` check.
CLAIM_FIXTURE_NAMES = frozenset({"scratch_test_db"})

# Used ONLY to scope the opaque-``**``-splat leg (see `_splat_candidate`). Every
# other route in this module is deliberately callee-agnostic; this is the one
# place an enumeration is the lesser evil, because `**` forwarding is ubiquitous
# in test helpers and a callee-agnostic version flagged 183 unrelated sites.
REDIS_CONSTRUCTORS = frozenset({"Redis", "StrictRedis", "from_url"})

# The positional index of `db` on `redis.Redis.__init__` -- `(self, host, port,
# db, ...)`, so index 2 after `self`. redis-py has never deprecated positional
# `host`/`port`/`db` construction, but it HAS reshuffled argument conventions
# before (redis/redis-py#510), so this index is a fact about the installed
# library, not a permanent truth. It is pinned by
# ``test_db_derivation_guard.py::test_positional_index_still_names_db``, which
# re-derives it from ``inspect.signature(redis.Redis.__init__)`` at test time --
# a future redis-py reshuffle then turns that test red instead of silently
# making this constant name the wrong argument. This module stays a pure-AST
# module with no `redis` import; only the test knows about the library.
REDIS_DB_POSITIONAL_INDEX = 2

_URL_DB_RE = re.compile(r"^redis(?:s)?://[^/]*/(\d+)\s*$")


# ---------------------------------------------------------------------------
# Dispositions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    """One dispositioned site.

    ``path`` is repo-relative to ``tests/``. ``expr`` is the exact
    ``ast.unparse`` of the offending value, which is stable across line moves
    (unlike a line number).

    Matching is **per-file-per-expression, not per-site**: one entry covers
    every site in that file sharing the expression, and would silently cover a
    new one. The single ``test_redis_flush_guard.py`` / ``"0"`` entry currently
    covers four sites. The ``ALLOWLIST`` db-0 invariant bounds the blast radius
    — a new site can only be swept up if it names db 0 too — which is why this
    is acceptable rather than merely convenient.
    """

    path: str
    expr: str
    reason: str
    blocked_on: str | None = None  # "#2628" — required for DEFERRED
    expires: str | None = None  # ISO date — required for DEFERRED


# Permanent exemptions. INVARIANT (checked, not reviewed): none of these may
# name a database in [1..TEST_DB_POOL_MAX]. db 0 only.
ALLOWLIST: tuple[Exemption, ...] = (
    Exemption(
        path="_worker_guard.py",
        expr="0",
        reason=(
            "Worker heartbeat registrations only ever exist on production db 0, so the "
            "liveness read must target db 0 explicitly. Read-only scan/get; never flushes."
        ),
    ),
    Exemption(
        path="integration/test_redis_models.py",
        expr="0",
        reason=(
            "Asserts test data did NOT leak into production db 0. The assertion is about "
            "db 0 by construction; any other db would make the test vacuous."
        ),
    ),
    Exemption(
        path="unit/test_redis_flush_guard.py",
        expr="0",
        reason=(
            "These are the db-0 flush guard's own tests. They must construct a db-0 client "
            "to prove flushdb()/flushall() is refused there. The guard raises before any "
            "destructive command reaches the server."
        ),
    ),
    Exemption(
        path="unit/test_redis_flush_guard.py",
        expr="'redis://localhost:6379/0'",
        reason=(
            "Same db-0 guard tests, via the from_url route: the guard must also refuse a "
            "db-0 client that was built from a URL rather than a db= keyword."
        ),
    ),
)

# Temporary exemptions. Each MUST name a blocking issue and an expiry date, and
# is reported on every run. These are NOT allowlist entries -- ALLOWLIST's
# ``check_dispositions`` invariant (no entry may name a pool-slot literal)
# does not apply to this tuple, which is what would let a DEFERRED entry cover
# a site that genuinely needs a temporary pool-slot exemption. Empty as of
# #2628: the last two sites that lived here -- integration/test_notify_isolation.py
# (converted to db=claim_test_db()) and unit/test_conftest_isolation_guards.py's
# divergent_db (recognised structurally, see CLAIM_FIXTURE_NAMES) -- are both
# resolved, not merely deferred.
DEFERRED: tuple[Exemption, ...] = ()


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _terminal_name(node: ast.AST) -> str | None:
    """Terminal identifier of a callee or reference, across both node kinds.

    ``claim_test_db`` parses as ``ast.Name`` (``.id``); ``_db_claim.claim_test_db``
    parses as ``ast.Attribute`` (``.attr``). Reading only ``.id`` matches ZERO of
    the 17 Redis constructions in this tree — the vacuity this guard exists to
    avoid. Both branches are load-bearing; the ``ast.Attribute`` branch carries
    100% of current sites.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_claim_call(node: ast.AST) -> bool:
    """True when ``node`` is a direct call to the claim API (shape S1)."""
    return isinstance(node, ast.Call) and _terminal_name(node.func) in CLAIM_FUNCS


def _url_db(node: ast.AST) -> int | None:
    """Database number encoded in a string-literal Redis URL, if parseable."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        m = _URL_DB_RE.match(node.value)
        if m:
            return int(m.group(1))
    return None


def _int_literals(node: ast.AST) -> set[int]:
    """Every integer literal in ``node``'s subtree, plus any URL-encoded db."""
    found: set[int] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, bool):
            continue
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int):
            found.add(sub.value)
        db = _url_db(sub)
        if db is not None:
            found.add(db)
    return found


class _LocalBindings(ast.NodeVisitor):
    """Collect ``name -> [assigned value nodes]`` per function scope.

    Deliberately shallow: one hop, one scope, no cross-module resolution. The
    hop exists solely so the canonical fixture shape

        test_db = claim_test_db()
        redis.Redis(db=test_db)

    passes without an exemption. A name bound more than once, or bound to
    anything other than a claim call, is a violation.
    """

    def __init__(self) -> None:
        self._stack: list[ast.AST] = []
        self.bindings: dict[ast.AST, dict[str, list[ast.AST]]] = {}

    def _enter_function(self, node: ast.AST) -> None:
        self.bindings[node] = {}
        self._stack.append(node)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast.NodeVisitor API
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast API
        self._enter_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 — ast.NodeVisitor API
        if self._stack:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bindings[self._stack[-1]].setdefault(target.id, []).append(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 — ast.NodeVisitor API
        if self._stack and isinstance(node.target, ast.Name) and node.value is not None:
            self.bindings[self._stack[-1]].setdefault(node.target.id, []).append(node.value)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Candidates and violations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A site the guard is obliged to have an opinion about."""

    path: str  # relative to tests/
    lineno: int
    kind: str  # "db-kwarg" | "from-url" | "db-positional"
    expr: str  # ast.unparse of the value
    callee: str | None  # terminal name of the enclosing call, for the message
    ok: bool
    detail: str
    pool_db: int | None = None  # a pool slot this site provably names


@dataclass
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def violations(self) -> list[Candidate]:
        return [c for c in self.candidates if not c.ok]


def _resolve_one_hop(
    name: ast.Name,
    enclosing_fn: ast.AST | None,
    bindings: dict[ast.AST, dict[str, list[ast.AST]]],
) -> tuple[bool, str, ast.AST | None]:
    """Shape S2: a name bound exactly once in this function to a claim call,
    or to a sanctioned fixture-parameter name (``CLAIM_FIXTURE_NAMES``)."""
    if enclosing_fn is None:
        return False, f"{name.id!r} is not a local name in any function scope", None
    bound = bindings.get(enclosing_fn, {}).get(name.id)
    if not bound:
        return False, f"{name.id!r} has no local binding in the enclosing function", None
    if len(bound) > 1:
        return False, f"{name.id!r} is rebound {len(bound)} times in the enclosing function", None
    value = bound[0]
    if _is_claim_call(value):
        return True, f"{name.id!r} = {ast.unparse(value)}", value
    if isinstance(value, ast.Name) and value.id in CLAIM_FIXTURE_NAMES:
        return True, f"{name.id!r} = {ast.unparse(value)}, a sanctioned fixture parameter", value
    return False, f"{name.id!r} = {ast.unparse(value)}, which is not a claim call", value


def _parameter_names_without_defaults(fn: ast.AST) -> set[str]:
    """Names of ``fn``'s parameters that carry no default value.

    Reads ``posonlyargs``, ``args`` and ``kwonlyargs`` -- every parameter
    spelling (plain, positional-only, keyword-only) -- and excludes ``vararg``
    and ``kwarg``: ``*args`` is a tuple and ``**kw`` a dict, neither is ever a
    db. ``args.defaults`` aligns RIGHT-to-left against ``posonlyargs + args``
    (the last N entries carry the last N defaults); ``kwonlyargs`` pairs
    positionally with ``kw_defaults``, where a ``None`` entry means "no
    default" for that keyword-only parameter specifically.
    """
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    cut = len(positional) - len(args.defaults)
    names = {p.arg for p in positional[:cut]}
    for kwarg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is None:
            names.add(kwarg.arg)
    return names


def _rebound_names(fn: ast.AST) -> set[str]:
    """Names rebound anywhere in ``fn``'s own scope, by any binding form.

    Implemented as a node sweep (``ast.Name`` with ``ctx=ast.Store``), not a
    statement-type list -- a list has been wrong in this module's own drafts
    twice (#2764). The sweep is joined with the binders whose bound name is a
    bare ``str`` rather than a ``Name`` node: ``ast.ExceptHandler.name``,
    ``ast.alias`` (``import ... as`` / ``from ... import ... as``), a nested
    ``def``/``class``'s own ``.name``, and the three ``match`` capture forms
    (``ast.MatchAs.name``, ``ast.MatchStar.name``, ``ast.MatchMapping.rest``).

    Does **not** descend into a nested ``Lambda``/``FunctionDef``/
    ``AsyncFunctionDef``/``ClassDef`` BODY for any of the above: a binding
    inside a nested scope is that scope's own and leaves the outer name
    intact (verified in the interpreter). It **does** collect a
    ``global``/``nonlocal`` DECLARATION naming a name, wherever nested --
    ``nonlocal`` is the one binding form that can only be spelled inside a
    nested scope and genuinely rebinds the outer name; a nested ``global``
    cannot rebind it but is treated as a rebind anyway, a deliberate
    over-refusal in the safe direction.

    A nested ``def``/``class`` is only its BODY, not the whole node: a
    parameter default, a keyword-only default, and a decorator all evaluate
    at DEFINITION time, in the ENCLOSING scope, not inside the new scope the
    ``def``/``class`` introduces (#2764) -- ``def inner(x=(scratch_test_db
    := 7)): ...`` rebinds the OUTER ``scratch_test_db`` the moment ``inner``
    is defined, before its body ever runs. So this sweep recurses into a
    nested ``FunctionDef``/``AsyncFunctionDef``'s ``args.defaults``,
    ``args.kw_defaults`` and ``decorator_list``, and into a nested
    ``ClassDef``'s ``bases``, ``keywords`` and ``decorator_list`` -- never
    into ``body`` or the parameter names themselves, which stay the new
    scope's own.

    Excludes only ``ast.comprehension.target`` nodes (never a whole
    comprehension subtree): a ``for``-target has had its own scope since
    Python 3 and cannot rebind an enclosing parameter, but per PEP 572 a
    walrus written *inside* a comprehension binds in the nearest enclosing
    FUNCTION scope and must still be caught.
    """
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()

    names: set[str] = set()

    excluded_ids: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.comprehension):
            excluded_ids.update(id(sub) for sub in ast.walk(node.target))

    def walk_own_scope(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                if id(child) not in excluded_ids:
                    names.add(child.id)
            elif isinstance(child, ast.ExceptHandler):
                if child.name:
                    names.add(child.name)
            elif isinstance(child, ast.alias):
                names.add(child.asname or child.name.split(".")[0])
            elif isinstance(child, ast.MatchAs):
                if child.name:
                    names.add(child.name)
            elif isinstance(child, ast.MatchStar):
                if child.name:
                    names.add(child.name)
            elif isinstance(child, ast.MatchMapping):
                if child.rest:
                    names.add(child.rest)
            elif isinstance(child, ast.Lambda):
                continue  # anonymous; nothing binds in the outer scope
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(child.name)  # the def's own name binds HERE
                # Defaults and decorators evaluate in the ENCLOSING scope at
                # definition time; only the body and the parameter names
                # themselves belong to the new scope.
                for default in (
                    *child.args.defaults,
                    *(d for d in child.args.kw_defaults if d is not None),
                ):
                    walk_own_scope(default)
                for decorator in child.decorator_list:
                    walk_own_scope(decorator)
                continue  # body/args are a new scope; do not sweep them
            elif isinstance(child, ast.ClassDef):
                names.add(child.name)  # the class's own name binds HERE
                # Bases, keyword arguments (e.g. metaclass=...) and
                # decorators evaluate in the ENCLOSING scope; only the class
                # body belongs to the new scope.
                for base in child.bases:
                    walk_own_scope(base)
                for keyword in child.keywords:
                    walk_own_scope(keyword.value)
                for decorator in child.decorator_list:
                    walk_own_scope(decorator)
                continue  # body is a new scope; do not sweep it
            walk_own_scope(child)

    walk_own_scope(fn)

    # global/nonlocal DECLARATIONS rebind regardless of nesting depth.
    for node in ast.walk(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)

    return names


def _is_unshadowed_fixture_parameter(
    name: str, sanctioned_names: frozenset[str], enclosing_fn: ast.AST | None
) -> bool:
    """The positive fixture-parameter accept, shared by both routes (#2764).

    Accepts only when all three hold: ``name`` is one of the reserved
    identifiers in ``sanctioned_names`` (never widen this to an arbitrary
    identifier); ``name`` is genuinely a parameter of ``enclosing_fn`` with no
    default supplying it; and ``name`` is rebound by no binding form anywhere
    in that function's own scope.

    Deliberately POSITIVE. A negative formulation -- "sanctioned and absent
    from ``_LocalBindings``" -- was this fix's first draft and is wrong:
    ``_LocalBindings`` visits only ``ast.Assign``/``ast.AnnAssign`` inside a
    function, so absence from it is equally satisfied by a default argument, a
    module-level assignment, a ``for`` target, a walrus, a ``with ... as``, an
    ``except ... as``, an ``import ... as``, a nested ``def``, a ``match``
    capture, or a nested ``nonlocal`` -- every one of which must stay a
    violation. An absence check reads as a presence check and is not one.
    """
    if enclosing_fn is None or name not in sanctioned_names:
        return False
    if name not in _parameter_names_without_defaults(enclosing_fn):
        return False
    return name not in _rebound_names(enclosing_fn)


def _judge_db_value(
    value: ast.AST,
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
    bindings: dict[ast.AST, dict[str, list[ast.AST]]],
) -> tuple[bool, str, int | None]:
    """Judge a value passed as a Redis ``db=`` -- keyword or positional --
    against the sanctioned shapes. Shared by route 1's keyword leg and its
    positional leg (#2764) so the two judgments cannot drift apart."""
    pool_db: int | None = None
    if _is_claim_call(value):
        return True, "direct claim-API call", None
    if isinstance(value, ast.Name):
        enclosing_fn = _enclosing_function(call, parents)
        if _is_unshadowed_fixture_parameter(value.id, CLAIM_FIXTURE_NAMES, enclosing_fn):
            return True, "claim-API fixture parameter", None
        ok, detail, bound = _resolve_one_hop(value, enclosing_fn, bindings)
        if not ok and bound is not None:
            pool_db = _first_pool_db(_int_literals(bound))
        return ok, detail, pool_db
    detail = "value is not a call to claim_test_db()/claim_scratch_test_db()"
    pool_db = _first_pool_db(_int_literals(value))
    return False, detail, pool_db


def _splat_candidate(
    value: ast.AST, rel_path: str, call: ast.Call, callee: str | None
) -> Candidate | None:
    """A candidate for a ``**`` splat that could be carrying a ``db``.

    Two shapes, judged differently:

    - **a dict literal with a visible ``"db"`` key** -- callee-agnostic,
      like the rest of route 1, no matter what else is in the dict. Every
      key is inspected before deciding, rather than returning the instant a
      ``"db"`` key is found, because a Python dict literal lets a later
      entry silently overwrite an earlier one: an opaque entry that comes
      AFTER the ``"db"`` key (``**{"db": claim_test_db(), **overrides}``) can
      overwrite it at runtime, so that shape is judged on the visible value
      but can never be accepted (``ok=False``) -- the guard can prove there
      is a ``db`` in plain sight AND that something later can silently
      replace it, and both facts land in the message. An opaque entry that
      comes BEFORE the ``"db"`` key (``**{**base_kw, "db": 9}``) cannot
      overwrite it -- the literal ``"db"`` entry is the one that wins -- so
      that shape is judged directly on the visible value and accepted when
      that value is a claim call. Either way, a visible ``"db"`` key is
      **never** gated on the callee: a pool slot written in plain sight must
      not vanish just because the callee is not in ``REDIS_CONSTRUCTORS``.
    - **no visible ``"db"`` key, but an opaque entry that could be carrying
      one invisibly** -- a nested ``**`` unpack (``key is None``, e.g.
      ``**{**base_kw, "host": "x"}``) or a computed key (e.g. ``**{k: v}``).
      Undecidable, so a violation, but *only* for a callee that looks like a
      Redis construction.

    That callee scoping is a deliberate, bounded exception to the module's
    otherwise callee-agnostic polarity, and it applies **only** to the fully
    opaque case above -- never to a dict where a ``"db"`` key is visible.
    Route 1 can afford to ignore the callee wherever a value is visible
    because every one of the 17 ``db=`` sites in this tree is a Redis
    construction. A fully opaque ``**`` is not like that: it is the ordinary
    way test helpers forward kwargs, and flagging it everywhere regardless of
    callee produced **183 violations across 100+ unrelated files** on the
    first attempt. A guard that fires on every test helper in the repo does
    not get fixed, it gets deleted -- and then the real hole is open again
    with no guard at all.

    So the enumeration here buys usability at a known cost: a Redis client
    constructed through an alias nobody listed, receiving an opaque splat
    with no visible ``"db"`` key anywhere in it, is invisible. Today that
    costs nothing (the tree has 191 ``**`` call sites and none carries a
    ``"db"`` key together with an opaque entry), and the visible-``"db"``
    leg above stays fully callee-agnostic, with no exception. This is gap 1
    of "What this guard still cannot see" in the module docstring, which is
    the single place all of this module's residual gaps are listed together.
    """
    if isinstance(value, ast.Dict):
        db_val: ast.AST | None = None
        any_opaque = False
        opaque_after_db = False
        # Scan every entry before deciding -- classify first, decide once --
        # rather than returning from inside the loop the instant a "db" key
        # is seen. That is what lets the ordering below be judged correctly:
        # whether an opaque entry can overwrite the "db" key depends on
        # whether it comes before or after it, and that is only knowable
        # once the whole dict has been looked at.
        for key, val in zip(value.keys, value.values, strict=False):
            if key is None or not isinstance(key, ast.Constant):
                # A nested ** unpack (key is None, e.g.
                # **{**base_kw, "host": "x"}) or a computed key (e.g.
                # **{k: v}). Either way this entry's contents are invisible
                # to a static scan, so the dict cannot be proven safe on
                # this entry alone.
                any_opaque = True
                if db_val is not None:
                    opaque_after_db = True
                continue
            if key.value == "db":
                db_val = val

        if db_val is not None and not opaque_after_db:
            # The "db" key is visible and nothing after it can silently
            # overwrite it, so judge it exactly as a written-out db= would
            # be judged.
            return Candidate(
                path=rel_path,
                lineno=call.lineno,
                kind="db-kwarg",
                expr=ast.unparse(db_val),
                callee=callee,
                ok=_is_claim_call(db_val),
                detail=(
                    "db passed through a ** dict literal; value is not a "
                    "call to claim_test_db()/claim_scratch_test_db()"
                ),
                pool_db=None if _is_claim_call(db_val) else _first_pool_db(_int_literals(db_val)),
            )
        if db_val is None and not any_opaque:
            # Every key is a constant and none of them is "db" -- provably
            # safe.
            return None
        if db_val is not None:
            # opaque_after_db (the only way to reach here with a visible
            # "db" key): the value is visible, but a later entry in the
            # same dict literal (a nested ** unpack or a computed key) can
            # silently overwrite it at runtime. Judge this callee-agnostically
            # -- like every other visible db= site -- rather than gating it
            # on REDIS_CONSTRUCTORS: a db slot written in plain sight must
            # not vanish just because the callee is not one the guard
            # recognizes as a Redis construction. Never accepted (ok=False)
            # even if the visible value is itself a claim call, because the
            # guard cannot prove nothing downstream overwrites it.
            return Candidate(
                path=rel_path,
                lineno=call.lineno,
                kind="db-kwarg",
                expr=ast.unparse(db_val),
                callee=callee,
                ok=False,
                detail=(
                    'a "db" key is visible in this ** dict literal, but a later '
                    "entry in the same literal (a nested ** unpack or a computed "
                    "key) can silently overwrite it at runtime"
                ),
                pool_db=_first_pool_db(_int_literals(db_val)),
            )
        # No visible "db" key, but an opaque entry (nested ** unpack or
        # computed key) could be carrying one invisibly. Undecidable, so a
        # violation, but only for a callee that looks like a Redis
        # construction -- see the opaque-leg docstring above.

    if callee not in REDIS_CONSTRUCTORS:
        return None

    return Candidate(
        path=rel_path,
        lineno=call.lineno,
        kind="db-kwarg",
        expr=f"**{ast.unparse(value)}",
        callee=callee,
        ok=False,
        detail=(
            "opaque ** splat into a Redis construction: this call may carry a "
            "db= the guard cannot see. Pass db= explicitly from "
            "claim_test_db() instead of splatting a dict."
        ),
        pool_db=None,
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
    return None


def scan_source(source: str, rel_path: str) -> ScanResult:
    """Scan one module's source. Raises ``SyntaxError`` on unparseable input.

    Propagating the SyntaxError is deliberate. A guard that swallows files it
    cannot parse reports clean on the file most likely to be wrong.
    """
    tree = ast.parse(source, rel_path)
    parents = _parent_map(tree)
    binder = _LocalBindings()
    binder.visit(tree)
    result = ScanResult()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _terminal_name(node.func)

        # --- Route 1: any db= keyword, to any callee whatsoever ------------
        for kw in node.keywords:
            # A `**` splat parses as a keyword with `arg is None`, so a plain
            # `kw.arg != "db"` skip made `redis.Redis(**{"db": 15})` produce no
            # candidate at all -- not a violation, not a pass, simply unseen.
            # That is the same shape the guard exists to close: one nobody
            # enumerated. There is no live site today (191 `**` call sites
            # under tests/, zero of them Redis constructions).
            # integration/test_notify_isolation.py once built a Redis client
            # from a connection_kwargs dict -- the shape this leg exists for --
            # but #2628 converted it to pass db=claim_test_db() as an explicit
            # keyword instead, so this leg remains prospective rather than
            # exercised by any current site.
            if kw.arg is None:
                splat = _splat_candidate(kw.value, rel_path, node, callee)
                if splat is not None:
                    result.candidates.append(splat)
                continue
            if kw.arg != "db":
                continue
            value = kw.value
            ok, detail, pool_db = _judge_db_value(value, node, parents, binder.bindings)
            result.candidates.append(
                Candidate(
                    path=rel_path,
                    lineno=node.lineno,
                    kind="db-kwarg",
                    expr=ast.unparse(value),
                    callee=callee,
                    ok=ok,
                    detail=detail,
                    pool_db=pool_db,
                )
            )

        # --- Route 1b: positional `db` on Redis()/StrictRedis() (#2764) ----
        # A bare positional argument means nothing without knowing the
        # callee -- unlike a written-out `db=`, `some_helper("a", "b", 7)` is
        # not a db -- so this leg is deliberately scoped to Redis/StrictRedis
        # by terminal name. That callee scoping is disclosed as gap 2 in
        # "What this guard still cannot see" above, not slipped in silently.
        if (
            callee in {"Redis", "StrictRedis"}
            and len(node.args) > REDIS_DB_POSITIONAL_INDEX
            and not any(kw.arg == "db" for kw in node.keywords)
            # A `db=` keyword alongside 3+ positionals is a TypeError at
            # runtime (`Redis("h", 6379, 7, db=8)`) and cannot be a live
            # site; emitting a second, positional candidate for the same
            # unrunnable line would duplicate the violation and muddy the
            # message, so the keyword leg above takes precedence.
        ):
            positional_value = node.args[REDIS_DB_POSITIONAL_INDEX]
            # `redis.Redis("h", 6379, *rest)` puts an `ast.Starred` at this
            # index and passes the length guard above -- genuinely reachable,
            # unlike `redis.Redis(*args)` (one argument, short-circuits on the
            # length guard first). Without this check the Starred node falls
            # through to `_judge_db_value`'s generic `else` and reports
            # `expr="*rest"` as a derived db: a spurious violation with a
            # nonsense expression. Suppressed deliberately; the contents of
            # `rest` are unexamined -- disclosed as gap 5 above.
            if not isinstance(positional_value, ast.Starred):
                ok, detail, pool_db = _judge_db_value(
                    positional_value, node, parents, binder.bindings
                )
                result.candidates.append(
                    Candidate(
                        path=rel_path,
                        lineno=node.lineno,
                        kind="db-positional",
                        expr=ast.unparse(positional_value),
                        callee=callee,
                        ok=ok,
                        detail=detail,
                        pool_db=pool_db,
                    )
                )

        # --- Route 2: from_url(<url>) ---------------------------------------
        # The URL may arrive positionally or, since #2764, by `url=` keyword;
        # prefer the positional when both are somehow present, for the same
        # single-candidate reason as the positional leg above.
        if callee == "from_url":
            url_arg: ast.AST | None = None
            if node.args:
                url_arg = node.args[0]
            else:
                for kw in node.keywords:
                    if kw.arg == "url":
                        url_arg = kw.value
                        break
            if url_arg is not None:
                arg = url_arg
                pool_db = None
                if isinstance(arg, ast.Call) and _terminal_name(arg.func) in CLAIM_URL_NAMES:
                    ok, detail = True, "direct claim-API URL call"
                elif isinstance(arg, ast.Name) and _is_unshadowed_fixture_parameter(
                    arg.id, CLAIM_URL_NAMES, _enclosing_function(node, parents)
                ):
                    # The pytest fixture parameter of the same name, used
                    # directly and unshadowed. It is a function argument, not
                    # a local assignment, so _resolve_one_hop cannot resolve
                    # it; the identifier itself is the sanctioned source, but
                    # (#2764) only when it is genuinely still that parameter
                    # -- see _is_unshadowed_fixture_parameter's docstring for
                    # why a bare identifier match alone is unsafe here.
                    ok, detail = True, "claim-API URL fixture parameter"
                else:
                    ok = False
                    url_db = _url_db(arg)
                    if url_db is None:
                        detail = "URL is not redis_test_url() and its db cannot be determined"
                    else:
                        detail = f"URL literal hardcodes db {url_db}"
                        if 1 <= url_db <= TEST_DB_POOL_MAX:
                            pool_db = url_db
                result.candidates.append(
                    Candidate(
                        path=rel_path,
                        lineno=node.lineno,
                        kind="from-url",
                        expr=ast.unparse(arg),
                        callee=callee,
                        ok=ok,
                        detail=detail,
                        pool_db=pool_db,
                    )
                )

    return result


def _first_pool_db(literals: set[int]) -> int | None:
    hits = sorted(n for n in literals if 1 <= n <= TEST_DB_POOL_MAX)
    return hits[0] if hits else None


def scan_tree(root: Path | None = None) -> ScanResult:
    """Scan every ``*.py`` under ``tests/``."""
    root = root or TESTS_ROOT
    result = ScanResult()
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
        result.candidates.extend(scan_source(path.read_text(), rel).candidates)
    return result


# ---------------------------------------------------------------------------
# Disposition application and the settled invariant
# ---------------------------------------------------------------------------


def _matches(entry: Exemption, cand: Candidate) -> bool:
    return entry.path == cand.path and entry.expr == cand.expr


def apply_dispositions(
    result: ScanResult,
    allowlist: tuple[Exemption, ...] = ALLOWLIST,
    deferred: tuple[Exemption, ...] = DEFERRED,
) -> tuple[list[Candidate], list[Exemption]]:
    """Return ``(undispositioned_violations, stale_entries)``.

    A stale entry — one matching no violation — is itself a failure: the site was
    fixed or moved and the exemption must go, or it will silently cover the next
    site that lands on the same expression.
    """
    remaining: list[Candidate] = []
    used: set[int] = set()
    for cand in result.violations:
        hit = next(
            (
                i
                for i, e in enumerate(allowlist + deferred)
                if _matches(e, cand) and (cand.pool_db is None or i >= len(allowlist))
            ),
            None,
        )
        if hit is None:
            remaining.append(cand)
        else:
            used.add(hit)
    stale = [e for i, e in enumerate(allowlist + deferred) if i not in used]
    return remaining, stale


def check_dispositions(
    allowlist: tuple[Exemption, ...] = ALLOWLIST,
    deferred: tuple[Exemption, ...] = DEFERRED,
    today: date | None = None,
) -> list[str]:
    """Structural checks on the disposition tables. Returns problem strings.

    The settled invariant lives here: **no allowlist entry may name a database in
    ``[1..TEST_DB_POOL_MAX]``**. Allowlisting is for db-0 literals in the db-0
    guard's own tests, never for a claimable pool slot.
    """
    problems: list[str] = []
    today = today or date.today()

    for entry in allowlist:
        try:
            expr_node = ast.parse(entry.expr, mode="eval").body
        except SyntaxError:
            problems.append(
                f"ALLOWLIST {entry.path} :: {entry.expr!r} is not a parseable expression"
            )
            continue
        pool = _first_pool_db(_int_literals(expr_node))
        if pool is not None:
            problems.append(
                f"ALLOWLIST {entry.path} :: {entry.expr!r} names db {pool}, which is inside the "
                f"claimable pool [1..{TEST_DB_POOL_MAX}]. Allowlisting is for db-0 literals only; "
                "a pool slot must be claimed, not exempted."
            )
        if not entry.reason.strip():
            problems.append(f"ALLOWLIST {entry.path} :: {entry.expr!r} has no stated reason")

    for entry in deferred:
        if not (entry.blocked_on and re.fullmatch(r"#\d+", entry.blocked_on)):
            problems.append(
                f"DEFERRED {entry.path} :: {entry.expr!r} must name a blocking issue like '#2628'"
            )
        if not entry.expires:
            problems.append(f"DEFERRED {entry.path} :: {entry.expr!r} must carry an expiry date")
            continue
        try:
            when = date.fromisoformat(entry.expires)
        except ValueError:
            problems.append(
                f"DEFERRED {entry.path} :: expires={entry.expires!r} is not an ISO date"
            )
            continue
        if today > when:
            problems.append(
                f"DEFERRED {entry.path} :: {entry.expr!r} expired on {entry.expires} "
                f"(blocked on {entry.blocked_on}). Either that issue landed and this site can now "
                "use the claim API, or the deferral needs a fresh date and a fresh justification."
            )
        if not entry.reason.strip():
            problems.append(f"DEFERRED {entry.path} :: {entry.expr!r} has no stated reason")

    return problems


def format_violation(cand: Candidate) -> str:
    where = f"tests/{cand.path}:{cand.lineno}"
    callee = f"{cand.callee}(...)" if cand.callee else "call"
    # An explicit branch per kind (#2764) -- the previous two-way branch's
    # `else` silently meant "from_url", so a `db-positional` candidate
    # rendered as `from_url(7)` and named the wrong shape entirely. An
    # unrecognized kind now renders its own name rather than impersonating
    # another one, so the next kind added fails visibly instead of silently.
    if cand.kind == "db-kwarg":
        headline = f"{where}: {callee} takes db={cand.expr}"
    elif cand.kind == "db-positional":
        headline = f"{where}: {callee} takes db={cand.expr} (positional)"
    elif cand.kind == "from-url":
        headline = f"{where}: from_url({cand.expr})"
    else:
        headline = f"{where}: {cand.kind}({cand.expr})"
    lines = [headline, f"    {cand.detail}"]
    if cand.pool_db is not None:
        lines.append(
            f"    This provably names db {cand.pool_db}, inside the claimable pool "
            f"[1..{TEST_DB_POOL_MAX}]. Another live pytest process may own it."
        )
    lines.append(
        "    Use tests.db_claim.claim_test_db() (or redis_test_url() for a URL). If the site "
        "genuinely cannot, add an ALLOWLIST entry in tests/db_derivation_guard.py with a reason "
        "-- db 0 only -- or a DEFERRED entry naming a blocking issue and an expiry date."
    )
    return "\n".join(lines)


def source_fingerprint(path: Path | None = None) -> str:
    """SHA-256 of this module's own source, for the mutation harness's revert check."""
    path = path or Path(__file__)
    return hashlib.sha256(path.read_bytes()).hexdigest()
