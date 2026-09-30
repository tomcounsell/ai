"""Shared fixtures. Database tests need the local Postgres from README.md
(database `cori`, trust auth on localhost, migrations applied) and skip
when it is unreachable."""

import os

# popoto connects at import time and reads only REDIS_URL, so the whole
# session is bound by whichever module imports it first. Bound here, before
# any import that could reach popoto, so tests use db 1 (CLAUDE.md) whatever
# order they collect in.
os.environ["REDIS_URL"] = os.environ.get("CORI_REDIS_URL", "redis://localhost:6379/1")


def _bind_popoto_to_the_test_database() -> None:
    """Point popoto's client at REDIS_URL, pool and all.

    The environment variable above is not enough on its own: popoto ships a
    pytest plugin as a `pytest11` entry point, and pytest loads plugins
    before any conftest, so `popoto.redis_db` was already imported and its
    client already built from whatever REDIS_URL held then, which is db 0.

    The pool is replaced on the existing client object rather than the
    client being rebuilt, because every popoto module imported
    POPOTO_REDIS_DB by value at import time and would keep the old one.
    This is popoto's own `pytest_plugin._swap_db` move, from the URL rather
    than from a database number, so host and port travel with it.
    """
    try:
        import redis
        from popoto import redis_db
    except ImportError:  # popoto is optional for the rest of the suite
        return
    client = redis_db.POPOTO_REDIS_DB
    old_pool = client.connection_pool
    client.connection_pool = redis.ConnectionPool.from_url(
        os.environ["REDIS_URL"], socket_timeout=5, socket_connect_timeout=5
    )
    old_pool.disconnect()


_bind_popoto_to_the_test_database()

import shutil
import subprocess
import uuid

import psycopg
import pytest

PGHOST = os.environ.get("CORI_PGHOST", "localhost")
PGPORT = os.environ.get("CORI_PGPORT", "5432")
PGDATABASE = os.environ.get("CORI_PGDATABASE", "cori")


def dsn(role: str) -> str:
    return f"postgresql://{role}@{PGHOST}:{PGPORT}/{PGDATABASE}"


def _reachable() -> bool:
    try:
        with psycopg.connect(dsn("kernel_rw"), connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


requires_postgres = pytest.mark.skipif(
    not _reachable(), reason="local Postgres with the cori roles is not reachable"
)


@pytest.fixture
async def kernel():
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        yield conn


@pytest.fixture
async def context():
    async with await psycopg.AsyncConnection.connect(dsn("context_ro")) as conn:
        yield conn


@pytest.fixture
async def migrator():
    async with await psycopg.AsyncConnection.connect(dsn("migrator")) as conn:
        yield conn


@pytest.fixture
def space():
    """A fresh space id per test so tests never see each other's rows."""
    return f"space-{uuid.uuid4().hex[:8]}"


def _container_ready() -> bool:
    """apple/container with its system running. Sandbox tests need a real VM
    and are skipped where there is none (plan 06, tasks 3 to 8)."""
    if shutil.which("container") is None:
        return False
    try:
        r = subprocess.run(
            ["container", "system", "status"], capture_output=True, timeout=30
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


requires_container = pytest.mark.skipif(
    not _container_ready(), reason="apple/container is not running on this machine"
)


@pytest.fixture
def spaces_for(monkeypatch):
    """Make the given space ids real to `kernel.memory` for one test.

    Seams §3.8 gives `write` and `propose` no `spaces` argument, so the
    manifest set they check against is the module name `kernel.memory.load_all`
    and a test patches that. The manifests are the smallest a `Space` accepts:
    one root, kind `client`, ceiling `propose` (rank 1), because nothing on the memory path
    reads any field but the id.
    """

    def _for(*ids: str):
        from schemas.space import Space

        spaces = {
            space_id: Space(
                id=space_id,
                kind="client",
                roots=[f"/{space_id}"],
                max_effect_class="propose",
            )
            for space_id in ids
        }
        monkeypatch.setattr("kernel.memory.load_all", lambda: spaces)
        return spaces

    return _for
