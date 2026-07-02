"""Transport-polymorphic actor-turn driver seam for the granite container.

Plan #1842. The PM↔Dev orchestration loop (relay, steering, watchdog, exit
classification) is transport-agnostic; only each actor's "send a message,
await a settled reply" surface differs by transport. This module defines that
seam:

* :class:`RoleDriver` — the minimal Protocol the container loop needs from an
  actor (role + transport labels, a transport-tagged resume handle).
* :class:`PTYRoleDriver` — wraps today's :class:`PTYDriver` + its
  :class:`HookEdgeConsumer` UNCHANGED. The PTY read path, idle detection,
  liveness signals, and hook-channel turn-end are byte-identical to the
  pre-#1842 behavior (the extraction is mechanical — TERRITORY RULE).
* :class:`HeadlessRoleDriver` — runs one ``claude -p`` subprocess per turn via
  the preserved harness (:func:`agent.sdk_client.get_response_via_harness`),
  metered against the Agent SDK credit pool. Turn-end is reconciled from two
  signals: a #1688 ``TURN_END`` hook envelope when it lands, else the
  subprocess ``result`` / clean exit (a real, well-defined boundary for a
  single-shot invocation — the documented narrow exception in the plan's
  "Turn-end authority"). The PTY leg stays hook-channel-only.

The headless leg's turn-end fallback is probe-independent by construction: it
prefers the hook envelope when present and falls back to clean exit otherwise,
so it is correct whether or not the ``Stop`` hook flushes before the single-shot
subprocess exits (Task 0 Probe A defers the empirical selection).
"""

from __future__ import annotations

import logging
import pathlib
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agent.granite_container.hook_edge import (
    COMPACTION,
    NEEDS_HUMAN,
    TURN_END,
    HookEdge,
    HookEdgeConsumer,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Transport labels (mirror the validated config vocabulary).
TRANSPORT_PTY = "pty"
TRANSPORT_HEADLESS = "headless"

# Headless prime-injection strategy (concern 5). Both paths are implemented and
# unit-tested; the driver selects at build time. The DEFAULT is the
# slash-command path: Task 0 Probe B empirically confirmed
# ``/granite:prime-pm-role`` RESOLVES under ``claude -p`` (the primed persona's
# routing token surfaced under Substrate B, claude 2.1.198 / qwen). The
# ``--append-system-prompt`` path stays as a documented contingency, selectable
# via ``prime_path=PRIME_PATH_APPEND`` if slash resolution ever regresses.
PRIME_PATH_SLASH = "slash_command"
PRIME_PATH_APPEND = "append_system_prompt"
DEFAULT_HEADLESS_PRIME_PATH = PRIME_PATH_SLASH

# The granite prime command files, keyed by role. Invoked as
# ``/granite:prime-{pm,dev,teammate}-role`` (slash path) or read as the
# ``--append-system-prompt`` body (fallback path). Paths are repo-relative to
# the project root.
_PRIME_COMMAND_DIR = ".claude/commands/granite"
_PRIME_SLASH_BY_ROLE = {
    "pm": "/granite:prime-pm-role",
    "dev": "/granite:prime-dev-role",
    "teammate": "/granite:prime-teammate-role",
}
_PRIME_FILE_BY_ROLE = {
    "pm": "prime-pm-role.md",
    "dev": "prime-dev-role.md",
    "teammate": "prime-teammate-role.md",
}


@runtime_checkable
class RoleDriver(Protocol):
    """The transport-polymorphic actor surface the container orchestrates.

    Implementations expose their ``role`` ("pm"/"dev"/"teammate") and
    ``transport`` ("pty"/"headless") labels and a transport-tagged resume
    handle. The heavy per-turn surface (send/await/liveness) is transport
    specific — PTY roles keep the PTYDriver + HookEdgeConsumer path; headless
    roles use :meth:`HeadlessRoleDriver.run_turn`.
    """

    role: str
    transport: str

    def resume_handle(self) -> dict | None:
        """Return a ``{role, claude_session_id, transcript_path, transport}``
        entry, or None if no claude session has been captured yet."""
        ...


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
        logger.warning("[headless-driver] prime file missing: %s", path)
        return ""
    # Drop a leading YAML frontmatter block if present.
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return raw.strip()


def _slash_command_for(role: str) -> str:
    return _PRIME_SLASH_BY_ROLE.get(role, _PRIME_SLASH_BY_ROLE["pm"])


class PTYRoleDriver:
    """Wraps a :class:`PTYDriver` (+ its :class:`HookEdgeConsumer`) unchanged.

    This is a mechanical extraction: every attribute/method the container loop
    reads is delegated to the wrapped PTYDriver so PTY behavior is
    byte-identical under the default (both-PTY) config. The consumer is the
    #1688 hook-channel turn-end authority for PTY roles.
    """

    transport = TRANSPORT_PTY

    def __init__(
        self,
        pty: Any,
        consumer: HookEdgeConsumer | None = None,
        *,
        role: str,
        session_id: str | None = None,
        transcript_path: str | None = None,
    ) -> None:
        self.pty = pty
        self.consumer = consumer
        self.role = role
        # The claude session UUID (set at spawn via ``claude --session-id``).
        self._claude_session_id = session_id or getattr(pty, "_session_id", None)
        self._transcript_path = transcript_path

    # -- Delegated actor-turn surface (byte-identical to raw PTYDriver) ----
    def write(self, text: str) -> None:
        self.pty.write(text)

    def read_until_idle(self, *args: Any, **kwargs: Any) -> Any:
        return self.pty.read_until_idle(*args, **kwargs)

    def isalive(self) -> bool:
        return bool(self.pty.isalive())

    def close(self, force: bool = True) -> None:
        self.pty.close(force=force)

    def spawn(self) -> None:
        self.pty.spawn()

    def last_resume_uuid(self) -> str | None:
        return self.pty.last_resume_uuid()

    @property
    def pid(self) -> int | None:
        return getattr(self.pty, "pid", None)

    def resume_handle(self) -> dict | None:
        if not self._claude_session_id:
            return None
        return {
            "role": self.role,
            "claude_session_id": self._claude_session_id,
            "transcript_path": self._transcript_path,
            "transport": self.transport,
        }


@dataclass
class HeadlessTurnOutcome:
    """Result of one headless actor turn.

    ``turn_ended`` is True when the turn reached a well-defined boundary;
    ``turn_end_source`` records which signal decided it ("hook_edge" for a
    #1688 ``TURN_END`` envelope, "result" for the subprocess clean exit
    fallback, "none" for a hung/failed turn). ``exit_reason`` is None on
    success and a slug otherwise (feeds the container's exit classification).
    """

    reply_text: str = ""
    turn_ended: bool = False
    turn_end_source: str = "none"
    claude_session_id: str | None = None
    transcript_path: str | None = None
    needs_human: HookEdge | None = None
    compaction: HookEdge | None = None
    exit_reason: str | None = None
    hung: bool = False
    metered: bool = True


class HeadlessRoleDriver:
    """Drives one role headlessly: one ``claude -p`` subprocess per turn.

    Reuses :func:`agent.sdk_client.get_response_via_harness` for all subprocess
    handling (argv assembly, ANTHROPIC_API_KEY strip, stale-UUID retry,
    stream-json parsing, single metered token accumulation). This driver adds
    only: persona priming (first turn), --resume continuation (later turns),
    turn-end reconciliation against the #1688 hook edge file, resume-handle
    capture, and a bounded-wait hung-subprocess guard.
    """

    transport = TRANSPORT_HEADLESS

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
    ) -> None:
        self.role = role
        self.session_id = session_id
        self.working_dir = working_dir
        self.model = model
        self.settings_path = settings_path
        self.edge_file = edge_file
        self.env = env
        self.prime_path = prime_path
        self.project_root = project_root
        self.turn_timeout_s = turn_timeout_s
        self.full_context_message = full_context_message
        self._harness_fn = harness_fn
        self._on_stdout_event = on_stdout_event
        # A per-role HookEdgeConsumer over the same edge file the subprocess's
        # --settings hook set writes to (turn-end reconciliation). Constructed
        # lazily so callers can inject a fake consumer in tests.
        self._consumer = consumer
        if self._consumer is None and edge_file:
            self._consumer = HookEdgeConsumer(edge_file, session_id=None)
        # Captured on first-turn completion; drives --resume on later turns and
        # the resume handle.
        self._claude_session_id: str | None = None
        self._transcript_path: str | None = None
        self._primed = False

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
        ``needs_human`` / ``compaction`` edges are drained opportunistically for
        parity with the PTY leg.
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
        return (turn_end, needs_human, compaction)

    # -- Prime injection --------------------------------------------------
    def _prime_args(self, message: str) -> tuple[str, str | None]:
        """Return (message, system_prompt) with first-turn priming applied.

        Slash path: prepend the role's ``/granite:prime-*`` slash command to
        the first message (mirrors the PTY container's first-write).
        Append path (default): inject the prime command body via
        ``--append-system-prompt`` (system_prompt), leaving the message intact.
        """
        if self._primed:
            return (message, None)
        if self.prime_path == PRIME_PATH_SLASH:
            slash = _slash_command_for(self.role)
            return (f"{slash} {message}" if message else slash, None)
        # Default: append-system-prompt fallback.
        body = _read_prime_body(self.role, self.project_root)
        return (message, body or None)

    # -- Public per-turn API ----------------------------------------------
    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        """Run one headless turn end-to-end and return a classified outcome."""
        import asyncio  # noqa: PLC0415

        from agent.sdk_client import HarnessThinkingBlockCorruptionError  # noqa: PLC0415

        harness_fn = self._harness_fn
        if harness_fn is None:
            from agent.sdk_client import get_response_via_harness  # noqa: PLC0415

            harness_fn = get_response_via_harness

        turn_message, system_prompt = self._prime_args(message)
        prior_uuid = self._claude_session_id  # None on first turn

        # Race 4: drain stale edges BEFORE spawning this turn's subprocess.
        snapshot_ts = self._snapshot_edges()

        outcome = HeadlessTurnOutcome()
        try:
            reply = await asyncio.wait_for(
                harness_fn(
                    turn_message,
                    self.working_dir,
                    env=self.env,
                    prior_uuid=prior_uuid,
                    session_id=self.session_id,
                    full_context_message=self.full_context_message,
                    model=self.model,
                    system_prompt=system_prompt,
                    settings_path=self.settings_path,
                    metered=True,
                    role=self.role,
                    on_stdout_event=self._on_stdout_event,
                ),
                timeout=self.turn_timeout_s,
            )
        except TimeoutError:
            # Hung subprocess: no result, no Stop within the bounded wait.
            logger.error(
                "[headless-driver] %s turn timed out after %.0fs — hung subprocess",
                self.role,
                self.turn_timeout_s,
            )
            outcome.hung = True
            outcome.exit_reason = "headless_turn_timeout"
            return outcome
        except HarnessThinkingBlockCorruptionError as e:
            # Nonzero exit + thinking-block corruption: propagate exit_reason.
            logger.error("[headless-driver] %s turn corruption: %s", self.role, e)
            outcome.exit_reason = f"headless_thinking_corruption: {e}"
            return outcome
        except Exception as e:  # noqa: BLE001
            logger.error("[headless-driver] %s turn subprocess error: %s", self.role, e)
            outcome.exit_reason = f"headless_subprocess_error: {e}"
            return outcome

        # The harness marks a binary-not-found failure inline in the reply text.
        if isinstance(reply, str) and reply.startswith("Error: CLI harness not found"):
            outcome.reply_text = reply
            outcome.exit_reason = "headless_binary_missing"
            return outcome

        # Empty-output guard: no result event, no accumulated text.
        if not reply:
            outcome.reply_text = ""
            outcome.exit_reason = "empty_output"
            return outcome

        outcome.reply_text = reply

        # Capture the new claude session UUID (stored as a side effect by
        # get_response_via_harness) for --resume and the resume handle.
        if not self._claude_session_id:
            captured = self._capture_claude_session_id()
            if captured:
                self._claude_session_id = captured
                self._transcript_path = _headless_transcript_path(self.working_dir, captured)
        outcome.claude_session_id = self._claude_session_id
        outcome.transcript_path = self._transcript_path
        self._primed = True

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

    def resume_handle(self) -> dict | None:
        if not self._claude_session_id:
            return None
        return {
            "role": self.role,
            "claude_session_id": self._claude_session_id,
            "transcript_path": self._transcript_path,
            "transport": self.transport,
        }


def _headless_transcript_path(cwd: str, claude_uuid: str) -> str:
    """Derive the JSONL transcript path for a headless claude session.

    Uses the codebase's real slugging function
    (:func:`agent.granite_container.bridge_adapter._transcript_path_from_spec`)
    so the formula stays in one place — ``~/.claude/projects/<cwd-slug>/<uuid>.jsonl``
    where the slug replaces both ``/`` and ``.`` with ``-``.
    """
    from agent.granite_container.bridge_adapter import _transcript_path_from_spec  # noqa: PLC0415

    return _transcript_path_from_spec(cwd, claude_uuid)
