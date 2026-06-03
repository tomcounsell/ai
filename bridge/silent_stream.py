"""Silent Telethon update-gap observability (issue #1408).

Telethon can stop delivering ``NewMessage`` events for a specific chat with no
error and no disconnect — the bridge believes it is connected, but the event
handler simply stops firing for that chat. This is observability-only: the
reconciler and per-chat catchup cursor handle *recovery*; this watcher makes the
*silent failure* visible by logging a WARNING when a chat that recently had
activity goes quiet while the bridge is healthy.

False-positive suppression rules:

- Only ``respond_to_unaddressed: true`` chats are watched — those are the chats
  where a silently dropped message has guaranteed downstream consequences.
  Mention-gated chats tolerate silence and would generate noise.
- A chat with no ``bridge:last_event`` record is skipped: with no prior-activity
  baseline there is no signal that "silence" is anomalous.
- The bridge must have been continuously connected for at least the silence
  threshold — we never warn within the first 15 minutes after startup, when a
  cold ``last_event`` is expected.
- Each chat warns at most once per 30-minute window to avoid log spam during a
  sustained gap.
"""

import asyncio
import logging
import time

from bridge.dedup import get_last_event_ts

logger = logging.getLogger(__name__)

SILENT_STREAM_INTERVAL_SECONDS = 300  # 5 minutes between scans
SILENCE_THRESHOLD_SECONDS = 15 * 60  # 15 minutes of silence triggers a warning
WARN_SUPPRESSION_SECONDS = 30 * 60  # one warning per chat per 30 minutes


def _is_respond_to_unaddressed(project: dict | None) -> bool:
    """True if the project config opts the chat into respond-to-unaddressed."""
    if not project:
        return False
    return bool(project.get("telegram", {}).get("respond_to_unaddressed", False))


async def check_silent_streams(
    client,
    monitored_groups: list[str],
    find_project_fn,
    bridge_start_ts: float,
    warned_chats: dict,
    now: float | None = None,
) -> int:
    """Run a single silent-stream scan; return the number of warnings emitted.

    Args:
        client: TelegramClient (used to enumerate dialogs → chat ids/titles).
        monitored_groups: lowercase monitored group titles.
        find_project_fn: maps a chat title to its project config dict (or None).
        bridge_start_ts: unix timestamp of bridge startup (suppresses cold-start
            false positives).
        warned_chats: mutable dict {chat_id: last_warn_ts} for per-chat
            suppression; mutated in place across scans.
        now: override for the current unix timestamp (testing).
    """
    if not monitored_groups:
        return 0

    now = time.time() if now is None else now

    # Never warn before the bridge has been up at least the silence threshold —
    # a cold last_event right after startup is expected, not anomalous.
    if now - bridge_start_ts < SILENCE_THRESHOLD_SECONDS:
        return 0

    warnings_emitted = 0
    dialogs = await client.get_dialogs()

    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title or chat_title.lower() not in monitored_groups:
            continue

        project = find_project_fn(chat_title)
        if not _is_respond_to_unaddressed(project):
            continue

        chat_id = dialog.id

        last_event_ts = await get_last_event_ts(chat_id)
        # No prior activity baseline → no signal.
        if last_event_ts is None:
            continue

        silence = now - last_event_ts
        if silence < SILENCE_THRESHOLD_SECONDS:
            continue

        # Per-chat suppression window.
        last_warn = warned_chats.get(chat_id)
        if last_warn is not None and (now - last_warn) < WARN_SUPPRESSION_SECONDS:
            continue

        warned_chats[chat_id] = now
        warnings_emitted += 1
        logger.warning(
            "[silent-stream] No events for chat %s (id=%s) in %d+ min — "
            "possible Telethon update gap; reconciler will scan within 3 min",
            chat_title,
            chat_id,
            int(silence // 60),
        )

    return warnings_emitted


async def silent_stream_loop(
    client,
    monitored_groups: list[str],
    find_project_fn,
    bridge_start_ts: float | None = None,
) -> None:
    """Background loop: periodically scan for silently-stalled chats.

    Each iteration is wrapped so a transient failure (e.g. a Redis read error)
    logs and the loop survives to the next cycle.
    """
    bridge_start_ts = time.time() if bridge_start_ts is None else bridge_start_ts
    warned_chats: dict = {}

    logger.info(
        "[silent-stream] Started (interval=%ds, silence_threshold=%dm)",
        SILENT_STREAM_INTERVAL_SECONDS,
        SILENCE_THRESHOLD_SECONDS // 60,
    )

    while True:
        await asyncio.sleep(SILENT_STREAM_INTERVAL_SECONDS)
        try:
            await check_silent_streams(
                client=client,
                monitored_groups=monitored_groups,
                find_project_fn=find_project_fn,
                bridge_start_ts=bridge_start_ts,
                warned_chats=warned_chats,
            )
        except Exception as e:
            logger.error("[silent-stream] Error in scan: %s", e, exc_info=True)
