"""
Boss Messenger - Communication channel back to the supervisor.

This module provides a way for long-running agent work to send messages
back to the supervisor (currently via Telegram, but abstracted for future
platforms).

Usage:
    messenger = BossMessenger(send_callback)
    await messenger.send("Here's what I found...")
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from bridge.utc import utc_now

logger = logging.getLogger(__name__)


@dataclass
class MessageRecord:
    """Record of a sent message for tracking."""

    content: str
    timestamp: datetime
    message_type: str = "result"  # "result", "error"


@dataclass
class BossMessenger:
    """
    Communication channel for sending messages to the supervisor.

    The agent uses this to send results when work is complete.
    The bridge provides the actual send implementation.

    Liveness-observer callbacks (optional, issue #1036, #1269):
        on_sdk_started:    one-shot, fires when the SDK subprocess is spawned
                           (pid is provided by the caller once known).
        on_sdk_finished:   one-shot, fires once per subprocess exit
                           (`proc.communicate()` return) — issue #1269.
                           Paired with on_sdk_started so the worker can clear
                           `AgentSession.harness_pid` immediately when the
                           subprocess dies, preventing PID-recycling false
                           positives in the dashboard liveness probe.
        on_heartbeat_tick: fires on each `_watchdog` tick (default 60s) while
                           the SDK subprocess is running.
        on_stdout_event:   fires on each stdout event the SDK emits.

    All four callbacks default to None; when set, exceptions they raise are
    caught and logged at WARNING. The messenger imports nothing from `models/`
    — the queue layer provides implementations that bump ORM fields.
    """

    # Callback to actually send the message (provided by bridge)
    _send_callback: Callable[[str], Awaitable[None]]

    # Chat context (for logging/tracking)
    chat_id: str = ""
    session_id: str = ""

    # Track sent messages
    messages_sent: list[MessageRecord] = field(default_factory=list)

    # === Liveness callbacks (issue #1036, #1269) ===
    # These are optional and ORM-free; the messenger invokes them blindly.
    on_sdk_started: Callable[[int], None] | None = None
    on_sdk_finished: Callable[[], None] | None = None
    on_heartbeat_tick: Callable[[], None] | None = None
    on_stdout_event: Callable[[], None] | None = None

    def notify_sdk_started(self, pid: int) -> None:
        """Invoke on_sdk_started(pid) if provided. Exceptions are logged WARNING."""
        cb = self.on_sdk_started
        if cb is None:
            return
        try:
            cb(pid)
        except Exception as e:
            logger.warning(
                "[%s] on_sdk_started callback raised (pid=%s): %s",
                self.session_id,
                pid,
                e,
            )

    def notify_sdk_finished(self) -> None:
        """Invoke on_sdk_finished() if provided. Exceptions are logged WARNING.

        Fires once per harness subprocess exit (#1269), symmetric with
        notify_sdk_started. The worker uses this to clear AgentSession.harness_pid
        the instant proc.communicate() returns, preventing the dashboard's
        liveness probe from reading a stale PID that has been recycled by a
        worker-spawned subprocess (gh/git/pytest/ruff/MCP) on a busy host.
        """
        cb = self.on_sdk_finished
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.warning(
                "[%s] on_sdk_finished callback raised: %s",
                self.session_id,
                e,
            )

    def notify_heartbeat_tick(self) -> None:
        """Invoke on_heartbeat_tick() if provided. Exceptions are logged WARNING."""
        cb = self.on_heartbeat_tick
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.warning(
                "[%s] on_heartbeat_tick callback raised: %s",
                self.session_id,
                e,
            )

    def notify_stdout_event(self) -> None:
        """Invoke on_stdout_event() if provided. Exceptions are logged WARNING."""
        cb = self.on_stdout_event
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.warning(
                "[%s] on_stdout_event callback raised: %s",
                self.session_id,
                e,
            )

    async def send(self, message: str, message_type: str = "result") -> bool:
        """
        Send a message to the supervisor.

        Args:
            message: The message content to send
            message_type: Type of message ("result", "error")

        Returns:
            True if sent successfully, False otherwise
        """
        if not message or not message.strip():
            logger.debug("Skipping empty message")
            return False

        try:
            await self._send_callback(message)

            record = MessageRecord(
                content=message[:200],  # Truncate for record
                timestamp=utc_now(),
                message_type=message_type,
            )
            self.messages_sent.append(record)

            logger.info(
                f"[{self.session_id}] Sent {message_type} message "
                f"({len(message)} chars) to chat {self.chat_id}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.session_id}] Failed to send message: {e}")
            return False

    def has_communicated(self) -> bool:
        """Check if any message has been sent to the supervisor."""
        return len(self.messages_sent) > 0

    def get_last_message_time(self) -> datetime | None:
        """Get timestamp of the last sent message."""
        if self.messages_sent:
            return self.messages_sent[-1].timestamp
        return None


class BackgroundTask:
    """
    Manages a background agent task with timeout watchdog.

    Handles:
    - Launching agent work without blocking
    - Internal health logging if work takes > timeout
    - Sending final result when complete
    """

    def __init__(
        self,
        messenger: BossMessenger,
        acknowledgment_timeout: float = 180.0,  # 3 minutes
        working_dir: str | None = None,
        project_key: str | None = None,
    ):
        """Construct a BackgroundTask.

        Args:
            messenger: BossMessenger used to deliver final results and
                liveness callbacks.
            acknowledgment_timeout: Reserved for future ack-timeout behavior;
                currently informational. Defaults to 180s.
            working_dir: Optional path the SDK subprocess was spawned with as
                its CWD. When provided, ``_watchdog`` checks each tick that
                the directory still exists and cancels the work task if it
                has vanished (issue #1357, #1246). When ``None`` or empty,
                the cwd-vanished check is skipped — preserves backward-compat
                for callers that don't yet thread ``working_dir`` through.
            project_key: Optional project key, used only as the prefix of the
                ``{project_key}:session-health:cwd_vanished`` Redis counter.
                Passed in from the caller (session_executor) so messenger.py
                stays ORM-free — the architectural boundary enforced by
                ``tests/unit/test_messenger_callbacks.py::
                TestMessengerArchitecturalBoundary``. When ``None``, the
                counter falls back to the bare ``session-health:cwd_vanished``
                key (mirrors the orphan-reap fallback shape).
        """
        self.messenger = messenger
        self.acknowledgment_timeout = acknowledgment_timeout
        # Issue #1357: track the SDK subprocess's CWD so the watchdog can
        # detect a vanished worktree mid-run. Empty string is treated as None
        # so callers can pass ``str(maybe_path or "")`` safely.
        self._working_dir: str | None = working_dir if working_dir else None
        self._project_key: str | None = project_key if project_key else None

        self._task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._result: str | None = None
        self._error: Exception | None = None

    async def run(
        self,
        coro: Awaitable[str],
        send_result: bool = True,
    ) -> None:
        """
        Run the coroutine as a background task.

        Args:
            coro: The async work to perform (should return a string result)
            send_result: Whether to automatically send the result when done
        """
        self._started_at = utc_now()

        # Start the main work
        self._task = asyncio.create_task(self._run_work(coro, send_result))

        # Start the watchdog
        self._watchdog_task = asyncio.create_task(self._watchdog())

        logger.info(f"[{self.messenger.session_id}] Background task started")

    async def _run_work(self, coro: Awaitable[str], send_result: bool) -> None:
        """Execute the work and handle completion.

        Note (hotfix #1055): Post-session memory extraction is NO LONGER called
        from here. It is scheduled by ``agent/session_executor.py::
        _schedule_post_session_extraction`` AFTER ``complete_transcript(...)``
        runs, as a fire-and-forget ``asyncio.create_task`` — so a hang in
        extraction cannot block session finalization or the dev→PM nudge.
        Do NOT re-introduce an ``await run_post_session_extraction(...)``
        block here: it would couple extraction latency to ``_run_work`` and
        regress the 6-hour stall observed in #1055.
        """
        try:
            self._result = await coro
            self._completed_at = utc_now()

            if send_result:
                if self._result:
                    await self.messenger.send(self._result, message_type="result")
                else:
                    # Empty result from harness: invoke the send_callback directly with ""
                    # so the router can apply nudge_empty or deliver_fallback logic.
                    # Without this, sessions that produce no output complete silently
                    # and the user never receives a final Telegram message.
                    try:
                        await self.messenger._send_callback("")
                    except Exception as _cb_err:
                        logger.debug(
                            "[%s] Empty-result router call failed: %s",
                            self.messenger.session_id,
                            _cb_err,
                        )

        except asyncio.CancelledError:
            # Shutdown-path handler (issue #1058, failure mode #3).
            #
            # `asyncio.CancelledError` inherits from `BaseException` and therefore
            # bypasses the plain `except Exception` below. Worker shutdown used to
            # leave the session "running" until startup-recovery re-queued it
            # (~5 minutes of silence on the user side). Here we best-effort
            # deliver a user-visible "I was interrupted" line and then re-raise
            # so asyncio shutdown semantics are preserved.
            #
            # Flap protection (plan Risk 6): a flapping worker (deploy loop,
            # OOM-kill cycling, health churn) would otherwise fire this handler
            # repeatedly. We gate the send on a Redis key
            # `interrupted-sent:{session_id}` with a 120s TTL via SET NX. Only
            # the caller that acquires the key sends. The TTL lets genuinely
            # distinct interruptions surface a fresh message after 2 minutes.
            self._completed_at = utc_now()
            try:
                _should_send = True
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

                    dedup_key = f"interrupted-sent:{self.messenger.session_id}"
                    acquired = POPOTO_REDIS_DB.set(dedup_key, "1", nx=True, ex=120)
                    if not acquired:
                        _should_send = False
                        logger.info(
                            "[%s] CancelledError interrupted-message suppressed (dedup key held)",
                            self.messenger.session_id,
                        )
                except Exception as _lock_err:
                    # Redis unavailable: fall through and send (duplicate is
                    # preferable to silence on a genuine interruption).
                    logger.debug(
                        "[%s] interrupted-sent dedup lock failed: %s",
                        self.messenger.session_id,
                        _lock_err,
                    )

                if _should_send:
                    try:
                        await asyncio.wait_for(
                            self.messenger._send_callback(
                                "I was interrupted and will resume automatically. No action needed."
                            ),
                            timeout=2.0,
                        )
                    except (TimeoutError, Exception) as _send_err:
                        logger.warning(
                            "[%s] CancelledError best-effort send failed: %s",
                            self.messenger.session_id,
                            _send_err,
                        )
            finally:
                # Cancel watchdog inside the handler so shutdown proceeds even
                # if the outer `finally` is skipped (shouldn't happen, but
                # defensive).
                if self._watchdog_task and not self._watchdog_task.done():
                    self._watchdog_task.cancel()
                raise  # preserve asyncio cancellation semantics

        except Exception as e:
            self._error = e
            self._completed_at = utc_now()

            err_str = str(e)
            if any(
                sig in err_str
                for sig in (
                    "Separator is not found, and chunk exceed the limit",
                    "Separator is found, but chunk is longer than limit",
                )
            ):
                logger.warning(
                    f"[{self.messenger.session_id}] Harness context overflow: {err_str[:120]}"
                )
                await self.messenger.send(
                    "Context too long — please resend your request.",
                    message_type="error",
                )
            else:
                logger.error(f"[{self.messenger.session_id}] Background task failed: {e}")
                await self.messenger.send(
                    f"I encountered an error: {str(e)[:200]}", message_type="error"
                )
        finally:
            # Cancel watchdog if still running
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()

    # Tunable for tests (issue #1357). Production stays at 60s; the
    # integration test monkeypatches this to 1s to exercise the
    # cwd-vanished branch without waiting two minutes in CI.
    HEARTBEAT_INTERVAL = 60  # seconds

    async def _watchdog(self) -> None:
        """
        Internal health check watchdog with periodic heartbeat.

        Emits a heartbeat log every ``HEARTBEAT_INTERVAL`` seconds while the
        SDK subprocess is running. This provides continuous liveness
        visibility instead of a single check at acknowledgment_timeout.
        Does not send any message to chat.

        After each heartbeat log, invokes `messenger.notify_heartbeat_tick()`
        so the queue layer can update `last_sdk_heartbeat_at` for the
        two-tier no-progress detector (issue #1036). Callback exceptions are
        caught inside `notify_heartbeat_tick` and do not crash the watchdog.

        CWD-vanished detection (issue #1357, ties to investigation #1246):
            When ``self._working_dir`` is set, each tick first checks that
            the directory still exists on disk. If it has been removed out
            from under the SDK subprocess (e.g. a sibling ``/do-merge`` ran
            ``post_merge_cleanup.py`` for the same slug, or someone did
            ``rm -rf`` manually), we log ``cwd_vanished``, increment
            ``{project_key}:session-health:cwd_vanished``, cancel the work
            task, and break the loop. The existing ``CancelledError`` handler
            in ``_run_work`` then sends "I was interrupted..." to the user.
        """
        heartbeat_interval = self.HEARTBEAT_INTERVAL
        elapsed = 0
        try:
            while self._task and not self._task.done():
                await asyncio.sleep(heartbeat_interval)
                elapsed += heartbeat_interval
                if self._task and not self._task.done():
                    # Issue #1357: detect vanished cwd before logging the
                    # heartbeat. If we don't, the heartbeat keeps writing
                    # "running Ns" while the SDK is silently wedged on a
                    # dead vnode (the macOS kernel does not signal the
                    # subprocess about its deleted cwd).
                    if self._working_dir and not os.path.isdir(self._working_dir):
                        logger.warning(
                            "[%s] cwd_vanished session_id=%s working_dir=%s",
                            self.messenger.session_id,
                            self.messenger.session_id,
                            self._working_dir,
                        )
                        self._increment_cwd_vanished_counter()
                        try:
                            self._task.cancel()
                        except Exception as cancel_err:
                            logger.debug(
                                "[%s] cwd_vanished cancel raised: %s",
                                self.messenger.session_id,
                                cancel_err,
                            )
                        break

                    communicated = self.messenger.has_communicated()
                    logger.info(
                        "[%s] SDK heartbeat: running %ds, communicated=%s",
                        self.messenger.session_id,
                        elapsed,
                        communicated,
                    )
                    # Two-tier no-progress detector callback (#1036).
                    self.messenger.notify_heartbeat_tick()
        except asyncio.CancelledError:
            # Normal cancellation when task completes
            pass
        except Exception as e:
            logger.error(f"[{self.messenger.session_id}] Watchdog error: {e}")

    def _increment_cwd_vanished_counter(self) -> None:
        """Bump the ``cwd_vanished`` Redis counter (issue #1357).

        Project-scoped counter: ``{project_key}:session-health:cwd_vanished``
        when the constructor was given a ``project_key``. Falls back to bare
        ``session-health:cwd_vanished`` when ``project_key`` is None
        (mirrors the orphan-reap counter pattern in
        ``agent/session_health.py::_increment_orphan_process_counter``).

        ``project_key`` is supplied by the caller (``session_executor``) at
        construction time so messenger.py stays ORM-free — see the
        architectural-boundary test in tests/unit/test_messenger_callbacks.py.

        All Redis errors are swallowed — the counter is observability, not
        correctness. Failing here must not crash the watchdog.
        """
        try:
            from popoto.redis_db import POPOTO_REDIS_DB as _R  # noqa: PLC0415

            if self._project_key:
                _R.incr(f"{self._project_key}:session-health:cwd_vanished")
            else:
                _R.incr("session-health:cwd_vanished")
        except Exception as e:
            logger.debug(
                "[%s] cwd_vanished counter increment failed (non-fatal): %s",
                self.messenger.session_id,
                e,
            )

    @property
    def is_running(self) -> bool:
        """Check if the task is still running."""
        return self._task is not None and not self._task.done()

    @property
    def is_complete(self) -> bool:
        """Check if the task has completed."""
        return self._completed_at is not None

    @property
    def result(self) -> str | None:
        """Get the result if complete."""
        return self._result

    @property
    def error(self) -> Exception | None:
        """Get the error if failed."""
        return self._error
