"""Unit tests for worker startup gate logic.

Tests the _should_register_email_handler() helper that determines whether
EmailOutputHandler should be registered for a project. Imported directly
from worker.__main__ — no worker startup, no Redis, no callback dicts touched.
"""


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
