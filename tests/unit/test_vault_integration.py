"""
Integration tests verifying that runtime modules resolve configuration paths
through `config.settings.vault` (not hardcoded `~/Desktop/Valor` literals).

Each test sets VALOR_VAULT_DIR to a tmp_path, drops the relevant config file
inside, and verifies the module's resolution function returns the vault path.
"""

import sys
from pathlib import Path

import pytest

import config.settings  # noqa: F401  (force import so sys.modules has the entry)

_VAULT_MODULE = sys.modules["config.settings"]


@pytest.fixture(autouse=True)
def _reset_vault_singleton(monkeypatch):
    """Force a fresh vault resolution for every test."""
    monkeypatch.setattr(_VAULT_MODULE, "_vault_singleton", None)


@pytest.fixture
def allow_ephemeral_vault(monkeypatch):
    """tmp_path lives under /var/folders; bypass the ephemeral-root rejection."""
    monkeypatch.setattr(_VAULT_MODULE, "_EPHEMERAL_PATH_PREFIXES", ())


# ---------------------------------------------------------------------------
# bridge/routing.py
# ---------------------------------------------------------------------------


def test_routing_uses_vault_projects_path(monkeypatch, tmp_path, allow_ephemeral_vault):
    """`bridge.routing._resolve_config_path()` reads from <vault>/projects.json."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("PROJECTS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("VALOR_LAUNCHD", raising=False)

    projects_path = tmp_path / "projects.json"
    projects_path.write_text('{"projects": {}, "defaults": {}}\n')

    from bridge import routing

    resolved = routing._resolve_config_path()
    assert resolved == projects_path, (
        f"routing should resolve to vault projects.json, got {resolved}"
    )


def test_routing_skips_vault_under_launchd_when_tcc_restricted(
    monkeypatch, tmp_path, allow_ephemeral_vault
):
    """Under launchd AND a TCC-restricted vault, routing must use the local copy."""
    # Fake home so ~/Desktop falls under tmp_path; vault dir lives at ~/Desktop/Valor.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tcc_vault = tmp_path / "Desktop" / "Valor"
    tcc_vault.mkdir(parents=True)
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tcc_vault))
    monkeypatch.delenv("PROJECTS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    (tcc_vault / "projects.json").write_text('{"projects": {}, "defaults": {}}\n')

    from bridge import routing

    resolved = routing._resolve_config_path()
    repo_local = Path(routing.__file__).parent.parent / "config" / "projects.json"
    assert resolved == repo_local, (
        f"TCC vault under launchd must fall through to in-repo local copy, got {resolved}"
    )


def test_routing_reads_vault_under_launchd_when_not_tcc_restricted(
    monkeypatch, tmp_path, allow_ephemeral_vault
):
    """Under launchd but with a non-TCC vault, routing reads the vault directly."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("PROJECTS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    projects_path = tmp_path / "projects.json"
    projects_path.write_text('{"projects": {}, "defaults": {}}\n')

    from bridge import routing

    resolved = routing._resolve_config_path()
    assert resolved == projects_path, (
        f"non-TCC vault is readable under launchd; routing must use it directly, got {resolved}"
    )


def test_routing_projects_config_path_override_wins(monkeypatch, tmp_path, allow_ephemeral_vault):
    """PROJECTS_CONFIG_PATH override wins over the vault even under launchd."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path / "vault"))
    override = tmp_path / "elsewhere" / "projects.json"
    override.parent.mkdir(parents=True)
    override.write_text('{"projects": {}, "defaults": {}}\n')
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(override))
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    from bridge import routing

    assert routing._resolve_config_path() == override


# ---------------------------------------------------------------------------
# reflections/utilities.py
# ---------------------------------------------------------------------------


def test_reflections_utils_uses_vault_projects_path(monkeypatch, tmp_path, allow_ephemeral_vault):
    """`reflections.utilities.load_local_projects` reads from <vault>/projects.json."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("PROJECTS_CONFIG_PATH", raising=False)

    workdir = tmp_path / "work"
    workdir.mkdir()
    (tmp_path / "projects.json").write_text(
        '{"projects": {"alpha": {"working_directory": "' + str(workdir) + '"}}}\n'
    )

    from reflections import utilities as reflections_utils

    projects = reflections_utils.load_local_projects()
    slugs = {p["slug"] for p in projects}
    assert "alpha" in slugs, f"expected vault-loaded project alpha, got {slugs}"


# ---------------------------------------------------------------------------
# agent/reflection_scheduler.py
# ---------------------------------------------------------------------------


def test_reflection_scheduler_uses_vault_reflections_yaml(
    monkeypatch, tmp_path, allow_ephemeral_vault
):
    """`agent.reflection_scheduler._resolve_registry_path()` reads from <vault>/reflections.yaml."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("REFLECTIONS_YAML", raising=False)
    monkeypatch.delenv("VALOR_LAUNCHD", raising=False)

    yaml_path = tmp_path / "reflections.yaml"
    yaml_path.write_text("reflections: []\n")

    from agent import reflection_scheduler

    # Re-resolve via the function (the module-level REGISTRY_PATH is frozen at import).
    assert reflection_scheduler._resolve_registry_path() == yaml_path


def test_reflection_scheduler_env_override_wins(monkeypatch, tmp_path, allow_ephemeral_vault):
    """REFLECTIONS_YAML env var wins over <vault>/reflections.yaml."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "reflections.yaml").write_text("reflections: [vault]\n")

    override = tmp_path / "override.yaml"
    override.write_text("reflections: [override]\n")
    monkeypatch.setenv("REFLECTIONS_YAML", str(override))
    monkeypatch.delenv("VALOR_LAUNCHD", raising=False)

    from agent import reflection_scheduler

    assert reflection_scheduler._resolve_registry_path() == override


def test_reflection_scheduler_skips_vault_when_tcc_restricted_under_launchd(
    monkeypatch, tmp_path, allow_ephemeral_vault
):
    """Under launchd + TCC vault, scheduler must fall through to in-repo copy."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    tcc_vault = tmp_path / "Desktop" / "Valor"
    tcc_vault.mkdir(parents=True)
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tcc_vault))
    monkeypatch.delenv("REFLECTIONS_YAML", raising=False)
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    (tcc_vault / "reflections.yaml").write_text("reflections: [vault]\n")

    from agent import reflection_scheduler

    resolved = reflection_scheduler._resolve_registry_path()
    repo_local = Path(reflection_scheduler.__file__).parent.parent / "config" / "reflections.yaml"
    assert resolved == repo_local


def test_reflection_scheduler_reads_vault_under_launchd_when_not_tcc_restricted(
    monkeypatch, tmp_path, allow_ephemeral_vault
):
    """Under launchd + non-TCC vault, scheduler reads vault directly."""
    monkeypatch.setenv("VALOR_VAULT_DIR", str(tmp_path))
    monkeypatch.delenv("REFLECTIONS_YAML", raising=False)
    monkeypatch.setenv("VALOR_LAUNCHD", "1")

    yaml_path = tmp_path / "reflections.yaml"
    yaml_path.write_text("reflections: []\n")

    from agent import reflection_scheduler

    assert reflection_scheduler._resolve_registry_path() == yaml_path
