"""Integration test: end-to-end `--all` against the committed fixture.

Exercises the full pipeline (generate + lint + DTCG/Tailwind export +
check). Requires Node; skipped when `npx` is absent. Runs the generator
against a tempdir copy of the fixture so the test does not mutate the
committed fixture tree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/design_system"


def _npx_present() -> bool:
    if shutil.which("npx") is None:
        return False
    result = subprocess.run(
        ["npx", "--no-install", "@google/design.md", "--version"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result.returncode == 0 and "0.1.1" in result.stdout


@pytest.fixture
def worktree_fixture(tmp_path: Path) -> Path:
    """Copy the fixture tree into tmp_path so we can mutate it freely."""
    dst = tmp_path / "design_system"
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


@pytest.mark.skipif(not _npx_present(), reason="npx / @google/design.md@0.1.1 not available")
def test_all_pipeline_produces_committed_artifacts(worktree_fixture: Path):
    pen = worktree_fixture / "design-system.pen"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.design_system_sync",
            "--all",
            "--pen",
            str(pen),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert (worktree_fixture / "design-system.md").is_file()
    assert (worktree_fixture / "css/brand.css").is_file()
    assert (worktree_fixture / "css/source.css").is_file()
    assert (worktree_fixture / "exports/tokens.dtcg.json").is_file()
    assert (worktree_fixture / "exports/tailwind.theme.json").is_file()

    # `--check` now passes.
    check = subprocess.run(
        [sys.executable, "-m", "tools.design_system_sync", "--check", "--pen", str(pen)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert check.returncode == 0, check.stderr


@pytest.mark.skipif(not _npx_present(), reason="npx not available")
def test_check_detects_mutation(worktree_fixture: Path):
    pen = worktree_fixture / "design-system.pen"
    # Prime with a successful --all so the exports exist.
    subprocess.run(
        [sys.executable, "-m", "tools.design_system_sync", "--all", "--pen", str(pen)],
        check=True,
        cwd=str(REPO_ROOT),
    )
    brand = worktree_fixture / "css/brand.css"
    brand.write_text(brand.read_text() + "\n/* drift */\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "tools.design_system_sync", "--check", "--pen", str(pen)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    assert "differs from generated output" in result.stderr


@pytest.mark.skipif(not _npx_present(), reason="npx not available")
def test_fixture_design_md_passes_lint():
    """The committed design-system.md must pass `@google/design.md lint`."""
    md = FIXTURE_DIR / "design-system.md"
    assert md.is_file(), "fixture design-system.md missing — run --all"
    result = subprocess.run(
        ["npx", "--no-install", "@google/design.md", "lint", str(md)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # At least one finding entry should report "errors: 0".
    parsed = json.loads(result.stdout)
    assert parsed["summary"]["errors"] == 0
