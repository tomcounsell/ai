"""Cross-repo smoke test for SDLC plan resolution (issue #1535, D1/D2).

This is the test that would have caught D1/D2 originally: it stands up a
*temporary non-`ai` git repo* (no ``SDLC_TARGET_REPO`` set) and asserts that
``find_plan_path`` resolves a plan from the cwd git root — the portability
contract that the pipeline depends on to run unattended in any repo.

It runs the resolver in a real subprocess whose cwd is the temp repo, so the
``git rev-parse --show-toplevel`` branch is exercised end-to-end (not mocked).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.sdlc

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "otherrepo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _resolve_plan_in(repo: Path, issue_number: int) -> str:
    """Run find_plan_path in a subprocess with cwd=repo and no SDLC_TARGET_REPO."""
    env = {k: v for k, v in os.environ.items() if k != "SDLC_TARGET_REPO"}
    code = textwrap.dedent(
        f"""
        from tools._sdlc_utils import find_plan_path
        p = find_plan_path({issue_number})
        print(p if p else "")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_resolves_plan_from_cwd_git_root_no_env(tmp_path):
    """D1: with no SDLC_TARGET_REPO, the plan resolves from the cwd git root."""
    repo = _make_temp_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    plan = plans_dir / "some-feature.md"
    plan.write_text("---\ntracking: https://github.com/org/otherrepo/issues/4242\n---\n")

    resolved = _resolve_plan_in(repo, 4242)
    assert resolved == str(plan)


def test_tracking_url_only_plan_resolves(tmp_path):
    """D2: a plan referencing the issue only by tracking URL is found."""
    repo = _make_temp_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    plan = plans_dir / "url-only.md"
    plan.write_text("tracking: https://github.com/org/otherrepo/issues/145\n")

    resolved = _resolve_plan_in(repo, 145)
    assert resolved == str(plan)


def test_boundary_longer_issue_number_does_not_match(tmp_path):
    """D2: #1455 must not satisfy a lookup for issue 145."""
    repo = _make_temp_repo(tmp_path)
    plans_dir = repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "other.md").write_text("see #1455 and issues/1455\n")

    resolved = _resolve_plan_in(repo, 145)
    assert resolved == ""


def _resolve_plan_with_env(env_repo: Path, issue_number: int) -> str:
    """Run find_plan_path with SDLC_TARGET_REPO set, cwd forced to REPO_ROOT (ai-repo)."""
    env = {k: v for k, v in os.environ.items() if k != "SDLC_TARGET_REPO"}
    code = textwrap.dedent(
        f"""
        from tools._sdlc_utils import find_plan_path
        p = find_plan_path({issue_number})
        print(p if p else "")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),  # force cwd to ai-repo (simulates sdlc-tool behaviour)
        capture_output=True,
        text=True,
        env={
            **env,
            "PYTHONPATH": str(REPO_ROOT),
            "SDLC_TARGET_REPO": str(env_repo),
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_sdlc_target_repo_honored_even_when_cwd_is_ai_repo(tmp_path):
    """SDLC_TARGET_REPO is used by find_plan_path even when cwd is the ai-repo.

    This is the end-to-end regression test for issue #1761: sdlc-tool forces
    cwd to ~/src/ai, so without SDLC_TARGET_REPO the resolver finds plans
    in the ai-repo instead of the target repo.
    """
    target_repo = tmp_path / "client-repo"
    plans_dir = target_repo / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    plan = plans_dir / "client-feature.md"
    plan.write_text("tracking: https://github.com/client/repo/issues/6161\n")

    resolved = _resolve_plan_with_env(target_repo, 6161)
    assert resolved == str(plan)


def test_file_fallback_bare_mention_suppressed(tmp_path):
    """When only the __file__ fallback resolves and we're not in any git repo,
    a bare-#N textual match returns None (suppressed per CONCERN 3, #1761).

    This test runs a subprocess with cwd set to a non-git directory so that
    _git_toplevel returns None, forcing the __file__ fallback.  The ai-repo
    docs/plans directory contains no plan for issue 8888, so the result is None.
    We plant a plan in a *non-git* tmp dir to simulate a foreign cross-reference
    that must NOT be returned.
    """
    # Create a non-git temp directory to use as cwd (no git init).
    non_git_dir = tmp_path / "not-a-repo"
    non_git_dir.mkdir()

    env = {k: v for k, v in os.environ.items() if k != "SDLC_TARGET_REPO"}
    code = textwrap.dedent(
        """
        from tools._sdlc_utils import find_plan_path
        # Issue 8888888 extremely unlikely to be in the real ai-repo plans dir.
        p = find_plan_path(8888888)
        print(p if p else "")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(non_git_dir),  # not a git repo — forces __file__ fallback
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr
    # When __file__ fallback is used and no tracking: match exists, bare-#N
    # must return None (suppressed).
    assert result.stdout.strip() == ""
