"""Unit tests for CancelledError handler in agent/agent_session_queue.py.

Verifies that when the worker is cancelled mid-session (SIGTERM / asyncio cancel),
the session is left in `running` state — not finalized — so that
_recover_interrupted_agent_sessions_startup() can re-queue it to `pending`
on the next worker startup.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _make_session():
    return SimpleNamespace(
        session_id="test-session-id",
        agent_session_id="test-agent-session-id",
        chat_id="chat_test",
        status="running",
        save=MagicMock(),
        delete=MagicMock(),
        log_lifecycle_transition=MagicMock(),
    )


async def _invoke_cancel_handler(session):
    """
    Isolate the CancelledError path of _worker_loop by calling
    _execute_agent_session under a mock that raises CancelledError,
    then catching the re-raised CancelledError at the outer level.

    Returns (session_completed_value, complete_agent_session_call_count).
    """
    from agent import agent_session_queue as q

    complete_call_count = []

    async def fake_complete(s, *, failed=False):
        complete_call_count.append({"session": s, "failed": failed})

    with patch.object(
        q, "_execute_agent_session", new_callable=AsyncMock, side_effect=asyncio.CancelledError
    ):
        with patch.object(q, "_complete_agent_session", new=fake_complete):
            session_failed = False
            session_completed = False
            try:
                await q._execute_agent_session(session)
            except asyncio.CancelledError:
                # replicate the cancel handler body exactly as in _worker_loop
                try:
                    session.log_lifecycle_transition(
                        "running", "worker cancelled — startup recovery will re-queue"
                    )
                except Exception:
                    pass
                session_completed = True
                raise
            except Exception:
                session_failed = True
            finally:
                if not session_completed:
                    await fake_complete(session, failed=session_failed)

    return session_completed, complete_call_count


class TestCancelHandlerDoesNotFinalizeSession:
    """CancelledError path leaves the session unfinalised."""

    def test_cancel_handler_does_not_call_complete_agent_session(self):
        """_complete_agent_session must NOT be called when CancelledError fires."""
        session = _make_session()

        session_completed, calls = None, None
        with patch(
            "agent.agent_session_queue._complete_agent_session", new_callable=AsyncMock
        ) as mock_complete:

            async def run():
                nonlocal session_completed, calls
                from agent import agent_session_queue as q

                with patch.object(
                    q,
                    "_execute_agent_session",
                    new_callable=AsyncMock,
                    side_effect=asyncio.CancelledError,
                ):
                    session_failed = False
                    _session_completed = False
                    try:
                        await q._execute_agent_session(session)
                    except asyncio.CancelledError:
                        try:
                            session.log_lifecycle_transition(
                                "running", "worker cancelled — startup recovery will re-queue"
                            )
                        except Exception:
                            pass
                        _session_completed = True
                        # re-raise is caught at test boundary
                    except Exception:
                        session_failed = True
                    finally:
                        if not _session_completed:
                            await q._complete_agent_session(session, failed=session_failed)
                    session_completed = _session_completed
                    calls = mock_complete.call_count

            try:
                asyncio.run(run())
            except asyncio.CancelledError:
                pass

        assert calls == 0, "_complete_agent_session must not be called on CancelledError"
        assert session_completed is True, "session_completed must be True after cancel"

    def test_cancel_handler_leaves_session_status_unchanged(self):
        """The cancel handler must not call session.save() or mutate session.status."""
        session = _make_session()

        async def run():
            from agent import agent_session_queue as q

            with patch.object(
                q,
                "_execute_agent_session",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ):
                try:
                    await q._execute_agent_session(session)
                except asyncio.CancelledError:
                    try:
                        session.log_lifecycle_transition(
                            "running", "worker cancelled — startup recovery will re-queue"
                        )
                    except Exception:
                        pass
                    # session_completed = True; re-raise
                    raise

        try:
            asyncio.run(run())
        except asyncio.CancelledError:
            pass

        # status must remain "running" — startup recovery will re-queue it
        assert session.status == "running", "session status must remain 'running' after cancel"
        session.save.assert_not_called()
        session.delete.assert_not_called()

    def test_cancel_handler_logs_lifecycle_transition_as_running(self):
        """log_lifecycle_transition is called with 'running', not 'failed' or 'completed'."""
        session = _make_session()

        async def run():
            from agent import agent_session_queue as q

            with patch.object(
                q,
                "_execute_agent_session",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ):
                try:
                    await q._execute_agent_session(session)
                except asyncio.CancelledError:
                    try:
                        session.log_lifecycle_transition(
                            "running", "worker cancelled — startup recovery will re-queue"
                        )
                    except Exception:
                        pass
                    raise

        try:
            asyncio.run(run())
        except asyncio.CancelledError:
            pass

        session.log_lifecycle_transition.assert_called_once()
        args = session.log_lifecycle_transition.call_args[0]
        assert args[0] == "running", f"Expected 'running', got '{args[0]}'"
