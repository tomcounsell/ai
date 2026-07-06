"""Executor-facing adapter for the headless session runner.

The adapter is the boundary between the runner's turn loop and the session
harness (AgentSession persistence, Telegram delivery). Three
responsibilities:

1. **Resolve the registered send_cb once.** The adapter captures the
   registered ``TelegramRelayOutputHandler.send`` callback at construction
   time and stashes the chat_id, reply_to_msg_id, and agent_session for use
   during per-turn delivery. Delivery callbacks remain transport-keyed
   (telegram/email — the *channel*, resolved via
   ``agent_session_queue._resolve_callbacks``).

2. **Deliver mid-loop.** When the runner classifies a turn as ``[/user]``,
   the user-payload callback fires and blocks until the delivery is
   acknowledged; ``[/complete]`` fires the complete-payload callback with the
   trailing summary. Both callbacks write a ``session_events`` entry on
   failure so the dashboard surfaces the error, and fall back to the Redis
   outbox so a reply is never silently lost.

3. **Publish exit signals and resume scalars.** The adapter writes the
   exit-summary observability event and persists the four resume scalars
   (``claude_session_id`` / ``dev_agent_id`` / ``runner_cwd`` /
   ``claude_version`` — plan #1924, spike #1928). There is no
   ``resume_handles`` list: the four flat scalars are the entire resume
   contract.

**Defensive ``send_cb=None`` default**: if no callback is registered
(standalone worker, no bridge), the adapter logs a warning and the runner
still completes; payloads are logged, not delivered.

**Callback threading contract**: delivery callbacks may fire from a
non-asyncio thread. :meth:`SessionRunnerAdapter.capture_event_loop` must be
called on the worker's asyncio thread before the run; ``_deliver_sync``
schedules the async ``send_cb`` onto that captured loop via
``asyncio.run_coroutine_threadsafe(...).result(timeout=...)`` when called
from another thread, and falls back to fire-and-forget + outbox recovery
when already on the loop thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Default timeout for the synchronous delivery of a routed payload. Matches
# the Telegram relay's standard send timeout.
# Provisional/tunable — override with SESSION_RUNNER_DELIVERY_TIMEOUT_S.
DEFAULT_DELIVERY_TIMEOUT_S: float = float(
    os.environ.get("SESSION_RUNNER_DELIVERY_TIMEOUT_S", "30.0")
)

# TTL for outbox re-enqueue entries. Mirrors output_handler.py::OUTBOX_TTL
# so the bridge relay drains them within the same expiry window.
_OUTBOX_TTL = 3600


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for ``session_events`` entries."""
    return datetime.now(UTC).isoformat()


def _transcript_path_from_spec(cwd: str, session_id: str) -> str:
    """Compute the Claude Code JSONL transcript path from cwd + session UUID.

    Claude Code slugifies the cwd by replacing BOTH ``/`` and ``.`` with ``-``,
    so ``~/.claude/projects/{slug}/{uuid}.jsonl``.

    The ``.`` substitution is load-bearing: bridge sessions run in synthetic
    ``.worktrees/dev-{id}`` worktrees, so the cwd always contains a dot
    (``.worktrees`` → ``--worktrees``). Replacing only ``/`` produced a path
    Claude Code never writes to, so transcript reads came back file-missing
    on every turn.
    """
    # Resolve symlinks before slugging so the slug matches Claude Code's
    # own realpath-based naming. Guard on truthiness: os.path.realpath("")
    # returns the process CWD, which would silently corrupt the slug.
    if cwd:
        cwd = os.path.realpath(cwd)
    slug = cwd.replace("/", "-").replace(".", "-")
    home = os.path.expanduser("~")
    return os.path.join(home, ".claude", "projects", slug, f"{session_id}.jsonl")


def sidechain_agent_ids(
    cwd: str,
    claude_session_id: str,
    *,
    projects_root: str | None = None,
) -> list[str]:
    """Return subagent ids from the session's sidechain directory, oldest first.

    Structural ``dev_agent_id`` capture (plan #1924, Data Flow §7 / Race 5):
    Claude Code writes each subagent's sidechain transcript to
    ``~/.claude/projects/{cwd-slug}/{claude_session_id}/subagents/agent-*.jsonl``
    the moment the agent is SPAWNED — so a preempt mid-Dev-spawn still
    captures it. Agent ids are the filename stems; they are NEVER parsed
    from PM prose. Ordered by file mtime (oldest first — the newest id is
    the continuation target). Fail-silent: returns [] on any error.

    ``projects_root`` overrides ``~/.claude/projects`` for tests.
    """
    if not cwd or not claude_session_id:
        return []
    try:
        if projects_root is None:
            projects_root = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        real_cwd = os.path.realpath(cwd)
        slug = real_cwd.replace("/", "-").replace(".", "-")
        base = os.path.join(projects_root, slug, claude_session_id, "subagents")
        entries = []
        for name in os.listdir(base):
            if not (name.startswith("agent-") and name.endswith(".jsonl")):
                continue
            path = os.path.join(base, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            entries.append((mtime, name[: -len(".jsonl")]))
        entries.sort()
        return [agent_id for _, agent_id in entries]
    except OSError:
        return []
    except Exception as e:  # noqa: BLE001 — capture must never crash a turn
        logger.debug("[runner-adapter] sidechain scan failed: %s", e)
        return []


def sidechain_transcript_path(
    cwd: str,
    claude_session_id: str,
    agent_id: str,
    *,
    projects_root: str | None = None,
) -> str:
    """Path of one subagent's sidechain JSONL (for the turn-history mirror)."""
    if projects_root is None:
        projects_root = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    real_cwd = os.path.realpath(cwd) if cwd else cwd
    slug = real_cwd.replace("/", "-").replace(".", "-")
    return os.path.join(projects_root, slug, claude_session_id, "subagents", f"{agent_id}.jsonl")


def _hook_edge_base_dir() -> str:
    """Base directory for per-session hook settings + edge files.

    ``<data_dir>/session_runner_hook_edges`` when settings are available,
    else a temp dir. Per-session subdirs are created under it by
    :meth:`SessionRunnerAdapter.provision_hook_channel`.
    """
    try:
        from config.settings import settings

        return str(settings.paths.data_dir / "session_runner_hook_edges")
    except Exception:
        import tempfile

        return os.path.join(tempfile.gettempdir(), "session_runner_hook_edges")


def _append_session_event(agent_session, event: dict) -> None:
    """Append an observability event to ``agent_session.session_events``
    and persist it.

    ``AgentSession.session_events`` is a ``ListField(null=True)``, so a
    freshly created session has ``session_events is None`` rather than an
    empty list; this helper initializes the list when it is ``None`` so the
    entry is never lost, and persists with the model's documented
    partial-save pattern (``save(update_fields=["session_events",
    "updated_at"])``).

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
            "[runner-adapter] could not write session_event %s: %s",
            event.get("type"),
            e,
        )


@dataclass
class RunSummary:
    """Terminal summary of a runner session, published as ``exit_summary``.

    ``exit_reason`` uses the exit-classification vocabulary in
    :mod:`agent.session_runner.router`. ``user_facing_routed`` is True when
    at least one ``[/user]`` or non-empty ``[/complete]`` payload was
    delivered during the run (the adapter's delivery callbacks OR the
    runner's wrap-up guard may set it).
    """

    exit_reason: str = "in_progress"
    exit_message: str = ""
    turn_count: int = 0
    compliance_misses: int = 0
    user_facing_routed: bool = False


class SessionRunnerAdapter:
    """Delivery + persistence boundary for one runner session.

    Single-shot: one adapter instance, one runner run, one user-message.
    """

    def __init__(
        self,
        agent_session: Any,
        project_key: str,
        transport: str,
        resolve_callbacks: (Callable[[str, str], tuple[Callable | None, Any]] | None) = None,
        delivery_timeout_s: float = DEFAULT_DELIVERY_TIMEOUT_S,
    ) -> None:
        self._agent_session = agent_session
        self._project_key = project_key
        # ``transport`` is the delivery *channel* key (telegram/email) the
        # callback resolver is registered under — per repo convention. It is
        # NOT an execution-transport selector; there is one execution
        # transport and no seam.
        self._transport = transport
        self._delivery_timeout_s = delivery_timeout_s
        # The worker's event loop, captured via capture_event_loop() on the
        # asyncio thread before the run. Delivery callbacks may fire from a
        # non-asyncio thread, where asyncio.get_running_loop() raises — this
        # captured ref is the only way _deliver_sync can reach the loop.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Set to True by the user/complete callbacks when _deliver_sync
        # returns True (confirmed delivery). Propagated to
        # agent_session.user_facing_routed in publish_exit_summary so the
        # executor's emoji branch can distinguish a real delivery from a
        # session that was never routed to the user.
        self._user_facing_routed: bool = False

        # Resolve the bridge callback. The default resolver looks for a
        # registered agent_session_queue callback; tests can pass an
        # alternative resolver.
        if resolve_callbacks is None:
            resolve_callbacks = _default_resolve_callbacks
        self._send_cb, _state = resolve_callbacks(project_key, transport)

        # Capture chat_id and reply_to_msg_id once. The AgentSession may not
        # have these yet (e.g. tests construct a session without persisting
        # it); fall back to None and let the callback short-circuit.
        self._chat_id = getattr(agent_session, "chat_id", None)
        self._reply_to_msg_id = getattr(agent_session, "telegram_message_id", None)

        # If no callback is registered (standalone worker, no bridge), use a
        # logger-only no-op. The runner still completes; we just don't
        # deliver.
        if self._send_cb is None:
            self.on_user_payload: Callable[[str], None] = self._log_only_user
            self.on_complete_payload: Callable[[str], None] = self._log_only_complete
        else:
            self.on_user_payload = self._make_user_callback()
            self.on_complete_payload = self._make_complete_callback()

    # -- Wiring -------------------------------------------------------------

    def capture_event_loop(self) -> None:
        """Capture the running event loop for cross-thread delivery.

        Must be called on the worker's asyncio thread before the runner
        starts dispatching turns. No-op (logged) when no loop is running.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[runner-adapter] capture_event_loop: no running loop")

    def provision_hook_channel(self, name: str = "pm") -> tuple[str, str]:
        """Provision the per-session hook settings file + edge file.

        Returns ``(settings_path, edge_file_path)``. The edge file is keyed
        by the AgentSession's session_id (one top-level claude session per
        AgentSession); the forwarder receives the edge path as its CLI
        argument.
        """
        from agent.session_runner.hook_edge import generate_hook_settings  # noqa: PLC0415

        session_key = str(getattr(self._agent_session, "session_id", "") or name)
        base = os.path.join(_hook_edge_base_dir(), session_key)
        settings_dir = os.path.join(base, name)
        edge_file = os.path.join(base, f"{name}_hook_edges.ndjson")
        return generate_hook_settings(settings_dir, edge_file)

    # -- Resume scalars (four-scalar shape — plan #1924 / spike #1928) ------

    def persist_resume_scalars(
        self,
        *,
        claude_session_id: str | None = None,
        dev_agent_id: str | None = None,
        runner_cwd: str | None = None,
        claude_version: str | None = None,
    ) -> None:
        """Persist the four flat resume scalars on the AgentSession.

        - ``claude_session_id`` — the PM session UUID, the sole ``--resume``
          entry point (stored on the existing ``claude_session_uuid`` field).
        - ``dev_agent_id`` — the dev subagent continuation handle (captured
          structurally from the sidechain directory, never from PM prose).
        - ``runner_cwd`` — exact absolute working dir; resume is cwd-scoped.
        - ``claude_version`` — continuation behavior is version-specific.

        Upsert semantics: only non-None arguments are written; an existing
        value is never overwritten with None. Call this the moment a scalar
        becomes known — ``claude_session_id`` at the stream-json
        ``system/init`` event (capture-at-init, Race 5), ``dev_agent_id`` the
        turn Dev is first spawned (and again post-preempt). Fail-silent —
        persistence must never crash the run.
        """
        if self._agent_session is None:
            return
        scalars = {
            # claude_session_id reuses the pre-existing scalar field.
            "claude_session_uuid": claude_session_id,
            "dev_agent_id": dev_agent_id,
            "runner_cwd": runner_cwd,
            "claude_version": claude_version,
        }
        try:
            update_fields: list[str] = []
            for field_name, value in scalars.items():
                if value is None:
                    continue
                setattr(self._agent_session, field_name, value)
                update_fields.append(field_name)
            if not update_fields:
                return
            update_fields.append("updated_at")
            self._agent_session.updated_at = datetime.now(UTC)
            save = getattr(self._agent_session, "save", None)
            if callable(save):
                save(update_fields=update_fields)
        except Exception as e:  # noqa: BLE001
            logger.warning("[runner-adapter] resume-scalar persist failed: %s", e)

    # -- Session events ------------------------------------------------------

    def publish_exit_summary(self, summary: RunSummary) -> None:
        """Append the ``exit_summary`` ``session_events`` entry and persist
        the terminal fields.

        The dashboard's reflection sweep and Sentry log capture surface this
        entry; it is NOT delivered to Telegram. Also propagates the
        ``user_facing_routed`` flag (adapter deliveries OR the runner's
        wrap-up guard) onto agent_session so the executor's emoji branch can
        see it.
        """
        _append_session_event(
            self._agent_session,
            {
                "type": "exit_summary",
                "exit_reason": summary.exit_reason,
                "turns": summary.turn_count,
                "compliance_misses": summary.compliance_misses,
                "ts": _now_iso(),
            },
        )
        try:
            routed = self._user_facing_routed or summary.user_facing_routed
            if self._agent_session is not None:
                update_fields = ["updated_at"]
                if routed:
                    self._agent_session.user_facing_routed = routed
                    update_fields.append("user_facing_routed")
                # Persist exit_reason. Fail-silent: observability must never
                # crash the run.
                self._agent_session.exit_reason = summary.exit_reason
                update_fields.append("exit_reason")
                self._agent_session.updated_at = datetime.now(UTC)
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=update_fields)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[runner-adapter] exit summary field save failed: %s", e)

    @property
    def user_facing_routed(self) -> bool:
        """Whether a confirmed user-facing delivery happened this run."""
        return self._user_facing_routed

    # -- Delivery callbacks ---------------------------------------------------

    def _make_user_callback(self) -> Callable[[str], None]:
        """Build the callable for ``[/user]`` payloads."""
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
            # Emit typed routing event for the dashboard feed.
            _append_session_event(
                agent_session,
                {
                    "type": "runner_user_routed",
                    "event_type": "runner_user_routed",
                    "text": f"[/user] routed ({len(payload)} chars)",
                    "delivered": delivered,
                    "ts": _now_iso(),
                },
            )

        return _on_user

    def _make_complete_callback(self) -> Callable[[str], None]:
        """Build the callable for ``[/complete]`` payloads."""
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
            _append_session_event(
                agent_session,
                {
                    "type": "runner_complete_routed",
                    "event_type": "runner_complete_routed",
                    "text": f"[/complete] routed ({len(payload)} chars)",
                    "delivered": delivered,
                    "ts": _now_iso(),
                },
            )

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
        """Deliver a payload through send_cb, blocking until acknowledged.

        A sync send_cb is called directly on the calling thread. An async
        send_cb is scheduled on the captured worker loop
        (``capture_event_loop``): from a foreign thread via
        ``run_coroutine_threadsafe(...).result(timeout=...)``; from the loop
        thread itself as fire-and-forget with an outbox-recovery
        done-callback (blocking there would deadlock the loop).

        Returns True when the payload is delivered synchronously or
        re-enqueued to the outbox before returning; False when neither
        happened (sync send_cb raised, a pre-scheduling error occurred, or
        the same-thread fire-and-forget path where outbox recovery is
        deferred to a done-callback).

        On a missing/closed loop or a delivery timeout, the error is logged,
        the payload is re-enqueued to the Redis outbox, and a session_events
        entry is appended. The runner keeps going — a delivery failure never
        crashes the run.
        """
        import inspect  # noqa: PLC0415

        future = None
        coro = None  # closed explicitly on error paths to suppress RuntimeWarning
        # True once we have reached the async scheduling path. Used to
        # distinguish "sync send_cb raised" from "async loop closed/timed
        # out", since only the latter warrants an outbox re-enqueue.
        _async_delivery_started = False
        try:
            if not inspect.iscoroutinefunction(send_cb):
                # Sync send_cb (test doubles, legacy wrappers): call
                # directly on this thread.
                send_cb(chat_id, payload, reply_to, agent_session)
                return True

            loop = self._loop
            if loop is None or loop.is_closed():
                # capture_event_loop was never called or the worker loop is
                # gone (shutdown). Without a live loop there is nowhere to
                # schedule the coroutine. Re-enqueue to the outbox so the
                # reply is not lost (_enqueue_to_outbox is sync Redis).
                logger.warning(
                    "[runner-adapter] no captured event loop for send_cb; "
                    "re-enqueueing to outbox (loop=%r)",
                    loop,
                )
                recovered = self._enqueue_to_outbox(chat_id, payload, reply_to)
                self._record_delivery_event(payload, "no_event_loop", recovered=recovered)
                return recovered

            coro = send_cb(chat_id, payload, reply_to, agent_session)
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                # Same-thread call. Blocking on a future here would deadlock
                # the loop — schedule fire-and-forget instead. Delivery is
                # not confirmed synchronously, but the reply must still
                # never be silently lost: a done-callback re-enqueues to the
                # outbox iff the task failed or was cancelled. On success
                # the callback is a no-op, so there is no duplicate delivery.
                task = loop.create_task(coro)

                def _reenqueue_same_thread_on_failure(
                    t: asyncio.Task[Any],
                    _payload: str = payload,
                    _chat_id: Any = chat_id,
                    _reply_to: Any = reply_to,
                ) -> None:
                    try:
                        if t.cancelled():
                            recovered = self._enqueue_to_outbox(_chat_id, _payload, _reply_to)
                            self._record_delivery_event(
                                _payload, "CancelledError", recovered=recovered
                            )
                            return
                        exc = t.exception()
                        if exc is not None:
                            recovered = self._enqueue_to_outbox(_chat_id, _payload, _reply_to)
                            self._record_delivery_event(
                                _payload,
                                f"{type(exc).__name__}: {exc}",
                                recovered=recovered,
                            )
                    except Exception:  # pragma: no cover - defensive
                        logger.exception("[runner-adapter] same-thread re-enqueue callback failed")

                task.add_done_callback(_reenqueue_same_thread_on_failure)
                # Fire-and-forget: delivery is not yet confirmed, so return
                # False; the caller will not set user_facing_routed.
                return False
            # Cross-thread path: schedule on the captured worker loop and
            # block until delivered.
            _async_delivery_started = True
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.result(timeout=timeout_s)
            return True
        except RuntimeError as e:
            if not _async_delivery_started:
                # RuntimeError came from the sync send_cb call or
                # pre-scheduling code (e.g. constructing the coroutine). No
                # outbox fallback for non-delivery errors.
                if coro is not None:
                    coro.close()  # dispose unscheduled coroutine
                logger.warning("[runner-adapter] send_cb raised before async scheduling: %s", e)
                self._record_delivery_event(payload, f"{type(e).__name__}: {e}", recovered=False)
                return False
            # Loop closed between the check and the schedule (worker
            # shutdown race). The coroutine was never scheduled; close it
            # explicitly to suppress "never awaited" RuntimeWarning.
            coro.close()
            logger.warning("[runner-adapter] send_cb delivery failed (loop closed): %s", e)
            recovered = self._enqueue_to_outbox(chat_id, payload, reply_to)
            self._record_delivery_event(payload, f"{type(e).__name__}: {e}", recovered=recovered)
            return recovered
        except Exception as e:
            # Anything else (including FutureTimeoutError): log and continue.
            logger.warning(
                "[runner-adapter] send_cb delivery raised: %s (payload=%d chars)",
                e,
                len(payload),
            )
            # Cancel the timed-out future before re-enqueueing to minimise
            # the risk of duplicate delivery. Future.cancel() returns False
            # once the coroutine is already running — cancellation is
            # best-effort; the downstream redundancy filter
            # (bridge/redundancy_filter.py::should_suppress) is the backstop
            # for the already-running edge case.
            failure_reason = f"{type(e).__name__}: {e}"
            if future is not None and not future.cancel():
                failure_reason = f"{failure_reason} [future_uncancellable_possible_duplicate]"
            recovered = self._enqueue_to_outbox(chat_id, payload, reply_to)
            self._record_delivery_event(payload, failure_reason, recovered=recovered)
            return recovered
        return False

    def _record_delivery_event(self, payload: str, failure_reason: str, *, recovered: bool) -> None:
        """Append a ``session_events`` entry when a mid-loop delivery fails.

        If ``recovered`` is True the payload was re-enqueued to the outbox
        and will be delivered later — event type is
        ``runner_delivery_recovered_via_outbox``. If False the payload is
        permanently lost — event type is ``runner_delivery_dropped``.

        The dashboard surfaces the event; we do NOT emit a user-visible
        "message failed" reply (would violate the no-spam rule).
        """
        outcome = "recovered_via_outbox" if recovered else "dropped"
        _append_session_event(
            self._agent_session,
            {
                "event_type": f"runner_delivery_{outcome}",
                "text": f"delivery {outcome}: {failure_reason} ({len(payload)} chars)",
                "type": "delivery_failure",
                "payload_chars": len(payload),
                "reason": outcome,
                "failure_reason": failure_reason,
                "recovered": recovered,
                "ts": _now_iso(),
            },
        )

    def _enqueue_to_outbox(
        self,
        chat_id: Any,
        payload: str,
        reply_to: Any,
        file_paths: list[str] | None = None,
    ) -> bool:
        """Write payload to ``telegram:outbox:{session_id}`` as a fallback
        when the primary send_cb delivery times out or fails.

        Uses the same Redis list key and payload shape as
        ``output_handler.py`` so the bridge relay processes it identically.

        Returns True if the rpush succeeded, False otherwise.
        """
        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        if not session_id:
            logger.warning(
                "[runner-adapter] _enqueue_to_outbox: no session_id on agent_session, "
                "cannot re-enqueue (%d chars)",
                len(payload),
            )
            return False
        queue_key = f"telegram:outbox:{session_id}"
        outbox_payload: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_to": reply_to,
            "text": payload,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        if file_paths:
            outbox_payload["file_paths"] = file_paths
        try:
            from popoto.redis_db import POPOTO_REDIS_DB  # noqa: N811, PLC0415

            POPOTO_REDIS_DB.rpush(queue_key, json.dumps(outbox_payload))
            POPOTO_REDIS_DB.expire(queue_key, _OUTBOX_TTL)
            logger.info(
                "[runner-adapter] re-enqueued to outbox %s (%d chars)",
                queue_key,
                len(payload),
            )
            return True
        except Exception as e:
            logger.error(
                "[runner-adapter] _enqueue_to_outbox failed for %s: %s",
                queue_key,
                e,
            )
            return False

    # -- send_cb=None fallback ------------------------------------------------

    def _log_only_user(self, payload: str) -> None:
        logger.warning(
            "[runner-adapter] bridge callback missing — [/user] output "
            "will be logged but not delivered (payload=%d chars)",
            len(payload),
        )
        logger.info("[runner-adapter-user] %s", payload)

    def _log_only_complete(self, payload: str) -> None:
        logger.warning(
            "[runner-adapter] bridge callback missing — [/complete] output "
            "will be logged but not delivered (payload=%d chars)",
            len(payload),
        )
        logger.info("[runner-adapter-complete] %s", payload)


def _default_resolve_callbacks(project_key: str, transport: str) -> tuple[Callable | None, Any]:
    """Default resolver: defer to ``agent_session_queue._resolve_callbacks``.

    Imported lazily to avoid a hard dependency at module-import time (the
    agent_session_queue module may not exist in test environments)."""
    try:
        from agent.agent_session_queue import _resolve_callbacks
    except Exception:
        return (None, None)
    return _resolve_callbacks(project_key, transport)
