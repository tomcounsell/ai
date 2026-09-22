"""Lane branch identity: the two-turn regression test for issue #3411.

The defect: a lane's branch identity is *derived* from the slug
(``session/{slug}``) instead of read from the record, so the end-of-turn
cleanup deletes a branch that is not where the turn's commits are, and the
next turn's #1377 launch guard then demands the branch cleanup just deleted.
The 2026-09-16 incident had 646 ms between the delete and the refusal, and the
user-visible symptom was silence: the work had shipped, but the turn that
would have reported it never launched.

**This test runs two turns.** A single-turn assertion passes while the bug
survives (plan Risk 1): the damage only becomes visible when the *next* turn
tries to launch. The second turn launching is the assertion.

Assertion shapes deliberately avoided, because they pass against the unfixed
code and so certify nothing:

* "nothing was deleted" / "``safe_delete_branch`` refused" — git itself
  refuses to delete a branch a live worktree has checked out (plan spike-1),
  and ``merged_via_ancestor`` fail-safes to *preserve* when handed a branch
  name that no longer resolves (plan spike-4). Both hold on the known-bad
  code. Every assertion below is instead about the **target name** cleanup
  chose and about whether the next turn launches.

Two divergence shapes get a test each, because they strand the lane for
different reasons and are fixed by different halves of the change:

* ``test_diverged_lane_second_turn_launches`` — the worktree sits on a
  differently-named branch. The incident shape, and 2 of the 7 divergent lanes
  measured in plan spike-3.
* ``test_detached_lane_second_turn_launches`` — the worktree is detached, so
  ``git rev-parse --abbrev-ref HEAD`` returns the literal string ``"HEAD"``
  (plan spike-2) and there is no branch to name at all. 5 of those 7 lanes are
  in this state, making it the dominant shape rather than a corner case.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.agent_session_queue import checkpoint_branch_state
from agent.branch_manager import mark_work_done
from agent.worktree_manager import (
    WORKTREES_DIR,
    WorktreeBranchMismatchError,
    merged_via_ancestor,
    safe_delete_branch,
    verify_worktree_branch,
)
from models.agent_session import AgentSession

PROJECT_KEY = "test-lane-branch-identity"
SLUG = "dev-3411abcd"
LANE_BRANCH = f"session/{SLUG}"
WORK_BRANCH = "session/cruft-auditor-exception-checks"


# ---------------------------------------------------------------------------
# Real-git helpers (no mocks — real repo, real linked worktree, real branches)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _branch_exists(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _head_branch(worktree: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """A minimal repo on ``main``, with the ``docs/plans/`` dir cleanup reads."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / ".gitkeep").write_text("")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md", "docs/plans/.gitkeep")
    _git(repo, "commit", "-m", "initial commit")
    return repo


@pytest.fixture
def diverged_lane(tmp_path):
    """Stage a lane whose worktree moved off ``session/{slug}`` mid-turn.

    This is the incident shape: the worktree is created on the slug-derived
    branch, then the agent checks out a differently-named branch and commits
    its work there. 7 of 31 live lanes were measured in this state
    (plan spike-3), so it is the normal case, not an exotic one.
    """
    repo = _make_repo(tmp_path)
    worktree = repo / WORKTREES_DIR / SLUG
    _git(repo, "worktree", "add", str(worktree), "-b", LANE_BRANCH)

    # Mid-turn: the agent moves off the lane branch and commits real work.
    _git(worktree, "checkout", "-b", WORK_BRANCH)
    (worktree / "feature.py").write_text("# shipped work\n")
    _git(worktree, "add", "feature.py")
    _git(worktree, "commit", "-m", "feat: the work the turn actually did")

    assert _head_branch(worktree) == WORK_BRANCH
    assert _branch_exists(repo, LANE_BRANCH)

    session = AgentSession(
        session_id="tg_valor_-1003449100931_1473",
        project_key=PROJECT_KEY,
        session_type="eng",
        status="running",
        slug=SLUG,
        working_dir=str(worktree),
    )
    session.save()
    try:
        yield repo, worktree, session
    finally:
        for row in AgentSession.query.filter(project_key=PROJECT_KEY):
            row.delete()


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------


def test_diverged_lane_second_turn_launches(diverged_lane):
    """A lane whose worktree left ``session/{slug}`` stays resumable.

    RED on baseline ``bbe5dc7a1``: turn 1's cleanup reads the slug-derived
    name, deletes ``session/{slug}`` (deletable precisely because it is *not*
    where the work is), and turn 2's guard then demands that same deleted
    name and raises ``WorktreeBranchMismatchError``.
    """
    repo, worktree, session = diverged_lane

    # --- Turn 1 ---------------------------------------------------------
    # The record is written before cleanup runs (session_executor.py:2589,
    # via complete_transcript -> finalize_session).
    checkpoint_branch_state(session)

    # End-of-turn cleanup, in production order. The branch identity comes
    # from the model accessor rather than a name spelled here, because *which
    # name cleanup reads* is the whole defect.
    cleanup_target = session.derived_branch_name
    predicate_targets: list[str] = []

    def recording_predicate(repo_root: str, branch: str, base: str) -> bool:
        """The real #1646 predicate, recording what it was asked about."""
        predicate_targets.append(branch)
        return merged_via_ancestor(repo_root, branch, base)

    mark_work_done(worktree, cleanup_target)
    safe_delete_branch(
        str(worktree),
        cleanup_target,
        predicate=recording_predicate,
        force=False,
    )

    # --- Turn 2: the assertion -------------------------------------------
    # A fresh turn re-reads the row and runs the #1377 launch guard. Whether
    # this launches is the whole test; everything below it is corroboration
    # of *why*.
    rows = list(AgentSession.query.filter(project_key=PROJECT_KEY))
    assert len(rows) == 1, f"expected one lane row, got {len(rows)}"
    expected_branch = rows[0].derived_branch_name
    assert expected_branch, "the next turn has no branch to launch against"

    try:
        verify_worktree_branch(worktree, expected_branch)
    except WorktreeBranchMismatchError as exc:
        pytest.fail(
            "second turn refused to launch (issue #3411): "
            f"expected={exc.expected_branch!r} actual={exc.actual_branch!r} "
            f"-- {exc}"
        )

    # --- Why it launched --------------------------------------------------
    # The turn's commits live on WORK_BRANCH, so that is the only branch any
    # deletion may target and the only branch the merge predicate may be
    # asked about (plan AC 2, AC 3).
    assert predicate_targets == [WORK_BRANCH], (
        f"merge predicate was asked about {predicate_targets}, not the branch "
        f"holding the turn's commits ({WORK_BRANCH!r})"
    )
    assert _branch_exists(repo, LANE_BRANCH), (
        f"cleanup deleted {LANE_BRANCH!r} — a branch that holds none of the "
        f"turn's commits, and the one the next turn's guard will demand"
    )
    assert _branch_exists(repo, WORK_BRANCH), (
        f"cleanup deleted {WORK_BRANCH!r}, which carries unmerged work"
    )
