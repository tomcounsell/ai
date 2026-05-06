"""Deferred message enrichment for the session worker.

This module contains enrichment logic executed in the worker before agent
invocation. The bridge owns Telegram RPC and the worker owns AI work; that
split is reflected here:

* Media: the bridge downloads the file at intake and persists
  ``TelegramMessage.media_local_path``. The worker calls
  ``process_downloaded_media(path, media_type)`` (no Telethon dependency).
  This restores the broken-after-bridge/worker-split contract — see #1297.
* YouTube / link summaries: still deferred to the worker as before.
* Reply chain: the existing branch still requires a Telethon client and is
  silently skipped in the worker until a follow-up issue lands. Tracking:
  companion to #1297.

The :func:`enrich_message` function is called from
``agent/session_executor.py`` in the worker process before invoking the agent,
so the agent receives fully enriched text.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def enrich_message(
    message_text: str,
    *,
    telegram_message=None,
    youtube_urls: str | None = None,
    non_youtube_urls: str | None = None,
    sender_name: str | None = None,
    chat_id: str | None = None,
    message_id: int | None = None,
) -> str:
    """Perform deferred enrichment on a message before agent invocation.

    Each enrichment step is independent and guarded by a try/except so that
    a failure in one step does not prevent the others from running.

    Args:
        message_text: The cleaned message text from the event handler.
        telegram_message: The persisted ``TelegramMessage`` record for this
            message, or ``None`` for non-Telegram / legacy / manual-test
            sessions where no record was created. When ``None``, the media
            and reply-chain branches are skipped without warning — this is a
            normal path, not an error.
        youtube_urls: JSON-encoded list of (url, video_id) tuples.
        non_youtube_urls: JSON-encoded list of URL strings.
        sender_name: Name of the message sender.
        chat_id: Telegram chat ID (as string) for link-summary metadata.
        message_id: Telegram message ID of the current message.

    Returns:
        The enriched message text string.
    """
    enriched_text = message_text
    failed_steps: list[str] = []
    youtube_count = 0
    link_count = 0

    # --- 1. Media processing (worker-side AI on bridge-downloaded file) ---
    media_summary = "no"
    has_media = bool(getattr(telegram_message, "has_media", False)) if telegram_message else False
    if telegram_message is None:
        # Non-Telegram session, manual test, or pre-migration record: nothing
        # to enrich. This is a normal path; do not log a warning.
        media_summary = "skipped:no_record"
    elif has_media:
        media_local_path = getattr(telegram_message, "media_local_path", None)
        media_download_error = getattr(telegram_message, "media_download_error", None)
        media_type = getattr(telegram_message, "media_type", None)

        if not media_local_path:
            # Bridge attempted the download but it failed (or the bridge ran
            # before this code shipped, in which case the field is None).
            if media_download_error:
                logger.warning(
                    f"[enrichment] media download failed at intake "
                    f"({media_download_error}); skipping AI enrichment"
                )
                media_summary = "skipped:download_failed"
            else:
                logger.warning(
                    "[enrichment] has_media=True but media_local_path is unset; "
                    "skipping AI enrichment (legacy record?)"
                )
                media_summary = "skipped:no_path"
        else:
            path = Path(media_local_path)
            if not (path.exists() and os.access(path, os.R_OK)):
                logger.warning(
                    f"[enrichment] media file at {path} not readable; skipping AI enrichment"
                )
                media_summary = "skipped:file_unreadable"
            else:
                try:
                    from bridge.media import process_downloaded_media

                    media_description, _files = await process_downloaded_media(
                        path, media_type or "media"
                    )
                    if media_description:
                        if enriched_text and not enriched_text.startswith("--"):
                            enriched_text = f"{media_description}\n\n{enriched_text}"
                        else:
                            enriched_text = media_description
                        logger.info(
                            f"Enrichment: processed media ({media_type}): "
                            f"{media_description[:100]}..."
                        )
                        media_summary = "yes"
                    else:
                        media_summary = "skipped:no_description"
                except Exception as e:
                    logger.warning(f"Enrichment: media AI processing failed: {e}")
                    failed_steps.append("media")
                    media_summary = "failed"

    # --- 2. YouTube URL transcription ---
    if youtube_urls:
        try:
            from tools.link_analysis import process_youtube_urls_in_text

            parsed_urls = json.loads(youtube_urls)
            youtube_count = len(parsed_urls)
            if parsed_urls:
                yt_enriched, youtube_results = await process_youtube_urls_in_text(enriched_text)
                successful = sum(1 for r in youtube_results if r.get("success"))
                # Always apply enriched text — failure context strings must reach the agent too
                enriched_text = yt_enriched
                if successful > 0:
                    logger.info(
                        f"Enrichment: transcribed {successful}/{len(parsed_urls)} YouTube video(s)"
                    )
                for r in youtube_results:
                    if r.get("error"):
                        logger.warning(
                            f"Enrichment: YouTube processing failed for "
                            f"{r.get('video_id')}: {r.get('error')}"
                        )
        except Exception as e:
            logger.warning(f"Enrichment: YouTube processing failed: {e}")
            failed_steps.append("youtube")

    # --- 3. Link summaries ---
    if non_youtube_urls:
        try:
            from bridge.context import format_link_summaries, get_link_summaries

            parsed_urls = json.loads(non_youtube_urls)
            link_count = len(parsed_urls)
            if parsed_urls:
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
                        f"{enriched_text}\n\n--- LINK SUMMARIES ---\n{link_summary_text}"
                    )
                    logger.info(f"Enrichment: added {len(link_summaries)} link summaries")
        except Exception as e:
            logger.warning(f"Enrichment: link summary processing failed: {e}")
            failed_steps.append("links")

    # --- 4. Reply chain context ---
    # Telethon-dependent; the worker has no Telethon client. Tracked as a
    # companion follow-up to #1297. Skipped silently here so this branch does
    # not regress until the follow-up persists pre-fetched reply chains.
    reply_chain_summary = "skipped:companion_issue"

    # Single enrichment summary line
    summary = (
        f"[enrichment] Summary: media={media_summary}, "
        f"youtube={youtube_count}, links={link_count}, "
        f"reply_chain={reply_chain_summary}, "
        f"result_length={len(enriched_text)}"
    )
    if failed_steps:
        summary += f", failed_steps={','.join(failed_steps)}"
    logger.info(summary)

    return enriched_text
