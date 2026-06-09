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
     hang (await_idle timeout), startup_unresolved (parser
     UNKNOWN past the startup window), or any exception

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
from typing import Any

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

# Startup window: how many idle cycles to spend watching for
# startup events before declaring startup_unresolved. The trust-
# folder prompt and update notice typically appear within the
# first 1-2 cycles; 10 is a comfortable safety margin.
STARTUP_WINDOW_CYCLES = 10

# Per-cycle idle timeout. If a PTY doesn't reach idle within this
# many seconds, the container treats it as a hang (pm_hang /
# dev_hang) and exits the loop.
CYCLE_IDLE_TIMEOUT_S = 120.0

# Per-cycle idle budget for the startup-phase poll loop. Short by
# design: the loop's job is to detect transient startup events
# (trust-folder, update notice), not to wait for a long model
# turn. The persona load completes inside `_prime_session`'s
# post-write wait; by the time the startup loop runs, the slash
# command has already returned and the TUI is at the prompt.
# A short budget means we cycle fast through the no-event case
# (10 × STARTUP_CYCLE_TIMEOUT_S total = 30s ceiling) and only
# burn the long steady-state budget (CYCLE_IDLE_TIMEOUT_S) once
# we enter the actual turn loop. Without this, the startup
# loop can consume 10 × 120s = 1200s of idle waits on a slow
# persona load (PR #1612 live run, June 2026).
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
    dev_hang, pm_hang, startup_unresolved, exception. The PoC's
    results doc renders this as the verdict.
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


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


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

    def _run_pkill_fallback(self) -> None:
        """Last-ditch teardown: kill any orphaned `claude --bypassPermissions` PTYs.

        Mirrors the probe's teardown at
        `scripts/probe_slash_arguments.py:367-373`. The container
        prefers `child.close(force=True)`; this is the safety net.
        """
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
        """
        result = pty.read_until_idle(
            min_content_bytes=min_content_bytes, timeout_s=CYCLE_IDLE_TIMEOUT_S
        )
        return (result.saw_idle, result.buffer, result.idle_marker, result.elapsed_ms)

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

    def _prime_session(self, pty: PTYDriver, slash_cmd: str) -> None:
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
        # Send the slash command + the user message as $ARGUMENTS.
        pty.write(f"{slash_cmd} {self.user_message}")
        # Wait for the model to actually finish the prime. The
        # LOADING_RE negative in read_until_idle blocks idle
        # declaration while the spinner ("Sprouting…", "Honking…",
        # "Synthesizing…", etc.) is on screen, so this read waits
        # for the model's "Worked for Ns" response (or times out
        # at PRIME_POST_WRITE_TIMEOUT_S).
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

        try:
            # Persona priming.
            logger.info("container: priming PM")
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD)
            logger.info("container: PM prime done")
            logger.info("container: priming Dev")
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD)
            logger.info("container: Dev prime done; entering startup loop")

            # Startup-phase loop. Watch both PTYs for known startup
            # events. Trust-folder is the most likely; the parser
            # dismisses it with "1\r".
            for cycle in range(STARTUP_WINDOW_CYCLES):
                # Short per-cycle budget: the persona load is done
                # by `_prime_session`'s post-write wait, so by the
                # time we reach the startup loop, the TUI is at the
                # prompt and any startup event is already painted.
                # 3s/cycle × 10 cycles = 30s ceiling for the no-
                # event case (vs. 10 × 120s = 1200s with the
                # steady-state budget, which silently burned the
                # harness watchdog in PR #1612 live run, June 2026).
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
                        break
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
            else:
                # All STARTUP_WINDOW_CYCLES exhausted without a known
                # startup event AND without both PTYs going idle. The
                # startup phase did not settle. The persona load on
                # Opus 4.8 high-effort can take longer than 30s after
                # the prime post-write budget returns, so this is
                # expected on a cold start. Fall through to the
                # steady-state loop rather than declaring
                # `startup_unresolved`: the steady-state's per-cycle
                # 120s budget will wait out the remaining persona
                # load. If the model has truly hung, the steady-state
                # loop will eventually exit with `pm_hang` and the
                # container will report a real failure mode.
                logger.info(
                    "container: startup did not settle after %d cycles; "
                    "falling through to steady state",
                    STARTUP_WINDOW_CYCLES,
                )

            # Steady state.
            for turn in range(self.max_turns):
                # Wait for PM idle.
                pm_idle, pm_buf, pm_marker, pm_ms = self._cycle_idle(self._pm_pty)
                if not pm_idle:
                    result.exit_reason = "pm_hang"
                    result.exit_message = f"PM did not reach idle within {CYCLE_IDLE_TIMEOUT_S}s"
                    break

                result.total_pm_pty_bytes += len(pm_buf)

                # Classify PM's output. The classifier is a regex
                # parse on PM's first non-empty line; no ollama
                # call. The classification is what determines
                # whether we route to Dev, route to user (results
                # log), or exit on complete.
                classification = classify_pm_prefix(pm_buf)
                if classification.compliance_miss:
                    result.classification_compliance_misses += 1
                if classification.destination == "unknown":
                    result.parse_failures += 1
                    # No usable routing; PM is the source of the
                    # miss. Re-prompt PM with a corrective nudge so
                    # the next read sees fresh output — without a
                    # write PM stays idle on the same buffer and the
                    # loop reclassifies the identical miss every tick
                    # until max_turns.
                    turn_record = TurnRecord(
                        turn_index=turn,
                        pm_idle_ms=pm_ms,
                        dev_idle_ms=0,
                        classification="unknown",
                        compliance_miss=classification.compliance_miss,
                        pm_first_line=classification.raw_first_line,
                        routed_payload_chars=0,
                        granite_extract_ms=0,
                        granite_summarize_ms=0,
                        pm_idle_marker=pm_marker,
                        dev_idle_marker="",
                    )
                    result.turns.append(turn_record)
                    self._pm_pty.write(PM_COMPLIANCE_NUDGE)
                    continue

                # Routing.
                if classification.destination == "complete":
                    turn_record = TurnRecord(
                        turn_index=turn,
                        pm_idle_ms=pm_ms,
                        dev_idle_ms=0,
                        classification="complete",
                        compliance_miss=classification.compliance_miss,
                        pm_first_line=classification.raw_first_line,
                        routed_payload_chars=len(classification.payload),
                        granite_extract_ms=0,
                        granite_summarize_ms=0,
                        pm_idle_marker=pm_marker,
                        dev_idle_marker="",
                    )
                    result.turns.append(turn_record)
                    result.exit_reason = "pm_complete"
                    result.exit_message = classification.payload
                    # BridgeAdapter hook: emit the trailing
                    # summary to the user-visible channel
                    # (Telegram relay) at the end of the run.
                    if self._on_complete_payload is not None:
                        try:
                            self._on_complete_payload(classification.payload)
                        except Exception as e:
                            logger.warning(
                                "[granite-container] on_complete_payload callback raised: %s",
                                e,
                            )
                    break

                if classification.destination == "user":
                    # User-address text goes to the user-visible
                    # channel (Telegram relay) mid-loop. With no
                    # user reply to re-prompt PM, the PoC's
                    # headless invocation is terminal — exit on
                    # pm_user. A bridge-wired deployment also
                    # exits on pm_user; the adapter has already
                    # delivered the payload to the chat.
                    turn_record = TurnRecord(
                        turn_index=turn,
                        pm_idle_ms=pm_ms,
                        dev_idle_ms=0,
                        classification="user",
                        compliance_miss=classification.compliance_miss,
                        pm_first_line=classification.raw_first_line,
                        routed_payload_chars=len(classification.payload),
                        granite_extract_ms=0,
                        granite_summarize_ms=0,
                        pm_idle_marker=pm_marker,
                        dev_idle_marker="",
                    )
                    result.turns.append(turn_record)
                    result.exit_reason = "pm_user"
                    result.exit_message = classification.payload
                    # BridgeAdapter hook: emit the user-address
                    # payload to the user-visible channel
                    # (Telegram relay) BEFORE exiting. The
                    # callback is synchronous and blocks the
                    # thread until delivery completes (per the
                    # ADV-5 hardening); the thread holds for
                    # the duration of the network call.
                    if self._on_user_payload is not None:
                        try:
                            self._on_user_payload(classification.payload)
                        except Exception as e:
                            logger.warning(
                                "[granite-container] on_user_payload callback raised: %s",
                                e,
                            )
                    break

                # destination == "dev" — extract a developer
                # instruction and write to Dev's PTY.
                if not classification.payload.strip():
                    # PM emitted [/dev] but no payload; treat as a
                    # compliance miss and re-prompt PM with the
                    # corrective nudge so the next read sees fresh
                    # output rather than spinning on the same empty
                    # instruction until max_turns.
                    result.parse_failures += 1
                    turn_record = TurnRecord(
                        turn_index=turn,
                        pm_idle_ms=pm_ms,
                        dev_idle_ms=0,
                        classification="unknown",
                        compliance_miss=True,
                        pm_first_line=classification.raw_first_line,
                        routed_payload_chars=0,
                        granite_extract_ms=0,
                        granite_summarize_ms=0,
                        pm_idle_marker=pm_marker,
                        dev_idle_marker="",
                    )
                    result.turns.append(turn_record)
                    self._pm_pty.write(PM_COMPLIANCE_NUDGE)
                    continue

                extract_start = time.monotonic()
                try:
                    dev_prompt = extract_dev_prompt(pm_buf)
                except Exception as e:
                    result.exit_reason = "exception"
                    result.exit_message = _truncate_exit_message(f"extract_dev_prompt failed: {e}")
                    break
                extract_ms = int((time.monotonic() - extract_start) * 1000)

                if not dev_prompt.strip():
                    # Granite produced an empty dev_prompt from a
                    # well-formed [/dev] turn; re-prompt PM with the
                    # corrective nudge so the next read yields fresh
                    # output rather than re-extracting the same empty
                    # result from the same buffer until max_turns.
                    result.parse_failures += 1
                    turn_record = TurnRecord(
                        turn_index=turn,
                        pm_idle_ms=pm_ms,
                        dev_idle_ms=0,
                        classification="dev",
                        compliance_miss=classification.compliance_miss,
                        pm_first_line=classification.raw_first_line,
                        routed_payload_chars=0,
                        granite_extract_ms=extract_ms,
                        granite_summarize_ms=0,
                        pm_idle_marker=pm_marker,
                        dev_idle_marker="",
                    )
                    result.turns.append(turn_record)
                    self._pm_pty.write(PM_COMPLIANCE_NUDGE)
                    continue

                # Write to Dev's PTY (await idle first to enforce
                # the "write only to idle PTYs" invariant).
                await_idle, _, _, _ = self._cycle_idle(self._dev_pty)
                if not await_idle:
                    result.exit_reason = "dev_hang"
                    result.exit_message = "Dev did not reach idle before PM instruction"
                    break
                self._dev_pty.write(dev_prompt)

                # Wait for Dev to respond and reach idle.
                dev_idle, dev_buf, dev_marker, dev_ms = self._cycle_idle(self._dev_pty)
                if not dev_idle:
                    result.exit_reason = "dev_hang"
                    result.exit_message = f"Dev did not reach idle within {CYCLE_IDLE_TIMEOUT_S}s"
                    break

                result.total_dev_pty_bytes += len(dev_buf)

                # Summarize Dev's output for PM.
                summarize_start = time.monotonic()
                try:
                    summary = summarize_for_pm(dev_buf)
                except Exception as e:
                    result.exit_reason = "exception"
                    result.exit_message = _truncate_exit_message(f"summarize_for_pm failed: {e}")
                    break
                summarize_ms = int((time.monotonic() - summarize_start) * 1000)

                # Write summary to PM's PTY.
                await_pm, _, _, _ = self._cycle_idle(self._pm_pty)
                if not await_pm:
                    result.exit_reason = "pm_hang"
                    result.exit_message = "PM did not reach idle before summary"
                    break
                self._pm_pty.write(summary)

                turn_record = TurnRecord(
                    turn_index=turn,
                    pm_idle_ms=pm_ms,
                    dev_idle_ms=dev_ms,
                    classification="dev",
                    compliance_miss=classification.compliance_miss,
                    pm_first_line=classification.raw_first_line,
                    routed_payload_chars=len(dev_prompt),
                    granite_extract_ms=extract_ms,
                    granite_summarize_ms=summarize_ms,
                    pm_idle_marker=pm_marker,
                    dev_idle_marker=dev_marker,
                )
                result.turns.append(turn_record)

            # If we ran out the for loop without breaking, max_turns
            # is the exit reason.
            if result.exit_reason == "in_progress":
                result.exit_reason = "pm_max_turns"
                result.exit_message = f"reached max_turns={self.max_turns} without a [/complete]"

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
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD)
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD)
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
