"""Periodic message reconciliation: detect and recover messages missed during live connection.

The Telegram bridge can silently miss messages when Telethon drops updates due to
pts (persistent timeline sequence) gaps. This module runs a periodic scan of
monitored groups, compares recent messages against dedup records, and re-dispatches
any that were never processed.

Complements bridge/catchup.py (startup-only scan) with continuous monitoring.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from bridge.dedup import is_duplicate_message, record_message_processed

logger = logging.getLogger(__name__)

# Configuration
RECONCILE_INTERVAL_SECONDS = 180  # 3 minutes
RECONCILE_LOOKBACK_MINUTES = 10  # Look back 10 minutes
RECONCILE_MESSAGE_LIMIT = 20  # Messages per group per scan


async def reconciler_loop(
    client,
    monitored_groups: list[str],
    should_respond_fn,
    enqueue_job_fn,
    find_project_fn,
):
    """Run periodic reconciliation to detect missed messages.

    Scans monitored groups for messages that bypassed the event handler.
    Gates all re-dispatches through dedup to prevent duplicate processing.
    """
    logger.info(
        "[reconciler] Started (interval=%ds, lookback=%dm, limit=%d)",
        RECONCILE_INTERVAL_SECONDS,
        RECONCILE_LOOKBACK_MINUTES,
        RECONCILE_MESSAGE_LIMIT,
    )

    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            recovered = await reconcile_once(
                client=client,
                monitored_groups=monitored_groups,
                should_respond_fn=should_respond_fn,
                enqueue_job_fn=enqueue_job_fn,
                find_project_fn=find_project_fn,
            )
            if recovered > 0:
                logger.warning("[reconciler] Recovered %d missed message(s)", recovered)
            else:
                logger.debug("[reconciler] Scan complete, no gaps found")
        except Exception as e:
            logger.error("[reconciler] Error in reconciliation: %s", e, exc_info=True)


async def reconcile_once(
    client,
    monitored_groups: list[str],
    should_respond_fn,
    enqueue_job_fn,
    find_project_fn,
) -> int:
    """Run a single reconciliation scan across all monitored groups.

    Returns the number of missed messages recovered and enqueued.
    """
    if not monitored_groups:
        logger.debug("[reconciler] No monitored groups, skipping scan")
        return 0

    cutoff = datetime.now(UTC) - timedelta(minutes=RECONCILE_LOOKBACK_MINUTES)
    recovered = 0
    groups_scanned = 0

    dialogs = await client.get_dialogs()

    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue

        if chat_title.lower() not in monitored_groups:
            continue

        project = find_project_fn(chat_title)
        if not project:
            logger.debug("[reconciler] No project config for %s, skipping", chat_title)
            continue

        project_key = project.get("_key", "unknown")
        working_dir = project.get("working_directory", "")
        groups_scanned += 1

        # Use dialog.id (includes -100 prefix for supergroups) to match
        # the event handler's event.chat_id format. dialog.entity.id is
        # the raw entity ID without prefix, causing session ID mismatches.
        chat_id = dialog.id

        try:
            messages = await client.get_messages(
                dialog.entity,
                limit=RECONCILE_MESSAGE_LIMIT,
            )

            for message in messages:
                # Skip messages outside lookback window
                if message.date < cutoff:
                    break

                # Skip outgoing messages (our own)
                if message.out:
                    continue

                # Skip messages without text
                text = message.text or ""
                if not text.strip():
                    continue

                # Skip messages already processed (dedup check)
                if await is_duplicate_message(chat_id, message.id):
                    continue

                # Get sender info
                sender = await message.get_sender()
                sender_name = getattr(sender, "first_name", "Unknown")
                sender_username = getattr(sender, "username", None)
                sender_id = getattr(sender, "id", None)

                # Check if we should respond via routing logic
                class MinimalEvent:
                    def __init__(self, msg, ev_chat_id):
                        self.message = msg
                        self.chat_id = ev_chat_id
                        self.is_private = False

                minimal_event = MinimalEvent(message, chat_id)

                should_respond, _is_reply_to_valor = await should_respond_fn(
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
                    continue

                # Build session ID and enqueue
                session_id = f"tg_{project_key}_{chat_id}_{message.id}"

                logger.warning(
                    "[reconciler] Recovered missed message in %s: msg %d from %s: '%s'",
                    chat_title,
                    message.id,
                    sender_name,
                    text[:80],
                )

                await enqueue_job_fn(
                    project_key=project_key,
                    session_id=session_id,
                    working_dir=working_dir,
                    message_text=text,
                    sender_name=sender_name,
                    chat_id=str(chat_id),
                    telegram_message_id=message.id,
                    chat_title=chat_title,
                    priority="low",
                    sender_id=sender_id,
                )

                await record_message_processed(chat_id, message.id)
                recovered += 1

        except Exception as e:
            logger.error("[reconciler] Error scanning %s: %s", chat_title, e, exc_info=True)
            continue

    logger.debug(
        "[reconciler] Scanned %d group(s), recovered %d message(s)",
        groups_scanned,
        recovered,
    )
    return recovered
