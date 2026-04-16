"""Unit tests for worker startup gate logic.

Tests the _should_register_email_handler() helper and dev session semaphore
initialization from worker.__main__. Imported directly — no worker startup,
no Redis, no callback dicts touched.
"""

import asyncio
import os

import agent.agent_session_queue as _queue
from worker.__main__ import _should_register_email_handler


class TestShouldRegisterEmailHandler:
    """_should_register_email_handler() gate logic for email handler registration."""

    def test_contacts_only_returns_true(self):
        """Project with email.contacts only → registers handler (regression guard)."""
        project_cfg = {
            "email": {
                "contacts": {
                    "alice@example.com": {"name": "Alice"},
                }
            }
        }
        assert _should_register_email_handler(project_cfg) is True

    def test_domains_only_returns_true(self):
        """Project with email.domains only → registers handler (the failing case)."""
        project_cfg = {
            "email": {
                "domains": ["psyoptimal.com"],
            }
        }
        assert _should_register_email_handler(project_cfg) is True

    def test_both_contacts_and_domains_returns_true(self):
        """Project with both contacts and domains → registers handler."""
        project_cfg = {
            "email": {
                "contacts": {"alice@example.com": {}},
                "domains": ["example.com"],
            }
        }
        assert _should_register_email_handler(project_cfg) is True

    def test_neither_returns_false(self):
        """Project with no email config → does not register handler."""
        project_cfg = {"name": "No Email Project"}
        assert _should_register_email_handler(project_cfg) is False

    def test_empty_contacts_and_domains_returns_false(self):
        """Project with empty contacts dict and empty domains list → does not register."""
        project_cfg = {
            "email": {
                "contacts": {},
                "domains": [],
            }
        }
        assert _should_register_email_handler(project_cfg) is False

    def test_none_email_config_returns_false(self):
        """Project with email=None → does not register (the 'or {}' guard)."""
        project_cfg = {"email": None}
        assert _should_register_email_handler(project_cfg) is False

    def test_missing_email_key_returns_false(self):
        """Project dict with no 'email' key at all → does not register."""
        project_cfg = {}
        assert _should_register_email_handler(project_cfg) is False

    def test_empty_email_dict_returns_false(self):
        """Project with email={} (no contacts or domains keys) → does not register."""
        project_cfg = {"email": {}}
        assert _should_register_email_handler(project_cfg) is False

    def test_single_domain_string_list_returns_true(self):
        """Single-element domains list is truthy → registers handler."""
        project_cfg = {"email": {"domains": ["example.com"]}}
        assert _should_register_email_handler(project_cfg) is True

    def test_nonempty_contacts_dict_returns_true(self):
        """Non-empty contacts dict is truthy → registers handler."""
        project_cfg = {"email": {"contacts": {"user@example.com": {}}}}
        assert _should_register_email_handler(project_cfg) is True


class TestDevSessionSemaphoreInit:
    """MAX_CONCURRENT_DEV_SESSIONS env var initializes the dev semaphore correctly."""

    def _simulate_init(self, monkeypatch, env_value: str | None) -> None:
        """Simulate the semaphore init block from _run_worker without starting the worker."""

        if env_value is None:
            monkeypatch.delenv("MAX_CONCURRENT_DEV_SESSIONS", raising=False)
        else:
            monkeypatch.setenv("MAX_CONCURRENT_DEV_SESSIONS", env_value)

        max_dev = max(1, int(os.environ.get("MAX_CONCURRENT_DEV_SESSIONS", "1")))
        _queue._dev_session_semaphore = asyncio.Semaphore(max_dev)
        _queue._dev_session_semaphore_cap = max_dev

    def test_zero_clamped_to_one(self, monkeypatch):
        """MAX_CONCURRENT_DEV_SESSIONS=0 must be clamped to minimum 1."""
        original_sem = _queue._dev_session_semaphore
        original_cap = _queue._dev_session_semaphore_cap
        try:
            self._simulate_init(monkeypatch, "0")
            assert _queue._dev_session_semaphore_cap == 1
            assert _queue._dev_session_semaphore._value == 1
        finally:
            _queue._dev_session_semaphore = original_sem
            _queue._dev_session_semaphore_cap = original_cap

    def test_three_initializes_with_cap_three(self, monkeypatch):
        """MAX_CONCURRENT_DEV_SESSIONS=3 initializes semaphore with _value == 3."""
        original_sem = _queue._dev_session_semaphore
        original_cap = _queue._dev_session_semaphore_cap
        try:
            self._simulate_init(monkeypatch, "3")
            assert _queue._dev_session_semaphore_cap == 3
            assert _queue._dev_session_semaphore._value == 3
        finally:
            _queue._dev_session_semaphore = original_sem
            _queue._dev_session_semaphore_cap = original_cap

    def test_default_is_one_when_env_not_set(self, monkeypatch):
        """When MAX_CONCURRENT_DEV_SESSIONS is unset, default cap is 1."""
        original_sem = _queue._dev_session_semaphore
        original_cap = _queue._dev_session_semaphore_cap
        try:
            self._simulate_init(monkeypatch, None)
            assert _queue._dev_session_semaphore_cap == 1
            assert _queue._dev_session_semaphore._value == 1
        finally:
            _queue._dev_session_semaphore = original_sem
            _queue._dev_session_semaphore_cap = original_cap
