"""Unit tests for the D9 username plumbing in config.redis_bootstrap (#2645, BLOCKER fix).

After the separate #2661 REDIS_URL rotation, popoto must authenticate as the
locked-down `valor-app` ACL user rather than `default` -- `default` is
deliberately left `nopass`/`flushdb`-capable (D3), so a one-argument
`AUTH <pw>` (no username forwarded) either errors outright against `default`
(nopass rejects a password -- bridge/worker/dashboard down fleet-wide) or, in
the counterfactual where it connected, would connect AS `default` and retain
`flushdb` -- Layer 2 would then protect every client except the one that
caused the 2026-08-07 incident.

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


def _run_and_capture(monkeypatch, redis_url: str) -> dict:
    """Run configure_resilient_redis() with redis.url overridden to `redis_url`
    and return the kwargs captured by the patched set_REDIS_DB_settings."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _reset_bootstrap()

    captured: dict = {}
    import popoto.redis_db as rdb

    original = rdb.set_REDIS_DB_settings

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

    return captured


class TestUsernamePlumbing:
    """config/redis_bootstrap.py:112 must forward parsed.username, not drop it."""

    def test_credentialed_url_forwards_username(self, monkeypatch):
        """redis://valor-app:pw@h:6379/0 -> username='valor-app' in captured kwargs."""
        captured = _run_and_capture(monkeypatch, "redis://valor-app:pw@myhost:6379/0")

        assert captured.get("username") == "valor-app", (
            f"Expected username='valor-app' forwarded to set_REDIS_DB_settings. "
            f"Got keys: {list(captured)}, username={captured.get('username')!r}"
        )
        # Password must still be forwarded too -- D9 adds username, it must
        # not regress the existing password plumbing.
        assert captured.get("password") == "pw"

    def test_bare_url_forwards_username_none(self, monkeypatch):
        """A bare redis://h:6379/0 (pre-rotation, no username) -> username=None.

        This is the byte-identical-to-today case: username=None is redis-py's
        own default, so a pre-rotation REDIS_URL produces unchanged behavior.
        """
        captured = _run_and_capture(monkeypatch, "redis://myhost:6379/0")

        assert "username" in captured, (
            f"set_REDIS_DB_settings must receive an explicit username kwarg "
            f"(even if None). Got keys: {list(captured)}"
        )
        assert captured.get("username") is None
        assert captured.get("password") is None
