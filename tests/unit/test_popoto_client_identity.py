"""The popoto client identity invariant (#2771).

One object, one authority per fact. ``popoto.redis_db.POPOTO_REDIS_DB`` is the
same Python object for the whole process, its ``connection_pool`` is the same
object for the whole session, every ``popoto.*`` submodule that captured the
symbol holds *that* object, and the pool is popoto's own
``BlockingConnectionPool`` carrying popoto's own connection cap.

These four assertions only hold when ``tests/conftest.py`` mutates the canonical
client's pool **in place, once per session** instead of replacing the object per
test. They are the fence around that design:

* object stability catches a return to per-test object replacement;
* the all-bindings-agree walk catches a stranded submodule binding, which is the
  #2037 split-brain the deleted repatch loop used to paper over;
* the pool-class/cap assertion catches a pool rebuilt by something that does not
  know about popoto's cap (popoto's own ``_swap_db`` is exactly such a thing);
* pool-object stability catches a function-scoped restore/rebuild cycle, which
  would leave the three assertions above green while reintroducing the hazard.

The two stability assertions compare against a session-scoped baseline rather
than against state left behind by a sibling test, so they are armed in every
xdist worker and under any ``-k`` selection.
"""

import sys

import popoto.redis_db as rdb
import pytest
import redis


@pytest.fixture(scope="session")
def installed_identity(_popoto_pool_install):
    """The canonical client and pool object ids, captured once per session.

    Session scope is what makes the two stability assertions below un-vacuous.
    An earlier shape recorded the baseline in a *test* and compared in a later
    one, which silently no-opped whenever the pair was split — standalone, under
    a ``-k`` selection, or under xdist, where the module's tests are distributed
    across workers and a worker can receive the comparison without the baseline.
    A session fixture runs once in every worker that collects any test here, so
    the baseline always exists wherever the comparison runs.

    It depends on ``_popoto_pool_install`` so the capture happens after conftest
    has installed the session pool, never against the plugin's import-time one.
    """
    client = rdb.POPOTO_REDIS_DB
    return {"client_id": id(client), "pool_id": id(client.connection_pool)}


def test_pool_identity_is_stable_across_tests(installed_identity):
    """(d) The connection pool object survives from one test to the next.

    A function-scoped restore/rebuild cycle in ``redis_test_db`` breaks only this
    assertion, which is why it exists separately from the client-identity one.
    """
    assert id(rdb.POPOTO_REDIS_DB.connection_pool) == installed_identity["pool_id"], (
        "popoto's connection pool was replaced between two tests in the same "
        "session. The pool is installed once per session by conftest's "
        "_popoto_pool_install; a per-test restore or rebuild reintroduces the "
        "hazard that fixture exists to remove (#2771)."
    )


def test_client_object_identity_is_stable_across_tests(installed_identity):
    """(a) ``rdb.POPOTO_REDIS_DB`` is the same object across tests in a process."""
    assert id(rdb.POPOTO_REDIS_DB) == installed_identity["client_id"], (
        "rdb.POPOTO_REDIS_DB was rebound to a different object between two tests. "
        "conftest must mutate the canonical client's pool in place, never replace "
        "the object — replacement strands every popoto submodule's import-time "
        "binding (#2037, #2771)."
    )


def test_every_popoto_binding_is_the_canonical_client():
    """(b) Every ``popoto.*`` module holding the symbol holds the canonical object."""
    canonical = rdb.POPOTO_REDIS_DB
    stranded = [
        name
        for name, mod in list(sys.modules.items())
        if mod is not None and name.startswith("popoto") and hasattr(mod, "POPOTO_REDIS_DB")
        if mod.POPOTO_REDIS_DB is not canonical
    ]
    assert not stranded, (
        f"popoto submodules hold a POPOTO_REDIS_DB that is not the canonical "
        f"client: {sorted(stranded)}. Those bindings were captured at import time "
        "via `from ..redis_db import POPOTO_REDIS_DB`; they follow the canonical "
        "object only while nothing rebinds the module-level name (#2037, #2771)."
    )


def test_pool_is_blocking_with_popoto_cap():
    """(c) The live pool is popoto's ``BlockingConnectionPool`` with popoto's cap.

    ``max_connections`` is a *pool* constructor argument and is not carried in
    ``connection_kwargs``, so anything that rebuilds the pool from those kwargs
    silently drops both the pool class and the cap — which is what popoto's own
    ``_swap_db`` does. The cap is read defensively from ``popoto.redis_db``;
    #2770 is the upstream coordination point if that name ever moves.
    """
    pool = rdb.POPOTO_REDIS_DB.connection_pool
    assert isinstance(pool, redis.BlockingConnectionPool), (
        f"popoto's client is on a {type(pool).__name__}, not a "
        "redis.BlockingConnectionPool. popoto builds a BlockingConnectionPool so "
        "that an asyncio.gather burst blocks instead of raising "
        "MaxConnectionsError; a rebuilt pool drops that protection (#2771)."
    )
    expected_cap = getattr(rdb, "_SYNC_MAX_CONNECTIONS", 128)
    assert pool.max_connections == expected_cap, (
        f"popoto's connection cap was dropped: pool.max_connections="
        f"{pool.max_connections}, popoto._SYNC_MAX_CONNECTIONS={expected_cap}."
    )
