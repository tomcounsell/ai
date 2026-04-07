#!/usr/bin/env python3
"""Session status report -- show current agent session states.

Usage:
    python monitoring/session_status.py              # Show all active sessions
    python monitoring/session_status.py --all        # Include completed sessions
    python monitoring/session_status.py --stalled    # Only show stalled sessions
"""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.0f}m"
    else:
        return f"{seconds / 3600:.1f}h"


def get_session_report(include_completed: bool = False, stalled_only: bool = False) -> str:
    """Generate session status report.

    Args:
        include_completed: If True, include completed/failed sessions.
        stalled_only: If True, only show sessions that exceed stall thresholds.

    Returns:
        Formatted string report of session statuses.
    """
    from models.agent_session import AgentSession
    from monitoring.session_watchdog import STALL_THRESHOLDS

    thresholds = STALL_THRESHOLDS

    now = time.time()
    all_sessions = list(AgentSession.query.all())

    if not include_completed:
        all_sessions = [s for s in all_sessions if s.status not in ("completed", "failed")]

    if not all_sessions:
        return "No active sessions."

    lines = ["SESSION STATUS REPORT", "=" * 60]

    stalled_count = 0
    for s in sorted(all_sessions, key=lambda x: x.created_at or 0, reverse=True):
        status = s.status or "unknown"

        # Determine reference time for duration calculation
        if status == "active":
            # For active sessions, stall is based on last_activity
            transition_time = s.last_activity or s.started_at or s.created_at or now
        else:
            transition_time = s.started_at or s.created_at or now

        duration = now - transition_time
        project = s.project_key or "?"

        # Check if stalled
        threshold = thresholds.get(status)
        is_stalled = threshold is not None and duration > threshold
        if is_stalled:
            stalled_count += 1

        if stalled_only and not is_stalled:
            continue

        stall_marker = " STALLED" if is_stalled else ""

        # Last history entry for context
        history = s._get_history_list() if hasattr(s, "_get_history_list") else []
        last_entry = history[-1] if history else "no history"

        session_id = s.session_id or s.job_id or "unknown"
        lines.append(
            f"{session_id:40s}  {status:10s}  {format_duration(duration):>6s}  "
            f"project={project:8s}  last={str(last_entry)[:50]}{stall_marker}"
        )

    if stalled_only and stalled_count == 0:
        return "No stalled sessions detected."

    lines.append(
        f"\nTotal: {len(all_sessions)} sessions"
        + (f" ({stalled_count} stalled)" if stalled_count > 0 else "")
    )
    return "\n".join(lines)


def session_report():
    """Print current session status report to stdout.

    Convenience wrapper around get_session_report() for direct CLI use.
    """
    print(get_session_report())


def main():
    parser = argparse.ArgumentParser(description="Session status report")
    parser.add_argument("--all", action="store_true", help="Include completed/failed sessions")
    parser.add_argument("--stalled", action="store_true", help="Only show stalled sessions")
    args = parser.parse_args()

    print(get_session_report(include_completed=args.all, stalled_only=args.stalled))


if __name__ == "__main__":
    main()
