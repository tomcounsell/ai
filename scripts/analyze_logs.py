#!/usr/bin/env python3
"""Analyze bridge logs and events for debugging."""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
EVENTS_FILE = LOG_DIR / "bridge.events.jsonl"


def load_events(limit: int = 100) -> list[dict]:
    """Load recent events from the events log."""
    if not EVENTS_FILE.exists():
        return []

    events = []
    with open(EVENTS_FILE) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue

    return events[-limit:]


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to readable string."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def analyze_recent(limit: int = 20):
    """Show recent events with analysis."""
    events = load_events(limit)

    if not events:
        print("No events found. Events will be logged after bridge restart.")
        return

    print(f"\n=== Last {len(events)} Events ===\n")

    # Group by request_id for correlation
    requests = defaultdict(list)
    for event in events:
        rid = event.get("request_id", event.get("message_id", "unknown"))
        requests[rid].append(event)

    # Show each request flow
    for rid, req_events in requests.items():
        first = req_events[0]
        ts = format_timestamp(first.get("timestamp", 0))

        # Summarize the request
        if first["type"] == "message_received":
            sender = first.get("sender", "?")
            project = first.get("project", "?")
            print(f"[{ts}] Message from {sender} ({project})")

            for evt in req_events:
                if evt["type"] == "agent_request":
                    print(f"  → Agent called (session: {evt.get('session_id', '?')[:20]}...)")
                elif evt["type"] == "agent_response":
                    elapsed = evt.get("elapsed_seconds", 0)
                    length = evt.get("response_length", 0)
                    print(f"  ✓ Response in {elapsed:.1f}s ({length} chars)")
                elif evt["type"] == "agent_timeout":
                    elapsed = evt.get("elapsed_seconds", 0)
                    print(f"  ✗ TIMEOUT after {elapsed:.1f}s")
                elif evt["type"] == "agent_error":
                    code = evt.get("exit_code", "?")
                    print(f"  ✗ ERROR (exit {code}): {evt.get('stderr_preview', '')[:50]}")
                elif evt["type"] == "reply_sent":
                    print(f"  ✓ Reply sent ({evt.get('response_length', 0)} chars)")
            print()

        elif first["type"] == "agent_request":
            # Orphaned agent request (no message_received)
            print(f"[{ts}] Agent request {rid[:30]}...")
            for evt in req_events:
                if evt["type"] == "agent_response":
                    elapsed = evt.get("elapsed_seconds", 0)
                    print(f"  ✓ Response in {elapsed:.1f}s")
                elif evt["type"] == "agent_timeout":
                    print(f"  ✗ TIMEOUT")
            print()


def show_timeouts():
    """Show all timeout events."""
    events = load_events(500)
    timeouts = [e for e in events if e.get("type") == "agent_timeout"]

    if not timeouts:
        print("No timeouts recorded.")
        return

    print(f"\n=== {len(timeouts)} Timeout Events ===\n")
    for evt in timeouts:
        ts = format_timestamp(evt.get("timestamp", 0))
        session = evt.get("session_id", "?")
        elapsed = evt.get("elapsed_seconds", 0)
        print(f"[{ts}] Session {session[:30]}... - timed out after {elapsed:.1f}s")


def show_stats():
    """Show statistics from events."""
    events = load_events(1000)

    if not events:
        print("No events to analyze.")
        return

    # Count event types
    type_counts = defaultdict(int)
    response_times = []

    for evt in events:
        type_counts[evt.get("type", "unknown")] += 1
        if evt.get("type") == "agent_response":
            response_times.append(evt.get("elapsed_seconds", 0))

    print("\n=== Event Statistics ===\n")
    for evt_type, count in sorted(type_counts.items()):
        print(f"  {evt_type}: {count}")

    if response_times:
        avg_time = sum(response_times) / len(response_times)
        max_time = max(response_times)
        min_time = min(response_times)
        print(f"\n=== Response Times ===")
        print(f"  Average: {avg_time:.1f}s")
        print(f"  Min: {min_time:.1f}s")
        print(f"  Max: {max_time:.1f}s")
        print(f"  Samples: {len(response_times)}")


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
    else:
        print("Usage: analyze_logs.py [recent|timeouts|stats] [limit]")


if __name__ == "__main__":
    main()
