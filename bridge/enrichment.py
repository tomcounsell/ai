"""Deferred message enrichment for the job worker.

This module contains enrichment logic that was previously executed inline
in the Telegram event handler. By deferring these operations to the job
worker, the event handler can enqueue messages within milliseconds instead
of blocking on media processing, YouTube transcription, link summaries,
and reply chain fetching.

The enrich_message() function is called by the job worker in _execute_job()
before invoking the agent, so the agent still receives fully enriched text.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Module-level Telegram client reference, set by the bridge at startup.
_telegram_client = None


def set_telegram_client(client) -> None:
    """Register the Telegram client for use by enrichment operations.

    Called once from telegram_bridge.py main() after client creation.
    """
    global _telegram_client
    _telegram_client = client


def get_telegram_client():
    """Return the registered Telegram client, or None."""
    return _telegram_client


async def enrich_message(
    telegram_client,
    message_text: str,
    has_media: bool = False,
    media_type: str | None = None,
    raw_media_message_id: int | None = None,
    youtube_urls: str | None = None,
    non_youtube_urls: str | None = None,
    reply_to_msg_id: int | None = None,
    chat_id: str | None = None,
    sender_name: str | None = None,
    message_id: int | None = None,
) -> str:
    """Perform deferred enrichment on a message before agent invocation.

    Each enrichment step is independent and guarded by a try/except so that
    a failure in one step does not prevent the others from running. If all
    steps fail, the original message_text is returned unchanged.

    Args:
        telegram_client: Telethon TelegramClient (may be None if unavailable).
        message_text: The cleaned message text from the event handler.
        has_media: Whether the original message had media attached.
        media_type: Type string ("photo", "voice", "document", etc.).
        raw_media_message_id: Original Telegram message ID for media download.
        youtube_urls: JSON-encoded list of (url, video_id) tuples.
        non_youtube_urls: JSON-encoded list of URL strings.
        reply_to_msg_id: Telegram message ID of the parent reply, if any.
        chat_id: Telegram chat ID (as string) for API calls.
        sender_name: Name of the message sender.
        message_id: Telegram message ID of the current message.

    Returns:
        The enriched message text string.
    """
    enriched_text = message_text

    # --- 1. Media processing ---
    if has_media and telegram_client and raw_media_message_id and chat_id:
        try:
            from bridge.media import process_incoming_media

            # Fetch the original message object so we can process its media
            chat_id_int = int(chat_id)
            msg_obj = await telegram_client.get_messages(
                chat_id_int, ids=raw_media_message_id
            )
            if msg_obj and msg_obj.media:
                media_description, _media_files = await process_incoming_media(
                    telegram_client, msg_obj
                )
                if media_description:
                    if enriched_text and enriched_text != "Hello":
                        enriched_text = f"{media_description}\n\n{enriched_text}"
                    else:
                        enriched_text = media_description
                    logger.info(
                        f"Enrichment: processed media ({media_type}): "
                        f"{media_description[:100]}..."
                    )
        except Exception as e:
            logger.warning(f"Enrichment: media processing failed: {e}")

    # --- 2. YouTube URL transcription ---
    if youtube_urls:
        try:
            from tools.link_analysis import process_youtube_urls_in_text

            parsed_urls = json.loads(youtube_urls)
            if parsed_urls:
                # process_youtube_urls_in_text works on raw text containing URLs,
                # so we pass the enriched_text which should contain the URLs.
                yt_enriched, youtube_results = await process_youtube_urls_in_text(
                    enriched_text
                )
                successful = sum(1 for r in youtube_results if r.get("success"))
                if successful > 0:
                    enriched_text = yt_enriched
                    logger.info(
                        f"Enrichment: transcribed {successful}/{len(parsed_urls)} "
                        f"YouTube video(s)"
                    )
                else:
                    for r in youtube_results:
                        if r.get("error"):
                            logger.warning(
                                f"Enrichment: YouTube processing failed for "
                                f"{r.get('video_id')}: {r.get('error')}"
                            )
        except Exception as e:
            logger.warning(f"Enrichment: YouTube processing failed: {e}")

    # --- 3. Link summaries ---
    if non_youtube_urls:
        try:
            from bridge.context import format_link_summaries, get_link_summaries

            parsed_urls = json.loads(non_youtube_urls)
            if parsed_urls:
                # get_link_summaries expects the raw text to extract URLs from.
                # Since we already have the URLs, we construct a text with them.
                urls_text = " ".join(parsed_urls)
                link_summaries = await get_link_summaries(
                    text=urls_text,
                    sender=sender_name or "Unknown",
                    chat_id=chat_id or "",
                    message_id=message_id or 0,
                    timestamp=None,
                )
                link_summary_text = format_link_summaries(link_summaries)
                if link_summary_text:
                    enriched_text = (
                        f"{enriched_text}\n\n"
                        f"--- LINK SUMMARIES ---\n{link_summary_text}"
                    )
                    logger.info(
                        f"Enrichment: added {len(link_summaries)} link summaries"
                    )
        except Exception as e:
            logger.warning(f"Enrichment: link summary processing failed: {e}")

    # --- 4. Reply chain context ---
    if reply_to_msg_id and telegram_client and chat_id:
        try:
            from bridge.context import fetch_reply_chain, format_reply_chain

            chat_id_int = int(chat_id)
            reply_chain = await fetch_reply_chain(
                telegram_client,
                chat_id_int,
                reply_to_msg_id,
                max_depth=20,
            )
            if reply_chain:
                reply_chain_context = format_reply_chain(reply_chain)
                if reply_chain_context:
                    enriched_text = (
                        f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{enriched_text}"
                    )
                    logger.info(
                        f"Enrichment: fetched reply chain with "
                        f"{len(reply_chain)} messages"
                    )
        except Exception as e:
            logger.warning(f"Enrichment: reply chain fetch failed: {e}")

    return enriched_text
