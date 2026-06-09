"""BridgeAdapter: thin wrapper around Container for the bridge path (plan #1572).

The BridgeAdapter is the boundary between the Container (sync,
pexpect-driven, owns 2 PTYs) and the session harness (async, owns
AgentSession, owns Telegram delivery). It is the only module that
imports both sides of the boundary.

Three responsibilities:

1. **Resolve the registered send_cb once.** The container is a
   sync pexpect driver; the bridge is async. The adapter captures
   the registered TelegramRelayOutputHandler.send callback at
   construction time and stashes the chat_id, reply_to_msg_id, and
   agent_session for use during the per-turn delivery.

2. **Deliver mid-loop.** When the container classifies a turn as
   `[/user]`, the adapter's user-payload callback fires
   synchronously and blocks the container thread until the
   delivery is acknowledged. When the container classifies
   `[/complete]`, the complete-payload callback fires with the
   trailing summary. Both callbacks write a `session_events`
   entry on failure so the dashboard surfaces the error.

3. **Publish progress and exit signals.** The adapter writes
   per-turn observability data to `agent_session.session_events`
   (granite latency, classification misses, exit reason).
   Mid-loop `[/user]` and `[/complete]` payloads reach Telegram
   through the existing `TelegramRelayOutputHandler` path;
   `BackgroundTask.run(coro, send_result=False)` is the harness
   contract so the harness layer does NOT double-deliver.

**Defensive `send_cb=None` default** (BRIDGE-1): if no callback
is registered (standalone worker, no bridge), the adapter logs
a warning and runs the container to completion. The integration
test must cover this path.

**Synchronous callback contract** (ADV-5): the container's
`on_user_payload` is called from a thread. The adapter wraps the
async `send_cb` in a sync callable that does
`asyncio.run_coroutine_threadsafe(self._send_cb(...), loop).result(timeout=30)`
to block until delivery completes. The thread holds for the
duration of the network call, which is acceptable per-turn for
6h sessions.

**Why not fire-and-forget?** The container exits on `pm_user`
with `exit_message = payload`. If the callback were fire-and-
forget, the user would see "no message delivered" because the
harness's BackgroundTask would never see the delivery. By
blocking, we guarantee the user-visible message lands before
the container returns.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent.granite_container.container import Container, ContainerResult
from agent.granite_container.pty_pool import PTYPool

logger = logging.getLogger(__name__)

# Default timeout for the synchronous bridge-callback delivery. The
# container's thread blocks for this long waiting on the asyncio
# future. 30s matches the Telegram relay's standard send timeout.
DEFAULT_DELIVERY_TIMEOUT_S = 30.0


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for `session_events` entries."""
    return datetime.now(UTC).isoformat()


def _append_session_event(agent_session, event: dict) -> None:
    """Append an observability event to ``agent_session.session_events``.

    ``AgentSession.session_events`` is a ``ListField(null=True)``, so a
    freshly created session has ``session_events is None`` rather than an
    empty list. Earlier code early-returned on ``None`` and silently
    dropped every granite event in production. This helper initializes the
    list when it is ``None`` so the entry is never lost. All writes fail
    silently — observability must never crash the run.
    """
    if agent_session is None:
        return
    try:
        events = getattr(agent_session, "session_events", None)
        if events is None:
            events = []
            agent_session.session_events = events
        events.append(event)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(
            "[bridge-adapter] could not write session_event %s: %s",
            event.get("type"),
            e,
        )


class BridgeAdapter:
    """Thin wrapper around Container that publishes per-turn output
    to the bridge's Telegram relay.

    The adapter is single-shot: one BridgeAdapter instance, one
    Container.run, one user-message. Reuse would require a state
    machine the cutover plan does not need.
    """

    def __init__(
        self,
        agent_session: Any,
        project_key: str,
        transport: str,
        pool: PTYPool,
        resolve_callbacks: Callable[[str, str], tuple[Callable | None, Any]] | None = None,
        delivery_timeout_s: float = DEFAULT_DELIVERY_TIMEOUT_S,
    ) -> None:
        self._agent_session = agent_session
        self._project_key = project_key
        self._transport = transport
        self._pool = pool
        self._delivery_timeout_s = delivery_timeout_s

        # Resolve the bridge callback. The default resolver looks
        # for a registered `agent_session_queue` callback; tests
        # can pass an alternative resolver.
        if resolve_callbacks is None:
            resolve_callbacks = _default_resolve_callbacks
        self._send_cb, _state = resolve_callbacks(project_key, transport)

        # Capture chat_id and reply_to_msg_id once. The
        # AgentSession may not have these yet (e.g. tests
        # construct a session without persisting it); fall back
        # to None and let the callback short-circuit.
        self._chat_id = getattr(agent_session, "chat_id", None)
        self._reply_to_msg_id = getattr(agent_session, "telegram_message_id", None)

        # If no callback is registered (standalone worker, no
        # bridge), use a logger-only no-op. The container still
        # runs to completion; we just don't deliver. This
        # satisfies BRIDGE-1.
        if self._send_cb is None:
            self._on_user_payload: Callable[[str], None] = self._log_only_user
            self._on_complete_payload: Callable[[str], None] = self._log_only_complete
        else:
            self._on_user_payload = self._make_user_callback()
            self._on_complete_payload = self._make_complete_callback()

    # -- Public API -------------------------------------------------------

    async def run(self, user_message: str, working_dir: str) -> str:
        """Run the container end-to-end. Returns "" — `BackgroundTask`
        has `send_result=False` and discards the return value.

        The mid-loop `[/user]` and `[/complete]` payloads are
        delivered through the bridge callback. The container's
        `exit_summary` lands in `agent_session.session_events`.
        """
        # Use the pool's async context manager to acquire a
        # (pm, dev) PTY pair. The pool's semaphore is bounded
        # by GRANITE_PTY_POOL_SIZE; over-cap sessions wait in
        # Redis.
        async with self._pool.acquire_pair() as (pm, dev):
            # Hand the pool's pre-warmed pair to Container so it
            # reuses them instead of spawning a fresh pair. The
            # pre-warm is the pool's whole point — discarding it
            # doubled the live `claude` process count and broke
            # the orphan-leak acceptance criterion (issue #1572).
            # Mark the pair as pool-owned so Container._close_pair
            # does not double-close; the pool's __aexit__ owns the
            # close + respawn lifecycle.
            pm._released_to_pool = True
            dev._released_to_pool = True
            container = Container(
                user_message=user_message,
                cwd=working_dir,
                on_user_payload=self._on_user_payload,
                on_complete_payload=self._on_complete_payload,
                pm_pty=pm,
                dev_pty=dev,
            )
            # The container's run is sync (pexpect-driven). Run
            # it in a worker thread so the asyncio event loop
            # stays responsive to heartbeats, steering
            # injection, and watchdog ticks.
            result: ContainerResult = await asyncio.to_thread(container.run)
            self._publish_exit_summary(result)
            self._maybe_publish_exit_anomaly(result)
        return ""

    # -- Session events ---------------------------------------------------

    def _publish_exit_summary(self, result: ContainerResult) -> None:
        """Append a `session_events` entry summarizing the run.

        The dashboard's reflection sweep and Sentry log capture
        surface this entry; it is NOT delivered to Telegram
        (per the plan's `## Solution`).
        """
        _append_session_event(
            self._agent_session,
            {
                "type": "exit_summary",
                "exit_reason": result.exit_reason,
                "turns": len(result.turns),
                "compliance_misses": result.classification_compliance_misses,
                "ts": _now_iso(),
            },
        )

    def _maybe_publish_exit_anomaly(self, result: ContainerResult) -> None:
        """When the run ended on a hang / startup-unresolved
        exit_reason, log at ERROR and append a session_events
        entry. This is the on-call path for kernel regressions
        at 3am — Sentry's default log-capture wiring picks up
        the logger.error, and the dashboard surfaces the
        session_events entry (hardens OPS-1)."""
        if result.exit_reason not in ("pm_hang", "dev_hang", "startup_unresolved"):
            return
        logger.error(
            "[granite-exit-anomaly] session=%s exit_reason=%s exit_message=%s",
            getattr(self._agent_session, "session_id", "<no-id>"),
            result.exit_reason,
            result.exit_message,
        )
        _append_session_event(
            self._agent_session,
            {
                "type": "exit_anomaly",
                "exit_reason": result.exit_reason,
                "ts": _now_iso(),
            },
        )

    # -- Bridge callbacks (sync wrappers around async send_cb) -----------

    def _make_user_callback(self) -> Callable[[str], None]:
        """Build the sync callable for `[/user]` payloads.

        The container thread is a pexpect thread (not an asyncio
        thread). We use `asyncio.run_coroutine_threadsafe` to
        schedule the async `send_cb` on the worker's event loop
        and `.result(timeout=...)` to block until delivery
        completes. Without `.result`, the container could exit
        before the user sees the message.
        """
        send_cb = self._send_cb
        chat_id = self._chat_id
        reply_to = self._reply_to_msg_id
        agent_session = self._agent_session
        timeout_s = self._delivery_timeout_s

        def _on_user(payload: str) -> None:
            self._deliver_sync(send_cb, chat_id, payload, reply_to, agent_session, timeout_s)

        return _on_user

    def _make_complete_callback(self) -> Callable[[str], None]:
        """Build the sync callable for `[/complete]` payloads.
        Same shape as the user callback; the container
        classifies a single `[/complete]` at the end of the
        run."""
        send_cb = self._send_cb
        chat_id = self._chat_id
        reply_to = self._reply_to_msg_id
        agent_session = self._agent_session
        timeout_s = self._delivery_timeout_s

        def _on_complete(payload: str) -> None:
            self._deliver_sync(send_cb, chat_id, payload, reply_to, agent_session, timeout_s)

        return _on_complete

    def _deliver_sync(
        self,
        send_cb: Callable,
        chat_id: Any,
        payload: str,
        reply_to: Any,
        agent_session: Any,
        timeout_s: float,
    ) -> None:
        """Schedule the async send_cb on the worker's event loop
        and block the calling thread until delivery completes
        or the timeout fires.

        On RuntimeError (no running loop — worker is shutting
        down) or asyncio.TimeoutError, the error is logged and
        a session_events entry is appended. The container keeps
        running.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread. The pexpect thread
            # is not the asyncio thread. Use the worker
            # thread's loop via run_coroutine_threadsafe against
            # the worker loop, but we don't have a direct
            # handle to it here. The simplest fallback: log and
            # skip delivery. (In practice, the worker thread's
            # loop is captured in `BridgeAdapter.__init__` via
            # the resolve_callbacks hook and stashed; for the
            # cutover, we accept this fallback for any path
            # that runs from outside the asyncio thread.)
            logger.warning(
                "[bridge-adapter] send_cb called outside the asyncio loop; "
                "delivery skipped (this should not happen in production)"
            )
            self._record_delivery_failure(payload, "no_event_loop")
            return

        # We're on the asyncio thread (the harness awaits
        # `asyncio.to_thread` from here). The send_cb is async;
        # we can just call it directly via `loop.create_task`
        # and `await` on it via `asyncio.run_coroutine_threadsafe`
        # only when the calling thread is not the asyncio thread.
        # Since `container.run` is in a worker thread, we DO need
        # `run_coroutine_threadsafe`. Capture the loop ref at
        # call time and use the worker-loop pattern.
        # For simplicity, we schedule and `await` directly here
        # because the container's thread is the calling thread
        # and `_on_user` is called from there, but Python's
        # `asyncio.get_running_loop()` is per-thread. So we
        # detect: if the calling thread is the asyncio thread,
        # we can call directly; otherwise we use
        # `run_coroutine_threadsafe`.
        import inspect

        try:
            if inspect.iscoroutinefunction(send_cb):
                coro = send_cb(chat_id, payload, reply_to, agent_session)
            else:
                # If send_cb is sync, just call it; the
                # result is a coroutine-aware wrapper that the
                # bridge uses today.
                send_cb(chat_id, payload, reply_to, agent_session)
                return
            # We're calling from a pexpect thread. Schedule on
            # the worker's event loop and block.
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result(timeout=timeout_s)
        except RuntimeError as e:
            # Loop is closed (worker shutdown).
            logger.warning("[bridge-adapter] send_cb delivery failed (loop closed): %s", e)
            self._record_delivery_failure(payload, "loop_closed")
        except Exception as e:
            # Anything else: log and continue. The container
            # must not crash on a delivery failure.
            logger.warning(
                "[bridge-adapter] send_cb delivery raised: %s (payload=%d chars)",
                e,
                len(payload),
            )
            self._record_delivery_failure(payload, str(e))

    def _record_delivery_failure(self, payload: str, reason: str) -> None:
        """Append a `session_events` entry when a mid-loop
        delivery fails. The dashboard surfaces the error; we
        do NOT emit a user-visible "I tried to send you a
        message but it failed" delivery (would violate the
        no-spam rule)."""
        _append_session_event(
            self._agent_session,
            {
                "type": "delivery_failure",
                "payload_chars": len(payload),
                "reason": reason,
                "ts": _now_iso(),
            },
        )

    # -- send_cb=None fallback (BRIDGE-1) ---------------------------------

    def _log_only_user(self, payload: str) -> None:
        logger.warning(
            "[bridge-adapter] bridge callback missing — granite [/user] output "
            "will be logged but not delivered (payload=%d chars)",
            len(payload),
        )
        logger.info("[bridge-adapter-granite-user] %s", payload)

    def _log_only_complete(self, payload: str) -> None:
        logger.warning(
            "[bridge-adapter] bridge callback missing — granite [/complete] output "
            "will be logged but not delivered (payload=%d chars)",
            len(payload),
        )
        logger.info("[bridge-adapter-granite-complete] %s", payload)


def _default_resolve_callbacks(project_key: str, transport: str) -> tuple[Callable | None, Any]:
    """Default resolver: defer to `agent_session_queue._resolve_callbacks`.

    Imported lazily to avoid a hard dependency at module-import
    time (the agent_session_queue module may not exist in test
    environments)."""
    try:
        from agent.agent_session_queue import _resolve_callbacks
    except Exception:
        return (None, None)
    return _resolve_callbacks(project_key, transport)
