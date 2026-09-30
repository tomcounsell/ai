"""The bridge's revival-dormancy path, after `mark_work_done` stopped deleting.

``mark_work_done`` used to delete the branch itself, gated only on a
``session/`` prefix (#3411). Removing that deletion leaves the bridge's
revival-dormancy path with nothing to make a lane dormant: archiving,
committing and returning to ``main`` do not remove a branch from
``git branch --list``, and ``check_revival``'s branch-existence check is what
drops a candidate from the prompt list. Without a replacement the lane re-fires
its revival prompt every ``REVIVAL_COOLDOWN_SECONDS`` forever — and because
that cooldown is per-chat, each false revival also suppresses genuine ones for
24h.

The replacement is ``retire_revival_branch``, which routes the deletion through
``safe_delete_branch`` + ``merged_via_ancestor`` so the #1646 guard applies. The
behaviour change is deliberate and is the point of the task: an **unmerged**
branch used to be destroyed here to achieve dormancy, and is now preserved. The
prompt re-firing once per 24h for genuinely unfinished work is the designed
cadence, not a regression.

``working_dir_str`` on this path is the shared project checkout, not a
``.worktrees/{slug}/`` lane, so nothing lane-scoped applies here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bridge.telegram_bridge import retire_revival_branch

BRANCH = "session/dev-3411beef"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _branch_exists(repo: Path, branch: str) -> bool:
    return bool(
        subprocess.run(
            ["git", "branch", "--list", branch], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
    )


@pytest.fixture
def project_checkout(tmp_path):
    """A shared project checkout on ``main``, with a stale session branch.

    Shaped like the bridge's real input: ``project["working_directory"]``, the
    ordinary checkout, never a linked lane worktree.
    """
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / ".gitkeep").write_text("")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md", "docs/plans/.gitkeep")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def test_landed_branch_is_deleted_so_the_prompt_goes_dormant(project_checkout):
    """A merged-and-stuck branch is removed, which is the pointless-nag case.

    This is the outcome that makes dormancy work: ``check_revival`` drops a
    candidate when its branch no longer exists, so the merged branch that would
    otherwise re-prompt every 24h stops being a candidate at all.
    """
    repo = project_checkout
    _git(repo, "branch", BRANCH)

    result = retire_revival_branch(str(repo), BRANCH, "test-project")

    assert result is not None
    assert result["deleted"] is True
    assert not _branch_exists(repo, BRANCH)


def test_unmerged_branch_is_preserved(project_checkout):
    """Unmerged work survives — the #1646 violation this path used to commit.

    Dormancy is not worth destroying work for. The prompt may re-fire once per
    24h per chat for this branch, and that is the designed cadence for work
    that is genuinely unfinished.
    """
    repo = project_checkout
    _git(repo, "checkout", "-b", BRANCH)
    (repo / "feature.py").write_text("# work that never landed\n")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "feat: unfinished")
    _git(repo, "checkout", "main")

    result = retire_revival_branch(str(repo), BRANCH, "test-project")

    assert result is not None
    assert result["deleted"] is False
    assert result["skipped_unmerged"] is True
    assert _branch_exists(repo, BRANCH), (
        "the bridge destroyed unmerged work to achieve dormancy (#1646, #3411)"
    )


def test_a_broken_checkout_preserves_and_does_not_raise(tmp_path):
    """A non-repo path fails safe to preserving, and never breaks intake.

    This runs inline in the Telegram message handler, so nothing here may
    propagate. ``safe_delete_branch`` cannot resolve a base in a non-repo, and
    an unresolvable base is a refusal to delete — which is why the log line for
    this slot says "not proven merged" rather than "unmerged".
    """
    not_a_repo = tmp_path / "nowhere"
    not_a_repo.mkdir()

    result = retire_revival_branch(str(not_a_repo), BRANCH, "test-project")

    assert result is not None
    assert result["deleted"] is False
    assert result["skipped_unmerged"] is True
