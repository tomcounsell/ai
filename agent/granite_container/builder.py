"""BuilderHarness protocol and PtyClaudeBuilder implementation.

The BuilderHarness abstraction decouples the granite container's
dev-relay branch from the concrete PTY/claude implementation so that
alternative builders (e.g. a Pi subprocess) can be plugged in without
touching the container loop.

The container owns:
- PTY lifecycle (spawn, close)
- Empty-return fallback gate (transcript_fallback_count / DEV_REPORT_UNAVAILABLE)
- _last_dev_report assignment

The builder owns:
- Executing one dev turn (cycle idle -> write -> snapshot -> cycle idle -> read)
- Returning the raw assistant text (or "" on miss)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Subprocess timeout for Pi-mode builder.
# Distinct from CYCLE_IDLE_TIMEOUT_S (12-hour PTY ceiling) — Pi is a
# one-shot subprocess; 10 minutes is a generous wall-clock ceiling for
# a single self-contained turn.
PI_SUBPROCESS_TIMEOUT_S = 10 * 60


@runtime_checkable
class BuilderHarness(Protocol):
    """Protocol satisfied by any builder that can run a single dev turn."""

    @property
    def name(self) -> str:
        """Human-readable name of this builder (e.g. 'claude', 'pi')."""
        ...

    def prepare(self, spec: Any) -> None:
        """One-time setup called before the first run_turn.

        May be a no-op if the PTY is already running.
        ``spec`` is an opaque container-level run spec (ContainerRunSpec or
        similar); builders that need it cast it themselves.
        """
        ...

    def run_turn(self, prompt: str) -> str:
        """Execute one dev turn: write *prompt* and return the assistant text.

        Returns the verbatim last-assistant-text from Dev's JSONL transcript,
        or ``""`` if the transcript read returned empty OR if a hang was
        detected (the container caller handles both cases via ``last_hung``).

        Preserved surfaces that #1721 (lossless checkpoint) depends on —
        these identifiers must remain stable inside the implementation:
        - dev_transcript  (= result.dev_transcript_path)
        - dev_baseline    (= text_bearing_count(dev_transcript))
        - text_bearing_count
        - last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)
        """
        ...

    def close(self) -> None:
        """Tear-down.  May be a no-op when the PTY is owned by the container."""
        ...


class PtyClaudeBuilder:
    """BuilderHarness backed by the existing Dev PTY + JSONL transcript path.

    This is a zero-behaviour-change extraction of the dev-relay branch of
    ``_route_pm_classification``.  The container constructs one instance per
    container run and passes ``self._cycle_idle`` so the builder can reuse
    the same idle-wait helper.

    The container remains the owner of:
    - ``result.transcript_fallback_count`` (empty-return gate)
    - ``self._last_dev_report`` (wrap-up guard capture)
    - ``result.total_dev_pty_bytes`` (I/O accounting)

    Those are threaded through the caller in ``_route_pm_classification``,
    not here.

    Per-turn metadata (available after ``run_turn`` returns):
    - ``last_dev_buf``    — raw PTY buffer from the second _cycle_idle call
    - ``last_dev_marker`` — idle marker from the second _cycle_idle call
    - ``last_dev_ms``     — elapsed ms from the second _cycle_idle call
    - ``last_hung``       — True if a hang was detected (pre-write or post-write)
    """

    def __init__(
        self,
        dev_pty: Any,
        dev_transcript_getter: Callable[[], str | None],
        cycle_idle_fn: Callable[[Any], tuple[bool, str, str, int]],
    ) -> None:
        """
        Args:
            dev_pty: PTYDriver instance for the Dev persona.
            dev_transcript_getter: Zero-arg callable that returns the path to
                Dev's JSONL transcript (or None before the PTY has started).
                The container passes ``lambda: result.dev_transcript_path``.
            cycle_idle_fn: The container's ``_cycle_idle`` bound method,
                used to enforce the "write only to idle PTYs" invariant and to
                wait for Dev to finish responding.
        """
        self._dev_pty = dev_pty
        self._dev_transcript_getter = dev_transcript_getter
        self._cycle_idle = cycle_idle_fn

        # Per-turn metadata populated by run_turn, read by the container.
        self.last_dev_buf: str = ""
        self.last_dev_marker: str = ""
        self.last_dev_ms: int = 0
        self.last_hung: bool = False

    @property
    def name(self) -> str:
        return "claude"

    def prepare(self, spec: Any) -> None:
        """No-op — PTY setup is done at container level before run_turn."""

    def run_turn(self, prompt: str) -> str:
        """Execute one dev turn and return the last assistant text.

        Sequence (mirrors the pre-refactor dev-relay path exactly):
        1. _cycle_idle(dev) — enforce idle before write
        2. dev_pty.write(prompt)
        3. dev_baseline = text_bearing_count(dev_transcript)
        4. _cycle_idle(dev) — wait for Dev to finish responding
        5. last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)
        6. Return text (or "" on miss/hang)

        Sets ``last_hung=True`` if either _cycle_idle call reports not-idle.
        The container caller checks ``last_hung`` to distinguish a hang
        (exit_reason="dev_hang") from an empty transcript read
        (transcript_fallback_count bump).

        Preserved surfaces (#1721 lossless checkpoint):
        - dev_transcript  (result.dev_transcript_path via getter)
        - dev_baseline    (text_bearing_count(dev_transcript))
        - text_bearing_count
        - last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)
        """
        from agent.granite_container.transcript_tailer import (
            last_assistant_text,
            text_bearing_count,
        )

        # Reset per-turn metadata.
        self.last_dev_buf = ""
        self.last_dev_marker = ""
        self.last_dev_ms = 0
        self.last_hung = False

        # Step 1: Wait for Dev to be idle before writing.
        await_idle, _, _, _ = self._cycle_idle(self._dev_pty)
        if not await_idle:
            self.last_hung = True
            logger.warning("[PtyClaudeBuilder] Dev did not reach idle before write")
            return ""

        # Step 2: Write prompt to Dev's PTY.
        self._dev_pty.write(prompt)

        # Step 3: Snapshot text-bearing count before waiting for response.
        dev_transcript = self._dev_transcript_getter()
        dev_baseline = text_bearing_count(dev_transcript) if dev_transcript else 0

        # Step 4: Wait for Dev to finish responding.
        dev_idle, dev_buf, dev_marker, dev_ms = self._cycle_idle(self._dev_pty)
        self.last_dev_buf = dev_buf
        self.last_dev_marker = dev_marker
        self.last_dev_ms = dev_ms
        if not dev_idle:
            self.last_hung = True
            logger.warning("[PtyClaudeBuilder] Dev did not reach idle after write")
            return ""

        # Step 5: Read Dev's verbatim last assistant text from JSONL transcript.
        dev_text = (
            last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)
            if dev_transcript
            else ""
        )
        return dev_text

    def close(self) -> None:
        """No-op — PTY lifecycle is owned by the container."""


# ---------------------------------------------------------------------------
# Pi subprocess builder
# ---------------------------------------------------------------------------


def parse_pi_final_text(stream: str) -> str:
    """Extract the final assistant text from a Pi --mode json NDJSON event stream.

    Pi streams NDJSON events to stdout. The terminal ``agent_end`` event
    carries the full ``messages`` array. We take the final assistant message
    and concatenate all content entries where ``type == "text"``, dropping
    ``type == "thinking"``.

    Returns ``""`` on: no stream, no agent_end event, no text content,
    parse error. This drives the container's DEV_REPORT_UNAVAILABLE fallback
    (caller-owned, Risk 5).
    """
    if not stream:
        return ""

    agent_end_data = None
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "agent_end":
            agent_end_data = event

    if agent_end_data is None:
        return ""

    messages = agent_end_data.get("messages", [])
    # Find the last assistant message.
    last_assistant = None
    for msg in messages:
        if msg.get("role") == "assistant":
            last_assistant = msg

    if last_assistant is None:
        return ""

    content = last_assistant.get("content", [])
    text_parts = [
        c.get("text", "") if isinstance(c, dict) else str(c)
        for c in content
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    return "".join(text_parts)


class PiSubprocessBuilder:
    """BuilderHarness implementation that drives Pi as a one-shot subprocess builder.

    Runs Pi in non-interactive mode: ``pi -p --mode json``. Primes via two
    ``--append-system-prompt`` flags: canonical rails first, Pi-tuned persona
    delta second.

    ``builder_cwd`` MUST be the same directory the claude Dev PTY runs in
    (PTYDriver.cwd, resolved at spawn time). It is NEVER None — the constructor
    raises if falsy, preventing silent ``Popen(cwd=None)`` which would inherit
    the repo root and defeat worktree isolation (Risk 6).

    Per-turn metadata (compatible surface with PtyClaudeBuilder — the container
    reads these after ``run_turn`` returns):
    - ``last_dev_buf``    — always ``""`` (Pi has no PTY buffer)
    - ``last_dev_marker`` — always ``""``
    - ``last_dev_ms``     — always ``0``
    - ``last_hung``       — always ``False`` (timeout returns ``""`` instead)
    """

    name = "pi"

    # Compatible surface with PtyClaudeBuilder so the container can read
    # these unconditionally after run_turn returns.
    last_dev_buf: str = ""
    last_dev_marker: str = ""
    last_dev_ms: int = 0
    last_hung: bool = False

    def __init__(
        self,
        builder_cwd: str,
        rails_path: str,
        persona_path: str,
        provider: str = "google",
        model: str = "ollama/gemma4:31b",
        timeout_s: float = PI_SUBPROCESS_TIMEOUT_S,
    ) -> None:
        """
        Args:
            builder_cwd: Working directory for the Pi subprocess. MUST be
                non-empty/non-None — falsy value raises immediately.
                Use the same cwd as the claude Dev PTY (Risk 6).
            rails_path: Absolute path to the canonical rails file
                (``.claude/commands/granite/_prime-rails.md``). Loaded
                first via ``--append-system-prompt`` so Pi cannot override
                the no-push-to-main / principal-context guards.
            persona_path: Absolute path to the Pi-tuned dev persona delta
                (``config/personas/granite/pi_dev_rails.md``). Loaded
                second to add Pi-specific role framing on top of the rails.
            provider: Pi provider string passed to ``--provider``.
            model: Pi model identifier passed to ``--model``.
            timeout_s: Wall-clock timeout for a single ``run_turn`` call.
                On expiry the whole process group is SIGKILL'd and ``""``
                is returned (DEV_REPORT_UNAVAILABLE path, Risk 5).
        """
        if not builder_cwd:
            raise ValueError(
                "PiSubprocessBuilder: builder_cwd must be non-empty/non-None. "
                "Falsy cwd would spawn Pi with cwd=None (inheriting repo root), "
                "defeating worktree isolation (Risk 6)."
            )
        self.builder_cwd = builder_cwd
        self.rails_path = rails_path
        self.persona_path = persona_path
        self.provider = provider
        self.model = model
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    def prepare(self, spec: Any = None) -> None:
        """No-op: Pi is stateless (primed via --append-system-prompt flags)."""

    def run_turn(self, prompt: str) -> str:
        """Execute one Pi turn and return the final assistant text.

        Spawns ``pi -p --mode json --no-session`` with the prompt on stdin.
        The output is NDJSON; ``parse_pi_final_text`` extracts the terminal
        ``agent_end`` assistant text.

        On timeout: kills the whole process group (``start_new_session=True``
        gives Pi its own pgid) and returns ``""``.
        On non-zero exit: logs a warning and returns ``""``.
        On ``pi`` not found: logs an error and returns ``""``.
        All ``""`` returns drive DEV_REPORT_UNAVAILABLE in the container
        (Risk 5 — caller-owned gate).
        """
        cmd = [
            "pi",
            "-p",
            "--mode",
            "json",
            "--no-session",
            "--append-system-prompt",
            self.rails_path,
            "--append-system-prompt",
            self.persona_path,
            "--provider",
            self.provider,
            "--model",
            self.model,
            "--tools",
            "read,bash,edit,write",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.builder_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # own pgid — reap whole subtree on timeout
            )
            self._proc = proc
            try:
                out, err = proc.communicate(input=prompt, timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                logger.warning(
                    "Pi builder turn timed out after %ss; killed process group",
                    self.timeout_s,
                )
                return ""
            finally:
                self._proc = None

            if proc.returncode != 0:
                logger.warning(
                    "Pi builder exited with code %d; stderr: %s",
                    proc.returncode,
                    (err or "").strip()[:500],
                )
                return ""

            return parse_pi_final_text(out)
        except FileNotFoundError:
            logger.error("Pi builder: 'pi' CLI not found on PATH")
            return ""
        except Exception as e:
            logger.warning("Pi builder run_turn failed: %s", e)
            return ""

    def close(self) -> None:
        """Reap any live subprocess."""
        if self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except Exception:
                pass
            self._proc = None
