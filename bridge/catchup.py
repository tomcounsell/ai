"""Startup catchup: scan for unread messages missed during downtime.

On bridge startup, this module scans monitored groups for recent messages
that weren't processed (e.g., sent while the bridge was down). It enqueues
any messages that should have triggered a response.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telethon.errors import FloodWaitError

from bridge.dedup import get_or_init_dm_coverage_epoch
from bridge.history_fetch import fetch_messages_back_to
from bridge.room_inbox import shadow_append_inbox
from bridge.routing import (
    find_project_for_dm_dialog,
    persona_to_session_type,
    resolve_persona,
)
from config.enums import SessionType

logger = logging.getLogger(__name__)

# How far back to look for missed messages (default: 1 hour)
CATCHUP_LOOKBACK_MINUTES = 60

# Page size for one get_messages() call. The scan pages backwards until it
# reaches the per-chat cutoff rather than stopping after a single fetch: a
# single fixed-size fetch silently truncated the cursor-extended lookback,
# losing every missed message older than the newest page exactly when a deep
# outage made recovery matter most (issues #2476/#2477).
# GRAIN OF SALT: provisional/tunable.
CATCHUP_MESSAGE_LIMIT = int(os.environ.get("CATCHUP_MESSAGE_LIMIT", "50"))
# Hard ceiling on messages fetched per chat per scan. This is the real bound on
# recovery depth, and it is NOT free to raise: DedupRecord retains only its most
# recent _MAX_IDS ids per chat, so a scan that reaches past that window loses
# its "already handled" guard and re-delivers old messages. Two invariants pin
# this value: `DedupRecord._MAX_IDS >= CATCHUP_MAX_MESSAGES_PER_CHAT`
# (tests/unit/test_dedup.py) and equality with the reconciler's ceiling so the
# two recovery scanners keep a single depth policy
# (tests/unit/test_catchup_paging.py). Raise them together or not at all.
# GRAIN OF SALT: provisional/tunable. 200 matches RECONCILE_MAX_MESSAGES_PER_CHAT;
# sized against dedup retention, not measured traffic.
CATCHUP_MAX_MESSAGES_PER_CHAT = int(os.environ.get("CATCHUP_MAX_MESSAGES_PER_CHAT", "200"))

# FloodWaitError is Telegram's normal rate-limit backpressure signal, NOT a crash:
# GetHistoryRequest during a catchup scan trips it when many chats are scanned
# back-to-back. We honor the requested wait and retry rather than logging it at
# error level (which Sentry captures, fanning out one issue per chat name — see
# issues #2353-#2355). Values are provisional/tunable.
CATCHUP_FLOODWAIT_MAX_RETRIES = 2  # retries after the initial attempt
CATCHUP_FLOODWAIT_MAX_SLEEP_S = 60  # skip the chat if Telegram asks for a longer wait
CATCHUP_FLOODWAIT_SLEEP_BUFFER_S = 2  # small cushion added to Telegram's requested wait

# Operator kill switch for ALL message-recovery scans (startup catchup, periodic
# reconciler, valor-catchup agent sweep). Same flag-file convention as
# data/auto-revert-enabled: `touch data/catchup-disabled` pauses recovery,
# `rm data/catchup-disabled` re-enables it. Realtime message handling is
# unaffected — only the "re-scan history for missed messages" layer is gated.
CATCHUP_DISABLED_FLAG = Path(__file__).resolve().parent.parent / "data" / "catchup-disabled"


def catchup_disabled() -> bool:
    """True when the operator flag file pauses all recovery scans."""
    return CATCHUP_DISABLED_FLAG.exists()


def catchup_disabled_age_hours() -> float | None:
    """Hours since the kill-switch flag was set, or None when it is absent.

    Uses the flag file's mtime (`touch data/catchup-disabled` sets it), so the
    age tracks when the operator last (re)armed the switch. Never raises: a
    flag that vanishes between the exists-check and the stat maps to None.
    """
    import time

    try:
        mtime = CATCHUP_DISABLED_FLAG.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() - mtime) / 3600.0)


def kill_switch_status() -> dict:
    """Operator-surface status of the recovery kill switch (issue #2473).

    Single source of truth consumed by `tools.doctor` and `/dashboard.json`.
    A kill switch with no expiry and no alarm silently disabled the entire
    recovery layer for 7 days; `stale` flips once the flag has been present
    longer than `settings.timeouts.catchup_disabled_warn_hours`.
    """
    from config.settings import settings

    warn_hours = settings.timeouts.catchup_disabled_warn_hours
    # `disabled` shares catchup_disabled()'s exists() predicate so this surface
    # can never disagree with the scanners' own gate: a flag whose stat() fails
    # mid-check still reports disabled=True, just with no age (and not stale).
    disabled = catchup_disabled()
    age = catchup_disabled_age_hours() if disabled else None
    return {
        "disabled": disabled,
        "age_hours": round(age, 2) if age is not None else None,
        "warn_hours": warn_hours,
        "stale": age is not None and age >= warn_hours,
    }


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
    # Structured per-scan counters (Observability & Rollback signal): a
    # post-rollout spike in re_enqueued for historical (pre-restart) message
    # ids is greppable in logs/bridge.log.
    re_enqueued = 0
    skipped_duplicate = 0
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
        is_dm = False
        if chat_title:
            # Note: monitored_groups contains lowercase group names, but Telegram
            # group titles may have capitals. Compare case-insensitively.
            if chat_title.lower() not in monitored_groups:
                logger.debug(f"[catchup] Skipping non-monitored group: {chat_title}")
                continue

            # Deduplicate by dialog ID — Telethon may return the same supergroup
            # twice (once as a channel, once as a linked discussion group).
            if dialog.id in seen_chat_ids:
                logger.warning(
                    f"[catchup] Skipping duplicate dialog for {chat_title} (id={dialog.id})"
                )
                continue
            seen_chat_ids.add(dialog.id)

            logger.info(f"[catchup] Found monitored group: {chat_title}")
            matched_groups.append(chat_title)

            project = find_project_fn(chat_title)
            if not project:
                logger.warning(f"[catchup] No project config for {chat_title}")
                continue
        else:
            # DM Rooms (durability plan Task 10, issue #2494): a private chat
            # is a User entity with no title. A whitelisted DM is a covered
            # Room and is scanned identically to a group.
            project = find_project_for_dm_dialog(dialog.entity)
            if project is None:
                continue
            if dialog.id in seen_chat_ids:
                continue
            seen_chat_ids.add(dialog.id)
            is_dm = True

        chat_label = chat_title or f"DM:{dialog.id}"

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
        # the crash. Note: the 24-hour cap above does NOT bound this — it applies
        # only to lookback_override. Total time reach is unbounded; recovery depth
        # is bounded by CATCHUP_MAX_MESSAGES_PER_CHAT in the fetch below (#2477).
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
                        f"[catchup] {chat_label}: per-chat cutoff {per_chat_cutoff.isoformat()} "
                        f"predates global cutoff {cutoff.isoformat()} "
                        f"(last dispatched {last_proc_dt.isoformat()}) — extending lookback"
                    )
        except Exception as e:
            # Defensive: a cursor read failure must not break the per-group scan.
            logger.warning(
                f"[catchup] {chat_label}: get_last_processed failed ({e}); "
                f"falling back to global cutoff"
            )

        if is_dm:
            # Newly-covered DM Rooms initialize their cursor at "now" — never
            # replay pre-coverage history as "recovered" messages (durability
            # plan Task 10). Once covered, the epoch clamps every lookback.
            epoch_dt, newly_initialized = await get_or_init_dm_coverage_epoch(chat_id)
            if newly_initialized:
                logger.info(
                    f"[catchup] {chat_label}: DM coverage epoch initialized at "
                    f"{epoch_dt.isoformat()} — skipping scan this pass"
                )
                continue
            per_chat_cutoff = max(per_chat_cutoff, epoch_dt)

        logger.info(f"[catchup] Scanning {chat_label} for missed messages...")

        try:
            # Fetch recent messages via the paged, bounded, loud fetch shared
            # with bridge/reconciler.py (issues #2476/#2477): page backwards to
            # per_chat_cutoff, hard-capped at CATCHUP_MAX_MESSAGES_PER_CHAT,
            # WARN on truncation. GetHistoryRequest can trip Telegram's
            # FloodWaitError rate-limit backpressure when many chats are scanned
            # back-to-back; honor the requested wait and retry rather than treating
            # it as a hard error (issues #2353-#2355). A FloodWait mid-paging
            # restarts the fetch from the newest message — the partial
            # collection is discarded before the caller sees it, so the retry
            # cost is wasted API calls, never duplicates.
            messages = None
            for _attempt in range(CATCHUP_FLOODWAIT_MAX_RETRIES + 1):
                try:
                    messages = await fetch_messages_back_to(
                        client,
                        dialog.entity,
                        per_chat_cutoff,
                        chat_label,
                        page_size=CATCHUP_MESSAGE_LIMIT,
                        max_messages=CATCHUP_MAX_MESSAGES_PER_CHAT,
                        scanner="catchup",
                    )
                    break
                except FloodWaitError as flood:
                    if flood.seconds > CATCHUP_FLOODWAIT_MAX_SLEEP_S:
                        logger.warning(
                            "[catchup] %s: FloodWait %ds exceeds cap %ds — skipping "
                            "this chat's scan (Telegram backpressure, not an error)",
                            chat_label,
                            flood.seconds,
                            CATCHUP_FLOODWAIT_MAX_SLEEP_S,
                        )
                        break
                    logger.warning(
                        "[catchup] %s: FloodWait %ds (Telegram backpressure, not an "
                        "error) — honoring wait, retrying",
                        chat_label,
                        flood.seconds,
                    )
                    await asyncio.sleep(flood.seconds + CATCHUP_FLOODWAIT_SLEEP_BUFFER_S)
            if messages is None:
                # Exhausted retries or wait exceeded the cap — skip this chat's scan.
                continue

            logger.info(
                f"[catchup] {chat_label}: Fetched {len(messages)} messages, "
                f"scanning for messages after {per_chat_cutoff.isoformat()}"
            )

            for message in messages:
                # Skip if too old
                if message.date < per_chat_cutoff:
                    logger.debug(
                        f"[catchup] {chat_label}: msg {message.id} too old "
                        f"({message.date.isoformat()}) - stopping scan"
                    )
                    break

                # Skip outgoing messages (our own)
                if message.out:
                    logger.debug(f"[catchup] {chat_label}: msg {message.id} is outgoing - skip")
                    continue

                # Skip messages without text
                text = message.text or ""
                if not text.strip():
                    logger.debug(f"[catchup] {chat_label}: msg {message.id} has no text - skip")
                    continue

                # Skip messages already processed (Redis dedup)
                from bridge.dedup import is_duplicate_message

                if await is_duplicate_message(chat_id, message.id):
                    skipped_duplicate += 1
                    logger.info(
                        f"[catchup] {chat_label}: msg {message.id} "
                        f"already processed (Redis dedup) - skip"
                    )
                    continue

                # Get sender info
                sender = await message.get_sender()
                sender_name = getattr(sender, "first_name", "Unknown")
                sender_username = getattr(sender, "username", None)
                sender_id = getattr(sender, "id", None)

                logger.info(
                    f"[catchup] {chat_label}: msg {message.id} from {sender_name} "
                    f"at {message.date.isoformat()}: '{text[:50]}...'"
                )

                # NOTE: the reply-only handled-check heuristic was removed
                # (docs/plans/catchup-rehandles-handled-messages.md). The
                # is_duplicate_message check above is now the sole and
                # authoritative "already handled" guard -- it covers every
                # answer type (reply, non-reply, reaction, deliberate
                # no-reply) because the dedup set is written at *dispatch*
                # time, not inferred from a reply-shaped side effect.

                # Check if we should respond to this message
                # Create a minimal event-like object for should_respond_fn
                class MinimalEvent:
                    def __init__(self, msg, chat_id, is_private):
                        self.message = msg
                        self.chat_id = chat_id
                        self.is_private = is_private

                minimal_event = MinimalEvent(message, chat_id, is_dm)

                should_respond, is_reply_to_valor = await should_respond_fn(
                    client,
                    minimal_event,
                    text,
                    is_dm,
                    chat_title,
                    project,
                    sender_name,
                    sender_username,
                    sender_id,
                )

                if not should_respond:
                    logger.info(
                        f"[catchup] {chat_label}: msg {message.id} - "
                        f"should_respond=False (reply_to_valor={is_reply_to_valor}) - skip"
                    )
                    continue

                # Queue this message for processing
                logger.info(
                    f"[catchup] Found missed message in {chat_label}: "
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
                    persona = resolve_persona(project, chat_title, is_dm=is_dm)
                    session_type = persona_to_session_type(persona)
                except Exception as e:
                    logger.warning(
                        "[catchup] persona resolution failed for chat %s (%s); "
                        "defaulting to eng: %s",
                        chat_id,
                        chat_label,
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

                # Durable Room-inbox shadow append (durability plan Task 11
                # phase 1, issue #2494): written alongside the untouched
                # re-enqueue below, mirroring live intake's append-precedes-
                # dispatch order. NOT authoritative — dispatch still routes
                # from the re-enqueue; shadow_append_inbox never raises into
                # the recovery path.
                shadow_append_inbox(
                    project,
                    chat_id=chat_id,
                    message_id=message.id,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    date=message.date,
                )

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

                # Only the winner writes the durable cursor-coupled membership record,
                # and only AFTER its own successful enqueue -- see the
                # BLOCKER rationale in bridge/dispatch.py's module docstring.
                from bridge.dedup import record_last_processed, record_message_processed

                await record_message_processed(chat_id, message.id)
                await record_last_processed(chat_id, message.id, message.date)
                queued += 1
                re_enqueued += 1
                age_s = (datetime.now(UTC) - message.date).total_seconds()
                logger.info(
                    "catchup.re_enqueue reason=missed_scan msg_id=%s chat=%s age_s=%.0f",
                    message.id,
                    chat_id,
                    age_s,
                )

        except FloodWaitError as flood:
            # Telegram rate-limit backpressure surfacing from a later request in
            # this chat's scan (e.g. get_sender). Expected, not a crash — log at
            # warning level so it is not captured to Sentry (issues #2353-#2355).
            logger.warning(
                "[catchup] %s: FloodWait %ds (Telegram backpressure, not an error) — "
                "skipping this chat's scan",
                chat_label,
                flood.seconds,
            )
            continue
        except Exception as e:
            logger.error(f"[catchup] Error scanning {chat_label}: {e}")
            continue

    logger.info(
        f"[catchup] Scan complete: matched {len(matched_groups)} groups, "
        f"queued {queued} missed message(s)"
    )
    logger.info(
        "[catchup] Scan decision counters: re_enqueued=%d skipped_duplicate=%d",
        re_enqueued,
        skipped_duplicate,
    )
    if matched_groups:
        logger.info(f"[catchup] Groups scanned: {', '.join(matched_groups)}")
    else:
        logger.warning(f"[catchup] No groups matched! Looking for: {monitored_groups}")
    return queued
