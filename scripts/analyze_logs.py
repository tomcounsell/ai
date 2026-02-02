#!/usr/bin/env python3
"""Analyze bridge events stored in Redis via BridgeEvent model."""

import sys
from collections import defaultdict
from datetime import datetime

from models.bridge_event import BridgeEvent


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to readable string."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def load_events(limit: int = 100) -> list[BridgeEvent]:
    """Load recent events from Redis, sorted by timestamp descending."""
    events = BridgeEvent.query.filter()
    if not events:
        return []

    events.sort(key=lambda e: e.timestamp or 0, reverse=True)
    return events[:limit]


def analyze_recent(limit: int = 20):
    """Show recent events with analysis."""
    events = load_events(limit)

    if not events:
        print("No events found.")
        return

    print(f"\n=== Last {len(events)} Events ===\n")

    # Group by request_id for correlation
    requests = defaultdict(list)
    for event in events:
        data = event.data or {}
        rid = data.get("request_id", data.get("message_id", "unknown"))
        requests[rid].append(event)

    # Show each request flow
    for rid, req_events in requests.items():
        first = req_events[0]
        ts = format_timestamp(first.timestamp or 0)

        if first.event_type == "message_received":
            data = first.data or {}
            sender = data.get("sender", "?")
            project = first.project_key or "?"
            print(f"[{ts}] Message from {sender} ({project})")

            for evt in req_events:
                evt_data = evt.data or {}
                if evt.event_type == "agent_request":
                    print(f"  -> Agent called (session: {evt_data.get('session_id', '?')[:20]}...)")
                elif evt.event_type == "agent_response":
                    elapsed = evt_data.get("elapsed_seconds", 0)
                    length = evt_data.get("response_length", 0)
                    print(f"  + Response in {elapsed:.1f}s ({length} chars)")
                elif evt.event_type == "agent_timeout":
                    elapsed = evt_data.get("elapsed_seconds", 0)
                    print(f"  x TIMEOUT after {elapsed:.1f}s")
                elif evt.event_type == "agent_error":
                    code = evt_data.get("exit_code", "?")
                    print(f"  x ERROR (exit {code}): {evt_data.get('stderr_preview', '')[:50]}")
                elif evt.event_type == "reply_sent":
                    print(f"  + Reply sent ({evt_data.get('response_length', 0)} chars)")
            print()

        elif first.event_type == "agent_request":
            print(f"[{ts}] Agent request {str(rid)[:30]}...")
            for evt in req_events:
                evt_data = evt.data or {}
                if evt.event_type == "agent_response":
                    elapsed = evt_data.get("elapsed_seconds", 0)
                    print(f"  + Response in {elapsed:.1f}s")
                elif evt.event_type == "agent_timeout":
                    print("  x TIMEOUT")
            print()


def show_timeouts():
    """Show all timeout events."""
    events = BridgeEvent.query.filter(event_type="agent_timeout")

    if not events:
        print("No timeouts recorded.")
        return

    events.sort(key=lambda e: e.timestamp or 0)
    print(f"\n=== {len(events)} Timeout Events ===\n")
    for evt in events:
        ts = format_timestamp(evt.timestamp or 0)
        data = evt.data or {}
        session = data.get("session_id", "?")
        elapsed = data.get("elapsed_seconds", 0)
        print(f"[{ts}] Session {session[:30]}... - timed out after {elapsed:.1f}s")


def show_stats():
    """Show statistics from events."""
    events = BridgeEvent.query.filter()

    if not events:
        print("No events to analyze.")
        return

    # Count event types
    type_counts = defaultdict(int)
    response_times = []

    for evt in events:
        type_counts[evt.event_type or "unknown"] += 1
        if evt.event_type == "agent_response":
            data = evt.data or {}
            response_times.append(data.get("elapsed_seconds", 0))

    print("\n=== Event Statistics ===\n")
    for evt_type, count in sorted(type_counts.items()):
        print(f"  {evt_type}: {count}")

    if response_times:
        avg_time = sum(response_times) / len(response_times)
        max_time = max(response_times)
        min_time = min(response_times)
        print("\n=== Response Times ===")
        print(f"  Average: {avg_time:.1f}s")
        print(f"  Min: {min_time:.1f}s")
        print(f"  Max: {max_time:.1f}s")
        print(f"  Samples: {len(response_times)}")


def cleanup(days: int = 7):
    """Delete events older than N days."""
    deleted = BridgeEvent.cleanup_old(max_age_seconds=days * 86400)
    print(f"Deleted {deleted} events older than {days} days.")


def main():
    """Main entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "recent"

    if cmd == "recent":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        analyze_recent(limit)
    elif cmd == "timeouts":
        show_timeouts()
    elif cmd == "stats":
        show_stats()
    elif cmd == "cleanup":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        cleanup(days)
    else:
        print("Usage: analyze_logs.py [recent|timeouts|stats|cleanup] [limit|days]")


if __name__ == "__main__":
    main()
