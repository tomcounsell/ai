"""Silent Telethon update-gap observability (issue #1408).

Telethon can stop delivering ``NewMessage`` events for a specific chat with no
error and no disconnect — the bridge believes it is connected, but the event
handler simply stops firing for that chat. This is observability-only: the
reconciler and per-chat catchup cursor handle *recovery*; this watcher makes the
*silent failure* visible by logging a WARNING when a chat that recently had
activity goes quiet while the bridge is healthy.

The silent-gap check **rides the reconciler's existing dialog pass** rather than
running its own loop. The reconciler already calls ``client.get_dialogs()`` every
``RECONCILE_INTERVAL_SECONDS`` and iterates every monitored group; this module
provides the per-chat suppression logic the reconciler invokes inside that loop,
reusing the dialogs it already fetched. This adds **no** recurring
``get_dialogs()`` call beyond the reconciler's existing one (issue #1408 no-go:
must not increase steady-state Telegram API call rate).

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

import logging
import time
from dataclasses import dataclass, field

from bridge.dedup import get_last_event_ts

logger = logging.getLogger(__name__)

SILENCE_THRESHOLD_SECONDS = 15 * 60  # 15 minutes of silence triggers a warning
WARN_SUPPRESSION_SECONDS = 30 * 60  # one warning per chat per 30 minutes


@dataclass
class SilentStreamState:
    """Mutable state the silent-gap check carries across reconciler passes.

    Threaded through ``reconcile_once`` so the silent-gap check can ride the
    reconciler's existing dialog iteration instead of running its own loop with
    a redundant ``get_dialogs()`` call (issue #1408).
    """

    bridge_start_ts: float
    warned_chats: dict = field(default_factory=dict)


def _is_respond_to_unaddressed(project: dict | None) -> bool:
    """True if the project config opts the chat into respond-to-unaddressed."""
    if not project:
        return False
    return bool(project.get("telegram", {}).get("respond_to_unaddressed", False))


async def check_silent_chat(
    chat_id,
    chat_title: str,
    project: dict | None,
    state: SilentStreamState,
    now: float | None = None,
) -> bool:
    """Run the silent-gap check for a single (already-fetched) dialog.

    Designed to be called from inside the reconciler's dialog loop, reusing the
    dialog the reconciler already enumerated — no independent ``get_dialogs()``
    call. Returns ``True`` if a warning was emitted for this chat.

    Args:
        chat_id: the dialog id (``dialog.id``, with the -100 supergroup prefix).
        chat_title: the dialog title (already matched against monitored groups).
        project: the project config dict for this chat (or None).
        state: shared mutable silent-stream state (start ts + per-chat
            suppression timestamps), mutated in place across passes.
        now: override for the current unix timestamp (testing).
    """
    now = time.time() if now is None else now

    # Never warn before the bridge has been up at least the silence threshold —
    # a cold last_event right after startup is expected, not anomalous.
    if now - state.bridge_start_ts < SILENCE_THRESHOLD_SECONDS:
        return False

    if not _is_respond_to_unaddressed(project):
        return False

    last_event_ts = await get_last_event_ts(chat_id)
    # No prior activity baseline → no signal.
    if last_event_ts is None:
        return False

    silence = now - last_event_ts
    if silence < SILENCE_THRESHOLD_SECONDS:
        return False

    # Per-chat suppression window.
    last_warn = state.warned_chats.get(chat_id)
    if last_warn is not None and (now - last_warn) < WARN_SUPPRESSION_SECONDS:
        return False

    state.warned_chats[chat_id] = now
    logger.warning(
        "[silent-stream] No events for chat %s (id=%s) in %d+ min — "
        "possible Telethon update gap; reconciler will scan within 3 min",
        chat_title,
        chat_id,
        int(silence // 60),
    )
    return True


async def check_silent_streams(
    dialogs,
    monitored_groups: list[str],
    find_project_fn,
    state: SilentStreamState,
    now: float | None = None,
) -> int:
    """Run the silent-gap check across an already-fetched dialog list.

    Does **not** fetch dialogs itself — the caller (the reconciler) passes in the
    dialogs it already retrieved. Returns the number of warnings emitted.

    Args:
        dialogs: the dialog list the reconciler already fetched this pass.
        monitored_groups: lowercase monitored group titles.
        find_project_fn: maps a chat title to its project config dict (or None).
        state: shared mutable silent-stream state.
        now: override for the current unix timestamp (testing).
    """
    if not monitored_groups:
        return 0

    now = time.time() if now is None else now

    # Cheap top-level cold-start guard (the per-chat check repeats it, but this
    # avoids touching Redis at all during the cold-start window).
    if now - state.bridge_start_ts < SILENCE_THRESHOLD_SECONDS:
        return 0

    warnings_emitted = 0
    for dialog in dialogs:
        chat_title = getattr(dialog.entity, "title", None)
        if not chat_title or chat_title.lower() not in monitored_groups:
            continue

        project = find_project_fn(chat_title)
        if await check_silent_chat(
            chat_id=dialog.id,
            chat_title=chat_title,
            project=project,
            state=state,
            now=now,
        ):
            warnings_emitted += 1

    return warnings_emitted
