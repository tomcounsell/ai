"""Integration test: end-to-end `--all` against the committed fixture.

Exercises the full pipeline (generate + lint + DTCG/Tailwind export +
check). Requires Node; skipped when `npx` is absent. Runs the generator
against a tempdir copy of the fixture so the test does not mutate the
committed fixture tree.

Each test in this module runs the ``@google/design.md`` npm package via
``npx`` against a shared ``node_modules`` cache. Concurrent npx invocations
race on that cache (resolver / extract / link phases), producing
intermittent non-zero exits. ``--dist=loadfile`` (set in
``pyproject.toml``) keeps all tests in this file on a single xdist worker
so the npx invocations execute serially without losing inter-file
parallelism.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/design_system"


@functools.lru_cache(maxsize=1)
def _pinned_design_md_version() -> str:
    """The `@google/design.md` version pinned in package.json.

    Read dynamically so version bumps don't require a test edit — the probe
    below asserts the installed npx package matches the committed pin.
    """
    pkg = json.loads((REPO_ROOT / "package.json").read_text())
    return pkg["dependencies"]["@google/design.md"]


@functools.lru_cache(maxsize=1)
def _npx_present() -> bool:
    """Memoized npx availability probe.

    Each ``@pytest.mark.skipif(not _npx_present(), ...)`` decorator evaluates
    this at collection time. Without memoization, every test in the module
    spawns an ``npx`` subprocess during collection — and under ``pytest -n
    auto`` every worker collects in parallel. Two of those concurrent
    ``npx --no-install`` probes can race against each other inside npm's
    cache (``~/.npm/_cacache``) and one will spuriously report the package
    as missing, masking the real test signal.

    A short retry loop covers the residual race where multiple xdist
    workers run this probe at collection time even after the lru_cache
    eliminates intra-worker repeats.
    """
    import time as _time

    if shutil.which("npx") is None:
        return False
    for attempt in range(3):
        result = subprocess.run(
            ["npx", "--no-install", "@google/design.md", "--version"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and _pinned_design_md_version() in result.stdout:
            return True
        # npm cache races sometimes report "missing packages" spuriously
        # when another concurrent npx is mutating ~/.npm/_cacache. Brief
        # backoff then retry.
        _time.sleep(0.5 * (attempt + 1))
    return False


@pytest.fixture
def worktree_fixture(tmp_path: Path) -> Path:
    """Copy the fixture tree into tmp_path so we can mutate it freely."""
    dst = tmp_path / "design_system"
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


@pytest.mark.skipif(not _npx_present(), reason="npx / @google/design.md pin not available")
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
