"""Startup-phase parser for the granite interactive-TUI session runner.

Watches the PTY buffer for known startup shapes that the operator
needs to handle before the steady-state loop can take over:
    - login prompt
    - update notice
    - error modal
    - persona-prime acknowledgement
    - trust-folder prompt (per the F-probe finding at
      scripts/probe_slash_arguments.py:243-247)

The parser is a small Python function. It does not interpret; it
identifies which known shape was seen and returns a `StartupEvent`
enum value. When a known shape is detected, the container asks
granite for response text and writes the response to the appropriate
PTY.

The pattern set is enumerated at module load (not at runtime). Adding
a new shape means editing this file; the container does not enumerate
patterns itself. This keeps the parser a deterministic finite-state
recognizer, not a learned classifier.

C4: the `/help` overlay is **not** a startup event. It is part of
the steady-state loop's idle heuristic (see pty_driver.py's
OVERLAY_BAR). If the operator sends `/help` mid-session, the loop
holds; the container does not route the overlay through the
startup-phase parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class StartupEvent(StrEnum):
    """A recognized startup-phase event the parser can detect.

    `UNKNOWN` is the catch-all when no pattern matches; the
    container treats it as a no-op (no idle miss, no early exit).
    """

    UNKNOWN = "unknown"
    LOGIN_PROMPT = "login_prompt"
    UPDATE_NOTICE = "update_notice"
    ERROR_MODAL = "error_modal"
    PERSONA_PRIME_ACK = "persona_prime_ack"
    TRUST_FOLDER_PROMPT = "trust_folder_prompt"


@dataclass
class StartupMatch:
    """Result of `parse_startup_frame`."""

    event: StartupEvent
    matched_text: str  # the substring of the buffer that matched
    response: str | None  # the canned response the container should send (None = ask granite)


# Pattern set, in priority order. Higher-priority patterns (errors
# first, then interactive prompts, then passive acknowledgements)
# shadow lower-priority ones. The patterns are intentionally
# conservative; a false-negative surfaces as UNKNOWN, not as a
# wrong classification.

# Login prompt: the OAuth/Max flow shows a "Sign in to continue" /
# "Press Enter to continue" style frame on first run.
_LOGIN_PATTERNS = [
    (re.compile(r"Sign in to continue", re.IGNORECASE), "Sign in to continue", None),
    # The Max subscription OAuth flow sometimes shows a paste-the-url
    # frame. We treat it as login too.
    (re.compile(r"paste.*url.*continue", re.IGNORECASE), "paste url to continue", None),
    # The real claude 2.1.185 re-auth frame (token expiry mid-run): a theme
    # picker → "Select login method" menu → an auto-open / "Browser didn't
    # open?" / "Opening browser" frame. The earlier two patterns do NOT match
    # this shape, so these are mandatory for BYOB recovery (issue #1750) to
    # fire in production. None of these phrases contain an _ERROR_PATTERNS
    # substring, so the error-shadows-login precedence (C4) classifies a real
    # re-auth frame as LOGIN_PROMPT, not ERROR_MODAL.
    (re.compile(r"Select login method", re.IGNORECASE), "select login method", None),
    (re.compile(r"Browser didn't open", re.IGNORECASE), "browser didnt open", None),
    (re.compile(r"Opening browser", re.IGNORECASE), "opening browser", None),
]

# Update notice: "A new version of Claude Code is available" or
# similar. The TUI offers to skip or apply; we just send Enter to
# dismiss the modal so the steady state can take over.
_UPDATE_PATTERNS = [
    (re.compile(r"new version of Claude Code", re.IGNORECASE), "new version available", "\r"),
    (re.compile(r"update available", re.IGNORECASE), "update available", "\r"),
]

# Error modal: a fatal-looking frame. The container surfaces the
# event to the results JSON; the loop does not auto-respond.
_ERROR_PATTERNS = [
    (
        re.compile(r"(Authentication failed|Invalid API key|Login failed)", re.IGNORECASE),
        "auth failed",
        None,
    ),
    (re.compile(r"(fatal error|panic:|internal server error)", re.IGNORECASE), "fatal error", None),
]

# Persona-prime ack: the model has received the slash command and
# is processing it. The TUI's bottom bar changes shape briefly.
# This is informational; the container's idle heuristic will catch
# the real "ready" state when the model finishes the prime.
_PRIME_PATTERNS = [
    (
        re.compile(r"(primed|loading persona|reading commands)", re.IGNORECASE),
        "persona loading",
        None,
    ),
]

# Trust-folder prompt: the F-probe (scripts/probe_slash_arguments.py:243-247)
# surfaced this on first run in a fresh tempdir. Dismissal is "1\r"
# (per the probe's confirmed behavior).
_TRUST_FOLDER_PROMPT = re.compile(
    r"(Yes, I trust this folder|trust this folder\?)",
    re.IGNORECASE,
)
_TRUST_FOLDER_RESPONSE = "1"


def _match_any(
    patterns: list[tuple[re.Pattern[str], str, str | None]], buffer: str
) -> StartupMatch | None:
    for regex, label, response in patterns:
        m = regex.search(buffer)
        if m:
            return StartupMatch(
                event=_label_to_event(label),
                matched_text=m.group(0),
                response=response,
            )
    return None


def _label_to_event(label: str) -> StartupEvent:
    """Map a pattern label to its StartupEvent enum value.

    The label is a short string the pattern author chose; this
    mapping is the contract between the pattern set and the enum.
    Adding a new label means editing this function and the enum.
    """
    label_to_event_map = {
        "Sign in to continue": StartupEvent.LOGIN_PROMPT,
        "paste url to continue": StartupEvent.LOGIN_PROMPT,
        "select login method": StartupEvent.LOGIN_PROMPT,
        "browser didnt open": StartupEvent.LOGIN_PROMPT,
        "opening browser": StartupEvent.LOGIN_PROMPT,
        "new version available": StartupEvent.UPDATE_NOTICE,
        "update available": StartupEvent.UPDATE_NOTICE,
        "auth failed": StartupEvent.ERROR_MODAL,
        "fatal error": StartupEvent.ERROR_MODAL,
        "persona loading": StartupEvent.PERSONA_PRIME_ACK,
    }
    return label_to_event_map.get(label, StartupEvent.UNKNOWN)


# Priority order matters: errors and login are the most
# load-bearing; prime ack is informational and gets shadowed.
_PATTERN_GROUPS: list[list[tuple[re.Pattern[str], str, str | None]]] = [
    _ERROR_PATTERNS,
    _LOGIN_PATTERNS,
    _UPDATE_PATTERNS,
    _PRIME_PATTERNS,
]


def parse_startup_frame(buffer: str) -> StartupMatch:
    """Parse a startup-phase buffer frame; return the matched event.

    Priority order (errors first): if a buffer matches both an
    error pattern and a login pattern, the error wins. This is
    intentional — a fatal-looking frame should never be auto-
    dismissed by a login-prompt response.

    The trust-folder prompt has its own slot and is checked last
    in its own slot: it can co-occur with prime-ack (the model
    hasn't finished priming but the folder prompt has appeared).
    The trust-folder prompt is more urgent; it shadows.
    """
    # Trust-folder prompt is the most likely co-occurring event
    # (the TUI shows it on first run in a fresh tempdir, exactly
    # when persona priming is in flight). Check it first.
    tf_match = _TRUST_FOLDER_PROMPT.search(buffer)
    if tf_match:
        return StartupMatch(
            event=StartupEvent.TRUST_FOLDER_PROMPT,
            matched_text=tf_match.group(0),
            response=_TRUST_FOLDER_RESPONSE,
        )

    for group in _PATTERN_GROUPS:
        match = _match_any(group, buffer)
        if match is not None:
            return match

    return StartupMatch(
        event=StartupEvent.UNKNOWN,
        matched_text="",
        response=None,
    )


def known_patterns() -> list[tuple[str, str]]:
    """Enumerate the (label, regex pattern) pairs the parser recognizes.

    Used by the parser's self-test to feed each pattern into the
    parser and assert the right enum value. The list is rebuilt
    here rather than introspected from the pattern groups because
    the response-handling metadata doesn't matter for the self-test
    — only the label/pattern match.
    """
    out: list[tuple[str, str]] = []
    for group in _PATTERN_GROUPS:
        for regex, label, _response in group:
            out.append((label, regex.pattern))
    out.append(("trust_folder", _TRUST_FOLDER_PROMPT.pattern))
    return out
