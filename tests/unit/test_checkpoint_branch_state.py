"""Tests for ``checkpoint_branch_state`` and ``restore_branch_state`` (#3411).

``checkpoint_branch_state`` is the sole writer of ``AgentSession.branch_name``
(``refresh_lane_branch`` delegates to it), so what it records *is* the lane's
identity. Three properties are asserted here:

- a detached worktree clears the field rather than recording the literal
  ``"HEAD"``, which git returns from ``rev-parse --abbrev-ref`` and which is
  not a branch name;
- a git failure leaves the record **unchanged** — clearing on failure would
  turn a transient read error into a permanently lost lane identity;
- ``commit_sha`` is persisted. It reads and writes through ``session_events``,
  not a Popoto field of its own: the property is an alias for
  ``last_commit_sha``, which scans the event list for the latest ``checkpoint``
  event, and the setter appends one. ``session_events`` is already in
  ``update_fields``, so the write lands. Adding a literal ``"commit_sha"`` to
  that list does not strengthen this — Popoto rejects it outright with
  ``ModelException: Unknown field 'commit_sha'``, which would make every
  checkpoint raise. This test is the standing guard against that "fix".

Every assertion reloads the row **through the ORM**, never through raw Redis.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.agent_session_queue import checkpoint_branch_state, restore_branch_state
from models.agent_session import AgentSession

PROJECT_KEY = "test-checkpoint-3411"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_session(working_dir: Path, **kwargs) -> AgentSession:
    session = AgentSession(
        session_id=kwargs.pop("session_id", "tg_valor_-100checkpoint_1"),
        project_key=PROJECT_KEY,
        session_type="eng",
        status="running",
        slug="ckpt-3411",
        working_dir=str(working_dir),
        **kwargs,
    )
    session.save()
    return session


def _reload(session: AgentSession) -> AgentSession:
    """Re-read the row through the ORM — never raw Redis."""
    rows = list(AgentSession.query.filter(project_key=PROJECT_KEY))
    match = [r for r in rows if r.session_id == session.session_id]
    assert match, f"session {session.session_id} not found via the ORM"
    return match[0]


@pytest.fixture(autouse=True)
def _cleanup_rows():
    yield
    for row in AgentSession.query.filter(project_key=PROJECT_KEY):
        row.delete()


# ---------------------------------------------------------------------------
# checkpoint_branch_state
# ---------------------------------------------------------------------------


def test_records_the_live_branch_and_the_sha(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-b", "session/ckpt-3411")
    session = _make_session(repo)

    checkpoint_branch_state(session)

    reloaded = _reload(session)
    assert reloaded.branch_name == "session/ckpt-3411"
    assert reloaded.commit_sha == _head_sha(repo), (
        "the SHA half of the checkpoint must survive a reload; it rides on "
        "session_events, which is already in update_fields"
    )


def test_detached_head_clears_the_branch_rather_than_recording_the_literal(tmp_path):
    """``rev-parse --abbrev-ref`` answers ``"HEAD"``; that is not a branch."""
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "--detach")
    session = _make_session(repo, branch_name="session/ckpt-3411")

    checkpoint_branch_state(session)

    reloaded = _reload(session)
    assert (reloaded.branch_name or "").strip() != "HEAD"
    assert not (reloaded.branch_name or "").strip(), (
        "a detached lane is on no branch, and the record must say so"
    )
    assert reloaded.commit_sha == _head_sha(repo), (
        "detaching loses the branch, not the commit — the SHA is still the "
        "half of the checkpoint that can restore the state"
    )


def test_a_git_failure_leaves_the_record_untouched(tmp_path):
    """Never clear on failure: a transient read error is not a detached HEAD."""
    session = _make_session(tmp_path / "not-a-repo", branch_name="session/ckpt-3411")
    session.commit_sha = "0" * 40
    session.save(update_fields=["session_events", "updated_at"])

    checkpoint_branch_state(session)

    reloaded = _reload(session)
    assert reloaded.branch_name == "session/ckpt-3411"
    assert reloaded.commit_sha == "0" * 40


def test_no_working_dir_is_a_no_op(tmp_path):
    session = _make_session(tmp_path / "unused", branch_name="session/ckpt-3411")
    session.working_dir = ""
    session.save(update_fields=["working_dir", "updated_at"])

    checkpoint_branch_state(session)

    assert _reload(session).branch_name == "session/ckpt-3411"


# ---------------------------------------------------------------------------
# restore_branch_state
# ---------------------------------------------------------------------------


def test_restore_is_a_no_op_when_already_on_the_recorded_branch(tmp_path):
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-b", "session/ckpt-3411")
    session = _make_session(repo, branch_name="session/ckpt-3411")
    session.commit_sha = _head_sha(repo)
    session.save(update_fields=["session_events", "updated_at"])

    assert restore_branch_state(session) is True
    assert _live_branch(repo) == "session/ckpt-3411"


def test_restore_checks_out_the_recorded_branch_from_a_detached_head(tmp_path):
    """Detached now compares ``None`` against the record, not the literal ``"HEAD"``.

    Same outcome as before by luck — ``"HEAD"`` also compared unequal — but for
    the right reason, and with one spelling of the read in the repo.
    """
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-b", "session/ckpt-3411")
    sha = _head_sha(repo)
    _git(repo, "checkout", "--detach")
    session = _make_session(repo, branch_name="session/ckpt-3411")
    session.commit_sha = sha
    session.save(update_fields=["session_events", "updated_at"])

    assert restore_branch_state(session) is True
    assert _live_branch(repo) == "session/ckpt-3411"


def _live_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
