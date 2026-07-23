"""One-time per-chat dedup-seeding pass (rollout bridge for the dedup TTL fix).

Background (see docs/plans/catchup-rehandles-handled-messages.md): DedupRecord's
old TTL (2h) was shorter than the startup-catchup scan window (cursor-extended,
up to ~30 days), so a message handled more than 2h before a bridge restart had
already aged out of the dedup set and been deleted from Redis. Now that
DedupRecord's TTL is coupled to the cursor TTL (models/dedup.py), the dedup set
is authoritative going forward -- but the *already-handled historical window*
has nothing in it, because those keys were deleted under the old short TTL. An
``EXPIRE``-refresh migration cannot fix this (nothing left to refresh).

This module re-seeds the dedup set once, from a live Telethon read, before the
first post-fix scan runs. For each monitored/owned chat, it fetches the most
recent messages and writes a DedupRecord entry (via ``record_message_processed``)
for every *inbound* message whose id is `<=` that chat's LastProcessedRecord
cursor id -- i.e. messages the cursor already advanced past, and therefore
messages that were demonstrably already dispatched.

Idempotent and one-shot **per chat**, never globally. Each chat is guarded by
its own marker file, ``data/dedup-seeded.{chat_id}``, written ONLY after that
chat's seed fully succeeds. A single global marker was an explicit BLOCKER in
plan critique: a partial per-chat Telethon failure (rate-limit, transient)
would still let the pass finish and stamp a global marker, permanently
skipping the failed chat's seed on every future restart. Per-chat markers
self-heal instead: an unmarked chat (failed, or newly added) simply re-seeds
on the next restart; an already-seeded chat is skipped without a Telethon
read.

Ordering contract (Race 2 / Race 3 in the plan): the caller MUST run this pass
to completion BEFORE ``bridge.catchup.scan_for_missed_messages`` reads the
dedup set, and SHOULD sequence it before the live NewMessage handler begins
dispatching. Sequencing removes the lost-update race on
``DedupRecord.add_message`` (a read-modify-write, not an atomic SADD) without
needing a lock.
"""

import logging
from pathlib import Path

from bridge.dedup import get_last_processed, record_message_processed

logger = logging.getLogger(__name__)

_SEED_MARKER_DIR = Path(__file__).resolve().parent.parent / "data"
_SEED_MARKER_PREFIX = "dedup-seeded."


def _seed_marker_path(chat_id) -> Path:
    """Per-chat marker path -- NEVER a single global marker (critique BLOCKER)."""
    return _SEED_MARKER_DIR / f"{_SEED_MARKER_PREFIX}{chat_id}"


def is_chat_seeded(chat_id) -> bool:
    """True if this chat's one-time dedup seed already completed successfully."""
    return _seed_marker_path(chat_id).exists()


def _write_seed_marker(chat_id) -> None:
    _SEED_MARKER_DIR.mkdir(parents=True, exist_ok=True)
    _seed_marker_path(chat_id).touch()


async def seed_dedup_for_chat(client, chat_id, entity, max_messages: int) -> tuple[int, bool]:
    """Seed dedup for a single chat. Returns ``(count_seeded, marker_written)``.

    Writes the marker only on full success (including the "no cursor yet"
    case, which is a legitimate no-op outcome, not a failure). On any
    exception, logs and returns ``(0, False)`` WITHOUT writing the marker, so
    the chat retries on the next restart.
    """
    if is_chat_seeded(chat_id):
        return (0, True)

    try:
        last_proc = await get_last_processed(chat_id)
        if last_proc is None:
            # No cursor -> no "already dispatched" evidence -> seed nothing.
            # This is a successful (if empty) outcome: mark done so we don't
            # re-probe Telethon for this chat every restart. A future
            # dispatch will populate dedup and the cursor normally.
            _write_seed_marker(chat_id)
            return (0, True)

        cursor_id, _cursor_dt = last_proc

        messages = await client.get_messages(entity, limit=max_messages)
        seeded = 0
        for message in messages:
            if message.out:
                continue
            if message.id <= cursor_id:
                await record_message_processed(chat_id, message.id)
                seeded += 1

        _write_seed_marker(chat_id)
        return (seeded, True)
    except Exception as e:
        logger.warning(
            "[dedup-seed] chat=%s seeding failed (marker NOT written, will retry next restart): %s",
            chat_id,
            e,
        )
        return (0, False)


async def seed_dedup_for_chats(
    client,
    monitored_groups: list[str],
    find_project_fn,
    max_messages: int,
) -> dict:
    """Run the one-time per-chat dedup seed pass across monitored/owned chats.

    Mirrors bridge/catchup.py's dialog-matching logic (case-insensitive title
    match against ``monitored_groups``, deduped by dialog id, must resolve to
    a known project) so the seed covers exactly the chats the scanners cover.

    Defensive at every layer: a ``get_dialogs()`` failure skips the whole pass
    (logged, never raises); a per-chat failure is isolated to that chat and
    does not abort the others. Never crashes bridge startup.

    Returns a ``{chat_id: {"chat_title": str, "count": int, "marker_written": bool}}``
    summary for logging/testing.
    """
    summary: dict = {}
    try:
        dialogs = await client.get_dialogs()
    except Exception as e:
        logger.warning("[dedup-seed] get_dialogs failed, skipping seed pass entirely: %s", e)
        return summary

    seen_chat_ids: set[int] = set()
    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue
        if chat_title.lower() not in monitored_groups:
            continue
        if dialog.id in seen_chat_ids:
            continue
        seen_chat_ids.add(dialog.id)

        try:
            project = find_project_fn(chat_title)
        except Exception as e:
            logger.warning("[dedup-seed] find_project_fn failed for %s: %s", chat_title, e)
            continue
        if not project:
            continue

        chat_id = dialog.id
        if is_chat_seeded(chat_id):
            logger.debug("[dedup-seed] chat=%s (%s) already seeded, skipping", chat_id, chat_title)
            continue

        count, marker_written = await seed_dedup_for_chat(
            client, chat_id, dialog.entity, max_messages
        )
        summary[chat_id] = {
            "chat_title": chat_title,
            "count": count,
            "marker_written": marker_written,
        }
        # Structured per-chat seed summary (Observability & Rollback signal).
        logger.info(
            "[dedup-seed] chat=%s title=%s seeded=%d marker_written=%s",
            chat_id,
            chat_title,
            count,
            marker_written,
        )

    return summary
