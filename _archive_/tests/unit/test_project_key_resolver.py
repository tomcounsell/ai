"""Unit tests for config.project_key_resolver.resolve_project_key.

Covers the full priority chain:
  1. Explicit project_key kwarg
  2. VALOR_PROJECT_KEY env var
  3. projects.json working_directory prefix match against cwd
  4. None return when all inputs are absent/empty
"""

from __future__ import annotations

import json
from pathlib import Path

from config.project_key_resolver import resolve_project_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_projects_json(tmp_path: Path, projects: dict) -> Path:
    """Write a minimal projects.json to a temp directory."""
    p = tmp_path / "projects.json"
    p.write_text(json.dumps({"projects": projects}))
    return p


# ---------------------------------------------------------------------------
# Priority 1: explicit project_key kwarg
# ---------------------------------------------------------------------------


def test_explicit_project_key_returned_immediately():
    result = resolve_project_key(project_key="myproject")
    assert result == "myproject"


def test_explicit_project_key_bypasses_env(monkeypatch):
    monkeypatch.setenv("VALOR_PROJECT_KEY", "env-project")
    result = resolve_project_key(project_key="explicit")
    assert result == "explicit"


def test_empty_string_project_key_falls_through(monkeypatch, tmp_path):
    """Empty string is treated as absent — falls through to env lookup."""
    monkeypatch.setenv("VALOR_PROJECT_KEY", "env-project")
    result = resolve_project_key(project_key="")
    assert result == "env-project"


# ---------------------------------------------------------------------------
# Priority 2: VALOR_PROJECT_KEY env var
# ---------------------------------------------------------------------------


def test_env_var_used_when_no_explicit_key(monkeypatch):
    monkeypatch.setenv("VALOR_PROJECT_KEY", "valor")
    result = resolve_project_key()
    assert result == "valor"


def test_env_dict_override(monkeypatch):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    result = resolve_project_key(env={"VALOR_PROJECT_KEY": "from-dict"})
    assert result == "from-dict"


def test_env_dict_takes_precedence_over_os_environ(monkeypatch):
    monkeypatch.setenv("VALOR_PROJECT_KEY", "os-env")
    result = resolve_project_key(env={"VALOR_PROJECT_KEY": "dict-env"})
    assert result == "dict-env"


def test_empty_env_var_falls_through(monkeypatch, tmp_path):
    """VALOR_PROJECT_KEY set to empty string is treated as absent."""
    monkeypatch.setenv("VALOR_PROJECT_KEY", "")
    result = resolve_project_key(cwd=None)
    assert result is None


def test_whitespace_env_var_falls_through(monkeypatch):
    monkeypatch.setenv("VALOR_PROJECT_KEY", "   ")
    result = resolve_project_key()
    assert result is None


# ---------------------------------------------------------------------------
# Priority 3: projects.json cwd match
# ---------------------------------------------------------------------------


def test_cwd_match_returns_project_key(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"myrepo": {"working_directory": str(tmp_path)}},
    )
    result = resolve_project_key(
        cwd=str(tmp_path / "subdir"),
        projects_path=projects_path,
    )
    assert result == "myrepo"


def test_cwd_exact_match(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"exactrepo": {"working_directory": str(tmp_path)}},
    )
    result = resolve_project_key(cwd=str(tmp_path), projects_path=projects_path)
    assert result == "exactrepo"


def test_longest_match_wins(monkeypatch, tmp_path):
    """Most-specific (longest) working_directory prefix should win."""
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    sub = tmp_path / "workspace" / "sub"
    projects_path = _make_projects_json(
        tmp_path,
        {
            "parent-proj": {"working_directory": str(tmp_path / "workspace")},
            "sub-proj": {"working_directory": str(sub)},
        },
    )
    result = resolve_project_key(
        cwd=str(sub / "code"),
        projects_path=projects_path,
    )
    assert result == "sub-proj"


def test_no_cwd_match_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"someproject": {"working_directory": "/other/path"}},
    )
    result = resolve_project_key(cwd="/completely/different", projects_path=projects_path)
    assert result is None


def test_cwd_none_skips_projects_json(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"someproject": {"working_directory": str(tmp_path)}},
    )
    result = resolve_project_key(cwd=None, projects_path=projects_path)
    assert result is None


# ---------------------------------------------------------------------------
# Priority 4: None return
# ---------------------------------------------------------------------------


def test_all_none_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(tmp_path, {})
    result = resolve_project_key(cwd=None, projects_path=projects_path)
    assert result is None


def test_no_inputs_at_all_returns_none(monkeypatch, tmp_path):
    """When nothing is provided and no env var set, result is None."""
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    # Pass a projects_path with no matching projects
    projects_path = _make_projects_json(tmp_path, {})
    result = resolve_project_key(projects_path=projects_path)
    assert result is None


# ---------------------------------------------------------------------------
# Resilience: corrupt / missing projects.json
# ---------------------------------------------------------------------------


def test_corrupt_projects_json_falls_through_to_none(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    bad_file = tmp_path / "projects.json"
    bad_file.write_text("NOT VALID JSON{{{")
    result = resolve_project_key(cwd=str(tmp_path), projects_path=bad_file)
    # Cannot match cwd — returns None (no dirname fallback)
    assert result is None


def test_missing_projects_json_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    nonexistent = tmp_path / "no_such_file.json"
    result = resolve_project_key(cwd=str(tmp_path), projects_path=nonexistent)
    assert result is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_cwd_string_treated_as_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"repo": {"working_directory": str(tmp_path)}},
    )
    result = resolve_project_key(cwd="", projects_path=projects_path)
    assert result is None


def test_env_kwarg_empty_dict_falls_through_to_none(tmp_path):
    projects_path = _make_projects_json(tmp_path, {})
    result = resolve_project_key(env={}, cwd=None, projects_path=projects_path)
    assert result is None


def test_projects_json_without_working_directory_skipped(monkeypatch, tmp_path):
    """Projects without working_directory should not match."""
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
    projects_path = _make_projects_json(
        tmp_path,
        {"no-wd-proj": {"name": "no working dir here"}},
    )
    result = resolve_project_key(cwd=str(tmp_path), projects_path=projects_path)
    assert result is None


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def test_cache_reloads_on_new_path(monkeypatch, tmp_path):
    """Calling with a different projects_path flushes the cache."""
    monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)

    p1_dir = tmp_path / "p1"
    p1_dir.mkdir(exist_ok=True)
    path1 = _make_projects_json(p1_dir, {"proj1": {"working_directory": str(tmp_path)}})

    p2_dir = tmp_path / "p2"
    p2_dir.mkdir(exist_ok=True)
    path2 = _make_projects_json(p2_dir, {"proj2": {"working_directory": str(tmp_path)}})

    result1 = resolve_project_key(cwd=str(tmp_path), projects_path=path1)
    assert result1 == "proj1"

    result2 = resolve_project_key(cwd=str(tmp_path), projects_path=path2)
    assert result2 == "proj2"
