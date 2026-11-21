"""A model usage limit: when it resets, and the one notice its chat gets.

Tom, 2026-11-21 (Telegram DM, message 12007): when Valor hits its model
usage limit, the chat waiting on it hears "I'm offline until X" and the work
starts again once the reset passes (plan `docs/plans/3a022d3c-usage-limit.md`).

The harness ends such a turn with text like "You've hit your Fable limit ·
resets 2pm (Asia/Bangkok). Switch to another model to continue." or "…
resets Nov 21 at 2pm (Asia/Bangkok)…". `until(reason, at)` reads the reset
from a failed step's reason; the router writes it on `step.failed` as
`until` (with `zone`), and a limit row neither counts as a try nor ends the
task: it is due again at the reset (`router.retry_due`).

`notify` is the one exception to "only a chat session's own turn speaks as
Valor" (`docs/plans/one-voice.md`), because Tom asked for it by name: after
a limit failure, a chat session holding a person's unanswered message
(`notices.unanswered`, through earlier ended sessions of the chat too) sends
one fixed sentence, a reply to that message, through the chat's own send.
Once per chat per limit window: its request id is `limit/<until>`. Every
row of it is `quiet`, so the session sleeps until the reset. No model
writes it: it works with the subscription refused.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core import broker, ledger

LIMIT = re.compile(
    r"hit your .{0,40}?limit\b.{0,10}?resets (?:(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}) at )?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm) \((?P<zone>[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\)",
    re.IGNORECASE,
)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# A retry just after the reset still reads the same "resets 2pm" as that 2pm.
GRACE = timedelta(hours=1)

INTERNAL = "I'm offline until {when}: I've hit my model usage limit. I'll pick this up then."
EXTERNAL = "I'm offline until {when} and will pick this up then."


def until(reason: str | None, at: datetime) -> datetime | None:
    """The reset a usage-limit failure names, in its own zone, or None for
    any other failure. Without a date it is the first time of day named
    after `at - GRACE`; with one, that date in `at`'s year (the next year
    when it falls more than a day before `at`)."""
    m = LIMIT.search(reason or "")
    if m is None:
        return None
    try:
        zone = ZoneInfo(m["zone"])
    except ZoneInfoNotFoundError, ValueError:
        return None
    hour = int(m["h"]) % 12 + (12 if m["ampm"].lower() == "pm" else 0)
    minute = int(m["m"] or 0)
    local = at.astimezone(zone)
    if m["mon"]:
        month = next((i + 1 for i, n in enumerate(MONTHS) if n.lower() == m["mon"].lower()), None)
        if month is None:
            return None
        found = datetime(local.year, month, int(m["day"]), hour, minute, tzinfo=zone)
        if found < at - timedelta(days=1):
            found = found.replace(year=local.year + 1)
        return found
    floor = (at - GRACE).astimezone(zone)
    found = floor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if found <= floor:
        found = datetime.combine(found.date() + timedelta(days=1), found.timetz())
    return found


def when(reset: datetime, zone, now: datetime) -> str:
    """The reset in `zone` as a reader says it: "2pm Bangkok time", then
    "tomorrow" or "on Nov 21" when it falls on a later day there."""
    local, today = reset.astimezone(zone), now.astimezone(zone).date()
    hour = f"{local.hour % 12 or 12}" + (f":{local.minute:02d}" if local.minute else "")
    place = getattr(zone, "key", None) or "UTC"
    said = f"{hour}{'am' if local.hour < 12 else 'pm'} {place.split('/')[-1].replace('_', ' ')} time"
    day: date = local.date()
    if day == today + timedelta(days=1):
        return f"{said} tomorrow"
    if day > today:
        return f"{said} on {MONTHS[day.month - 1]} {day.day}"
    return said


async def notify(
    conn, performers: broker.Performers, task_id: str, now: datetime | None = None
) -> str | None:
    """Send the limit notice the session's latest `step.failed` owes, if it
    is a limit, the task a chat session, a person's message unanswered, and
    no notice of this window sent; the effect id, else None."""
    from core import notices, tasks

    rows = await ledger.read(conn, task_id)
    last = next((r for r in reversed(rows) if not ledger.quiet(r)), None)
    if last is None or last["type"] != "step.failed" or not last["payload"].get("until"):
        return None
    b = await tasks.brief(conn, task_id)
    if not b.chat:
        return None
    request_id = f"limit/{last['payload']['until']}"
    if any(r["payload"].get("request_id") == request_id for r in rows if r["type"].startswith("effect.")):
        return None
    said = await notices.unanswered(conn, task_id, rows)
    if said is None:
        return None
    reset = datetime.fromisoformat(last["payload"]["until"])
    operator = (said.get("provenance") or {}).get("by") == "tom"
    if operator:
        from core import session

        zone = session._zone()
    else:
        zone = ZoneInfo(last["payload"]["zone"])
    inside = operator or await _internal(conn, b, said.get("sender_name"))
    text = (INTERNAL if inside else EXTERNAL).format(when=when(reset, zone, now or datetime.now(UTC)))
    action = await _action(conn, b.chat, said, text)
    if action is None:
        return None
    out = await broker.request(conn, performers, task_id, action, request_id=request_id, quiet=True)
    return out.effect_id


async def _internal(conn, b, name: str | None) -> bool:
    """Whether the record marks the sender, by exact name or alias, as one
    of Tom's team (`memory.people`); not when it cannot be read."""
    from core import memory, workspace
    from memory.facts import fold

    if not name or not b.project:
        return False
    try:
        spec = workspace.Spec.load(b.project["name"])
        found = await memory.people(conn, spec, name)
    except memory.Unavailable, workspace.Refused, FileNotFoundError:
        return False
    return any(
        p["team"] == "internal" and fold(name) in {fold(n) for n in [p["name"], *p["aliases"]]}
        for p in found["people"]
    )


async def _action(conn, chat: dict[str, str], said: dict[str, Any], text: str) -> broker.Action | None:
    """The chat's own send, a reply to the waiting message."""
    channel, chat_id = chat["channel"], chat["chat_id"]
    if channel == "email":
        from core import session

        action, error = await session._reply_all(
            conn, broker.Action("email.send", "", {"reply_to": said.get("received_id"), "body": text})
        )
        return None if error else action
    if channel == "local":
        return broker.Action("local.send_message", "local", {"text": text})
    topic = await _topic(conn, said.get("received_id"))
    if channel == "telegram":
        return broker.Action(
            "telegram.send_message",
            chat_id,
            {"text": text, "reply_to": said.get("message_id"), "topic_id": topic},
        )
    if channel == "slack":
        return broker.Action(
            "slack.send_message", chat_id, {"text": text, "reply_to": topic or said.get("message_id")}
        )
    return None


async def _topic(conn, received_id: str | None) -> str | None:
    """The waiting message's topic (Telegram) or thread (Slack), as received."""
    if not received_id:
        return None
    row = await (
        await conn.execute(
            "SELECT payload->>'topic_id' FROM events WHERE type = 'message.received' "
            "AND payload->>'received_id' = %s LIMIT 1",
            (received_id,),
        )
    ).fetchone()
    return row[0] if row else None


def sent(rows: list[dict[str, Any]]) -> str:
    """The line a resumed chat turn reads: what the chat was told while the
    limit held, since its last collected turn; empty when nothing was."""
    told = []
    for r in reversed(rows):
        if r["type"] == "turn.collected" and r["payload"].get("verdict") != "failed":
            break
        p = r["payload"]
        if r["type"] == "effect.held" and str(p.get("request_id") or "").startswith("limit/"):
            body = p["payload"].get("text") or p["payload"].get("body")
            told.append(f'"{body}"')
    if not told:
        return ""
    return (
        "# While your model usage limit held\n\nThe kernel sent this chat, as you, before this turn: "
        + "; ".join(reversed(told))
        + ". It is lifted now: answer what is waiting, without sending that notice again."
    )
