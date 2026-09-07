"""Behavioral tests for the #3176 lane-visibility fix in ``agent/session_executor.py``.

Real integration tests against the local Redis test DB (autouse
``redis_test_db`` fixture) — no mocks for the ORM/Redis layer. Only the
runner (``agent.session_runner.SessionRunner``) is faked; worktree
provisioning uses a REAL git worktree in a throwaway repo under ``tmp_path``
so the synthetic-slug cleanup's actual busy-check / removal machinery runs
for real, matching ``tests/unit/test_teammate_cold_start_finalize.py``'s
"real ORM, faked runner" shape.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.session_executor import _execute_agent_session
from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class FakeSessionRunner:
    """Records constructor kwargs; never spawns a real Claude subprocess.

    ``on_run`` (optional class attribute) lets a test make ``run()`` raise,
    simulating a harness failure — the trigger for BackgroundTask's own
    ``messenger.send(..., message_type="error")`` call.
    """

    instances: list[FakeSessionRunner] = []
    on_run = None  # optional: callable() -> None, may raise

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        type(self).instances.append(self)

    async def run(self, user_message: str):
        if type(self).on_run is not None:
            type(self).on_run()
        from agent.session_runner import RunSummary

        return RunSummary(exit_reason="pm_complete", turn_count=1)


@pytest.fixture(autouse=True)
def _reset_fake_runner():
    FakeSessionRunner.instances = []
    FakeSessionRunner.on_run = None
    yield
    FakeSessionRunner.instances = []
    FakeSessionRunner.on_run = None


def _patch_runner():
    return patch("agent.session_runner.SessionRunner", FakeSessionRunner)


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _make_eng_session(project_key: str, working_dir: str, **overrides) -> AgentSession:
    """A slugless eng session — the exact synthesis precondition (#1272):
    ``session_type="eng"``, ``slug=None``, ``agent_session_id`` present (via
    the model's ``id``/AutoKeyField, always populated by ``.create()``)."""
    defaults = dict(
        session_id=_sid("lane-vis"),
        session_type="eng",
        project_key=project_key,
        working_dir=working_dir,
        status="running",
        chat_id=f"chat-{uuid.uuid4().hex[:8]}",
        message_text="do the thing",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    defaults.update(overrides)
    return AgentSession.create(**defaults)


def _make_real_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, so ``resolve_main_repo_root``
    (``git rev-parse --git-common-dir``) and real ``git worktree`` commands
    have something genuine to operate on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731
        args, cwd=repo, check=True, capture_output=True, text=True
    )
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "test@test.invalid")
    run("git", "config", "user.name", "test")
    (repo / "README.md").write_text("x")
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "init")
    return repo


def _patch_worktree_real(repo: Path):
    """Stub ``get_or_create_worktree`` to provision a REAL git worktree
    inside ``repo`` for whatever slug the executor synthesizes, and
    ``verify_worktree_branch`` to no-op (matches
    ``tests/unit/test_session_executor_runner_dispatch.py``'s
    ``_patch_worktree`` shape, but slug-aware so ``.worktrees/{slug}``
    actually exists and is a registered worktree — required for the
    synthetic cleanup's real ``cleanup_after_merge`` -> ``remove_worktree``
    -> ``worktree_busy_check`` chain to run genuinely, not mocked)."""

    def _create(_working_dir, slug):
        wt_path = repo / ".worktrees" / slug
        subprocess.run(
            ["git", "worktree", "add", "-B", f"session/{slug}", str(wt_path)],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return str(wt_path)

    @contextmanager
    def _ctx():
        with ExitStack() as stack:
            stack.enter_context(
                patch("agent.worktree_manager.get_or_create_worktree", side_effect=_create)
            )
            stack.enter_context(
                patch("agent.worktree_manager.verify_worktree_branch", return_value=None)
            )
            yield

    return _ctx()


def _dev_slug(session: AgentSession) -> str:
    return f"dev-{session.agent_session_id[:8]}"


def _log_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# 1. Pre-spawn exec_cwd stamp
# ---------------------------------------------------------------------------


class TestPreSpawnStamp:
    @pytest.mark.asyncio
    async def test_slugless_eng_session_stamps_exec_cwd_with_lane(self, redis_test_db, tmp_path):
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-1", str(repo))

        with _patch_runner(), _patch_worktree_real(repo):
            await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        expected_slug = _dev_slug(session)
        assert reloaded.exec_cwd is not None
        assert reloaded.exec_cwd.endswith(f".worktrees/{expected_slug}")
        # slug is deliberately never written back onto the hydrated row —
        # it's a KeyField (spike-3); writing it would fork the Redis row.
        assert reloaded.slug is None
        # working_dir is deliberately never written either (spike-8) — it
        # still names what the row was created with, the main checkout.
        assert reloaded.working_dir == str(repo)

    @pytest.mark.asyncio
    async def test_live_fence_none_on_prestamp_only(self, redis_test_db, tmp_path):
        """A pre-spawn-only stamp carries no pid and no spawn_history entry,
        so it must be invisible to live_fence (Risk 5)."""
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-2", str(repo))

        with _patch_runner(), _patch_worktree_real(repo):
            await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded.exec_cwd is not None
        assert reloaded.live_fence is None


# ---------------------------------------------------------------------------
# 2. Lane-writeback failure is loud, not fatal
# ---------------------------------------------------------------------------


class TestLaneWritebackFailure:
    @pytest.mark.asyncio
    async def test_raising_save_logs_lane_writeback_warning(self, redis_test_db, tmp_path, caplog):
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-3", str(repo))

        real_save = AgentSession.save

        def _raising_save(self, *args, **kwargs):
            fields = kwargs.get("update_fields")
            if fields and "exec_cwd" in fields:
                raise RuntimeError("simulated Redis blip on exec_cwd save")
            return real_save(self, *args, **kwargs)

        with (
            _patch_runner(),
            _patch_worktree_real(repo),
            patch.object(AgentSession, "save", _raising_save),
            caplog.at_level(logging.WARNING),
        ):
            # The session must still proceed to completion — a lane-writeback
            # failure is non-fatal.
            await _execute_agent_session(session)

        assert FakeSessionRunner.instances, "session must still run despite the save failure"
        warnings = [m for m in _log_messages(caplog) if "[lane-writeback]" in m]
        assert warnings, "expected a [lane-writeback] WARNING on the failed exec_cwd save"
        assert session.session_id in warnings[0]


# ---------------------------------------------------------------------------
# 3. Terminal-row cleanup: worktree actually removed
# ---------------------------------------------------------------------------


class TestTerminalRowCleanup:
    @pytest.mark.asyncio
    async def test_normal_completion_removes_worktree(self, redis_test_db, tmp_path, caplog):
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-4", str(repo))
        slug = _dev_slug(session)

        with (
            _patch_runner(),
            _patch_worktree_real(repo),
            caplog.at_level(logging.INFO),
        ):
            await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded.status not in ("running", "pending")

        wt_path = repo / ".worktrees" / slug
        assert not wt_path.exists(), "worktree should be removed on normal completion"

        msgs = _log_messages(caplog)
        assert any("Cleaned up worktree+branch" in m for m in msgs)
        assert not any("cleanup blocked" in m for m in msgs)


# ---------------------------------------------------------------------------
# 4. Pre-finalize guard: raising exit with `task` never bound
# ---------------------------------------------------------------------------


class TestPreFinalizeGuardUnboundTask:
    @pytest.mark.asyncio
    async def test_early_raise_before_task_bound_finalizes_failed_and_removes_worktree(
        self, redis_test_db, tmp_path, caplog
    ):
        """Force the raise ahead of ``task = BackgroundTask(...)`` so
        ``locals().get("task")`` is None in the ``finally``. The reloaded
        row's status must be "failed" BY VALUE — not merely "no exception
        escaped" — since the naive repair
        (``getattr(_task, "error", None)``) would finalize "completed"."""
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-5", str(repo))
        slug = _dev_slug(session)

        with (
            _patch_runner(),
            _patch_worktree_real(repo),
            patch("agent.BackgroundTask", side_effect=RuntimeError("boom before task bound")),
            caplog.at_level(logging.INFO),
        ):
            with pytest.raises(RuntimeError, match="boom before task bound"):
                await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "failed", (
            "an early raise before `task` is bound must finalize to the honest "
            "'failed' status, not silently degrade to 'completed'"
        )

        wt_path = repo / ".worktrees" / slug
        assert not wt_path.exists(), "the pre-finalize guard must unblock cleanup's removal"

        msgs = _log_messages(caplog)
        assert any("synthetic-cleanup pre-finalize" in m or "Pre-finalize guard" in m for m in msgs)
        assert any("Cleaned up worktree+branch" in m for m in msgs)
        assert not any("cleanup blocked" in m for m in msgs)


# ---------------------------------------------------------------------------
# 5. Deferred / auto-continue exit on the main nudge path preserves the lane
# ---------------------------------------------------------------------------


class TestDeferredNudgeMainPathPreservesLane:
    @pytest.mark.asyncio
    async def test_nudge_continue_leaves_row_pending_and_lane_preserved(
        self, redis_test_db, tmp_path, caplog
    ):
        """Drive the path where the harness run fails, BackgroundTask's own
        error handler calls ``messenger.send(...)`` -> ``route_session_output``
        (forced here to "nudge_continue"), and ``_enqueue_nudge``'s MAIN path
        re-reads the row and calls ``transition_status(session, "pending",
        ...)`` on the SAME row. The pre-finalize guard must see a `pending`
        row and no-op; the synthetic cleanup must then find the row still
        non-terminal (carrying the exec_cwd stamped earlier in this same
        run) and refuse removal, preserving the lane for the continuation
        that is about to re-enter it (spike-10)."""
        repo = _make_real_repo(tmp_path)
        session = _make_eng_session("lane-vis-6", str(repo))
        slug = _dev_slug(session)

        def _raise():
            raise RuntimeError("simulated harness failure")

        FakeSessionRunner.on_run = _raise

        with (
            _patch_runner(),
            _patch_worktree_real(repo),
            patch(
                "agent.output_router.route_session_output",
                return_value=("nudge_continue", 50),
            ),
            # Avoid spinning up a real background worker loop as a side
            # effect of _enqueue_nudge's main path.
            patch("agent.session_executor._call_ensure_worker"),
            caplog.at_level(logging.WARNING),
        ):
            await _execute_agent_session(session)

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        assert reloaded.status == "pending", (
            "the main nudge path must leave the row pending, untouched by the "
            "pre-finalize guard (status == 'running' predicate must no-op here)"
        )

        wt_path = repo / ".worktrees" / slug
        assert wt_path.exists(), "the lane must be preserved for the continuation"

        msgs = _log_messages(caplog)
        assert any("[synthetic-slug]" in m and "cleanup blocked" in m for m in msgs)
