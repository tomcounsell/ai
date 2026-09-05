"""Unit tests for agent/worktree_manager.py — busy-check and process guards."""

import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.worktree_manager import (
    _fetch_live_sessions,
    _scan_worktree_sessions,
    _worktree_has_live_process,
    cleanup_after_merge,
    remove_worktree,
    worktree_busy_check,
    worktree_busy_probe,
    worktree_busy_probe_many,
)


def _make_session(
    working_dir: str | None,
    status: str,
    session_id: str = "sess-1",
    agent_session_id: str = "agt-1",
) -> SimpleNamespace:
    """Build a duck-typed AgentSession stand-in for busy-check tests."""
    return SimpleNamespace(
        working_dir=working_dir,
        status=status,
        session_id=session_id,
        agent_session_id=agent_session_id,
        project_key="test-proj",
    )


class _RaisingOnIter:
    """A ``query.filter(...)`` return value that raises when materialized.

    ``AgentSession.query.filter(...)`` returns a lazy ``QueryBuilder`` in
    production; the real failure surfaces during iteration (``list(...)``),
    not at the ``filter()`` call itself (Decision 0). A plain
    ``side_effect`` on ``.filter`` would raise too early and leave that
    failure mode untested, so these fixtures raise from ``__iter__`` instead.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __iter__(self):
        raise self._exc


class _CountingRows(list):
    """A list subclass that counts how many times it was iterated.

    Used to prove a batch fetch materializes exactly once per sweep rather
    than once per slug (Decision 0 / Risk 1b).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


class TestWorktreeBusyCheck:
    """Tests for worktree_busy_check (issue #1357)."""

    @patch("models.agent_session.AgentSession")
    def test_no_sessions_returns_none(self, mock_as):
        mock_as.query.filter.return_value = []
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_terminal_session_does_not_block(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session("/fake/repo/.worktrees/sdlc-1218", "completed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "killed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "failed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "abandoned"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "cancelled"),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_running_session_blocks(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "running",
                session_id="0_LIVE",
                agent_session_id="agt-LIVE",
            ),
        ]
        result = worktree_busy_check(Path("/fake/repo"), "sdlc-1218")
        assert result == ("0_LIVE", "agt-LIVE")

    @patch("models.agent_session.AgentSession")
    def test_subdir_match_blocks(self, mock_as):
        """working_dir below the worktree root still counts as busy."""
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218/sub/dir",
                "running",
                session_id="0_SUB",
            ),
        ]
        result = worktree_busy_check(Path("/fake/repo"), "sdlc-1218")
        assert result is not None
        assert result[0] == "0_SUB"

    @patch("models.agent_session.AgentSession")
    def test_substring_near_miss_does_not_block(self, mock_as):
        """sdlc-1218-other must NOT match sdlc-1218 (segment-aware)."""
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218-other",
                "running",
            ),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_relative_working_dir_match(self, mock_as):
        """working_dir stored as a relative path resolves against repo_root."""
        mock_as.query.filter.return_value = [
            _make_session(".worktrees/sdlc-1218", "running", session_id="0_REL"),
        ]
        # Use the actual cwd-resolvable repo root so resolve() works.
        repo_root = Path("/tmp")
        result = worktree_busy_check(repo_root, "sdlc-1218")
        # Relative paths are resolved via repo_root / wd; should match.
        assert result is not None
        assert result[0] == "0_REL"

    @patch("models.agent_session.AgentSession")
    def test_query_raises_returns_none(self, mock_as):
        """Popoto query failure fails open (returns None) and logs WARNING."""
        mock_as.query.filter.return_value = _RaisingOnIter(RuntimeError("redis down"))
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None

    @patch("models.agent_session.AgentSession")
    def test_session_with_no_working_dir_skipped(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session(None, "running"),
            _make_session("", "running"),
        ]
        assert worktree_busy_check(Path("/fake/repo"), "sdlc-1218") is None


class TestWorktreeBusyProbe:
    """Tests for worktree_busy_probe — the fail-CLOSED wrapper (issue #2517).

    These drive the real function. worktree_busy_check's tests cannot cover
    this: that wrapper maps both "clear" and "error" to None, so an assertion
    of ``is None`` passes whether the query succeeded or blew up. Asserting the
    "error" STATE here is what makes the fail-closed posture observable, and is
    the only thing that distinguishes the two wrappers.
    """

    @patch("models.agent_session.AgentSession")
    def test_query_failure_reports_error_state(self, mock_as):
        """A Redis outage must read as "error", never as "clear"."""
        mock_as.query.filter.return_value = _RaisingOnIter(RuntimeError("redis down"))
        state, detail = worktree_busy_probe(Path("/fake/repo"), "sdlc-1218")
        assert state == "error"
        assert detail.startswith("query_failed:")
        assert detail == "query_failed:RuntimeError"

    def test_model_import_failure_reports_error_state(self):
        """A broken deferred import is its own error branch, not "clear"."""
        with patch.dict(sys.modules, {"models.agent_session": None}):
            state, detail = worktree_busy_probe(Path("/fake/repo"), "sdlc-1218")
        assert state == "error"
        assert detail.startswith("model_import_failed:")

    @patch("models.agent_session.AgentSession")
    def test_live_session_in_lane_reports_busy(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "running",
                session_id="0_LIVE",
                agent_session_id="agt-LIVE",
            ),
        ]
        assert worktree_busy_probe(Path("/fake/repo"), "sdlc-1218") == ("busy", "0_LIVE")

    @patch("models.agent_session.AgentSession")
    def test_busy_falls_back_to_agent_session_id(self, mock_as):
        """An empty session_id must not degrade the detail into a falsy blank."""
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "running",
                session_id="",
                agent_session_id="agt-ONLY",
            ),
        ]
        assert worktree_busy_probe(Path("/fake/repo"), "sdlc-1218") == ("busy", "agt-ONLY")

    @patch("models.agent_session.AgentSession")
    def test_unrelated_sessions_report_clear(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session("/fake/repo/.worktrees/sdlc-9999", "running"),
            _make_session("/fake/repo/.worktrees/sdlc-1218-other", "running"),
            _make_session("/fake/repo", "running"),
        ]
        assert worktree_busy_probe(Path("/fake/repo"), "sdlc-1218") == ("clear", "")

    @patch("models.agent_session.AgentSession")
    def test_terminal_session_in_lane_reports_clear(self, mock_as):
        """The probe's own terminal filtering, observable at the probe level."""
        mock_as.query.filter.return_value = [
            _make_session("/fake/repo/.worktrees/sdlc-1218", "completed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "killed"),
            _make_session("/fake/repo/.worktrees/sdlc-1218", "failed"),
        ]
        assert worktree_busy_probe(Path("/fake/repo"), "sdlc-1218") == ("clear", "")

    @patch("models.agent_session.AgentSession")
    def test_no_sessions_reports_clear(self, mock_as):
        mock_as.query.filter.return_value = []
        assert worktree_busy_probe(Path("/fake/repo"), "sdlc-1218") == ("clear", "")


class TestScanWorktreeSessions:
    """Direct 3-tuple contract of the shared scan behind both wrappers."""

    @patch("models.agent_session.AgentSession")
    def test_query_failure_tuple(self, mock_as):
        mock_as.query.filter.return_value = _RaisingOnIter(ValueError("boom"))
        assert _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218") == (
            "error",
            "query_failed:ValueError",
            "",
        )

    def test_model_import_failure_tuple(self):
        with patch.dict(sys.modules, {"models.agent_session": None}):
            state, detail, extra = _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218")
        assert state == "error"
        assert detail.startswith("model_import_failed:")
        assert extra == ""

    @patch("models.agent_session.AgentSession")
    def test_busy_tuple_carries_both_ids(self, mock_as):
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218/sub",
                "running",
                session_id="0_S",
                agent_session_id="agt-S",
            ),
        ]
        assert _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218") == (
            "busy",
            "0_S",
            "agt-S",
        )

    @patch("models.agent_session.AgentSession")
    def test_clear_tuple(self, mock_as):
        mock_as.query.filter.return_value = []
        assert _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218") == ("clear", "", "")

    def test_unknown_status_value_reads_busy(self):
        """A status outside ALL_STATUSES must stay fail-closed (Risk 2, spike-4).

        It is non-terminal under the surviving Python check but absent from
        the index union that produced ``sessions``, so this exercises the
        Python `` not in TERMINAL_STATUSES`` check directly rather than the
        index — the row is handed in via ``sessions=`` as if the index had
        already (correctly, for a real deployment) returned it.
        """
        rows = [
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "some_future_status",
                session_id="0_UNKNOWN",
            ),
        ]
        assert _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218", sessions=rows) == (
            "busy",
            "0_UNKNOWN",
            "agt-1",
        )

    def test_raising_row_is_skipped_and_later_busy_row_still_wins(self):
        """A row that raises on attribute access is skipped, not fatal.

        Covers the per-row ``except Exception`` / ``continue`` branch, which
        had zero test coverage before this change. ``MagicMock(spec=["status"])``
        has a ``status`` attribute but no ``working_dir``, so
        ``getattr(session, "working_dir", None)`` does not raise -- the
        matcher must be exercised with a row that raises on the attribute it
        *does* try to read.
        """

        class _BoomOnWorkingDir:
            @property
            def working_dir(self):
                raise RuntimeError("attribute access exploded")

        rows = [
            _BoomOnWorkingDir(),
            _make_session(
                "/fake/repo/.worktrees/sdlc-1218",
                "running",
                session_id="0_LATER",
            ),
        ]
        assert _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218", sessions=rows) == (
            "busy",
            "0_LATER",
            "agt-1",
        )

    def test_injected_sessions_skips_fetch(self):
        """``sessions=`` bypasses ``_fetch_live_sessions`` entirely.

        A model-import failure that would normally produce
        ``model_import_failed:`` must not surface when rows are injected --
        the injected path never touches the deferred import that fetching
        would use.
        """
        rows = [
            _make_session("/fake/repo/.worktrees/sdlc-1218", "running", session_id="0_INJ"),
        ]
        with patch.dict(sys.modules, {"models.agent_session": None}):
            result = _scan_worktree_sessions(Path("/fake/repo"), "sdlc-1218", sessions=rows)
        assert result == ("busy", "0_INJ", "agt-1")


class TestFetchLiveSessions:
    """Tests for ``_fetch_live_sessions`` — the single Redis touch point."""

    @patch("models.agent_session.AgentSession")
    def test_materializes_exactly_once(self, mock_as):
        """``list(...)`` must consume the query object exactly once (Decision 0)."""
        rows = _CountingRows(
            [_make_session("/fake/repo/.worktrees/sdlc-1218", "running")],
        )
        mock_as.query.filter.return_value = rows
        fetched, error_reason = _fetch_live_sessions()
        assert error_reason == ""
        assert len(fetched) == 1
        assert rows.iterations == 1

    @patch("models.agent_session.AgentSession")
    def test_query_failure_on_iteration(self, mock_as):
        mock_as.query.filter.return_value = _RaisingOnIter(RuntimeError("redis down"))
        rows, error_reason = _fetch_live_sessions()
        assert rows == []
        assert error_reason == "query_failed:RuntimeError"

    def test_model_import_failure(self):
        with patch.dict(sys.modules, {"models.agent_session": None}):
            rows, error_reason = _fetch_live_sessions()
        assert rows == []
        assert error_reason.startswith("model_import_failed:")

    @patch("models.agent_session.AgentSession")
    def test_filters_on_non_terminal_status(self, mock_as):
        """The query is built from ``status__in=NON_TERMINAL_STATUSES``."""
        mock_as.query.filter.return_value = []
        _fetch_live_sessions()
        assert mock_as.query.filter.call_count == 1
        _, kwargs = mock_as.query.filter.call_args
        assert "status__in" in kwargs


class TestWorktreeBusyProbeMany:
    """Tests for ``worktree_busy_probe_many`` — the batch probe (Task 1)."""

    def test_empty_slugs_returns_empty_and_queries_nothing(self):
        with patch("agent.worktree_manager._fetch_live_sessions") as mock_fetch:
            result = worktree_busy_probe_many(Path("/fake/repo"), [])
        assert result == {}
        mock_fetch.assert_not_called()

    @patch("models.agent_session.AgentSession")
    def test_agrees_with_single_slug_probe_across_states(self, mock_as):
        """One rows object, two slugs, two different verdicts."""
        mock_as.query.filter.return_value = [
            _make_session(
                "/fake/repo/.worktrees/lane-b",
                "running",
                session_id="0_B",
                agent_session_id="agt-B",
            ),
        ]
        result = worktree_busy_probe_many(Path("/fake/repo"), ["lane-a", "lane-b"])
        assert result == {
            "lane-a": ("clear", ""),
            "lane-b": ("busy", "0_B"),
        }
        # Same fixture, driven through the single-slug wrapper, must agree.
        assert worktree_busy_probe(Path("/fake/repo"), "lane-a") == ("clear", "")
        assert worktree_busy_probe(Path("/fake/repo"), "lane-b") == ("busy", "0_B")

    @patch("models.agent_session.AgentSession")
    def test_fetch_error_fans_out_to_every_slug(self, mock_as):
        """A Redis outage must not default any requested slug to clear."""
        mock_as.query.filter.return_value = _RaisingOnIter(RuntimeError("redis down"))
        result = worktree_busy_probe_many(Path("/fake/repo"), ["lane-a", "lane-b"])
        assert result == {
            "lane-a": ("error", "query_failed:RuntimeError"),
            "lane-b": ("error", "query_failed:RuntimeError"),
        }

    @patch("models.agent_session.AgentSession")
    def test_never_raises_on_fetch_failure(self, mock_as):
        """Decision 6: the batch probe itself must not propagate."""
        mock_as.query.filter.side_effect = RuntimeError("boom before even building a builder")
        result = worktree_busy_probe_many(Path("/fake/repo"), ["lane-a"])
        assert result == {"lane-a": ("error", "query_failed:RuntimeError")}

    @patch("models.agent_session.AgentSession")
    def test_one_rows_object_serves_many_slugs_with_one_materialization(self, mock_as):
        rows = _CountingRows(
            [_make_session("/fake/repo/.worktrees/lane-a", "running", session_id="0_A")]
        )
        mock_as.query.filter.return_value = rows
        result = worktree_busy_probe_many(
            Path("/fake/repo"), ["lane-a", "lane-b", "lane-c", "lane-d"]
        )
        assert result["lane-a"] == ("busy", "0_A")
        assert result["lane-b"] == ("clear", "")
        assert result["lane-c"] == ("clear", "")
        assert result["lane-d"] == ("clear", "")
        assert rows.iterations == 1


class TestRemoveWorktreeBusyGuard:
    """Tests for remove_worktree's refuse-busy guard (issue #1357)."""

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_clear_path_returns_true(self, mock_run, mock_busy):
        """No live session: remove proceeds and returns True."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result is True

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_blocked_returns_tuple(self, mock_run, mock_busy):
        """Live session: returns ('blocked', session_id) and skips git."""
        mock_busy.return_value = ("0_LIVE", "agt-LIVE")
        result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result == ("blocked", "0_LIVE")
        # git worktree remove must NOT be called when blocked
        for call in mock_run.call_args_list:
            assert "remove" not in str(call) or "branch" not in str(call) or True
        # Stronger: the busy guard fires BEFORE the worktree_dir.exists() check,
        # so no subprocess invocations should have happened.
        assert mock_run.call_count == 0

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_force_overrides_busy_guard(self, mock_run, mock_busy, caplog):
        """force=True logs WARNING and proceeds."""
        import logging

        mock_busy.return_value = ("0_LIVE", "agt-LIVE")
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = remove_worktree(Path("/fake/repo"), "sdlc-1218", force=True)
        assert result is True
        # Ensure the force WARNING fired
        assert any("force-removing" in rec.message for rec in caplog.records)

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_busy_check_failure_treated_as_clear(self, mock_run, mock_busy):
        """If the busy helper returns None (fail-open path), removal proceeds."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        with patch.object(Path, "exists", return_value=True):
            result = remove_worktree(Path("/fake/repo"), "sdlc-1218")
        assert result is True


class TestWorktreeHasLiveProcess:
    """Tests for _worktree_has_live_process (issue #2305 defect 3)."""

    def test_detects_live_process_rooted_in_worktree(self, tmp_path):
        """A real child process with cwd inside the worktree is found."""
        proc = subprocess.Popen(["sleep", "30"], cwd=str(tmp_path))
        try:
            deadline = time.monotonic() + 5
            pid = None
            while time.monotonic() < deadline:
                pid = _worktree_has_live_process(tmp_path)
                if pid is not None:
                    break
            assert pid == proc.pid
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_no_match_when_no_process_present(self, tmp_path):
        """A directory nothing has ever chdir'd into returns None."""
        empty_dir = tmp_path / "definitely-empty"
        empty_dir.mkdir()
        assert _worktree_has_live_process(empty_dir) is None

    def test_segment_aware_containment_no_false_match(self, tmp_path):
        """sdlc-1218-other must not match a scan for sdlc-1218 (Risk 5)."""
        near_miss_dir = tmp_path / "sdlc-1218-other"
        near_miss_dir.mkdir()
        target_dir = tmp_path / "sdlc-1218"
        target_dir.mkdir()
        proc = subprocess.Popen(["sleep", "30"], cwd=str(near_miss_dir))
        try:
            # Give the child a moment to actually be scheduled/alive.
            time.sleep(0.2)
            assert _worktree_has_live_process(target_dir) is None
        finally:
            proc.kill()
            proc.wait(timeout=5)


class TestRemoveWorktreeProcessGuard:
    """Tests for remove_worktree's process-rooted-in-worktree guard (defect 3)."""

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_live_foreign_process_blocks(self, mock_run, mock_busy, tmp_path):
        """A foreign process (no AgentSession row) with cwd in the worktree blocks removal."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        worktree_dir = tmp_path / ".worktrees" / "sdlc-9999"
        worktree_dir.mkdir(parents=True)
        proc = subprocess.Popen(["sleep", "30"], cwd=str(worktree_dir))
        try:
            deadline = time.monotonic() + 5
            result = None
            while time.monotonic() < deadline:
                result = remove_worktree(tmp_path, "sdlc-9999")
                if result == ("blocked", f"pid:{proc.pid}"):
                    break
            assert result == ("blocked", f"pid:{proc.pid}")
            # Blocked before any git subprocess call.
            assert mock_run.call_count == 0
        finally:
            proc.kill()
            proc.wait(timeout=5)

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_no_live_process_proceeds(self, mock_run, mock_busy, tmp_path):
        """No live process rooted in the worktree: removal proceeds."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        worktree_dir = tmp_path / ".worktrees" / "sdlc-9998"
        worktree_dir.mkdir(parents=True)
        result = remove_worktree(tmp_path, "sdlc-9998")
        assert result is True

    @patch("agent.worktree_manager.worktree_busy_check")
    @patch("agent.worktree_manager.subprocess.run")
    def test_force_overrides_process_guard(self, mock_run, mock_busy, caplog, tmp_path):
        """force=True logs WARNING and proceeds despite the live process."""
        mock_busy.return_value = None
        mock_run.return_value = MagicMock(returncode=0)
        worktree_dir = tmp_path / ".worktrees" / "sdlc-9997"
        worktree_dir.mkdir(parents=True)
        proc = subprocess.Popen(["sleep", "30"], cwd=str(worktree_dir))
        try:
            time.sleep(0.2)
            with caplog.at_level(logging.WARNING, logger="agent.worktree_manager"):
                result = remove_worktree(tmp_path, "sdlc-9997", force=True)
            assert result is True
            assert any("force-removing" in rec.message for rec in caplog.records)
        finally:
            proc.kill()
            proc.wait(timeout=5)


class TestCleanupAfterMergeBusyBlock:
    """Tests for cleanup_after_merge surfacing the busy block (issue #1357)."""

    @patch("agent.worktree_manager.subprocess.run")
    @patch("agent.worktree_manager._branch_exists")
    @patch("agent.worktree_manager.remove_worktree")
    def test_blocked_by_live_session(self, mock_remove_wt, mock_branch_exists, mock_run):
        """When remove_worktree returns ('blocked', sid), result reflects it."""
        repo = Path("/fake/repo")
        slug = "sdlc-1218"

        with patch.object(Path, "exists", return_value=True):
            mock_remove_wt.return_value = ("blocked", "0_LIVE")
            mock_branch_exists.return_value = False
            mock_run.return_value = MagicMock(returncode=0)

            result = cleanup_after_merge(repo, slug)

        assert result["worktree_removed"] is False
        assert result["blocked_by_session"] == "0_LIVE"
        # Block is recorded as an error (so post_merge_cleanup.py can decide
        # to emit the distinct exit-2 path).
        assert any("blocked: worktree in use" in e for e in result["errors"])
        assert result["already_clean"] is False
