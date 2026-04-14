"""Tests for reflections multi-repo support (updated for reflections/ package).

Tests cover:
- reflections.utils.load_local_projects() filters to directories that exist on this machine
- reflections.utils.is_ignored() checks active ignore patterns
- reflections.utils.has_existing_github_work() checks for open issues/PRs

Previously tested scripts.reflections.ReflectionRunner and step_* methods.
Those are now replaced by individual callables in the reflections/ package,
each tested in tests/unit/test_reflections_package.py.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

# --- load_local_projects() tests ---


class TestLoadLocalProjects:
    """Tests for reflections.utils.load_local_projects() filtering."""

    def test_returns_projects_whose_directory_exists(self, tmp_path):
        """Only projects with existing working_directory are returned."""
        from reflections.utils import load_local_projects

        existing_dir = tmp_path / "project_a"
        existing_dir.mkdir()
        missing_dir = tmp_path / "project_b_does_not_exist"

        config = {
            "projects": {
                "proj-a": {
                    "name": "Project A",
                    "working_directory": str(existing_dir),
                },
                "proj-b": {
                    "name": "Project B",
                    "working_directory": str(missing_dir),
                },
            }
        }
        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps(config))

        orig_env = os.environ.get("PROJECTS_CONFIG_PATH")
        os.environ["PROJECTS_CONFIG_PATH"] = str(config_path)
        try:
            projects = load_local_projects()
        finally:
            if orig_env is None:
                os.environ.pop("PROJECTS_CONFIG_PATH", None)
            else:
                os.environ["PROJECTS_CONFIG_PATH"] = orig_env

        slugs = [p["slug"] for p in projects]
        assert "proj-a" in slugs
        assert "proj-b" not in slugs

    def test_includes_slug_in_project_dict(self, tmp_path):
        """Each project dict includes 'slug' key from config key."""
        from reflections.utils import load_local_projects

        existing_dir = tmp_path / "my_project"
        existing_dir.mkdir()

        config = {
            "projects": {
                "my-slug": {
                    "name": "My Project",
                    "working_directory": str(existing_dir),
                }
            }
        }

        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps(config))
        orig_env = os.environ.get("PROJECTS_CONFIG_PATH")
        os.environ["PROJECTS_CONFIG_PATH"] = str(config_path)
        try:
            projects = load_local_projects()
        finally:
            if orig_env is None:
                os.environ.pop("PROJECTS_CONFIG_PATH", None)
            else:
                os.environ["PROJECTS_CONFIG_PATH"] = orig_env

        assert len(projects) == 1
        assert projects[0]["slug"] == "my-slug"
        assert projects[0]["name"] == "My Project"

    def test_returns_empty_list_when_no_projects_exist(self, tmp_path):
        """Returns empty list when no configured projects have existing dirs."""
        from reflections.utils import load_local_projects

        config = {
            "projects": {
                "ghost": {
                    "name": "Ghost",
                    "working_directory": str(tmp_path / "does_not_exist"),
                }
            }
        }

        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps(config))
        orig_env = os.environ.get("PROJECTS_CONFIG_PATH")
        os.environ["PROJECTS_CONFIG_PATH"] = str(config_path)
        try:
            projects = load_local_projects()
        finally:
            if orig_env is None:
                os.environ.pop("PROJECTS_CONFIG_PATH", None)
            else:
                os.environ["PROJECTS_CONFIG_PATH"] = orig_env

        assert projects == []

    def test_returns_empty_list_when_config_missing(self, tmp_path):
        """Returns empty list when projects.json doesn't exist."""
        from reflections.utils import load_local_projects

        orig_env = os.environ.get("PROJECTS_CONFIG_PATH")
        nonexistent = str(tmp_path / "does_not_exist.json")
        os.environ["PROJECTS_CONFIG_PATH"] = nonexistent
        try:
            with patch("reflections.utils.AI_ROOT", tmp_path):
                projects = load_local_projects()
        finally:
            if orig_env is None:
                os.environ.pop("PROJECTS_CONFIG_PATH", None)
            else:
                os.environ["PROJECTS_CONFIG_PATH"] = orig_env

        assert projects == []

    def test_multi_project_filters_to_existing(self, tmp_path):
        """Multiple projects: only those with existing dirs are included."""
        from reflections.utils import load_local_projects

        dir_a = tmp_path / "repo_a"
        dir_b = tmp_path / "repo_b"
        dir_a.mkdir()
        # dir_b intentionally not created

        config = {
            "projects": {
                "slug-a": {"name": "A", "working_directory": str(dir_a)},
                "slug-b": {"name": "B", "working_directory": str(dir_b)},
                "slug-c": {"name": "C", "working_directory": str(dir_a)},
            }
        }
        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps(config))

        orig_env = os.environ.get("PROJECTS_CONFIG_PATH")
        os.environ["PROJECTS_CONFIG_PATH"] = str(config_path)
        try:
            projects = load_local_projects()
        finally:
            if orig_env is None:
                os.environ.pop("PROJECTS_CONFIG_PATH", None)
            else:
                os.environ["PROJECTS_CONFIG_PATH"] = orig_env

        slugs = {p["slug"] for p in projects}
        assert "slug-a" in slugs
        assert "slug-c" in slugs
        assert "slug-b" not in slugs


# --- is_ignored() tests ---


class TestIsIgnored:
    """Tests for reflections.utils.is_ignored()."""

    def test_matches_substring(self):
        """Returns True when ignore pattern is substring of input."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "redis connection", "ignored_until": "", "reason": ""}]
        assert is_ignored("redis connection error", entries) is True

    def test_no_match(self):
        """Returns False when no entries match."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "redis connection", "ignored_until": "", "reason": ""}]
        assert is_ignored("completely unrelated pattern", entries) is False

    def test_empty_entries(self):
        """Returns False when entries list is empty."""
        from reflections.utils import is_ignored

        assert is_ignored("any pattern", []) is False

    def test_case_insensitive_match(self):
        """Matching is case-insensitive."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "Redis Connection", "ignored_until": "", "reason": ""}]
        assert is_ignored("redis connection timeout", entries) is True

    def test_reverse_match(self):
        """Input also matches if it's a substring of the pattern."""
        from reflections.utils import is_ignored

        entries = [{"pattern": "redis connection timeout issue", "ignored_until": "", "reason": ""}]
        assert is_ignored("redis connection timeout", entries) is True


# --- has_existing_github_work() tests ---


class TestHasExistingGithubWork:
    """Tests for reflections.utils.has_existing_github_work()."""

    def test_returns_true_when_issue_found(self, tmp_path):
        """Returns True when gh issue list finds a matching open issue."""
        from reflections.utils import has_existing_github_work

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  #42  Bug: connection error")
            result = has_existing_github_work("redis connection error", str(tmp_path))

        assert result is True

    def test_returns_false_when_no_issues(self, tmp_path):
        """Returns False when gh issue list returns empty output."""
        from reflections.utils import has_existing_github_work

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = has_existing_github_work("no such bug", str(tmp_path))

        assert result is False

    def test_returns_false_on_gh_failure(self, tmp_path):
        """Returns False when gh CLI fails."""
        from reflections.utils import has_existing_github_work

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = has_existing_github_work("some pattern", str(tmp_path))

        assert result is False

    def test_handles_exception_gracefully(self, tmp_path):
        """Returns False when gh CLI raises an exception."""
        from reflections.utils import has_existing_github_work

        with patch("subprocess.run", side_effect=Exception("timeout")):
            result = has_existing_github_work("some pattern", str(tmp_path))

        assert result is False
