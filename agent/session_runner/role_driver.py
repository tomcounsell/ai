"""Headless role driver: one ``claude -p`` subprocess per turn, per role.

:class:`HeadlessRoleDriver` runs a role (pm / dev / teammate) headlessly via
the :class:`~agent.session_runner.harness.claude.ClaudeHarnessAdapter`
(plan #2000 Task 2.2; the adapter wraps the preserved
:func:`agent.sdk_client.get_response_via_harness` harness). Turn-end is
reconciled from two protocol signals: a ``TURN_END`` hook envelope (the
``Stop`` hook, via :class:`~agent.session_runner.hook_edge.HookEdgeConsumer`)
when it lands, else the subprocess ``result`` / clean exit — a real,
well-defined boundary for a single-shot invocation. There is no idle
scraping and no PTY anywhere (protocol, not paint — see the package
docstring).

Auth posture (G5): the driver — not ambient worker env — owns subscription
auth for its subprocesses. :func:`subscription_auth_env` sets
``CLAUDE_CODE_OAUTH_TOKEN`` (vault-loaded into the process env at startup)
and strips ``ANTHROPIC_API_KEY`` / endpoint overrides so headless turns
always ride the Claude subscription. ``--bare`` must never be passed on the
role paths: it does not read ``CLAUDE_CODE_OAUTH_TOKEN``.
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent.session_runner.harness import events as harness_events
from agent.session_runner.harness.base import TurnEvent, TurnRequest
from agent.session_runner.harness.claude import ClaudeHarnessAdapter
from agent.session_runner.hook_edge import (
    COMPACTION,
    NEEDS_HUMAN,
    TURN_END,
    HookEdge,
    HookEdgeConsumer,
)
from agent.session_runner.router import PM_TURN_JSON_SCHEMA, ExitReason, TurnFailure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Headless prime-injection strategy. Both paths are implemented and
# unit-tested; the driver selects at build time. The DEFAULT is the
# slash-command path — empirically confirmed to RESOLVE under ``claude -p``
# (slash commands and skills are expanded before running; verified against
# the docs and live probes). The ``--append-system-prompt`` path stays as a
# documented contingency, selectable via ``prime_path=PRIME_PATH_APPEND`` if
# slash resolution ever regresses.
PRIME_PATH_SLASH = "slash_command"
PRIME_PATH_APPEND = "append_system_prompt"
DEFAULT_HEADLESS_PRIME_PATH = PRIME_PATH_SLASH

# The role prime command files, keyed by role. Invoked as
# ``/roles:prime-{pm,dev,teammate}-role`` (slash path) or read as the
# ``--append-system-prompt`` body (fallback path). Paths are repo-relative to
# the project root.
_PRIME_COMMAND_DIR = ".claude/commands/roles"
_PRIME_SLASH_BY_ROLE = {
    "pm": "/roles:prime-pm-role",
    "dev": "/roles:prime-dev-role",
    "teammate": "/roles:prime-teammate-role",
}
_PRIME_FILE_BY_ROLE = {
    "pm": "prime-pm-role.md",
    "dev": "prime-dev-role.md",
    "teammate": "prime-teammate-role.md",
}


def subscription_auth_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build the subprocess env overlay that pins subscription auth (G5).

    Returns a copy of ``base`` (or a fresh dict) with:

    - ``CLAUDE_CODE_OAUTH_TOKEN`` set from the process env (the vault
      ``~/Desktop/Valor/.env`` is loaded into the worker's environment at
      startup) when present;
    - ``ANTHROPIC_API_KEY`` blanked — headless role turns must never fall
      back to metered API-key auth;
    - ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` blanked — a shell
      that exports an ollama endpoint would otherwise silently redirect
      subscription-auth calls to a host with no Claude models.

    The overlay is merged over ``os.environ`` by the harness; blanking (not
    popping) is what actually overrides an inherited value.
    """
    env: dict[str, str] = dict(base) if base else {}
    env["ANTHROPIC_API_KEY"] = ""
    env["ANTHROPIC_BASE_URL"] = ""
    env["ANTHROPIC_AUTH_TOKEN"] = ""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def _read_prime_body(role: str, project_root: str | None = None) -> str:
    """Read the role's prime command file body for --append-system-prompt.

    Strips the YAML frontmatter (``---`` delimited) so only the persona body is
    injected. Returns "" if the file is missing (fail-soft — the driver still
    runs, just without a primed persona, which is observable in the reply).
    """
    filename = _PRIME_FILE_BY_ROLE.get(role, _PRIME_FILE_BY_ROLE["pm"])
    root = pathlib.Path(project_root) if project_root else pathlib.Path.cwd()
    path = root / _PRIME_COMMAND_DIR / filename
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("[role-driver] prime file missing: %s", path)
        return ""
    # Drop a leading YAML frontmatter block if present.
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return raw.strip()


def _slash_command_for(role: str) -> str:
    return _PRIME_SLASH_BY_ROLE.get(role, _PRIME_SLASH_BY_ROLE["pm"])


@dataclass
class HeadlessTurnOutcome:
    """Result of one headless actor turn.

    ``turn_ended`` is True when the turn reached a well-defined boundary;
    ``turn_end_source`` records which signal decided it ("hook_edge" for a
    ``TURN_END`` envelope, "result" for the subprocess clean exit fallback,
    "none" for a hung/failed turn). ``failure`` is None on success and a
    :class:`~agent.session_runner.router.TurnFailure` otherwise — a structured
    ``ExitReason`` plus free-form detail (feeds the runner's exit
    classification; ``str(failure)`` is the legacy wire format).

    ``structured_output`` (plan #2000 Task 2.3) mirrors
    :attr:`~agent.session_runner.harness.base.TurnResult.structured_output`
    verbatim — ``None`` when the schema-validated ``StructuredOutput`` tool
    call is absent (no schema requested, or the CLI's own validation gave
    up). The runner's router treats ``None`` as the fallback-to-regex signal.
    """

    reply_text: str = ""
    turn_ended: bool = False
    turn_end_source: str = "none"
    claude_session_id: str | None = None
    transcript_path: str | None = None
    needs_human: HookEdge | None = None
    compaction: HookEdge | None = None
    structured_output: dict[str, Any] | None = None
    failure: TurnFailure | None = None
    hung: bool = False
    metered: bool = True


class HeadlessRoleDriver:
    """Drives one role headlessly: one ``claude -p`` subprocess per turn.

    Drives the turn through :class:`~agent.session_runner.harness.claude.
    ClaudeHarnessAdapter` (plan #2000 Task 2.2), which owns all subprocess
    handling (argv assembly, stale-UUID retry, stream-json parsing, single
    metered token accumulation). This driver adds only: persona priming
    (first turn), --resume continuation (later turns), turn-end
    reconciliation against the hook edge file, claude-session-id capture, a
    bounded-wait hung-subprocess guard, and the explicit subscription-auth
    env overlay (G5). It never passes ``--bare``.
    """

    def __init__(
        self,
        *,
        role: str,
        session_id: str,
        working_dir: str,
        model: str | None = None,
        settings_path: str | None = None,
        edge_file: str | None = None,
        consumer: HookEdgeConsumer | None = None,
        env: dict[str, str] | None = None,
        prime_path: str = DEFAULT_HEADLESS_PRIME_PATH,
        project_root: str | None = None,
        turn_timeout_s: float = 600.0,
        full_context_message: str | None = None,
        harness_fn: Callable[..., Awaitable[str]] | None = None,
        on_stdout_event: Callable[[], None] | None = None,
        on_spawn: Callable[[int], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        on_init: Callable[[dict], None] | None = None,
    ) -> None:
        self.role = role
        self.session_id = session_id
        self.working_dir = working_dir
        self.model = model
        self.settings_path = settings_path
        self.edge_file = edge_file
        # G5: the subprocess env overlay always carries the explicit
        # subscription-auth posture, regardless of what the caller passes.
        self.env = subscription_auth_env(env)
        self.prime_path = prime_path
        self.project_root = project_root
        self.turn_timeout_s = turn_timeout_s
        self.full_context_message = full_context_message
        self._harness_fn = harness_fn
        self._on_stdout_event = on_stdout_event
        # Spawn/exit callbacks (Race 2): on_spawn(pid) fires as soon as the
        # subprocess pid is known — the runner records PID/PGID on the
        # AgentSession turn record BEFORE the turn-await; on_exit() fires when
        # the subprocess exits (clears per-turn pid tracking).
        self._on_spawn = on_spawn
        self._on_exit = on_exit
        # Caller's init-event observer (fires after the driver's own
        # capture-at-init bookkeeping below).
        self._on_init = on_init
        # A per-role HookEdgeConsumer over the same edge file the subprocess's
        # --settings hook set writes to (turn-end reconciliation). Constructed
        # lazily so callers can inject a fake consumer in tests.
        self._consumer = consumer
        if self._consumer is None and edge_file:
            self._consumer = HookEdgeConsumer(edge_file, session_id=None)
        # The current claude session UUID: seeded from persisted resume
        # scalars (seed_resume), then adopted by capture-at-init on every
        # turn (Race 5). Historically every ``--resume`` was assumed to fork
        # a NEW session id; plan #2000 Task 2.1's live probe (claude 2.1.207)
        # confirmed plain ``--resume`` REUSES the id instead. The id is still
        # adopted here each turn (a future CLI regressing to fork behavior
        # keeps working); ``_handle_init`` now asserts-and-alarms on drift
        # rather than silently expecting it. Drives --resume on later turns.
        self._claude_session_id: str | None = None
        self._transcript_path: str | None = None
        self._primed = False
        self._resume_seeded = False

    def seed_resume(self, claude_session_id: str) -> None:
        """Seed a validated persisted session UUID: --resume + skip prime.

        The caller (runner init) is responsible for validation — UUID shape,
        cwd-scoped lookup (Race 3). A seeded driver never re-primes: the
        persona is already in the resumed session's context. The stale-UUID
        fallback (retry once without --resume, full context) remains the
        only recovery tier; :meth:`run_turn` builds a prime-prefixed
        full-context message for it so a cold retry is still primed.
        """
        self._claude_session_id = claude_session_id
        self._transcript_path = _headless_transcript_path(self.working_dir, claude_session_id)
        self._primed = True
        self._resume_seeded = True

    def _handle_init(self, data: dict) -> None:
        """Capture-at-init (Race 5): two independent rationales, one guard each.

        Fires the moment the stream-json ``system/init`` event is parsed —
        BEFORE the turn's ``result``. Two assignments happen here, and they
        are NOT collapsed behind one guard (plan #2000 Definitions,
        "Capture-at-init"):

        1. ``self._transcript_path`` is retargeted UNCONDITIONALLY on every
           init event — mid-turn-preempt safety: a preempted/killed turn's
           *partial* transcript must be the resume target, never the stale
           pre-turn uuid. This rationale is independent of resume-id
           behavior and is untouched by the finding below.
        2. ``self._claude_session_id`` is still adopted from the observed id
           every turn (so a future CLI that resumes forking behavior keeps
           working), but a mismatch against the previously-expected id is
           now assert-and-alarm: plan #2000 Task 2.1's live probe (claude
           2.1.207) empirically confirmed plain ``--resume`` REUSES the
           session id rather than forking it, so a drift is now an anomaly
           worth an error-level log (Sentry capture) keyed to the session's
           persisted ``claude_version``, not silent expectation.

        Never raises.
        """
        try:
            sid = data.get("session_id")
            if sid:
                sid = str(sid)
                expected = self._claude_session_id
                if expected is not None and expected != sid:
                    logger.error(
                        "[role-driver] claude session id drift on --resume: "
                        "expected %s, observed %s (role=%s, session_id=%s) — "
                        "plain --resume was expected to be stable (plan #2000 "
                        "Task 2.1 probe, claude 2.1.207); adopting the new id "
                        "and continuing",
                        expected,
                        sid,
                        self.role,
                        self.session_id,
                    )
                self._claude_session_id = sid
                # Mid-turn-preempt safety (independent rationale, see
                # docstring point 1): always retarget, regardless of drift.
                self._transcript_path = _headless_transcript_path(self.working_dir, sid)
        except Exception:  # noqa: BLE001
            logger.debug("[role-driver] init-event capture failed", exc_info=True)
        if self._on_init is not None:
            try:
                self._on_init(data)
            except Exception as e:  # noqa: BLE001
                logger.warning("[role-driver] on_init observer raised: %s", e)

    # -- Captured session identity (feeds the four-scalar resume persistence)
    @property
    def claude_session_id(self) -> str | None:
        """The captured claude session UUID (None before the first turn)."""
        return self._claude_session_id

    @property
    def transcript_path(self) -> str | None:
        """The derived JSONL transcript path (None before the first turn)."""
        return self._transcript_path

    # -- Turn-end reconciliation helpers ----------------------------------
    def _snapshot_edges(self) -> float:
        """Drain already-written edges and return a monotonic-ish snapshot ts.

        Draining before each spawn (Race 4): a stale ``Stop`` from a prior
        sequential headless turn is consumed here so it cannot end the NEXT
        turn. We also return the max ``ts`` seen so the post-spawn poll honors
        only envelopes that postdate this snapshot.
        """
        snapshot_ts = time.time()
        if self._consumer is None:
            return snapshot_ts
        try:
            drained = self._consumer.poll()
        except Exception:  # noqa: BLE001
            return snapshot_ts
        for e in drained:
            if e.ts and e.ts > snapshot_ts:
                snapshot_ts = e.ts
        return snapshot_ts

    def _reconcile_turn_end(
        self, snapshot_ts: float, claude_session_id: str | None
    ) -> tuple[HookEdge | None, HookEdge | None, HookEdge | None]:
        """Poll the edge file once and return (turn_end, needs_human, compaction).

        Only ``TURN_END`` envelopes that postdate ``snapshot_ts`` (Race 4) and
        match ``claude_session_id`` (when both are known) are honored.

        #1919 ordering: when a fresh ``turn_end`` and a ``needs_human`` edge
        arrive in the same poll batch, the ``turn_end`` wins and the
        ``needs_human`` is suppressed — the completed turn's real answer must
        be delivered, never preempted by a trailing notification. (This
        inverts the pre-cutover ordering bug that swallowed the PM's
        ``[/user]`` answer behind an idle notification.)
        """
        if self._consumer is None:
            return (None, None, None)
        try:
            edges = self._consumer.poll()
        except Exception:  # noqa: BLE001
            return (None, None, None)
        turn_end: HookEdge | None = None
        needs_human: HookEdge | None = None
        compaction: HookEdge | None = None
        for e in edges:
            if e.kind == TURN_END:
                if e.ts and e.ts <= snapshot_ts:
                    continue  # stale (Race 4)
                if claude_session_id and e.session_id and e.session_id != claude_session_id:
                    continue
                turn_end = e  # last matching wins
            elif e.kind == NEEDS_HUMAN and needs_human is None:
                needs_human = e
            elif e.kind == COMPACTION and compaction is None:
                compaction = e
        if turn_end is not None:
            # Prefer turn_end over needs_human within one batch (#1919).
            needs_human = None
        return (turn_end, needs_human, compaction)

    # -- Prime injection --------------------------------------------------
    def _prime_args(self, message: str) -> tuple[str, str | None]:
        """Return (message, system_prompt) with first-turn priming applied.

        Slash path (default): prepend the role's prime slash command to the
        first message. Append path: inject the prime command body via
        ``--append-system-prompt`` (system_prompt), leaving the message intact.
        """
        if self._primed:
            return (message, None)
        if self.prime_path == PRIME_PATH_SLASH:
            slash = _slash_command_for(self.role)
            return (f"{slash} {message}" if message else slash, None)
        # append-system-prompt contingency path.
        body = _read_prime_body(self.role, self.project_root)
        return (message, body or None)

    def _dispatch_turn_event(self, event: TurnEvent) -> None:
        """Translate one normalized :class:`TurnEvent` into this driver's
        pre-seam callback surface (plan #2000 Task 2.2).

        ``SESSION_STARTED`` calls :meth:`_handle_init` with the raw init
        event dict exactly as the pre-extraction ``on_init`` callback did
        (preserving capture-at-init timing, Race 1/5). The others fan out
        to the caller-supplied 0/1-arg observers unchanged.
        """
        if event.type == harness_events.SESSION_STARTED:
            self._handle_init(event.data.get("raw", {}))
        elif event.type == harness_events.TURN_SPAWNED:
            if self._on_spawn is not None:
                self._on_spawn(event.data.get("pid"))
        elif event.type == harness_events.TURN_EXITED:
            if self._on_exit is not None:
                self._on_exit()
        elif event.type == harness_events.ITEM_STDOUT:
            if self._on_stdout_event is not None:
                self._on_stdout_event()

    # -- Public per-turn API ----------------------------------------------
    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        """Run one headless turn end-to-end and return a classified outcome."""
        import asyncio  # noqa: PLC0415

        from agent.sdk_client import HarnessThinkingBlockCorruptionError  # noqa: PLC0415

        # Injectable per-call (not cached at __init__) so tests that
        # reassign `driver._harness_fn` between turns (e.g. Race 4 coverage)
        # keep working through the adapter seam unchanged.
        adapter = ClaudeHarnessAdapter(harness_fn=self._harness_fn)

        turn_message, system_prompt = self._prime_args(message)
        prior_uuid = self._claude_session_id  # None on first turn

        # Stale-UUID fallback context: when this turn rides --resume and the
        # caller supplied no full-context message, build a prime-prefixed one
        # so the harness's cold retry (the only recovery tier, D3) starts a
        # fresh session WITH the persona instead of unprimed.
        full_context_message = self.full_context_message
        if prior_uuid and full_context_message is None and self.prime_path == PRIME_PATH_SLASH:
            slash = _slash_command_for(self.role)
            full_context_message = f"{slash} {message}" if message else slash

        # Race 4: drain stale edges BEFORE spawning this turn's subprocess.
        snapshot_ts = self._snapshot_edges()

        outcome = HeadlessTurnOutcome()
        try:
            turn_result = await asyncio.wait_for(
                adapter.run_turn(
                    TurnRequest(
                        message=turn_message,
                        working_dir=self.working_dir,
                        env=self.env,
                        prior_uuid=prior_uuid,
                        session_id=self.session_id,
                        full_context_message=full_context_message,
                        model=self.model,
                        system_prompt=system_prompt,
                        settings_path=self.settings_path,
                        role=self.role,
                        # Own process group (Race 2 + D4): the preempt watcher
                        # signals the whole subprocess tree via killpg, and the
                        # worker orphan sweep reaps survivors after a crash.
                        start_new_session=True,
                        # Schema-first routing (plan #2000 Task 2.3): every
                        # top-level role turn (pm/teammate) requests a
                        # schema-validated StructuredOutput tool call. The
                        # runner's router prefers it; absence (schema
                        # validation failure) demotes to the prefix-regex
                        # fallback.
                        json_schema=PM_TURN_JSON_SCHEMA,
                    ),
                    on_event=self._dispatch_turn_event,
                ),
                timeout=self.turn_timeout_s,
            )
            reply = turn_result.final_text
            # Exit-status capture (residual #1916): the harness reports each
            # subprocess's (returncode, result_event_fired); the LAST
            # invocation (a stale-UUID fallback retry supersedes the
            # primary) is the turn's authoritative exit shape. `None` means
            # the adapter never received an exit-status callback at all
            # (distinct from "received one with fired=False").
            exit_statuses: list[tuple[int | None, bool]] = (
                [(turn_result.returncode, turn_result.result_event_fired)]
                if turn_result.result_event_fired is not None
                else []
            )
        except TimeoutError:
            # Hung subprocess: no result, no Stop within the bounded wait.
            logger.error(
                "[role-driver] %s turn timed out after %.0fs — hung subprocess",
                self.role,
                self.turn_timeout_s,
            )
            outcome.hung = True
            outcome.failure = TurnFailure(ExitReason.HEADLESS_TURN_TIMEOUT)
            return outcome
        except HarnessThinkingBlockCorruptionError as e:
            # Nonzero exit + thinking-block corruption: propagate the failure.
            logger.error("[role-driver] %s turn corruption: %s", self.role, e)
            outcome.failure = TurnFailure(ExitReason.HEADLESS_THINKING_CORRUPTION, str(e))
            return outcome
        except Exception as e:  # noqa: BLE001
            logger.error("[role-driver] %s turn subprocess error: %s", self.role, e)
            outcome.failure = TurnFailure(ExitReason.HEADLESS_SUBPROCESS_ERROR, str(e))
            return outcome

        # The harness marks a binary-not-found failure inline in the reply text.
        if isinstance(reply, str) and reply.startswith("Error: CLI harness not found"):
            outcome.reply_text = reply
            outcome.failure = TurnFailure(ExitReason.HEADLESS_BINARY_MISSING)
            return outcome

        # Empty-output guard: no result event, no accumulated text.
        if not reply:
            outcome.reply_text = ""
            outcome.failure = TurnFailure(ExitReason.EMPTY_OUTPUT)
            return outcome

        outcome.reply_text = reply
        # Schema-first routing (plan #2000 Task 2.3): pass the harness's
        # structured_output straight through — None on schema-validation
        # failure (the router's fallback-to-regex signal).
        outcome.structured_output = turn_result.structured_output

        # Capture the new claude session UUID (stored as a side effect by
        # get_response_via_harness) for --resume and resume-scalar persistence.
        if not self._claude_session_id:
            captured = self._capture_claude_session_id()
            if captured:
                self._claude_session_id = captured
                self._transcript_path = _headless_transcript_path(self.working_dir, captured)
        outcome.claude_session_id = self._claude_session_id
        outcome.transcript_path = self._transcript_path
        self._primed = True

        # Nonzero exit WITHOUT a result event (residual #1916): the harness
        # returned partial accumulated text from a crashed subprocess — that
        # is a failed turn, never a clean one. (A nonzero exit AFTER a
        # result event keeps the result: the event is the protocol's
        # completion signal. Preempt/timeout kills are caught by the runner's
        # handle.killed; a zero-output crash hit empty_output above.)
        if exit_statuses:
            returncode, result_event_fired = exit_statuses[-1]
            if returncode not in (None, 0) and not result_event_fired:
                logger.error(
                    "[role-driver] %s turn subprocess exited %s without a result "
                    "event (%d chars of partial text) — classifying as failed",
                    self.role,
                    returncode,
                    len(reply),
                )
                outcome.failure = TurnFailure(ExitReason.HEADLESS_NONZERO_EXIT_NO_RESULT)
                return outcome

        # Turn-end reconciliation: prefer a fresh TURN_END envelope; else the
        # clean subprocess exit is the authoritative boundary (fallback).
        turn_end, needs_human, compaction = self._reconcile_turn_end(
            snapshot_ts, self._claude_session_id
        )
        outcome.needs_human = needs_human
        outcome.compaction = compaction
        if turn_end is not None:
            outcome.turn_ended = True
            outcome.turn_end_source = "hook_edge"
            if turn_end.transcript_path:
                outcome.transcript_path = turn_end.transcript_path
        else:
            outcome.turn_ended = True
            outcome.turn_end_source = "result"
        return outcome

    def _capture_claude_session_id(self) -> str | None:
        """Read the claude UUID stored by get_response_via_harness (side effect)."""
        try:
            from agent.sdk_client import _get_prior_session_uuid  # noqa: PLC0415

            return _get_prior_session_uuid(self.session_id)
        except Exception:  # noqa: BLE001
            return None


def _headless_transcript_path(cwd: str, claude_uuid: str) -> str:
    """Derive the JSONL transcript path for a headless claude session.

    Uses the codebase's real slugging function
    (:func:`agent.session_runner.adapter._transcript_path_from_spec`)
    so the formula stays in one place — ``~/.claude/projects/<cwd-slug>/<uuid>.jsonl``
    where the slug replaces both ``/`` and ``.`` with ``-``.
    """
    from agent.session_runner.adapter import _transcript_path_from_spec  # noqa: PLC0415

    return _transcript_path_from_spec(cwd, claude_uuid)
