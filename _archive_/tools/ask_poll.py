"""``valor-ask-poll`` — ask a blocked agent's question as a native Telegram poll.

The single transport-aware entry point ``/ask-me`` reaches through Bash on a
headless bridge session. **Degradation happens here, once**: every surface that
cannot render a poll — a 1:1 DM, a ``teammate`` session, email, a local session,
the system sink — gets the question as numbered prose through the ordinary
``send_message`` path, before a poll payload ever exists.

A user account cannot send a poll into a 1:1 DM (MTProto rejects it with
``MediaInvalidError``), and polls are an engineering affordance, so eligibility
is "group chat AND eng session" — see ``bridge/poll_gating.py``.

**This CLI does not end the turn.** ``/ask-me`` must invoke ``AskUserQuestion``
as its final act after this returns: the ``needs_human`` edge fires only on a
``PreToolUse`` tool-name match against ``AskUserQuestion``, and this tool's Bash
invocation has tool name ``Bash``, which never matches. Without that final call
the turn does not end on ``PM_NEEDS_HUMAN`` and the asking session keeps running
while the human decides. See ``.claude/skill-context/ask-me.md``.

Usage:
    valor-ask-poll --question "Which approach?" --option "A" --option "B"

See ``docs/features/telegram-poll-questions.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

#: Owner decision 4: the final option is always this literal string. The human
#: taps it when none of the offered options fit, and the agent then sends a
#: narrowed plain-text followup they answer by reply-to.
ESCAPE_HATCH_OPTION = "Other: wait for followup message"

#: Telegram's own limits on a poll. Provisional and tunable — grain of salt.
MIN_OPTIONS = 2
MAX_OPTIONS = 10
MAX_OPTION_CHARS = 100


def _fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def normalize_options(options: list[str]) -> list[str]:
    """Validate options and guarantee the mandatory escape hatch is last.

    **This function owns ALL option validation.** The drafter's
    ``_validate_for_medium(text, medium)`` takes text only and physically cannot
    see the options, so widening it would ripple through every call site for no
    gain. Question-text validation lives there; option validation lives here.

    The escape hatch is appended when absent and moved to last when the caller
    already supplied it, so an agent that remembers it and one that forgets
    produce the same poll.
    """
    cleaned = [opt.strip() for opt in options if opt and opt.strip()]
    if len(cleaned) != len(options):
        _fail("poll options must be non-empty")

    # De-duplicate preserving order; Telegram tolerates duplicates but a poll
    # with two identical options is a broken question.
    seen: set[str] = set()
    deduped = []
    for opt in cleaned:
        if opt in seen:
            continue
        seen.add(opt)
        deduped.append(opt)

    deduped = [opt for opt in deduped if opt != ESCAPE_HATCH_OPTION]
    deduped.append(ESCAPE_HATCH_OPTION)

    if len(deduped) < MIN_OPTIONS:
        _fail(f"a poll needs at least {MIN_OPTIONS} options (including the escape hatch)")
    if len(deduped) > MAX_OPTIONS:
        _fail(f"a poll takes at most {MAX_OPTIONS} options, got {len(deduped)}")
    for opt in deduped:
        if len(opt) > MAX_OPTION_CHARS:
            _fail(f"option exceeds {MAX_OPTION_CHARS} chars: {opt[:40]!r}...")
    return deduped


def _send_as_text(question: str, options: list[str], *, reason: str) -> int:
    """Degrade to the ordinary text send path.

    Uses the same rendering the relay's fallback uses, so a question that
    "should have been a poll" reads identically wherever it lands. The reason is
    logged so that outcome is diagnosable rather than mysterious.
    """
    from agent.output_handler import render_poll_as_text
    from tools.send_message import send_message

    logger.info("ask_poll degrading to text (reason=%s)", reason)
    text = render_poll_as_text(question, options)
    send_message(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="valor-ask-poll",
        description=(
            "Ask a question as a native Telegram poll when the surface supports "
            "one, or as numbered prose when it does not."
        ),
    )
    parser.add_argument("--question", required=True, help="The question to ask.")
    parser.add_argument(
        "--option",
        action="append",
        dest="options",
        default=[],
        help=(
            "A poll option; repeat for each. Put the recommended option FIRST. "
            f"{ESCAPE_HATCH_OPTION!r} is appended automatically if omitted."
        ),
    )
    args = parser.parse_args(argv)

    question = (args.question or "").strip()
    if not question:
        _fail("--question must not be empty")

    from bridge.message_drafter import POLL_QUESTION_MAX_CHARS

    if len(question) > POLL_QUESTION_MAX_CHARS:
        _fail(f"question exceeds {POLL_QUESTION_MAX_CHARS} chars ({len(question)})")

    options = normalize_options(args.options)

    from tools.send_message import _resolve_transport

    transport = _resolve_transport()
    if transport != "telegram":
        return _send_as_text(question, options, reason=f"transport:{transport}")

    # The env trio, read together the way the sibling send path reads it.
    # `_resolve_transport()` only tests for the PRESENCE of TELEGRAM_CHAT_ID and
    # hands back neither value, so reading it alone is not enough.
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    session_id = os.environ.get("VALOR_SESSION_ID")
    reply_to = os.environ.get("TELEGRAM_REPLY_TO")

    if not chat_id:
        _fail("TELEGRAM_CHAT_ID not set; session is not Telegram-triggered.")
    # Hard-exit rather than degrade: poll_eligible(chat_id, None) would return
    # `unknown_session_type` and silently turn an eligible eng question into
    # prose under a misleading reason.
    if not session_id:
        _fail("VALOR_SESSION_ID not set.")

    from bridge.poll_gating import poll_eligible

    eligibility = poll_eligible(chat_id, session_id)
    if not eligibility.ok:
        return _send_as_text(question, options, reason=eligibility.reason)

    from agent.output_handler import TelegramRelayOutputHandler

    handler = TelegramRelayOutputHandler()
    # Capability probe rather than a Protocol method, so EmailOutputHandler and
    # FileOutputHandler stay valid without a poll implementation.
    if not hasattr(handler, "send_poll"):
        return _send_as_text(question, options, reason="handler_lacks_send_poll")

    class _Session:
        """Minimal carrier for the one field the handler reads off a session."""

        def __init__(self, session_id: str) -> None:
            self.session_id = session_id

    try:
        asyncio.run(
            handler.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                reply_to_msg_id=int(reply_to) if reply_to else None,
                session=_Session(session_id),
            )
        )
    except Exception as e:  # noqa: BLE001 — a failed poll must still reach the human
        logger.warning("ask_poll: poll enqueue failed (%s); falling back to text", e)
        return _send_as_text(question, options, reason="enqueue_failed")

    print(f"Poll queued to chat {chat_id} ({len(options)} options).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
