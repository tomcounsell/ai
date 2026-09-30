"""Automatic drift guard for the Codex skills manifest.

scripts/codex_skills.py check validates .agents/skills-manifest.json against the
repository, including sha256 drift of the Claude sources each Codex skill was derived
from. Nothing wired that into CI or a hook, and scripts/tests/test_codex_skills.py is
excluded from the default pytest suite (pyproject.toml testpaths = ["tests"]), so the
drift guard never actually ran. This test is collected by the default suite and runs
the real check against the real repository root: any edit to a .claude/skills*/ source
without refreshing its source_files hash in .agents/skills-manifest.json fails here.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/codex_skills.py"

_spec = importlib.util.spec_from_file_location("codex_skills_manifest_guard", SCRIPT)
_codex_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_codex_skills)


def test_codex_skills_manifest_has_no_drift():
    result = _codex_skills.check(REPO_ROOT)
    assert result["ok"], "Codex skills manifest drift detected:\n" + "\n".join(result["errors"])


def test_codex_skills_counts_are_internally_consistent():
    result = _codex_skills.check(REPO_ROOT)
    assert result["global"] + result["project"] == result["skills"]
