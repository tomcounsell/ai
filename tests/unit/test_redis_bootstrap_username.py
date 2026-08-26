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
