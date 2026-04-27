"""Tests for bridge.config_validation: dm whitelist + groups + email routing."""

from __future__ import annotations

import json

import pytest

from bridge.config_validation import (
    ConfigValidationError,
    validate_dm_whitelist,
    validate_email_routing,
    validate_projects_config,
    validate_telegram_groups,
)


def _make_config(whitelist: list, projects: dict | None = None) -> dict:
    return {
        "projects": projects or {},
        "dms": {"whitelist": whitelist},
    }


def test_empty_whitelist_passes():
    validate_dm_whitelist(_make_config([]))


def test_no_dms_section_passes():
    validate_dm_whitelist({"projects": {}})


def test_valid_single_machine_per_project_passes():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 2, "name": "Bob", "project": "popoto"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "popoto": {"machine": "Cowboy"},
        },
    )
    validate_dm_whitelist(cfg)


def test_two_contacts_on_different_machines_passes():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 2, "name": "Charlie", "project": "psyoptimal"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    validate_dm_whitelist(cfg)


def test_same_contact_two_projects_same_machine_passes():
    """A contact appearing twice but resolving to the same machine is allowed."""
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 1, "name": "Alice", "project": "popoto"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "popoto": {"machine": "Cowboy"},
        },
    )
    validate_dm_whitelist(cfg)


def test_same_contact_two_projects_different_machines_fails():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 1, "name": "Alice", "project": "psyoptimal"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    msg = str(exc_info.value)
    assert "id=1" in msg
    assert "Cowboy" in msg
    assert "Captain" in msg


def test_missing_project_field_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "no 'project' field" in str(exc_info.value)


def test_unknown_project_reference_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice", "project": "ghost"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "unknown project 'ghost'" in str(exc_info.value)


def test_project_missing_machine_field_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice", "project": "valor"}],
        projects={"valor": {}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "has no 'machine' field" in str(exc_info.value)


def test_non_integer_id_fails():
    cfg = _make_config(
        whitelist=[{"id": "not-a-number", "name": "Alice", "project": "valor"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "non-integer id" in str(exc_info.value)


def test_entry_without_id_is_skipped_silently():
    """Entries with no id field are not whitelist entries — likely doc/comment shapes."""
    cfg = _make_config(
        whitelist=[
            {"name": "comment", "note": "this is just a doc"},
            {"id": 1, "name": "Alice", "project": "valor"},
        ],
        projects={"valor": {"machine": "Cowboy"}},
    )
    validate_dm_whitelist(cfg)


def test_multiple_errors_aggregated():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice"},  # missing project
            {"id": 2, "name": "Bob", "project": "ghost"},  # unknown project
            {"id": 3, "name": "Charlie", "project": "valor"},
            {"id": 3, "name": "Charlie2", "project": "psyoptimal"},  # dup → 2 machines
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    msg = str(exc_info.value)
    assert "id=1" in msg
    assert "ghost" in msg
    assert "id=3" in msg


def test_live_projects_json_passes_validation():
    """The actual config in ~/Desktop/Valor/projects.json must validate."""
    import os

    path = os.path.expanduser("~/Desktop/Valor/projects.json")
    if not os.path.exists(path):
        pytest.skip("Live config not present on this machine")
    with open(path) as f:
        cfg = json.load(f)
    validate_projects_config(cfg)


# ── Telegram groups ────────────────────────────────────────────────────


def test_groups_unique_per_machine_passes():
    cfg = {
        "projects": {
            "valor": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Dev: Valor": {}, "PM: Valor": {}}},
            },
            "popoto": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Dev: Popoto": {}}},
            },
            "psy": {
                "machine": "Captain",
                "telegram": {"groups": {"Dev: Psy": {}}},
            },
        }
    }
    validate_telegram_groups(cfg)


def test_same_group_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"groups": {"Shared Room": {}}}},
            "b": {"machine": "Captain", "telegram": {"groups": {"Shared Room": {}}}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_telegram_groups(cfg)
    msg = str(exc_info.value)
    assert "shared room" in msg.lower()
    assert "Cowboy" in msg
    assert "Captain" in msg


def test_group_case_insensitive_collision_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"groups": {"Dev: Foo": {}}}},
            "b": {"machine": "Captain", "telegram": {"groups": {"dev: foo": {}}}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_telegram_groups(cfg)


def test_groups_without_machine_fails():
    cfg = {"projects": {"a": {"telegram": {"groups": {"X": {}}}}}}
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_telegram_groups(cfg)
    assert "no 'machine' field" in str(exc_info.value)


# ── Email routing ──────────────────────────────────────────────────────


def test_email_unique_per_machine_passes():
    cfg = {
        "projects": {
            "a": {
                "machine": "Bald",
                "email": {"contacts": ["alice@x.com"], "domains": ["x.com"]},
            },
            "b": {
                "machine": "Captain",
                "email": {"domains": ["y.com"]},
            },
        }
    }
    validate_email_routing(cfg)


def test_same_email_contact_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["alice@x.com"]}},
            "b": {"machine": "Captain", "email": {"contacts": ["alice@x.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "alice@x.com" in str(exc_info.value)


def test_same_email_domain_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"domains": ["shared.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["shared.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "shared.com" in str(exc_info.value)


def test_explicit_contact_overlaps_other_domain_wildcard_fails():
    """An explicit contact on machine A whose domain is claimed by machine B."""
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["alice@psy.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["psy.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    msg = str(exc_info.value)
    assert "alice@psy.com" in msg
    assert "psy.com" in msg


def test_email_case_insensitive():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["Alice@X.com"]}},
            "b": {"machine": "Captain", "email": {"contacts": ["alice@x.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_email_routing(cfg)


def test_domain_wildcard_prefix_normalized():
    """`*.foo.com`, `@foo.com`, and `foo.com` all collapse to the same key."""
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"domains": ["*.foo.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["@foo.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_email_routing(cfg)


def test_email_routing_without_machine_fails():
    cfg = {"projects": {"a": {"email": {"contacts": ["x@y.com"]}}}}
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "no 'machine' field" in str(exc_info.value)


# ── Aggregate validator ────────────────────────────────────────────────


def test_validate_projects_config_aggregates_all_errors():
    cfg = {
        "projects": {
            "a": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Shared": {}}},
                "email": {"contacts": ["x@y.com"]},
            },
            "b": {
                "machine": "Captain",
                "telegram": {"groups": {"Shared": {}}},
                "email": {"contacts": ["x@y.com"]},
            },
        },
        "dms": {
            "whitelist": [
                {"id": 1, "name": "Z", "project": "a"},
                {"id": 1, "name": "Z2", "project": "b"},
            ]
        },
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_projects_config(cfg)
    msg = str(exc_info.value).lower()
    # All three categories of error appear in the aggregated message
    assert "id=1" in msg
    assert "shared" in msg
    assert "x@y.com" in msg
