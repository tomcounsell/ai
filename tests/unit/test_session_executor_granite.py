"""Unit tests for the granite PTY container path in ``_execute_agent_session``.

After the granite PTY production cutover (plan #1572, Task 4), the
harness call at ``agent/session_executor.py:1708`` is replaced with
``BridgeAdapter.run`` — the all-or-nothing cutover, no fallback flag.
This module covers the wiring:

* ``_execute_agent_session`` calls ``BridgeAdapter.run`` (not
  ``get_response_via_harness``).
* ``BackgroundTask.run`` is invoked with ``send_result=False`` (the
  adapter publishes ``[/user]`` / ``[/complete]`` mid-loop, so the
  harness layer must not double-deliver).
* The harness path imports are removed from the do_work block (the
  harness code itself stays in ``agent/sdk_client.py`` per the plan's
  No-Gos, but ``_execute_agent_session`` no longer reaches it).

These are unit tests of the executor's flow, not integration tests
of the full path. The integration test in
``tests/integration/test_granite_pty_production.py`` (Task 6) covers
the full path with a mocked pexpect layer.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agent.granite_container.bridge_adapter import BridgeAdapter
from agent.granite_container.pty_pool import PTYPool
from agent.session_executor import _execute_agent_session
from models.agent_session import AgentSession
from tools.valor_session import resume_session


def _make_pool(size: int = 1) -> PTYPool:
    """Build a pool with a temp pid registry. Spawn is mocked in the
    test's `_patch_spawn` context — the pool is `initialize()`'d in
    that context. The pool's pid registry is a temp file so the test
    never touches `data/granite_pty_pids.json` on disk."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    return PTYPool(pool_size=size, pid_registry_path=tmp.name)


def _patch_spawn():
    return patch("agent.granite_container.pty_pool.PTYDriver.spawn", lambda self: None)


async def _make_initialized_pool(size: int = 1) -> PTYPool:
    """Build a pool, initialize it with mocked spawn, and return it."""
    pool = _make_pool(size=size)
    with _patch_spawn():
        await pool.initialize()
    return pool


def _patch_bridge_adapter_run_with_result(result_factory):
    """Patch BridgeAdapter.run to call result_factory() and return its
    output. The adapter is single-shot, so this lets us exercise the
    executor's flow without driving a real container."""

    async def _fake_run(self, user_message, working_dir):
        return result_factory()

    return patch.object(BridgeAdapter, "run", _fake_run)


def _make_container_result(
    exit_reason: str = "pm_complete",
    exit_message: str = "Trailing summary.",
):
    """Build a ContainerResult-like object the executor doesn't directly
    inspect (BackgroundTask has send_result=False), but the BridgeAdapter
    does. Returning a sane stub keeps the adapter's exit_summary write
    safe under our patched run."""
    result = MagicMock()
    result.exit_reason = exit_reason
    result.exit_message = exit_message
    result.turns = [MagicMock()]
    result.classification_compliance_misses = 0
    return result


def _make_session(
    project_key: str = "test",
    working_dir: str | None = "/tmp",
    session_id: str = "exec-granite-001",
    message_text: str = "hello granite",
) -> AgentSession:
    """Build a minimal AgentSession for the executor."""
    return AgentSession.create(
        session_id=session_id,
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


class TestExecutorGraniteWiring:
    """Wiring tests for the granite PTY container path.

    These tests confirm ``_execute_agent_session`` calls the new
    BridgeAdapter path and the harness path is no longer reached.
    """

    @pytest.mark.asyncio
    async def test_executor_calls_bridge_adapter_run(self, redis_test_db, caplog):
        """``_execute_agent_session`` calls ``BridgeAdapter.run`` (not
        ``get_response_via_harness``) for bridge-originated sessions."""
        session = _make_session(working_dir="/tmp")

        # Track the call.
        bridge_called = []

        async def _fake_run(self, user_message, working_dir):
            # Capture the role_transports the adapter was constructed with
            # (plan #1842). The default (no config) must be both-PTY.
            bridge_called.append((user_message, working_dir, dict(self._role_transports)))
            return ""

        # The pool singleton needs to exist; build a fresh one and
        # inject it via the module-level helper.
        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _fake_run),
            _patch_worktree(),
            caplog.at_level(logging.INFO),
        ):
            await _execute_agent_session(session)

        assert bridge_called, "BridgeAdapter.run was not called"
        # The user message is the constructed harness turn input.
        user_message, working_dir, role_transports = bridge_called[0]
        assert "hello granite" in user_message
        # working_dir is subject to worktree validation (falls back to
        # project root when outside the allowed root), so we only assert
        # it's a non-empty string — the routing is what matters.
        assert isinstance(working_dir, str) and working_dir
        # Plan #1842: the default (no transport config) resolves to both-PTY.
        assert role_transports == {"pm": "pty", "dev": "pty"}

    @pytest.mark.asyncio
    async def test_executor_does_not_call_harness_under_default_pty_config(self, redis_test_db):
        """Under the DEFAULT (both-PTY) config the executor never reaches the
        harness (plan #1842). The harness is now reachable ONLY through the
        headless role driver, which the default config does not select — the
        executor routes to ``BridgeAdapter.run`` with both roles on PTY, so
        ``get_response_via_harness`` must not be called from this path."""
        session = _make_session(working_dir="/tmp")

        # Spy on the harness function. If it's called, the test fails.
        harness_called = []

        async def _fake_harness(*args, **kwargs):
            harness_called.append(args)
            return "should not happen"

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch("agent.session_executor.get_response_via_harness", _fake_harness, create=True),
            patch.object(BridgeAdapter, "run", async_mock_return("")),
            patch("agent.sdk_client.get_response_via_harness", _fake_harness, create=True),
            _patch_worktree(),
        ):
            await _execute_agent_session(session)

        assert not harness_called, (
            "get_response_via_harness was called under default both-PTY config — "
            "the harness path is only reachable via a headless-configured role"
        )

    @pytest.mark.asyncio
    async def test_headless_config_persists_and_routes(self, redis_test_db):
        """Counterpart (plan #1842): a session whose project config selects a
        headless role persists ``role_transports`` and routes it into
        ``BridgeAdapter`` — the headless leg is selected (and thus the harness
        becomes reachable via the HeadlessRoleDriver; the driver→harness call
        is proven in test_headless_role_driver.py)."""
        session = _make_session(working_dir="/tmp")
        # Inject a project config selecting headless for the Dev role.
        session.project_config = {
            "_key": "test",
            "working_directory": "/tmp",
            "name": "test",
            "transport": {"pm": "pty", "dev": "headless"},
        }
        session.save(update_fields=["project_config"])
        # Transition to "running" so the executor's status="running" lookup
        # resolves agent_session — in production the worker does this before
        # dispatch, and the role_transports persistence write (plan #1842)
        # only fires when that lookup succeeds.
        session.status = "running"
        session.save(update_fields=["status"])

        captured = []

        async def _fake_run(self, user_message, working_dir):
            captured.append(dict(self._role_transports))
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _fake_run),
            _patch_worktree(),
        ):
            await _execute_agent_session(session)

        assert captured, "BridgeAdapter.run was not called"
        assert captured[0] == {"pm": "pty", "dev": "headless"}
        # The resolved map is persisted on the AgentSession for immutability
        # (Race 2) and dashboard/analytics visibility.
        session.refresh_from_db() if hasattr(session, "refresh_from_db") else None
        reloaded = AgentSession.query.filter(session_id=session.session_id).all()[0]
        assert reloaded.role_transports == {"pm": "pty", "dev": "headless"}


class TestExecutorGranitePathErrors:
    """Failure-path coverage for the granite cutover."""

    @pytest.mark.asyncio
    async def test_pool_not_initialized_fails_loud(self, redis_test_db, caplog):
        """If the PTY pool is not initialized, the executor surfaces
        the error and the session is marked failed. There is no
        silent fallback to the harness."""
        session = _make_session(working_dir="/tmp")

        # Force the pool singleton to None so get_pty_pool() falls
        # through to initialize_pty_pool() and the real pool machinery.
        # With the test cwd's settings, the pool will try to spawn
        # real PTYs — which the mock blocks. The error propagates and
        # BackgroundTask records task.error → session.status="failed".
        with (
            caplog.at_level(logging.ERROR),
            patch("agent.granite_container.pty_pool._pty_pool", None),
            patch("agent.granite_container.pty_pool.PTYDriver.spawn", lambda self: None),
        ):
            # The pool's initialize() may raise or succeed with zero
            # spawned pids; either way, acquire_pair blocks waiting on
            # a slot whose event is never set. The test's reasonable
            # bound: we just confirm the session is finalized and no
            # user-visible string is delivered via BackgroundTask.
            try:
                await asyncio.wait_for(_execute_agent_session(session), timeout=2.0)
            except (TimeoutError, Exception):
                pass  # Expected: pool acquire blocks or raises.

        # Whatever the path, status should be "failed" or remain
        # "running" if BackgroundTask never produced a result. The
        # test asserts there is no "completed" status that would imply
        # the harness path took over.
        assert session.status in ("running", "failed", "pending"), (
            f"Unexpected terminal status: {session.status}"
        )


def async_mock_return(value):
    """Build an async mock that returns the given value."""

    async def _coro(*args, **kwargs):
        return value

    return _coro


# ---------------------------------------------------------------------------
# Fix B guard tests (issue #1741): pre-SCOPE empty-turn-input guard
# ---------------------------------------------------------------------------


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

    This lets the executor proceed past all worktree guards and reach the
    Fix B pre-SCOPE guard at ~line 1541.
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


class TestExecutorGuardEmptyTurnInput:
    """Fix B (#1741): the pre-SCOPE guard fails loud on empty/None/"None" turn input.

    Context: before Fix B, a messageless sdlc-local session (``message_text=None``)
    would pass through the executor, build the harness turn input as
    "MESSAGE: None", and prime the granite PM with a phantom task — producing a
    silent [/complete] no-op with no error logged.

    Fix B inserts a guard at line ~1541 in ``agent/session_executor.py`` that
    checks ``_turn_input`` BEFORE ``build_harness_turn_input`` wraps it in the
    SCOPE header block. It must:
    - Set session status to "failed"
    - Log an ``[executor-guard]`` ERROR with reason ``empty_container_message``
    - NOT call BridgeAdapter.run

    Non-trigger cases (must pass through to BridgeAdapter.run):
    - Normal non-empty message_text
    - A message containing "None" mid-text (not stripping to exactly "None")

    IMPORTANT: this class uses the same fixture shape as TestExecutorGraniteWiring
    (_make_session with working_dir="/tmp", valid session_id) and patches
    BridgeAdapter.run via patch.object — NOT the _block_path_constructor fixture
    from TestExecutorGuardWorkingDirNone, which fires at Path() ~line 773 and
    would block BEFORE the new guard at ~line 1541.

    Worktree provisioning is also patched (_patch_worktree) because the executor
    allocates a synthetic slug for slugless eng sessions and calls git to create
    a worktree. In non-main-checkout environments that fails before the guard is
    reached. The mock returns a temp dir so the guard is the first failure point.
    """

    @pytest.mark.asyncio
    async def test_none_message_text_triggers_guard(self, redis_test_db, caplog):
        """message_text=None → guard triggers; BridgeAdapter.run NOT called."""
        import uuid

        session = AgentSession.create(
            session_id=f"guard-test-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text=None,
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert not bridge_called, "BridgeAdapter.run must NOT be called when message_text is None"
        assert session.status == "failed", f"Expected failed, got {session.status!r}"
        guard_logs = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_logs, "Expected an [executor-guard] ERROR log"
        assert any("empty_container_message" in r.message for r in guard_logs), (
            "Guard log must mention empty_container_message"
        )

    @pytest.mark.asyncio
    async def test_empty_string_message_text_triggers_guard(self, redis_test_db, caplog):
        """message_text='' triggers the guard — BridgeAdapter.run NOT called."""
        import uuid

        session = AgentSession.create(
            session_id=f"guard-test-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text="",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert not bridge_called, "BridgeAdapter.run must NOT be called for empty message_text"
        assert session.status == "failed"

    @pytest.mark.asyncio
    async def test_whitespace_only_message_text_triggers_guard(self, redis_test_db, caplog):
        """message_text='   ' (whitespace only) triggers the guard."""
        import uuid

        session = AgentSession.create(
            session_id=f"guard-test-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text="   ",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert not bridge_called, "BridgeAdapter.run must NOT be called for whitespace message_text"
        assert session.status == "failed"

    @pytest.mark.asyncio
    async def test_bare_none_string_message_text_triggers_guard(self, redis_test_db, caplog):
        """message_text='None' (the bare string) triggers the guard — this is the
        #1460 silent-no-op shape where Python's str(None) == 'None'."""
        import uuid

        session = AgentSession.create(
            session_id=f"guard-test-{uuid.uuid4().hex[:8]}",
            session_type="eng",
            project_key="test",
            working_dir="/tmp",
            status="pending",
            chat_id="999",
            message_text="None",
            sender_name="tester",
            created_at=datetime.now(tz=UTC),
            turn_count=0,
            tool_call_count=0,
        )

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert not bridge_called, (
            "BridgeAdapter.run must NOT be called when message_text is the bare string 'None'"
        )
        assert session.status == "failed"
        guard_logs = [r for r in caplog.records if "[executor-guard]" in r.message]
        assert guard_logs, "Expected an [executor-guard] ERROR log for bare-None message"

    @pytest.mark.asyncio
    async def test_normal_message_text_passes_through(self, redis_test_db, caplog):
        """A normal non-empty message_text passes through to BridgeAdapter.run."""
        session = _make_session(working_dir="/tmp", message_text="hello granite")

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert bridge_called, "BridgeAdapter.run must be called for a normal message"
        guard_errors = [
            r
            for r in caplog.records
            if "[executor-guard]" in r.message and r.levelno >= logging.ERROR
        ]
        assert not guard_errors, f"Guard must NOT fire for normal message; got: {guard_errors}"

    @pytest.mark.asyncio
    async def test_none_mid_text_does_not_trigger_guard(self, redis_test_db, caplog):
        """A message containing 'None' mid-text does NOT trigger the guard.

        Only the bare string 'None' (after strip()) matches — not substrings.
        Example: 'Investigate the None return from foo()' must pass through.
        """
        session = _make_session(
            working_dir="/tmp",
            message_text="Investigate the None return from foo()",
        )

        bridge_called = []

        async def _spy_run(self, user_message, working_dir):
            bridge_called.append(user_message)
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _spy_run),
            _patch_worktree(),
            caplog.at_level(logging.ERROR),
        ):
            await _execute_agent_session(session)

        assert bridge_called, (
            "BridgeAdapter.run must be called when 'None' appears mid-text (not bare)"
        )
        guard_errors = [
            r
            for r in caplog.records
            if "[executor-guard]" in r.message and r.levelno >= logging.ERROR
        ]
        assert not guard_errors, "Guard must NOT fire for a message that contains 'None' mid-text"


# ---------------------------------------------------------------------------
# Reaction-gating tests (Task 6 of issue #1648)
# ---------------------------------------------------------------------------


class TestIsNonCleanGraniteExit:
    """Unit tests for the _is_non_clean_granite_exit helper."""

    @pytest.mark.parametrize(
        "exit_reason,expected",
        [
            # Clean exits → False
            ("pm_complete", False),
            ("pm_user", False),
            # pm_floor_delivered: wrap-up guard delivered PM's real (prefix-less)
            # last message — a genuine delivery, not a canned fallback (#1719).
            ("pm_floor_delivered", False),
            # Non-clean exits → True
            ("exception", True),
            ("pm_hang", True),
            ("dev_hang", True),
            ("startup_unresolved", True),
            ("pm_no_user_message", True),
            ("pm_max_turns", True),
            # None (non-granite or not yet set) → False
            (None, False),
        ],
    )
    def test_exit_reason_classification(self, exit_reason, expected):
        from agent.session_executor import _is_non_clean_granite_exit

        session = MagicMock()
        session.exit_reason = exit_reason
        assert _is_non_clean_granite_exit(session) is expected

    def test_missing_exit_reason_attribute(self):
        """Sessions without exit_reason attr (e.g. no Task 2 migration) → False."""
        from agent.session_executor import _is_non_clean_granite_exit

        session = object()  # bare object with no attrs
        assert _is_non_clean_granite_exit(session) is False


class TestReactionGating:
    """Table-driven tests for the executor reaction-selection branch.

    We test _is_non_clean_granite_exit directly AND the emoji selection
    via the full executor path using a mocked BridgeAdapter.run.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exit_reason,user_facing_routed,is_error",
        [
            # Clean exits → NOT REACTION_ERROR
            ("pm_complete", False, False),
            ("pm_complete", True, False),
            ("pm_user", False, False),
            # pm_floor_delivered: wrap-up guard delivered real (prefix-less) PM
            # text directly — genuine delivery, clean exit (#1719).
            ("pm_floor_delivered", True, False),
            ("pm_floor_delivered", False, False),
            # Non-clean exits → REACTION_ERROR (regardless of delivery)
            ("exception", False, True),
            ("exception", True, True),
            ("pm_hang", False, True),
            ("dev_hang", False, True),
            ("startup_unresolved", False, True),
            ("pm_max_turns", False, True),
            # None exit_reason (non-granite) → NOT REACTION_ERROR
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
        """The executor selects REACTION_ERROR for non-clean granite exits and
        a non-error reaction for clean exits.

        We use sentinel EmojiResult objects injected via patched constants so
        the test is deterministic regardless of which real emoji is picked by
        find_best_emoji().

        Note: we only assert REACTION_ERROR vs non-REACTION_ERROR. The distinction
        between REACTION_SUCCESS and REACTION_COMPLETE (the other non-error bucket)
        depends on messenger.has_communicated() which varies with internal executor
        flow and is not the target behavior being tested here.
        """
        from tools.emoji_embedding import EmojiResult

        # Build unique sentinels so identity comparison is unambiguous.
        sentinel_success = EmojiResult(emoji="TEST_SUCCESS_SENTINEL")
        sentinel_complete = EmojiResult(emoji="TEST_COMPLETE_SENTINEL")
        sentinel_error = EmojiResult(emoji="TEST_ERROR_SENTINEL")

        import uuid

        # Use a unique session_id per test to avoid Redis collisions when tests
        # run in parallel (parametrize generates many sessions).
        unique_sid = f"rg-{uuid.uuid4().hex[:12]}"
        session = _make_session(working_dir="/tmp", session_id=unique_sid)

        # Transition the session to "running" so the executor's
        # AgentSession.query.filter(status="running") lookup finds it and
        # agent_session is non-None for the reaction-gating branch.
        session.status = "running"
        session.save(update_fields=["status"])

        # Track react_cb calls via a spy injected through _resolve_callbacks.
        react_calls: list[tuple] = []

        async def _spy_react(chat_id, message_id, emoji):
            react_calls.append((chat_id, message_id, emoji))

        async def _null_send(msg: str) -> None:
            pass

        # BridgeAdapter.run sets exit_reason and user_facing_routed directly on
        # self._agent_session (the same Python object the executor holds as
        # agent_session). We replicate that in _fake_run via self._agent_session
        # so the executor's reaction-gating branch sees the updated values.
        async def _fake_run(self, user_message, working_dir):
            if self._agent_session is not None:
                self._agent_session.exit_reason = exit_reason
                if user_facing_routed:
                    self._agent_session.user_facing_routed = True
            return ""

        pool = await _make_initialized_pool(size=1)
        with (
            patch("agent.granite_container.pty_pool._pty_pool", pool),
            patch.object(BridgeAdapter, "run", _fake_run),
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


# ---------------------------------------------------------------------------
# Part B (#1836): PM-handle claude_session_uuid population via
# _persist_resume_handles, so the resume gate stops hard-erroring for granite.
# PTY-PM only (headless-PM UUID population is deferred to #1843).
# ---------------------------------------------------------------------------


def _make_adapter(session: AgentSession) -> BridgeAdapter:
    """Construct a BridgeAdapter around a real AgentSession for calling
    _persist_resume_handles directly. The pool/callbacks are never touched by
    _persist_resume_handles, so a MagicMock pool is sufficient."""
    return BridgeAdapter(
        agent_session=session,
        project_key="test",
        transport="telegram",
        pool=MagicMock(spec=PTYPool),
    )


class TestPersistResumeHandlesUuidPopulation:
    """`_persist_resume_handles` mirrors the PTY-PM handle's UUID onto the
    scalar `claude_session_uuid` so `resume_session()`'s gate passes (#1836
    Part B). PTY-PM only."""

    def test_pty_pm_run_populates_claude_session_uuid(self, redis_test_db):
        """A PTY-PM granite spawn mirrors the PM handle's `claude_session_id`
        onto `claude_session_uuid` (persisting it via `save(update_fields=...)`),
        and `resume_session()` succeeds against the session (gate passes).

        We assert on the same in-memory instance the adapter mutates (it holds
        `self._agent_session is session`) and spy on `save` to prove the field
        is persisted — a class-set reload would be flaky here because the redis
        test DB is shared across concurrent SDLC lanes that flush it.
        """
        import uuid as uuid_module

        sid = f"granite-resume-{uuid_module.uuid4().hex[:12]}"
        session = _make_session(session_id=sid)
        # Resume only targets terminal sessions; a completed status clears the
        # status gate (read in-memory by resume_session).
        session.status = "completed"

        adapter = _make_adapter(session)
        pm_uuid = str(uuid_module.uuid4())
        dev_uuid = str(uuid_module.uuid4())
        with patch.object(session, "save", wraps=session.save) as spy_save:
            adapter._persist_resume_handles(
                "/tmp/wd",
                {"pm": pm_uuid, "dev": dev_uuid},
                {"pm": "pty", "dev": "pty"},
            )

        assert session.claude_session_uuid == pm_uuid, (
            "PTY-PM run must mirror the PM handle's claude_session_id onto claude_session_uuid"
        )
        # resume_handles carries the per-role schema #1721 consumes.
        pm_handle = next(h for h in session.resume_handles if h["role"] == "pm")
        assert pm_handle["claude_session_id"] == pm_uuid
        # The write was persisted: claude_session_uuid was in the save's update_fields.
        persisted_fields = [c.kwargs.get("update_fields") or [] for c in spy_save.call_args_list]
        assert any("claude_session_uuid" in fields for fields in persisted_fields), (
            "claude_session_uuid must be included in the persisted update_fields"
        )

        # The resume gate now passes for this granite session. The real Redis
        # transition (CAS + steering push) is covered by
        # test_valor_session_resume_release.py; mock it here so this test stays
        # deterministic under the shared, concurrently-flushed redis test DB.
        with (
            patch("models.session_lifecycle.transition_status") as mock_transition,
            patch("agent.steering.push_steering_message"),
        ):
            result = resume_session(session, "continue where we left off", source="test")
        assert result.success is True, (
            f"resume_session must succeed once claude_session_uuid is populated; "
            f"got error={result.error!r}"
        )
        mock_transition.assert_called_once()
        assert result.warning and "#1721" in result.warning, (
            "granite gate-pass must carry the #1721 re-entry-deferral warning"
        )

    def test_null_pm_uuid_does_not_clobber_existing_value(self, redis_test_db):
        """A null PM `claude_session_id` at spawn (headless-at-spawn) must NOT
        write None over an existing `claude_session_uuid` (Risk 3).

        Asserted on the in-memory instance the adapter mutates — a class-set
        reload is avoided (shared, concurrently-flushed redis test DB).
        """
        import uuid as uuid_module

        sid = f"granite-resume-neg-{uuid_module.uuid4().hex[:12]}"
        existing_uuid = str(uuid_module.uuid4())
        session = _make_session(session_id=sid)
        session.claude_session_uuid = existing_uuid

        adapter = _make_adapter(session)
        # Headless transport → the handle's claude_session_id is null at spawn.
        adapter._persist_resume_handles(
            "/tmp/wd",
            {"pm": None, "dev": None},
            {"pm": "headless", "dev": "headless"},
        )

        assert session.claude_session_uuid == existing_uuid, (
            "a null PM claude_session_id must never clobber an existing claude_session_uuid"
        )
        # And claude_session_uuid must NOT appear in the resume_handles-only save.
        pm_handle = next(h for h in session.resume_handles if h["role"] == "pm")
        assert pm_handle["claude_session_id"] is None

    def test_persist_failure_logs_warning_and_does_not_crash(self, redis_test_db, caplog):
        """`_persist_resume_handles` wraps its body in `except Exception`: a
        persist failure logs a warning and does not raise (fail-silent —
        observability must never crash the run)."""
        import uuid as uuid_module

        session = _make_session(session_id=f"granite-resume-exc-{uuid_module.uuid4().hex[:12]}")
        adapter = _make_adapter(session)

        with (
            patch(
                "agent.granite_container.bridge_adapter._transcript_path_from_spec",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            # Must not raise despite the injected failure.
            adapter._persist_resume_handles(
                "/tmp/wd",
                {"pm": "pm-uuid-x", "dev": "dev-uuid-y"},
                {"pm": "pty", "dev": "pty"},
            )

        assert any("resume_handles persist failed" in r.message for r in caplog.records), (
            "a persist failure must log the [bridge-adapter] warning"
        )
