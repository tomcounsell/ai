"""Startup catchup: scan for unread messages missed during downtime.

On bridge startup, this module scans monitored groups for recent messages
that weren't processed (e.g., sent while the bridge was down). It enqueues
any messages that should have triggered a response.
"""

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# How far back to look for missed messages (default: 1 hour)
CATCHUP_LOOKBACK_MINUTES = 60

# Maximum messages to fetch per chat
MAX_MESSAGES_PER_CHAT = 50


async def scan_for_missed_messages(
    client,
    monitored_groups: list[str],
    projects_config: dict,
    should_respond_fn,
    enqueue_job_fn,
    find_project_fn,
) -> int:
    """
    Scan monitored groups for messages that may have been missed.

    Args:
        client: TelegramClient instance
        monitored_groups: List of group titles to scan
        projects_config: Projects configuration dict
        should_respond_fn: Async function to check if we should respond
        enqueue_job_fn: Async function to enqueue a job
        find_project_fn: Function to find project config for a chat

    Returns:
        Number of messages queued for processing
    """
    queued = 0
    cutoff = datetime.now(UTC) - timedelta(minutes=CATCHUP_LOOKBACK_MINUTES)

    logger.info(
        f"[catchup] Scanning {len(monitored_groups)} groups for messages since {cutoff.isoformat()}"
    )

    # Get all dialogs to find monitored groups
    dialogs = await client.get_dialogs()
    logger.info(f"[catchup] Got {len(dialogs)} total dialogs")

    matched_groups = []
    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue

        # Note: monitored_groups contains lowercase group names, but Telegram
        # group titles may have capitals. Compare case-insensitively.
        if chat_title.lower() not in monitored_groups:
            logger.debug(f"[catchup] Skipping non-monitored group: {chat_title}")
            continue

        logger.info(f"[catchup] Found monitored group: {chat_title}")
        matched_groups.append(chat_title)

        project = find_project_fn(chat_title)
        if not project:
            logger.warning(f"[catchup] No project config for {chat_title}")
            continue

        project_key = project.get("_key", "unknown")
        working_dir = project.get("working_directory", "")

        logger.info(f"[catchup] Scanning {chat_title} for missed messages...")

        try:
            # Fetch recent messages
            messages = await client.get_messages(
                dialog.entity,
                limit=MAX_MESSAGES_PER_CHAT,
            )

            logger.info(
                f"[catchup] {chat_title}: Fetched {len(messages)} messages, "
                f"scanning for messages after {cutoff.isoformat()}"
            )

            for message in messages:
                # Skip if too old
                if message.date < cutoff:
                    logger.debug(
                        f"[catchup] {chat_title}: msg {message.id} too old "
                        f"({message.date.isoformat()}) - stopping scan"
                    )
                    break

                # Skip outgoing messages (our own)
                if message.out:
                    logger.debug(
                        f"[catchup] {chat_title}: msg {message.id} is outgoing - skip"
                    )
                    continue

                # Skip messages without text
                text = message.text or ""
                if not text.strip():
                    logger.debug(
                        f"[catchup] {chat_title}: msg {message.id} has no text - skip"
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
                already_handled = await _check_if_handled(
                    client, dialog.entity, message
                )
                if already_handled:
                    logger.info(
                        f"[catchup] {chat_title}: msg {message.id} already handled - skip"
                    )
                    continue

                # Check if we should respond to this message
                # Create a minimal event-like object for should_respond_fn
                class MinimalEvent:
                    def __init__(self, msg, chat_id):
                        self.message = msg
                        self.chat_id = chat_id
                        self.is_private = False

                minimal_event = MinimalEvent(message, dialog.entity.id)

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
                session_id = f"tg_{project_key}_{dialog.entity.id}_{message.id}"

                await enqueue_job_fn(
                    project_key=project_key,
                    session_id=session_id,
                    working_dir=working_dir,
                    message_text=text,
                    sender_name=sender_name,
                    chat_id=str(dialog.entity.id),
                    message_id=message.id,
                    chat_title=chat_title,
                    priority="low",  # Lower priority than real-time messages
                    sender_id=sender_id,
                    workflow_id=None,
                )
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
        logger.warning(
            f"[catchup] No groups matched! Looking for: {monitored_groups}"
        )
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
