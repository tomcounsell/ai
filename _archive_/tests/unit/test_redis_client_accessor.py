"""``utils/redis_client.py``: non-ORM Redis access follows popoto's pool (#3003).

Every hand-built ``redis.Redis.from_url(os.environ["REDIS_URL"])`` in production
code resolved its own database at call time and, under test, wrote to
production db 0. The accessor derives its clients from ``POPOTO_REDIS_DB``'s
live pool, which the autouse ``redis_test_db`` fixture has already repointed
at this process's claimed test database. These tests pin that contract and
prove one converted site through it end to end: with ``REDIS_URL`` pointed at
db 0 for the duration of the call, the write still lands in the claimed db.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import redis
from popoto.redis_db import POPOTO_REDIS_DB

from utils import redis_client
from utils.redis_client import bytes_redis, derived_redis, text_redis


def _claimed_db() -> int:
    return int(POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db", 0) or 0)


class TestTextRedis:
    def test_is_on_popotos_database_and_decodes(self):
        assert _claimed_db() != 0, "the suite must be on a claimed test db"
        client = text_redis()
        kw = client.connection_pool.connection_kwargs
        assert int(kw["db"]) == _claimed_db()
        assert kw["decode_responses"] is True
        assert kw["host"] == POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("host")
        assert int(kw["port"]) == int(POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("port"))

    def test_writes_are_visible_through_popotos_client(self):
        text_redis().set("test-accessor:round-trip", "value")
        try:
            assert POPOTO_REDIS_DB.get("test-accessor:round-trip") == b"value"
            assert text_redis().get("test-accessor:round-trip") == "value"
        finally:
            text_redis().delete("test-accessor:round-trip")

    def test_is_cached_while_the_pool_is_stable(self):
        assert text_redis() is text_redis()

    def test_rebuilds_when_popotos_pool_changes(self, monkeypatch):
        before = text_redis()
        old_pool = POPOTO_REDIS_DB.connection_pool
        same_identity_pool = redis.ConnectionPool(**dict(old_pool.connection_kwargs))
        monkeypatch.setattr(POPOTO_REDIS_DB, "connection_pool", same_identity_pool)
        try:
            after = text_redis()
            assert after is not before
            assert int(after.connection_pool.connection_kwargs["db"]) == _claimed_db()
        finally:
            same_identity_pool.disconnect()
        monkeypatch.undo()
        assert text_redis() is not after

    def test_ignores_redis_url_entirely(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        # Force a cache miss so the identity is recomputed under the hostile
        # REDIS_URL. Close the client we're evicting ourselves: nulling the
        # cache directly (rather than letting a real pool swap trigger it)
        # bypasses text_redis()'s own `previous.close()`.
        if redis_client._cached_text_client is not None:
            redis_client._cached_text_client.close()
        monkeypatch.setattr(redis_client, "_cached_text_client", None)
        monkeypatch.setattr(redis_client, "_cached_text_identity", None)
        try:
            assert int(text_redis().connection_pool.connection_kwargs["db"]) == _claimed_db()
        finally:
            if redis_client._cached_text_client is not None:
                redis_client._cached_text_client.close()


class TestOtherAccessors:
    def test_bytes_redis_is_popotos_client(self):
        assert bytes_redis() is POPOTO_REDIS_DB

    def test_derived_redis_takes_overrides_and_is_uncached(self):
        a = derived_redis(decode_responses=False, socket_timeout=None)
        b = derived_redis(decode_responses=False, socket_timeout=1.5)
        try:
            assert a is not b
            assert int(a.connection_pool.connection_kwargs["db"]) == _claimed_db()
            assert a.connection_pool.connection_kwargs["socket_timeout"] is None
            assert b.connection_pool.connection_kwargs["socket_timeout"] == 1.5
            assert a.connection_pool.connection_kwargs["decode_responses"] is False
        finally:
            a.close()
            b.close()


class TestConvertedSiteWritesToTheClaimedDb:
    """The red-before/green-after acceptance check from #3003.

    ``bridge.liveness.record_update_received`` used to build its own client
    from ``REDIS_URL``. With that variable pointed at db 0 for the call, the
    old code wrote to production and this assertion on the claimed db failed;
    through the accessor the stamp lands where popoto's pool points.
    """

    def test_liveness_stamp_lands_in_the_claimed_db(self, monkeypatch):
        from bridge import liveness

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        POPOTO_REDIS_DB.delete(liveness._UPDATE_KEY)
        try:
            liveness.record_update_received()
            assert POPOTO_REDIS_DB.exists(liveness._UPDATE_KEY) == 1
        finally:
            POPOTO_REDIS_DB.delete(liveness._UPDATE_KEY)

    def test_outbox_handler_enqueues_into_the_claimed_db(self, monkeypatch):
        from agent.output_handler import TelegramRelayOutputHandler

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        handler = TelegramRelayOutputHandler()
        key = "telegram:outbox:test-accessor-session"
        POPOTO_REDIS_DB.delete(key)
        try:
            handler._get_redis().rpush(key, "{}")
            assert POPOTO_REDIS_DB.llen(key) == 1
        finally:
            POPOTO_REDIS_DB.delete(key)


# Every top-level package that ships production Python. Enumerated rather than
# hand-picked: the first draft of this guard listed eight packages and missed
# `monitoring/`, where `bridge_watchdog.py` kept a hand-built client that the
# guard could not see. A package absent from this tuple is a blind spot, so the
# companion test below asserts the tuple still covers the tree.
_PRODUCTION_PACKAGES = (
    "agent",
    "analytics",
    "bridge",
    "config",
    "mcp_servers",
    "models",
    "monitoring",
    "reflections",
    "scripts",
    "tools",
    "ui",
    "utils",
    "worker",
)

# `utils/redis_client.py` is the one module allowed to build a client by hand;
# it is what every other site delegates to. Exempted by path so that adding
# `utils` to the scan above does not exempt the rest of the package with it.
_SANCTIONED_CONSTRUCTOR = ("utils", "redis_client.py")


_TARGET_CLASSES = {"Redis", "StrictRedis", "ConnectionPool"}


def _redis_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """Names this module binds to the ``redis`` package or its classes.

    Returns ``(module_aliases, symbol_aliases)``: ``module_aliases`` are names
    that refer to the ``redis`` package itself (``import redis``, ``import
    redis as _redis``, ``import redis.asyncio as aio`` — a submodule import is
    still part of the redis family for this purpose); ``symbol_aliases`` maps
    a bare name to the redis class it was imported as (``from redis import
    Redis``, ``from redis import ConnectionPool as Pool``).
    """
    module_aliases = {"redis"}
    symbol_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "redis":
                    module_aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "redis":
                for alias in node.names:
                    if alias.name in _TARGET_CLASSES:
                        symbol_aliases[alias.asname or alias.name] = alias.name
    return module_aliases, symbol_aliases


def _resolves_to_class(
    node: ast.expr, module_aliases: set[str], symbol_aliases: dict[str, str], targets: set[str]
) -> bool:
    """Whether ``node`` is an expression naming one of ``targets`` in the redis package.

    Handles a bare/aliased name bound via ``from redis import X`` and an
    attribute chain rooted at a redis module alias, at any nesting depth
    (``redis.Redis``, ``redis.asyncio.Redis``, ``_redis.client.Redis``).
    """
    if isinstance(node, ast.Name):
        return symbol_aliases.get(node.id) in targets
    if isinstance(node, ast.Attribute):
        if node.attr not in targets:
            return False
        root = node.value
        while isinstance(root, ast.Attribute):
            root = root.value
        return isinstance(root, ast.Name) and root.id in module_aliases
    return False


def _raw_client_constructions(path: pathlib.Path) -> list[int]:
    """Line numbers where a module builds a redis client by hand.

    Matches a direct construction (``redis.Redis(...)``, ``redis.StrictRedis(...)``,
    ``redis.ConnectionPool(...)``) or ``.from_url(...)`` on any of those, under
    any import alias or nesting depth (``import redis as _redis``, ``from
    redis import Redis``, ``redis.asyncio.Redis(...)``), by AST rather than
    text so a docstring that mentions the idiom does not count.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    module_aliases, symbol_aliases = _redis_bindings(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "from_url":
            base = func.value
            if isinstance(base, ast.Name) and base.id in module_aliases:
                hits.append(node.lineno)
            elif _resolves_to_class(base, module_aliases, symbol_aliases, _TARGET_CLASSES):
                hits.append(node.lineno)
        elif _resolves_to_class(func, module_aliases, symbol_aliases, _TARGET_CLASSES):
            hits.append(node.lineno)
    return hits


class TestNoRawClientsInProduction:
    def test_only_the_accessor_constructs_redis_clients(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        offenders = []
        for package in _PRODUCTION_PACKAGES:
            for path in (root / package).rglob("*.py"):
                if ".venv" in path.parts:
                    continue
                if path.relative_to(root).parts == _SANCTIONED_CONSTRUCTOR:
                    continue
                for line in _raw_client_constructions(path):
                    offenders.append(f"{path.relative_to(root)}:{line}")
        assert offenders == [], (
            "Raw Redis client construction outside utils/redis_client.py. Route the "
            "site through text_redis()/bytes_redis()/derived_redis() so tests can "
            "repoint it: " + ", ".join(offenders)
        )

    def test_the_scan_covers_every_production_package(self):
        """A new top-level package must be added to the scan or excluded on purpose.

        The guard is only as wide as its package list, and a missing entry is
        silent: `monitoring/` was absent from the first draft and its raw client
        survived a sweep that claimed to be exhaustive. This test fails when a
        package appears in the tree that the list does not mention.
        """
        root = pathlib.Path(__file__).resolve().parents[2]
        # Not production Python: the suite itself, docs, and build/venv dirs.
        not_production = {"tests", "docs", "logs", "data", "site", ".venv", ".git"}
        found = {
            path.parts[0]
            for path in (p.relative_to(root) for p in root.glob("*/**/*.py"))
            if path.parts[0] not in not_production and not path.parts[0].startswith(".")
        }
        unscanned = sorted(found - set(_PRODUCTION_PACKAGES))
        assert unscanned == [], (
            "These packages ship production Python but no raw-Redis-client scan "
            "reaches them. Add them to _PRODUCTION_PACKAGES, or to not_production "
            f"if they are not production code: {unscanned}"
        )

    def test_the_scanner_sees_a_raw_construction(self, tmp_path):
        sample = tmp_path / "sample.py"
        sample.write_text(
            "import os\nimport redis as _r\n\n"
            "def f():\n"
            '    a = _r.Redis.from_url(os.environ["REDIS_URL"])\n'
            "    b = _r.Redis(host='x')\n"
            "    c = _r.from_url('redis://x')\n"
            "    return a, b, c\n"
        )
        assert _raw_client_constructions(sample) == [5, 6, 7]

    def test_the_scanner_sees_aliased_names_and_connection_pool(self, tmp_path):
        """Shapes that a base-``ast.Attribute``-only matcher slips past.

        A bare/aliased name pulled in via ``from redis import Redis`` (an
        ``ast.Name`` call base, not an ``ast.Attribute``), an attribute chain
        nested through a submodule alias (``redis.asyncio.Redis``), and both
        call forms of ``ConnectionPool`` must all still be caught.
        """
        sample = tmp_path / "sample_widened.py"
        sample.write_text(
            "from redis import Redis, ConnectionPool\n"
            "import redis.asyncio as aio\n\n"
            "def f():\n"
            '    a = Redis(host="x")\n'
            '    b = aio.Redis(host="x")\n'
            '    c = ConnectionPool.from_url("redis://x")\n'
            '    d = ConnectionPool(host="x")\n'
            "    return a, b, c, d\n"
        )
        assert _raw_client_constructions(sample) == [5, 6, 7, 8]

    def test_the_accessor_module_is_the_one_sanctioned_constructor(self):
        path = pathlib.Path(redis_client.__file__)
        assert len(_raw_client_constructions(path)) >= 2


_OFFLOADERS = {"to_thread", "run_in_executor"}


def _called_names(node: ast.AST) -> set[str]:
    """Every bare callee name invoked anywhere inside ``node``."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _blocking_pool_consumers(tree: ast.AST, seeds: set[str] = frozenset()) -> set[str]:
    """Names that block the caller when called in ``tree``, transitively.

    A helper that calls a helper that calls the accessor is just as blocking as
    a direct call, and this defect arrived one frame up from the wrapped site --
    so the closure matters more than the direct match. ``seeds`` carries names
    already known to block because they were imported from another module; they
    are part of the answer even though this module does not define them.
    """
    funcs = {
        node.name: _called_names(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)  # sync defs only
    }
    tainted = set(seeds) | {name for name, calls in funcs.items() if "bytes_redis" in calls}
    changed = True
    while changed:
        changed = False
        for name, calls in funcs.items():
            if name not in tainted and calls & tainted:
                tainted.add(name)
                changed = True
    return tainted


def _sync_def_names(tree: ast.AST) -> set[str]:
    """Names of sync functions this module actually defines."""
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _imported_names(tree: ast.AST) -> list[tuple[str, str, str]]:
    """``(source_module, original_name, local_name)`` for every from-import.

    Lazy function-body imports count: ``bridge/email_bridge.py`` reaches the
    blocking pool through exactly such an import.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                out.append((node.module, alias.name, alias.asname or alias.name))
    return out


def _unwrapped_async_calls(tree: ast.AST, tainted: set[str]) -> list[tuple[int, str]]:
    """Calls to ``tainted`` names inside an ``async def``, not handed to a thread."""
    offloaded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _OFFLOADERS:
                # Everything in the argument list runs off the loop, whether it
                # is called there or merely referenced for the threadpool.
                for sub in ast.walk(node):
                    offloaded.add(id(sub))

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in offloaded:
                continue
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in tainted:
                hits.append((sub.lineno, name))
    return hits


class TestBlockingPoolNeverReachesAnEventLoop:
    """``bytes_redis()`` is popoto's own client, on a ``BlockingConnectionPool``.

    On pool exhaustion a checkout *blocks* rather than raising, and
    ``socket_timeout`` does not cover a pool checkout -- ceiling 20s. Reached
    from a coroutine, that stalls the whole bridge event loop. ``text_redis()``
    is deliberately NOT in scope here: it carries its own bounded pool, which
    raises on exhaustion under a socket timeout, so it is a different and much
    smaller hazard.

    The defect this guard was written for sat one frame above a correctly
    wrapped call: ``resolve_customer`` offloaded all four of its own Redis
    touches, while a sibling coroutine called a sync helper that reached the
    same pool. Pinning the one line would not have caught the next one.
    """

    def test_no_coroutine_reaches_the_blocking_pool_synchronously(self):
        repo_root = pathlib.Path(__file__).resolve().parents[2]

        trees: dict[pathlib.Path, ast.AST] = {}
        for package in _PRODUCTION_PACKAGES:
            for path in sorted((repo_root / package).rglob("*.py")):
                try:
                    trees[path] = ast.parse(path.read_text())
                except (SyntaxError, UnicodeDecodeError):
                    continue

        # Taint is MODULE-QUALIFIED. `_get_redis` is a name six modules share,
        # and only `bridge.routing`'s returns `bytes_redis()`; a global name set
        # would report the other five as offenders. Taint crosses a module
        # boundary only along a real import edge.
        modname = {
            path: ".".join(path.relative_to(repo_root).with_suffix("").parts) for path in trees
        }
        by_name = {modname[path]: (path, tree) for path, tree in trees.items()}

        # `blocks_here` is what stalls the loop when called in that module,
        # imported names included. `exports` is the subset the module actually
        # defines -- only those propagate outward along an import edge.
        blocks_here: dict[str, set[str]] = {mod: set() for mod in by_name}
        changed = True
        while changed:
            changed = False
            exports = {mod: blocks_here[mod] & _sync_def_names(by_name[mod][1]) for mod in by_name}
            for mod, (_path, tree) in by_name.items():
                seeds = {
                    local
                    for source, original, local in _imported_names(tree)
                    if original in exports.get(source, ())
                }
                grown = _blocking_pool_consumers(tree, seeds)
                if grown - blocks_here[mod]:
                    blocks_here[mod] |= grown
                    changed = True

        offenders = []
        for mod, (path, tree) in by_name.items():
            # The accessor itself is the root of the class, under whatever
            # local name the module imported it as.
            names = blocks_here[mod] | {
                local
                for source, original, local in _imported_names(tree)
                if source == "utils.redis_client" and original == "bytes_redis"
            }
            for lineno, name in _unwrapped_async_calls(tree, names):
                offenders.append(f"{path.relative_to(repo_root)}:{lineno} {name}()")

        assert not offenders, (
            "These coroutines reach popoto's BlockingConnectionPool synchronously. "
            "A checkout there blocks the event loop for up to 20s and is not "
            "covered by socket_timeout. Wrap the call in asyncio.to_thread(). "
            f"Found: {offenders}"
        )

    def test_the_taint_closure_follows_indirect_callers(self):
        """The transitive step is the load-bearing half; prove it independently."""
        tree = ast.parse(
            "def leaf():\n"
            "    return bytes_redis()\n\n"
            "def middle():\n"
            "    return leaf()\n\n"
            "def unrelated():\n"
            "    return 1\n"
        )
        assert _blocking_pool_consumers(tree) == {"leaf", "middle"}

    def test_an_offloaded_reference_counts_as_safe(self):
        """``to_thread(helper, arg)`` hands over a reference, never an ast.Call."""
        tree = ast.parse(
            "async def f():\n"
            "    await asyncio.to_thread(helper)\n"
            "    return await asyncio.to_thread(helper, 1)\n"
        )
        assert _unwrapped_async_calls(tree, {"helper"}) == []

    def test_a_direct_call_in_a_coroutine_is_reported(self):
        tree = ast.parse("async def f():\n    return helper()\n")
        assert _unwrapped_async_calls(tree, {"helper"}) == [(2, "helper")]


@pytest.fixture(autouse=True)
def _reset_text_client_cache():
    yield
    # Close before dropping the reference: the cache holds the only handle to
    # this client's pool, so nulling it without closing leaks a live pool per
    # test -- the same hazard `test_ignores_redis_url_entirely` handles inline.
    client = redis_client._cached_text_client
    redis_client._cached_text_client = None
    redis_client._cached_text_identity = None
    if client is not None:
        client.close()
