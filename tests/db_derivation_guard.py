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

So this guard enumerates nothing about *what is being called*. It flags EVERY
``db=`` keyword argument passed to ANY call anywhere under ``tests/``, and every
``from_url(...)`` argument, and requires the VALUE to match one of two shapes.
Anything else is a violation that must be dispositioned in writing.

Measured on the tree at the time of writing: all 17 ``db=`` keyword arguments in
``tests/`` are Redis constructions, so ignoring the callee name costs zero false
positives while making the guard immune to a constructor name nobody thought of.
All 17 ``Redis`` calls parse as ``ast.Attribute`` (``redis.Redis(...)``) and
ZERO as ``ast.Name`` — a matcher reading only ``node.func.id`` would match
nothing and be vacuously green forever. :func:`_terminal_name` reads ``.attr``
for ``ast.Attribute`` and ``.id`` for ``ast.Name`` for exactly that reason.

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
  — and it **accepts** a bare name like ``'divergent_db'``, because a name in
  isolation has no integer in it to find.
- :func:`apply_dispositions` is what actually stops laundering. Its
  ``cand.pool_db is None or i >= len(allowlist)`` condition refuses to let any
  ``ALLOWLIST`` entry cover a candidate the *scan* proved names a pool slot,
  and the scan does have the bindings, so ``divergent_db`` arrives carrying
  ``pool_db=14``.

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
# ``claim_scratch_test_db`` has no call sites yet: #2628 adds it to
# tests/db_claim.py, and the deferred `divergent_db` site is waiting on it. The
# entry is inert until then and becomes live the moment that lands. Do not
# garbage-collect it as dead code in the meantime.
CLAIM_FUNCS = frozenset({"claim_test_db", "claim_scratch_test_db"})

# The sanctioned source of a Redis URL: ``tests.db_claim.redis_test_url()`` and
# the same-named pytest fixture in conftest, which returns its value.
CLAIM_URL_NAMES = frozenset({"redis_test_url"})

# Used ONLY to scope the opaque-``**``-splat leg (see `_splat_candidate`). Every
# other route in this module is deliberately callee-agnostic; this is the one
# place an enumeration is the lesser evil, because `**` forwarding is ubiquitous
# in test helpers and a callee-agnostic version flagged 183 unrelated sites.
REDIS_CONSTRUCTORS = frozenset({"Redis", "StrictRedis", "from_url"})

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
# is reported on every run. These are NOT allowlist entries: the invariant check
# below would reject at least one of them outright, which is the point.
DEFERRED: tuple[Exemption, ...] = (
    Exemption(
        path="integration/test_notify_isolation.py",
        expr="int(kw.get('db', 0) or 0)",
        reason=(
            "Derives its db from another client's connection_kwargs — the exact route this "
            "guard rejects. The one-line conversion to claim_test_db() is behaviour-preserving "
            "(pub/sub delivery is server-global, per the docstring at :53-57) but this file is "
            "under #2628's exclusive lock and #2628 is folding the conversion in directly."
        ),
        blocked_on="#2628",
        expires="2026-11-06",
    ),
    Exemption(
        path="unit/test_conftest_isolation_guards.py",
        expr="divergent_db",
        reason=(
            "Hand-picks a second pool slot (15, or 14 if this process claimed 15) and FLUSHES "
            "it, so it can be flushed out from under a concurrent process. Fixing it needs a "
            "second independently-claimed db — claim_scratch_test_db() — which does not exist "
            "in tests/db_claim.py yet; #2628 is adding it. Deliberately DEFERRED and not "
            "ALLOWLIST: the scan resolves this name's binding to pool slot 14, and "
            "apply_dispositions refuses to let an ALLOWLIST entry cover a candidate carrying "
            "a pool_db. (check_dispositions would accept the bare name — it sees only the "
            "expression text, with no bindings — so apply_dispositions is the layer doing the "
            "work here.) Weakening either is exactly the decay this guard exists to prevent."
        ),
        blocked_on="#2628",
        expires="2026-11-06",
    ),
)


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

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._stack:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bindings[self._stack[-1]].setdefault(target.id, []).append(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
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
    kind: str  # "db-kwarg" | "from-url"
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
    """Shape S2: a name bound exactly once in this function to a claim call."""
    if enclosing_fn is None:
        return False, f"{name.id!r} is not a local name in any function scope", None
    bound = bindings.get(enclosing_fn, {}).get(name.id)
    if not bound:
        return False, f"{name.id!r} has no local binding in the enclosing function", None
    if len(bound) > 1:
        return False, f"{name.id!r} is rebound {len(bound)} times in the enclosing function", None
    if _is_claim_call(bound[0]):
        return True, f"{name.id!r} = {ast.unparse(bound[0])}", bound[0]
    return False, f"{name.id!r} = {ast.unparse(bound[0])}, which is not a claim call", bound[0]


def _splat_candidate(
    value: ast.AST, rel_path: str, call: ast.Call, callee: str | None
) -> Candidate | None:
    """A candidate for a ``**`` splat that could be carrying a ``db``.

    Two shapes, judged differently:

    - **a dict literal with a ``"db"`` key** -- callee-agnostic, like the rest
      of route 1. The value is visible, so it is judged exactly as a written-out
      ``db=`` would be. A dict literal with no ``db`` key is provably safe and
      yields no candidate.
    - **an opaque ``**name``** -- undecidable, so a violation, but *only* for a
      callee that looks like a Redis construction.

    That callee scoping is a deliberate, bounded exception to the module's
    otherwise callee-agnostic polarity, and it is worth naming. Route 1 can
    afford to ignore the callee because every one of the 17 ``db=`` sites in
    this tree is a Redis construction. ``**`` is not like that: it is the
    ordinary way test helpers forward kwargs, and flagging it everywhere
    produced **183 violations across 100+ unrelated files** on the first
    attempt. A guard that fires on every test helper in the repo does not get
    fixed, it gets deleted -- and then the real hole is open again with no
    guard at all.

    So the enumeration here buys usability at a known cost: a Redis client
    constructed through an alias nobody listed, receiving an opaque splat, is
    invisible. Today that costs nothing (the tree has 191 ``**`` call sites and
    zero are Redis constructions), and the dict-literal leg above stays fully
    callee-agnostic.
    """
    if isinstance(value, ast.Dict):
        for key, val in zip(value.keys, value.values, strict=False):
            if isinstance(key, ast.Constant) and key.value == "db":
                return Candidate(
                    path=rel_path,
                    lineno=call.lineno,
                    kind="db-kwarg",
                    expr=ast.unparse(val),
                    callee=callee,
                    ok=_is_claim_call(val),
                    detail=(
                        "db passed through a ** dict literal; value is not a "
                        "call to claim_test_db()/claim_scratch_test_db()"
                    ),
                    pool_db=None if _is_claim_call(val) else _first_pool_db(_int_literals(val)),
                )
        return None

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
            # under tests/, zero of them Redis constructions), but the deferred
            # test_notify_isolation.py site already works with connection_kwargs
            # dicts, so `redis.Redis(**kw)` is one refactor away.
            if kw.arg is None:
                splat = _splat_candidate(kw.value, rel_path, node, callee)
                if splat is not None:
                    result.candidates.append(splat)
                continue
            if kw.arg != "db":
                continue
            value = kw.value
            pool_db = None
            if _is_claim_call(value):
                ok, detail = True, "direct claim-API call"
            elif isinstance(value, ast.Name):
                ok, detail, bound = _resolve_one_hop(
                    value, _enclosing_function(node, parents), binder.bindings
                )
                if not ok and bound is not None:
                    pool_db = _first_pool_db(_int_literals(bound))
            else:
                ok = False
                detail = "value is not a call to claim_test_db()/claim_scratch_test_db()"
                pool_db = _first_pool_db(_int_literals(value))
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

        # --- Route 2: from_url(<url>) --------------------------------------
        if callee == "from_url" and node.args:
            arg = node.args[0]
            pool_db = None
            if isinstance(arg, ast.Call) and _terminal_name(arg.func) in CLAIM_URL_NAMES:
                ok, detail = True, "direct claim-API URL call"
            elif isinstance(arg, ast.Name) and arg.id in CLAIM_URL_NAMES:
                # The pytest fixture parameter of the same name. It is a
                # function argument, not a local assignment, so S2 cannot
                # resolve it; the identifier itself is the sanctioned source.
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
    lines = [
        f"{where}: {callee} takes db={cand.expr}"
        if cand.kind == "db-kwarg"
        else f"{where}: from_url({cand.expr})",
        f"    {cand.detail}",
    ]
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
