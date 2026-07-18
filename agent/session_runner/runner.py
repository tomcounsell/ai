"""SessionRunner: the single-session turn loop for ALL session types.

One top-level ``claude -p`` session per AgentSession (plan #1924,
D1-amended): per turn the runner spawns ONE subprocess — the PM session — in
the session's working dir; turn 1 primes via the role's prime slash command.
For eng work the PM spawns and continues its ``dev`` subagent *inside* its
own turn via the harness's agent mechanism; the parent ``-p`` process blocks
until the subagent finishes, so an eng turn containing a full Dev build is
legitimately long. There is no relay loop, no pool, no idle scraping.

Routing is schema-first (plan #2000 Task 2.3, :mod:`agent.session_runner.
router`): ``_classify_turn`` prefers the claude harness's ``--json-schema``-
validated ``structured_output`` (``{route, message, file_paths?}``) on
``HeadlessTurnOutcome``, falling back to the legacy prefix-regex parse only
when it is absent or invalid (emitting ``schema_routing_fallback``
telemetry). Either way the classification collapses to the same table:

- ``route: "user"``      → deliver via the adapter's user callback (with any
  ``file_paths``), exit ``pm_user``
- ``route: "complete"``  → deliver the summary (with any ``file_paths``),
  exit ``pm_complete`` (wrap-up guard backstops an empty delivery)
- needs_human edge on an unroutable turn → deliver the PM's text, exit
  ``pm_needs_human`` (distinct from a real ``user``-routed answer, see below)
- anything else (``route: "continue"``, legacy ``[/dev]``, unknown) →
  continue (bounded compliance nudge, then the wrap-up guard — never an
  infinite loop)

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
import re
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent.hooks.liveness_writers import is_in_cooldown
from agent.session_runner.adapter import (
    RunSummary,
    SessionRunnerAdapter,
    _append_session_event,
    _now_iso,
    sidechain_agent_ids,
    sidechain_transcript_path,
)
from agent.session_runner.role_driver import (
    HeadlessRoleDriver,
    HeadlessTurnOutcome,
)
from agent.session_runner.router import (
    SCHEMA_ROUTING_FALLBACK_EVENT,
    SCHEMA_ROUTING_FALLBACK_METRIC,
    SCHEMA_ROUTING_TURN_METRIC,
    WRAPUP_ELIGIBLE_EXIT_REASONS,
    ClassificationResult,
    ExitReason,
    classify_pm_prefix,
    truncate_exit_message,
    validate_structured_route,
)
from agent.session_runner.transcript_tailer import last_assistant_text

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

# Teardown reap confirm (issue #1938): after a SYNCHRONOUS SIGKILL of the turn's
# process group in ``_run_one_turn``'s ``finally``, poll for the group's exit for
# at most this many seconds. SIGKILL is uncatchable so death is near-instant in
# the common case; the cap only bounds the pathological unkillable/D-state group.
# The poll uses ``time.sleep`` (NOT ``await``) so a re-delivered ``CancelledError``
# — the recovery path double-cancels (#1938) — cannot abort the confirm.
# Provisional/tunable — override with SESSION_RUNNER_REAP_CONFIRM_TIMEOUT_S /
# SESSION_RUNNER_REAP_CONFIRM_POLL_S.
REAP_CONFIRM_TIMEOUT_S: float = float(
    os.environ.get("SESSION_RUNNER_REAP_CONFIRM_TIMEOUT_S", "1.0")
)
REAP_CONFIRM_POLL_S: float = float(os.environ.get("SESSION_RUNNER_REAP_CONFIRM_POLL_S", "0.02"))

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

# Per-entry cap for the turn-history mirror text (bounded observability +
# disaster-recovery seed; NEVER read on the normal resume path).
# Provisional/tunable — override with SESSION_RUNNER_TURN_HISTORY_MAX_CHARS.
TURN_HISTORY_MAX_CHARS: int = int(os.environ.get("SESSION_RUNNER_TURN_HISTORY_MAX_CHARS", "4000"))

# Validation shapes for persisted resume scalars (garbage → cold start).
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DEV_AGENT_ID_RE = re.compile(r"^agent-[A-Za-z0-9._-]{1,128}$")

# Prepended to the first resumed message when a dev_agent_id survives, so the
# PM continues the SAME dev agent across the restart (Success Criterion 3).
DEV_CONTINUATION_PREFIX = (
    "(Resuming session. Your dev agent {dev_agent_id} is still available from "
    "before the restart. Continue that SAME agent for developer work; do not "
    "spawn a new one.)\n\n"
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
    killed: bool = False
    kill_cause: str | None = None  # "steer" | "timeout"


@dataclass
class _RouteDecision:
    """Internal result of routing one completed PM turn.

    ``compliance_miss`` mirrors the classifier's flag so the run loop can
    accumulate ``RunSummary.compliance_misses``.
    """

    should_break: bool
    exit_reason: ExitReason | None = None
    next_message: str | None = None
    compliance_miss: bool = False


# Process-wide ``claude --version`` cache. "" is the cached-failure sentinel:
# a machine whose probe failed must not re-run the blocking subprocess on
# every turn.
_claude_version_cache: str | None = None


def _probe_claude_version() -> str | None:
    """Best-effort ``claude --version`` probe, cached process-wide.

    Used only when the stream-json init event carries no version field.
    Fail-silent — resume works without the version; it is a deploy-gate
    signal (Risk 5a), not a functional dependency. Failures are cached (""
    sentinel) so a broken binary costs at most one probe per process.

    Blocking (subprocess.run) — never call this on the event-loop thread
    with a cold cache; :meth:`SessionRunner._on_harness_init` dispatches it
    to a worker thread.
    """
    global _claude_version_cache
    if _claude_version_cache is not None:
        return _claude_version_cache or None
    try:
        proc = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=5.0, check=False
        )
        out = (proc.stdout or "").strip()
        _claude_version_cache = out.split()[0] if out else ""
    except Exception:  # noqa: BLE001
        _claude_version_cache = ""
    return _claude_version_cache or None


def _default_pid_alive(pid: int) -> bool:
    """True when ``pid`` exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _default_enum_subtree(pid: int) -> list[tuple[int, float]]:
    """Snapshot ``(pid, create_time)`` of every recursive descendant of ``pid``.

    The reap-escalation seam for issue #2146. Uses psutil so a ``setsid`` child
    — which escapes ``killpg`` of the harness group but keeps its ``ppid`` in the
    parentage tree — is still captured. MUST be called while ``pid`` is alive:
    once the parent dies its descendants reparent to ``launchd`` (ppid==1) and
    this walk can no longer reach them. Fail-silent → ``[]`` (psutil missing, pid
    already gone, access denied).
    """
    try:
        import psutil  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[int, float]] = []
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                out.append((child.pid, child.create_time()))
            except Exception:  # noqa: BLE001, S112 — child vanished mid-walk
                continue
    except Exception:  # noqa: BLE001 — parent gone / access denied
        return []
    return out


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
        session_env: dict[str, str] | None = None,
        driver: HeadlessRoleDriver | None = None,
        harness_fn: Callable[..., Awaitable[str]] | None = None,
        resume: ResumeContext | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        turn_timeout_s: float | None = None,
        steering_pop_fn: Callable[[], list[dict]] | None = None,
        steering_push_fn: Callable[[dict], None] | None = None,
        steer_poll_interval_s: float = STEER_POLL_INTERVAL_S,
        steer_debounce_s: float = STEER_DEBOUNCE_S,
        term_grace_s: float = PREEMPT_TERM_GRACE_S,
        killpg_fn: Callable[[int, int], None] | None = None,
        kill_fn: Callable[[int, int], None] | None = None,
        pid_alive_fn: Callable[[int], bool] | None = None,
        enum_subtree_fn: Callable[[int], list[tuple[int, float]]] | None = None,
        on_turn: Callable[[], None] | None = None,
        projects_root: str | None = None,
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
        self._enum_subtree = enum_subtree_fn or _default_enum_subtree
        self._on_turn = on_turn
        # Per-session subprocess env overlay (SESSION_TYPE for the
        # pre_tool_use PM Bash restrictions, AGENT_SESSION_ID for hook
        # attribution, CLAUDE_CODE_TASK_LIST_ID for task-list isolation,
        # VALOR_PARENT_SESSION_ID for child-session linking, Telegram/Sentry
        # auth). Merged under the driver's subscription-auth overlay (G5).
        self._session_env = dict(session_env) if session_env else None
        # Per-turn progress state.
        self._generation = 0
        self._current_handle: _TurnHandle | None = None
        # Steers popped by the watcher (or the boundary drain) but not yet
        # injected into a turn.
        self._pending_steers: list[dict] = []
        self._last_reply_text = ""

        # Test seam for the sidechain scan root (~/.claude/projects).
        self._projects_root = projects_root
        # Structural dev-agent tracking + turn-history dedup + version cache.
        self._dev_agent_id: str | None = None
        self._last_dev_history_text: str | None = None
        self._claude_version: str | None = None
        # _stamp_stdout_liveness's cooldown state lives entirely in
        # agent.hooks.liveness_writers' module-level bucket map (keyed by
        # f"{session_id}:stdout"), not on this instance — no per-instance
        # cooldown dict is needed since a SessionRunner is 1:1 with a single
        # session_id for its lifetime and the shared bucket key already
        # prevents two concurrently running SessionRunner instances
        # (distinct session_ids) from suppressing each other's stamp
        # (CRITIQUE pass 3 BLOCKER fix, preserved via bucket-keying).

        if steering_pop_fn is None:
            steering_pop_fn = self._default_steering_pop
        self._pop_steering = steering_pop_fn
        if steering_push_fn is None:
            steering_push_fn = self._default_steering_push
        self._push_steering = steering_push_fn

        self._driver = driver if driver is not None else self._build_driver(model, harness_fn)

        # -- Resume consumption (D3, four-scalar shape) ---------------------
        # Validate + consume the persisted scalars: seed --resume, skip
        # prime, remember dev_agent_id for reintroduction. ANY invalid
        # scalar discards the whole context and cold-starts with prime —
        # the stale-UUID fallback is the only other recovery tier.
        self._resume_active = False
        if resume is not None:
            reason = self._resume_invalid_reason(resume)
            if reason is None:
                self._driver.seed_resume(resume.claude_session_id)
                self._resume_active = True
                self._dev_agent_id = resume.dev_agent_id
                logger.info(
                    "[runner] resuming claude session %s (dev_agent_id=%s)",
                    resume.claude_session_id,
                    resume.dev_agent_id,
                )
            else:
                logger.warning(
                    "[runner] discarding resume scalars (%s) — cold start with prime",
                    reason,
                )

    def _resume_invalid_reason(self, ctx: ResumeContext) -> str | None:
        """Validate persisted resume scalars; return a reason or None (valid).

        Race 3: Claude session lookup is cwd-scoped — the stored
        ``runner_cwd`` must exist on disk AND match this runner's working
        dir, or ``--resume`` would silently miss. Garbage in any scalar
        discards the whole context (cold start, never a crash).
        """
        if not ctx.claude_session_id or not _UUID_RE.match(str(ctx.claude_session_id)):
            return f"malformed claude_session_id: {ctx.claude_session_id!r}"
        if not ctx.runner_cwd:
            return "missing runner_cwd (resume is cwd-scoped)"
        if not os.path.isdir(ctx.runner_cwd):
            return f"runner_cwd does not exist: {ctx.runner_cwd!r}"
        if os.path.realpath(ctx.runner_cwd) != os.path.realpath(self._working_dir):
            return (
                f"runner_cwd mismatch: stored {ctx.runner_cwd!r} != "
                f"working_dir {self._working_dir!r}"
            )
        if ctx.dev_agent_id is not None and not _DEV_AGENT_ID_RE.match(str(ctx.dev_agent_id)):
            return f"garbage dev_agent_id: {ctx.dev_agent_id!r}"
        return None

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
            env=self._session_env,
            settings_path=settings_path,
            edge_file=edge_file,
            # The watcher's timeout-preempt fires FIRST; the driver's own
            # wait_for is only the backstop for a failed watcher.
            turn_timeout_s=self._turn_timeout_s + self._term_grace_s + DRIVER_BACKSTOP_MARGIN_S,
            harness_fn=harness_fn,
            on_spawn=self._on_turn_spawn,
            on_stdout_event=self._on_stdout_event_liveness,
            on_init=self._on_init_composed,
        )

    def _on_stdout_event_liveness(self) -> None:
        """0-arg ``on_stdout_event`` adapter (issue #1935).

        The driver's ``on_stdout_event`` slot is 0-arg
        (``role_driver.py:175``); this delegates straight to
        :meth:`_stamp_stdout_liveness`.
        """
        self._stamp_stdout_liveness()

    def _on_init_composed(self, data: dict) -> None:
        """1-arg ``on_init`` adapter that COMPOSES with ``_on_harness_init``.

        HARD CONSTRAINT (issue #1935, CRITIQUE pass 2): ``_on_harness_init``
        persists ``claude_session_uuid``/``runner_cwd``/``claude_version`` and
        MUST keep doing so, unchanged. It early-returns when the init event
        carries no ``session_id`` and wraps its body in a try/except, so the
        liveness stamp must NOT be placed inside it — a stamp placed there
        would be silently skipped on exactly the events where "the init
        event is real output" matters most. This adapter therefore calls
        ``_on_harness_init`` first (preserving resume-scalar persistence
        byte-for-byte) and stamps liveness unconditionally afterward.
        """
        self._on_harness_init(data)
        self._stamp_stdout_liveness()

    def _stamp_stdout_liveness(self) -> None:
        """Stamp ``last_stdout_at`` on the AgentSession (issue #1935).

        This is the headless-runner replacement for the PTY-era
        ``last_pty_read_loop_at`` liveness signal (#1843 Gap B, deleted with
        the granite substrate): a per-stream-activity progress write so a
        toolless-streaming turn (``init`` fires, then assistant output with
        no tool call) is recognized as having produced output by
        ``agent.session_runner.liveness.derive_sdk_ever_output`` instead of
        being misclassified ``zombie_uuid_no_output`` past the never-started
        grace window.

        Fail-silent (never raises — a liveness-write failure must never
        crash or wedge the turn) with a per-session-keyed cooldown bounding
        the Redis write rate on a chatty stdout stream (Risk 2). Reuses
        ``agent.hooks.liveness_writers.is_in_cooldown`` (the same
        lock-protected, ``COOLDOWN_WINDOW_SEC``-bound bucket map the CLI
        hooks already share) under a distinct ``f"{session_id}:stdout"``
        bucket key, instead of maintaining a second cooldown implementation.
        """
        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        if not session_id:
            return
        if is_in_cooldown(f"{session_id}:stdout", time.time()):
            return
        try:
            if self._agent_session is not None:
                self._agent_session.last_stdout_at = datetime.now(tz=UTC)
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=["last_stdout_at"])
                    logger.debug("stdout_liveness_stamped session_id=%s", session_id)
        except Exception as e:  # noqa: BLE001 — liveness writes must never crash a turn
            logger.debug("[runner] stdout liveness stamp failed: %s", e)

    def _default_steering_pop(self) -> list[dict]:
        """Pop all pending steering messages for this session (Redis list)."""
        from agent.steering import pop_all_steering_messages  # noqa: PLC0415

        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        if not session_id:
            return []
        return pop_all_steering_messages(session_id)

    def _default_steering_push(self, msg: dict) -> None:
        """Push one steering message back onto this session's Redis list."""
        from agent.steering import push_steering_message  # noqa: PLC0415

        session_id = str(getattr(self._agent_session, "session_id", "") or "")
        if not session_id:
            return
        push_steering_message(
            session_id,
            msg.get("text") or "",
            sender=msg.get("sender") or "runner-requeue",
            is_abort=bool(msg.get("is_abort")),
        )

    def _requeue_pending_steers(self) -> None:
        """Re-push steers popped mid-turn but never injected into a turn.

        Fires on loop exit: a steer popped by the watcher during the
        debounce, when the turn then completes naturally and routes
        ``[/user]``/``[/complete]``, would otherwise be silently dropped —
        the executor's leftover-steering re-enqueue drains only the Redis
        list. Fail-silent.
        """
        if not self._pending_steers:
            return
        steers, self._pending_steers = self._pending_steers, []
        for msg in steers:
            try:
                self._push_steering(msg)
            except Exception as e:  # noqa: BLE001 — never crash the terminal path
                logger.warning(
                    "[runner] failed to re-push pending steer (%r dropped): %s",
                    (msg.get("text") or "")[:80],
                    e,
                )

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
                # Also surface the LIVE subprocess identity to the recovery path
                # (Fix 2, issue #1938): #1537's ``_confirm_subprocess_dead`` keys
                # on ``claude_pid``, which the headless-runner cutover left unset
                # — so the confirm no-op'd on ``None`` (a false "confirmed dead").
                # Set it here on spawn; ``_clear_claude_pid`` nulls it on turn
                # exit. Same-object write, no cross-module reach.
                self._agent_session.claude_pid = pid
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=["pm_pid", "claude_pid"])
        except Exception as e:  # noqa: BLE001
            logger.debug("[runner] pm_pid/claude_pid persist failed: %s", e)

    def _on_harness_init(self, data: dict) -> None:
        """Capture-at-init (Race 5): persist the new turn's resume scalars.

        Fires the moment the stream-json ``system/init`` event is parsed —
        BEFORE the turn's ``result`` — so a preempted or killed turn's
        partial transcript remains the resume target, never the stale
        pre-turn uuid. Persists ``claude_session_id`` + ``runner_cwd`` +
        ``claude_version`` together. Fail-silent.
        """
        try:
            sid = data.get("session_id")
            if not sid:
                return
            version = data.get("version") or data.get("claude_code_version")
            if version:
                self._claude_version = str(version)
            elif self._claude_version is None:
                if _claude_version_cache is not None:
                    # Cache warm (success or cached failure) — instant read.
                    self._claude_version = _claude_version_cache or None
                else:
                    # Cold cache: this callback fires on the harness's async
                    # stream-json read loop, and the probe blocks up to 5s —
                    # dispatch it to a worker thread and persist the version
                    # when it lands (upsert semantics; sid/cwd go out now).
                    self._schedule_version_probe()
            self._adapter.persist_resume_scalars(
                claude_session_id=str(sid),
                runner_cwd=self._working_dir,
                claude_version=self._claude_version,
            )
        except Exception as e:  # noqa: BLE001 — persistence must never crash a turn
            logger.warning("[runner] capture-at-init persist failed: %s", e)

    def _schedule_version_probe(self) -> None:
        """Run ``claude --version`` off-loop; persist the result when known.

        The probe result (including a failure) is cached process-wide, so
        this dispatches at most one real subprocess per worker process.
        Fail-silent — the version is a deploy-gate signal, never a
        functional dependency.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (sync test contexts): the blocking call is safe.
            self._claude_version = _probe_claude_version()
            return

        future = loop.run_in_executor(None, _probe_claude_version)

        def _adopt(fut: asyncio.Future) -> None:
            try:
                version = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.debug("[runner] off-loop version probe failed: %s", e)
                return
            if not version or self._claude_version is not None:
                return
            self._claude_version = version
            self._adapter.persist_resume_scalars(claude_version=version)

        future.add_done_callback(_adopt)

    # -- Public API ----------------------------------------------------------

    async def run(self, user_message: str) -> RunSummary:
        """Run the session's turn loop to a terminal state; publish the summary.

        On a resumed session (crash recovery, user reply-to, external
        ``valor-session resume``), ``user_message`` IS the reply/steer —
        it becomes the resumed session's first message, with the surviving
        ``dev_agent_id`` reintroduced so the PM continues the SAME dev agent.
        """
        self._adapter.capture_event_loop()
        summary = RunSummary()
        message = user_message
        if self._resume_active and self._dev_agent_id:
            message = DEV_CONTINUATION_PREFIX.format(dev_agent_id=self._dev_agent_id) + message
        nudges = 0

        try:
            for _turn_index in range(self._max_turns):
                # -- Steering boundary drain (D4 + boundary case of Race 1) --
                steers, abort = self._drain_steering_boundary()
                if abort:
                    self._adapter.on_user_payload(STEER_ABORT_USER_MESSAGE)
                    summary.exit_reason = ExitReason.STEER_ABORT
                    break
                if steers:
                    message = self._merge_steers(message, steers)

                outcome, handle = await self._run_one_turn(message)
                summary.turn_count += 1

                # Structural dev-agent capture — after each turn AND on
                # preempt (the sidechain file exists from spawn, so a
                # preempt mid-Dev-spawn is still captured; Race 5).
                self._capture_dev_state()

                # -- Preempt outcomes (steer / timeout) ----------------------
                if handle.killed:
                    source = "timeout" if handle.kill_cause == "timeout" else "preempted"
                    self._record_turn_event(handle, turn_end_source=source)
                    if handle.kill_cause == "timeout":
                        # Graceful preempt, not an error: partial work stays
                        # in the transcript; surface needs-attention.
                        self._adapter.on_user_payload(TIMEOUT_NEEDS_ATTENTION_MESSAGE)
                        summary.exit_reason = ExitReason.TURN_TIMEOUT
                        break
                    # Steer preempt: pending steers drain at the next
                    # boundary; resume with them injected.
                    message = ""
                    continue

                # -- Turn-level failures -------------------------------------
                failure = outcome.failure
                if failure is not None and failure.reason is not ExitReason.EMPTY_OUTPUT:
                    # Subprocess failure: never "completed" (the #1916 class).
                    # str(failure) reproduces the legacy "reason: detail" wire
                    # format, so exit_message telemetry is unchanged.
                    summary.exit_reason = ExitReason.ERROR
                    summary.exit_message = truncate_exit_message(str(failure))
                    self._adapter.on_user_payload(RUNNER_ERROR_USER_MESSAGE)
                    break
                if (failure is not None and failure.reason is ExitReason.EMPTY_OUTPUT) or not (
                    outcome.reply_text or ""
                ).strip():
                    # Empty/whitespace-only PM turn → wrap-up guard, never an
                    # infinite loop (plan Failure Path).
                    summary.exit_reason = ExitReason.PM_EMPTY_TURN
                    break

                # -- Genuine turn end ----------------------------------------
                self._last_reply_text = outcome.reply_text
                self._record_turn_event(handle, turn_end_source=outcome.turn_end_source)
                self._record_telemetry({"type": "turn_end", "source": outcome.turn_end_source})
                self._append_turn_history("pm", outcome.reply_text)
                self._fire_on_turn()

                decision = self._route_turn(outcome)
                if decision.compliance_miss:
                    summary.compliance_misses += 1
                if decision.should_break:
                    summary.exit_reason = decision.exit_reason or summary.exit_reason
                    break
                nudges += 1
                if nudges > MAX_COMPLIANCE_NUDGES:
                    # Non-routing PM exhausted its nudges — hand off to the
                    # wrap-up guard rather than burning the turn cap.
                    summary.exit_reason = ExitReason.PM_MAX_TURNS
                    summary.exit_message = "compliance nudges exhausted without a routable prefix"
                    break
                message = decision.next_message or PM_COMPLIANCE_NUDGE
            else:
                summary.exit_reason = ExitReason.PM_MAX_TURNS
                summary.exit_message = f"reached max_turns={self._max_turns} without a [/complete]"

            # -- Wrap-up guard (graduated): guarantee a user-facing message --
            summary.user_facing_routed = self._adapter.user_facing_routed
            wrapup_trigger = summary.exit_reason in WRAPUP_ELIGIBLE_EXIT_REASONS or (
                summary.exit_reason is ExitReason.PM_EMPTY_TURN
            )
            if wrapup_trigger and not summary.user_facing_routed:
                await self._run_wrapup_guard(summary)
        except Exception as e:  # noqa: BLE001 — terminal classification, never a crash
            logger.error("[runner] session loop raised: %s", e, exc_info=True)
            summary.exit_reason = ExitReason.EXCEPTION
            summary.exit_message = truncate_exit_message(f"{type(e).__name__}: {e}")

        # Steers popped mid-turn but never injected (the turn completed
        # naturally during the debounce and the loop exited) go back to the
        # Redis steering list — the executor's leftover-steering re-enqueue
        # drains only that list, so anything left here would be dropped.
        self._requeue_pending_steers()

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

        # turn_start telemetry unblocks the #1917 class: crash-signature
        # extraction treats a trace with no turn_start as deterministically
        # non-resumable — PTY sessions never emitted these events, so their
        # crash auto-resume was structurally dead. Runner sessions emit them.
        self._record_telemetry({"type": "turn_start", "generation": handle.generation})

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
            # -- Cancellation-proof teardown reap (issue #1938) ---------------
            # THE load-bearing gate for both defects. On ANY teardown of this
            # coroutine (external cancel from the recovery path, exception, or
            # normal exit) the turn's detached ``claude -p`` process group must
            # be positively reaped — a cancelled ``SessionHandle.task`` alone
            # unwinds the coroutine but never kills the group, orphaning a live
            # subprocess parented to the worker.
            #
            # Ordering guarantee: this reap is SYNCHRONOUS (SIGKILL + a bounded
            # ``time.sleep`` poll, no interruptible ``await``). The recovery path
            # double-cancels — ``handle.task.cancel()`` then
            # ``wait_for(handle.task, 0.25s)`` re-cancels on timeout — so a
            # SIGTERM→await-grace reap would be aborted mid-grace. A synchronous
            # SIGKILL cannot be interrupted. Because this inner-task ``finally``
            # runs to completion before ``await task._task``
            # (session_executor.py:1967) resolves in the OUTER coroutine, the
            # group is provably dead before both the recovery-path confirm and
            # the executor's worktree cleanup ``finally`` — the AC#2 ordering.
            #
            # The reap runs FIRST, then the existing watcher teardown, so a
            # re-delivered ``CancelledError`` lands on the suppressed watcher
            # await (below), never mid-reap.
            if not turn_task.done():
                turn_task.cancel()
            try:
                confirmed_dead, reap_pgid, reap_survivors = self._reap_turn_group(handle)
                if not confirmed_dead:
                    # Pathological unkillable/D-state group: emit ONE durable,
                    # operator-visible side effect. Fix 3's executor cleanup
                    # reads this marker and SKIPS worktree deletion, so no
                    # filesystem is mutated under a possibly-live child.
                    # Issue #2146: any per-PID survivors are persisted to the
                    # durable boot kill-list here too.
                    self._record_reap_failed(handle, reap_pgid, reap_survivors)
            except Exception as e:  # noqa: BLE001 — the reap must never raise
                logger.warning(
                    "[runner] teardown reap raised (generation=%d pid=%s): %s",
                    handle.generation,
                    handle.pid,
                    e,
                )
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
            # Clear the live subprocess identity (Fix 2): between turns there is
            # no live ``claude -p``, so a ``None`` read by the recovery path is
            # correct (nothing to kill). Fail-silent.
            self._clear_claude_pid()
            self._current_handle = None
        return outcome, handle

    def _reap_turn_group(
        self, handle: _TurnHandle
    ) -> tuple[bool, int | None, list[tuple[int, float, int | None]]]:
        """SYNCHRONOUSLY SIGKILL + confirm the turn's process group (issue #1938,
        with per-PID subtree escalation for issue #2146).

        Cancellation-proof by construction: no interruptible ``await`` anywhere.
        The recovery path double-cancels this coroutine, so a re-delivered
        ``CancelledError`` must not be able to abort the kill or the confirm —
        SIGKILL is uncatchable and issued with no preceding ``await``, and every
        poll uses ``time.sleep`` rather than ``asyncio.sleep``.

        Flow:
          1. **Snapshot the descendant subtree BEFORE killing** (issue #2146,
             load-bearing). A tool subprocess that ``setsid``'d into its own
             process group (an ``xdist``/``pytest`` suite, a ``pytest-clean.sh``
             wrapper) escapes ``killpg`` of the harness group entirely, but stays
             a descendant by ``ppid``. After the harness dies its children
             reparent to ``launchd`` and become unreachable, so the walk must run
             now, while ``pid`` is alive.
          2. ``killpg(pgid, SIGKILL)`` + bounded confirm poll, exactly as #1938.
             On the happy path (group killed and confirmed dead) return
             immediately — no per-PID sweep, no persistence.
          3. **On EPERM / unconfirmed group death only**, escalate: per-PID
             SIGKILL over the snapshot, then a bounded verify. Any straggler is
             returned as a ``(pid, create_time, pgid)`` survivor for the caller to
             persist to the durable boot kill-list.

        Returns ``(confirmed_dead, pgid, survivors)``. Signals go through the
        injected ``self._killpg`` / ``self._kill`` / ``self._pid_alive`` seams and
        the ``self._enum_subtree`` snapshot seam so unit tests drive the outcome
        with fakes. Never raises.
        """
        pid = handle.pid
        if pid is None:
            # No subprocess was ever spawned — nothing to reap.
            return True, None, []
        # Snapshot the descendant subtree while the harness is still alive.
        subtree = self._enum_subtree(pid)
        pgid = handle.pgid
        if pgid is None:
            try:
                pgid = os.getpgid(pid)
            except ProcessLookupError:
                # The leader is already gone → the group is gone.
                return True, None, []
            except Exception:  # noqa: BLE001 — fake pids in tests, races in prod
                # Own session/group under start_new_session (pgid == pid).
                pgid = pid
        # SIGKILL the whole group (no ``await`` — uninterruptible).
        group_kill_failed = False
        try:
            self._killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return True, pgid, []  # group already gone → confirmed dead
        except Exception as e:  # noqa: BLE001 — EPERM (recycled/foreign pgid, xeuid member)
            logger.warning("[runner] reap SIGKILL failed pgid=%s: %s", pgid, e)
            group_kill_failed = True

        # Confirm exit via a SYNCHRONOUS bounded poll (no ``await``). SIGKILL
        # death is near-instant, so the first probe returns dead in the common
        # case; the cap only bounds an unkillable/D-state group.
        deadline = time.monotonic() + REAP_CONFIRM_TIMEOUT_S
        group_dead = False
        while True:
            try:
                self._killpg(pgid, 0)
            except ProcessLookupError:
                group_dead = True  # group gone → confirmed dead
                break
            except Exception:  # noqa: BLE001, S110 — cannot probe; keep polling to the cap
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(REAP_CONFIRM_POLL_S)

        if group_dead and not group_kill_failed:
            # Happy path — group SIGKILL confirmed. No per-PID sweep, no
            # persistence (issue #2146 escalation is failure-only).
            return True, pgid, []

        # --- Escalation (issue #2146): EPERM or unconfirmed group death --------
        survivors = self._escalate_subtree(subtree, pgid)
        confirmed = group_dead and not survivors
        return confirmed, pgid, survivors

    def _escalate_subtree(
        self, subtree: list[tuple[int, float]], pgid: int | None
    ) -> list[tuple[int, float, int | None]]:
        """Per-PID SIGKILL sweep over the pre-kill subtree snapshot (issue #2146).

        Fires only when the group reap raised EPERM or could not confirm death.
        Each snapshot PID still alive is SIGKILLed per-PID (this is how a
        ``setsid`` child that escaped ``killpg`` is reached), then verified with a
        bounded poll. Stragglers still alive after the verify are returned as
        ``(pid, create_time, pgid)`` for persistence to the durable boot
        kill-list; the ``create_time`` rides along so the boot drain can guard
        against PID recycle. No ``await`` — cancellation-proof (#1938). Never
        raises.
        """
        if not subtree:
            return []
        for cpid, _ctime in subtree:
            if not self._pid_alive(cpid):
                continue
            try:
                self._kill(cpid, signal.SIGKILL)
            except ProcessLookupError:
                continue  # died between the liveness probe and the kill
            except Exception as e:  # noqa: BLE001
                logger.warning("[runner] reap per-PID SIGKILL failed pid=%s: %s", cpid, e)

        # Bounded verify (shares the #1938 confirm budget). Anything still alive
        # after the cap is handed off to the durable kill-list — the designed
        # boundary, not a bug to poll around.
        deadline = time.monotonic() + REAP_CONFIRM_TIMEOUT_S
        while True:
            still = [(cpid, ctime) for (cpid, ctime) in subtree if self._pid_alive(cpid)]
            if not still or time.monotonic() >= deadline:
                survivors: list[tuple[int, float, int | None]] = []
                for cpid, ctime in still:
                    logger.warning(
                        "[runner] reap subtree survivor pid=%s (pgid=%s) survived per-PID "
                        "SIGKILL — persisting to boot kill-list",
                        cpid,
                        pgid,
                    )
                    survivors.append((cpid, ctime, pgid))
                return survivors
            time.sleep(REAP_CONFIRM_POLL_S)

    def _record_reap_failed(
        self,
        handle: _TurnHandle,
        pgid: int | None,
        survivors: list[tuple[int, float, int | None]] | None = None,
    ) -> None:
        """Emit the durable ``runner_reap_failed`` marker + a WARNING (issue #1938),
        and persist any per-PID survivors to the boot kill-list (issue #2146).

        The session event is the deterministic side effect for a reap that could
        not confirm the group's death: it is durable (survives the process), so
        the executor's synthetic-slug cleanup reads it and skips deleting a
        worktree out from under a possibly-live child.

        Two distinct consumers of reap-failure state (see the plan): this
        ``runner_reap_failed`` marker → executor worktree-skip; the
        ``valor:reap:killlist`` Redis key → the boot/hourly orphan-reap drain that
        actually kills survivors. The ``survivor_pids`` marker field is additive
        observability only — the kill-list is the authoritative drain source.
        Fail-silent throughout.
        """
        survivors = survivors or []
        session_ref = getattr(self._agent_session, "agent_session_id", None) or getattr(
            self._agent_session, "session_id", "unknown"
        )
        logger.warning(
            "[runner] teardown reap could NOT confirm group death for session %s "
            "(generation=%d pid=%s pgid=%s survivors=%d) — worktree cleanup will be skipped",
            session_ref,
            handle.generation,
            handle.pid,
            pgid,
            len(survivors),
        )
        _append_session_event(
            self._agent_session,
            {
                "type": "runner_reap_failed",
                "generation": handle.generation,
                "pid": handle.pid,
                "pgid": pgid,
                "survivor_pids": [
                    {"pid": s[0], "create_time": s[1], "pgid": s[2]} for s in survivors
                ],
                "ts": _now_iso(),
            },
        )
        # Persist survivors for the next boot/hourly orphan-reap drain. The
        # kill-list is the authoritative source; ``add`` is best-effort and must
        # never crash the reap teardown.
        if survivors:
            try:
                from agent import reap_killlist  # noqa: PLC0415

                reap_killlist.add((s[0], s[1], s[2], session_ref) for s in survivors)
            except Exception as e:  # noqa: BLE001
                logger.debug("[runner] reap kill-list persist failed (non-fatal): %s", e)

    def _clear_claude_pid(self) -> None:
        """Clear the live subprocess identity on turn exit (Fix 2, issue #1938).

        Set on spawn by :meth:`_on_turn_spawn`; cleared here so the recovery
        path reads ``None`` between turns (no live subprocess to kill). Same-
        object write, fail-silent — persistence must never crash the run.
        """
        try:
            if self._agent_session is not None:
                self._agent_session.claude_pid = None
                save = getattr(self._agent_session, "save", None)
                if callable(save):
                    save(update_fields=["claude_pid"])
        except Exception as e:  # noqa: BLE001
            logger.debug("[runner] claude_pid clear failed: %s", e)

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

    def _classify_turn(self, outcome: HeadlessTurnOutcome) -> ClassificationResult:
        """Schema-first classification with a telemetered regex fallback.

        Prefers ``outcome.structured_output`` (validated by the claude
        harness's ``--json-schema``, plan #2000 Task 2.3); falls back to the
        prefix-regex parse when absent/invalid, emitting
        :data:`~agent.session_runner.router.SCHEMA_ROUTING_FALLBACK_EVENT`
        session telemetry AND the paired analytics counters
        (:func:`_record_schema_routing_metric`) that feed the fallback-rate
        alert threshold (``monitoring/schema_routing_alert.py``). Both paths
        record the turn-volume counter — a healthy schema path is ~0%
        fallback, not 0 recorded turns.
        """
        classification = validate_structured_route(outcome.structured_output)
        if classification is not None:
            self._record_schema_routing_metric(fallback=False)
            return classification

        self._record_schema_routing_metric(fallback=True)
        self._record_telemetry(
            {
                "type": SCHEMA_ROUTING_FALLBACK_EVENT,
                "raw_first_line": (outcome.reply_text or "").splitlines()[0][:200]
                if outcome.reply_text
                else "",
            }
        )
        return classify_pm_prefix(outcome.reply_text)

    def _record_schema_routing_metric(self, *, fallback: bool) -> None:
        """Best-effort analytics counters for the schema-routing fallback-rate
        alert (plan #2000 Task 2.3). Fail-silent — analytics must never
        affect routing.
        """
        try:
            from analytics.collector import record_metric  # noqa: PLC0415

            record_metric(SCHEMA_ROUTING_TURN_METRIC, 1.0)
            if fallback:
                record_metric(SCHEMA_ROUTING_FALLBACK_METRIC, 1.0)
        except Exception as e:  # noqa: BLE001
            logger.debug("[runner] schema-routing metric record failed: %s", e)

    def _route_turn(self, outcome: HeadlessTurnOutcome) -> _RouteDecision:
        """Route one completed PM turn: [/user] deliver, [/complete] wrap, else continue."""
        text = outcome.reply_text
        classification = self._classify_turn(outcome)
        miss = classification.compliance_miss

        if classification.destination == "user" and classification.payload:
            self._adapter.on_user_payload(classification.payload, classification.file_paths)
            return _RouteDecision(
                should_break=True, exit_reason=ExitReason.PM_USER, compliance_miss=miss
            )

        if classification.destination == "complete":
            payload = classification.payload or ""
            if payload:
                self._adapter.on_complete_payload(payload, classification.file_paths)
            return _RouteDecision(
                should_break=True, exit_reason=ExitReason.PM_COMPLETE, compliance_miss=miss
            )

        # A substantive needs-human edge alongside an unroutable turn: the
        # hook already filtered boilerplate (#1919), so this message is a
        # genuine question for the human — deliver the PM's text. Tagged
        # ``pm_needs_human`` (not ``pm_user``) since this is a runner-forwarded
        # needs-input prompt, not a real ``[/user]`` answer the PM chose to send.
        if outcome.needs_human is not None and text.strip():
            self._adapter.on_user_payload(text.strip())
            return _RouteDecision(
                should_break=True, exit_reason=ExitReason.PM_NEEDS_HUMAN, compliance_miss=miss
            )

        # Anything else — legacy [/dev], unknown prefix, empty payload —
        # continues the loop with a bounded compliance nudge.
        return _RouteDecision(
            should_break=False, next_message=PM_COMPLIANCE_NUDGE, compliance_miss=miss
        )

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
                classification = self._classify_turn(outcome)
                if classification.destination == "user" and classification.payload:
                    self._adapter.on_user_payload(classification.payload, classification.file_paths)
                    summary.exit_reason = ExitReason.PM_USER
                elif classification.destination == "complete" and classification.payload:
                    self._adapter.on_complete_payload(
                        classification.payload, classification.file_paths
                    )
                    summary.exit_reason = ExitReason.PM_COMPLETE
                else:
                    # Non-empty but prefix-less: deliver directly (relaxed
                    # floor) — OPERATOR_TERMINAL_MESSAGE is reserved for a
                    # genuinely silent PM.
                    self._adapter.on_user_payload(text)
                    summary.exit_reason = ExitReason.PM_FLOOR_DELIVERED
                summary.user_facing_routed = self._adapter.user_facing_routed
                if summary.user_facing_routed:
                    return
            if not self._adapter.user_facing_routed:
                self._adapter.on_user_payload(OPERATOR_TERMINAL_MESSAGE)
                summary.exit_reason = ExitReason.PM_NO_USER_MESSAGE
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

    def _record_telemetry(self, event: dict) -> None:
        """Append one event to the session's telemetry timeline. Fail-silent."""
        try:
            from agent.session_telemetry import record_telemetry_event  # noqa: PLC0415

            session_id = str(getattr(self._agent_session, "session_id", "") or "")
            if session_id:
                record_telemetry_event(session_id, event)
        except Exception as e:  # noqa: BLE001
            logger.debug("[runner] telemetry write failed: %s", e)

    def _append_turn_history(self, actor: str, text: str) -> None:
        """Mirror one turn's user-visible text onto the session-event stream.

        Bounded observability + disaster-recovery seed ONLY (owner mandate,
        plan #1924): full user-visible text, tool-noise excluded (the input
        is already the turn's final text), length capped by
        :data:`TURN_HISTORY_MAX_CHARS`. This mirror is NEVER read on the
        normal resume path — transcripts stay the source of truth
        (lossless-resume is the explicit rabbit hole). Fail-silent.
        """
        text = (text or "").strip()
        if not text:
            return
        _append_session_event(
            self._agent_session,
            {
                # Dual-keyed like the adapter's other dashboard-visible runner
                # events: ``event_type`` is the stream's canonical dashboard
                # key (SessionEvent convention, read by ui/data/sdlc.py's
                # _parse_history); ``type`` stays for the DR-seed filter.
                "type": "turn_history",
                "event_type": "turn_history",
                "actor": actor,
                "text": text[:TURN_HISTORY_MAX_CHARS],
                "ts": _now_iso(),
            },
        )

    def _capture_dev_state(self) -> None:
        """Structurally capture the dev subagent id + mirror its last report.

        Scans the sidechain directory under the CURRENT claude session id
        (the file exists from the moment of spawn — a preempt mid-Dev-spawn
        is still captured). Persists a newly-seen agent id via the
        four-scalar writer; NEVER parses ids from PM prose. Also mirrors the
        dev's latest user-visible text into the turn history. Fail-silent.
        """
        try:
            sid = getattr(self._driver, "claude_session_id", None)
            if not sid:
                return
            ids = sidechain_agent_ids(self._working_dir, sid, projects_root=self._projects_root)
            if not ids:
                return
            latest = ids[-1]
            if latest != self._dev_agent_id:
                self._dev_agent_id = latest
                self._adapter.persist_resume_scalars(dev_agent_id=latest)
            transcript = sidechain_transcript_path(
                self._working_dir, sid, latest, projects_root=self._projects_root
            )
            dev_text = last_assistant_text(transcript)
            if dev_text and dev_text != self._last_dev_history_text:
                self._last_dev_history_text = dev_text
                self._append_turn_history("dev", dev_text)
        except Exception as e:  # noqa: BLE001 — capture must never crash the loop
            logger.debug("[runner] dev-state capture failed: %s", e)


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
