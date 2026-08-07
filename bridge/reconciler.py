"""Periodic message reconciliation: detect and recover messages missed during live connection.

The Telegram bridge can silently miss messages when Telethon drops updates due to
pts (persistent timeline sequence) gaps. This module runs a periodic scan of
monitored groups, compares recent messages against dedup records, and re-dispatches
any that were never processed.

Complements bridge/catchup.py (startup-only scan) with continuous monitoring.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from bridge.dedup import (
    claim_message,
    is_duplicate_message,
    record_last_processed,
    record_message_processed,
    release_message_claim,
)
from bridge.history_fetch import fetch_messages_back_to
from bridge.routing import persona_to_session_type, resolve_persona
from bridge.silent_stream import SilentStreamState, check_silent_chat
from config.enums import SessionType

logger = logging.getLogger(__name__)

# Configuration
RECONCILE_INTERVAL_SECONDS = 180  # 3 minutes
# 30-minute lookback covers the worst-case multi-restart scenario (issue #1408)
# where the worker was down across several restarts and a message would age out
# of the old 10-minute window before the first effective reconciler scan. This
# is only the FLOOR -- the per-chat cursor extension below reaches further back
# when the cursor is older (5d9515671).
RECONCILE_LOOKBACK_MINUTES = 30
# Page size for one get_messages() call. The scan pages backwards until it
# reaches the per-chat cutoff rather than stopping after a single fetch: a
# single fixed-size fetch silently truncated the cursor-extended lookback,
# losing every missed message older than the newest page exactly when a deep
# wedge made recovery matter most (issue #2476).
RECONCILE_MESSAGE_LIMIT = int(os.environ.get("RECONCILE_MESSAGE_LIMIT", "30"))
# Hard ceiling on messages fetched per chat per scan. This is the real bound on
# recovery depth, and it is NOT free to raise: DedupRecord retains only its most
# recent _MAX_IDS ids per chat, so a scan that reaches past that window loses
# its "already handled" guard and re-delivers old messages. The invariant
# `DedupRecord._MAX_IDS >= RECONCILE_MAX_MESSAGES_PER_CHAT` is pinned by
# tests/unit/test_dedup.py -- raise both together or not at all.
# GRAIN OF SALT: provisional/tunable. 200 covers a multi-hour wedge in a busy
# chat at ~7 pages; sized against dedup retention, not measured traffic.
RECONCILE_MAX_MESSAGES_PER_CHAT = int(os.environ.get("RECONCILE_MAX_MESSAGES_PER_CHAT", "200"))


async def reconciler_loop(
    client,
    monitored_groups: list[str],
    should_respond_fn,
    enqueue_agent_session_fn,
    find_project_fn,
    silent_stream_state: SilentStreamState | None = None,
):
    """Run periodic reconciliation to detect missed messages.

    Scans monitored groups for messages that bypassed the event handler.
    Gates all re-dispatches through dedup to prevent duplicate processing.

    When ``silent_stream_state`` is provided, the silent-gap observability check
    (issue #1408) rides this same dialog pass — reusing the dialogs already
    fetched here rather than running its own loop with a redundant
    ``get_dialogs()`` call.
    """
    logger.info(
        "[reconciler] Started (interval=%ds, lookback_floor=%dm, page=%d, max_per_chat=%d)",
        RECONCILE_INTERVAL_SECONDS,
        RECONCILE_LOOKBACK_MINUTES,
        RECONCILE_MESSAGE_LIMIT,
        RECONCILE_MAX_MESSAGES_PER_CHAT,
    )

    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            recovered = await reconcile_once(
                client=client,
                monitored_groups=monitored_groups,
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
                silent_stream_state=silent_stream_state,
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
    enqueue_agent_session_fn,
    find_project_fn,
    silent_stream_state: SilentStreamState | None = None,
) -> int:
    """Run a single reconciliation scan across all monitored groups.

    Returns the number of missed messages recovered and enqueued.

    When ``silent_stream_state`` is provided, the silent-gap observability check
    (issue #1408) runs for each monitored dialog using the dialogs already
    fetched here — no separate ``get_dialogs()`` call. The check is purely
    observability (logs a WARNING) and never affects the recovery count.
    """
    from bridge.catchup import CATCHUP_DISABLED_FLAG, catchup_disabled

    if catchup_disabled():
        logger.warning(
            "[reconciler] Skipped — %s exists (operator kill switch)", CATCHUP_DISABLED_FLAG
        )
        return 0

    if not monitored_groups:
        logger.debug("[reconciler] No monitored groups, skipping scan")
        return 0

    cutoff = datetime.now(UTC) - timedelta(minutes=RECONCILE_LOOKBACK_MINUTES)
    recovered = 0
    groups_scanned = 0
    # Structured per-scan counters (Observability & Rollback signal, mirrors
    # bridge/catchup.py) — a post-rollout spike in re_enqueued is greppable.
    re_enqueued = 0
    skipped_duplicate = 0

    dialogs = await client.get_dialogs()

    # Record successful API probe for the stale-stream detector.
    # get_dialogs() success proves the Telethon TCP/API layer is alive.
    # Best-effort: never raises.
    from bridge.liveness import record_probe_ok

    record_probe_ok()

    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title:
            continue

        if chat_title.lower() not in monitored_groups:
            continue

        project = find_project_fn(chat_title)

        # Silent-gap observability (issue #1408): ride this dialog pass instead
        # of a separate loop. Best-effort — a failure here must never break the
        # recovery scan. Runs even when the chat has no project config (the
        # per-chat check applies its own respond_to_unaddressed gate).
        if silent_stream_state is not None:
            try:
                await check_silent_chat(
                    chat_id=dialog.id,
                    chat_title=chat_title,
                    project=project,
                    state=silent_stream_state,
                )
            except Exception as e:
                logger.error(
                    "[silent-stream] check failed for %s: %s", chat_title, e, exc_info=True
                )

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

        # Per-chat cutoff (mirrors bridge/catchup.py, issue #1408): the fixed
        # `cutoff` above is a rolling `now - 30min` window. A message that
        # arrives during an update-loop wedge (issue #1408) and is missed by
        # both the live handler AND every reconciler tick within that 30-minute
        # window ages out of this fixed window forever -- no future tick will
        # ever look back far enough to find it again, even though the message
        # was never dispatched. Extending from the per-chat last-processed
        # cursor closes that gap the same way catchup's startup scan already
        # does: look back AT LEAST as far as the global cutoff, and further if
        # the cursor is older. Never max() -- that could miss a message that
        # arrived after the last cursor update but before the gap.
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
                        "[reconciler] %s: per-chat cutoff %s predates global cutoff %s "
                        "(last dispatched %s) — extending lookback",
                        chat_title,
                        per_chat_cutoff.isoformat(),
                        cutoff.isoformat(),
                        last_proc_dt.isoformat(),
                    )
        except Exception as e:
            # Defensive: a cursor read failure must not break the per-group scan.
            logger.warning(
                "[reconciler] %s: get_last_processed failed (%s); falling back to global cutoff",
                chat_title,
                e,
            )

        try:
            # Paged, bounded, loud fetch shared with bridge/catchup.py
            # (issues #2476/#2477) — see bridge/history_fetch.py.
            messages = await fetch_messages_back_to(
                client,
                dialog.entity,
                per_chat_cutoff,
                chat_title,
                page_size=RECONCILE_MESSAGE_LIMIT,
                max_messages=RECONCILE_MAX_MESSAGES_PER_CHAT,
                scanner="reconciler",
            )

            for message in messages:
                # Skip messages outside lookback window
                if message.date < per_chat_cutoff:
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
                    skipped_duplicate += 1
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
                        "[reconciler] persona resolution failed for chat %s (%s); "
                        "defaulting to eng: %s",
                        chat_id,
                        chat_title,
                        e,
                    )
                    session_type = SessionType.ENG

                # Atomic per-message producer claim (issue #1817 B1, BLOCKER):
                # shared key with the live handler (bridge/dispatch.py) and
                # bridge/catchup.py so a peer producer racing on this SAME
                # message loses cleanly instead of double-enqueueing. A lost
                # claim means a peer already won (or is winning) this message
                # -- skip WITHOUT recording durable dedup, so a winner-death
                # self-heals via the next reconciler scan re-picking the
                # message instead of being silently dropped forever.
                if not await claim_message(chat_id, message.id):
                    logger.info(
                        "[reconciler] lost message claim for chat=%s msg=%s "
                        "-- a peer producer won, skipping",
                        chat_id,
                        message.id,
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
                        priority="low",
                        sender_id=sender_id,
                        session_type=session_type,
                        project_config=project,
                    )
                except BaseException:
                    # No orphan: release the claim so a retry (this scan's
                    # next tick, or a peer) is not locked out for the TTL.
                    await release_message_claim(chat_id, message.id)
                    raise

                # Only the winner writes the durable cursor-coupled membership record,
                # and only AFTER its own successful enqueue -- see the
                # BLOCKER rationale in bridge/dispatch.py's module docstring.
                await record_message_processed(chat_id, message.id)
                await record_last_processed(chat_id, message.id, message.date)
                recovered += 1
                re_enqueued += 1
                age_s = (datetime.now(UTC) - message.date).total_seconds()
                logger.info(
                    "catchup.re_enqueue reason=reconciler msg_id=%s chat=%s age_s=%.0f",
                    message.id,
                    chat_id,
                    age_s,
                )

        except Exception as e:
            logger.error("[reconciler] Error scanning %s: %s", chat_title, e, exc_info=True)
            continue

    logger.debug(
        "[reconciler] Scanned %d group(s), recovered %d message(s)",
        groups_scanned,
        recovered,
    )
    logger.info(
        "[reconciler] Scan decision counters: re_enqueued=%d skipped_duplicate=%d",
        re_enqueued,
        skipped_duplicate,
    )
    return recovered
