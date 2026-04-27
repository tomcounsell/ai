"""Tests for bridge.config_validation.validate_dm_whitelist."""

from __future__ import annotations

import json

import pytest

from bridge.config_validation import ConfigValidationError, validate_dm_whitelist


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
    validate_dm_whitelist(cfg)
