"""Unit tests for config.redis_bootstrap.configure_resilient_redis().

Coverage:
- Degrade-don't-die: ConnectionError from set_REDIS_DB_settings → warning logged, no raise.
- Run-once guard: calling twice only runs setup once.
- Pytest no-op guard: PYTEST_CURRENT_TEST set → no call to set_REDIS_DB_settings.
- Empty/missing ``settings.redis.url``: falls back to 127.0.0.1:6379/db=0.
  This is a pydantic ``Field(default=...)``, not an ``os.environ["REDIS_URL"]``
  read — ``RedisSettings`` uses ``env_nested_delimiter="__"``, so its actual
  env seam is ``REDIS__URL``, unaffected by pytest's plain ``REDIS_URL``
  export (#2805).
- Retry kwargs: set_REDIS_DB_settings receives retry, retry_on_error, health_check_interval.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_popoto_client(redis_test_db):
    """Undo the global-client rebind these tests deliberately cause (#3130).

    Several tests here delenv PYTEST_CURRENT_TEST so configure_resilient_redis()
    reaches the real popoto.redis_db.set_REDIS_DB_settings, which REPLACES the
    module-global POPOTO_REDIS_DB with a fresh client built from
    config.settings.RedisSettings.url — the ambient host string on db 0, since
    that seam is REDIS__URL, not the REDIS_URL pytest exports. The session-scoped
    _popoto_pool_install never re-runs, so without this restore the rebound
    global survives the test and every later test on the same xdist worker dies
    in redis_test_db's claim-registry assertion (25 nightly issues from one
    defect).

    Restoring ``rdb.POPOTO_REDIS_DB`` alone is NOT enough:
    ``configure_resilient_redis`` follows its ``set_REDIS_DB_settings`` call
    with a ``sys.modules`` walk that re-points every popoto submodule's
    import-time ``POPOTO_REDIS_DB`` binding at the new client
    (``config/redis_bootstrap.py``). Without the mirror walk below,
    ``popoto.models.base`` keeps the leaked client after the module attribute
    is restored, and a later test's ``save()`` writes through it while its
    ``query`` reads the claimed db — a split-brain that surfaces as "Session
    not found after save" (or a connection error to the URL-parsing tests'
    fake host).

    Depending on redis_test_db orders this teardown BEFORE that fixture's own,
    so the next test's setup assertion always sees the restored client.
    """
    import sys

    import popoto.redis_db as rdb

    original = rdb.POPOTO_REDIS_DB
    yield
    leaked = rdb.POPOTO_REDIS_DB
    rdb.POPOTO_REDIS_DB = original
    # Mirror configure_resilient_redis's submodule walk, pointing every cached
    # binding back at the original client. Idempotent when nothing drifted.
    for name, mod in list(sys.modules.items()):
        if (
            mod is not None
            and name.startswith("popoto")
            and hasattr(mod, "POPOTO_REDIS_DB")
            and mod is not rdb
        ):
            mod.POPOTO_REDIS_DB = original
    if leaked is not original:
        try:
            leaked.connection_pool.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_bootstrap():
    """Reset the run-once sentinel so each test starts fresh."""
    import config.redis_bootstrap as mod

    mod._BOOTSTRAPPED = False


def _make_fake_set_redis():
    """Return a (mock, captured_kwargs_list) pair for patching set_REDIS_DB_settings."""
    captured: list[dict] = []

    def fake_set(env_partition_name="", *args, **kwargs):
        captured.append(kwargs)

    return fake_set, captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDegradeDontDie:
    """ConnectionError from set_REDIS_DB_settings must not propagate."""

    def test_connection_error_logs_warning_no_raise(self, caplog, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        with (
            patch("popoto.redis_db.set_REDIS_DB_settings", side_effect=ConnectionError("refused")),
            patch(
                "sys.modules",
                {**__import__("sys").modules, "popoto.redis_db": __import__("popoto.redis_db")},
            ),
            caplog.at_level(logging.WARNING, logger="config.redis_bootstrap"),
        ):
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()  # must not raise

        assert any(
            "degraded mode" in r.message.lower() or "degraded" in r.message.lower()
            for r in caplog.records
        ), f"Expected degraded-mode warning. Got: {[r.message for r in caplog.records]}"

    def test_connection_error_no_exception_raised(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        import popoto.redis_db as rdb

        original = rdb.set_REDIS_DB_settings

        def bad_set(*a, **kw):
            raise ConnectionError("down")

        rdb.set_REDIS_DB_settings = bad_set
        try:
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()  # must not raise
        finally:
            rdb.set_REDIS_DB_settings = original


class TestRunOnceGuard:
    """configure_resilient_redis() must be idempotent."""

    def test_calling_twice_only_runs_setup_once(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        call_count = 0
        original_set = None

        import popoto.redis_db as rdb

        original_set = rdb.set_REDIS_DB_settings

        def counting_set(env_partition_name="", *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_set(env_partition_name, *args, **kwargs)

        rdb.set_REDIS_DB_settings = counting_set
        try:
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()
            mod.configure_resilient_redis()  # second call — must be no-op
        finally:
            rdb.set_REDIS_DB_settings = original_set

        assert call_count == 1, f"Expected set_REDIS_DB_settings called once, got {call_count}"


class TestPytestNoOpGuard:
    """When PYTEST_CURRENT_TEST is set, bootstrap must be a no-op."""

    def test_pytest_env_set_skips_setup(self, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/unit/test_redis_bootstrap.py::something")
        _reset_bootstrap()

        import popoto.redis_db as rdb

        original = rdb.set_REDIS_DB_settings
        call_count = 0

        def tracking_set(*a, **kw):
            nonlocal call_count
            call_count += 1
            return original(*a, **kw)

        rdb.set_REDIS_DB_settings = tracking_set
        try:
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()
        finally:
            rdb.set_REDIS_DB_settings = original

        assert call_count == 0, "set_REDIS_DB_settings must not be called under pytest"

    def test_pytest_env_not_set_allows_setup(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        import popoto.redis_db as rdb

        original = rdb.set_REDIS_DB_settings
        call_count = 0

        def tracking_set(*a, **kw):
            nonlocal call_count
            call_count += 1
            return original(*a, **kw)

        rdb.set_REDIS_DB_settings = tracking_set
        try:
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()
        finally:
            rdb.set_REDIS_DB_settings = original

        assert call_count == 1, "set_REDIS_DB_settings must be called once when not under pytest"


class TestUrlParsing:
    """URL fallback and parsing behaviour."""

    def _run_and_capture(self, monkeypatch, redis_url_override=None):
        """Run configure_resilient_redis and capture the kwargs passed to set_REDIS_DB_settings."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        captured: dict = {}
        import popoto.redis_db as rdb

        original = rdb.set_REDIS_DB_settings

        def capturing_set(env_partition_name="", *args, **kwargs):
            captured.update(kwargs)
            return original(env_partition_name, *args, **kwargs)

        rdb.set_REDIS_DB_settings = capturing_set
        try:
            if redis_url_override is not None:
                # Patch the settings object that the bootstrap imports locally.
                # The import is ``from config.settings import settings as _settings``
                # so we patch the source attribute on the settings module.
                from config.settings import settings as _settings_inst

                original_url = _settings_inst.redis.url
                _settings_inst.redis.url = redis_url_override
                try:
                    import config.redis_bootstrap as mod

                    mod.configure_resilient_redis()
                finally:
                    _settings_inst.redis.url = original_url
            else:
                import config.redis_bootstrap as mod

                mod.configure_resilient_redis()
        finally:
            rdb.set_REDIS_DB_settings = original

        return captured

    def test_default_url_falls_back_to_localhost(self, monkeypatch):
        kwargs = self._run_and_capture(monkeypatch, redis_url_override="redis://localhost:6379/0")
        assert kwargs.get("host") in ("localhost", "127.0.0.1")
        assert kwargs.get("port") == 6379
        assert kwargs.get("db") == 0

    def test_custom_url_is_parsed(self, monkeypatch):
        kwargs = self._run_and_capture(monkeypatch, redis_url_override="redis://myhost:6380/3")
        assert kwargs.get("host") == "myhost"
        assert kwargs.get("port") == 6380
        assert kwargs.get("db") == 3


class TestRetryKwargs:
    """set_REDIS_DB_settings must receive the resilience kwargs."""

    def test_retry_kwargs_present(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        _reset_bootstrap()

        captured: dict = {}

        import popoto.redis_db as rdb

        original = rdb.set_REDIS_DB_settings

        def capturing_set(env_partition_name="", *args, **kwargs):
            captured.update(kwargs)
            return original(env_partition_name, *args, **kwargs)

        rdb.set_REDIS_DB_settings = capturing_set
        try:
            import config.redis_bootstrap as mod

            mod.configure_resilient_redis()
        finally:
            rdb.set_REDIS_DB_settings = original

        assert "retry" in captured, f"retry kwarg missing. Got keys: {list(captured)}"
        assert "retry_on_error" in captured, "retry_on_error kwarg missing"
        assert "retry_on_timeout" not in captured, (
            "retry_on_timeout is deprecated in redis-py >=6.0 and must not be passed"
        )
        has_connection_err = any(
            issubclass(e, (ConnectionError, OSError)) for e in captured["retry_on_error"]
        )
        assert has_connection_err, (
            f"retry_on_error must include a connection-related exception. "
            f"Got: {captured['retry_on_error']}"
        )
        assert captured.get("health_check_interval") == 30, (
            f"health_check_interval must be 30, got {captured.get('health_check_interval')}"
        )
        assert captured.get("socket_timeout") == 5
        assert captured.get("socket_connect_timeout") == 5
