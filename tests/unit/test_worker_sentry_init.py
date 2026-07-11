"""Unit tests for the shared Sentry init helper (#1877 defect #3, #1834).

`configure_sentry` must:
  (a) call `sentry_sdk.init` when `SENTRY_DSN` is set and no guard trips;
  (b) return WITHOUT calling `sentry_sdk.init` under `PYTEST_CURRENT_TEST`
      even when `SENTRY_DSN` is present (no `production` mis-tag);
  (c) resolve the `environment` tag via machine ownership (#1834): a designated
      bridge machine reports `production`, every other machine reports
      `development`, and an explicit `SENTRY_ENVIRONMENT` always wins.

The init-path tests below pin `_is_designated_bridge_machine` so they do not
depend on whether the test host owns a project in the real `projects.json`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from monitoring.sentry_config import (
    _is_designated_bridge_machine,
    _owned_project_key,
    _resolve_environment,
    configure_sentry,
    drop_orphan_noise,
)


def test_configure_sentry_inits_when_dsn_set_and_no_guard(monkeypatch):
    """DSN present + guards cleared + designated bridge machine → init with production."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/123")
    # Clear the test/CI guard so the init path is exercised.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    # No explicit override — environment must resolve via machine ownership.
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)

    fake_sentry = MagicMock()
    with (
        patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        patch(
            "monitoring.sentry_config.subprocess.check_output",
            return_value="abc123\n",
        ),
        # Pin ownership so the assertion does not depend on the host's projects.json.
        patch("monitoring.sentry_config._owned_project_key", return_value="valor"),
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
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)

    def _before_send(event, hint):
        return event

    fake_sentry = MagicMock()
    with (
        patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        patch(
            "monitoring.sentry_config.subprocess.check_output",
            return_value="abc123\n",
        ),
        patch("monitoring.sentry_config._owned_project_key", return_value="valor"),
    ):
        result = configure_sentry("bridge", before_send=_before_send)

    assert result is True
    _, kwargs = fake_sentry.init.call_args
    assert kwargs["before_send"] is _before_send


def test_configure_sentry_passes_before_send_for_worker(monkeypatch):
    """The worker's real Sentry init threads drop_orphan_noise as before_send."""
    monkeypatch.setenv("SENTRY_DSN", "https://example@sentry.io/123")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)

    fake_sentry = MagicMock()
    with (
        patch.dict("sys.modules", {"sentry_sdk": fake_sentry}),
        patch(
            "monitoring.sentry_config.subprocess.check_output",
            return_value="abc123\n",
        ),
        patch("monitoring.sentry_config._owned_project_key", return_value="valor"),
    ):
        result = configure_sentry("worker", before_send=drop_orphan_noise)

    assert result is True
    fake_sentry.init.assert_called_once()
    _, kwargs = fake_sentry.init.call_args
    assert kwargs["before_send"] is drop_orphan_noise


# --- Environment resolution (#1834) -----------------------------------------


def test_environment_development_when_not_bridge_machine(monkeypatch):
    """No explicit override + owns no project (owned_key is None) → development."""
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    assert _resolve_environment(None) == "development"


def test_environment_production_when_bridge_machine(monkeypatch):
    """No explicit override + owns a project → production."""
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    assert _resolve_environment("valor") == "production"


def test_explicit_sentry_environment_overrides(monkeypatch):
    """Explicit SENTRY_ENVIRONMENT wins over machine ownership (even a bridge machine)."""
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    assert _resolve_environment("valor") == "staging"


def test_owned_project_key_empty_machine_returns_none():
    """Load-bearing guard (critique concern #1, #1834): an empty ComputerName
    must NOT own a project. The guard now lives in
    ``config.machine.get_machine_project_keys`` (short-circuits before any file
    read), and ``_owned_project_key`` inherits it as a thin adapter — so this
    holds regardless of the real projects.json on the test host."""
    assert _owned_project_key("") is None


def test_owned_project_key_returns_first_owned_key():
    """The adapter returns the first key ``get_machine_project_keys`` reports."""
    with patch("monitoring.sentry_config.get_machine_project_keys", return_value=["p1", "p2"]):
        assert _owned_project_key("prod-box") == "p1"


def test_owned_project_key_no_owned_keys_returns_none():
    """No owned keys (unowned host or read failure → []) resolves to None."""
    with patch("monitoring.sentry_config.get_machine_project_keys", return_value=[]):
        assert _owned_project_key("Unowned-Laptop") is None


def test_is_designated_bridge_machine_false_on_empty_computer_name():
    """Fail-to-development: an unresolved ComputerName is never a bridge machine."""
    with patch("monitoring.sentry_config.get_machine_name", return_value=""):
        assert _is_designated_bridge_machine() is False


def test_is_designated_bridge_machine_false_on_read_failure():
    """Fail-to-development: no owned keys (e.g. unreadable projects.json → [])
    means this host is never a designated bridge machine."""
    with (
        patch("monitoring.sentry_config.get_machine_name", return_value="Dev-Laptop"),
        patch("monitoring.sentry_config.get_machine_project_keys", return_value=[]),
    ):
        assert _is_designated_bridge_machine() is False
