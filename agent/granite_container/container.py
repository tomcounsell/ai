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
import shutil
import subprocess
import tempfile
import time
import uuid
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

# Cap on the size of `ContainerResult.exit_message`. A multi-KB
# traceback or ollama error body can land here on the exception
# branch, and the result is published into the Telegram relay —
# keep the message bounded so a single failure doesn't flood a
# chat. 500 chars matches the Telegram message clamp the relay
# already enforces downstream; truncating here keeps the relay
# clean and the JSON results doc readable.
EXIT_MESSAGE_MAX_CHARS = 500


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

    `exit_reason` is one of: pm_complete, pm_max_turns, dev_hang,
    pm_hang, startup_unresolved, exception. The PoC's results doc
    renders this as the verdict.
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
    ) -> None:
        if not user_message.strip():
            raise ValueError("Container.user_message must be non-empty")
        self.user_message = user_message
        self.cwd = cwd
        self.max_turns = max_turns
        self._pm_model = pm_model
        self._dev_model = dev_model
        self._pm_pty: PTYDriver | None = None
        self._dev_pty: PTYDriver | None = None
        self._sandbox: tuple[str, str] | None = None

    # -- Lifecycle --------------------------------------------------------

    def _spawn_pair(self) -> None:
        """Spawn both PTYs and prime both personas."""
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
        for pty in (self._pm_pty, self._dev_pty):
            if pty is not None:
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

    def _handle_startup(self, buffer_pm: str, buffer_dev: str) -> tuple[str | None, str, str]:
        """Run the startup-phase parser on both PTY buffers.

        Returns (response_for_pm, new_buffer_pm, new_buffer_dev)
        where response_for_pm is the text to write to PM's PTY (or
        None for no action), and the buffers are the post-dismissal
        state. The caller is responsible for slicing the buffer
        delta and feeding it to the parser.
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
            return (None, buffer_pm, buffer_dev)

        _, r = chosen
        return (r.response, buffer_pm, buffer_dev)

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

    def _prime_session(self, pty: PTYDriver, slash_cmd: str) -> None:
        """Send the persona-priming slash command to a PTY.

        The slash command body is invisible to the operator (F4);
        the only substrate signal is "did the model respond?". This
        helper sends the slash command and waits for the TUI to
        return to idle. The model may not have finished priming
        (e.g., still loading the persona) — that's fine, the
        steady-state loop's first read will see the prime-ack in
        the buffer.
        """
        # Wait for the TUI's initial idle (no content floor — the
        # first paint is the welcome frame, not a response).
        pty.read_until_idle(min_content_bytes=0, timeout_s=60.0)
        # Send the slash command + the user message as $ARGUMENTS.
        pty.write(f"{slash_cmd} {self.user_message}")

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

        try:
            # Persona priming.
            self._prime_session(self._pm_pty, PM_PRIME_SLASH_CMD)
            self._prime_session(self._dev_pty, DEV_PRIME_SLASH_CMD)

            # Startup-phase loop. Watch both PTYs for known startup
            # events. Trust-folder is the most likely; the parser
            # dismisses it with "1\r".
            for cycle in range(STARTUP_WINDOW_CYCLES):
                pm_idle = self._cycle_idle(self._pm_pty, min_content_bytes=0)
                dev_idle = self._cycle_idle(self._dev_pty, min_content_bytes=0)
                response, _, _ = self._handle_startup(pm_idle[1], dev_idle[1])
                if response is None:
                    # No startup event in this window — break if
                    # both PTYs are idle, otherwise keep watching.
                    if pm_idle[0] and dev_idle[0]:
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
                    # miss. The container treats this as a turn
                    # without a routing target; we still log it
                    # and continue (so the next PM turn has a
                    # chance to be classified).
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
                    break

                if classification.destination == "user":
                    # User-address text goes to the results log
                    # (the PoC does not wire to the bridge).
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
                    # Loop continues — PM may have more to say.
                    continue

                # destination == "dev" — extract a developer
                # instruction and write to Dev's PTY.
                if not classification.payload.strip():
                    # PM emitted [/dev] but no payload; treat as
                    # compliance miss and continue.
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
                    # Granite produced empty dev_prompt; treat as
                    # parse failure and continue.
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
