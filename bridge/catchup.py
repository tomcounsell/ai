"""Startup catchup: scan for unread messages missed during downtime.

On bridge startup, this module scans monitored groups for recent messages
that weren't processed (e.g., sent while the bridge was down). It enqueues
any messages that should have triggered a response.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridge.routing import persona_to_session_type, resolve_persona
from config.enums import SessionType

logger = logging.getLogger(__name__)

# How far back to look for missed messages (default: 1 hour)
CATCHUP_LOOKBACK_MINUTES = 60

# Maximum messages to fetch per chat
MAX_MESSAGES_PER_CHAT = 50

# Operator kill switch for ALL message-recovery scans (startup catchup, periodic
# reconciler, valor-catchup agent sweep). Same flag-file convention as
# data/auto-revert-enabled: `touch data/catchup-disabled` pauses recovery,
# `rm data/catchup-disabled` re-enables it. Realtime message handling is
# unaffected — only the "re-scan history for missed messages" layer is gated.
CATCHUP_DISABLED_FLAG = Path(__file__).resolve().parent.parent / "data" / "catchup-disabled"


def catchup_disabled() -> bool:
    """True when the operator flag file pauses all recovery scans."""
    return CATCHUP_DISABLED_FLAG.exists()


async def scan_for_missed_messages(
    client,
    monitored_groups: list[str],
    projects_config: dict,
    should_respond_fn,
    enqueue_agent_session_fn,
    find_project_fn,
    lookback_override: timedelta | None = None,
) -> int:
    """
    Scan monitored groups for messages that may have been missed.

    Args:
        client: TelegramClient instance
        monitored_groups: List of group titles to scan
        projects_config: Projects configuration dict
        should_respond_fn: Async function to check if we should respond
        enqueue_agent_session_fn: Async function to enqueue a session
        find_project_fn: Function to find project config for a chat
        lookback_override: If provided, use this timedelta instead of
            CATCHUP_LOOKBACK_MINUTES. Capped at 24 hours.

    Returns:
        Number of messages queued for processing
    """
    if catchup_disabled():
        logger.warning(
            "[catchup] Skipped — %s exists (operator kill switch)", CATCHUP_DISABLED_FLAG
        )
        return 0

    queued = 0
    if lookback_override is not None:
        # Cap at 24 hours to prevent scanning excessive history
        max_lookback = timedelta(hours=24)
        effective_lookback = min(lookback_override, max_lookback)
        cutoff = datetime.now(UTC) - effective_lookback
        logger.info(
            "[catchup] Using dynamic lookback: %s (capped at 24h)",
            effective_lookback,
        )
    else:
        cutoff = datetime.now(UTC) - timedelta(minutes=CATCHUP_LOOKBACK_MINUTES)

    logger.info(
        f"[catchup] Scanning {len(monitored_groups)} groups for messages since {cutoff.isoformat()}"
    )

    # Get all dialogs to find monitored groups
    dialogs = await client.get_dialogs()
    logger.info(f"[catchup] Got {len(dialogs)} total dialogs")

    matched_groups = []
    # Telethon can return the same group twice (channel + linked discussion group)
    seen_chat_ids: set[int] = set()
    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue

        # Note: monitored_groups contains lowercase group names, but Telegram
        # group titles may have capitals. Compare case-insensitively.
        if chat_title.lower() not in monitored_groups:
            logger.debug(f"[catchup] Skipping non-monitored group: {chat_title}")
            continue

        # Deduplicate by dialog ID — Telethon may return the same supergroup
        # twice (once as a channel, once as a linked discussion group).
        if dialog.id in seen_chat_ids:
            logger.warning(f"[catchup] Skipping duplicate dialog for {chat_title} (id={dialog.id})")
            continue
        seen_chat_ids.add(dialog.id)

        logger.info(f"[catchup] Found monitored group: {chat_title}")
        matched_groups.append(chat_title)

        project = find_project_fn(chat_title)
        if not project:
            logger.warning(f"[catchup] No project config for {chat_title}")
            continue

        project_key = project.get("_key", "unknown")
        working_dir = project.get("working_directory", "")

        # Use dialog.id (includes -100 prefix for supergroups) to match
        # the event handler's event.chat_id format.
        chat_id = dialog.id

        # Per-chat cutoff (issue #1408): the global `cutoff` is derived from
        # `last_connected`, which advances on every 5-minute heartbeat. A message
        # sent inside the connection window but silently dropped by Telethon falls
        # BEFORE that cutoff on restart and would be excluded. The per-chat
        # last-processed cursor records the last message we actually dispatched
        # for this chat; if it predates the global cutoff, we use it (minus a
        # 60-second safety margin) so the scan reaches back to the real gap.
        #
        # We take min(global_cutoff, candidate): "look back AT LEAST as far as the
        # global cutoff, and further if the cursor is older." Never max() — that
        # could miss a message that arrived after the last cursor update but before
        # the crash. The 24-hour cap (above) still bounds total lookback.
        from bridge.dedup import get_last_processed

        per_chat_cutoff = cutoff
        try:
            last_proc = await get_last_processed(chat_id)
            if last_proc is not None:
                _last_msg_id, last_proc_dt = last_proc
                candidate = last_proc_dt - timedelta(seconds=60)
                per_chat_cutoff = min(cutoff, candidate)
                if per_chat_cutoff < cutoff:
                    logger.info(
                        f"[catchup] {chat_title}: per-chat cutoff {per_chat_cutoff.isoformat()} "
                        f"predates global cutoff {cutoff.isoformat()} "
                        f"(last dispatched {last_proc_dt.isoformat()}) — extending lookback"
                    )
        except Exception as e:
            # Defensive: a cursor read failure must not break the per-group scan.
            logger.warning(
                f"[catchup] {chat_title}: get_last_processed failed ({e}); "
                f"falling back to global cutoff"
            )

        logger.info(f"[catchup] Scanning {chat_title} for missed messages...")

        try:
            # Fetch recent messages
            messages = await client.get_messages(
                dialog.entity,
                limit=MAX_MESSAGES_PER_CHAT,
            )

            logger.info(
                f"[catchup] {chat_title}: Fetched {len(messages)} messages, "
                f"scanning for messages after {per_chat_cutoff.isoformat()}"
            )

            for message in messages:
                # Skip if too old
                if message.date < per_chat_cutoff:
                    logger.debug(
                        f"[catchup] {chat_title}: msg {message.id} too old "
                        f"({message.date.isoformat()}) - stopping scan"
                    )
                    break

                # Skip outgoing messages (our own)
                if message.out:
                    logger.debug(f"[catchup] {chat_title}: msg {message.id} is outgoing - skip")
                    continue

                # Skip messages without text
                text = message.text or ""
                if not text.strip():
                    logger.debug(f"[catchup] {chat_title}: msg {message.id} has no text - skip")
                    continue

                # Skip messages already processed (Redis dedup)
                from bridge.dedup import is_duplicate_message

                if await is_duplicate_message(chat_id, message.id):
                    logger.info(
                        f"[catchup] {chat_title}: msg {message.id} "
                        f"already processed (Redis dedup) - skip"
                    )
                    continue

                # Get sender info
                sender = await message.get_sender()
                sender_name = getattr(sender, "first_name", "Unknown")
                sender_username = getattr(sender, "username", None)
                sender_id = getattr(sender, "id", None)

                logger.info(
                    f"[catchup] {chat_title}: msg {message.id} from {sender_name} "
                    f"at {message.date.isoformat()}: '{text[:50]}...'"
                )

                # Check if we already responded (look for our reply)
                already_handled = await _check_if_handled(client, dialog.entity, message)
                if already_handled:
                    logger.info(f"[catchup] {chat_title}: msg {message.id} already handled - skip")
                    continue

                # Check if we should respond to this message
                # Create a minimal event-like object for should_respond_fn
                class MinimalEvent:
                    def __init__(self, msg, chat_id):
                        self.message = msg
                        self.chat_id = chat_id
                        self.is_private = False

                minimal_event = MinimalEvent(message, chat_id)

                should_respond, is_reply_to_valor = await should_respond_fn(
                    client,
                    minimal_event,
                    text,
                    False,  # is_dm
                    chat_title,
                    project,
                    sender_name,
                    sender_username,
                    sender_id,
                )

                if not should_respond:
                    logger.info(
                        f"[catchup] {chat_title}: msg {message.id} - "
                        f"should_respond=False (reply_to_valor={is_reply_to_valor}) - skip"
                    )
                    continue

                # Queue this message for processing
                logger.info(
                    f"[catchup] Found missed message in {chat_title}: "
                    f"'{text[:50]}...' from {sender_name}"
                )

                # Build session ID for this message
                session_id = f"tg_{project_key}_{chat_id}_{message.id}"

                # Resolve persona here for parity with the live handler
                # (bridge/telegram_bridge.py). Without this, the scanner would
                # let session_type default to eng and a teammate-configured chat
                # would wrongly run as an eng PM<->Dev loop. The try/except is
                # NARROW (per-message): a persona failure falls back to the eng
                # default and continues the scan rather than aborting the chat.
                try:
                    persona = resolve_persona(project, chat_title, is_dm=False)
                    session_type = persona_to_session_type(persona)
                except Exception as e:
                    logger.warning(
                        "[catchup] persona resolution failed for chat %s (%s); "
                        "defaulting to eng: %s",
                        chat_id,
                        chat_title,
                        e,
                    )
                    session_type = SessionType.ENG

                # Atomic per-message producer claim (issue #1817 B1, BLOCKER):
                # shared key with the live handler (bridge/dispatch.py) and
                # bridge/reconciler.py so a peer producer racing on this SAME
                # message loses cleanly instead of double-enqueueing. A lost
                # claim means a peer already won (or is winning) this message
                # -- skip WITHOUT recording durable dedup, so a winner-death
                # self-heals via the next reconciler scan re-picking the
                # message instead of being silently dropped forever.
                from bridge.dedup import claim_message, release_message_claim

                if not await claim_message(chat_id, message.id):
                    logger.info(
                        f"[catchup] lost message claim for chat={chat_id} "
                        f"msg={message.id} -- a peer producer won, skipping"
                    )
                    continue

                try:
                    await enqueue_agent_session_fn(
                        project_key=project_key,
                        session_id=session_id,
                        working_dir=working_dir,
                        message_text=text,
                        sender_name=sender_name,
                        chat_id=str(chat_id),
                        telegram_message_id=message.id,
                        chat_title=chat_title,
                        priority="low",  # Lower priority than real-time messages
                        sender_id=sender_id,
                        session_type=session_type,
                        project_config=project,
                    )
                except BaseException:
                    # No orphan: release the claim so a retry (this scan's
                    # next tick, or a peer) is not locked out for the TTL.
                    await release_message_claim(chat_id, message.id)
                    raise

                # Only the winner writes the durable 2h membership record,
                # and only AFTER its own successful enqueue -- see the
                # BLOCKER rationale in bridge/dispatch.py's module docstring.
                from bridge.dedup import record_last_processed, record_message_processed

                await record_message_processed(chat_id, message.id)
                await record_last_processed(chat_id, message.id, message.date)
                queued += 1

        except Exception as e:
            logger.error(f"[catchup] Error scanning {chat_title}: {e}")
            continue

    logger.info(
        f"[catchup] Scan complete: matched {len(matched_groups)} groups, "
        f"queued {queued} missed message(s)"
    )
    if matched_groups:
        logger.info(f"[catchup] Groups scanned: {', '.join(matched_groups)}")
    else:
        logger.warning(f"[catchup] No groups matched! Looking for: {monitored_groups}")
    return queued


async def _check_if_handled(client, entity, message) -> bool:
    """
    Check if we already responded to this message.

    Looks for a reply from us (Valor) to this message.
    """
    try:
        # Get messages after this one, looking for our reply
        replies = await client.get_messages(
            entity,
            limit=10,
            min_id=message.id,
        )

        for reply in replies:
            # Check if it's our message replying to this one
            if reply.out and reply.reply_to_msg_id == message.id:
                return True

        return False

    except Exception as e:
        logger.debug(f"[catchup] Error checking handled status: {e}")
        return False  # Assume not handled, better to double-process than miss
