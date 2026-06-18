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
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agent.granite_container.container import Container, ContainerResult
from agent.granite_container.pty_pool import PairSpawnSpec, PTYPool
from agent.granite_container.transcript_tailer import (
    TranscriptTelemetry,
    read_transcript_telemetry,
)

logger = logging.getLogger(__name__)

# Default timeout for the synchronous bridge-callback delivery. The
# container's thread blocks for this long waiting on the asyncio
# future. 30s matches the Telegram relay's standard send timeout.
DEFAULT_DELIVERY_TIMEOUT_S = 30.0

# Transcript tailer tick interval (seconds). Each tick reads only new bytes
# since last tick (O(Δbytes)). Bounded by PTY pool size — total save
# volume is pool_size × 1 diff-gated save per tick. ~5s matches _bump_last_turn_at cadence.
_TAILER_INTERVAL_S: float = 5.0

# Cooldown window for startup-failure alerts. Per-machine Redis TTL key
# (cross-process layer) + process-local monotonic gate (Redis-down layer).
# Two layers: (1) Redis SET NX EX suppresses a cross-process alert storm
# on the same machine; (2) process-local dict is the fallback when Redis
# is itself the co-casualty of the outage.
_STARTUP_ALERT_COOLDOWN_S = 300  # 5 minutes
_STARTUP_ALERT_REDIS_KEY_PREFIX = "granite:startup_alert_cooldown"
# Process-local last-alert monotonic timestamps keyed by machine name.
# Module-level so it persists across calls within the same worker process.
_startup_alert_last_sent: dict[str, float] = {}


def _get_machine_name() -> str:
    """Return the machine name for cooldown key scoping."""
    try:
        from config.settings import settings

        return getattr(settings, "machine_name", None) or os.uname().nodename
    except Exception:
        return os.uname().nodename


def _should_alert(machine: str) -> bool:
    """Return True when a startup-failure alert is permitted for this machine.

    Inverted contract vs _dedup_set: returns True = send is permitted.
    Docstring states the inverted contract explicitly.

    Two-layer cooldown:
    (1) Process-local monotonic gate (checked FIRST -- always available).
        Returns False if last alert was within _STARTUP_ALERT_COOLDOWN_S.
        Updates the timestamp when returning True.
    (2) Cross-process Redis TTL key (only attempted if layer 1 permits).
        SET key NX EX <ttl>: if key already existed, suppress (return False).
        Redis unavailable: fall through to the layer-1 decision (send anyway --
        better a duplicate than a silenced outage). Redis-down does NOT log
        the suppression tag (the alert still sends, so suppression did not occur).
    """
    now = time.monotonic()
    last = _startup_alert_last_sent.get(machine, 0.0)
    if now - last < _STARTUP_ALERT_COOLDOWN_S:
        # Layer 1 (process-local): within cooldown window -- suppress.
        return False

    # Layer 1 permits. Try Redis cross-process gate.
    redis_key = f"{_STARTUP_ALERT_REDIS_KEY_PREFIX}:{machine}"
    try:
        from popoto.redis_db import POPOTO_REDIS_DB  # noqa: N811

        set_result = POPOTO_REDIS_DB.set(redis_key, "1", nx=True, ex=_STARTUP_ALERT_COOLDOWN_S)
        if not set_result:
            # Redis key already existed: another process already sent the alert.
            return False
        # Won the Redis gate. Update process-local timestamp.
        _startup_alert_last_sent[machine] = now
        return True
    except Exception:
        # Redis unavailable: fall through to process-local decision (send anyway).
        # Log a warning but NOT the suppression tag -- the alert still sends.
        logger.warning(
            "[bridge-adapter] Redis unavailable for startup alert cooldown check "
            "(machine=%s); proceeding with process-local gate only",
            machine,
        )
        _startup_alert_last_sent[machine] = now
        return True


def _send_startup_alert(session_id: str, failure_kind: str, frame_excerpt: str) -> None:
    """Best-effort valor-telegram notification for startup_unresolved exits.

    Fail-silent: all failure modes are swallowed so the notification never
    crashes the run. Fired only for startup_unresolved (not other anomalies)
    to avoid alert fatigue. Gated by _should_alert (two-layer cooldown).

    Subprocess call uses timeout=3 (NOT the precedent's timeout=10) to bound
    worker-thread blocking: during a fleet-wide outage this runs once per
    hung session, so a 10s block per session would compound the outage.
    The cooldown gate bounds this to one 3s subprocess per 5min per machine
    on the fast path; suppressed calls skip the subprocess entirely.

    Uses check=False (no CalledProcessError branch -- that exception is
    never raised under check=False; testing it would assert against
    unreachable code).
    """
    machine = _get_machine_name()
    if not _should_alert(machine):
        logger.error(
            "[granite-alert-suppressed] startup alert suppressed by cooldown "
            "(machine=%s session=%s kind=%s)",
            machine,
            session_id,
            failure_kind,
        )
        return

    # Truncate frame excerpt for the alert message.
    excerpt = frame_excerpt[:500].strip() if frame_excerpt else "(no frame)"
    message = (
        f"[granite-startup-failure] session={session_id} kind={failure_kind}\n"
        f"Frame excerpt:\n{excerpt}"
    )
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except FileNotFoundError:
        logger.error(
            "[granite-alert-suppressed] valor-telegram not on PATH; "
            "startup alert not sent (session=%s kind=%s)",
            session_id,
            failure_kind,
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "[granite-alert-suppressed] valor-telegram timed out; "
            "startup alert not sent (session=%s kind=%s)",
            session_id,
            failure_kind,
        )
    except Exception as exc:
        logger.error(
            "[granite-alert-suppressed] valor-telegram failed: %s; "
            "startup alert not sent (session=%s kind=%s)",
            exc,
            session_id,
            failure_kind,
        )


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for `session_events` entries."""
    return datetime.now(UTC).isoformat()


def _transcript_path_from_spec(cwd: str, session_id: str) -> str:
    """Compute the Claude Code JSONL transcript path from cwd + session UUID.

    Claude Code slugifies the cwd by replacing BOTH ``/`` and ``.`` with ``-``,
    so ``~/.claude/projects/{slug}/{uuid}.jsonl``. This is deterministic because
    we set the UUID via `claude --session-id`.

    The ``.`` substitution is load-bearing: every bridge session runs in a
    synthetic ``.worktrees/dev-{id}`` worktree, so the cwd always contains a
    dot (``.worktrees`` → ``--worktrees``). Replacing only ``/`` produced a
    path Claude Code never writes to, so the PM transcript read came back
    ``file-missing`` on every turn, every turn classified ``unknown``, and the
    run exhausted max_turns and shipped OPERATOR_TERMINAL_MESSAGE instead of the
    PM's real reply.
    """
    # Resolve symlinks before slugging so the slug matches Claude Code's
    # own realpath-based naming. Guard on truthiness: os.path.realpath("")
    # returns the process CWD, which would silently corrupt the slug.
    if cwd:
        cwd = os.path.realpath(cwd)
    slug = cwd.replace("/", "-").replace(".", "-")
    home = os.path.expanduser("~")
    return os.path.join(home, ".claude", "projects", slug, f"{session_id}.jsonl")


def _exception_is_benign(result: ContainerResult) -> bool:
    """Return True for soft exceptions that don't warrant an ERROR-level alert.

    Soft: the session had at least one classified turn AND recent last_turn_at
    activity (proxied by non-empty turns list). These are likely network blips or
    clean SIGTERM during idle.
    Hard: crashed before producing any output -> operator-actionable.
    """
    return bool(result.turns)


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
        session_type: str | None = None,
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
        # pm_system_prompt is no longer used — persona is delivered via prime
        # commands (issue #1692). Ignored if passed.
        if pm_system_prompt:
            import warnings

            warnings.warn(
                "pm_system_prompt is deprecated and has no effect. "
                "Persona is delivered via prime commands (issue #1692).",
                DeprecationWarning,
                stacklevel=2,
            )
        # D1-resolved PM model (session.model > settings > codebase
        # default). The Dev PTY intentionally has no per-session model
        # knob — it stays on GRANITE__DEV_MODEL via the spec default.
        self._pm_model = pm_model
        # session_type drives PM prime selection in the Container.
        # "teammate" → TEAMMATE_PRIME_SLASH_CMD; all others → PM_PRIME_SLASH_CMD.
        self._session_type = session_type
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
        # Path-B mid-run wedge detector (#1724): prior PTY buffer snapshot
        # used by _make_pty_read_callback to diff-gate last_pty_activity_at.
        # Reset at Container construction time (single-shot BridgeAdapter).
        self._prev_pty_buffer: str | None = None

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
            pm_session_id=pm_session_id,
            dev_session_id=dev_session_id,
        )
        async with self._pool.acquire_pair(spawn_spec=spawn_spec) as (pm, dev, pty_slot):
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
                on_pty_read=self._make_pty_read_callback(),
                pm_pty=pm,
                dev_pty=dev,
                session_type=self._session_type,
            )
            # Compute transcript paths for tailer (known at spawn time since we set the UUIDs).
            pm_path = _transcript_path_from_spec(working_dir, pm_session_id)
            dev_path = _transcript_path_from_spec(working_dir, dev_session_id)
            # Start the tailer task before handing the container to the worker thread.
            # The tailer reads new bytes from both transcripts every ~5s, merges counters,
            # and persists them to agent_session (diff-gated to avoid spurious saves).
            tailer_task = asyncio.create_task(
                self._run_tailer_task(pm_path, dev_path),
                name=f"transcript-tailer-{pm_session_id[:8]}",
            )
            try:
                # The container's run is sync (pexpect-driven). Run
                # it in a worker thread so the asyncio event loop
                # stays responsive to heartbeats, steering
                # injection, and watchdog ticks.
                result: ContainerResult = await asyncio.to_thread(container.run)
            finally:
                # Cancel the tailer; the container has exited so no more bytes
                # will appear. Await the cancellation so the task is cleaned up
                # before we write the exit summary.
                tailer_task.cancel()
                try:
                    await tailer_task
                except asyncio.CancelledError:
                    pass
            # Stamp the PTYPool slot index onto the result so it propagates
            # through _publish_exit_summary into agent_session.pty_slot.
            result.pty_slot = pty_slot
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
                "transcript_fallback_count": result.transcript_fallback_count,
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
                if result.pty_slot is not None:
                    self._agent_session.pty_slot = result.pty_slot
                    update_fields.append("pty_slot")
                # Persist startup failure diagnostic (issue #1710).
                if result.startup_failure_kind is not None:
                    self._agent_session.startup_failure_kind = result.startup_failure_kind
                    update_fields.append("startup_failure_kind")
                if result.startup_diagnostic_frame is not None:
                    # Size-cap the persisted frame to keep the Redis hash bounded.
                    _frame = result.startup_diagnostic_frame[:6000]
                    self._agent_session.startup_captured_frame = _frame
                    update_fields.append("startup_captured_frame")
                # Partial-data guard: if pm_pid is set but pty_slot is None,
                # the slot capture in acquire_pair may have regressed.
                if result.pm_pid is not None and result.pty_slot is None:
                    logger.warning(
                        "[bridge-adapter] pm_pid set but pty_slot is None — "
                        "slot capture may have regressed"
                    )
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=update_fields)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[bridge-adapter] exit summary field save failed: %s", e)

    def _maybe_publish_exit_anomaly(self, result: ContainerResult) -> None:
        """When the run ended on a hang / startup-unresolved / exception
        exit_reason, log (ERROR for hard exits, WARNING for soft exceptions)
        and append a session_events entry. This is the on-call path for
        kernel regressions at 3am — Sentry's default log-capture wiring picks
        up the logger.error, and the dashboard surfaces the session_events
        entry (hardens OPS-1).

        exception severity gating: soft exits (had turns -> likely network
        blip) log at WARNING (no Sentry alert). Hard exits (crashed before
        producing output) log at ERROR (Sentry captures).
        """
        anomaly_reasons = {
            "pm_hang",
            "dev_hang",
            "startup_unresolved",
            "pm_no_user_message",
            "exception",
        }
        if result.exit_reason not in anomaly_reasons:
            return

        # exception: classify before logging to avoid Sentry alert fatigue.
        if result.exit_reason == "exception" and _exception_is_benign(result):
            logger.warning(
                "[granite-exit-anomaly] session=%s exit_reason=%s exit_message=%s",
                getattr(self._agent_session, "session_id", "<no-id>"),
                result.exit_reason,
                result.exit_message,
            )
        else:
            logger.error(
                "[granite-exit-anomaly] session=%s exit_reason=%s exit_message=%s",
                getattr(self._agent_session, "session_id", "<no-id>"),
                result.exit_reason,
                result.exit_message,
            )
        # Build exit_anomaly event payload. For startup_unresolved, include
        # the diagnostic frame and failure kind so the dashboard shows the
        # diagnosis without needing to open the AgentSession record separately.
        anomaly_event: dict = {
            "type": "exit_anomaly",
            "exit_reason": result.exit_reason,
            "ts": _now_iso(),
        }
        if result.exit_reason == "startup_unresolved":
            if result.startup_failure_kind is not None:
                anomaly_event["startup_failure_kind"] = result.startup_failure_kind
            if result.startup_diagnostic_frame is not None:
                # Truncate the frame to ~1KB for the session_events payload.
                anomaly_event["startup_diagnostic_frame"] = result.startup_diagnostic_frame[:1000]
        _append_session_event(self._agent_session, anomaly_event)

        # Fire a direct operator alert for startup_unresolved (fail-silent).
        if result.exit_reason == "startup_unresolved":
            session_id = getattr(self._agent_session, "session_id", "<no-id>")
            _send_startup_alert(
                session_id=str(session_id),
                failure_kind=result.startup_failure_kind or "unknown",
                frame_excerpt=result.startup_diagnostic_frame or "",
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

    def _make_pty_read_callback(self) -> Callable[[str], None]:
        """Build the sync callable for PTY read-loop liveness stamps (#1724).

        # Spike-1 result: the parent PTY (PM or Dev TUI) renders its own
        # bottom bar, spinner animation, and elapsed-seconds counter as part
        # of the claude process's OWN terminal output — not from any child
        # subprocess. When a long Task subagent runs inside the Dev TUI, the
        # PARENT claude process is still alive, still consuming the child's
        # output, and its own TUI repaints the spinner+elapsed counter at >=1 Hz.
        # Therefore the parent PTY DOES keep repainting while a subagent is
        # running, and byte-quiescence (QUIESCENCE_S gap) is a reliable signal
        # that the parent TUI has genuinely gone quiet — not merely that a Task
        # subagent is running. The stage-1 gate (screen diff + MID_RUN_QUIESCENCE_SECS
        # window) correctly distinguishes a healthy deep-subagent run (repaint every
        # second) from a wedged run (no repaint for MID_RUN_QUIESCENCE_SECS).

        Returns a closure that:
        - Stamps ``last_pty_read_loop_at`` unconditionally on every call.
        - Stamps ``last_pty_activity_at`` only when ``buffer`` differs from
          the prior call's buffer (screen repainted).
        Both writes are fail-silent and use ``update_fields`` to avoid clobbering
        concurrent saves from the tailer task.
        """
        agent_session = self._agent_session

        def _on_pty_read(buffer: str) -> None:
            if agent_session is None:
                return
            try:
                now = datetime.now(UTC)
                update_fields = ["last_pty_read_loop_at"]
                agent_session.last_pty_read_loop_at = now
                # Diff-gate: only stamp activity when the buffer has changed.
                # NOTE: this compares the ANSI-stripped (but not cursor/spinner-normalized)
                # buffer from read_until_idle, not fully normalized bytes (per plan Key
                # Element 3, normalized bytes were specified to also strip cursor/blink noise).
                # The ANSI-stripped buffer is settled enough that this is safe for stage-1
                # observe-only, but full normalization will matter in stage-2 recovery: a
                # blinking cursor or spinner repaint that normalization would strip could keep
                # last_pty_activity_at fresh on a wedged screen, defeating quiescence detection.
                # Address this before wiring stage-2 recovery in the follow-up to #1724.
                if buffer != self._prev_pty_buffer:
                    agent_session.last_pty_activity_at = now
                    update_fields.append("last_pty_activity_at")
                self._prev_pty_buffer = buffer
                save = getattr(agent_session, "save", None)
                if callable(save):
                    save(update_fields=update_fields)
            except Exception as _e:
                logger.debug("[bridge-adapter] pty_read liveness stamp failed: %s", _e)

        return _on_pty_read

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
            # Emit typed routing event for dashboard feed
            _append_session_event(
                agent_session,
                {
                    "type": "granite_user_routed",
                    "event_type": "granite_user_routed",
                    "text": f"[/user] routed ({len(payload)} chars)",
                    "delivered": delivered,
                    "ts": _now_iso(),
                },
            )

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
            # Emit typed routing event for dashboard feed
            _append_session_event(
                agent_session,
                {
                    "type": "granite_complete_routed",
                    "event_type": "granite_complete_routed",
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
            # Production path: pexpect worker thread -> schedule on the
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
                "event_type": "granite_delivery_failure",
                "text": f"delivery failed: {reason} ({len(payload)} chars)",
                "type": "delivery_failure",
                "payload_chars": len(payload),
                "reason": reason,
                "ts": _now_iso(),
            },
        )

    # -- Transcript tailer (incremental telemetry, issue #1648) -----------

    async def _run_tailer_task(
        self,
        pm_transcript_path: str | None,
        dev_transcript_path: str | None,
    ) -> None:
        """Periodic transcript tailer: reads PM + Dev JSONL incrementally every ~5s.

        Runs as an asyncio.Task (started in run(), cancelled before exit summary).
        Persists via asyncio.to_thread so the blocking Redis save never stalls the loop.

        update_fields is strictly disjoint from _publish_exit_summary's set
        (excludes updated_at, last_turn_at) to avoid concurrent-write clobber.
        """
        pm_state = TranscriptTelemetry()
        dev_state = TranscriptTelemetry()
        tailer_fields = [
            "turn_count",
            "tool_call_count",
            "total_input_tokens",
            "total_output_tokens",
            "total_cache_read_tokens",
            "current_tool_name",
            "last_tool_use_at",
            "recent_thinking_excerpt",
        ]
        while True:
            try:
                await asyncio.sleep(_TAILER_INTERVAL_S)
                await self._tailer_tick(
                    pm_transcript_path,
                    dev_transcript_path,
                    pm_state,
                    dev_state,
                    tailer_fields,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[bridge-adapter] tailer tick error: %s", e)

    async def _tailer_tick(
        self,
        pm_transcript_path: str | None,
        dev_transcript_path: str | None,
        pm_state: TranscriptTelemetry,
        dev_state: TranscriptTelemetry,
        update_fields: list[str],
    ) -> None:
        """One tailer tick: read new transcript bytes, merge PM+Dev counters, persist if changed.

        Diff-gated: skips the save when turn/tool/token counts are unchanged since last tick.
        """
        if self._agent_session is None:
            return

        new_pm = read_transcript_telemetry(pm_transcript_path, pm_state)
        new_dev = read_transcript_telemetry(dev_transcript_path, dev_state)

        # Merge: sum counters, pick most recent tool/thinking across both
        merged_turns = new_pm.turn_count + new_dev.turn_count
        merged_tools = new_pm.tool_call_count + new_dev.tool_call_count
        merged_input = new_pm.total_input_tokens + new_dev.total_input_tokens
        merged_output = new_pm.total_output_tokens + new_dev.total_output_tokens
        merged_cache = new_pm.total_cache_read_tokens + new_dev.total_cache_read_tokens

        # Pick most recent current_tool_name (prefer dev if tied, since dev does the actual work)
        current_tool = new_dev.current_tool_name or new_pm.current_tool_name
        last_use_at = None
        if new_pm.last_tool_use_at and new_dev.last_tool_use_at:
            last_use_at = max(new_pm.last_tool_use_at, new_dev.last_tool_use_at)
        else:
            last_use_at = new_dev.last_tool_use_at or new_pm.last_tool_use_at
        thinking = new_dev.recent_thinking_excerpt or new_pm.recent_thinking_excerpt

        # Check if anything changed (diff gate — avoid pointless saves)
        prev_turns = getattr(self._agent_session, "turn_count", 0) or 0
        prev_tools = getattr(self._agent_session, "tool_call_count", 0) or 0
        prev_input = getattr(self._agent_session, "total_input_tokens", 0) or 0
        if merged_turns == prev_turns and merged_tools == prev_tools and merged_input == prev_input:
            # Update offsets even if no change, but don't save
            pm_state.byte_offset = new_pm.byte_offset
            dev_state.byte_offset = new_dev.byte_offset
            return

        # Update states with new offsets and telemetry
        pm_state.byte_offset = new_pm.byte_offset
        pm_state.turn_count = new_pm.turn_count
        pm_state.tool_call_count = new_pm.tool_call_count
        pm_state.total_input_tokens = new_pm.total_input_tokens
        pm_state.total_output_tokens = new_pm.total_output_tokens
        pm_state.total_cache_read_tokens = new_pm.total_cache_read_tokens
        pm_state.current_tool_name = new_pm.current_tool_name
        pm_state.last_tool_use_at = new_pm.last_tool_use_at

        dev_state.byte_offset = new_dev.byte_offset
        dev_state.turn_count = new_dev.turn_count
        dev_state.tool_call_count = new_dev.tool_call_count
        dev_state.total_input_tokens = new_dev.total_input_tokens
        dev_state.total_output_tokens = new_dev.total_output_tokens
        dev_state.total_cache_read_tokens = new_dev.total_cache_read_tokens
        dev_state.current_tool_name = new_dev.current_tool_name
        dev_state.last_tool_use_at = new_dev.last_tool_use_at

        try:
            self._agent_session.turn_count = merged_turns
            self._agent_session.tool_call_count = merged_tools
            self._agent_session.total_input_tokens = merged_input
            self._agent_session.total_output_tokens = merged_output
            self._agent_session.total_cache_read_tokens = merged_cache
            if current_tool is not None:
                self._agent_session.current_tool_name = current_tool
            if last_use_at is not None:
                # last_use_at is a raw ISO string from TranscriptTelemetry.last_tool_use_at.
                # AgentSession.last_tool_use_at is a DatetimeField — it requires a tz-aware
                # datetime object, not a string.  Parse here before assignment.
                try:
                    self._agent_session.last_tool_use_at = datetime.fromisoformat(
                        last_use_at.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass
            if thinking is not None:
                self._agent_session.recent_thinking_excerpt = thinking
            save = getattr(self._agent_session, "save", None)
            if callable(save):
                await asyncio.to_thread(save, update_fields=list(update_fields))
        except Exception as e:
            logger.warning("[bridge-adapter] tailer persist failed: %s", e)

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
