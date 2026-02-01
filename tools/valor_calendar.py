"""valor-calendar: Log work sessions as Google Calendar events.

Usage: valor-calendar <session-slug>

Maps slugs to calendar IDs via ~/Desktop/claude_code/calendar_config.json.
Creates or extends events using 30-minute segment rounding.
Falls back to offline queue on auth failure.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_DIR = Path.home() / "Desktop" / "claude_code"
CALENDAR_CONFIG_PATH = CONFIG_DIR / "calendar_config.json"
QUEUE_PATH = CONFIG_DIR / "calendar_queue.jsonl"


def load_calendar_config() -> dict:
    """Load calendar slug-to-ID mapping from config file."""
    if not CALENDAR_CONFIG_PATH.exists():
        return {"calendars": {"default": "primary"}}
    return json.loads(CALENDAR_CONFIG_PATH.read_text())


def get_calendar_id(slug: str, config: dict) -> str:
    """Resolve a session slug to a Google Calendar ID."""
    calendars = config.get("calendars", {})
    return calendars.get(slug, calendars.get("default", "primary"))


def round_down_30(dt: datetime) -> datetime:
    """Round a datetime DOWN to the nearest 30-minute boundary."""
    minute = (dt.minute // 30) * 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def round_up_30(dt: datetime) -> datetime:
    """Round a datetime UP to the nearest 30-minute boundary."""
    if dt.minute == 0 and dt.second == 0:
        return dt.replace(second=0, microsecond=0)
    if dt.minute <= 30:
        return dt.replace(minute=30, second=0, microsecond=0)
    return (dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))


def current_segment(now: datetime) -> tuple[datetime, datetime]:
    """Return the 30-minute segment boundaries containing `now`."""
    start = round_down_30(now)
    end = start + timedelta(minutes=30)
    return start, end


def find_todays_event(service, calendar_id: str, slug: str, now: datetime):
    """Find an existing event for today matching the slug summary."""
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)

    events_result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            q=slug,
        )
        .execute()
    )

    for event in events_result.get("items", []):
        if event.get("summary") == slug:
            return event
    return None


def parse_event_time(time_str: str) -> datetime:
    """Parse a Google Calendar dateTime string to a datetime."""
    return datetime.fromisoformat(time_str)


def format_dt(dt: datetime) -> str:
    """Format datetime for Google Calendar API."""
    return dt.isoformat()


def create_event(service, calendar_id: str, slug: str, start: datetime, end: datetime):
    """Create a new calendar event."""
    body = {
        "summary": slug,
        "start": {
            "dateTime": format_dt(start),
            "timeZone": str(start.tzinfo) if start.tzinfo else "UTC",
        },
        "end": {
            "dateTime": format_dt(end),
            "timeZone": str(end.tzinfo) if end.tzinfo else "UTC",
        },
    }
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def extend_event(service, calendar_id: str, event, new_end: datetime):
    """Extend an existing event's end time."""
    tz = event["end"].get("timeZone", "UTC")
    event["end"]["dateTime"] = format_dt(new_end)
    event["end"]["timeZone"] = tz
    return (
        service.events()
        .patch(calendarId=calendar_id, eventId=event["id"], body={"end": event["end"]})
        .execute()
    )


def queue_entry(slug: str, now: datetime) -> None:
    """Append a failed request to the offline queue."""
    entry = {
        "timestamp": now.isoformat(),
        "slug": slug,
    }
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def replay_queue(service, config: dict, now: datetime) -> int:
    """Replay queued entries. Returns count of replayed entries."""
    if not QUEUE_PATH.exists():
        return 0

    lines = QUEUE_PATH.read_text().strip().splitlines()
    if not lines:
        return 0

    cutoff = now - timedelta(hours=24)
    replayed = 0
    skipped = 0

    for line in lines:
        entry = json.loads(line)
        entry_time = datetime.fromisoformat(entry["timestamp"])
        if entry_time < cutoff:
            skipped += 1
            continue

        slug = entry["slug"]
        calendar_id = get_calendar_id(slug, config)
        process_calendar_event(service, calendar_id, slug, entry_time)
        replayed += 1

    # Clear the queue after replay
    QUEUE_PATH.unlink()

    if skipped:
        print(f"Skipped {skipped} stale queue entries (>24h old)")

    return replayed


def process_calendar_event(service, calendar_id: str, slug: str, now: datetime) -> str:
    """Core logic: find or create/extend event for the given slug and time."""
    seg_start, seg_end = current_segment(now)
    event = find_todays_event(service, calendar_id, slug, now)

    if event is None:
        create_event(service, calendar_id, slug, seg_start, seg_end)
        return f"Created event '{slug}' {seg_start.strftime('%H:%M')}-{seg_end.strftime('%H:%M')}"

    event_end = parse_event_time(event["end"]["dateTime"])

    if event_end >= seg_end:
        return f"Event '{slug}' already covers current segment (ends {event_end.strftime('%H:%M')})"

    extend_event(service, calendar_id, event, seg_end)
    event_start = parse_event_time(event["start"]["dateTime"])
    return f"Extended event '{slug}' to {event_start.strftime('%H:%M')}-{seg_end.strftime('%H:%M')}"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: valor-calendar <session-slug>")
        sys.exit(1)

    slug = sys.argv[1]
    now = datetime.now().astimezone()
    config = load_calendar_config()
    calendar_id = get_calendar_id(slug, config)

    try:
        from tools.google_workspace.auth import get_service

        service = get_service("calendar", "v3")

        # Replay any queued entries first
        replayed = replay_queue(service, config, now)
        if replayed:
            print(f"Replayed {replayed} queued entries")

        # Process current request
        result = process_calendar_event(service, calendar_id, slug, now)
        print(result)

    except Exception as e:
        queue_entry(slug, now)
        print(f"Queued locally (auth/network issue): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
