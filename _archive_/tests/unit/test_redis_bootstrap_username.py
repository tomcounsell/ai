"""Unit tests for username plumbing in config.redis_bootstrap.

A managed Redis handed out as a credentialed URL (`redis://user:pw@host/0`)
needs popoto to authenticate as that user, not `default` -- this is plain
connection-string parsing, not access-control configuration. The stack needs
nothing more than the URL: a bare `redis://host/0` carries no username, and
`username=None` is redis-py's own default, so that case is unaffected.

These tests patch `popoto.redis_db.set_REDIS_DB_settings` and never touch a
real Redis connection -- `set_REDIS_DB_settings` is a pure kwargs-forwarder
here, so patching it and inspecting the captured kwargs proves the wiring
without connecting to anything.
"""

from __future__ import annotations


def _reset_bootstrap():
    """Reset the run-once sentinel so each test starts fresh."""
    import config.redis_bootstrap as mod

    mod._BOOTSTRAPPED = False


def _popoto_client_bindings() -> list[tuple[object, object]]:
    """Every live module binding of ``POPOTO_REDIS_DB``, paired with its client.

    ``set_REDIS_DB_settings`` REBINDS the module global
    ``popoto.redis_db.POPOTO_REDIS_DB`` to a brand-new client, and
    ``configure_resilient_redis`` then copies that client onto every popoto
    submodule that cached the symbol at import time. Both rebinds outlive a
    monkeypatch teardown, which is what leaked a ``myhost`` client into the next
    test (#3200).

    The restore has to put back the exact OBJECTS ``tests/conftest.py``'s
    ``_popoto_pool_install`` established, never a freshly built equivalent: that
    fixture swaps ``client.connection_pool`` in place precisely so every cached
    binding follows one object. Snapshotting identities and reinstating them is
    the only restore that preserves that invariant.
    """
    import sys

    return [
        (mod, mod.POPOTO_REDIS_DB)
        for name, mod in list(sys.modules.items())
        if mod is not None and name.startswith("popoto") and hasattr(mod, "POPOTO_REDIS_DB")
    ]


def _run_and_capture(monkeypatch, redis_url: str) -> dict:
    """Run configure_resilient_redis() with redis.url overridden to `redis_url`
    and return the kwargs captured by the patched set_REDIS_DB_settings.

    The bootstrap genuinely runs here -- ``PYTEST_CURRENT_TEST`` is deleted so
    its no-op guard stands down -- so it genuinely rebuilds the popoto client
    against the fake URL. The ``finally`` puts the suite's own client back on
    every binding before returning; without it the next test to touch popoto
    dials ``myhost`` (#3200).
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_bootstrap()

    captured: dict = {}
    import popoto.redis_db as rdb

    original = rdb.set_REDIS_DB_settings
    client_bindings = _popoto_client_bindings()

    def capturing_set(env_partition_name="", *args, **kwargs):
        captured.update(kwargs)
        return original(env_partition_name, *args, **kwargs)

    from config.settings import settings as _settings_inst

    original_url = _settings_inst.redis.url
    _settings_inst.redis.url = redis_url
    rdb.set_REDIS_DB_settings = capturing_set
    try:
        import config.redis_bootstrap as mod

        mod.configure_resilient_redis()
    finally:
        rdb.set_REDIS_DB_settings = original
        _settings_inst.redis.url = original_url
        for bound_module, client in client_bindings:
            bound_module.POPOTO_REDIS_DB = client

    return captured


class TestUsernamePlumbing:
    """config/redis_bootstrap.py:112 must forward parsed.username, not drop it."""

    def test_credentialed_url_forwards_username(self, monkeypatch):
        """redis://appuser:pw@h:6379/0 -> username='appuser' in captured kwargs."""
        captured = _run_and_capture(monkeypatch, "redis://appuser:pw@myhost:6379/0")

        assert captured.get("username") == "appuser", (
            f"Expected username='appuser' forwarded to set_REDIS_DB_settings. "
            f"Got keys: {list(captured)}, username={captured.get('username')!r}"
        )
        # Password must still be forwarded too -- username forwarding must
        # not regress the existing password plumbing.
        assert captured.get("password") == "pw"

    def test_bare_url_forwards_username_none(self, monkeypatch):
        """A bare redis://h:6379/0 (no username in the URL) -> username=None.

        username=None is redis-py's own default, so a bare REDIS_URL produces
        unchanged behavior.
        """
        captured = _run_and_capture(monkeypatch, "redis://myhost:6379/0")

        assert "username" in captured, (
            f"set_REDIS_DB_settings must receive an explicit username kwarg "
            f"(even if None). Got keys: {list(captured)}"
        )
        assert captured.get("username") is None
        assert captured.get("password") is None


class TestHelperRestoresPopotoClient:
    """`_run_and_capture` must leave popoto on the suite's own client (#3200)."""

    def test_popoto_client_is_restored_after_helper(self, monkeypatch):
        """After the helper returns, every popoto binding is back on the claimed db.

        The helper runs the real bootstrap against a fake `myhost` URL, which
        rebuilds `popoto.redis_db.POPOTO_REDIS_DB` and copies the new client
        onto every popoto submodule. Left in place, the next test that touches
        popoto -- including the autouse `redis_test_db` flush -- dials `myhost`
        and errors with `Error 8 connecting to myhost:6379`.

        Read through `popoto.redis_db` and the pool's `connection_kwargs`, the
        same accessor `tests/conftest.py` asserts on. A hand-built raw client
        would prove nothing about what popoto actually holds.
        """
        from tests.db_claim import claim_test_db, redis_test_host, redis_test_port

        _run_and_capture(monkeypatch, "redis://appuser:pw@myhost:6379/0")

        expected = (str(redis_test_host()), int(redis_test_port()), claim_test_db())

        for bound_module, client in _popoto_client_bindings():
            kwargs = client.connection_pool.connection_kwargs
            actual = (
                str(kwargs.get("host")),
                int(kwargs.get("port") or 0),
                int(kwargs.get("db") or 0),
            )
            assert actual == expected, (
                f"{bound_module.__name__}.POPOTO_REDIS_DB points at "
                f"{actual[0]}:{actual[1]}/{actual[2]} after _run_and_capture; "
                f"expected the claimed test db {expected[0]}:{expected[1]}/{expected[2]} (#3200)"
            )
