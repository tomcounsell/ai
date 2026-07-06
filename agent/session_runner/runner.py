"""SessionRunner: the single-session turn loop for ALL session types.

One top-level ``claude -p`` session per AgentSession (plan #1924,
D1-amended): per turn the runner spawns ONE subprocess — the PM session — in
the session's working dir; turn 1 primes via the role's prime slash command.
For eng work the PM spawns and continues its ``dev`` subagent *inside* its
own turn via the harness's agent mechanism; the parent ``-p`` process blocks
until the subagent finishes, so an eng turn containing a full Dev build is
legitimately long. There is no relay loop, no pool, no idle scraping.

Routing is the simplified regex table (:mod:`agent.session_runner.router`):

- ``[/user]``      → deliver via the adapter's user callback, exit ``pm_user``
- ``[/complete]``  → deliver the summary, exit ``pm_complete`` (wrap-up guard
  backstops an empty delivery)
- anything else    → continue (bounded compliance nudge, then the wrap-up
  guard — never an infinite loop)

Steer-preempt (D4): a per-turn watcher polls the steering list; on a
substantive steer it terminates the in-flight turn's process group
(SIGTERM → grace → SIGKILL, generation-token-guarded — Race 1), the loop
drains the steer at the boundary, and the next turn ``--resume``s with it
injected. **Timeout expiry is a graceful preempt, not an error**: the same
kill path fires with ``turn_end_source="timeout"``, partial work stays in
the transcript, and the user gets a persona-safe needs-attention message.

Async discipline: every ``create_task`` handle is held for its lifetime and
awaited (or cancelled + awaited) before the turn returns; a watcher
exception never kills the runner loop (logged warning, turn intact);
``CancelledError`` during shutdown is expected and never logged as an error;
every potentially-unbounded await carries a timeout (the driver's backstop
``asyncio.wait_for`` bounds the turn itself).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.session_runner.adapter import (
    RunSummary,
    SessionRunnerAdapter,
    _append_session_event,
    _now_iso,
)
from agent.session_runner.role_driver import (
    HeadlessRoleDriver,
    HeadlessTurnOutcome,
)
from agent.session_runner.router import (
    WRAPUP_ELIGIBLE_EXIT_REASONS,
    classify_pm_prefix,
    truncate_exit_message,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables — all provisional, all env-overridable (no bare magic numbers).
# ---------------------------------------------------------------------------

# Turn-loop safety cap: max PM turns before the wrap-up guard takes over.
# Provisional/tunable — override with SESSION_RUNNER_MAX_TURNS.
DEFAULT_MAX_TURNS: int = int(os.environ.get("SESSION_RUNNER_MAX_TURNS", "10"))

# Role-aware per-turn timeouts. Eng turns are generous by design — the Dev
# subagent's whole build runs INSIDE the PM turn (D1); this is an honest
# ceiling on a protocol that reports completion, not an idle guess. Expiry is
# a graceful preempt, never a hard error.
# Provisional/tunable — override with SESSION_RUNNER_ENG_TURN_TIMEOUT_S /
# SESSION_RUNNER_TEAMMATE_TURN_TIMEOUT_S.
ENG_TURN_TIMEOUT_S: float = float(os.environ.get("SESSION_RUNNER_ENG_TURN_TIMEOUT_S", "7200"))
TEAMMATE_TURN_TIMEOUT_S: float = float(
    os.environ.get("SESSION_RUNNER_TEAMMATE_TURN_TIMEOUT_S", "900")
)

# How often the preempt watcher polls the steering list during a turn.
# Provisional/tunable — override with SESSION_RUNNER_STEER_POLL_INTERVAL_S.
STEER_POLL_INTERVAL_S: float = float(os.environ.get("SESSION_RUNNER_STEER_POLL_INTERVAL_S", "2.0"))

# Debounce window: steers arriving within this many seconds of the first are
# batched into ONE preempt (prevents kill-thrash on rapid-fire steering).
# Provisional/tunable — override with SESSION_RUNNER_STEER_DEBOUNCE_S.
STEER_DEBOUNCE_S: float = float(os.environ.get("SESSION_RUNNER_STEER_DEBOUNCE_S", "3.0"))

# SIGTERM → SIGKILL escalation grace: how long the CLI gets to flush its
# transcript after SIGTERM before the group is SIGKILLed.
# Provisional/tunable — override with SESSION_RUNNER_PREEMPT_TERM_GRACE_S.
PREEMPT_TERM_GRACE_S: float = float(os.environ.get("SESSION_RUNNER_PREEMPT_TERM_GRACE_S", "10.0"))

# Poll cadence while waiting out the SIGTERM grace window.
# Provisional/tunable — override with SESSION_RUNNER_KILL_POLL_INTERVAL_S.
KILL_POLL_INTERVAL_S: float = float(os.environ.get("SESSION_RUNNER_KILL_POLL_INTERVAL_S", "0.2"))

# How many compliance nudges a non-routing PM gets before the loop hands off
# to the wrap-up guard. Provisional/tunable — override with
# SESSION_RUNNER_MAX_COMPLIANCE_NUDGES.
MAX_COMPLIANCE_NUDGES: int = int(os.environ.get("SESSION_RUNNER_MAX_COMPLIANCE_NUDGES", "1"))

# Margin added to the role timeout for the driver's own asyncio.wait_for
# backstop, so the watcher's graceful timeout-preempt always fires FIRST and
# the backstop only catches a watcher that itself failed.
# Provisional/tunable — override with SESSION_RUNNER_DRIVER_BACKSTOP_MARGIN_S.
DRIVER_BACKSTOP_MARGIN_S: float = float(
    os.environ.get("SESSION_RUNNER_DRIVER_BACKSTOP_MARGIN_S", "120.0")
)

# ---------------------------------------------------------------------------
# Persona-safe user-facing strings (no raw system text ever reaches the CEO).
# ---------------------------------------------------------------------------

# Corrective nudge sent as the next PM turn when the PM's reply carried no
# routable prefix (or an empty payload).
PM_COMPLIANCE_NUDGE = (
    "Your last reply did not start with a routing prefix on its own line. "
    "Re-send your reply starting with exactly one of [/user] or [/complete] "
    "on the first line, followed by the content."
)

# Wrap-up prompt: one extra PM turn to produce a user-facing message when the
# run ended without delivering one.
PM_WRAPUP_PROMPT = (
    "The session is wrapping up. Here is the latest report:\n\n{seed}\n\n"
    "Send your [/user] or [/complete] summary to the human now. "
    "Include the specific outcomes from the report above, not a generic "
    "acknowledgement."
)

# Seed used by the wrap-up guard when no prior turn text is available.
REPORT_UNAVAILABLE_SEED = "No report was captured for this session."

# Terminal fallback when the PM stays silent even after the wrap-up prompt.
OPERATOR_TERMINAL_MESSAGE = (
    "I wasn't able to produce a response to this. Please rephrase or follow up."
)

# Delivered when an operator aborts a running session via an is_abort steer.
STEER_ABORT_USER_MESSAGE = "Session stopped at your request."

# Needs-attention message for a timeout-preempted turn: the work is paused,
# not lost — the partial transcript remains the resume target.
TIMEOUT_NEEDS_ATTENTION_MESSAGE = (
    "This is taking longer than expected. I've paused the work and kept the "
    "progress so far. Reply to continue from where it left off."
)

# Persona-safe apology for a turn whose subprocess failed outright.
RUNNER_ERROR_USER_MESSAGE = (
    "I hit a problem finishing this and had to stop. Please try again or follow up."
)


def turn_timeout_for(session_type: str | None) -> float:
    """Role-aware per-turn timeout: teammate short, everything else generous.

    Eng/PM sessions carry the Dev subagent's work inside the PM turn (D1), so
    they get the generous ceiling; teammate sessions are conversational.
    """
    if (session_type or "").strip().lower() == "teammate":
        return TEAMMATE_TURN_TIMEOUT_S
    return ENG_TURN_TIMEOUT_S


@dataclass
class ResumeContext:
    """The four resume scalars (spike #1928) handed to the runner at init.

    Seam only in this task: the runner stores the context; consumption
    (seed ``--resume``, skip prime, re-introduce ``dev_agent_id``,
    capture-at-init) lands with build-resume (plan task 3).
    """

    claude_session_id: str | None = None
    dev_agent_id: str | None = None
    runner_cwd: str | None = None
    claude_version: str | None = None


@dataclass
class _TurnHandle:
    """Per-turn kill target for the preempt watcher (Race 1 + Race 2).

    ``generation`` is captured at spawn; the watcher only ever signals a
    process whose generation still equals the runner's current generation.
    ``pid``/``pgid`` are recorded by the driver's ``on_spawn`` callback the
    moment the subprocess exists — BEFORE the turn-await (Race 2).
    """

    generation: int
    pid: int | None = None
    pgid: int | None = None
    alive: bool = False
    killed: bool = False
    kill_cause: str | None = None  # "steer" | "timeout"


@dataclass
class _RouteDecision:
    """Internal result of routing one completed PM turn."""

    should_break: bool
    exit_reason: str | None = None
    next_message: str | None = None


def _default_pid_alive(pid: int) -> bool:
    """True when ``pid`` exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SessionRunner:
    """Single-session turn loop for one AgentSession, every session type.

    Construction (executor-facing, wired in the integrate task)::

        adapter = SessionRunnerAdapter(agent_session, project_key, "telegram")
        runner = SessionRunner(
            agent_session=agent_session,
            adapter=adapter,
            working_dir=working_dir,
            session_type=agent_session.session_type,
        )
        summary = await runner.run(user_message)

    Test seams: ``driver`` (a fake :class:`HeadlessRoleDriver`),
    ``steering_pop_fn`` (defaults to the Redis steering list),
    ``killpg_fn``/``kill_fn``/``pid_alive_fn`` (default os signals), and the
    timing knobs.
    """

    def __init__(
        self,
        *,
        agent_session: Any,
        adapter: SessionRunnerAdapter,
        working_dir: str,
        session_type: str | None = "eng",
        model: str | None = None,
        driver: HeadlessRoleDriver | None = None,
        harness_fn: Callable[..., Awaitable[str]] | None = None,
        resume: ResumeContext | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        turn_timeout_s: float | None = None,
        steering_pop_fn: Callable[[], list[dict]] | None = None,
        steer_poll_interval_s: float = STEER_POLL_INTERVAL_S,
        steer_debounce_s: float = STEER_DEBOUNCE_S,
        term_grace_s: float = PREEMPT_TERM_GRACE_S,
        killpg_fn: Callable[[int, int], None] | None = None,
        kill_fn: Callable[[int, int], None] | None = None,
        pid_alive_fn: Callable[[int], bool] | None = None,
        on_turn: Callable[[], None] | None = None,
    ) -> None:
        self._agent_session = agent_session
        self._adapter = adapter
        self._working_dir = working_dir
        self._session_type = session_type
        self._max_turns = max_turns
        self._turn_timeout_s = (
            turn_timeout_s if turn_timeout_s is not None else turn_timeout_for(session_type)
        )
        # Stored resume scalars — consumed by build-resume (task 3), not here.
        self._resume = resume
        self._steer_poll_interval_s = steer_poll_interval_s
        self._steer_debounce_s = steer_debounce_s
        self._term_grace_s = term_grace_s
        self._killpg = killpg_fn or os.killpg
        self._kill = kill_fn or os.kill
        self._pid_alive = pid_alive_fn or _default_pid_alive
        self._on_turn = on_turn
        # Per-turn progress state.
        self._generation = 0
        self._current_handle: _TurnHandle | None = None
        # Steers popped by the watcher (or the boundary drain) but not yet
        # injected into a turn.
        self._pending_steers: list[dict] = []
        self._last_reply_text = ""

        if steering_pop_fn is None:
            steering_pop_fn = self._default_steering_pop
        self._pop_steering = steering_pop_fn

        self._driver = driver if driver is not None else self._build_driver(model, harness_fn)

    # -- Construction helpers ----------------------------------------------

    def _build_driver(
        self,
        model: str | None,
        harness_fn: Callable[..., Awaitable[str]] | None,
    ) -> HeadlessRoleDriver:
        """Build the session's role driver over a provisioned hook channel."""
        role = "teammate" if (self._session_type or "").lower() == "teammate" else "pm"
        settings_path, edge_file = self._adapter.provision_hook_channel(role)
        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        return HeadlessRoleDriver(
            role=role,
            session_id=session_id,
            working_dir=self._working_dir,
            model=model,
            settings_path=settings_path,
            edge_file=edge_file,
            # The watcher's timeout-preempt fires FIRST; the driver's own
            # wait_for is only the backstop for a failed watcher.
            turn_timeout_s=self._turn_timeout_s + self._term_grace_s + DRIVER_BACKSTOP_MARGIN_S,
            harness_fn=harness_fn,
            on_spawn=self._on_turn_spawn,
            on_exit=self._on_turn_exit,
        )

    def _default_steering_pop(self) -> list[dict]:
        """Pop all pending steering messages for this session (Redis list)."""
        from agent.steering import pop_all_steering_messages  # noqa: PLC0415

        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        if not session_id:
            return []
        return pop_all_steering_messages(session_id)

    # -- Spawn/exit bookkeeping (Race 2) ------------------------------------

    def _on_turn_spawn(self, pid: int) -> None:
        """Record PID/PGID on the current turn handle + AgentSession record.

        Fires from the driver the moment the subprocess exists — before the
        turn-await blocks — so a worker crash mid-turn leaves a reapable
        record (Race 2). Fail-silent.
        """
        handle = self._current_handle
        if handle is None:
            return
        handle.pid = pid
        handle.alive = True
        try:
            handle.pgid = os.getpgid(pid)
        except Exception:  # noqa: BLE001 — fake pids in tests, races in prod
            handle.pgid = None
        _append_session_event(
            self._agent_session,
            {
                "type": "runner_turn_spawned",
                "generation": handle.generation,
                "pid": handle.pid,
                "pgid": handle.pgid,
                "ts": _now_iso(),
            },
        )
        try:
            if self._agent_session is not None:
                self._agent_session.pm_pid = pid
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=["pm_pid"])
        except Exception as e:  # noqa: BLE001
            logger.debug("[runner] pm_pid persist failed: %s", e)

    def _on_turn_exit(self) -> None:
        """Clear liveness on the current handle when the subprocess exits."""
        handle = self._current_handle
        if handle is not None:
            handle.alive = False

    # -- Public API ----------------------------------------------------------

    async def run(self, user_message: str) -> RunSummary:
        """Run the session's turn loop to a terminal state; publish the summary."""
        self._adapter.capture_event_loop()
        summary = RunSummary()
        message = user_message
        nudges = 0

        try:
            for _turn_index in range(self._max_turns):
                # -- Steering boundary drain (D4 + boundary case of Race 1) --
                steers, abort = self._drain_steering_boundary()
                if abort:
                    self._adapter.on_user_payload(STEER_ABORT_USER_MESSAGE)
                    summary.exit_reason = "steer_abort"
                    break
                if steers:
                    message = self._merge_steers(message, steers)

                outcome, handle = await self._run_one_turn(message)
                summary.turn_count += 1

                # -- Preempt outcomes (steer / timeout) ----------------------
                if handle.killed:
                    self._record_turn_event(
                        handle, turn_end_source=handle.kill_cause or "preempted"
                    )
                    if handle.kill_cause == "timeout":
                        # Graceful preempt, not an error: partial work stays
                        # in the transcript; surface needs-attention.
                        self._adapter.on_user_payload(TIMEOUT_NEEDS_ATTENTION_MESSAGE)
                        summary.exit_reason = "turn_timeout"
                        break
                    # Steer preempt: pending steers drain at the next
                    # boundary; resume with them injected.
                    message = ""
                    continue

                # -- Turn-level failures -------------------------------------
                if outcome.exit_reason == "empty_output" or not (outcome.reply_text or "").strip():
                    # Empty/whitespace-only PM turn → wrap-up guard, never an
                    # infinite loop (plan Failure Path).
                    summary.exit_reason = "pm_empty_turn"
                    break
                if outcome.exit_reason is not None:
                    summary.exit_reason = "error"
                    summary.exit_message = truncate_exit_message(outcome.exit_reason)
                    self._adapter.on_user_payload(RUNNER_ERROR_USER_MESSAGE)
                    break

                # -- Genuine turn end ----------------------------------------
                self._last_reply_text = outcome.reply_text
                self._record_turn_event(handle, turn_end_source=outcome.turn_end_source)
                self._fire_on_turn()

                decision = self._route_turn(outcome)
                if decision.should_break:
                    summary.exit_reason = decision.exit_reason or summary.exit_reason
                    break
                nudges += 1
                if nudges > MAX_COMPLIANCE_NUDGES:
                    # Non-routing PM exhausted its nudges — hand off to the
                    # wrap-up guard rather than burning the turn cap.
                    summary.exit_reason = "pm_max_turns"
                    summary.exit_message = "compliance nudges exhausted without a routable prefix"
                    break
                message = decision.next_message or PM_COMPLIANCE_NUDGE
            else:
                summary.exit_reason = "pm_max_turns"
                summary.exit_message = f"reached max_turns={self._max_turns} without a [/complete]"

            # -- Wrap-up guard (graduated): guarantee a user-facing message --
            summary.user_facing_routed = self._adapter.user_facing_routed
            wrapup_trigger = summary.exit_reason in WRAPUP_ELIGIBLE_EXIT_REASONS or (
                summary.exit_reason == "pm_empty_turn"
            )
            if wrapup_trigger and not summary.user_facing_routed:
                await self._run_wrapup_guard(summary)
        except Exception as e:  # noqa: BLE001 — terminal classification, never a crash
            logger.error("[runner] session loop raised: %s", e, exc_info=True)
            summary.exit_reason = "exception"
            summary.exit_message = truncate_exit_message(f"{type(e).__name__}: {e}")

        summary.user_facing_routed = self._adapter.user_facing_routed or summary.user_facing_routed
        self._adapter.publish_exit_summary(summary)
        return summary

    # -- One turn ------------------------------------------------------------

    async def _run_one_turn(self, message: str) -> tuple[HeadlessTurnOutcome, _TurnHandle]:
        """Dispatch one PM turn with its preempt watcher; settle both tasks."""
        self._generation += 1
        handle = _TurnHandle(generation=self._generation)
        self._current_handle = handle
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        turn_task = asyncio.create_task(self._driver.run_turn(message))
        watcher_task = asyncio.create_task(self._preempt_watcher(handle, turn_task, started_at))
        try:
            try:
                outcome = await turn_task
            except asyncio.CancelledError:
                if handle.killed:
                    # Killed before the subprocess existed (cooperative
                    # cancel) — synthesize a preempted outcome.
                    outcome = HeadlessTurnOutcome(turn_ended=False)
                else:
                    raise
        finally:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
            self._current_handle = None
        return outcome, handle

    # -- Preempt watcher (D4, Race 1) ----------------------------------------

    async def _preempt_watcher(
        self,
        handle: _TurnHandle,
        turn_task: asyncio.Task,
        started_at: float,
    ) -> None:
        """Poll steering + turn-timeout during one turn; kill on either.

        Generation-token guard (Race 1): this watcher only ever signals a
        process whose captured generation equals the runner's current
        generation AND whose turn task is still running — a steer landing as
        the turn completes naturally simply drains at the boundary that is
        already occurring.

        A failure inside the watcher must never kill the runner loop: every
        poll error is logged and swallowed; only CancelledError propagates.
        """
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self._steer_poll_interval_s)
            if turn_task.done() or handle.generation != self._generation:
                return
            # Timeout expiry → graceful preempt (never a hard error).
            if self._turn_timeout_s and (loop.time() - started_at) >= self._turn_timeout_s:
                await self._kill_turn(handle, turn_task, cause="timeout")
                return
            try:
                popped = self._pop_steering()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — watcher must not kill the loop
                logger.warning("[runner] preempt watcher poll error (turn intact): %s", e)
                continue
            substantive = [m for m in popped if self._steer_is_substantive(m)]
            if not substantive:
                # Empty steers drained mid-turn are ignored; the turn is NOT
                # killed (plan Failure Path).
                continue
            self._pending_steers.extend(substantive)
            if not any(m.get("is_abort") for m in substantive):
                # Debounce: batch steers arriving within the window into one
                # preempt (provisional 3s default).
                await asyncio.sleep(self._steer_debounce_s)
                try:
                    late = self._pop_steering()
                except Exception:  # noqa: BLE001
                    late = []
                self._pending_steers.extend(m for m in late if self._steer_is_substantive(m))
            if turn_task.done() or handle.generation != self._generation:
                return  # completed during debounce — steers drain at the boundary
            await self._kill_turn(handle, turn_task, cause="steer")
            return

    @staticmethod
    def _steer_is_substantive(msg: dict) -> bool:
        """True for a steer that warrants action (non-empty text or abort)."""
        return bool((msg.get("text") or "").strip()) or bool(msg.get("is_abort"))

    async def _kill_turn(self, handle: _TurnHandle, turn_task: asyncio.Task, cause: str) -> None:
        """SIGTERM → grace → SIGKILL the turn's process group.

        Signals the recorded PGID (own process group — covers the whole
        subprocess tree) or falls back to the bare PID; if no subprocess was
        ever spawned, cancels the turn task cooperatively. The SIGTERM grace
        lets the CLI flush its transcript so ``--resume`` continues from the
        partial turn (D4).
        """
        if turn_task.done() or handle.generation != self._generation or handle.killed:
            return
        handle.killed = True
        handle.kill_cause = cause
        logger.info(
            "[runner] preempting turn generation=%d cause=%s pid=%s pgid=%s",
            handle.generation,
            cause,
            handle.pid,
            handle.pgid,
        )
        if not self._signal_turn(handle, signal.SIGTERM):
            # No subprocess to signal (not spawned yet, or already gone):
            # cooperative cancel is the only lever left.
            turn_task.cancel()
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._term_grace_s
        while loop.time() < deadline:
            await asyncio.sleep(KILL_POLL_INTERVAL_S)
            if turn_task.done():
                return
            if handle.pid is not None and not self._pid_alive(handle.pid):
                return
        self._signal_turn(handle, signal.SIGKILL)

    def _signal_turn(self, handle: _TurnHandle, sig: int) -> bool:
        """Send ``sig`` to the turn's process group (or pid). Never raises."""
        try:
            if handle.pgid is not None:
                self._killpg(handle.pgid, sig)
                return True
            if handle.pid is not None:
                self._kill(handle.pid, sig)
                return True
        except ProcessLookupError:
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("[runner] signal %s failed: %s", sig, e)
            return False
        return False

    # -- Steering ------------------------------------------------------------

    def _drain_steering_boundary(self) -> tuple[list[dict], bool]:
        """Drain pending + fresh steers at the turn boundary.

        Returns ``(substantive_steers, abort)``. Empty steers are dropped.
        """
        steers = list(self._pending_steers)
        self._pending_steers = []
        try:
            steers.extend(self._pop_steering())
        except Exception as e:  # noqa: BLE001 — steering must not crash the loop
            logger.warning("[runner] boundary steering drain failed: %s", e)
        substantive = [m for m in steers if self._steer_is_substantive(m)]
        abort = any(m.get("is_abort") for m in substantive)
        return substantive, abort

    @staticmethod
    def _merge_steers(message: str, steers: list[dict]) -> str:
        """Compose the next turn's message from steer texts (+ prior message)."""
        texts = [(m.get("text") or "").strip() for m in steers]
        texts = [t for t in texts if t]
        joined = "\n\n".join(texts)
        if message and joined:
            return f"{message}\n\n{joined}"
        return joined or message

    # -- Routing (simplified table) -------------------------------------------

    def _route_turn(self, outcome: HeadlessTurnOutcome) -> _RouteDecision:
        """Route one completed PM turn: [/user] deliver, [/complete] wrap, else continue."""
        text = outcome.reply_text
        classification = classify_pm_prefix(text)

        if classification.destination == "user" and classification.payload:
            self._adapter.on_user_payload(classification.payload)
            return _RouteDecision(should_break=True, exit_reason="pm_user")

        if classification.destination == "complete":
            payload = classification.payload or ""
            if payload:
                self._adapter.on_complete_payload(payload)
            return _RouteDecision(should_break=True, exit_reason="pm_complete")

        # A substantive needs-human edge alongside an unroutable turn: the
        # hook already filtered boilerplate (#1919), so this message is a
        # genuine question for the human — deliver the PM's text.
        if outcome.needs_human is not None and text.strip():
            self._adapter.on_user_payload(text.strip())
            return _RouteDecision(should_break=True, exit_reason="pm_user")

        # Anything else — legacy [/dev], unknown prefix, empty payload —
        # continues the loop with a bounded compliance nudge.
        return _RouteDecision(should_break=False, next_message=PM_COMPLIANCE_NUDGE)

    # -- Wrap-up guard (graduated) ---------------------------------------------

    async def _run_wrapup_guard(self, summary: RunSummary) -> None:
        """Drive the PM to produce a user-facing message when none was delivered.

        One extra bounded PM turn seeded with the last report; a prefix-less
        but non-empty reply is floor-delivered (``pm_floor_delivered``); a
        silent PM gets the canned terminal message (``pm_no_user_message``).
        The human always receives something. Never raises.
        """
        try:
            seed = self._last_reply_text.strip() or REPORT_UNAVAILABLE_SEED
            outcome = await self._driver.run_turn(PM_WRAPUP_PROMPT.format(seed=seed))
            text = (outcome.reply_text or "").strip()
            if text:
                classification = classify_pm_prefix(text)
                if classification.destination == "user" and classification.payload:
                    self._adapter.on_user_payload(classification.payload)
                    summary.exit_reason = "pm_user"
                elif classification.destination == "complete" and classification.payload:
                    self._adapter.on_complete_payload(classification.payload)
                    summary.exit_reason = "pm_complete"
                else:
                    # Non-empty but prefix-less: deliver directly (relaxed
                    # floor) — OPERATOR_TERMINAL_MESSAGE is reserved for a
                    # genuinely silent PM.
                    self._adapter.on_user_payload(text)
                    summary.exit_reason = "pm_floor_delivered"
                summary.user_facing_routed = self._adapter.user_facing_routed
                if summary.user_facing_routed:
                    return
            if not self._adapter.user_facing_routed:
                self._adapter.on_user_payload(OPERATOR_TERMINAL_MESSAGE)
                summary.exit_reason = "pm_no_user_message"
                summary.user_facing_routed = self._adapter.user_facing_routed
        except Exception as e:  # noqa: BLE001 — the guard must never crash the run
            logger.warning("[runner] wrap-up guard raised unexpectedly: %s", e)

    # -- Observability ---------------------------------------------------------

    def _record_turn_event(self, handle: _TurnHandle, *, turn_end_source: str) -> None:
        """Append the per-turn record to session_events. Fail-silent."""
        _append_session_event(
            self._agent_session,
            {
                "type": "runner_turn",
                "generation": handle.generation,
                "turn_end_source": turn_end_source,
                "pid": handle.pid,
                "pgid": handle.pgid,
                "ts": _now_iso(),
            },
        )

    def _fire_on_turn(self) -> None:
        """Per-turn progress hook (liveness recency). Fail-silent."""
        if self._on_turn is None:
            return
        try:
            self._on_turn()
        except Exception as e:  # noqa: BLE001
            logger.warning("[runner] on_turn hook raised: %s", e)


# Re-exported for the executor wiring (task 4) and tests.
__all__ = [
    "DEFAULT_MAX_TURNS",
    "ENG_TURN_TIMEOUT_S",
    "MAX_COMPLIANCE_NUDGES",
    "OPERATOR_TERMINAL_MESSAGE",
    "PM_COMPLIANCE_NUDGE",
    "PM_WRAPUP_PROMPT",
    "PREEMPT_TERM_GRACE_S",
    "RUNNER_ERROR_USER_MESSAGE",
    "STEER_ABORT_USER_MESSAGE",
    "STEER_DEBOUNCE_S",
    "STEER_POLL_INTERVAL_S",
    "TEAMMATE_TURN_TIMEOUT_S",
    "TIMEOUT_NEEDS_ATTENTION_MESSAGE",
    "ResumeContext",
    "SessionRunner",
    "turn_timeout_for",
]
