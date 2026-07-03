"""Unit tests for the shared Sentry init helper (#1877 defect #3).

`configure_sentry` must:
  (a) call `sentry_sdk.init` when `SENTRY_DSN` is set and no guard trips;
  (b) return WITHOUT calling `sentry_sdk.init` under `PYTEST_CURRENT_TEST`
      even when `SENTRY_DSN` is present (no `production` mis-tag).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from monitoring.sentry_config import configure_sentry


def test_configure_sentry_inits_when_dsn_set_and_no_guard(monkeypatch):
    """DSN present + guards cleared → sentry_sdk.init is invoked."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/123")
    # Clear the test/CI guard so the init path is exercised.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)

    fake_sentry = MagicMock()
    with (
        patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        patch(
            "monitoring.sentry_config.subprocess.check_output",
            return_value="abc123\n",
        ),
    ):
        result = configure_sentry("worker", before_send=None)

    assert result is True
    fake_sentry.init.assert_called_once()
    _, kwargs = fake_sentry.init.call_args
    assert kwargs["dsn"] == "https://example@sentry.io/123"
    assert kwargs["before_send"] is None
    assert kwargs["environment"] == "production"


def test_configure_sentry_skips_under_pytest_even_with_dsn(monkeypatch):
    """PYTEST_CURRENT_TEST set + DSN present → init is skipped (no production mis-tag)."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/123")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_worker_sentry_init.py::x (call)")

    fake_sentry = MagicMock()
    with patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
        result = configure_sentry("worker", before_send=None)

    assert result is False
    fake_sentry.init.assert_not_called()


def test_configure_sentry_skips_when_no_dsn(monkeypatch):
    """No DSN → init is skipped even with guards cleared."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)

    fake_sentry = MagicMock()
    with patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
        result = configure_sentry("worker", before_send=None)

    assert result is False
    fake_sentry.init.assert_not_called()


def test_configure_sentry_passes_before_send_for_bridge(monkeypatch):
    """The bridge's before_send hook is threaded through to sentry_sdk.init."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/123")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)

    def _before_send(event, hint):
        return event

    fake_sentry = MagicMock()
    with (
        patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        patch(
            "monitoring.sentry_config.subprocess.check_output",
            return_value="abc123\n",
        ),
    ):
        result = configure_sentry("bridge", before_send=_before_send)

    assert result is True
    _, kwargs = fake_sentry.init.call_args
    assert kwargs["before_send"] is _before_send
