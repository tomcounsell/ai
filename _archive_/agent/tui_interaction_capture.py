"""Human-in-the-loop TUI interaction capture for local Claude Code sessions.

Pillar 3 of epic #1536: capture-and-store ONLY (no auto-emulation). This module
records *human decision* signal — the steering messages a human typed mid-run and
the slash-command sequence they ran — that the V1 telemetry recorder
(`agent/session_telemetry.py`) does not already capture, and at session end
distills it into one retrievable subconscious-memory observation.

It reuses two existing substrates without modifying them:

- ``agent.session_telemetry`` — the append-only per-session JSONL recorder. Two
  NEW event types ride the existing ``record_telemetry_event`` verbatim:
  ``slash_command`` and ``human_steering``. Tool approvals are NOT a new event —
  they are tallied at summarize time from the recorder's existing ``tool_use``
  events.
- ``models.memory.Memory`` — the subconscious-memory store. One distilled
  ``pattern`` observation per session, tagged ``tui-interaction`` and namespaced
  with ``agent_id=f"tui-{session_id}"`` so it stays separable from the Stop-hook
  Haiku *content* observations.

Both public functions are fail-silent: every body is wrapped in
``try/except Exception`` and never raises, matching the recorder + memory-bridge
policy so a capture failure can never block a hook or the TUI.

Capture surface: local Claude Code TUI sessions (via the ``UserPromptSubmit`` and
``Stop`` hooks). Bridge-driven sessions are out of scope — there is no
human-in-the-TUI there.

See ``docs/features/tui-interaction-capture.md`` and the plan at
``docs/plans/tui-interaction-capture.md`` (#1540).
"""

from __future__ import annotations

import logging

from agent.private_tag import strip_private
from agent.session_telemetry import read_session_timeline, record_telemetry_event
from models.memory import SOURCE_HUMAN, Memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Triviality gating constants.
#
# These are local copies of the gating knobs in
# ``.claude/hooks/hook_utils/memory_bridge.py`` (``TRIVIAL_PATTERNS`` and
# ``MIN_PROMPT_LENGTH``). That module lives on the hook-local sys.path and is NOT
# cleanly importable from ``agent/`` — so we deliberately keep our own copies here
# rather than couple to a hook module. Keep them in rough sync with the bridge
# values; exact parity is not required since this is a separate signal stream.
# ---------------------------------------------------------------------------

_TRIVIAL_PATTERNS: frozenset[str] = frozenset(
    {
        "yes",
        "no",
        "ok",
        "okay",
        "continue",
        "go",
        "go ahead",
        "thanks",
        "thank you",
        "done",
        "next",
        "sure",
        "right",
        "correct",
        "y",
        "n",
        "k",
        "yep",
        "nope",
        "got it",
        "sounds good",
        "lgtm",
    }
)

# Minimum stripped length for a non-slash prompt to count as substantive steering.
_MIN_STEERING_LENGTH: int = 50

# Maximum length of a stored steering snippet (privacy + recall compactness).
_MAX_SNIPPET_LENGTH: int = 120

# Maximum length of the distilled pattern string persisted as Memory content.
_MAX_CONTENT_LENGTH: int = 500

# Telemetry event types this module emits (additive to the V1 recorder schema).
_PROMPT_EVENT_TYPES = frozenset({"slash_command", "human_steering"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_prompt_event(session_id: str, prompt: str, cwd: str | None = None) -> None:
    """Classify a TUI prompt and record it as an interaction telemetry event.

    Fail-silent: the entire body is wrapped in ``try/except Exception`` and logged
    at DEBUG. NEVER raises.

    Classification:
        - A stripped prompt starting with ``/`` is a ``slash_command``. The
          command name is the token after ``/`` up to the first whitespace
          (e.g. ``/do-test args`` → ``do-test``). Slash commands are ALWAYS
          signal — no triviality gate. Emits
          ``{"type": "slash_command", "command": <name>}``.
        - Otherwise the prompt is a candidate ``human_steering`` event, gated
          BEFORE recording:
            1. ``strip_private`` the prompt.
            2. If the lowercased, stripped text is in ``_TRIVIAL_PATTERNS`` → skip.
            3. If its length is below ``_MIN_STEERING_LENGTH`` → skip.
            4. The ordinal is the count of existing prompt events
               (``slash_command`` / ``human_steering``) in the timeline. A
               non-slash prompt is steering ONLY when the ordinal > 0 — the first
               prompt of a session is the initial instruction, not a mid-run
               steer. Ordinal 0 → skip.
          Emits
          ``{"type": "human_steering", "ordinal": <n>, "snippet": <≤120 chars>}``.

    Args:
        session_id: The session to record against. Falsy → no-op.
        prompt: The raw human prompt text. Empty / whitespace-only / None → no-op.
        cwd: Accepted for hook call-site symmetry; currently unused. The
            ``UserPromptSubmit`` payload does NOT carry a turn counter, so the
            ordinal is derived internally from the timeline rather than passed in.
    """
    try:
        if not session_id:
            return
        if not prompt or not isinstance(prompt, str):
            return

        stripped = prompt.strip()
        if not stripped:
            return

        if stripped.startswith("/"):
            # Slash command — always signal. Command name = token after '/'.
            command = stripped[1:].split(maxsplit=1)[0] if len(stripped) > 1 else ""
            if not command:
                return
            event = {"type": "slash_command", "command": command}
            record_telemetry_event(session_id, event)
            return

        # Candidate steering message — gate before recording.
        cleaned = strip_private(prompt).strip()
        if not cleaned:
            return
        if cleaned.lower() in _TRIVIAL_PATTERNS:
            return
        if len(cleaned) < _MIN_STEERING_LENGTH:
            return

        # Derive the ordinal: count of prior prompt events in the timeline.
        timeline = read_session_timeline(session_id)
        ordinal = sum(1 for e in timeline if e.get("type") in _PROMPT_EVENT_TYPES)
        if ordinal <= 0:
            # First prompt of the session — initial instruction, not a steer.
            return

        snippet = cleaned[:_MAX_SNIPPET_LENGTH]
        event = {"type": "human_steering", "ordinal": ordinal, "snippet": snippet}
        record_telemetry_event(session_id, event)

    except Exception as exc:
        logger.debug(
            "capture_prompt_event silently swallowed exception for session %s: %r",
            session_id,
            exc,
        )


def summarize_and_store(session_id: str, project_key: str | None) -> None:
    """Distill a session's interaction shape into one retrievable Memory.

    Fail-silent: the entire body is wrapped in ``try/except Exception`` and logged
    at DEBUG. NEVER raises.

    Reads the session timeline and composes ONE compact natural-language pattern
    string covering: the ordered slash-command sequence, the steering count +
    ordinal positions, the approval tally (count of ``tool_use`` events), and any
    idle-gap interrupts. Saves it via ``Memory.safe_save`` with the exact shape
    required by the plan — ``category`` and ``tags`` live INSIDE the ``metadata``
    DictField, and ``agent_id`` is namespaced ``tui-<session_id>``.

    Skips the Memory write entirely (no exception) when:
        - ``session_id`` is falsy,
        - ``project_key`` is None (mirrors the ``ingest()`` / ``extract()``
          None-skip pattern; ``_get_project_key`` can return None),
        - the timeline is empty, or
        - there is NO interaction signal (no slash commands AND no steering) — a
          bare "approved N tools" with no human-decision signal is noise.

    Args:
        session_id: The session whose trace to summarize. Falsy → no-op.
        project_key: The project partition for the Memory. None → skip the write.
    """
    try:
        if not session_id:
            return

        if project_key is None:
            logger.debug(
                "summarize_and_store: project_key is None for session %s — skipping write.",
                session_id,
            )
            return

        timeline = read_session_timeline(session_id)
        if not timeline:
            return

        slash_commands: list[str] = []
        steering_ordinals: list[int] = []
        tool_count = 0
        idle_gaps: list[float] = []

        for event in timeline:
            etype = event.get("type")
            if etype == "slash_command":
                cmd = event.get("command")
                if cmd:
                    slash_commands.append(cmd)
            elif etype == "human_steering":
                ordinal = event.get("ordinal")
                if isinstance(ordinal, int):
                    steering_ordinals.append(ordinal)
                else:
                    steering_ordinals.append(len(steering_ordinals))
            elif etype == "tool_use":
                tool_count += 1
            elif etype == "idle_gap":
                gap = event.get("gap_seconds")
                if isinstance(gap, (int, float)):
                    idle_gaps.append(float(gap))

        # No interaction signal at all → noise, skip the write.
        if not slash_commands and not steering_ordinals:
            logger.debug(
                "summarize_and_store: no slash/steering signal for session %s — skipping.",
                session_id,
            )
            return

        pattern_str = _compose_pattern_string(
            slash_commands=slash_commands,
            steering_ordinals=steering_ordinals,
            tool_count=tool_count,
            idle_gaps=idle_gaps,
            total_events=len(timeline),
        )

        Memory.safe_save(
            agent_id=f"tui-{session_id}",
            project_key=project_key,
            content=pattern_str[:_MAX_CONTENT_LENGTH],
            importance=1.0,
            source=SOURCE_HUMAN,
            metadata={"category": "pattern", "tags": ["tui-interaction"]},
        )

    except Exception as exc:
        logger.debug(
            "summarize_and_store silently swallowed exception for session %s: %r",
            session_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compose_pattern_string(
    *,
    slash_commands: list[str],
    steering_ordinals: list[int],
    tool_count: int,
    idle_gaps: list[float],
    total_events: int,
) -> str:
    """Compose one compact natural-language interaction-shape sentence.

    Example target:
        "In a session, human ran /do-plan → /do-build → /do-test, steered once at
        turn 4, approved 12 tools, 1 idle-gap interrupt of 90s."
    """
    parts: list[str] = []

    if slash_commands:
        seq = " → ".join(f"/{c}" for c in slash_commands)
        parts.append(f"ran {seq}")

    if steering_ordinals:
        count = len(steering_ordinals)
        positions = ", ".join(str(o) for o in steering_ordinals)
        word = "once" if count == 1 else f"{count} times"
        turn_label = "turn" if count == 1 else "turns"
        parts.append(f"steered {word} at {turn_label} {positions}")

    if tool_count:
        tool_word = "tool" if tool_count == 1 else "tools"
        parts.append(f"approved {tool_count} {tool_word}")

    if idle_gaps:
        gap_count = len(idle_gaps)
        gap_word = "interrupt" if gap_count == 1 else "interrupts"
        gap_desc = ", ".join(f"{round(g)}s" for g in idle_gaps)
        parts.append(f"{gap_count} idle-gap {gap_word} of {gap_desc}")

    body = ", ".join(parts) if parts else "no notable interaction"
    return f"In a {total_events}-event session, human {body}."
