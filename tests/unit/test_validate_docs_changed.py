"""Tests for scripts/validate_docs_changed.py exit-code and scan-scope contract.

Covers issue #2133: the stale-marker scan must be diff-scoped (only lines ADDED
by the branch, never pre-existing file content) and the exit codes must follow
the documented contract:

    0 = pass
    1 = missing docs (hard fail, blocks PR)
    2 = stale markers found (non-blocking warning)
    3 = internal/usage error (plan not found, read failure) — blocks PR

These tests drive the validator as a subprocess against a purpose-built temp git
repo so the git-diff plumbing is exercised for real (no mocks).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_docs_changed.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_validator(
    repo: Path,
    plan_path: str,
    base_branch: str = "main",
    extra: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the validator with cwd inside the temp repo so its git calls resolve there."""
    cmd = [sys.executable, str(SCRIPT), plan_path, "--base-branch", base_branch]
    if extra:
        cmd += extra
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True)


def _write_plan(repo: Path, doc_paths: list[str], name: str = "plan.md") -> str:
    """Write a minimal plan file with a ## Documentation section listing doc_paths.

    Returns the plan path relative to the repo root (as passed on the CLI).
    """
    lines = ["# Plan", "", "## Documentation", ""]
    for p in doc_paths:
        lines.append(f"- [ ] Update `{p}` for the feature")
    plan = repo / name
    plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return name


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    # Seed an unrelated base commit so `main` exists as a ref.
    (repo / "README.md").write_text("# Seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def _commit_doc_on_main(git_repo: Path, rel: str, content: str) -> None:
    doc = git_repo / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(content, encoding="utf-8")
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", f"add {rel}")


def _new_branch(git_repo: Path, name: str = "session/test") -> None:
    _git(git_repo, "checkout", "-b", name)


# ---------------------------------------------------------------------------
# Exit-code contract
# ---------------------------------------------------------------------------


def test_stale_marker_in_added_line_exits_2(git_repo: Path):
    """A stale marker on a line ADDED by the branch → non-blocking warning, exit 2."""
    rel = "docs/features/foo.md"
    _commit_doc_on_main(git_repo, rel, "# Foo\n\nOriginal clean content.\n")
    _new_branch(git_repo)
    doc = git_repo / rel
    doc.write_text(
        "# Foo\n\nOriginal clean content.\nThis behaviour is deprecated now.\n",
        encoding="utf-8",
    )
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", "add stale line")

    plan = _write_plan(git_repo, [rel])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)


def test_missing_docs_exits_1(git_repo: Path):
    """Expected docs never changed → hard fail, exit 1."""
    _new_branch(git_repo)
    plan = _write_plan(git_repo, ["docs/features/never-touched.md"])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)


def test_missing_plan_file_exits_3(git_repo: Path):
    """Plan file not found → internal/usage error, exit 3 (not 2, which is a warning)."""
    result = _run_validator(git_repo, "docs/plans/does-not-exist.md")
    assert result.returncode == 3, (result.returncode, result.stdout, result.stderr)


def test_trigger_word_only_in_preexisting_content_exits_0(git_repo: Path):
    """A trigger word that exists ONLY in pre-existing (unchanged) lines is not flagged.

    Regression for issue #2133 / PR #2132 false positives (e.g. the
    "NO LEGACY CODE TOLERANCE" principle text living verbatim on main). Replaces
    the manual workaround noted in
    docs/plans/completed/merge-gate-baseline-refresh.md:364.
    """
    rel = "docs/features/principles.md"
    _commit_doc_on_main(
        git_repo,
        rel,
        "# Principles\n\nNO LEGACY CODE TOLERANCE: never leave traces of legacy code.\n",
    )
    _new_branch(git_repo)
    doc = git_repo / rel
    # Add an unrelated, clean line only.
    doc.write_text(
        "# Principles\n\nNO LEGACY CODE TOLERANCE: never leave traces of legacy code.\n"
        "A brand new capability was added here.\n",
        encoding="utf-8",
    )
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", "add unrelated clean line")

    plan = _write_plan(git_repo, [rel])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_stale_marker_in_new_untracked_doc_exits_2(git_repo: Path):
    """A brand-new untracked doc → all lines count as added; a stale marker IS flagged."""
    _new_branch(git_repo)
    rel = "docs/features/brand-new.md"
    doc = git_repo / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    # Untracked (never git-added) doc containing a trigger word.
    doc.write_text("# New\n\nThis path is deprecated already.\n", encoding="utf-8")

    plan = _write_plan(git_repo, [rel])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)


def test_single_line_addition_hunk_header_is_parsed(git_repo: Path):
    """Concern #2: a brand-new single-line doc emits `@@ -0,0 +1 @@` (omitted ,d count).

    The hunk parser must still detect the added line and flag its stale marker.
    """
    _new_branch(git_repo)
    rel = "docs/features/oneline.md"
    doc = git_repo / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("This single line is obsolete.\n", encoding="utf-8")
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", "add one-line doc")

    plan = _write_plan(git_repo, [rel])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)


def test_clean_added_doc_exits_0(git_repo: Path):
    """A changed doc with no stale markers in added lines → pass, exit 0."""
    rel = "docs/features/clean.md"
    _commit_doc_on_main(git_repo, rel, "# Clean\n\nOriginal.\n")
    _new_branch(git_repo)
    doc = git_repo / rel
    doc.write_text("# Clean\n\nOriginal.\nA fresh, tidy sentence.\n", encoding="utf-8")
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", "add clean line")

    plan = _write_plan(git_repo, [rel])
    result = _run_validator(git_repo, plan)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


# ---------------------------------------------------------------------------
# get_added_lines helper (unit level)
# ---------------------------------------------------------------------------


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_docs_changed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_get_added_lines_git_failure_returns_empty(tmp_path: Path, monkeypatch):
    """Concern: git failure (not a repo) must not crash; helper returns no added lines."""
    mod = _load_module()
    monkeypatch.chdir(tmp_path)  # not a git repo
    result = mod.get_added_lines("docs/features/whatever.md", "main")
    assert result == []


def test_get_added_lines_reports_added_line_numbers(git_repo: Path, monkeypatch):
    """Added lines are returned with their true new-file line numbers."""
    mod = _load_module()
    rel = "docs/features/nums.md"
    _commit_doc_on_main(git_repo, rel, "# Nums\n\nline a\nline b\n")
    _new_branch(git_repo)
    doc = git_repo / rel
    doc.write_text("# Nums\n\nline a\nline b\nline c added\n", encoding="utf-8")
    _git(git_repo, "add", rel)
    _git(git_repo, "commit", "-m", "add line c")

    monkeypatch.chdir(git_repo)
    added = mod.get_added_lines(rel, "main")
    # Only the newly added line body should be present.
    bodies = [text for _, text in added]
    assert any("line c added" in b for b in bodies)
    assert all("line a" not in b and "line b" not in b for b in bodies)
