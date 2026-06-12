"""Container: the steady-state loop for the granite operator PoC (issue #1546).

The container owns two PTYs (PM + Dev), the persona-priming slash
commands, the startup-phase parser, and the granite classifier. It
runs the loop described in the plan's *Data Flow* section:

  1. spawn both PTYs
  2. prime both personas via /granite-poc:prime-{pm,dev}-role
  3. startup-phase parser watches both PTYs; on trust-folder
     prompt, dismiss with "1\\r"
  4. steady state: wait for PM idle -> call granite to classify
     (regex parse) and extract_dev_prompt (ollama) -> write to Dev
     PTY -> wait for Dev idle -> call granite to summarize_for_pm
     (ollama) -> write summary to PM PTY -> repeat
  5. exit on PM [/complete] prefix, max_turns safety cap, dev
     hang (await_idle timeout), startup_unresolved (neither PTY
     settles within STARTUP_HARD_CEILING_S), or any exception

Two-PTY coordination is the early risk (per the plan's *Technical
Approach*). The container's loop is single-threaded; reads from
both PTYs are not interleaved within a single tick. The loop
processes one PM->granite->Dev->granite->PM cycle per tick.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from agent.granite_container.granite_classifier import (
    classify_pm_prefix,
    extract_dev_prompt,
    summarize_for_pm,
)
from agent.granite_container.pty_driver import (
    DEFAULT_MIN_CONTENT_BYTES,
    PTYDriver,
)
from agent.granite_container.startup_parser import (
    StartupEvent,
    parse_startup_frame,
)

logger = logging.getLogger(__name__)

# Path to the persona-priming slash commands. Both files are shipped
# in the repo under .claude/commands/granite-poc/. The container
# looks them up by name; the slash-command mechanism is part of the
# TUI's parser, not the container's.
PM_PRIME_SLASH_CMD = "/granite-poc:prime-pm-role"
DEV_PRIME_SLASH_CMD = "/granite-poc:prime-dev-role"

# The trust-folder prompt dismissal (per the F-probe at
# scripts/probe_slash_arguments.py:243-247).
TRUST_FOLDER_DISMISSAL = "1"

# Default max_turns safety cap. PM may run this many PM->Dev cycles
# before the container exits with pm_max_turns. The cap is a
# safety net; the steady-state exit is the PM [/complete] prefix.
DEFAULT_MAX_TURNS = 10

# Hard wall-clock ceiling for the startup phase. The startup loop
# keeps polling (short 3s reads) until both PTYs are idle; if they
# never settle within this ceiling, the container exits
# `startup_unresolved`. This is plan Risk 6's detection mode for a
# broken `--permission-mode` flag: when the flag is renamed or
# removed by a TUI upgrade, the bypass-permissions bar never paints,
# the C5 idle heuristic never fires, and the run exits with the
# distinct `startup_unresolved` signature instead of burning the
# steady-state budget and reporting a misleading `pm_hang`. The
# ceiling is deliberately long — persona load on a cold Opus
# high-effort PTY can run minutes past the prime post-write budget
# (PR #1612 live run, June 2026), and the short per-cycle reads
# make the extended wait cheap.
STARTUP_HARD_CEILING_S = 600.0

# Per-cycle idle timeout. If a PTY doesn't reach idle within this
# many seconds, the container treats it as a hang (pm_hang /
# dev_hang) and exits the loop.
CYCLE_IDLE_TIMEOUT_S = 120.0

# Per-cycle idle budget for the startup-phase poll loop. Short by
# design: the loop's job is to detect transient startup events
# (trust-folder, update notice), not to wait for a long model
# turn. A short per-read budget means the startup loop polls
# cheaply and frequently while waiting for the PTYs to settle
# under STARTUP_HARD_CEILING_S, and only burns the long
# steady-state budget (CYCLE_IDLE_TIMEOUT_S) once it enters the
# actual turn loop. Without this, the startup loop consumed
# 120s idle waits per cycle on a slow persona load (PR #1612
# live run, June 2026).
# HARD FLOOR: must stay strictly above pty_driver.QUIESCENCE_S
# (2.0s) — idle is only declared after that much byte-silence, so
# a startup poll shorter than QUIESCENCE_S can NEVER observe idle
# and silently reintroduces the startup_unresolved hang this
# constant exists to bound.
STARTUP_CYCLE_TIMEOUT_S = 3.0

# Trust-folder prompt pattern matched against the raw TUI buffer
# BEFORE the C5 idle heuristic. The workspace-trust dialog does
# NOT paint the bypass-permissions bar (different security layer
# from the per-tool permission dialogs that `bypassPermissions`
# suppresses), so the C5 heuristic's `bypass.{0,30}permissions`
# regex never matches. Dismissing with "1" unsticks the prime in
# <2s on the trust-folder path; the prior behavior silently
# burned 60s on `saw_idle=False` and never sent the prime
# command, deadlocking both PM and Dev (issue #1572 live run).
TRUST_FOLDER_RE = re.compile(r"(Yes, I trust this folder|trust this folder\?)", re.IGNORECASE)
# Pre-C5 trust dismissal budget: short, because we want to dismiss
# quickly and re-read. 10s catches a slow first paint without
# burning the full prime budget.
PRIME_TRUST_DISMISS_TIMEOUT_S = 10.0
# Pre-write C5 budget: the welcome frame paints fast, so this only
# has to cover initial render + any post-dismissal re-render.
PRIME_PRE_WRITE_TIMEOUT_S = 30.0
# Post-write C5 budget: persona body load + first-token wait. The
# pool's prewarmed PTY starts cold (no conversation history), so
# Opus 4.8 high-effort can take 90-180s for the slash command to
# actually be processed and "Worked for Ns" to print. PR #1612
# live run on June 2026 hit 120s saw_idle=False on PM; raise the
# post-write budget to absorb that latency without churning the
# startup-phase loop. The pre-write budget stays tight because it
# is bounded by render speed, not model latency.
PRIME_POST_WRITE_TIMEOUT_S = 360.0
# Legacy alias kept for tests and existing references. New code
# should reference the pre/post-write pair explicitly.
PRIME_C5_TIMEOUT_S = PRIME_POST_WRITE_TIMEOUT_S
# Post-write content floor. The bypass-permissions bar is a
# persistent footer, so an empty/minimal buffer can match the C5
# idle heuristic even before the model has produced any response
# content (the bar is what gated `_prime_session`'s pre-write
# read). Without a floor, the post-write read returns
# saw_idle=True on the stale pre-write buffer and `_prime_session`
# returns while the slash command is still being processed (PR
# #1612 live run, June 2026: Dev prime returned in 5ms with
# buffer_len=223 — the model never had time to load). 1500 bytes
# comfortably exceeds the welcome frame but is well under the
# persona-load response length.
PRIME_POST_WRITE_MIN_CONTENT_BYTES = 1500

# Cap on the size of `ContainerResult.exit_message`. A multi-KB
# traceback or ollama error body can land here on the exception
# branch, and the result is published into the Telegram relay —
# keep the message bounded so a single failure doesn't flood a
# chat. 500 chars matches the Telegram message clamp the relay
# already enforces downstream; truncating here keeps the relay
# clean and the JSON results doc readable.
EXIT_MESSAGE_MAX_CHARS = 500

# Corrective nudge written to PM's PTY when PM emits a prefix the
# classifier cannot route (no recognized [/dev]|[/user]|[/complete]
# token, or a recognized token with an empty payload). Without a
# write, PM stays idle on the same non-compliant buffer and the
# loop reclassifies the identical output every tick until max_turns
# — burning the safety cap on a stuck PM. The nudge re-prompts PM
# to re-emit a compliant prefix, so the next read sees fresh output.
PM_COMPLIANCE_NUDGE = (
    "Your last reply did not start with a routing prefix on its own "
    "line. Re-send your reply starting with exactly one of [/dev], "
    "[/user], or [/complete] on the first line, followed by the "
    "content."
)

# Wrap-up prompt written to PM when the run ends with no user-facing
# message delivered. Instructs PM to emit a [/user] or [/complete]
# summary so the human always receives a real message.
PM_WRAPUP_PROMPT = (
    "The developer has finished. Here is their final report:\n\n{seed}\n\n"
    "Send your [/user] or [/complete] summary to the human now. "
    "Include the specific outcomes from the report above — which files changed "
    "and what was done — not a generic acknowledgement."
)

# Maximum number of wrap-up attempts when PM still hasn't produced a
# user-facing message on exit. Capped at 1 to bound the extra latency;
# a PM that stays silent after the wrap-up prompt gets the canned
# OPERATOR_TERMINAL_MESSAGE instead.
MAX_WRAPUP_ATTEMPTS = 1

# Fallback seed string for the wrap-up prompt when the developer did
# not produce a captured report and the Dev PTY is no longer readable.
DEV_REPORT_UNAVAILABLE = "The developer did not produce a captured report."

# Fallback user-visible message delivered directly (bypassing PM) when
# the wrap-up guard exhausts MAX_WRAPUP_ATTEMPTS without PM emitting a
# user-facing prefix. Guarantees the human always gets some message.
OPERATOR_TERMINAL_MESSAGE = "Your request was completed; a summary could not be generated."


@dataclass
class TurnRecord:
    """One cycle of the steady-state loop's PM->Dev handoff."""

    turn_index: int
    pm_idle_ms: int
    dev_idle_ms: int
    classification: str  # dev | user | complete | unknown
    compliance_miss: bool
    pm_first_line: str
    routed_payload_chars: int
    granite_extract_ms: int
    granite_summarize_ms: int
    pm_idle_marker: str
    dev_idle_marker: str


@dataclass
class ContainerResult:
    """Final output of a container run.

    `exit_reason` is one of: pm_complete, pm_user, pm_max_turns,
    dev_hang, pm_hang, startup_unresolved, pm_no_user_message,
    exception. The PoC's results doc renders this as the verdict.

    `user_facing_routed` is True when at least one [/user] or
    non-empty [/complete] payload was delivered to the user channel
    during the run. BridgeAdapter propagates this flag to
    agent_session.user_facing_routed so session_executor can choose
    the correct post-run emoji.
    """

    session_id: str
    user_message: str
    turns: list[TurnRecord] = field(default_factory=list)
    exit_reason: str = "in_progress"
    exit_message: str = ""
    total_pm_pty_bytes: int = 0
    total_dev_pty_bytes: int = 0
    parse_failures: int = 0
    classification_compliance_misses: int = 0
    resume_uuid: str | None = None
    startup_events: list[dict[str, Any]] = field(default_factory=list)
    coord_test_pass: bool | None = None
    user_facing_routed: bool = False
    # PTY identity fields: PID and deterministic transcript path for each role.
    # Populated by Container.run() after the PTY pair is acquired. Transcript
    # paths follow Claude Code's naming convention:
    #   ~/.claude/projects/{cwd.replace("/", "-")}/{session_id}.jsonl
    pm_pid: int | None = None
    pm_transcript_path: str | None = None
    dev_pid: int | None = None
    dev_transcript_path: str | None = None


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


def _transcript_path(cwd: str, session_id: str | None) -> str | None:
    """Compute the Claude Code transcript path for a PTY session.

    Claude Code names transcripts:
        ~/.claude/projects/{cwd-slug}/{session_id}.jsonl
    where {cwd-slug} = cwd.replace("/", "-").

    Returns None when session_id is not known; callers that receive None
    should skip transcript tailing for that session.
    """
    if not session_id:
        return None
    cwd_slug = cwd.replace("/", "-")
    return str(Path.home() / ".claude" / "projects" / cwd_slug / f"{session_id}.jsonl")


def _capture_pty_identity(
    result: ContainerResult,
    pm_pty: PTYDriver | None,
    dev_pty: PTYDriver | None,
    cwd: str,
) -> None:
    """Populate ContainerResult PTY identity fields from live PTY drivers.

    Captures PIDs via PTYDriver.pid (None when not alive or not spawned)
    and computes deterministic transcript paths from the session_id stored
    on each driver. All operations are best-effort — a missing PID or
    unknown session_id leaves the field as None; callers must tolerate None.
    """
    if pm_pty is not None:
        result.pm_pid = getattr(pm_pty, "pid", None)
        result.pm_transcript_path = _transcript_path(cwd, getattr(pm_pty, "_session_id", None))
    if dev_pty is not None:
        result.dev_pid = getattr(dev_pty, "pid", None)
        result.dev_transcript_path = _transcript_path(cwd, getattr(dev_pty, "_session_id", None))


def _truncate_exit_message(text: str) -> str:
    """Bound `ContainerResult.exit_message` to `EXIT_MESSAGE_MAX_CHARS`.

    The exception branch can capture multi-kilobyte tracebacks or
    ollama error bodies; the result is published into the Telegram
    relay, so we clamp the size here rather than letting a single
    failure flood a chat. A short ellipsis marker preserves the
    "we truncated" signal in the published message.
    """
    if len(text) <= EXIT_MESSAGE_MAX_CHARS:
        return text
    return text[: EXIT_MESSAGE_MAX_CHARS - 3] + "..."


def _make_sandbox_cwd() -> tuple[str, str]:
    """Create a fresh sandbox tempdir for the PoC run.

    Returns (cwd, label) where label is a short prefix used for
    logging. The container writes nothing into the sandbox; it
    only uses it as the subprocess cwd. The sandbox is cleaned up
    on `__exit__` via a `try/finally` in `Container.run`.
    """
    sandbox_root = Path(tempfile.gettempdir()) / "granite-poc"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    sandbox = sandbox_root / f"run-{uuid.uuid4().hex[:8]}"
    sandbox.mkdir(parents=True, exist_ok=False)
    return str(sandbox), sandbox.name


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


class RouteOutcome(NamedTuple):
    """Return value of `_route_pm_classification`.

    `should_break` True means the steady-state loop should exit after this
    routing decision. `exit_reason` carries the ContainerResult.exit_reason
    to set when `should_break` is True (None means the loop should continue
    — dev routing does not break the loop).
    """

    should_break: bool
    exit_reason: str | None


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class Container:
    """The steady-state loop. Owns two PTYs and the granite calls.

    Lifecycle: `__init__` -> `run()` -> close both PTYs. The
    container is single-shot (one operator invocation, one
    container.run). A multi-turn session is a series of operator
    invocations; the container does not persist state across them
    (per the plan's *Persistent Artifacts* section).
    """

    def __init__(
        self,
        user_message: str,
        cwd: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        pm_model: str | None = None,
        dev_model: str | None = None,
        on_user_payload: Callable[[str], None] | None = None,
        on_complete_payload: Callable[[str], None] | None = None,
        on_turn: Callable[[], None] | None = None,
        pm_pty: PTYDriver | None = None,
        dev_pty: PTYDriver | None = None,
    ) -> None:
        if not user_message.strip():
            raise ValueError("Container.user_message must be non-empty")
        self.user_message = user_message
        self.cwd = cwd
        self.max_turns = max_turns
        self._pm_model = pm_model
        self._dev_model = dev_model
        self._on_user_payload = on_user_payload
        self._on_complete_payload = on_complete_payload
        # Per-turn progress hook: called once per classified PM turn
        # (every destination, including unknown). BridgeAdapter uses it
        # to bump `agent_session.last_turn_at` so the two-tier
        # no-progress detector's sub-check A stays live for granite
        # sessions (PR #1612 review TD1). Exceptions are swallowed —
        # progress signaling must never crash the loop.
        self._on_turn = on_turn
        # Optional pre-warmed PTY pair from the PTYPool. When both
        # are provided, Container skips _spawn_pair() and reuses
        # the pool's prewarmed pair (BridgeAdapter is the caller in
        # production). When None, Container spawns its own (used by
        # tests and run_ping_pong_test). The pool marks the pair
        # as _released_to_pool=True on its async with entry, so
        # _close_pair will NOT close them — the pool's __aexit__
        # owns the close.
        self._prewarmed_pm_pty = pm_pty
        self._prewarmed_dev_pty = dev_pty
        self._pm_pty: PTYDriver | None = None
        self._dev_pty: PTYDriver | None = None
        self._sandbox: tuple[str, str] | None = None
        # Last Dev report captured after each successful summarize_for_pm
        # in the steady-state dev branch. Used as the seed for the
        # wrap-up guard prompt so the PM can deliver a specific summary.
        self._last_dev_report: str | None = None
        # One-shot flags for prime-turn relay (issue #1644).
        # _prime_relayed=True means the PM's prime-turn buffer was routed
        # to user/complete (not dev) — the first steady-state iteration
        # should force a fresh _cycle_idle before classifying so it reads
        # genuinely new PM output, not the stale prime buffer.
        self._prime_relayed: bool = False
        self._prime_pm_buf_hash: int | None = None

    # -- Lifecycle --------------------------------------------------------

    def _spawn_pair(self) -> None:
        """Spawn both PTYs and prime both personas.

        Reuses a prewarmed pair when the ctor received one
        (production path: PTYPool -> BridgeAdapter -> Container).
        Spawns a fresh pair otherwise (tests, run_ping_pong_test).
        """
        if self._prewarmed_pm_pty is not None and self._prewarmed_dev_pty is not None:
            self._pm_pty = self._prewarmed_pm_pty
            self._dev_pty = self._prewarmed_dev_pty
            return

        if self.cwd is None:
            self._sandbox = _make_sandbox_cwd()
            cwd = self._sandbox[0]
        else:
            cwd = self.cwd

        self._pm_pty = PTYDriver(role="pm", cwd=cwd, model=self._pm_model)
        self._dev_pty = PTYDriver(role="dev", cwd=cwd, model=self._dev_model)
        self._pm_pty.spawn()
        self._dev_pty.spawn()

    def _close_pair(self) -> None:
        # Skip PTYs the pool already owns — its __aexit__ does the
        # close. Double-closing races the pool's respawn and can
        # leave the pool's respawned pair in a half-closed state
        # (see PTYPool contract: the slot's PTY is closed exactly
        # once, on release).
        for pty in (self._pm_pty, self._dev_pty):
            if pty is None:
                continue
            if getattr(pty, "_released_to_pool", False):
                continue
            try:
                pty.close(force=True)
            except Exception:
                pass
        if self._sandbox is not None:
            sandbox_path = Path(self._sandbox[0])
            if sandbox_path.exists():
                try:
                    shutil.rmtree(sandbox_path)
                except Exception:
                    pass

    def _uses_pool_pair(self) -> bool:
        """Whether this container runs on a PTYPool-prewarmed pair.

        Pool-backed runs must NEVER use the pkill fallback: the
        pattern matches every `claude --permission-mode
        bypassPermissions` process on the machine, which includes
        the pool's other slots (idle prewarmed pairs and pairs
        mid-run in concurrent granite sessions) and any operator-
        owned interactive session. The pool owns its PTY lifecycle
        (close-on-release + PID-targeted orphan kill at worker
        startup); the machine-wide pkill is only safe for the
        self-spawned single-container path (tests, ping-pong).
        """
        return self._prewarmed_pm_pty is not None and self._prewarmed_dev_pty is not None

    def _run_pkill_fallback(self) -> None:
        """Last-ditch teardown: kill any orphaned `claude --bypassPermissions` PTYs.

        Mirrors the probe's teardown at
        `scripts/probe_slash_arguments.py:367-373`. The container
        prefers `child.close(force=True)`; this is the safety net.
        Skipped entirely for pool-backed runs — see `_uses_pool_pair`.
        """
        if self._uses_pool_pair():
            return
        try:
            subprocess.run(
                ["pkill", "-f", "claude --permission-mode bypassPermissions"],
                check=False,
                timeout=5,
                capture_output=True,
            )
        except Exception:
            pass

    # -- Startup phase ----------------------------------------------------

    def _handle_startup(self, buffer_pm: str, buffer_dev: str) -> str | None:
        """Run the startup-phase parser on both PTY buffers.

        Returns the response text to write to PM's PTY, or None when
        no known startup event is present. The parser is pure with
        respect to the input buffers (it never mutates them), so the
        caller keeps ownership of the buffer state and feeds the next
        delta on the following cycle.
        """
        result_pm = parse_startup_frame(buffer_pm)
        result_dev = parse_startup_frame(buffer_dev)

        # Trust-folder is the most likely co-occurring event; the
        # parser has already deduplicated. Pick the highest-priority
        # event: error > trust_folder > update > login > prime_ack
        # > unknown.
        chosen = None
        for r in (result_pm, result_dev):
            if r.event in (StartupEvent.ERROR_MODAL,):
                chosen = ("error", r)
                break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.TRUST_FOLDER_PROMPT:
                    chosen = ("trust_folder", r)
                    break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.UPDATE_NOTICE:
                    chosen = ("update", r)
                    break
        if chosen is None:
            for r in (result_pm, result_dev):
                if r.event == StartupEvent.LOGIN_PROMPT:
                    chosen = ("login", r)
                    break

        if chosen is None:
            return None

        _, r = chosen
        return r.response

    # -- Steady-state loop ------------------------------------------------

    def _cycle_idle(
        self, pty: PTYDriver, min_content_bytes: int = DEFAULT_MIN_CONTENT_BYTES
    ) -> tuple[bool, str, str, int]:
        """Wait for a single PTY to reach idle.

        Returns (saw_idle, buffer, idle_marker, elapsed_ms). If the
        timeout fires without an idle, the buffer is whatever the
        TUI has painted so far and saw_idle is False.

        The buffer is the PTY's per-turn capture (everything painted
        since the last write to that PTY), not just the bytes read
        during this call: the routed output may have streamed during an
        earlier read (e.g. PM's prime response streams during the
        prime's post-write wait), and the steady-state read then sees a
        quiescent PTY. The `or result.buffer` fallback covers drivers
        that don't populate `turn_buffer` (unit-test mocks).
        """
        result = pty.read_until_idle(
            min_content_bytes=min_content_bytes, timeout_s=CYCLE_IDLE_TIMEOUT_S
        )
        buffer = result.turn_buffer or result.buffer
        return (result.saw_idle, buffer, result.idle_marker, result.elapsed_ms)

    # -- Startup phase ----------------------------------------------------

    def _startup_cycle_idle(self, pty: PTYDriver) -> tuple[bool, str, str, int]:
        """Startup-phase idle read with a short per-cycle budget.

        Same return shape as `_cycle_idle`. The startup phase is a
        poll for transient events, not a wait for a model turn, so
        3s per cycle is enough to catch an event the moment the
        TUI paints it without blocking the loop on persona-load
        latency. saw_idle=False here is expected and benign — the
        outer loop keeps cycling.
        """
        result = pty.read_until_idle(min_content_bytes=0, timeout_s=STARTUP_CYCLE_TIMEOUT_S)
        return (result.saw_idle, result.buffer, result.idle_marker, result.elapsed_ms)

    def _prime_session(
        self, pty: PTYDriver, slash_cmd: str, *, include_user_message: bool = True
    ) -> None:
        """Send the persona-priming slash command to a PTY.

        The slash command body is invisible to the operator (F4);
        the only substrate signal is "did the model respond?". This
        helper sends the slash command and waits for the TUI to
        return to idle TWICE: once for the welcome frame (pre-write),
        and once for the model to actually finish processing the
        prime (post-write). The post-write wait is critical because
        the bypass bar is a persistent footer that is visible WHILE
        the model is still loading — without the post-write wait,
        `_prime_session` returns while the model is still
        "Sprouting…" / "Synthesizing…", and the startup-phase
        loop's idle-break condition (both PTYs idle) fires
        immediately on the stale buffer, racing past the actual
        prime. The steady-state loop then reads the still-stale
        buffer and misclassifies as `unknown`, hitting `pm_hang`
        on the first turn (PR #1612 live run, June 2026).

        Pre-C5 trust dismissal: a fresh PTY in an untrusted cwd
        (e.g., a per-session sandbox tempdir) shows the workspace
        trust dialog as its first paint. The dialog does NOT paint
        the bypass-permissions bar, so the C5 idle heuristic
        cannot recognize it as idle. We loop briefly, looking for
        the trust pattern, and dismiss with "1" (the documented
        response — see `scripts/probe_slash_arguments.py:241-247`).
        This converts a 60s silent stall into a <2s dismiss + a
        normal C5 wait.

        `include_user_message` controls whether `self.user_message` is
        appended to the slash command as $ARGUMENTS. The PM prime
        includes it (the PM needs the task context to plan the work);
        the Dev prime does NOT — the Dev must wait for the operator to
        relay the PM's first [/dev] instruction, not start work
        immediately on its own from the raw user message (issue #1644).
        """
        for _ in range(5):
            result = pty.read_until_idle(
                min_content_bytes=0,
                timeout_s=PRIME_TRUST_DISMISS_TIMEOUT_S,
            )
            if result.saw_idle:
                break
            if TRUST_FOLDER_RE.search(result.buffer):
                pty.write("1")
                # TUI may re-render briefly after dismissal; loop
                # and re-read until C5 idle or the buffer changes
                # shape (then fall through to the C5 wait).
                continue
            # No trust pattern, no idle — the TUI may be still
            # painting its first frame. Fall through to the C5
            # wait, which has the full prime budget.
            break
        # Wait for the TUI's initial idle (no content floor — the
        # first paint is the welcome frame, not a response).
        pty.read_until_idle(min_content_bytes=0, timeout_s=PRIME_PRE_WRITE_TIMEOUT_S)
        # Send the slash command. The PM prime appends self.user_message
        # so the PM immediately has the task context. The Dev prime
        # sends the slash command alone — Dev must wait for the
        # operator's first relay of the PM's [/dev] instruction
        # (issue #1644: Dev self-starting on the raw user message
        # raced ahead of the PM before any [/dev] routing decision).
        if include_user_message:
            pty.write(f"{slash_cmd} {self.user_message}")
        else:
            pty.write(slash_cmd)
        # Wait for the model to actually finish the prime. The
        # quiescence gate in read_until_idle blocks idle declaration
        # while the TUI is still painting (spinner animation /
        # streaming response repaint at >=1 Hz), so this read waits
        # for the model's response to settle (or times out at
        # PRIME_POST_WRITE_TIMEOUT_S).
        #
        # The content floor is critical: without it, the bypass-
        # permissions bar (a persistent footer) satisfies the C5
        # idle heuristic on the stale pre-write buffer, and
        # `_prime_session` returns while the model is still
        # processing the slash command. The pre-write C5 read
        # already declared idle on the welcome frame; the post-
        # write read needs a content floor that proves the model
        # actually produced response content.
        post = pty.read_until_idle(
            min_content_bytes=PRIME_POST_WRITE_MIN_CONTENT_BYTES,
            timeout_s=PRIME_POST_WRITE_TIMEOUT_S,
        )
        logger.info(
            "container: prime post-write wait saw_idle=%s buffer_len=%d elapsed_ms=%d",
            post.saw_idle,
            len(post.buffer),
            post.elapsed_ms,
        )

    def run(self) -> ContainerResult:
        """Run the steady-state loop end-to-end.

        Returns a `ContainerResult` with the per-turn trace, exit
        reason, byte counts, and resume UUID (if any). The caller
        writes the result to JSON for the results doc.
        """
        session_id = uuid.uuid4().hex[:12]
        result = ContainerResult(session_id=session_id, user_message=self.user_message)

        try:
            self._spawn_pair()
        except Exception as e:
            result.exit_reason = "exception"
            result.exit_message = _truncate_exit_message(f"spawn failed: {e}")
            self._run_pkill_fallback()
            return result

        logger.info(
            "container: spawned pair (cwd=%s)",
            self.cwd or "<sandbox>",
        )

        # Capture PTY identity (PIDs + deterministic transcript paths).
        # The effective cwd is the container's cwd or the sandbox tempdir.
        effective_cwd = self.cwd or (self._sandbox[0] if self._sandbox else "")
        _capture_pty_identity(result, self._pm_pty, self._dev_pty, effective_cwd)

        try:
            # Persona priming.
            # PM receives the user_message as $ARGUMENTS so it has full
            # task context immediately. Dev does NOT — it must wait for
            # the operator to relay the PM's first [/dev] instruction
            # (issue #1644: Dev self-starting on the raw user message
            # raced ahead of the PM before any routing decision).
            logger.info("container: priming PM")
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD, include_user_message=True)
            logger.info("container: PM prime done")
            logger.info("container: priming Dev")
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD, include_user_message=False)
            logger.info("container: Dev prime done; entering startup loop")

            # Startup-phase loop. Watch both PTYs for known startup
            # events (trust-folder, update notice — the parser
            # dismisses them) and keep cycling on short 3s reads
            # until BOTH PTYs reach idle. The persona load on Opus
            # high-effort can run minutes past the prime post-write
            # budget, so a slow cold start simply keeps polling here
            # cheaply until the TUIs settle. If they never settle
            # within STARTUP_HARD_CEILING_S, the run exits
            # `startup_unresolved` — the distinct failure signature
            # plan Risk 6 relies on for a broken `--permission-mode`
            # flag (the bypass bar never paints, so idle never
            # fires).
            startup_settled = False
            startup_deadline = time.monotonic() + STARTUP_HARD_CEILING_S
            cycle = 0
            while time.monotonic() < startup_deadline:
                pm_idle = self._startup_cycle_idle(self._pm_pty)
                dev_idle = self._startup_cycle_idle(self._dev_pty)
                response = self._handle_startup(pm_idle[1], dev_idle[1])
                logger.info(
                    "container: startup cycle=%d pm_idle=%s dev_idle=%s response=%r",
                    cycle,
                    pm_idle[0],
                    dev_idle[0],
                    response,
                )
                if response is None:
                    # No startup event in this window — break if
                    # both PTYs are idle, otherwise keep watching.
                    if pm_idle[0] and dev_idle[0]:
                        logger.info("container: startup both idle, breaking")
                        startup_settled = True
                        break
                    cycle += 1
                    continue
                # The parser chose a startup event; respond. For
                # trust-folder, "1" is the dismissal. For update
                # notice, the response is "\r" (Enter to dismiss).
                if response:
                    # Pick the PTY that produced the event. Without
                    # a per-PTY tag, we send to PM (the first to
                    # reach the prompt in most sessions). This is
                    # a heuristic; a more rigorous version would
                    # track which PTY the parser saw the event on.
                    self._pm_pty.write(response)
                result.startup_events.append({"cycle": cycle, "response": response})
                cycle += 1
            if not startup_settled:
                result.exit_reason = "startup_unresolved"
                result.exit_message = (
                    f"startup did not settle within {STARTUP_HARD_CEILING_S:.0f}s "
                    f"hard ceiling ({cycle} cycles)"
                )
                return result

            # Prime-turn relay (issue #1644): after both primes complete,
            # read PM's prime-turn buffer and route it through
            # _route_pm_classification. PM may already have decided the
            # destination (user/complete/dev) during its prime response
            # rather than waiting for the first steady-state idle.
            pm_prime_idle, pm_prime_buf, pm_prime_marker, pm_prime_ms = self._cycle_idle(
                self._pm_pty
            )
            if not pm_prime_idle:
                result.exit_reason = "pm_hang"
                result.exit_message = (
                    f"PM did not reach idle after prime within {CYCLE_IDLE_TIMEOUT_S}s"
                )
            else:
                result.total_pm_pty_bytes += len(pm_prime_buf)
                prime_classification = classify_pm_prefix(pm_prime_buf)
                if self._on_turn is not None:
                    try:
                        self._on_turn()
                    except Exception as e:
                        logger.warning("[granite-container] on_turn callback raised: %s", e)
                prime_outcome = self._route_pm_classification(
                    prime_classification, pm_prime_buf, turn_index=-1, result=result
                )
                if prime_outcome.should_break:
                    result.exit_reason = prime_outcome.exit_reason or result.exit_reason
                else:
                    # PM's prime turn was routed to Dev (or was unknown/
                    # empty-dev). Set the stale-buffer guard so the first
                    # steady-state iteration forces a fresh _cycle_idle
                    # before classifying (prevents re-reading the prime
                    # buffer on the very first turn).
                    self._prime_relayed = True
                    self._prime_pm_buf_hash = hash(pm_prime_buf)

            # Steady state.
            if result.exit_reason == "in_progress":
                for turn in range(self.max_turns):
                    # Stale-buffer guard (issue #1644): on the first
                    # iteration after the prime-turn relay, force a
                    # fresh idle read so we classify genuinely new PM
                    # output rather than the already-processed prime
                    # buffer.
                    if turn == 0 and self._prime_relayed and self._prime_pm_buf_hash is not None:
                        guard_idle, guard_buf, _, _ = self._cycle_idle(self._pm_pty)
                        if guard_idle and hash(guard_buf) == self._prime_pm_buf_hash:
                            # PM has not produced anything new yet; nudge
                            # it to continue so the loop sees fresh output.
                            logger.info(
                                "container: prime stale-buffer guard fired — "
                                "nudging PM for fresh output"
                            )

                    # Wait for PM idle.
                    pm_idle, pm_buf, pm_marker, pm_ms = self._cycle_idle(self._pm_pty)
                    if not pm_idle:
                        result.exit_reason = "pm_hang"
                        result.exit_message = (
                            f"PM did not reach idle within {CYCLE_IDLE_TIMEOUT_S}s"
                        )
                        break

                    result.total_pm_pty_bytes += len(pm_buf)

                    # Classify PM's output. The classifier is a regex
                    # parse on PM's first non-empty line; no ollama
                    # call. The classification is what determines
                    # whether we route to Dev, route to user (results
                    # log), or exit on complete.
                    classification = classify_pm_prefix(pm_buf)
                    # Per-turn progress hook (TD1): every classified PM
                    # turn counts as progress for the two-tier no-progress
                    # detector, regardless of destination.
                    if self._on_turn is not None:
                        try:
                            self._on_turn()
                        except Exception as e:
                            logger.warning(
                                "[granite-container] on_turn callback raised: %s",
                                e,
                            )

                    outcome = self._route_pm_classification(
                        classification, pm_buf, turn_index=turn, result=result
                    )
                    if outcome.should_break:
                        result.exit_reason = outcome.exit_reason or result.exit_reason
                        break

                # If we ran out the for loop without breaking, max_turns
                # is the exit reason.
                if result.exit_reason == "in_progress":
                    result.exit_reason = "pm_max_turns"
                    result.exit_message = (
                        f"reached max_turns={self.max_turns} without a [/complete]"
                    )

            # Wrap-up guard (issue #1647): when the run is in a
            # successful-shaped terminal state but PM never delivered a
            # user-facing message, drive PM to produce one. This
            # guarantees the human always receives some output.
            _successful_exits = {"pm_complete", "pm_user", "pm_max_turns"}
            if result.exit_reason in _successful_exits and not result.user_facing_routed:
                self._run_wrapup_guard(result)

        except Exception as e:
            result.exit_reason = "exception"
            result.exit_message = _truncate_exit_message(f"{type(e).__name__}: {e}")
        finally:
            # Try to capture a resume UUID from the dying PM.
            try:
                if self._pm_pty is not None and self._pm_pty.isalive():
                    result.resume_uuid = self._pm_pty.last_resume_uuid()
            except Exception:
                pass
            self._close_pair()
            self._run_pkill_fallback()

        return result

    # -- Routing helper ---------------------------------------------------

    def _route_pm_classification(
        self,
        classification: Any,
        pm_buf: str,
        turn_index: int,
        result: ContainerResult,
    ) -> RouteOutcome:
        """Route a single classified PM turn and update result in place.

        Handles all four routing destinations:
          - unknown / empty-dev: compliance miss — re-prompt PM with
            PM_COMPLIANCE_NUDGE and return (should_break=False). The
            COMPLIANCE_NUDGE is only for genuine mid-loop misses;
            the wrap-up path does not call this with unknown turns.
          - complete (non-empty): deliver via on_complete_payload,
            set result.user_facing_routed=True, return should_break=True.
          - complete (empty): NOT user-facing — falls through to wrap-up
            guard instead of delivering. Returns should_break=True with
            pm_complete so the loop exits; user_facing_routed stays False.
          - user: deliver via on_user_payload, set
            result.user_facing_routed=True, return should_break=True.
          - dev: extract dev_prompt, write to Dev PTY, cycle Dev idle,
            summarize_for_pm, write summary to PM PTY, capture
            self._last_dev_report. Returns should_break=False.

        All exits (complete/user) set result.exit_reason before
        returning. The caller sets result.exit_reason from
        outcome.exit_reason only when should_break=True.
        """
        if classification.compliance_miss:
            result.classification_compliance_misses += 1

        if classification.destination == "unknown":
            result.parse_failures += 1
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="unknown",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=0,
                granite_extract_ms=0,
                granite_summarize_ms=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            self._pm_pty.write(PM_COMPLIANCE_NUDGE)
            return RouteOutcome(should_break=False, exit_reason=None)

        if classification.destination == "complete":
            payload = classification.payload
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="complete",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=len(payload),
                granite_extract_ms=0,
                granite_summarize_ms=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            result.exit_message = payload
            if payload.strip():
                # Non-empty [/complete] — deliver to user and mark routed.
                if self._on_complete_payload is not None:
                    try:
                        self._on_complete_payload(payload)
                        result.user_facing_routed = True
                    except Exception as e:
                        logger.warning(
                            "[granite-container] on_complete_payload callback raised: %s",
                            e,
                        )
            # Empty [/complete] is NOT user-facing — user_facing_routed
            # stays False and the wrap-up guard will drive PM to produce
            # a real summary.
            return RouteOutcome(should_break=True, exit_reason="pm_complete")

        if classification.destination == "user":
            payload = classification.payload
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="user",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=len(payload),
                granite_extract_ms=0,
                granite_summarize_ms=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            result.exit_message = payload
            if self._on_user_payload is not None:
                try:
                    self._on_user_payload(payload)
                    result.user_facing_routed = True
                except Exception as e:
                    logger.warning(
                        "[granite-container] on_user_payload callback raised: %s",
                        e,
                    )
            return RouteOutcome(should_break=True, exit_reason="pm_user")

        # destination == "dev" — extract a developer instruction.
        if not classification.payload.strip():
            # PM emitted [/dev] but no payload; compliance miss —
            # re-prompt PM so the next read sees fresh output.
            result.parse_failures += 1
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="unknown",
                compliance_miss=True,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=0,
                granite_extract_ms=0,
                granite_summarize_ms=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            self._pm_pty.write(PM_COMPLIANCE_NUDGE)
            return RouteOutcome(should_break=False, exit_reason=None)

        extract_start = time.monotonic()
        try:
            dev_prompt = extract_dev_prompt(pm_buf)
        except Exception as e:
            result.exit_message = _truncate_exit_message(f"extract_dev_prompt failed: {e}")
            return RouteOutcome(should_break=True, exit_reason="exception")
        extract_ms = int((time.monotonic() - extract_start) * 1000)

        if not dev_prompt.strip():
            result.parse_failures += 1
            turn_record = TurnRecord(
                turn_index=turn_index,
                pm_idle_ms=0,
                dev_idle_ms=0,
                classification="dev",
                compliance_miss=classification.compliance_miss,
                pm_first_line=classification.raw_first_line,
                routed_payload_chars=0,
                granite_extract_ms=extract_ms,
                granite_summarize_ms=0,
                pm_idle_marker="",
                dev_idle_marker="",
            )
            result.turns.append(turn_record)
            self._pm_pty.write(PM_COMPLIANCE_NUDGE)
            return RouteOutcome(should_break=False, exit_reason=None)

        # Write to Dev's PTY (await idle first to enforce the
        # "write only to idle PTYs" invariant).
        await_idle, _, _, _ = self._cycle_idle(self._dev_pty)
        if not await_idle:
            result.exit_message = "Dev did not reach idle before PM instruction"
            return RouteOutcome(should_break=True, exit_reason="dev_hang")
        self._dev_pty.write(dev_prompt)

        # Wait for Dev to respond and reach idle.
        dev_idle, dev_buf, dev_marker, dev_ms = self._cycle_idle(self._dev_pty)
        if not dev_idle:
            result.exit_message = f"Dev did not reach idle within {CYCLE_IDLE_TIMEOUT_S}s"
            return RouteOutcome(should_break=True, exit_reason="dev_hang")

        result.total_dev_pty_bytes += len(dev_buf)

        # Summarize Dev's output for PM.
        summarize_start = time.monotonic()
        try:
            summary = summarize_for_pm(dev_buf)
        except Exception as e:
            result.exit_message = _truncate_exit_message(f"summarize_for_pm failed: {e}")
            return RouteOutcome(should_break=True, exit_reason="exception")
        summarize_ms = int((time.monotonic() - summarize_start) * 1000)

        # Capture the summary as the last Dev report for the wrap-up
        # guard (issue #1647).
        self._last_dev_report = summary

        # Write summary to PM's PTY.
        await_pm, _, _, _ = self._cycle_idle(self._pm_pty)
        if not await_pm:
            result.exit_message = "PM did not reach idle before summary"
            return RouteOutcome(should_break=True, exit_reason="pm_hang")
        self._pm_pty.write(summary)

        turn_record = TurnRecord(
            turn_index=turn_index,
            pm_idle_ms=0,
            dev_idle_ms=dev_ms,
            classification="dev",
            compliance_miss=classification.compliance_miss,
            pm_first_line=classification.raw_first_line,
            routed_payload_chars=len(dev_prompt),
            granite_extract_ms=extract_ms,
            granite_summarize_ms=summarize_ms,
            pm_idle_marker="",
            dev_idle_marker=dev_marker,
        )
        result.turns.append(turn_record)
        return RouteOutcome(should_break=False, exit_reason=None)

    # -- Wrap-up guard (issue #1647) --------------------------------------

    def _run_wrapup_guard(self, result: ContainerResult) -> None:
        """Drive PM to produce a user-facing message when none was delivered.

        Called when the run exits in a successful-shaped state
        (pm_complete, pm_user, pm_max_turns) but result.user_facing_routed
        is still False. The guard:
          1. Builds a seed from self._last_dev_report (or a fresh Dev
             idle read + summarize, or DEV_REPORT_UNAVAILABLE).
          2. Writes PM_WRAPUP_PROMPT to PM's PTY.
          3. Cycles PM idle and routes via _route_pm_classification
             (capped at MAX_WRAPUP_ATTEMPTS=1).
          4. If PM still hasn't delivered, sends OPERATOR_TERMINAL_MESSAGE
             directly via on_user_payload so the human always gets something.

        Mutates result in place. All errors are swallowed — the wrap-up
        guard must never crash the run.
        """
        try:
            # Build the seed report.
            if self._last_dev_report:
                seed = self._last_dev_report
            elif self._dev_pty is not None:
                try:
                    _, dev_buf, _, _ = self._cycle_idle(self._dev_pty)
                    seed = summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE
                except Exception:
                    seed = DEV_REPORT_UNAVAILABLE
            else:
                seed = DEV_REPORT_UNAVAILABLE

            for _attempt in range(MAX_WRAPUP_ATTEMPTS):
                # Write the wrap-up prompt to PM.
                await_pm, _, _, _ = self._cycle_idle(self._pm_pty)
                if not await_pm:
                    logger.warning("[granite-container] wrap-up guard: PM hang waiting for idle")
                    break
                self._pm_pty.write(PM_WRAPUP_PROMPT.format(seed=seed))

                # Wait for PM to respond.
                pm_idle, pm_buf, _, _ = self._cycle_idle(self._pm_pty)
                if not pm_idle:
                    logger.warning("[granite-container] wrap-up guard: PM hung after wrapup prompt")
                    break

                wrapup_classification = classify_pm_prefix(pm_buf)
                outcome = self._route_pm_classification(
                    wrapup_classification, pm_buf, turn_index=-2, result=result
                )
                if result.user_facing_routed:
                    result.exit_reason = outcome.exit_reason or result.exit_reason
                    return

            # PM still silent after MAX_WRAPUP_ATTEMPTS — deliver canned
            # terminal message directly so the human always gets something.
            if not result.user_facing_routed and self._on_user_payload is not None:
                try:
                    self._on_user_payload(OPERATOR_TERMINAL_MESSAGE)
                    result.user_facing_routed = True
                    result.exit_reason = "pm_no_user_message"
                    logger.info(
                        "[granite-container] wrap-up guard delivered OPERATOR_TERMINAL_MESSAGE"
                    )
                except Exception as e:
                    logger.warning(
                        "[granite-container] wrap-up guard: terminal message delivery failed: %s",
                        e,
                    )
        except Exception as e:
            logger.warning("[granite-container] wrap-up guard raised unexpectedly: %s", e)

    # -- Ping-pong (two-PTY coordination) test ---------------------------

    def run_ping_pong_test(self) -> bool:
        """Spawn both PTYs, prime both, ping each in turn.

        The two-PTY coordination test runs BEFORE the granite
        classification layer is added (per the plan's
        *Technical Approach*). If this fails, the multi-PTY idle
        heuristic is broken; do not add the classification layer.

        Returns True if both pings reached idle without a hang.
        """
        try:
            self._spawn_pair()
        except Exception as e:
            logger.warning("ping_pong spawn failed: %s", e)
            return False
        try:
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD, include_user_message=True)
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD, include_user_message=False)
            # Ping each in turn.
            self._pm_pty.write("ping")
            pm_result = self._pm_pty.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            self._dev_pty.write("ping")
            dev_result = self._dev_pty.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            return pm_result.saw_idle and dev_result.saw_idle
        except Exception as e:
            logger.warning("ping_pong failed: %s", e)
            return False
        finally:
            self._close_pair()
            self._run_pkill_fallback()


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def result_to_json(result: ContainerResult) -> str:
    """Serialize a ContainerResult to a JSON string for the results doc.

    The shape is `ContainerResult` -> dict -> JSON. The classifier
    is the only place that knows the enums; the container's
    serialization uses the dataclass's asdict.
    """
    payload = asdict(result)
    return json.dumps(payload, indent=2, default=str)
