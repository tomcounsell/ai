"""Guard: every test subprocess that can reach Popoto must inherit the parent's claimed test db.

Issue #2763. ``tests/conftest.py``'s autouse ``redis_test_db`` fixture isolates
the *parent* pytest process by rebinding Popoto's client objects in memory. That
state cannot cross a ``fork``: the only channel to a child process is the
environment, and popoto resolves ``REDIS_URL`` at import time, falling back to
``redis://localhost:6379`` — **db0, production** — when the variable is unset.
So a test that shells out to a Python child without an explicit env silently
reads and writes production Redis.

``tests.db_claim.subprocess_env()`` is the bridge: it writes
``REDIS_URL=redis://127.0.0.1:<port>/<claimed db>`` into the child's env. This
guard fails the suite when a call site reintroduces the un-bridged shape.

The predicate
-------------
A ``subprocess.{run,Popen,check_output,check_call,call}`` call is IN SCOPE when
its **first positional argument** mentions a Python interpreter or a repo entry
point: ``sys.executable``, an identifier containing ``PYTHON``, a ``"-m"``
element, the ``WRAPPER`` constant, or a string naming a ``scripts/`` path or a
``python*`` binary.

``cwd=`` is deliberately **not** part of the predicate. Matching on the spelling
of a ``cwd`` variable is what let an earlier draft of this guard go green
without the sweep: converted files spell the repo root a dozen different ways
(``REPO_ROOT``, ``repo_root``, an inline ``Path(__file__).parent.parent.parent``),
and ``tests/unit/test_sdlc_tool_wrapper.py`` reaches Python against the repo
from ``cwd=str(tmp_path)`` — plausibly the very shape that wrote the stray db0
row this issue was filed against. What determines whether a child can import
popoto is *what is executed*, not where it is executed from.

Why ``git``-in-``tmp_path`` calls are excluded
----------------------------------------------
A large share of the suite's subprocess calls run ``git`` inside a scratch
repository under ``tmp_path``. A ``git`` child never imports Python, never
imports popoto, and therefore cannot reach Redis at all; converting those sites
would add an import and churn to dozens of files for zero isolation gain.
``SKIP_ARGV0`` names that exclusion explicitly, as one reviewable line, rather
than hiding it inside the predicate.

How to satisfy the guard
------------------------
Pass ``env=subprocess_env(...)``::

    from tests.db_claim import subprocess_env

    subprocess.run([sys.executable, "-m", "tools.thing"], env=subprocess_env())

Four shapes are accepted, because the fixes this guard exists to drive produce
all four:

1. a direct call — ``env=subprocess_env(project_root=str(REPO_ROOT))``;
2. a local variable assigned exactly once from ``subprocess_env(...)`` and
   thereafter only stripped — ``env = subprocess_env(); env.pop("X", None)``
   (the strips are load-bearing in several tests, so a rule that accepted only
   the direct call would reject this plan's own remedy);
3. a module-level delegator resolved **one** hop — ``def _w(**k): return
   subprocess_env(**k)`` used as ``env=_w()``, or the assign-mutate-return form
   of (2) ending ``return env``;
4. the same delegator reached as a method — ``env=self._w()`` / ``cls._w()``,
   resolved against the nearest enclosing class.

A helper that derives its db from anything other than ``subprocess_env`` — for
instance by reading it back out of
``POPOTO_REDIS_DB.connection_pool.connection_kwargs`` — is a violation, however
correct it happens to be today. The check resolves the ``env=`` value to an AST
node, never to source text: a substring test on ``"subprocess_env"`` would pass
``_isolated_subprocess_env()`` and ``_subprocess_env()``, which are precisely
the helpers this guard exists to kill.

Exemptions are entries in ``ALLOWLIST``, each carrying a written reason. Only
two reasons are permitted; see that constant. **When reachability is unclear,
convert the site — do not allowlist it.** A parametrized argv such as
``[sys.executable, "-m", module, *argv]`` reaches repo code even though a static
scan cannot name the module.

Self-tests
----------
This module lives inside its own scan root, so its fixtures are Python **source
strings** handed to :func:`scan_source`, never live importable code — a fixture
written as real code would report itself on the first green run, and the obvious
"fix" would be to allowlist it, quietly destroying the predicate's only proof.
The guard's own file is additionally excluded from the walk.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
GUARD_PATH = Path(__file__).resolve()

# subprocess entry points that fork a child.
SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "check_call", "call"})

# Programs that cannot import popoto and so cannot reach Redis, keyed by argv[0]
# basename. Resolved statically: a non-literal argv[0] is never skipped.
SKIP_ARGV0 = frozenset({"git"})

# Per-site exemptions, keyed "<repo-relative path>:<subprocess.<fn>( call-start line>".
#
# Exactly two reasons are permitted, and the reason is written on the line:
#   [#2628]             the file is owned by / collides with open PR #2683, so
#                       this PR must not touch it. Allowlisting here edits no
#                       forbidden file, which is what makes "guard green" and
#                       "no #2683-owned file in the diff" simultaneously
#                       satisfiable. Remove the entry and convert the site when
#                       #2628 lands.
#   [standalone-script] the child is a single-file .claude/hooks/ validator or
#                       scripts/ utility that imports no repo package.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # [#2628] owned by open PR #2683 — No-Go here, fix there; convert when #2628 lands
        "tests/unit/test_redis_flush_guard_prod.py:54",
        # [#2628] same file, same owner; helper sets PYTHONPATH but not REDIS_URL
        "tests/unit/test_redis_flush_guard_prod.py:555",
        # [#2628] same file, same owner
        "tests/unit/test_redis_flush_guard_prod.py:579",
        # [#2628] same file, same owner
        "tests/unit/test_redis_flush_guard_prod.py:648",
        # [#2628] same file, same owner. Beyond the five sites the plan enumerated:
        # env={**_subprocess_env(), "VIRTUAL_ENV": ...} is a Dict literal, so it
        # resolves to no helper at all. Same disposition — #2683 owns the fix.
        "tests/unit/test_redis_flush_guard_prod.py:666",
        # [#2628] owned by open PR #2683 — No-Go here, fix there
        "tests/unit/test_conftest_isolation_guards.py:463",
        # [#2628] PR #2683 deletes an adjacent class at :226; both edits land in one hunk
        "tests/unit/test_youtube_search.py:219",
        # [standalone-script] starred argv0 (*env_git) SKIP_ARGV0 cannot resolve; child is git
        "tests/unit/test_sdlc_next_skill.py:209",
    }
)

# Methods that would rebuild or re-point an env dict after it was built by
# subprocess_env(). `.pop(<literal>, None)` and `.update(...)` are the two the
# plan's remedies use; pure reads (`.get`, `.copy`, `.items`, …) are harmless
# and are not rejected, so this is written as a denylist of mutators rather than
# an allowlist of two names.
_DISALLOWED_MUTATORS = frozenset({"setdefault", "clear", "popitem", "__setitem__"})


# --------------------------------------------------------------------------
# argv predicate
# --------------------------------------------------------------------------


def _string_constants(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return out


def _identifiers(node: ast.AST) -> list[str]:
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
    return out


def _argv0_is_skipped(argv: ast.AST) -> bool:
    """True when argv[0] is a literal program named in SKIP_ARGV0."""
    first: ast.AST | None = None
    if isinstance(argv, (ast.List, ast.Tuple)):
        if not argv.elts:
            return False
        first = argv.elts[0]
    elif isinstance(argv, ast.Constant) and isinstance(argv.value, str):
        # shell=True form: "git status --porcelain"
        parts = argv.value.split()
        return bool(parts) and Path(parts[0]).name in SKIP_ARGV0
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return Path(first.value).name in SKIP_ARGV0
    return False


def _argv_reaches_python(argv: ast.AST) -> bool:
    """True when the first positional arg names a Python interpreter or repo entry point."""
    for ident in _identifiers(argv):
        upper = ident.upper()
        if ident == "executable" or "PYTHON" in upper or "WRAPPER" in upper:
            return True
    for s in _string_constants(argv):
        if s == "-m" or " -m " in s:
            return True
        if "scripts/" in s:
            return True
        if Path(s).name.lower().startswith("python"):
            return True
    return False


def _is_subprocess_call(node: ast.Call, ctx: _ScanContext) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr not in SUBPROCESS_FUNCS:
            return False
        root = func.value
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and root.id == "subprocess"
    if isinstance(func, ast.Name):
        return func.id in ctx.subprocess_names
    return False


# --------------------------------------------------------------------------
# env= resolution
# --------------------------------------------------------------------------


class _ScanContext:
    """Module-level facts the env= check needs: imports, helpers, parent links."""

    def __init__(self, tree: ast.Module, path: str) -> None:
        self.tree = tree
        self.path = path
        self.imports_db_claim = False
        self.subprocess_names: set[str] = set()
        self.module_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.parents: dict[ast.AST, ast.AST] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[-1] == "db_claim":
                    self.imports_db_claim = True
                if mod == "subprocess":
                    self.subprocess_names.update(
                        a.asname or a.name for a in node.names if a.name in SUBPROCESS_FUNCS
                    )
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[-1] == "db_claim":
                        self.imports_db_claim = True
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.module_funcs[node.name] = node

    def enclosing(self, node: ast.AST, kinds: tuple[type, ...]) -> ast.AST | None:
        cur = self.parents.get(node)
        while cur is not None:
            if isinstance(cur, kinds):
                return cur
            cur = self.parents.get(cur)
        return None


def _is_direct_subprocess_env(func: ast.AST) -> bool:
    """`subprocess_env` or `<mod>.subprocess_env` — exact, never a substring match."""
    if isinstance(func, ast.Name):
        return func.id == "subprocess_env"
    if isinstance(func, ast.Attribute):
        return func.attr == "subprocess_env"
    return False


def _name_is_clean(name: str, scope: ast.AST | None, ctx: _ScanContext) -> bool:
    """`name` is bound exactly once from subprocess_env(...) and only stripped after."""
    if scope is None:
        return False
    assigns: list[ast.Assign] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    assigns.append(node)
                elif isinstance(target, ast.Subscript):
                    if isinstance(target.value, ast.Name) and target.value.id == name:
                        return False  # env["REDIS_URL"] = ... — hand-derived db
                elif isinstance(target, (ast.Tuple, ast.List)):
                    if any(isinstance(e, ast.Name) and e.id == name for e in target.elts):
                        return False
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name:
                return False
            if isinstance(target, ast.Subscript):
                if isinstance(target.value, ast.Name) and target.value.id == name:
                    return False
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return False
        elif isinstance(node, ast.withitem):
            if isinstance(node.optional_vars, ast.Name) and node.optional_vars.id == name:
                return False
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == name
                and func.attr in _DISALLOWED_MUTATORS
            ):
                return False
    if len(assigns) != 1:
        return False
    value = assigns[0].value
    return (
        isinstance(value, ast.Call)
        and _is_direct_subprocess_env(value.func)
        and ctx.imports_db_claim
    )


def _body_statements(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def _delegator_is_clean(func: ast.FunctionDef | ast.AsyncFunctionDef, ctx: _ScanContext) -> bool:
    """One hop, never recursive: single `return subprocess_env(...)`, or assign-mutate-return."""
    body = _body_statements(func)
    if not body:
        return False
    last = body[-1]
    if not isinstance(last, ast.Return) or last.value is None:
        return False
    if len(body) == 1:
        value = last.value
        return (
            isinstance(value, ast.Call)
            and _is_direct_subprocess_env(value.func)
            and ctx.imports_db_claim
        )
    if isinstance(last.value, ast.Name):
        return _name_is_clean(last.value.id, func, ctx)
    return False


def _env_value_is_clean(value: ast.expr, call: ast.Call, ctx: _ScanContext) -> bool:
    if isinstance(value, ast.Call):
        func = value.func
        if _is_direct_subprocess_env(func):
            return ctx.imports_db_claim
        if isinstance(func, ast.Name):
            helper = ctx.module_funcs.get(func.id)
            return _delegator_is_clean(helper, ctx) if helper is not None else False
        if isinstance(func, ast.Attribute):
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id in {"self", "cls"}:
                klass = ctx.enclosing(call, (ast.ClassDef,))
                if klass is None:
                    return False
                for stmt in klass.body:
                    if (
                        isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and stmt.name == func.attr
                    ):
                        # @staticmethod / @classmethod wrapping is irrelevant to
                        # the body check, so no unwrapping is needed beyond
                        # locating the FunctionDef itself.
                        return _delegator_is_clean(stmt, ctx)
                return False  # not found in the enclosing class — a violation, not a pass
        return False
    if isinstance(value, ast.Name):
        scope = ctx.enclosing(call, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module))
        return _name_is_clean(value.id, scope, ctx)
    return False


# --------------------------------------------------------------------------
# scanner
# --------------------------------------------------------------------------


def scan_source(src: str, path: str = "<source>") -> list[str]:
    """Return ``"path:line — reason"`` for every in-scope call lacking a clean env=."""
    tree = ast.parse(src)
    ctx = _ScanContext(tree, path)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node, ctx):
            continue
        if not node.args:
            continue
        argv = node.args[0]
        if _argv0_is_skipped(argv):
            continue
        if not _argv_reaches_python(argv):
            continue
        if f"{path}:{node.lineno}" in ALLOWLIST:
            continue
        env_kw = next((k for k in node.keywords if k.arg == "env"), None)
        if env_kw is None:
            violations.append(f"{path}:{node.lineno} — no env= (child inherits ambient REDIS_URL)")
        elif not _env_value_is_clean(env_kw.value, node, ctx):
            expr = ast.unparse(env_kw.value)
            violations.append(
                f"{path}:{node.lineno} — env={expr} does not resolve to subprocess_env()"
            )
    return violations


def scan_tests_tree() -> tuple[list[str], int]:
    """Walk ``tests/**/*.py`` (excluding this guard). Returns (violations, files_scanned)."""
    violations: list[str] = []
    scanned = 0
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.resolve() == GUARD_PATH:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        scanned += 1
        violations.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return violations, scanned


REMEDY = (
    "Remedy: pass env=subprocess_env(...) "
    "(from tests.db_claim import subprocess_env). Extra variables go through "
    "subprocess_env(**extra); never hand-build a dict or re-derive the db number. "
    "See this module's docstring for the four accepted shapes and the two "
    "permitted ALLOWLIST reasons."
)


def test_every_test_subprocess_inherits_the_claimed_test_db():
    violations, scanned = scan_tests_tree()
    # An empty scan must never read as success (#2763): if a future refactor
    # moves the tests tree, the guard would silently become a no-op.
    assert scanned > 0, f"no Python files found under {TESTS_ROOT} — the guard scanned nothing"
    assert not violations, (
        f"{len(violations)} test subprocess call site(s) launch Python or a repo "
        f"entry point without inheriting this pytest process's claimed Redis test "
        f"db, so the child resolves REDIS_URL to production db0 (#2763):\n\n"
        + "\n".join(violations)
        + f"\n\n{REMEDY}"
    )


# --------------------------------------------------------------------------
# self-tests — fixtures are SOURCE STRINGS, never live code in the scan root
# --------------------------------------------------------------------------

_REJECTED_REDERIVED_HELPER = """
import subprocess
import sys
from popoto import redis_db as rdb


def _my_subprocess_env():
    env = os.environ.copy()
    kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
    env["REDIS_URL"] = f"redis://127.0.0.1:6379/{kwargs['db']}"
    return env


def test_thing():
    subprocess.run([sys.executable, "-m", "tools.thing"], env=_my_subprocess_env())
"""

_ACCEPTED_THIN_DELEGATOR = """
import subprocess
import sys

from tests.db_claim import subprocess_env


def _w(**k):
    return subprocess_env(**k)


def test_thing():
    subprocess.run([sys.executable, "-m", "tools.thing"], env=_w())
"""

_ACCEPTED_ASSIGN_MUTATE_RETURN = """
import subprocess
import sys

from tests.db_claim import subprocess_env


def _w():
    e = subprocess_env()
    e.pop("X", None)
    return e


def test_thing():
    subprocess.run([sys.executable, "-m", "tools.thing"], env=_w())
"""

_ACCEPTED_STATICMETHOD_DELEGATOR = """
import subprocess
import sys

from tests.db_claim import subprocess_env


class TestThing:
    @staticmethod
    def _w():
        e = subprocess_env()
        e.pop("X", None)
        return e

    def test_thing(self):
        subprocess.run([sys.executable, "-m", "tools.thing"], env=self._w())
"""


def test_self_test_rederived_helper_is_reported():
    """A helper that reads the db back out of the live client is NOT subprocess_env."""
    violations = scan_source(textwrap.dedent(_REJECTED_REDERIVED_HELPER), "fixture.py")
    assert len(violations) == 1, violations
    assert "fixture.py:" in violations[0]


def test_self_test_thin_delegator_is_accepted():
    """`def _w(**k): return subprocess_env(**k)` is a correct helper, not a violation."""
    assert scan_source(textwrap.dedent(_ACCEPTED_THIN_DELEGATOR), "fixture.py") == []


def test_self_test_assign_mutate_return_delegator_is_accepted():
    """The strips are load-bearing in several tests; the guard must accept them."""
    assert scan_source(textwrap.dedent(_ACCEPTED_ASSIGN_MUTATE_RETURN), "fixture.py") == []


def test_self_test_staticmethod_delegator_is_accepted():
    """`env=self._w()` resolves through the enclosing class, one hop."""
    assert scan_source(textwrap.dedent(_ACCEPTED_STATICMETHOD_DELEGATOR), "fixture.py") == []
