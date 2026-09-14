"""Cross-session view of persona-toolbelt enforce-state skew (plan #3081, Race 3).

``TOOLBELTS_ENFORCE`` propagates per machine via git sync + ``/update``, not
atomically. Mid-window the same session can resolve a different tool surface
depending on which host takes its next turn, so
``agent/session_runner/belt_resolver.py`` compares the prior-turn stamp against
the host's resolved state at turn start and records a ``belt_enforce_skew``
event when they disagree.

Those events land in per-session JSONL files.
``agent.session_telemetry.read_session_timeline`` reads one session at a time,
which is the wrong shape for the question an operator actually asks during an
activation window: *is the fleet converged yet, and which host is behind?* This
tool answers that by globbing every session file at once.

Run it during the activation window (plan task 4). A converged fleet reports
zero skew events, and that zero is stated explicitly rather than printed as
blank output.

Exit codes (stable):
    0 — report produced, including the no-skew-found empty state
    1 — the telemetry directory could not be read
    2 — usage error: a bad flag value, whether argparse rejected it or
        ``--since`` failed its own validation
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.session_telemetry import BELT_ENFORCE_SKEW_EVENT, _get_telemetry_dir

EXIT_OK = 0
EXIT_UNREADABLE = 1
#: Caller's mistake (a malformed flag value), distinct from an unreadable
#: stream — argparse returns this for the errors it catches itself, and the
#: hand-validated ``--since`` below must agree with it.
EXIT_USAGE = 2

#: Rows shown before ``--full`` is needed.
_COMPACT_ROW_LIMIT = 20


def _parse_ts(raw: object) -> datetime | None:
    """Parse a telemetry ``ts`` (ISO 8601 ending in 'Z'). None when unusable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_since(spec: str) -> timedelta | None:
    """Parse ``30d`` / ``12h`` / ``90m`` into a timedelta. None when unparseable."""
    spec = (spec or "").strip().lower()
    if not spec:
        return None
    units = {"d": "days", "h": "hours", "m": "minutes"}
    if spec[-1] not in units:
        return None
    try:
        return timedelta(**{units[spec[-1]]: float(spec[:-1])})
    except ValueError:
        return None


def collect_skew_events(telemetry_dir: Path, cutoff: datetime | None = None) -> list[dict]:
    """Return every ``belt_enforce_skew`` event across all session files.

    Sessions are independent files, so one unreadable or half-written file must
    not hide the rest of the fleet — each is read defensively and skipped on
    error. Malformed JSONL lines are skipped the same way
    ``read_session_timeline`` skips them.
    """
    events: list[dict] = []
    for path in sorted(telemetry_dir.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or BELT_ENFORCE_SKEW_EVENT not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") != BELT_ENFORCE_SKEW_EVENT:
                        continue
                    event.setdefault("session_id", path.stem)
                    if cutoff is not None:
                        ts = _parse_ts(event.get("ts"))
                        if ts is not None and ts < cutoff:
                            continue
                    events.append(event)
        except OSError:
            continue
    return events


def summarize(events: list[dict]) -> dict:
    """Pre-compute every aggregate the report shows, so one call returns
    assembled state rather than raw rows the caller has to fold itself."""
    by_session: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "hosts": Counter(), "last_ts": None, "transitions": Counter()}
    )
    by_host: Counter[str] = Counter()
    transitions: Counter[str] = Counter()

    for event in events:
        session_id = str(event.get("session_id") or "<unknown>")
        host = str(event.get("host") or "<unknown>")
        transition = (
            f"{event.get('prior_enforce_state', '?')}->{event.get('current_enforce_state', '?')}"
        )
        row = by_session[session_id]
        row["count"] += 1
        row["hosts"][host] += 1
        row["transitions"][transition] += 1
        ts = event.get("ts")
        if isinstance(ts, str) and (row["last_ts"] is None or ts > row["last_ts"]):
            row["last_ts"] = ts
        by_host[host] += 1
        transitions[transition] += 1

    return {
        "total_events": len(events),
        "sessions_affected": len(by_session),
        "hosts_affected": len(by_host),
        "by_host": dict(by_host.most_common()),
        "transitions": dict(transitions.most_common()),
        "by_session": {
            sid: {
                "count": row["count"],
                "hosts": dict(row["hosts"].most_common()),
                "transitions": dict(row["transitions"].most_common()),
                "last_ts": row["last_ts"],
            }
            for sid, row in sorted(by_session.items(), key=lambda kv: -kv[1]["count"])
        },
    }


def _render(summary: dict, telemetry_dir: Path, full: bool, since: str | None) -> str:
    window = f" in the last {since}" if since else ""
    lines = [f"Belt enforce-state skew — {telemetry_dir}{window}", ""]

    if summary["total_events"] == 0:
        lines += [
            "NO SKEW EVENTS FOUND.",
            "",
            "Every session whose turns were stamped resolved the same "
            "TOOLBELTS_ENFORCE state on every host that ran them, or no session "
            "has been stamped yet (belts ship dark; the resolver only stamps "
            "once a turn runs through it).",
            "",
            "Next step: this is the expected reading before the activation flip. "
            "Re-run it during the flip window (plan #3081 task 4) — a non-zero "
            "count there names the hosts still on the old state.",
        ]
        return "\n".join(lines)

    lines += [
        f"  events            {summary['total_events']}",
        f"  sessions affected {summary['sessions_affected']}",
        f"  hosts affected    {summary['hosts_affected']}",
        "",
        "By host:",
    ]
    lines += [f"  {host:<28} {count}" for host, count in summary["by_host"].items()]
    lines += ["", "By transition:"]
    lines += [f"  {t:<28} {count}" for t, count in summary["transitions"].items()]

    rows = list(summary["by_session"].items())
    shown = rows if full else rows[:_COMPACT_ROW_LIMIT]
    lines += ["", f"Sessions ({len(shown)} of {len(rows)}):"]
    for sid, row in shown:
        hosts = ",".join(row["hosts"])
        lines.append(f"  {sid:<40} {row['count']:>4}  {hosts:<24} {row['last_ts'] or '-'}")
    if len(shown) < len(rows):
        lines.append(f"  … {len(rows) - len(shown)} more — pass --full to see every session.")

    lines += [
        "",
        "Next step: skew during an activation window is expected and transient. "
        "Prompt an `/update` on the hosts listed above; if a host keeps "
        "reporting the old state after updating, check its TOOLBELTS_ENFORCE "
        "env override (break-glass rollback only).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.belt_skew_report",
        description=(
            "Aggregate persona-toolbelt enforce-state skew across every session "
            "telemetry file. The cross-session view read_session_timeline "
            "(one session at a time) cannot provide."
        ),
        epilog="Exit codes: 0 report produced (empty state included), 1 telemetry unreadable.",
    )
    parser.add_argument(
        "--telemetry-dir",
        type=Path,
        default=None,
        help="Session telemetry directory (default: the repo's logs/session_telemetry).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only count events newer than this window, e.g. 30d / 12h / 90m.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=f"List every affected session instead of the top {_COMPACT_ROW_LIMIT}.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    args = parser.parse_args(argv)

    cutoff = None
    if args.since:
        delta = _parse_since(args.since)
        if delta is None:
            print(
                json.dumps({"error": "bad_since", "value": args.since, "expected": "30d|12h|90m"}),
                file=sys.stderr,
            )
            return EXIT_USAGE
        cutoff = datetime.now(UTC) - delta

    telemetry_dir = args.telemetry_dir or _get_telemetry_dir()
    if not telemetry_dir.is_dir():
        print(
            json.dumps({"error": "telemetry_dir_missing", "path": str(telemetry_dir)}),
            file=sys.stderr,
        )
        return EXIT_UNREADABLE

    summary = summarize(collect_skew_events(telemetry_dir, cutoff))
    if args.json:
        print(json.dumps({"telemetry_dir": str(telemetry_dir), **summary}, indent=2))
    else:
        print(_render(summary, telemetry_dir, args.full, args.since))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
