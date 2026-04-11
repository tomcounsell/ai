"""Unit tests for email contact resolution in bridge/routing.py.

Tests find_project_for_email() and load_email_contacts() for exact-match
lookup, case normalization, unknown senders, and empty input handling.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(contacts: dict | None = None, project_key: str = "test-project") -> dict:
    """Build a minimal projects.json config dict for testing."""
    return {
        "projects": {
            project_key: {
                "name": "Test Project",
                "working_directory": "/tmp/test",
                "email": {
                    "contacts": contacts or {},
                },
            }
        },
        "defaults": {},
    }


# ---------------------------------------------------------------------------
# load_email_contacts()
# ---------------------------------------------------------------------------


class TestLoadEmailContacts:
    def setup_method(self):
        """Ensure ACTIVE_PROJECTS is set for each test."""
        import bridge.routing as routing

        self._orig_active = list(routing.ACTIVE_PROJECTS)
        routing.ACTIVE_PROJECTS[:] = ["test-project"]

    def teardown_method(self):
        import bridge.routing as routing

        routing.ACTIVE_PROJECTS[:] = self._orig_active

    def test_loads_contacts_from_config(self):
        from bridge.routing import load_email_contacts

        config = _make_config({"alice@example.com": {"name": "Alice"}})
        result = load_email_contacts(config)
        assert "alice@example.com" in result
        assert result["alice@example.com"]["_key"] == "test-project"

    def test_normalizes_email_to_lowercase(self):
        from bridge.routing import load_email_contacts

        config = _make_config({"Alice@EXAMPLE.COM": {"name": "Alice"}})
        result = load_email_contacts(config)
        assert "alice@example.com" in result
        assert "Alice@EXAMPLE.COM" not in result

    def test_strips_whitespace_from_email(self):
        from bridge.routing import load_email_contacts

        config = _make_config({"  alice@example.com  ": {"name": "Alice"}})
        result = load_email_contacts(config)
        assert "alice@example.com" in result

    def test_multiple_contacts_in_same_project(self):
        from bridge.routing import load_email_contacts

        config = _make_config(
            {
                "alice@example.com": {"name": "Alice"},
                "bob@example.com": {"name": "Bob"},
            }
        )
        result = load_email_contacts(config)
        assert len(result) == 2
        assert "alice@example.com" in result
        assert "bob@example.com" in result

    def test_project_key_stored_on_result(self):
        from bridge.routing import load_email_contacts

        config = _make_config({"alice@example.com": {}})
        result = load_email_contacts(config)
        assert result["alice@example.com"]["_key"] == "test-project"

    def test_empty_contacts_returns_empty_map(self):
        from bridge.routing import load_email_contacts

        config = _make_config({})
        result = load_email_contacts(config)
        assert result == {}

    def test_project_without_email_section_skipped(self):
        from bridge.routing import load_email_contacts

        config = {
            "projects": {
                "test-project": {
                    "name": "No Email",
                    "working_directory": "/tmp",
                    # no "email" key
                }
            },
            "defaults": {},
        }
        result = load_email_contacts(config)
        assert result == {}

    def test_inactive_projects_excluded(self):
        """Projects not in ACTIVE_PROJECTS are not loaded."""
        import bridge.routing as routing
        from bridge.routing import load_email_contacts

        routing.ACTIVE_PROJECTS[:] = []  # Clear active projects
        config = _make_config({"alice@example.com": {}})
        result = load_email_contacts(config)
        assert result == {}


# ---------------------------------------------------------------------------
# find_project_for_email()
# ---------------------------------------------------------------------------


class TestFindProjectForEmail:
    def setup_method(self):
        import bridge.routing as routing

        self._orig_email_map = dict(routing.EMAIL_TO_PROJECT)
        routing.EMAIL_TO_PROJECT.clear()
        routing.EMAIL_TO_PROJECT["alice@example.com"] = {
            "_key": "test-project",
            "name": "Test Project",
        }

    def teardown_method(self):
        import bridge.routing as routing

        routing.EMAIL_TO_PROJECT.clear()
        routing.EMAIL_TO_PROJECT.update(self._orig_email_map)

    def test_exact_match_returns_project(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email("alice@example.com")
        assert result is not None
        assert result["_key"] == "test-project"

    def test_case_insensitive_match(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email("Alice@EXAMPLE.COM")
        assert result is not None
        assert result["_key"] == "test-project"

    def test_unknown_sender_returns_none(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email("unknown@other.com")
        assert result is None

    def test_none_sender_returns_none(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email(None)
        assert result is None

    def test_empty_string_returns_none(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email("")
        assert result is None

    def test_whitespace_only_returns_none(self):
        from bridge.routing import find_project_for_email

        result = find_project_for_email("   ")
        assert result is None

    def test_partial_domain_match_not_allowed(self):
        """Partial domain should not match — exact match only."""
        from bridge.routing import find_project_for_email

        result = find_project_for_email("example.com")
        assert result is None

    def test_subdomain_not_matched(self):
        """alice@sub.example.com is not alice@example.com."""
        from bridge.routing import find_project_for_email

        result = find_project_for_email("alice@sub.example.com")
        assert result is None
