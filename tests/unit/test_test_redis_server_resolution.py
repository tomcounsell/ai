"""The test suite must talk to the Redis server its db-claim registry names (#2799).

`tests/conftest.py` built its client as `redis.Redis(db=test_db)` with no host or
port, so every run connected to localhost:6379 regardless of environment, while
`REDIS_PORT` re-keyed only the claim-registry directory. An agent who started a
private `redis-server --port 641x` for isolation therefore got the opposite of
isolation: tests still hit production 6379 — and flushed its db N at every setup
and teardown — while the run was opted OUT of the machine-global claim pool that
stops two runs picking the same db. Collisions became more likely, not less.

Reproduced live before the fix: with `REDIS_PORT=6491` and a real server on 6491,
popoto's client reported `localhost:6379 db=1`, and production db1 was flushed.
"""

import pytest

from tests import db_claim
from tests.conftest import _assert_client_matches_claim_registry


class TestServerResolutionIsSingleSourced:
    """Host and port come from one resolver, so the connected server and the
    registry-keyed server cannot diverge by construction."""

    def test_client_port_matches_the_claim_registry_port(self):
        """The invariant that was violated: popoto's live client must be on the
        same server the claim registry is keyed to."""
        import popoto.redis_db as rdb

        kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
        assert str(kwargs.get("port")) == db_claim.redis_test_port()
        assert str(kwargs.get("host")) == db_claim.redis_test_host()

    def test_registry_dir_is_keyed_by_the_same_port_the_client_uses(self):
        assert db_claim.redis_test_port() in db_claim._test_db_claim_dir()

    def test_url_names_the_resolved_host_and_port(self):
        url = db_claim.redis_test_url()
        assert url.startswith(f"redis://{db_claim.redis_test_host()}:{db_claim.redis_test_port()}/")

    @pytest.mark.asyncio
    async def test_async_client_matches_the_sync_client(self):
        """The async client mirrors the sync one, or async reads diverge from
        sync writes.

        Awaited rather than read off ``_POPOTO_ASYNC_REDIS_DB``: that global is
        ``None`` between tests by design (popoto's plugin nulls it at every
        setup and teardown so the client is built inside the test's own event
        loop), so reading it directly could only ever skip. ``get_async_redis_db()``
        constructs it here, in this loop, from the canonical sync client's
        connection kwargs — which is the contract worth asserting. Divergence
        must FAIL; there is no skip branch.
        """
        import popoto.redis_db as rdb

        async_db = await rdb.get_async_redis_db()

        sync_kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
        async_kwargs = async_db.connection_pool.connection_kwargs
        assert async_kwargs.get("host") == sync_kwargs.get("host")
        assert async_kwargs.get("port") == sync_kwargs.get("port")
        assert async_kwargs.get("db") == sync_kwargs.get("db")


class TestEmptyEnvFallsThroughToDefault:
    """#2957: `.get(k, default)` returns "" for a set-but-empty var, which is not
    the default. That composed `redis://127.0.0.1:/N` — a malformed URL."""

    def test_empty_port_uses_the_default(self, monkeypatch):
        monkeypatch.setenv("REDIS_PORT", "")
        assert db_claim.redis_test_port() == "6379"

    def test_empty_host_uses_the_default(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "")
        assert db_claim.redis_test_host() == "127.0.0.1"

    def test_unset_port_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("REDIS_PORT", raising=False)
        assert db_claim.redis_test_port() == "6379"

    def test_set_port_is_honored(self, monkeypatch):
        monkeypatch.setenv("REDIS_PORT", "6491")
        assert db_claim.redis_test_port() == "6491"
        assert db_claim._test_db_claim_dir().endswith("6491")

    def test_empty_port_never_composes_a_malformed_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_PORT", "")
        assert ":/" not in db_claim.redis_test_url().removeprefix("redis://")


class TestClaimRegistryAssertionFires:
    """The guard must be able to fire — an assertion that cannot detect the
    mismatch it exists to catch would silently re-admit #2799."""

    class _FakeClient:
        def __init__(self, host, port):
            self.connection_pool = type(
                "_Pool", (), {"connection_kwargs": {"host": host, "port": port}}
            )()

    def test_raises_on_a_port_mismatch(self):
        with pytest.raises(RuntimeError, match="db-claim registry"):
            _assert_client_matches_claim_registry(
                self._FakeClient("127.0.0.1", 6379), "127.0.0.1", 6491
            )

    def test_raises_on_a_host_mismatch(self):
        with pytest.raises(RuntimeError, match="db-claim registry"):
            _assert_client_matches_claim_registry(
                self._FakeClient("localhost", 6379), "127.0.0.1", 6379
            )

    def test_passes_when_client_and_registry_agree(self):
        _assert_client_matches_claim_registry(
            self._FakeClient("127.0.0.1", 6379), "127.0.0.1", 6379
        )

    def test_message_names_both_servers(self):
        with pytest.raises(RuntimeError) as exc:
            _assert_client_matches_claim_registry(
                self._FakeClient("127.0.0.1", 6379), "127.0.0.1", 6491
            )
        assert "6379" in str(exc.value) and "6491" in str(exc.value)
