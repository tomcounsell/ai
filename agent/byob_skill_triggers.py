"""Bridge-side inference for ``AgentSession.requires_real_chrome``.

This module owns the registry of message-text patterns that imply a
bridge-spawned session should engage the BYOB (real Chrome) scheduler gate
introduced by PR #1277. It exists because Telegram- and email-spawned
sessions never see ``valor-session create --needs-real-chrome``: the bridge
enqueue path constructs the session record directly. Without an inference
hook there, a user-typed "check my LinkedIn DMs" arriving via Telegram
silently bypasses the scheduler gate and two real-Chrome sessions can race
on the active tab.

Adding a new BYOB-migrated skill means adding a row to
``BYOB_SKILL_TRIGGERS`` -- not a new branch in ``bridge/telegram_bridge.py``
or ``bridge/email_bridge.py``. Both bridges call
:func:`infer_requires_real_chrome` once before constructing the
``enqueue_agent_session`` kwargs and pass the boolean through unchanged.

Design notes
------------

* **Regex with word boundaries, not bare substring** — Cycle-2 critique
  flagged that a substring match on ``"linkedin"`` false-positives on any
  message that quotes a URL containing ``linkedin.com`` or that mentions
  the platform in passing. The patterns below are anchored on first-person
  / intent phrasing (``my linkedin``, ``check linkedin``) so casual mentions
  do NOT serialize unrelated PM sessions behind the real-Chrome slot.
  False-positives are still safer than false-negatives (the worst-case is
  unnecessary serialization of one PM session, not a Chrome-tab race), but
  we tighten anyway because the cost is one regex.

* **Case-insensitive** — patterns match ``LinkedIn``, ``LINKEDIN``, and
  ``linkedin`` identically. The regex flag, not per-pattern alternation.

* **Exception-safe** — invalid input (``None``, non-string, decode errors)
  returns ``False``. The bridge enqueue path must never raise from
  inference; falling through to status quo is the safe default. This is
  documented in the plan's Failure Path Test Strategy.

* **CLI override unchanged** — operators using
  ``valor-session create --needs-real-chrome`` retain explicit control.
  Inference is purely additive on the bridge path. The CLI path does NOT
  consult this module.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Registry of patterns per migrated BYOB skill. Each value is a list of
# compiled regex strings; any match on the message text → set the flag.
#
# When you migrate a new skill to BYOB, add a row here. The bridge path
# will pick up the new triggers automatically -- no bridge edit required.
BYOB_SKILL_TRIGGERS: dict[str, list[str]] = {
    "linkedin": [
        # First-person intent: "my linkedin", "the linkedin"
        r"\b(?:my|the)\s+linked\s?in\b",
        # Verb + linkedin: "check linkedin", "open linkedin", "browse linkedin"
        r"\b(?:check|open|browse|read|reply\s+(?:on|to))\s+linked\s?in\b",
        # Object phrasing: "linkedin DMs", "linkedin messages", "linkedin feed",
        # "linkedin inbox", "in-mail" / "inmail"
        r"\blinked\s?in\s+(?:dms?|messages?|feed|inbox|notifications?|comments?)\b",
        r"\bin[-\s]?mails?\b",
        # Slash-skill explicit invocation — require start-of-string or
        # whitespace before the slash so URLs containing
        # "/linkedin-post" do not match. Operators type
        # "/linkedin foo" at the start of a turn, not embedded in URLs.
        r"(?:^|\s)/linkedin\b",
    ],
}


# Pre-compile the union per skill once at import time. Keeps
# infer_requires_real_chrome() hot-path cheap (no per-call recompile).
_COMPILED_TRIGGERS: dict[str, list[re.Pattern[str]]] = {
    skill: [re.compile(pat, re.IGNORECASE) for pat in patterns]
    for skill, patterns in BYOB_SKILL_TRIGGERS.items()
}


def infer_requires_real_chrome(message_text: str | None) -> bool:
    """Return True if the message text implies a BYOB-migrated skill.

    Called from the bridge enqueue path (Telegram and email) before
    constructing the kwargs to :func:`agent.agent_session_queue.enqueue_agent_session`.
    The result is forwarded as ``requires_real_chrome=`` so the worker
    scheduler can serialize the session against any other in-flight
    real-Chrome session.

    Args:
        message_text: The user-typed message body. May be ``None`` or
            empty. Non-string inputs are treated as no-match.

    Returns:
        True if any registered trigger matches; False otherwise. Never
        raises -- exception-safe by contract (see Failure Path Test
        Strategy in ``docs/plans/agent_browser_to_byob_skill_migration.md``).
    """
    if message_text is None:
        return False
    if not isinstance(message_text, str):
        # Defensive: bridge code should always pass a str, but if it
        # mistakenly hands us bytes or a custom object, fall through to
        # status quo rather than raise.
        try:
            message_text = str(message_text)
        except Exception:  # noqa: BLE001
            return False
    if not message_text.strip():
        return False

    try:
        for _skill, patterns in _COMPILED_TRIGGERS.items():
            for pattern in patterns:
                if pattern.search(message_text):
                    logger.debug(
                        "byob_inference_match skill=%s pattern=%r preview=%r",
                        _skill,
                        pattern.pattern,
                        message_text[:80],
                    )
                    return True
    except Exception as exc:  # noqa: BLE001
        # Never let a regex pathology escape into the bridge enqueue
        # path. Log and fall through to the safe default.
        logger.warning(
            "byob_inference_failed_safely error=%r preview=%r",
            exc,
            (message_text[:80] if isinstance(message_text, str) else "<non-str>"),
        )
        return False

    return False


__all__ = ["BYOB_SKILL_TRIGGERS", "infer_requires_real_chrome"]
