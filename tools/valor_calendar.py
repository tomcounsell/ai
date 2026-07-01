"""valor-calendar: Log work sessions as Google Calendar events.

Usage: valor-calendar [--project PROJECT] [--reauth] [--check] <session-slug>

Routes to the correct Google Calendar by project name using
config/calendar_config.json. Falls back to "default" calendar
when no project is specified or no mapping exists.
Creates or extends events using 20-minute minimum blocks, 10-minute increments.
Falls back to offline queue on auth failure.

Flags:
    --reauth    Clear stored tokens and re-run OAuth consent flow
    --check     Validate token health and print status (exit 0=valid, 1=invalid)
    --version   Print version and exit
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bridge.utc import utc_now
from config.paths import DATA_DIR

# Calendar config lives in ~/Desktop/Valor/, queue/cache in data/
CALENDAR_CONFIG_PATH = Path.home() / "Desktop" / "Valor" / "calendar_config.json"

QUEUE_PATH = DATA_DIR / "calendar_queue.jsonl"
EVENT_ID_CACHE_PATH = DATA_DIR / "calendar_event_ids.json"
# feature_key:date -> client-facing display name (generated once, reused all day)
NAME_CACHE_PATH = DATA_DIR / "calendar_feature_names.json"
# feature_key:date -> last fire epoch, to rate-limit API churn within a session
STAMP_CACHE_PATH = DATA_DIR / "calendar_fire_stamps.json"
# Don't re-touch the same feature's event more than once per this many seconds.
_HOOK_RATE_LIMIT_SECONDS = 600


def load_calendar_config() -> dict:
    """Load calendar project-to-ID mapping from config file."""
    if not CALENDAR_CONFIG_PATH.exists():
        return {"calendars": {"default": "primary"}}
    return json.loads(CALENDAR_CONFIG_PATH.read_text())


def get_calendar_id(project: str | None, config: dict) -> str | None:
    """Resolve a project name to a Google Calendar ID.

    Returns None if the project has no calendar mapping — callers should
    skip event creation rather than falling back to a default calendar.
    """
    calendars = config.get("calendars", {})
    if project:
        return calendars.get(project)
    return None


_SEGMENT_MINUTES = 10
_MIN_BLOCK_MINUTES = 20

# Branch prefixes that are scaffolding, not part of the feature name.
_BRANCH_PREFIXES = ("session/", "feature/", "feat/", "fix/", "bugfix/", "chore/", "hotfix/")

# Branch names that carry no feature signal (work on the trunk / detached HEAD).
_TRUNK_BRANCHES = {"main", "master", "trunk", "develop", "head", ""}

# Technical jargon stripped from client-facing event names. The calendar is
# client-visible: a non-technical reader should see the *value* being built,
# not the plumbing. These tokens describe how the work is done (process,
# tooling, mechanics), never what feature it delivers.
_TECH_JARGON = frozenset(
    {
        "sdlc",
        "prompt",
        "parallel",
        "hook",
        "hooks",
        "redis",
        "pr",
        "prs",
        "ci",
        "cd",
        "impl",
        "wip",
        "refactor",
        "refactored",
        "refactoring",
        "debug",
        "debugging",
        "merge",
        "rebase",
        "commit",
        "branch",
        "worktree",
        "lint",
        "ruff",
        "pytest",
        "test",
        "tests",
        "testing",
        "patch",
        "diff",
        "stub",
        "mock",
        "regex",
        "json",
        "yaml",
        "api",
        "cli",
        "sdk",
        "mcp",
        "popoto",
        "venv",
        "env",
        "config",
        "cache",
        "queue",
        "async",
        "thread",
        "subagent",
        "subagents",
        "executor",
        "bridge",
        "worker",
        "review",
        "critique",
        "investigation",
        "investigate",
        "audit",
        "fixup",
        "cleanup",
        "setup",
        "checkpoint",
        "validation",
        "validate",
        "execution",
        "execute",
        "issue",
        "issues",
        "ticket",
        "bug",
        "bugfix",
        "task",
        "wip",
    }
)

# Trivial prompts that should never produce a calendar event or slug call.
_TRIVIAL_PROMPTS = frozenset(
    {
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "y",
        "n",
        "go",
        "do it",
        "doit",
        "continue",
        "continue.",
        "next",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "please",
        "sure",
        "stop",
        "wait",
        "hi",
        "hello",
        "hey",
        "yep",
        "nope",
        "good",
        "great",
        "perfect",
        "nice",
        "cool",
        "done",
        "ack",
        "proceed",
    }
)

# A prompt must clear this length (after stripping) before it is worth a slug.
_MIN_PROMPT_CHARS = 12


def is_trivial_prompt(prompt: str) -> bool:
    """True when a prompt carries no work signal worth a calendar event.

    Gates out acknowledgements ("thanks", "continue"), bare confirmations, and
    very short messages *before* any slug generation runs. This is what stops
    noise slugs like ``do-it`` / ``thanks-acknowledgment`` from ever reaching
    the calendar.
    """
    normalized = " ".join(prompt.strip().lower().split())
    if not normalized:
        return True
    if normalized in _TRIVIAL_PROMPTS:
        return True
    return len(normalized) < _MIN_PROMPT_CHARS


def git_branch(cwd: str | None) -> str | None:
    """Return the current git branch for ``cwd``, or None if unavailable.

    Used as the primary coalescing signal: all work on one branch is one
    feature, so every prompt and every subagent on that branch rolls into a
    single calendar event instead of a pile of per-prompt stubs.
    """
    import subprocess

    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def derive_feature_key(
    cwd: str | None,
    env: dict | None = None,
    branch: str | None = None,
) -> str | None:
    """Resolve a STABLE coalescing key for the current unit of work.

    This is the event's identity — not its display name. Everything sharing a
    feature key (consecutive prompts, parallel subagents, every SDLC stage)
    coalesces into one calendar event.

    Priority:
        1. git branch (minus scaffolding prefix), when not on the trunk
        2. a slug-scoped task list id (tier-2 planned work; not ``thread-*``)
        3. None — no feature signal (caller must NOT emit a project-name stub)
    """
    mapping = env if env is not None else {}

    branch = branch if branch is not None else git_branch(cwd)
    if branch and branch.lower() not in _TRUNK_BRANCHES:
        slug = branch
        for prefix in _BRANCH_PREFIXES:
            if slug.startswith(prefix):
                slug = slug[len(prefix) :]
                break
        slug = slug.strip("/").replace("/", "-")
        if slug and slug.lower() not in _TRUNK_BRANCHES:
            return slug

    task_list_id = (mapping.get("CLAUDE_CODE_TASK_LIST_ID") or "").strip()
    if task_list_id and not task_list_id.startswith("thread-"):
        return task_list_id

    return None


def clean_feature_name(raw: str) -> str:
    """Reduce a raw slug/branch/prompt into a client-facing feature name.

    Strips technical jargon (``sdlc``, ``parallel``, ``test`` …) and pure
    issue-number tokens, leaving the product/value words. Always returns a
    non-empty kebab-case string: if stripping would empty it, the original
    cleaned token sequence is kept so the event is still named.
    """
    import re

    tokens = [t for t in re.split(r"[^a-z0-9]+", raw.lower()) if t]
    kept = [t for t in tokens if t not in _TECH_JARGON and not t.isdigit()]
    chosen = kept or [t for t in tokens if not t.isdigit()] or tokens
    return "-".join(chosen)[:60].strip("-")


def round_down_10(dt: datetime) -> datetime:
    """Round a datetime DOWN to the nearest 10-minute boundary."""
    minute = (dt.minute // _SEGMENT_MINUTES) * _SEGMENT_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def round_up_10(dt: datetime) -> datetime:
    """Round a datetime UP to the nearest 10-minute boundary.

    Returns the same time if already on a 10-minute boundary with second=0.
    Handles hour rollover (e.g. 10:59 -> 11:00).
    """
    if dt.second == 0 and dt.minute % _SEGMENT_MINUTES == 0:
        return dt.replace(second=0, microsecond=0)
    minute = ((dt.minute // _SEGMENT_MINUTES) + 1) * _SEGMENT_MINUTES
    if minute >= 60:
        return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return dt.replace(minute=minute, second=0, microsecond=0)


def current_segment(now: datetime) -> tuple[datetime, datetime]:
    """Return segment boundaries: start rounded down to 10-min, end = start + 20 min.

    The end is clamped to the end of ``now``'s day so a session that runs up to
    (or past) midnight can never push an event into the next calendar day.
    """
    start = round_down_10(now)
    end = min(start + timedelta(minutes=_MIN_BLOCK_MINUTES), end_of_day(now))
    return start, end


def start_of_day(now: datetime) -> datetime:
    """Midnight at the start of ``now``'s day (same tz)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(now: datetime) -> datetime:
    """The last representable instant of ``now``'s day (23:59:59, same tz).

    Used as a hard ceiling on event end times: events are day-bounded, so a
    runaway heartbeat can never produce a multi-day block.
    """
    return now.replace(hour=23, minute=59, second=59, microsecond=0)


def _load_event_id_cache() -> dict:
    """Load the slug -> event_id cache."""
    if EVENT_ID_CACHE_PATH.exists():
        try:
            return json.loads(EVENT_ID_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_event_id_cache(cache: dict) -> None:
    """Persist the slug -> event_id cache."""
    EVENT_ID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENT_ID_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _cache_key(slug: str, now: datetime) -> str:
    """Cache key scoped to slug + date."""
    return f"{slug}:{now.strftime('%Y-%m-%d')}"


def _starts_today(event, now: datetime) -> bool:
    """True only if ``event`` starts within ``now``'s calendar day.

    Google's list() returns any event that *overlaps* the day window, so a
    multi-day block created on a prior day would otherwise match and keep
    getting extended. Filtering on start date is what bounds events to a day.
    """
    start_str = event.get("start", {}).get("dateTime")
    if not start_str:
        return False
    try:
        ev_start = parse_event_time(start_str)
    except ValueError:
        return False
    return start_of_day(now) <= ev_start < end_of_day(now) + timedelta(seconds=1)


def find_todays_event(
    service, calendar_id: str, feature_key: str, display_name: str, now: datetime
):
    """Find today's event for ``feature_key``, by cached ID first, then summary.

    Only events that *started today* are eligible — a cached or summary-matched
    event from a previous day is ignored (and pruned from the cache) so events
    stay strictly day-bounded.
    """
    # Try cached event ID first (survives renames)
    cache = _load_event_id_cache()
    key = _cache_key(feature_key, now)
    cached_id = cache.get(key)
    if cached_id:
        try:
            event = service.events().get(calendarId=calendar_id, eventId=cached_id).execute()
            if event and event.get("status") != "cancelled" and _starts_today(event, now):
                return event
        except Exception:
            # Event was deleted or ID is stale; fall through to search
            pass

    # Fallback: search by display name, restricted to today's window
    events_result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start_of_day(now).isoformat(),
            timeMax=(end_of_day(now) + timedelta(seconds=1)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            q=display_name,
        )
        .execute()
    )

    for event in events_result.get("items", []):
        if event.get("summary") == display_name and _starts_today(event, now):
            # Update cache with found event ID
            cache[key] = event["id"]
            _save_event_id_cache(cache)
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


def queue_entry(
    feature_key: str,
    display_name: str,
    now: datetime,
    project: str | None = None,
) -> None:
    """Append a failed request to the offline queue."""
    entry = {
        "timestamp": now.isoformat(),
        "feature_key": feature_key,
        "display_name": display_name,
        "project": project,
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

        # Support both the current (feature_key/display_name) and legacy (slug)
        # entry shapes so a queue written by an older build still replays.
        feature_key = entry.get("feature_key") or entry.get("slug")
        display_name = entry.get("display_name") or feature_key
        project = entry.get("project")
        if not feature_key:
            skipped += 1
            continue
        calendar_id = get_calendar_id(project, config)
        if not calendar_id:
            skipped += 1
            continue
        process_calendar_event(service, calendar_id, feature_key, display_name, entry_time)
        replayed += 1

    # Clear the queue after replay
    QUEUE_PATH.unlink()

    if skipped:
        print(f"Skipped {skipped} stale queue entries (>24h old)")

    return replayed


def process_calendar_event(
    service,
    calendar_id: str,
    feature_key: str,
    display_name: str,
    now: datetime,
) -> str:
    """Find or create/extend today's event for ``feature_key``.

    The event is identified by ``feature_key`` (stable across prompts and
    subagents, so all work on one feature coalesces into a single event) and
    titled with the client-facing ``display_name``. The end never crosses the
    day boundary (``current_segment`` clamps to end-of-day).
    """
    seg_start, seg_end = current_segment(now)
    event = find_todays_event(service, calendar_id, feature_key, display_name, now)

    if event is None:
        new_event = create_event(service, calendar_id, display_name, seg_start, seg_end)
        # Cache the event ID keyed by the stable feature key (survives renames)
        if new_event and "id" in new_event:
            cache = _load_event_id_cache()
            cache[_cache_key(feature_key, now)] = new_event["id"]
            _save_event_id_cache(cache)
        return (
            f"Created event '{display_name}' "
            f"{seg_start.strftime('%H:%M')}-{seg_end.strftime('%H:%M')}"
        )

    event_end = parse_event_time(event["end"]["dateTime"])

    if event_end >= seg_end:
        return (
            f"Event '{display_name}' already covers current segment "
            f"(ends {event_end.strftime('%H:%M')})"
        )

    extend_event(service, calendar_id, event, seg_end)
    event_start = parse_event_time(event["start"]["dateTime"])
    return (
        f"Extended event '{display_name}' to "
        f"{event_start.strftime('%H:%M')}-{seg_end.strftime('%H:%M')}"
    )


def _load_json_cache(path: Path) -> dict:
    """Load a small JSON dict cache, tolerating missing/corrupt files."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_json_cache(path: Path, cache: dict) -> None:
    """Persist a small JSON dict cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2))


_HAIKU_NAME_INSTRUCTION = (
    "You name calendar events for a CLIENT-FACING work log. Rewrite the developer's "
    "task below as a short feature name a NON-TECHNICAL client would recognize as "
    "valuable work on their product. Output ONLY a 2-4 word kebab-case slug, nothing "
    "else. Describe the FEATURE/OUTCOME, never the mechanics: no words like sdlc, "
    "prompt, parallel, hook, refactor, test, merge, PR, bug, debug, pipeline. "
    "Examples: appointment-reminders, member-export, faster-checkout, "
    "privacy-policy-page."
)


def _haiku_feature_name(prompt: str) -> str | None:
    """Best-effort client-facing feature name via Haiku. None on any failure.

    Bounded by a short client timeout so it can never stall the hook; every
    failure mode (no key, network, bad output) falls back to the deterministic
    ``clean_feature_name`` path in the caller.
    """
    try:
        import anthropic

        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key, timeout=6.0)
        resp = client.messages.create(
            model=MODEL_FAST,
            max_tokens=30,
            messages=[{"role": "user", "content": f"{_HAIKU_NAME_INSTRUCTION}\n\nTask: {prompt}"}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        # Run the model output back through the jargon filter as a guardrail.
        return clean_feature_name(text) or None
    except Exception:
        return None


def resolve_display_name(feature_key: str, prompt: str, now: datetime, *, from_prompt: bool) -> str:
    """Return a stable client-facing event name for ``feature_key``.

    Generated once per feature/day and cached, so every later prompt and
    subagent reuses the same name (the event title stays stable as it grows).

    - Tracked work (branch / task-list feature key): deterministic, jargon-
      stripped name from the key itself — no network call.
    - Ad-hoc work (``from_prompt``): a Haiku-generated client-facing name from
      the seeding prompt, with a deterministic fallback.
    """
    cache = _load_json_cache(NAME_CACHE_PATH)
    key = _cache_key(feature_key, now)
    cached = cache.get(key)
    if cached:
        return cached

    name = None
    if from_prompt and prompt:
        name = _haiku_feature_name(prompt)
    if not name:
        source = prompt if from_prompt else feature_key
        name = clean_feature_name(source) or clean_feature_name(feature_key) or feature_key

    cache[key] = name
    _save_json_cache(NAME_CACHE_PATH, cache)
    return name


def _hook_rate_limited(feature_key: str, now: datetime) -> bool:
    """True if this feature's event was already touched within the rate window.

    The first fire for a feature on a given day is never limited; subsequent
    fires within ``_HOOK_RATE_LIMIT_SECONDS`` are skipped to bound API churn.
    """
    cache = _load_json_cache(STAMP_CACHE_PATH)
    key = _cache_key(feature_key, now)
    last = cache.get(key)
    if last is not None:
        try:
            if (now.timestamp() - float(last)) < _HOOK_RATE_LIMIT_SECONDS:
                return True
        except (TypeError, ValueError):
            pass
    cache[key] = now.timestamp()
    _save_json_cache(STAMP_CACHE_PATH, cache)
    return False


def run_hook(event_kind: str) -> None:
    """Handle --hook mode: read Claude Code hook JSON from stdin and log work.

    ``event_kind`` is "prompt" (UserPromptSubmit) or "stop" (Stop heartbeat).
    Always exits 0 and never raises — a calendar hook must never disrupt the
    session. This is the single Python entry that both shell hooks defer to.
    """
    # A hook runs on a tight budget; cap every Google round-trip hard.
    os.environ.setdefault("GWS_HTTP_TIMEOUT", "8")

    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    prompt = (data.get("prompt") or "").strip()
    cwd = data.get("cwd") or os.getcwd()
    env = os.environ

    # Acknowledgements / bare confirmations never warrant an event.
    if event_kind == "prompt" and is_trivial_prompt(prompt):
        return

    config = load_calendar_config()

    from config.project_key_resolver import resolve_project_key

    project = resolve_project_key(cwd=cwd, env=dict(env))
    if not project:
        return
    project = project.lower()
    calendar_id = get_calendar_id(project, config)
    if not calendar_id:
        return  # project not on the calendar allowlist

    # Stable coalescing key: branch/task-list for tracked work, else the
    # project (so a day's ad-hoc work coalesces instead of fragmenting). We
    # never title an event with the bare project key.
    feature_key = derive_feature_key(cwd, env=dict(env))
    from_prompt = feature_key is None
    if feature_key is None:
        feature_key = project

    now = utc_now()

    # The heartbeat can only extend an event that a prompt already named. For
    # ad-hoc work with no day-name yet, there is nothing to extend — skip.
    if from_prompt and not prompt:
        if not _load_json_cache(NAME_CACHE_PATH).get(_cache_key(feature_key, now)):
            return

    display_name = resolve_display_name(feature_key, prompt, now, from_prompt=from_prompt)

    if _hook_rate_limited(feature_key, now):
        return

    try:
        from tools.google_workspace.auth import get_service

        service = get_service("calendar", "v3")
        replay_queue(service, config, now)
        process_calendar_event(service, calendar_id, feature_key, display_name, now)
    except Exception as e:
        queue_entry(feature_key, display_name, now, project)
        print(f"calendar hook queued locally: {e}", file=sys.stderr)


def _handle_check() -> None:
    """Handle --check flag: validate token health and print status."""
    from tools.google_workspace.auth import verify_token

    result = verify_token()
    status = result["status"]

    if result["valid"]:
        print(f"Token status: {status}")
        if result["scopes"]:
            print(f"Scopes: {', '.join(result['scopes'])}")
        elif result["scopes"] is None:
            print("Scopes: unknown (reauth recommended if scope expansion needed)")
        if result["expired"] and result["has_refresh_token"]:
            print("Note: Token expired but has refresh token (will auto-refresh)")
        sys.exit(0)
    else:
        print(f"Token status: {status}")
        if status == "missing":
            print("No token file found. Run: valor-calendar --reauth")
        elif status == "invalid":
            print("Token file is corrupted or unreadable. Run: valor-calendar --reauth")
        elif status == "expired":
            print("Token expired with no refresh token. Run: valor-calendar --reauth")
        elif status == "scope_mismatch":
            granted = result.get("scopes", [])
            print(f"Granted scopes: {', '.join(granted) if granted else 'none'}")
            print("Required scopes not granted. Run: valor-calendar --reauth")
        elif status == "scopes_unknown":
            print("Cannot verify scopes. Run: valor-calendar --reauth")
        sys.exit(1)


def _handle_reauth() -> None:
    """Handle --reauth flag: clear tokens and re-run OAuth consent."""
    from tools.google_workspace.auth import clear_tokens, get_credentials

    print("Clearing stored tokens...")
    clear_tokens()

    print("Starting OAuth consent flow (browser will open)...")
    try:
        get_credentials()
        print("Re-authentication successful. Token saved.")
    except Exception as e:
        print(f"Re-authentication failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print("valor-calendar 0.2.0")
        return

    args = sys.argv[1:]

    # Handle --check and --reauth before other argument parsing
    if "--check" in args:
        _handle_check()
        return

    if "--reauth" in args:
        _handle_reauth()
        return

    # Hook mode: read hook JSON from stdin. Both shell hooks defer here.
    if "--hook" in args:
        event_kind = "prompt"
        if "--event" in args:
            idx = args.index("--event")
            if idx + 1 < len(args):
                event_kind = args[idx + 1].lower()
        try:
            run_hook(event_kind)
        except Exception as e:  # hooks must never disrupt the session
            print(f"calendar hook error (ignored): {e}", file=sys.stderr)
        return

    project = None

    # Parse --project flag
    if "--project" in args:
        idx = args.index("--project")
        if idx + 1 < len(args):
            project = args[idx + 1].lower()
            args = args[:idx] + args[idx + 2 :]

    if not args:
        print(
            "Usage: valor-calendar [--project PROJECT] [--reauth] [--check] <session-slug>\n"
            "       valor-calendar --hook --event prompt|stop   (reads hook JSON from stdin)"
        )
        sys.exit(1)

    # Manual / replay invocation: the positional argument is shown verbatim as
    # the event title and used as its own coalescing key.
    feature_key = args[0]
    display_name = args[0]
    now = utc_now()
    config = load_calendar_config()
    calendar_id = get_calendar_id(project, config)
    if not calendar_id:
        print(f"No calendar mapping for project '{project}'. Skipping.")
        sys.exit(0)

    try:
        from tools.google_workspace.auth import GoogleAuthError, get_service

        service = get_service("calendar", "v3")

        # Replay any queued entries first
        replayed = replay_queue(service, config, now)
        if replayed:
            print(f"Replayed {replayed} queued entries")

        # Process current request
        result = process_calendar_event(service, calendar_id, feature_key, display_name, now)
        print(result)

    except GoogleAuthError as e:
        queue_entry(feature_key, display_name, now, project)
        print(f"Auth error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        queue_entry(feature_key, display_name, now, project)
        print(f"Queued locally (auth/network issue): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
