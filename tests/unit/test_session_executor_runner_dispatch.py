"""Unit tests for the session-runner dispatch path in ``_execute_agent_session``.

After the granite PTY teardown (plan #1924, task 4), the executor's dispatch
leg constructs a :class:`~agent.session_runner.SessionRunner` (over a
:class:`~agent.session_runner.SessionRunnerAdapter`) instead of the old
``BridgeAdapter`` + ``PTYPool`` + ``Container`` stack. This module covers the
wiring:

* ``_execute_agent_session`` constructs ``SessionRunner`` and awaits
  ``runner.run(<harness turn input>)`` — there is one execution transport and
  no seam (no role_transports resolution, no pm-coercion guard).
* The per-session env (SESSION_TYPE, AGENT_SESSION_ID, task-list isolation,
  parent-session linking) flows into the runner via ``session_env``.
* The four-scalar resume context (D3, spike #1928) is built from the
  AgentSession record's persisted scalars — and omitted for fresh sessions.
* ``BackgroundTask.run`` is invoked with ``send_result=False`` (the runner
  adapter publishes ``[/user]`` / ``[/complete]`` mid-loop, so the harness
  layer must not double-deliver).
* The Fix B (#1741) empty-turn-input guard fires BEFORE the runner is built.
* Reaction gating keys off the runner's exit-classification vocabulary via
  ``_is_non_clean_runner_exit``.

These are unit tests of the executor's flow (the runner is faked); the
integration test in ``tests/integration/test_runner_dispatch_e2e.py``
drives the REAL runner + adapter + role driver with a fake harness.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agent.session_executor import _execute_agent_session, _is_non_clean_runner_exit
from agent.session_runner import ResumeContext, RunSummary
from models.agent_session import AgentSession


class FakeSessionRunner:
    """Records constructor kwargs + run() messages; class-level registry.

    Patched over ``agent.session_runner.SessionRunner`` so the executor's
    call-time ``from agent.session_runner import SessionRunner`` resolves to
    this spy. ``on_run`` (optional classmethod seam) lets a test mutate the
    agent_session mid-"run" the way the real adapter's publish_exit_summary
    would (exit_reason / user_facing_routed).
    """

    instances: list[FakeSessionRunner] = []
    on_run = None  # optional: callable(fake_runner) -> None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.run_messages: list[str] = []
        type(self).instances.append(self)

    async def run(self, user_message: str) -> RunSummary:
        self.run_messages.append(user_message)
        hook = type(self).on_run
        if hook is not None:
            hook(self)
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


def _make_session(
    project_key: str = "test",
    working_dir: str | None = "/tmp",
    session_id: str | None = None,
    message_text: str = "hello runner",
) -> AgentSession:
    """Build a minimal AgentSession for the executor."""
    return AgentSession.create(
        session_id=session_id or f"exec-runner-{uuid.uuid4().hex[:12]}",
        session_type="eng",
        project_key=project_key,
        working_dir=working_dir,
        status="pending",
        chat_id="999",
        message_text=message_text,
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


def _worktree_path() -> str:
    """Return a temp path whose string includes '.worktrees' so the executor's
    worktree-path guard (``WORKTREES_DIR in str(working_dir)``) is satisfied.
    The directory is created on disk so the ``exists()`` check also passes.
    """
    import os

    base = tempfile.mkdtemp()
    wt_path = os.path.join(base, ".worktrees", "test-slot")
    os.makedirs(wt_path, exist_ok=True)
    return wt_path


def _patch_worktree():
    """Return a context manager that stubs out all worktree git operations.

    The executor allocates a synthetic slug for slugless eng sessions and then
    tries to create a real git worktree, verify its branch, and run git commands
    against it. In a test environment (running from a worktree or in a directory
    that is not a git repo), all of these fail. We mock:
    - get_or_create_worktree → returns a fake .worktrees path (satisfies
      WORKTREES_DIR and exists() guards)
    - verify_worktree_branch → no-op (satisfies branch-mismatch guard)
    """
    from contextlib import ExitStack, contextmanager

    @contextmanager
    def _ctx():
        wt_path = _worktree_path()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "agent.worktree_manager.get_or_create_worktree",
                    return_value=wt_path,
                )
            )
            stack.enter_context(
                patch(
                    "agent.worktree_manager.verify_worktree_branch",
                    return_value=None,
                )
            )
            yield

    return _ctx()


class TestExecutorRunnerWiring:
    """Wiring tests: ``_execute_agent_session`` dispatches through SessionRunner."""

    @pytest.mark.asyncio
    async def test_executor_constructs_runner_and_awaits_run(self, redis_test_db, caplog):
        """The executor constructs a SessionRunner and awaits run() with the
        constructed harness turn input."""
        session = _make_session(working_dir="/tmp")

        with _patch_runner(), _patch_worktree(), caplog.at_level(logging.INFO):
            await _execute_agent_session(session)

        assert FakeSessionRunner.instances, "SessionRunner was never constructed"
        runner = FakeSessionRunner.instances[0]
        assert runner.run_messages, "SessionRunner.run was not awaited"
        # The user message is the constructed harness turn input.
        assert "hello runner" in runner.run_messages[0]
        # working_dir is subject to worktree validation (falls back to
        # project root when outside the allowed root); the routing is what
        # matters — a non-empty string was passed.
        wd = runner.init_kwargs.get("working_dir")
        assert isinstance(wd, str) and wd

    @pytest.mark.asyncio
    async def test_runner_receives_adapter_and_session_env(self, redis_test_db):
        """The runner is constructed over a SessionRunnerAdapter and receives
        the per-session env (SESSION_TYPE for the pre_tool_use PM Bash
        restrictions — issue #1148 — and hook-attribution/task-list vars)."""
        from agent.session_runner import SessionRunnerAdapter

        session = _make_session(working_dir="/tmp")
        # Transition to "running" so the executor's status="running" lookup
        # resolves agent_session (the worker does this before dispatch).
        session.status = "running"
        session.save(update_fields=["status"])

        with _patch_runner(), _patch_worktree():
            await _execute_agent_session(session)

        runner = FakeSessionRunner.instances[0]
        assert isinstance(runner.init_kwargs.get("adapter"), SessionRunnerAdapter)
        env = runner.init_kwargs.get("session_env")
        assert isinstance(env, dict)
        assert env.get("SESSION_TYPE") == "eng"
        assert env.get("AGENT_SESSION_ID") == session.agent_session_id
        assert "CLAUDE_CODE_TASK_LIST_ID" in env
        assert env.get("VALOR_PARENT_SESSION_ID") == session.agent_session_id
        assert runner.init_kwargs.get("session_type") == "eng"
        # #2190 (Seam B2): VALOR_SESSION_ID = session.session_id, distinct from
        # the per-run hex AGENT_SESSION_ID -- the resolver's primary
        # identifier for ownerless-adopt in tools/sdlc_session_ensure.py.
        assert env.get("VALOR_SESSION_ID") == session.session_id
        assert env.get("VALOR_SESSION_ID") != env.get("AGENT_SESSION_ID")

    @pytest.mark.asyncio
    async def test_session_env_pins_valor_session_id_to_session_id(self, redis_test_db):
        """#2190 regression pin: _harness_env carries VALOR_SESSION_ID equal to
        session.session_id (the human-shaped id: tg_valor_..., sdlc-local-...,
        etc.), NOT session.agent_session_id (the per-run Popoto AutoKey hex).
        This is the exact env shape tools/sdlc_session_ensure.py's resolver
        needs to adopt a live ownerless bridge PM session instead of minting
        a duplicate sdlc-local-<N>."""
        session = _make_session(working_dir="/tmp", session_id="tg_valor_test2190_9001")
        session.status = "running"
        session.save(update_fields=["status"])

        with _patch_runner(), _patch_worktree():
            await _execute_agent_session(session)

        runner = FakeSessionRunner.instances[0]
        env = runner.init_kwargs.get("session_env")
        assert env.get("VALOR_SESSION_ID") == "tg_valor_test2190_9001"
        assert env.get("VALOR_SESSION_ID") == session.session_id
        assert env.get("AGENT_SESSION_ID") == session.agent_session_id

    @pytest.mark.asyncio
    async def test_runner_session_env_includes_sdlc_vars_for_pr_and_issue_session(
        self, redis_test_db
    ):
        """Env-parity regression test (issue #2039): a session whose
        AgentSession carries a PR URL and a tracking issue URL must have the
        corresponding SDLC_* vars injected into the runner's ``session_env`` —
        exactly as the deleted ``ValorAgent`` did via
        ``_extract_sdlc_env_vars`` (main ``agent/sdk_client.py`` ~line 1665).

        ``test_runner_receives_adapter_and_session_env`` above builds a bare
        session with no PR/issue URLs, so it can never exercise this
        conditional injection — that gap is exactly why the harness-seam
        extraction silently dropped the call site. This test fails without
        the fix (SDLC_* keys absent from ``session_env``) and passes with it.
        """
        # Deliberately leave slug/branch_name unset — a slug routes this eng
        # session through the real worktree-provisioning + main-checkout
        # guard (issue #887), which is orthogonal to what this test verifies.
        session = _make_session(working_dir="/tmp")
        session.status = "running"
        session.pr_url = "https://github.com/tomcounsell/ai/pull/2038"
        session.issue_url = "https://github.com/tomcounsell/ai/issues/2000"
        session.save(update_fields=["status", "pr_url", "issue_url"])

        with _patch_runner(), _patch_worktree():
            await _execute_agent_session(session)

        runner = FakeSessionRunner.instances[0]
        env = runner.init_kwargs.get("session_env")
        assert isinstance(env, dict)
        assert env.get("SDLC_PR_NUMBER") == "2038"
        assert env.get("SDLC_ISSUE_NUMBER") == "2000"
        assert env.get("SDLC_TRACKING_ISSUE") == "2000"
        # Non-SDLC vars from the earlier construction must still be present —
        # the SDLC injection must never clobber them.
        assert env.get("SESSION_TYPE") == "eng"
        assert env.get("AGENT_SESSION_ID") == session.agent_session_id

    @pytest.mark.asyncio
    async def test_fresh_session_passes_no_resume_context(self, redis_test_db):
        """A session with no persisted claude_session_uuid dispatches with
        resume=None (cold start; the runner primes)."""
        session = _make_session(working_dir="/tmp")
        session.status = "running"
        session.save(update_fields=["status"])

        with _patch_runner(), _patch_worktree():
            await _execute_agent_session(session)

        runner = FakeSessionRunner.instances[0]
        assert runner.init_kwargs.get("resume") is None

    @pytest.mark.asyncio
    async def test_persisted_scalars_build_resume_context(self, redis_test_db):
        """The four persisted resume scalars (D3, spike #1928) are handed to
        the runner as a ResumeContext built from the AgentSession record."""
        session = _make_session(working_dir="/tmp")
        prior_uuid = str(uuid.uuid4())
        session.claude_session_uuid = prior_uuid
        session.dev_agent_id = "agent-0f3a2b1c"
        session.runner_cwd = "/tmp/prior-cwd"
        session.claude_version = "2.1.201"
        session.status = "running"
        session.save(
            update_fields=[
                "claude_session_uuid",
                "dev_agent_id",
                "runner_cwd",
                "claude_version",
                "status",
            ]
        )

        with _patch_runner(), _patch_worktree():
            await _execute_agent_session(session)

        runner = FakeSessionRunner.instances[0]
        resume = runner.init_kwargs.get("resume")
        assert isinstance(resume, ResumeContext)
        assert resume.claude_session_id == prior_uuid
        assert resume.dev_agent_id == "agent-0f3a2b1c"
        assert resume.runner_cwd == "/tmp/prior-cwd"
        assert resume.claude_version == "2.1.201"

    def test_executor_module_has_no_granite_imports(self):
        """Inverse row (one-way mandate): the executor source carries zero
        references to the deleted PTY substrate or the transport seam."""
        import inspect

        import agent.session_executor as mod

        source = inspect.getsource(mod)
        for forbidden in (
            "granite_container",
            "PTYPool",
            "pty_pool",
            "role_transports",
            "_resolve_role_transports",
        ):
            assert forbidden not in source, (
                f"agent/session_executor.py still references {forbidden!r} — "
                "the PTY substrate / transport seam must not resurface"
            )


# ---------------------------------------------------------------------------
# Fix B guard tests (issue #1741): pre-SCOPE empty-turn-input guard
# ---------------------------------------------------------------------------


class TestExecutorGuardEmptyTurnInput:
    """Fix B (#1741): the pre-SCOPE guard fails loud on empty/None/"None" turn input.

    Context: a messageless sdlc-local session (``message_text=None``) would
    otherwise pass through the executor, build the harness turn input as
    "MESSAGE: None", and prime the PM with a phantom task — producing a silent
    no-op with no error logged (#1460).

    The guard checks ``_turn_input`` BEFORE ``build_harness_turn_input`` wraps
    it in the SCOPE header block, and BEFORE the runner is constructed. It must:
    - Set session status to "failed"
    - Log an ``[executor-guard]`` ERROR with reason ``empty_turn_input``
    - NOT construct or run a SessionRunner
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message_text",
        [None, "", "   ", "None"],
        ids=["none", "empty", "whitespace", "bare-none-string"],
    )
    async def test_empty_turn_input_triggers_guard(self, redis_test_db, caplog, message_text):
        """None / empty / whitespace / bare-'None' message_text → guard fires;
        the runner is never constructed and the session is failed."""
        session = AgentSession.create(
            session_id=f"guard-test-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text=message_text,
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        with _patch_runner(), _patch_worktree(), caplog.at_level(logging.ERROR):
            await _execute_agent_session(session)

        assert not FakeSessionRunner.instances, (
            f"SessionRunner must NOT be constructed for message_text={message_text!r}"
        )
        assert session.status == "failed", f"Expected failed, got {session.status!r}"
        guard_logs = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_logs, "Expected an [executor-guard] ERROR log"
        assert any("empty_turn_input" in r.message for r in guard_logs), (
            "Guard log must mention empty_turn_input"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message_text",
        ["hello runner", "Investigate the None return from foo()"],
        ids=["normal", "none-mid-text"],
    )
    async def test_non_empty_turn_input_passes_through(self, redis_test_db, caplog, message_text):
        """A normal message — including one containing 'None' mid-text — passes
        through to the runner; the guard does not fire."""
        session = _make_session(working_dir="/tmp", message_text=message_text)

        with _patch_runner(), _patch_worktree(), caplog.at_level(logging.ERROR):
            await _execute_agent_session(session)

        runner_ran = FakeSessionRunner.instances and FakeSessionRunner.instances[0].run_messages
        assert runner_ran, f"SessionRunner.run must be called for message_text={message_text!r}"
        guard_errors = [
            r
            for r in caplog.records
            if "[executor-guard]" in r.message and r.levelno >= logging.ERROR
        ]
        assert not guard_errors, f"Guard must NOT fire; got: {guard_errors}"

    @pytest.mark.asyncio
    async def test_last_resort_save_failure_is_logged(self, redis_test_db, caplog):
        """Issue #1959: when the empty-turn-input guard's last-resort status
        save raises, the failure is logged (no silent ``except Exception: pass``).

        Reaches the guard with an empty ``message_text``, forces
        ``finalize_session`` to raise a non-terminal error (so the executor
        falls back to a direct ``session.save``), then forces that save to
        raise as well. The previously-silent swallow must now emit a WARNING
        carrying the session id so the failure is observable in production.
        """
        session = AgentSession.create(
            session_id=f"guard-save-fail-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text="",  # empty → triggers the empty_turn_input guard
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        # Force the last-resort direct save on THIS session instance to raise.
        def _boom(*args, **kwargs):
            raise RuntimeError("redis write failed")

        session.save = _boom  # instance-level; leaves the class save intact

        with (
            _patch_runner(),
            _patch_worktree(),
            # finalize_session raising a generic Exception drops the executor
            # into the last-resort branch that calls session.save directly.
            patch(
                "models.session_lifecycle.finalize_session",
                side_effect=RuntimeError("finalize blew up"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Must not raise: the swallow keeps its continue-past-error intent.
            await _execute_agent_session(session)

        assert not FakeSessionRunner.instances, "Runner must not be constructed on the guard path"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "last-resort status save failed" in r.message
        ]
        assert warnings, (
            "Expected a WARNING when the last-resort save raises; "
            f"got records: {[r.message for r in caplog.records]}"
        )
        assert any(session.agent_session_id in r.message for r in warnings), (
            "Last-resort save warning must carry the session id for traceability"
        )


# ---------------------------------------------------------------------------
# Reaction-gating tests (runner exit-classification vocabulary)
# ---------------------------------------------------------------------------


class TestIsNonCleanRunnerExit:
    """Unit tests for the _is_non_clean_runner_exit helper."""

    @pytest.mark.parametrize(
        "exit_reason,expected",
        [
            # Clean exits → False
            ("pm_complete", False),
            ("pm_user", False),
            # pm_needs_human: runner-forwarded needs-input prompt (needs_human
            # hook edge on an unroutable turn) — distinct from pm_user, still
            # clean (issue #1922).
            ("pm_needs_human", False),
            # pm_floor_delivered: wrap-up guard delivered PM's real (prefix-less)
            # last message — a genuine delivery, not a canned fallback (#1719).
            ("pm_floor_delivered", False),
            # steer_abort: operator-requested abort; user-facing message
            # delivered before the loop breaks (#1779).
            ("steer_abort", False),
            # Non-clean exits (runner vocabulary) → True
            ("error", True),
            ("exception", True),
            ("turn_timeout", True),
            ("pm_empty_turn", True),
            ("pm_max_turns", True),
            # None (not yet set) → False
            (None, False),
        ],
    )
    def test_exit_reason_classification(self, exit_reason, expected):
        session = MagicMock()
        session.exit_reason = exit_reason
        assert _is_non_clean_runner_exit(session) is expected

    def test_missing_exit_reason_attribute(self):
        """Sessions without an exit_reason attr → False."""
        session = object()  # bare object with no attrs
        assert _is_non_clean_runner_exit(session) is False


class TestReactionGating:
    """Table-driven tests for the executor reaction-selection branch.

    The fake runner sets exit_reason / user_facing_routed on the same
    agent_session object the executor holds (mirroring what the real
    adapter's publish_exit_summary does) so the reaction branch sees them.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exit_reason,user_facing_routed,is_error",
        [
            # Clean exits → NOT REACTION_ERROR
            ("pm_complete", False, False),
            ("pm_complete", True, False),
            ("pm_user", False, False),
            # pm_needs_human: runner-forwarded needs-input prompt, clean like
            # pm_user (issue #1922).
            ("pm_needs_human", True, False),
            ("pm_floor_delivered", True, False),
            ("pm_floor_delivered", False, False),
            # Non-clean exits → REACTION_ERROR (regardless of delivery)
            ("error", False, True),
            ("error", True, True),
            ("exception", False, True),
            ("turn_timeout", False, True),
            ("pm_empty_turn", False, True),
            ("pm_max_turns", False, True),
            # None exit_reason (not set) → NOT REACTION_ERROR
            (None, False, False),
            (None, True, False),
        ],
    )
    async def test_reaction_selection(
        self,
        redis_test_db,
        exit_reason,
        user_facing_routed,
        is_error,
    ):
        """The executor selects REACTION_ERROR for non-clean runner exits and
        a non-error reaction for clean exits.

        Sentinel EmojiResult objects are injected via patched constants so the
        test is deterministic regardless of which real emoji is picked. We only
        assert REACTION_ERROR vs non-REACTION_ERROR — the SUCCESS/COMPLETE
        split depends on messenger.has_communicated(), which is not the target
        behavior here.
        """
        from tools.emoji_embedding import EmojiResult

        sentinel_success = EmojiResult(emoji="TEST_SUCCESS_SENTINEL")
        sentinel_complete = EmojiResult(emoji="TEST_COMPLETE_SENTINEL")
        sentinel_error = EmojiResult(emoji="TEST_ERROR_SENTINEL")

        session = _make_session(working_dir="/tmp")
        # Transition the session to "running" so the executor's
        # AgentSession.query.filter(status="running") lookup finds it and
        # agent_session is non-None for the reaction-gating branch.
        session.status = "running"
        session.save(update_fields=["status"])

        react_calls: list[tuple] = []

        async def _spy_react(chat_id, message_id, emoji):
            react_calls.append((chat_id, message_id, emoji))

        async def _null_send(*args, **kwargs):
            pass

        def _on_run(fake_runner: FakeSessionRunner) -> None:
            agent_session = fake_runner.init_kwargs.get("agent_session")
            if agent_session is not None:
                agent_session.exit_reason = exit_reason
                if user_facing_routed:
                    agent_session.user_facing_routed = True

        FakeSessionRunner.on_run = staticmethod(_on_run)

        with (
            _patch_runner(),
            _patch_worktree(),
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_null_send, _spy_react),
            ),
            # Inject sentinels so the emoji branch is deterministic.
            patch("agent.session_executor.REACTION_SUCCESS", sentinel_success),
            patch("agent.session_executor.REACTION_COMPLETE", sentinel_complete),
            patch("agent.session_executor.REACTION_ERROR", sentinel_error),
        ):
            await _execute_agent_session(session)

        assert react_calls, "react_cb (_spy_react) was never called"
        _, _, actual_emoji = react_calls[-1]
        actual_is_error = actual_emoji is sentinel_error
        assert actual_is_error == is_error, (
            f"exit_reason={exit_reason!r}, user_facing_routed={user_facing_routed} → "
            f"expected is_error={is_error}, got emoji={actual_emoji!r}"
        )


class TestRunnerFinalStatus:
    """Runner error exits must finalize the AgentSession as ``failed``, never
    ``completed`` (plan #1924 Success Criterion — the #1916 class).

    ``SessionRunner.run()`` never raises (errors become
    ``summary.exit_reason="error"/"exception"``), so ``task.error`` alone
    cannot gate finalization: the executor must also consult
    ``_is_non_clean_runner_exit`` when computing the terminal status.
    """

    @pytest.mark.parametrize(
        "task_error,exit_reason,expected",
        [
            (None, "error", "failed"),
            (None, "exception", "failed"),
            (None, "turn_timeout", "failed"),
            (None, "pm_max_turns", "failed"),
            (None, "pm_complete", "completed"),
            (None, "pm_needs_human", "completed"),
            (None, "steer_abort", "completed"),
            (None, None, "completed"),
            (RuntimeError("boom"), "pm_complete", "failed"),
        ],
    )
    def test_runner_final_status_helper(self, task_error, exit_reason, expected):
        from agent.session_executor import _runner_final_status

        session = MagicMock()
        session.exit_reason = exit_reason
        assert _runner_final_status(task_error, session) == expected

    def test_runner_final_status_without_agent_session(self):
        """agent_session=None (lookup race) degrades to task_error-only gating."""
        from agent.session_executor import _runner_final_status

        assert _runner_final_status(None, None) == "completed"
        assert _runner_final_status(RuntimeError("x"), None) == "failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exit_reason,expected_status",
        [
            ("error", "failed"),
            ("exception", "failed"),
            ("pm_complete", "completed"),
        ],
    )
    async def test_terminal_status_tracks_runner_exit(
        self, redis_test_db, exit_reason, expected_status
    ):
        """End-to-end through the executor: a fake runner that publishes a
        non-clean exit_reason (as the real adapter's publish_exit_summary
        does) yields a ``failed`` terminal AgentSession status."""
        session = _make_session(working_dir="/tmp")
        session.status = "running"
        session.save(update_fields=["status"])

        async def _null_send(*args, **kwargs):
            pass

        async def _null_react(*args, **kwargs):
            pass

        def _on_run(fake_runner: FakeSessionRunner) -> None:
            agent_session = fake_runner.init_kwargs.get("agent_session")
            if agent_session is not None:
                agent_session.exit_reason = exit_reason

        FakeSessionRunner.on_run = staticmethod(_on_run)

        with (
            _patch_runner(),
            _patch_worktree(),
            patch(
                "agent.agent_session_queue._resolve_callbacks",
                return_value=(_null_send, _null_react),
            ),
        ):
            await _execute_agent_session(session)

        rows = list(AgentSession.query.filter(session_id=session.session_id))
        assert rows, "AgentSession row vanished"
        rows.sort(key=lambda s: s.created_at or 0, reverse=True)
        assert rows[0].status == expected_status
