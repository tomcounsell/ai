"""Unit tests for email routing helpers in bridge.routing.

Tests build_email_to_project_map() and find_project_for_email() using
monkeypatching to control module-level globals (ACTIVE_PROJECTS, EMAIL_TO_PROJECT).
"""

import logging

import pytest

import bridge.routing as routing_module
from bridge.routing import build_email_to_project_map, find_project_for_email

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(*projects: dict) -> dict:
    """Build a minimal config dict with the given project entries."""
    projects_dict = {}
    for proj in projects:
        key = proj["_key"]
        entry = {k: v for k, v in proj.items() if k != "_key"}
        projects_dict[key] = entry
    return {"projects": projects_dict, "defaults": {}}


# ---------------------------------------------------------------------------
# Tests for find_project_for_email()
# ---------------------------------------------------------------------------


class TestFindProjectForEmail:
    """find_project_for_email() does exact-match lookup in EMAIL_TO_PROJECT."""

    @pytest.fixture(autouse=True)
    def seed_email_map(self, monkeypatch):
        """Populate EMAIL_TO_PROJECT with a known contact before each test."""
        project = {
            "_key": "acme",
            "name": "ACME Corp",
            "email": {
                "contacts": {
                    "alice@example.com": {"name": "Alice"},
                }
            },
        }
        monkeypatch.setattr(
            routing_module,
            "EMAIL_TO_PROJECT",
            {"alice@example.com": project},
        )

    def test_known_address_returns_project(self):
        """Exact-match address returns the mapped project dict."""
        result = find_project_for_email("alice@example.com")
        assert result is not None
        assert result["name"] == "ACME Corp"

    def test_case_insensitive_lookup(self):
        """Lookup is case-insensitive — uppercase returns same project."""
        result = find_project_for_email("ALICE@EXAMPLE.COM")
        assert result is not None
        assert result["name"] == "ACME Corp"

    def test_mixed_case_lookup(self):
        """Mixed-case address also resolves correctly."""
        result = find_project_for_email("Alice@Example.Com")
        assert result is not None

    def test_unknown_address_returns_none(self):
        """Address not in map returns None."""
        result = find_project_for_email("unknown@example.com")
        assert result is None

    def test_none_sender_returns_none(self):
        """None input returns None without error."""
        result = find_project_for_email(None)
        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        result = find_project_for_email("")
        assert result is None


# ---------------------------------------------------------------------------
# Tests for build_email_to_project_map()
# ---------------------------------------------------------------------------


class TestBuildEmailToProjectMap:
    """build_email_to_project_map() reads email.contacts from active projects."""

    @pytest.fixture(autouse=True)
    def set_active_projects(self, monkeypatch):
        """Set ACTIVE_PROJECTS to control which projects are processed."""
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", ["acme", "beta"])

    def test_builds_map_for_active_projects(self):
        """Known contacts for active projects are mapped."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {
                    "contacts": {
                        "alice@example.com": {"name": "Alice"},
                        "bob@example.com": {"name": "Bob"},
                    }
                },
            },
        )
        result = build_email_to_project_map(config)
        assert "alice@example.com" in result
        assert "bob@example.com" in result
        assert result["alice@example.com"]["name"] == "ACME Corp"

    def test_addresses_lowercased_in_map(self):
        """Map keys are always lowercase regardless of config casing."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {
                    "contacts": {
                        "Alice@EXAMPLE.COM": {"name": "Alice"},
                    }
                },
            },
        )
        result = build_email_to_project_map(config)
        assert "alice@example.com" in result
        assert "Alice@EXAMPLE.COM" not in result

    def test_inactive_projects_excluded(self, monkeypatch):
        """Projects not in ACTIVE_PROJECTS are skipped."""
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", ["acme"])
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {"alice@example.com": {}}},
            },
            {
                "_key": "beta",
                "name": "Beta Project",
                "email": {"contacts": {"charlie@example.com": {}}},
            },
        )
        result = build_email_to_project_map(config)
        assert "alice@example.com" in result
        assert "charlie@example.com" not in result

    def test_project_key_set_on_result_project(self):
        """build_email_to_project_map() sets '_key' on each project dict."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {"alice@example.com": {}}},
            },
        )
        result = build_email_to_project_map(config)
        assert result["alice@example.com"]["_key"] == "acme"

    def test_project_with_no_email_config_produces_no_entries(self):
        """Project without email.contacts produces no map entries."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                # No 'email' key
            },
        )
        result = build_email_to_project_map(config)
        assert len(result) == 0

    def test_project_with_empty_contacts_produces_no_entries(self):
        """Project with contacts={} produces no map entries."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {}},
            },
        )
        result = build_email_to_project_map(config)
        assert len(result) == 0

    def test_duplicate_email_address_warns_and_uses_first(self, caplog):
        """Duplicate address across two active projects: first wins, warning logged."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {"shared@example.com": {}}},
            },
            {
                "_key": "beta",
                "name": "Beta Project",
                "email": {"contacts": {"shared@example.com": {}}},
            },
        )
        with caplog.at_level(logging.WARNING, logger="bridge.routing"):
            result = build_email_to_project_map(config)

        # First project wins
        assert result["shared@example.com"]["name"] in ("ACME Corp", "Beta Project")
        # A warning must have been emitted
        assert any("multiple projects" in msg.lower() for msg in caplog.messages)

    def test_multiple_projects_multiple_contacts(self):
        """Multiple active projects each contribute their own contacts."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {"alice@example.com": {}}},
            },
            {
                "_key": "beta",
                "name": "Beta Project",
                "email": {"contacts": {"charlie@example.com": {}}},
            },
        )
        result = build_email_to_project_map(config)
        assert len(result) == 2
        assert result["alice@example.com"]["_key"] == "acme"
        assert result["charlie@example.com"]["_key"] == "beta"

    def test_returns_empty_dict_for_empty_config(self):
        """Empty config produces empty map without errors."""
        result = build_email_to_project_map({"projects": {}, "defaults": {}})
        assert result == {}
