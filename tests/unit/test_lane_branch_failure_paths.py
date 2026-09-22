"""Failure-path, input-validation and guard-wiring tests for lane branch identity (#3411).

Companion to ``tests/unit/test_lane_branch_identity.py``, which owns the two
happy-path two-turn regressions. This module owns the edges: what the new
``tools.lane_identity`` surface does when git fails, what the accessor does with
the degenerate values Popoto actually stores, and whether the executor still
*calls* the pieces in the order the fix depends on.

Three notes on method, because they are the reason some tests look the way they
do rather than the obvious way:

* **Fault injection, not mocks.** A few tests monkeypatch ``subprocess.run`` or
  a module-level helper to raise. There is no way to make real git time out on
  demand, and the handler under test only runs when it does. Everything that
  can be provoked with real git and a real repo is, and every assertion about
  ordinary behaviour runs against a real repository.
* **Popoto stores unset strings as ``""``**, never ``None``, and booleans as the
  strings ``"True"``/``"False"``. Assertions here are on falsiness, never
  ``is None``.
* **Two tests are structural (AST), and say so in their docstrings.** The
  executor's end-of-turn cleanup block is inline in ``_execute_agent_session``,
  a ~1700-line async coroutine with no callable seam, so the *wiring* of the
  trailing refresh cannot be asserted behaviourally from a unit test. See
  ``TestExecutorCleanupWiring`` for what that buys and what it does not.
"""

from __future__ import annotations

import ast
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.agent_session_queue import checkpoint_branch_state
from agent.branch_manager import mark_work_done
from agent.session_executor import _execute_agent_session
from agent.worktree_manager import (
    WORKTREES_DIR,
    WorktreeBranchMismatchError,
    merged_via_ancestor,
    safe_delete_branch,
    verify_worktree_branch,
)
from models.agent_session import AgentSession
from tools.lane_identity import (
    read_worktree_branch,
    refresh_lane_branch,
    resolve_lane_branch,
)

PROJECT_KEY = "test-lane-branch-failure-paths"
SLUG = "dev-3411fail"
LANE_BRANCH = f"session/{SLUG}"

EXECUTOR_SOURCE = Path(__file__).resolve().parents[2] / "agent" / "session_executor.py"


# ---------------------------------------------------------------------------
# Real-git helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    """A real git repository with one commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    return repo


def _add_lane_worktree(repo: Path) -> Path:
    """A real *linked* worktree on ``LANE_BRANCH``, as a lane actually has."""
    worktree = repo / WORKTREES_DIR / SLUG
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", LANE_BRANCH, str(worktree))
    return worktree


def _commit(worktree: Path, name: str) -> None:
    (worktree / name).write_text("work\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", f"work: {name}")


def _branch_exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", branch],
            capture_output=True,
        ).returncode
        == 0
    )


def _make_session(working_dir: Path, *, slug: str | None = SLUG, **overrides) -> AgentSession:
    fields = {
        "session_id": f"lane-fp-{slug or 'noslug'}",
        "session_type": "eng",
        "project_key": PROJECT_KEY,
        "working_dir": str(working_dir),
        "status": "running",
        "chat_id": "0",
        "message_text": "failure path fixture",
        "created_at": datetime.now(tz=UTC),
        "turn_count": 0,
        "tool_call_count": 0,
    }
    if slug:
        fields["slug"] = slug
    fields.update(overrides)
    return AgentSession.create(**fields)


@pytest.fixture
def lane_rows(redis_test_db):
    """Delete every row this module created, through the ORM, scoped by key."""
    yield
    for row in AgentSession.query.filter(project_key=PROJECT_KEY):
        row.delete()


# ---------------------------------------------------------------------------
# read_worktree_branch: the one HEAD read. It has no failure mode but None.
# ---------------------------------------------------------------------------


class TestReadWorktreeBranch:
    """Plan: "a missing path, a non-repo path, and a timeout each return None"."""

    def test_branch_is_returned_for_a_real_worktree(self, tmp_path):
        repo = _make_repo(tmp_path)
        assert read_worktree_branch(repo) == "main"

    def test_detached_head_reads_as_none_not_the_literal_head_string(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "checkout", "--detach")
        # Raw git answers the literal "HEAD" here (spike-2). Recording that as a
        # branch name is the #3411 defect, so normalisation happens at the read.
        assert read_worktree_branch(repo) is None

    def test_missing_path_returns_none(self, tmp_path):
        assert read_worktree_branch(tmp_path / "does-not-exist") is None

    def test_non_repo_path_returns_none(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert read_worktree_branch(plain) is None

    def test_subprocess_timeout_returns_none(self, tmp_path, monkeypatch):
        """A guard running at a turn boundary must not die over a slow git."""
        repo = _make_repo(tmp_path)

        def _timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)

        monkeypatch.setattr("tools.lane_identity.subprocess.run", _timeout)
        assert read_worktree_branch(repo) is None

    def test_empty_and_none_inputs_return_none(self):
        assert read_worktree_branch(None) is None
        assert read_worktree_branch("") is None
        assert read_worktree_branch("   ") is None


# ---------------------------------------------------------------------------
# resolve_lane_branch: the accessor, against the values Popoto really stores.
# ---------------------------------------------------------------------------


class TestResolveLaneBranchDegenerateInputs:
    """Plan: each degenerate record "must yield the seed or None, never a bogus name"."""

    def test_recorded_branch_wins_over_the_seed(self, tmp_path, lane_rows):
        session = _make_session(tmp_path, branch_name="feature/manual")
        assert resolve_lane_branch(session) == "feature/manual"

    def test_empty_string_branch_name_falls_back_to_the_seed(self, tmp_path, lane_rows):
        # Popoto stores an unset string field as "", not None — the whole reason
        # this ladder tests truthiness rather than `is None`.
        session = _make_session(tmp_path, branch_name="")
        assert resolve_lane_branch(session) == LANE_BRANCH

    def test_whitespace_only_branch_name_falls_back_to_the_seed(self, tmp_path, lane_rows):
        session = _make_session(tmp_path, branch_name="   ")
        assert resolve_lane_branch(session) == LANE_BRANCH

    def test_literal_head_branch_name_falls_back_to_the_seed(self, tmp_path, lane_rows):
        """A record that says "HEAD" names no branch and must never be handed on."""
        session = _make_session(tmp_path, branch_name="HEAD")
        resolved = resolve_lane_branch(session)
        assert resolved == LANE_BRANCH
        assert resolved != "HEAD"

    def test_no_slug_and_no_record_yields_the_session_id_seed_or_none(self, tmp_path, lane_rows):
        session = _make_session(tmp_path, slug=None, branch_name="")
        resolved = resolve_lane_branch(session)
        # A slug-less session may still seed from its session_id; what it must
        # never do is invent a `session/None`-shaped name.
        assert resolved is None or (resolved and "None" not in resolved)

    def test_none_session_yields_none(self):
        assert resolve_lane_branch(None) is None

    def test_accessor_never_hands_the_guard_an_empty_expectation(self, tmp_path, lane_rows):
        """The interface contract with ``verify_worktree_branch`` (plan, Empty/Invalid).

        The guard raises ``ValueError`` on a blank expectation, so a blank answer
        from the accessor would turn every lane launch into a crash.
        """
        repo = _make_repo(tmp_path)
        with pytest.raises(ValueError):
            verify_worktree_branch(repo, "")

        for bad in ("", "   ", "HEAD"):
            session = _make_session(tmp_path, branch_name=bad)
            resolved = resolve_lane_branch(session)
            assert resolved and resolved.strip(), f"blank expectation from branch_name={bad!r}"
            assert resolved != "HEAD"


# ---------------------------------------------------------------------------
# checkpoint_branch_state: the record's sole writer.
# ---------------------------------------------------------------------------


class TestCheckpointBranchState:
    def test_records_the_live_branch(self, tmp_path, lane_rows):
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        session = _make_session(worktree)

        checkpoint_branch_state(session)

        assert session.branch_name == LANE_BRANCH

    def test_detached_clears_the_record_rather_than_recording_head(self, tmp_path, lane_rows):
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")
        _git(worktree, "checkout", "--detach")
        session = _make_session(worktree, branch_name=LANE_BRANCH)

        checkpoint_branch_state(session)

        assert not (session.branch_name or "").strip()
        assert (session.branch_name or "") != "HEAD"

    def test_git_failure_leaves_a_good_record_untouched(self, tmp_path, lane_rows):
        """Plan: "a transient git error cannot erase a good record"."""
        missing = tmp_path / "vanished"
        session = _make_session(missing, branch_name=LANE_BRANCH)

        checkpoint_branch_state(session)

        assert session.branch_name == LANE_BRANCH

    def test_branch_read_only_failure_leaves_a_good_record_untouched(
        self, tmp_path, lane_rows, monkeypatch
    ):
        """Plan: a transient failure of the branch read ALONE (SHA read still
        succeeds) must not erase a good record either.

        ``test_git_failure_leaves_a_good_record_untouched`` above covers a
        missing path, which fails both the SHA read and the branch read
        together. That leaves the "SHA read succeeds, branch read alone
        fails" case untested -- the read_worktree_branch() call and the
        `git rev-parse HEAD` call inside checkpoint_branch_state are two
        independent subprocess.run invocations, and the earlier code treated
        the SHA read's success as a discriminator for the branch read's
        failure, which is false. read_worktree_branch is patched directly
        (the simpler seam) rather than faulting subprocess.run, so the SHA
        subprocess call underneath still runs for real.
        """
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")
        session = _make_session(worktree, branch_name=LANE_BRANCH)

        monkeypatch.setattr("agent.agent_session_queue.read_worktree_branch", lambda *_: None)
        monkeypatch.setattr("agent.agent_session_queue.worktree_is_detached", lambda *_: None)

        checkpoint_branch_state(session)

        assert session.branch_name == LANE_BRANCH

    def test_commit_sha_round_trips_through_the_orm(self, tmp_path, lane_rows):
        """Plan bullet, corrected: ``commit_sha`` is a property over ``session_events``.

        It is not a Popoto field, so naming it in ``update_fields`` raises
        ``ModelException: Unknown field``; ``session_events`` is what persists
        it. Asserted by reloading the row through the ORM, never raw Redis.
        """
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")
        head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        session = _make_session(worktree)

        checkpoint_branch_state(session)

        rows = [r for r in AgentSession.query.filter(project_key=PROJECT_KEY)]
        assert len(rows) == 1, f"expected one row, got {len(rows)}"
        assert rows[0].commit_sha == head, "commit_sha did not survive the save"
        assert rows[0].branch_name == LANE_BRANCH


# ---------------------------------------------------------------------------
# safe_delete_branch: every failure must preserve.
# ---------------------------------------------------------------------------


class TestSafeDeleteBranchFailsSafe:
    def test_predicate_error_preserves(self, tmp_path):
        repo = _make_repo(tmp_path)
        _git(repo, "branch", LANE_BRANCH)

        def _explode(repo_root, branch, base):
            raise RuntimeError("predicate blew up")

        result = safe_delete_branch(str(repo), LANE_BRANCH, predicate=_explode, force=False)

        assert result["deleted"] is False
        assert result["skipped_unmerged"] is True
        assert _branch_exists(repo, LANE_BRANCH)

    def test_worktree_scan_failure_preserves(self, tmp_path, monkeypatch):
        """Plan: "a scan failure must also fail safe (preserve), never delete"."""
        repo = _make_repo(tmp_path)
        _git(repo, "branch", LANE_BRANCH)

        def _explode(repo_root, branch_name):
            raise RuntimeError("worktree scan blew up")

        monkeypatch.setattr("agent.worktree_manager._scan_checked_out_worktree", _explode)

        result = safe_delete_branch(
            str(repo),
            LANE_BRANCH,
            predicate=lambda *a: True,  # landed — only the scan failure preserves it
            force=False,
        )

        assert result["deleted"] is False
        assert result["skipped_checked_out"] is True
        assert _branch_exists(repo, LANE_BRANCH), "a scan failure deleted a branch"

    def test_checked_out_branch_is_preserved_and_named_as_such(self, tmp_path):
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        assert worktree.exists()

        result = safe_delete_branch(
            str(repo),
            LANE_BRANCH,
            predicate=lambda *a: True,  # landed, so only the checkout holds it back
            force=False,
        )

        assert result["deleted"] is False
        assert result["skipped_checked_out"] is True
        assert _branch_exists(repo, LANE_BRANCH)

    def test_skipped_checked_out_key_is_present_on_every_path(self, tmp_path):
        """Callers branch on this key unconditionally, so it is never absent."""
        repo = _make_repo(tmp_path)
        _git(repo, "branch", LANE_BRANCH)

        preserved = safe_delete_branch(
            str(repo), LANE_BRANCH, predicate=lambda *a: False, force=False
        )
        assert preserved["skipped_checked_out"] is False

        deleted = safe_delete_branch(str(repo), LANE_BRANCH, predicate=lambda *a: True, force=False)
        assert deleted["skipped_checked_out"] is False
        assert deleted["deleted"] is True

    def test_force_does_not_override_the_checked_out_refusal(self, tmp_path):
        """``force`` is about merge state; it must not reach a live worktree."""
        repo = _make_repo(tmp_path)
        _add_lane_worktree(repo)

        result = safe_delete_branch(str(repo), LANE_BRANCH, predicate=lambda *a: True, force=True)

        assert result["deleted"] is False
        assert result["skipped_checked_out"] is True
        assert _branch_exists(repo, LANE_BRANCH)

    def test_nonexistent_branch_is_preserved_not_deleted(self, tmp_path, caplog):
        """Plan (Empty/Invalid): a branch that does not exist must not be "deleted".

        ``merged_via_ancestor`` cannot resolve the ref, returns ``False``
        (spike-4), and the call preserves. The plan also asks that the log
        distinguish "preserved because unmerged" from "preserved because
        nonexistent"; it does not today, which is recorded in the Task 6 report
        rather than asserted here, since fixing it is a production change.
        """
        repo = _make_repo(tmp_path)

        with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
            result = safe_delete_branch(
                str(repo), "session/never-existed", predicate=merged_via_ancestor, force=False
            )

        assert result["deleted"] is False
        assert result["skipped_unmerged"] is True
        assert any("unmerged-branch-guard" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# refresh_lane_branch: the primitive the trailing refresh depends on.
# ---------------------------------------------------------------------------


class TestRefreshLaneBranch:
    def test_record_follows_the_worktree_back_to_main_after_cleanup(self, tmp_path, lane_rows):
        """The trailing refresh's contract, stated as behaviour (plan Risk 1).

        This is the assertion that would have caught a Task 5 that shipped
        without the trailing ``refresh_lane_branch``: after ``mark_work_done``
        returns the worktree to ``main`` and ``safe_delete_branch`` has run, the
        record must name what the worktree is actually on. If it still names the
        lane branch, the next turn's #1377 guard refuses and the lane is stranded
        — the #3411 incident under a new name.

        It proves the primitive. ``TestExecutorCleanupWiring`` proves the
        executor calls it; neither is sufficient alone.
        """
        repo = _make_repo(tmp_path)
        worktree = repo / "solo"
        _git(repo, "worktree", "add", "-b", LANE_BRANCH, str(worktree))
        # Park the primary checkout off `main` so the lane worktree can take it:
        # git refuses to check out a branch another worktree already holds, and
        # the behaviour under test is the record following the worktree, not
        # git's worktree-exclusivity rule.
        _git(repo, "checkout", "-b", "parked")
        _commit(worktree, "a.txt")
        session = _make_session(worktree)
        checkpoint_branch_state(session)
        assert session.branch_name == LANE_BRANCH

        # The worktree moves back to main, as mark_work_done does for a
        # non-linked checkout and as the merge of the lane eventually does.
        _git(worktree, "checkout", "main")

        refreshed = refresh_lane_branch(session, worktree)

        assert refreshed == "main"
        rows = [r for r in AgentSession.query.filter(project_key=PROJECT_KEY)]
        assert len(rows) == 1
        assert rows[0].branch_name == "main", (
            "the record still names the lane branch after cleanup — the next "
            "turn's #1377 guard will refuse to launch (issue #3411, plan Risk 1)"
        )
        # And the next turn launches against it.
        verify_worktree_branch(worktree, resolve_lane_branch(rows[0]))

    def test_detached_worktree_refreshes_to_none_and_clears_the_record(self, tmp_path, lane_rows):
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")
        session = _make_session(worktree)
        checkpoint_branch_state(session)
        _git(worktree, "checkout", "--detach")

        assert refresh_lane_branch(session, worktree) is None
        assert not (session.branch_name or "").strip()

    def test_checkpoint_failure_is_swallowed_not_raised(self, tmp_path, lane_rows, monkeypatch):
        """A turn must not die because a diagnostic write failed."""
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        session = _make_session(worktree, branch_name=LANE_BRANCH)

        def _explode(_session):
            raise RuntimeError("redis is down")

        monkeypatch.setattr("tools.lane_identity._checkpoint_branch_state", _explode)

        # Must not raise, and must still answer with the best branch it has.
        assert refresh_lane_branch(session, worktree) == LANE_BRANCH

    def test_none_session_and_empty_path_are_inert(self, tmp_path, lane_rows):
        assert refresh_lane_branch(None, tmp_path) is None
        session = _make_session(tmp_path)
        assert refresh_lane_branch(session, "") is None
        assert refresh_lane_branch(session, None) is None


# ---------------------------------------------------------------------------
# Detached lane, end to end.
# ---------------------------------------------------------------------------


class TestDetachedLaneEndToEnd:
    def test_cleanup_is_skipped_record_is_cleared_and_next_turn_launches(self, tmp_path, lane_rows):
        """Plan (Empty/Invalid): "cleanup skipped, record cleared, next turn launches".

        Replays the executor's cleanup decision with the real primitives: the
        leading refresh answers ``None`` for a detached worktree, which is the
        signal to skip cleanup entirely rather than invent ``session/{slug}``
        and delete it.
        """
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")
        session = _make_session(worktree)
        checkpoint_branch_state(session)
        _git(worktree, "checkout", "--detach")

        # --- Turn 1 cleanup, as the executor performs it ---
        lane_branch = refresh_lane_branch(session, worktree)
        assert lane_branch is None, "a detached lane must not resolve to a branch"
        # Cleanup is skipped: nothing is marked done, nothing is deleted.

        # --- Turn 2 ---
        rows = [r for r in AgentSession.query.filter(project_key=PROJECT_KEY)]
        assert len(rows) == 1
        assert not (rows[0].branch_name or "").strip(), "record was not cleared"

        expected = resolve_lane_branch(rows[0])
        assert expected == LANE_BRANCH, "a cleared record must fall back to the seed"
        assert _branch_exists(repo, LANE_BRANCH), "the skipped cleanup deleted the lane branch"

        try:
            verify_worktree_branch(worktree, expected)
        except WorktreeBranchMismatchError as exc:
            pytest.fail(
                "second turn refused to launch after a detached turn (issue #3411): "
                f"expected={exc.expected_branch!r} actual={exc.actual_branch!r} -- {exc}"
            )

    def test_mark_work_done_no_longer_deletes_the_branch(self, tmp_path):
        """Task 5a, as behaviour: the destructive line is gone, not re-pointed.

        ``git branch -d`` judges mergedness against the *current* HEAD, not
        against ``main``, so an archive commit landing on a detached HEAD made
        the lane branch an ancestor of it and the delete succeeded. That
        asymmetry was the destructive mechanism.
        """
        repo = _make_repo(tmp_path)
        worktree = _add_lane_worktree(repo)
        _commit(worktree, "a.txt")

        mark_work_done(worktree, LANE_BRANCH)

        assert _branch_exists(repo, LANE_BRANCH), "mark_work_done deleted the lane branch"


# ---------------------------------------------------------------------------
# Executor wiring (structural).
# ---------------------------------------------------------------------------


def _executor_cleanup_calls() -> list[tuple[int, str]]:
    """Every call in ``_execute_agent_session``, as ``(lineno, dotted name)``."""
    tree = ast.parse(EXECUTOR_SOURCE.read_text())
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_agent_session"
    )
    calls: list[tuple[int, str]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.append((node.lineno, func.id))
            elif isinstance(func, ast.Attribute):
                calls.append((node.lineno, func.attr))
    return calls


class TestExecutorCleanupWiring:
    """Structural assertions on the executor's end-of-turn cleanup block.

    **These are AST tests, and that is a deliberate compromise.** The cleanup
    block is inline in ``_execute_agent_session``, a ~1700-line async coroutine
    that runs a real ``claude -p`` subprocess; there is no seam to call it
    through, so a unit test cannot observe the trailing refresh happening. What
    these assert is that the call exists and is ordered after the destructive
    one — which is what a Task 5 omission would have broken, and strictly more
    than the ``grep -c`` that guarded it before.

    They do **not** assert the refresh works; ``TestRefreshLaneBranch`` does.
    Extracting the cleanup block into a named helper would make both
    behavioural, and is recommended in the Task 6 report.
    """

    def test_trailing_refresh_is_called_after_safe_delete_branch(self):
        calls = _executor_cleanup_calls()
        deletes = [ln for ln, name in calls if name == "safe_delete_branch"]
        refreshes = [ln for ln, name in calls if name == "refresh_lane_branch"]

        assert deletes, "no safe_delete_branch call in _execute_agent_session"
        assert refreshes, "no refresh_lane_branch call in _execute_agent_session"
        assert len(refreshes) >= 2, (
            "expected a leading and a trailing refresh_lane_branch; found "
            f"{len(refreshes)} at lines {refreshes}"
        )
        assert any(r > max(deletes) for r in refreshes), (
            "no refresh_lane_branch call after the last safe_delete_branch "
            f"(deletes at {deletes}, refreshes at {refreshes}) — without it the "
            "record names a branch cleanup just deleted and the next turn's "
            "#1377 guard refuses (issue #3411, plan Risk 1)"
        )

    def test_leading_refresh_precedes_mark_work_done(self):
        calls = _executor_cleanup_calls()
        marks = [ln for ln, name in calls if name == "mark_work_done"]
        refreshes = [ln for ln, name in calls if name == "refresh_lane_branch"]

        assert marks, "no mark_work_done call in _execute_agent_session"
        assert any(r < min(marks) for r in refreshes), (
            "cleanup acts before re-reading the live branch — the destructive "
            "act would name the seed rather than the branch holding the commits"
        )

    def test_skip_log_names_the_slug_and_the_worktree_path(self):
        """Plan (Error State): an operator reading ``logs/worker.log`` must be able
        to tell *which* lane skipped cleanup and where it lives."""
        source = EXECUTOR_SOURCE.read_text()
        marker = "[lane-branch] Skipping cleanup"
        assert marker in source, "the lane-branch skip log is gone"

        start = source.index(marker)
        window = source[start : start + 400]
        assert "session.slug" in window, "the skip log does not name the lane slug"
        assert "working_dir" in window, "the skip log does not name the worktree path"


# ---------------------------------------------------------------------------
# The historical guards, exercised through the real executor.
# ---------------------------------------------------------------------------


class TestMainCheckoutGuardIsActuallyEnforced:
    """Real coverage for the #887 guard in ``_execute_agent_session``.

    ``tests/unit/test_session_isolation_bypass.py`` — the plan's named target
    for this guard's mutation proof — asserts against ``_should_block``, a
    *copy* of the guard predicate living in the test file. Deleting the guard
    from ``agent/session_executor.py`` leaves that file entirely green, so it
    proves the predicate is right, not that the guard is there. This test calls
    the executor.
    """

    @pytest.mark.asyncio
    async def test_eng_session_with_a_slug_outside_a_worktree_is_refused(
        self, tmp_path, lane_rows, caplog
    ):
        plain = tmp_path / "main-checkout"
        plain.mkdir()
        session = _make_session(
            plain, slug="lane-fp-887", session_id="lane-fp-887", status="pending"
        )
        assert WORKTREES_DIR not in str(plain)

        with caplog.at_level(logging.CRITICAL, logger="agent.session_executor"):
            with pytest.raises(RuntimeError, match="must run in an existing worktree"):
                await _execute_agent_session(session)

        guard_logs = [r for r in caplog.records if "[worktree-guard]" in r.message]
        assert guard_logs, "the guard refused without saying so in the log"
        assert any("887" in r.message for r in guard_logs)

    @pytest.mark.asyncio
    async def test_eng_session_with_a_slug_and_a_missing_worktree_is_refused(
        self, tmp_path, lane_rows
    ):
        """The #887 follow-up: a path *string* under ``.worktrees/`` is not enough."""
        ghost = tmp_path / WORKTREES_DIR / "ghost-lane"
        # Deliberately not created.
        session = _make_session(
            ghost, slug="ghost-lane", session_id="lane-fp-887-ghost", status="pending"
        )
        assert WORKTREES_DIR in str(ghost)
        assert not ghost.exists()

        with pytest.raises(RuntimeError, match="must run in an existing worktree"):
            await _execute_agent_session(session)


class TestBranchMismatchRefusalIsAttributable:
    """Plan (Error State): a refusal must be attributable, not silent.

    The user-visible symptom of #3411 was silence — a reaction emoji and no
    message. ``AgentSession`` has no ``last_error`` field (the plan's wording
    predates the model); as with the ``[executor-guard]`` tests, the structured
    error log is the durable failure record, so that is what is asserted.
    """

    @pytest.mark.asyncio
    async def test_dirty_mismatched_worktree_logs_the_refusal_with_slug_and_session(
        self, tmp_path, lane_rows, caplog, monkeypatch
    ):
        # `_execute_agent_session` pins workspaces to `~/src` and rewrites
        # anything outside it to the main checkout, which would trip the #887
        # guard long before the #1377 one under test. Neutralising that
        # unrelated validator is what lets a tmp_path worktree reach the guard;
        # writing the fixture under `~/src` instead would put test repositories
        # in a shared directory on a machine several agents work in.
        monkeypatch.setattr(
            "agent.session_executor.validate_workspace",
            lambda working_dir, allowed_root, is_worktree=False: working_dir,
        )
        slug = "lane-fp-1377"
        worktree = tmp_path / WORKTREES_DIR / slug
        worktree.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-b", "main", str(worktree)], check=True, capture_output=True
        )
        _git(worktree, "config", "user.email", "test@example.com")
        _git(worktree, "config", "user.name", "Test")
        (worktree / "README.md").write_text("seed\n")
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-m", "initial")
        # Dirty, and the record names a branch this worktree is not on.
        (worktree / "uncommitted.txt").write_text("work in progress\n")

        session = _make_session(
            worktree,
            slug=slug,
            session_id="lane-fp-1377",
            status="pending",
            branch_name="session/some-other-lane",
        )

        with caplog.at_level(logging.ERROR, logger="agent.session_executor"):
            with pytest.raises(WorktreeBranchMismatchError):
                await _execute_agent_session(session)

        refusals = [r for r in caplog.records if "[worktree-branch-guard]" in r.message]
        assert refusals, "the launch was refused with no attributable log line"
        joined = " ".join(r.message for r in refusals)
        assert slug in joined, "the refusal log does not name the lane"
        assert "session/some-other-lane" in joined, "the refusal log does not name the expectation"
