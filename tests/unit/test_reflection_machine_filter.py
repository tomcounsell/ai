"""Unit tests for tools.reflection_machine_filter (single-machine reflection ownership).

Covers the update-time gate that disables repo-specific reflections on machines
that don't own the project, so the launchd scheduler needs no runtime check and
repo audits never fan out duplicate GitHub issues across machines.
"""

import json

import pytest
import yaml

from tools.reflection_machine_filter import filter_reflections_for_machine

pytestmark = pytest.mark.sdlc

PROJECTS = {
    "projects": {
        "valor": {"machine": "Valor the Cowboy"},
        "cuttlefish": {"machine": "Valor the Captain"},
    }
}

REFLECTIONS = {
    "reflections": [
        {"name": "docs-auditor", "project_key": "valor", "enabled": True, "callable": "x.y"},
        {
            "name": "cuttlefish-audit",
            "project_key": "cuttlefish",
            "enabled": True,
            "callable": "x.y",
        },
        {"name": "unscoped", "enabled": True, "callable": "x.y"},
        {"name": "typo", "project_key": "ghost", "enabled": True, "callable": "x.y"},
    ]
}


def _write(tmp_path, reflections=None, projects=None):
    rp = tmp_path / "reflections.yaml"
    pp = tmp_path / "projects.json"
    rp.write_text(
        yaml.safe_dump(reflections if reflections is not None else REFLECTIONS, sort_keys=False)
    )
    pp.write_text(json.dumps(projects if projects is not None else PROJECTS))
    return rp, pp


def _states(path):
    data = yaml.safe_load(path.read_text())
    return {r["name"]: r.get("enabled", True) for r in data["reflections"]}


def test_owner_keeps_its_reflection_enabled(tmp_path):
    rp, pp = _write(tmp_path)
    count, names = filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    states = _states(rp)
    assert states["docs-auditor"] is True  # owned → enabled
    assert states["cuttlefish-audit"] is False  # not owned → disabled
    assert "cuttlefish-audit" in names
    assert count == 1


def test_non_owner_disables_reflection(tmp_path):
    rp, pp = _write(tmp_path)
    count, names = filter_reflections_for_machine(rp, pp, machine_name="Valor the Captain")
    states = _states(rp)
    assert states["docs-auditor"] is False  # not owned → disabled
    assert states["cuttlefish-audit"] is True  # owned → enabled
    assert names == ["docs-auditor"]
    assert count == 1


def test_unscoped_reflection_never_touched(tmp_path):
    rp, pp = _write(tmp_path)
    filter_reflections_for_machine(rp, pp, machine_name="Valor the Captain")
    assert _states(rp)["unscoped"] is True


def test_unknown_project_key_fails_open(tmp_path):
    rp, pp = _write(tmp_path)
    _, names = filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    # 'ghost' is not in projects.json — must be left enabled, never silently killed.
    assert _states(rp)["typo"] is True
    assert "typo" not in names


def test_owner_respects_authored_disabled_state(tmp_path):
    """An owned reflection authored enabled:false stays disabled (filter never re-enables)."""
    reflections = {
        "reflections": [
            {"name": "docs-auditor", "project_key": "valor", "enabled": False, "callable": "x.y"},
        ]
    }
    rp, pp = _write(tmp_path, reflections=reflections)
    count, names = filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    assert _states(rp)["docs-auditor"] is False
    assert count == 0  # no change


def test_case_insensitive_machine_match(tmp_path):
    rp, pp = _write(tmp_path)
    filter_reflections_for_machine(rp, pp, machine_name="valor the cowboy")
    assert _states(rp)["docs-auditor"] is True


def test_blank_machine_name_fails_open(tmp_path):
    rp, pp = _write(tmp_path)
    count, names = filter_reflections_for_machine(rp, pp, machine_name="")
    assert (count, names) == (0, [])
    # nothing disabled when we can't identify the machine
    assert _states(rp)["cuttlefish-audit"] is True


def test_idempotent_on_repeat(tmp_path):
    rp, pp = _write(tmp_path)
    filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    count2, names2 = filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    assert (count2, names2) == (0, [])  # second run changes nothing


def test_no_write_when_nothing_changes(tmp_path):
    """Owner with only-owned scoped reflections: file is not rewritten (comments preserved)."""
    reflections_yaml = (
        "reflections:\n"
        "  # keep this comment\n"
        "  - name: docs-auditor\n"
        "    project_key: valor\n"
        "    enabled: true\n"
        "    callable: x.y\n"
    )
    rp = tmp_path / "reflections.yaml"
    pp = tmp_path / "projects.json"
    rp.write_text(reflections_yaml)
    pp.write_text(json.dumps(PROJECTS))
    count, _ = filter_reflections_for_machine(rp, pp, machine_name="Valor the Cowboy")
    assert count == 0
    assert "# keep this comment" in rp.read_text()  # untouched → comment survives


def test_refuses_to_write_through_symlink(tmp_path):
    """Never mutate the shared vault: a symlinked reflections path is left untouched."""
    real = tmp_path / "vault_reflections.yaml"
    real.write_text(yaml.safe_dump(REFLECTIONS, sort_keys=False))
    link = tmp_path / "reflections.yaml"
    link.symlink_to(real)
    pp = tmp_path / "projects.json"
    pp.write_text(json.dumps(PROJECTS))

    count, names = filter_reflections_for_machine(link, pp, machine_name="Valor the Captain")
    assert (count, names) == (0, [])
    # The vault (symlink target) must be byte-for-byte unchanged.
    assert _states(real)["docs-auditor"] is True


def test_entry_parses_project_key_in_registry(tmp_path):
    """ReflectionEntry exposes project_key from the registry YAML."""
    from agent.reflection_scheduler import load_registry

    rp = tmp_path / "reflections.yaml"
    rp.write_text(
        yaml.safe_dump(
            {
                "reflections": [
                    {
                        "name": "docs-auditor",
                        "description": "d",
                        "every": "86400s",
                        "priority": "low",
                        "execution_type": "function",
                        "callable": "x.y",
                        "project_key": "valor",
                        "enabled": True,
                    }
                ]
            },
            sort_keys=False,
        )
    )
    entries = load_registry(rp)
    assert len(entries) == 1
    assert entries[0].project_key == "valor"
