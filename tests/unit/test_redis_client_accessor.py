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
        redis_client._cached_text_client = None
        redis_client._cached_text_identity = None
        assert int(text_redis().connection_pool.connection_kwargs["db"]) == _claimed_db()


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


_PRODUCTION_PACKAGES = (
    "agent",
    "bridge",
    "tools",
    "reflections",
    "ui",
    "worker",
    "models",
    "config",
)


def _raw_client_constructions(path: pathlib.Path) -> list[int]:
    """Line numbers where a module builds a redis client by hand.

    Matches ``redis.Redis(...)``, ``redis.StrictRedis(...)``,
    ``redis.from_url(...)`` and ``redis.Redis.from_url(...)`` (also under an
    alias such as ``import redis as _redis``), by AST rather than text so a
    docstring that mentions the idiom does not count.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []
    aliases = {"redis"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "redis":
                    aliases.add(alias.asname or "redis")
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "from_url":
            base = func.value
            if isinstance(base, ast.Attribute) and base.attr in {"Redis", "StrictRedis"}:
                base = base.value
            if isinstance(base, ast.Name) and base.id in aliases:
                hits.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in {"Redis", "StrictRedis"}:
            if isinstance(func.value, ast.Name) and func.value.id in aliases:
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
                for line in _raw_client_constructions(path):
                    offenders.append(f"{path.relative_to(root)}:{line}")
        assert offenders == [], (
            "Raw Redis client construction outside utils/redis_client.py. Route the "
            "site through text_redis()/bytes_redis()/derived_redis() so tests can "
            "repoint it: " + ", ".join(offenders)
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

    def test_the_accessor_module_is_the_one_sanctioned_constructor(self):
        path = pathlib.Path(redis_client.__file__)
        assert len(_raw_client_constructions(path)) >= 2


@pytest.fixture(autouse=True)
def _reset_text_client_cache():
    yield
    redis_client._cached_text_client = None
    redis_client._cached_text_identity = None
