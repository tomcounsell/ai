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
`on_user_payload` is called from the `asyncio.to_thread` worker
thread, which has NO running event loop — `asyncio.get_running_loop()`
raises there. `run()` therefore captures the worker's event loop
(it always executes on the asyncio thread) into `self._loop`
before handing the container to the thread, and `_deliver_sync`
schedules the async `send_cb` onto that captured loop via
`asyncio.run_coroutine_threadsafe(...).result(timeout=30)` to
block until delivery completes. The thread holds for the
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
from agent.granite_container.pty_pool import PairSpawnSpec, PTYPool

logger = logging.getLogger(__name__)

# Default timeout for the synchronous bridge-callback delivery. The
# container's thread blocks for this long waiting on the asyncio
# future. 30s matches the Telegram relay's standard send timeout.
DEFAULT_DELIVERY_TIMEOUT_S = 30.0


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for `session_events` entries."""
    return datetime.now(UTC).isoformat()


def _append_session_event(agent_session, event: dict) -> None:
    """Append an observability event to ``agent_session.session_events``
    and persist it.

    ``AgentSession.session_events`` is a ``ListField(null=True)``, so a
    freshly created session has ``session_events is None`` rather than an
    empty list. Earlier code early-returned on ``None`` and silently
    dropped every granite event in production. This helper initializes the
    list when it is ``None`` so the entry is never lost.

    The append alone only mutates the in-memory ORM object — the
    executor's post-run saves use ``update_fields`` sets that exclude
    ``session_events``, and finalization loads a fresh copy by
    session_id, so an unsaved append never reaches Redis. Persist
    explicitly with the model's documented partial-save pattern
    (``models/agent_session.py`` ``_append_event_dict``; cf.
    ``agent/agent_session_queue.py`` checkpoint save):
    ``save(update_fields=["session_events", "updated_at"])``.

    All writes fail silently — observability must never crash the run.
    """
    if agent_session is None:
        return
    try:
        events = getattr(agent_session, "session_events", None)
        if events is None:
            events = []
            agent_session.session_events = events
        events.append(event)
        save = getattr(agent_session, "save", None)
        if callable(save):
            agent_session.updated_at = datetime.now(UTC)
            save(update_fields=["session_events", "updated_at"])
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
        session_env: dict[str, str] | None = None,
        pm_system_prompt: str | None = None,
        pm_model: str | None = None,
    ) -> None:
        self._agent_session = agent_session
        self._project_key = project_key
        self._transport = transport
        self._pool = pool
        self._delivery_timeout_s = delivery_timeout_s
        # Per-session spawn requirements (PR #1612 review B1+B2). Env
        # vars (SESSION_TYPE, AGENT_SESSION_ID, CLAUDE_CODE_TASK_LIST_ID,
        # VALOR_PARENT_SESSION_ID, ...) and the composed persona overlay
        # can only be injected at process spawn, so `run()` passes these
        # to the pool as a `PairSpawnSpec`; the pool spawns a fresh pair
        # at acquire time when they differ from its spawn-time defaults.
        self._session_env = dict(session_env) if session_env else None
        self._pm_system_prompt = pm_system_prompt
        # D1-resolved PM model (session.model > settings > codebase
        # default). The Dev PTY intentionally has no per-session model
        # knob — it stays on GRANITE__DEV_MODEL via the spec default.
        self._pm_model = pm_model
        # The worker's event loop, captured by `run()` on the asyncio
        # thread before the container is handed to `asyncio.to_thread`.
        # The container's callbacks fire on the to_thread worker thread,
        # where `asyncio.get_running_loop()` raises — this captured ref
        # is the only way `_deliver_sync` can reach the loop.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set to True by the user/complete callbacks when _deliver_sync
        # returns True (confirmed delivery). Propagated to
        # agent_session.user_facing_routed in _publish_exit_summary so
        # the executor's emoji branch can distinguish a real delivery
        # from a session that was never routed to the user (issue #1647).
        self._user_facing_routed: bool = False

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
        # Capture the running loop BEFORE entering the thread. The
        # container's callbacks fire on the to_thread worker thread
        # where no loop is running; `_deliver_sync` schedules onto
        # this captured loop via `run_coroutine_threadsafe`.
        self._loop = asyncio.get_running_loop()
        # Use the pool's async context manager to acquire a
        # (pm, dev) PTY pair. The pool's semaphore is bounded
        # by GRANITE_PTY_POOL_SIZE; over-cap sessions wait in
        # Redis. The spawn spec carries the session's cwd, env,
        # persona overlay, and model override — when any of them
        # differ from the pool's spawn-time defaults, the pool
        # replaces the pre-warmed pair with a per-session spawn
        # (spawn-on-acquire; the bounded-slot invariant holds).
        # Generate deterministic UUIDs for each PTY's Claude Code session.
        # These are passed via `claude --session-id <uuid>` so the transcript
        # path is known at spawn time:
        #   ~/.claude/projects/{cwd-slug}/{uuid}.jsonl
        pm_session_id = str(uuid.uuid4())
        dev_session_id = str(uuid.uuid4())
        spawn_spec = PairSpawnSpec(
            cwd=working_dir,
            env=self._session_env,
            pm_model=self._pm_model,
            pm_system_prompt=self._pm_system_prompt,
            pm_session_id=pm_session_id,
            dev_session_id=dev_session_id,
        )
        async with self._pool.acquire_pair(spawn_spec=spawn_spec) as (pm, dev):
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
                on_turn=self._bump_last_turn_at,
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

        Also propagates the `user_facing_routed` flag (set by the
        user/complete callbacks when _deliver_sync returns True, or by
        the container's wrap-up guard via result.user_facing_routed) onto
        agent_session so session_executor's emoji branch can see it
        (issue #1647). The OR of adapter flag and result flag covers all
        delivery paths.
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
        # Propagate delivery confirmation to agent_session (issue #1647).
        # The executor reads this via getattr(agent_session,
        # "user_facing_routed", False) so it selects REACTION_COMPLETE
        # instead of the bare-emoji REACTION_SUCCESS.
        try:
            routed = self._user_facing_routed or result.user_facing_routed
            if self._agent_session is not None:
                update_fields = ["updated_at"]
                if routed:
                    self._agent_session.user_facing_routed = routed
                    update_fields.append("user_facing_routed")
                # Persist exit_reason and PTY identity fields (issue #1648).
                # Fail-silent: observability must never crash the run.
                self._agent_session.exit_reason = result.exit_reason
                update_fields.append("exit_reason")
                if result.pm_pid is not None:
                    self._agent_session.pm_pid = result.pm_pid
                    update_fields.append("pm_pid")
                if result.dev_pid is not None:
                    self._agent_session.dev_pid = result.dev_pid
                    update_fields.append("dev_pid")
                if result.pm_transcript_path is not None:
                    self._agent_session.pm_transcript_path = result.pm_transcript_path
                    update_fields.append("pm_transcript_path")
                if result.dev_transcript_path is not None:
                    self._agent_session.dev_transcript_path = result.dev_transcript_path
                    update_fields.append("dev_transcript_path")
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=update_fields)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[bridge-adapter] exit summary field save failed: %s", e)

    def _maybe_publish_exit_anomaly(self, result: ContainerResult) -> None:
        """When the run ended on a hang / startup-unresolved
        exit_reason, log at ERROR and append a session_events
        entry. This is the on-call path for kernel regressions
        at 3am — Sentry's default log-capture wiring picks up
        the logger.error, and the dashboard surfaces the
        session_events entry (hardens OPS-1)."""
        if result.exit_reason not in (
            "pm_hang",
            "dev_hang",
            "startup_unresolved",
            "pm_no_user_message",
        ):
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

    # -- Liveness (two-tier no-progress detector, TD1) ---------------------

    def _bump_last_turn_at(self) -> None:
        """Bump ``agent_session.last_turn_at`` for the in-flight session.

        The harness path wrote this via the sdk_client ``result``
        handler and the liveness hooks (both keyed on
        ``AGENT_SESSION_ID``); the granite container has no SDK result
        events, so without this bump ``sdk_ever_output`` stays False
        forever and the two-tier no-progress detector's sub-check A is
        neutralized for granite sessions. The container calls this once
        per classified PM turn (the ``on_turn`` hook). Fail-silent:
        liveness signaling must never crash the run. Called from the
        ``asyncio.to_thread`` worker thread — the save is a plain
        blocking Redis write, which is fine off the event loop.
        """
        if self._agent_session is None:
            return
        try:
            self._agent_session.last_turn_at = datetime.now(UTC)
            save = getattr(self._agent_session, "save", None)
            if callable(save):
                save(update_fields=["last_turn_at"])
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[bridge-adapter] last_turn_at bump failed: %s", e)

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
            delivered = self._deliver_sync(
                send_cb, chat_id, payload, reply_to, agent_session, timeout_s
            )
            if delivered:
                self._user_facing_routed = True

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
            delivered = self._deliver_sync(
                send_cb, chat_id, payload, reply_to, agent_session, timeout_s
            )
            if delivered:
                self._user_facing_routed = True

        return _on_complete

    def _deliver_sync(
        self,
        send_cb: Callable,
        chat_id: Any,
        payload: str,
        reply_to: Any,
        agent_session: Any,
        timeout_s: float,
    ) -> bool:
        """Schedule the async send_cb on the worker's event loop
        (captured by `run()` into `self._loop`) and block the
        calling thread until delivery completes or the timeout
        fires.

        The caller is the container's `asyncio.to_thread` worker
        thread — `asyncio.get_running_loop()` raises there, so the
        captured loop ref is mandatory for async send_cbs. A sync
        send_cb is called directly on the calling thread.

        Returns True on confirmed delivery, False on any failure or
        timeout. The caller (_make_user_callback / _make_complete_callback)
        uses the return value to set self._user_facing_routed (issue #1647).

        On a missing/closed loop or a delivery timeout, the error
        is logged and a session_events entry is appended. The
        container keeps running.
        """
        import inspect

        try:
            if not inspect.iscoroutinefunction(send_cb):
                # Sync send_cb (test doubles, legacy wrappers): call
                # directly on this thread.
                send_cb(chat_id, payload, reply_to, agent_session)
                return True

            loop = self._loop
            if loop is None or loop.is_closed():
                # run() was never awaited (direct Container use) or
                # the worker loop is gone (shutdown). Without a live
                # loop there is nowhere to schedule the coroutine.
                logger.warning(
                    "[bridge-adapter] no captured event loop for send_cb; "
                    "delivery skipped (loop=%r)",
                    loop,
                )
                self._record_delivery_failure(payload, "no_event_loop")
                return False

            coro = send_cb(chat_id, payload, reply_to, agent_session)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                # Same-thread call (sync container.run invoked on the
                # asyncio thread, e.g. in tests). Blocking on a future
                # here would deadlock the loop — schedule fire-and-
                # forget instead.
                loop.create_task(coro)
                # Fire-and-forget: we cannot confirm delivery synchronously
                # on the same event loop. Count as False so the caller does
                # not set user_facing_routed prematurely.
                return False
            # Production path: pexpect worker thread → schedule on the
            # captured worker loop and block until delivered.
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result(timeout=timeout_s)
            return True
        except RuntimeError as e:
            # Loop closed between the check and the schedule (worker
            # shutdown race).
            logger.warning("[bridge-adapter] send_cb delivery failed (loop closed): %s", e)
            self._record_delivery_failure(payload, "loop_closed")
        except Exception as e:
            # Anything else (including FutureTimeoutError): log and
            # continue. The container must not crash on a delivery
            # failure.
            logger.warning(
                "[bridge-adapter] send_cb delivery raised: %s (payload=%d chars)",
                e,
                len(payload),
            )
            # Include the exception type: TimeoutError stringifies to "".
            self._record_delivery_failure(payload, f"{type(e).__name__}: {e}")
        return False

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
