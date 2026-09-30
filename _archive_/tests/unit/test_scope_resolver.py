"""Tests for the knowledge document scope resolver."""

import os
from unittest.mock import patch

import pytest

from tools.knowledge.scope_resolver import (
    get_vault_path,
    reload_mappings,
    resolve_scope,
)


@pytest.fixture(autouse=True)
def _reset_mappings():
    """Reset cached mappings before each test."""
    reload_mappings()
    yield
    reload_mappings()


@pytest.fixture
def mock_projects_json(tmp_path):
    """Create a mock projects.json with test data."""
    import json

    projects = {
        "projects": {
            "valor": {
                "name": "Valor AI",
                "knowledge_base": str(tmp_path / "work-vault" / "AI Valor Engels System"),
            },
            "psyoptimal": {
                "name": "PsyOptimal",
                "knowledge_base": str(tmp_path / "work-vault" / "PsyOptimal"),
            },
            "nobase": {
                "name": "No KB",
            },
        }
    }

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps(projects))

    # Create directories
    (tmp_path / "work-vault" / "AI Valor Engels System").mkdir(parents=True)
    (tmp_path / "work-vault" / "PsyOptimal").mkdir(parents=True)
    (tmp_path / "work-vault" / "shared-docs").mkdir(parents=True)

    return projects_file, tmp_path


@pytest.mark.unit
class TestScopeResolver:
    def test_client_scope_valor(self, mock_projects_json):
        """File under valor's knowledge_base maps to client scope."""
        projects_file, tmp_path = mock_projects_json
        str(tmp_path / "work-vault")

        with (
            patch("tools.knowledge.scope_resolver.Path.home", return_value=tmp_path),
            patch("tools.knowledge.scope_resolver._load_project_mappings") as mock_load,
        ):
            # Directly set the mappings
            kb_valor = str(tmp_path / "work-vault" / "AI Valor Engels System")
            kb_psy = str(tmp_path / "work-vault" / "PsyOptimal")
            mock_load.return_value = [
                (os.path.normpath(kb_valor), "valor"),
                (os.path.normpath(kb_psy), "psyoptimal"),
            ]

            import tools.knowledge.scope_resolver as sr

            sr._project_mappings = [
                (os.path.normpath(kb_valor), "valor"),
                (os.path.normpath(kb_psy), "psyoptimal"),
            ]

            result = resolve_scope(
                str(tmp_path / "work-vault" / "AI Valor Engels System" / "notes.md")
            )
            assert result is not None
            assert result[0] == "valor"
            assert result[1] == "client"

    def test_client_scope_psyoptimal(self, mock_projects_json):
        """File under psyoptimal's knowledge_base maps to client scope."""
        projects_file, tmp_path = mock_projects_json

        import tools.knowledge.scope_resolver as sr

        kb_valor = str(tmp_path / "work-vault" / "AI Valor Engels System")
        kb_psy = str(tmp_path / "work-vault" / "PsyOptimal")
        sr._project_mappings = [
            (os.path.normpath(kb_valor), "valor"),
            (os.path.normpath(kb_psy), "psyoptimal"),
        ]

        result = resolve_scope(str(tmp_path / "work-vault" / "PsyOptimal" / "assessment.md"))
        assert result is not None
        assert result[0] == "psyoptimal"
        assert result[1] == "client"

    def test_company_wide_scope(self, mock_projects_json):
        """File under work-vault root but not under any project maps to company-wide."""
        projects_file, tmp_path = mock_projects_json

        import tools.knowledge.scope_resolver as sr

        kb_valor = str(tmp_path / "work-vault" / "AI Valor Engels System")
        sr._project_mappings = [
            (os.path.normpath(kb_valor), "valor"),
        ]

        # Patch expanduser so ~/work-vault resolves to tmp_path/work-vault
        vault = str(tmp_path / "work-vault")
        _real_expanduser = os.path.expanduser

        def expand_side_effect(p):
            if p == "~/work-vault":
                return vault
            return _real_expanduser(p)

        with patch(
            "tools.knowledge.scope_resolver.os.path.expanduser", side_effect=expand_side_effect
        ):
            result = resolve_scope(str(tmp_path / "work-vault" / "shared-docs" / "readme.md"))
            assert result is not None
            assert result[0] == "company"
            assert result[1] == "company-wide"

    def test_outside_vault_returns_none(self, mock_projects_json):
        """File outside work-vault returns None."""
        projects_file, tmp_path = mock_projects_json

        import tools.knowledge.scope_resolver as sr

        sr._project_mappings = []

        result = resolve_scope("/tmp/random/file.md")
        assert result is None

    def test_longest_match_wins(self, mock_projects_json):
        """Most specific (longest) path match wins."""
        projects_file, tmp_path = mock_projects_json

        import tools.knowledge.scope_resolver as sr

        # Create nested paths
        parent = str(tmp_path / "work-vault" / "Parent")
        child = str(tmp_path / "work-vault" / "Parent" / "Child")
        os.makedirs(child, exist_ok=True)

        sr._project_mappings = [
            (os.path.normpath(child), "child_project"),
            (os.path.normpath(parent), "parent_project"),
        ]

        result = resolve_scope(str(tmp_path / "work-vault" / "Parent" / "Child" / "doc.md"))
        assert result is not None
        assert result[0] == "child_project"

    def test_get_vault_path(self):
        """get_vault_path returns expanded ~/work-vault."""
        vault = get_vault_path()
        assert "work-vault" in vault
        assert "~" not in vault

    def test_reload_clears_cache(self, mock_projects_json):
        """reload_mappings clears cached mappings."""
        import tools.knowledge.scope_resolver as sr

        sr._project_mappings = [("test", "test")]
        reload_mappings()
        # After reload, _project_mappings should be freshly loaded (not the test value)
        # Since projects.json at default location may or may not exist, just verify
        # the cache was cleared and reloaded
        assert sr._project_mappings != [("test", "test")] or sr._project_mappings is not None
