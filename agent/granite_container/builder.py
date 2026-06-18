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

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Subprocess timeout for Pi-mode builder (future use, Task 3).
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
