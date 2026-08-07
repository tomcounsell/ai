"""Shadow-mode durable Room-inbox append (Task 11 phase 1, issue #2494).

The target inbound flow of ``docs/plans/durability-room-job-agentrun.md`` is
👀 → **durable Room-inbox append** → route → bind: one Redis write collapses
the multi-step intake loss window to a single operation. Cutover is 3-phase
(spike-4 checklist) and this module is **phase 1 — shadow**: the durable
append is written ALONGSIDE the untouched dispatch flow so parity can be
verified in production. Nothing reads this inbox for dispatch yet; the
authoritative flip ships in a separate release, and the inbox is cap-bounded
(``models.room.ROOM_INBOX_MAX_ENTRIES``) precisely because nothing drains it
during the shadow phase.

Contract: never raises into the live intake path. A failed append returns
``False`` and logs at ERROR (loud, but the untouched dispatch flow still
handles the message — shadow mode has no durability responsibility yet).
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def shadow_append_inbox(
    project: dict | None,
    *,
    chat_id,
    message_id: int,
    sender_id,
    sender_name: str | None,
    text: str,
    date: datetime | None,
) -> bool:
    """Durably append one inbound Telegram message to its Room's inbox.

    The Room resolves from the project (``projects.json`` stays the source of
    truth) and the chat id — DMs and groups identically
    (``telegram:{chat_id}``). The entry carries a stable message identity
    (``chat_id`` + ``message_id``) so the future authoritative phase can
    route/bind idempotently from it.

    Returns ``True`` on a durable write, ``False`` otherwise. Never raises.
    """
    try:
        from models.room import Room, telegram_addressee

        project_key = (project or {}).get("_key")
        if not project_key:
            logger.error(
                "[room-inbox] shadow append skipped: no project key (chat=%s msg=%s)",
                chat_id,
                message_id,
            )
            return False

        room = Room.resolve(project_key, telegram_addressee(chat_id))
        return room.append_inbox(
            {
                "chat_id": str(chat_id),
                "message_id": message_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "text": text,
                "ts": date.isoformat() if date is not None else None,
            }
        )
    except Exception as e:  # noqa: BLE001 — intake must survive an inbox outage
        logger.error(
            "[room-inbox] shadow append failed for chat=%s msg=%s: %s", chat_id, message_id, e
        )
        return False


async def shadow_route_job(
    project: dict | None,
    *,
    chat_id,
    message_id: int,
    reply_to_msg_id: int | None,
    text: str,
):
    """Shadow bind-or-mint Job routing for one inbound message (Task 13).

    Runs the single routing authority (``bridge/job_router.py``) alongside
    the untouched session dispatch flow: Jobs are minted/bound and the
    permanent ``reply:`` index is written, but dispatch still routes from
    the live event until the authoritative 👀→append→route→bind cutover
    (phase 2, its own release). Spawned as a background task by the intake
    handler so router latency (spike-3: median ~1.1s on granite) never
    blocks the hot path.

    Returns the bound/minted ``Job`` or ``None``. Never raises.
    """
    try:
        from bridge.job_router import route_message, telegram_message_key
        from models.room import room_id as make_room_id
        from models.room import telegram_addressee

        project_key = (project or {}).get("_key")
        if not project_key:
            logger.debug(
                "[room-inbox] shadow route skipped: no project key (chat=%s msg=%s)",
                chat_id,
                message_id,
            )
            return None

        rid = make_room_id(project_key, telegram_addressee(chat_id))
        reply_key = telegram_message_key(chat_id, reply_to_msg_id) if reply_to_msg_id else None
        return await route_message(
            rid,
            text,
            telegram_message_key(chat_id, message_id),
            reply_to_message_key=reply_key,
        )
    except Exception as e:  # noqa: BLE001 — intake must survive a router outage
        logger.error(
            "[room-inbox] shadow route failed for chat=%s msg=%s: %s", chat_id, message_id, e
        )
        return None
