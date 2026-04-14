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
        addr_map, domain_map = build_email_to_project_map(config)
        assert "alice@example.com" in addr_map
        assert "bob@example.com" in addr_map
        assert addr_map["alice@example.com"]["name"] == "ACME Corp"

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
        addr_map, domain_map = build_email_to_project_map(config)
        assert "alice@example.com" in addr_map
        assert "Alice@EXAMPLE.COM" not in addr_map

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
        addr_map, domain_map = build_email_to_project_map(config)
        assert "alice@example.com" in addr_map
        assert "charlie@example.com" not in addr_map

    def test_project_key_set_on_result_project(self):
        """build_email_to_project_map() sets '_key' on each project dict."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {"alice@example.com": {}}},
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert addr_map["alice@example.com"]["_key"] == "acme"

    def test_project_with_no_email_config_produces_no_entries(self):
        """Project without email.contacts produces no map entries."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                # No 'email' key
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert len(addr_map) == 0
        assert len(domain_map) == 0

    def test_project_with_empty_contacts_produces_no_entries(self):
        """Project with contacts={} produces no map entries."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {}},
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert len(addr_map) == 0
        assert len(domain_map) == 0

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
            addr_map, domain_map = build_email_to_project_map(config)

        # First project wins
        assert addr_map["shared@example.com"]["name"] in ("ACME Corp", "Beta Project")
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
        addr_map, domain_map = build_email_to_project_map(config)
        assert len(addr_map) == 2
        assert addr_map["alice@example.com"]["_key"] == "acme"
        assert addr_map["charlie@example.com"]["_key"] == "beta"

    def test_returns_empty_dict_for_empty_config(self):
        """Empty config produces empty maps without errors."""
        addr_map, domain_map = build_email_to_project_map({"projects": {}, "defaults": {}})
        assert addr_map == {} and domain_map == {}

    # -----------------------------------------------------------------
    # New domain-map tests
    # -----------------------------------------------------------------

    def test_domain_map_populated_for_domain_only_project(self, monkeypatch):
        """Domain-only project populates domain_map, not addr_map."""
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", ["acme"])
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"domains": ["psyoptimal.com"]},
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert addr_map == {}
        assert "psyoptimal.com" in domain_map
        assert domain_map["psyoptimal.com"]["_key"] == "acme"

    def test_domain_map_key_has_at_sign_stripped(self, monkeypatch):
        """Domain with leading '@' is stored without it."""
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", ["acme"])
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"domains": ["@psyoptimal.com"]},
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert "psyoptimal.com" in domain_map
        assert "@psyoptimal.com" not in domain_map

    def test_both_contacts_and_domains_populate_both_maps(self):
        """Project with both contacts and domains populates both maps."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {
                    "contacts": {"alice@example.com": {}},
                    "domains": ["example.com"],
                },
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert "alice@example.com" in addr_map
        assert "example.com" in domain_map

    def test_empty_domains_list_produces_no_domain_entries(self):
        """Project with domains=[] produces no domain_map entries."""
        config = _make_config(
            {
                "_key": "acme",
                "name": "ACME Corp",
                "email": {"contacts": {}, "domains": []},
            },
        )
        addr_map, domain_map = build_email_to_project_map(config)
        assert addr_map == {}
        assert domain_map == {}


# ---------------------------------------------------------------------------
# Tests for find_project_for_email() domain fallback
# ---------------------------------------------------------------------------


class TestFindProjectForEmailDomainFallback:
    """find_project_for_email() falls back to EMAIL_DOMAIN_TO_PROJECT for @domain matches."""

    @pytest.fixture(autouse=True)
    def seed_domain_map(self, monkeypatch):
        """Clear exact-match map; seed domain map with psyoptimal.com."""
        self.domain_project = {
            "_key": "psyoptimal",
            "name": "PsyOptimal",
        }
        monkeypatch.setattr(routing_module, "EMAIL_TO_PROJECT", {})
        monkeypatch.setattr(
            routing_module,
            "EMAIL_DOMAIN_TO_PROJECT",
            {"psyoptimal.com": self.domain_project},
        )

    def test_domain_sender_resolves_to_project(self):
        """Sender from @psyoptimal.com resolves via domain fallback."""
        result = find_project_for_email("tcounsell@psyoptimal.com")
        assert result is not None
        assert result["_key"] == "psyoptimal"

    def test_domain_lookup_case_insensitive(self):
        """Domain lookup is case-insensitive."""
        result = find_project_for_email("USER@PSYOPTIMAL.COM")
        assert result is not None
        assert result["_key"] == "psyoptimal"

    def test_exact_match_wins_over_domain(self, monkeypatch):
        """Exact address match takes priority over domain fallback."""
        exact_project = {"_key": "exact", "name": "Exact Match"}
        monkeypatch.setattr(
            routing_module,
            "EMAIL_TO_PROJECT",
            {"alice@psyoptimal.com": exact_project},
        )
        result = find_project_for_email("alice@psyoptimal.com")
        assert result is not None
        assert result["_key"] == "exact"

    def test_unknown_domain_returns_none(self):
        """Sender from unknown domain returns None."""
        result = find_project_for_email("user@unknown.com")
        assert result is None
