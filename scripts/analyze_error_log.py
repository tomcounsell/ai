#!/usr/bin/env python3
"""Temporary script to analyze bridge.error.log.

Identifies the most common errors, groups them by root cause,
and produces an actionable summary.

Usage:
    python scripts/analyze_error_log.py
    python scripts/analyze_error_log.py --since 2026-02-01
    python scripts/analyze_error_log.py --top 20
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "logs" / "bridge.error.log"

# Patterns to classify errors into root cause buckets
ROOT_CAUSE_PATTERNS = [
    # Network / Telegram connection
    (r"Server closed the connection", "telegram_connection_drop"),
    (r"Connection closed while receiving", "telegram_connection_drop"),
    (r"Can't assign requested address", "telegram_connection_drop"),
    (r"ConnectionResetError", "telegram_connection_reset"),
    (r"ConnectionError", "telegram_connection_error"),
    (r"ServerDisconnectedError", "telegram_server_disconnect"),
    (r"The server has closed the connection", "telegram_connection_drop"),
    (r"RPCError|FloodWaitError|ChatWriteForbiddenError", "telegram_api_error"),
    (r"TimeoutError|timed out|timeout", "timeout"),
    # Session / Lock errors
    (r"session file locked|FailoverError.*locked", "session_lock_conflict"),
    (r"ModelException|popoto.*exception", "redis_model_error"),
    (r"unique constraint|duplicate key", "redis_duplicate_key"),
    # SDK / Claude Code errors
    (r"SDK.*error|sdk_client.*error", "sdk_error"),
    (r"Claude Code.*error|claude.*process", "claude_code_error"),
    (r"Clawdbot error", "clawdbot_error"),
    (r"Process.*killed|Process.*died|SIGKILL|SIGTERM", "process_killed"),
    (r"MemoryError|memory|OOM", "memory_error"),
    # API errors
    (r"HTTP.*4\d\d|HTTP.*5\d\d|status.*(4\d\d|5\d\d)", "http_error"),
    (r"rate.limit|429|too many requests", "rate_limit"),
    (r"overloaded|529|capacity", "api_overloaded"),
    # Auth errors
    (r"auth.*error|unauthorized|403|401", "auth_error"),
    # File / IO errors
    (r"FileNotFoundError|No such file", "file_not_found"),
    (r"PermissionError|Permission denied", "permission_error"),
    (r"OSError|IOError", "io_error"),
    # Python errors
    (r"TypeError|AttributeError|KeyError|ValueError|IndexError", "python_error"),
    (r"ImportError|ModuleNotFoundError", "import_error"),
    (r"Traceback \(most recent call last\)", "traceback"),
    # Webhook/callback
    (r"Got difference for account updates", "telegram_sync_update"),
]


def classify_error(line: str) -> str | None:
    """Classify a log line into a root cause bucket."""
    for pattern, cause in ROOT_CAUSE_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return cause
    return None


def parse_log_line(line: str) -> dict | None:
    """Parse a log line into components."""
    # Format: 2026-01-19 15:22:54,554 [LEVEL] message
    match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] (.*)", line)
    if match:
        return {
            "timestamp": match.group(1),
            "level": match.group(2),
            "message": match.group(3),
        }
    return None


def extract_error_signature(line: str) -> str:
    """Extract a normalized error signature for deduplication.

    Strips timestamps, session IDs, PIDs, paths, and other variable parts
    to group identical errors together.
    """
    msg = line
    # Remove timestamp prefix
    msg = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \[\w+\] ", "", msg)
    # Remove session IDs
    msg = re.sub(r"tg_\w+_-?\d+_\d+", "<SESSION>", msg)
    msg = re.sub(r"session[= ]\S+", "session=<ID>", msg)
    # Remove job IDs / UUIDs
    msg = re.sub(r"[0-9a-f]{8,32}", "<ID>", msg)
    # Remove PIDs
    msg = re.sub(r"pid=\d+", "pid=<PID>", msg)
    # Remove file paths (keep filename)
    msg = re.sub(r"/[\w/.-]+/(\w+\.\w+)", r".../<FILE:\1>", msg)
    # Remove IP addresses
    msg = re.sub(r"\d+\.\d+\.\d+\.\d+", "<IP>", msg)
    # Remove numeric values in parens
    msg = re.sub(r"\(\d+ chars?\)", "(<N> chars)", msg)
    msg = re.sub(r"\(\d+ bytes?\)", "(<N> bytes)", msg)
    # Remove timestamps in messages
    msg = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<TIMESTAMP>", msg)
    # Collapse whitespace
    msg = re.sub(r"\s+", " ", msg).strip()
    # Truncate long signatures
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg


def analyze_log(log_path: Path, since: str | None = None, top_n: int = 15):
    """Analyze the error log and print results."""
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        sys.exit(1)

    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%d")

    # Counters
    level_counts = Counter()
    root_cause_counts = Counter()
    error_signature_counts = Counter()
    error_examples = defaultdict(list)  # root_cause -> [example lines]
    root_cause_signatures = defaultdict(Counter)  # root_cause -> {sig: count}
    daily_errors = Counter()  # date -> count
    total_lines = 0
    error_lines = 0
    warning_lines = 0

    # Traceback aggregation
    in_traceback = False
    traceback_buffer = []
    traceback_trigger_line = ""

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip()
            if not line:
                continue

            parsed = parse_log_line(line)

            # Handle traceback continuation lines
            if in_traceback:
                if parsed and parsed["level"] in ("ERROR", "WARNING", "INFO", "DEBUG"):
                    # Traceback ended, process it
                    sig = extract_error_signature(traceback_trigger_line)
                    error_signature_counts[sig] += 1
                    in_traceback = False
                    traceback_buffer = []
                else:
                    traceback_buffer.append(line)
                    continue

            if not parsed:
                # Could be traceback continuation
                if line.startswith("  ") or line.startswith("Traceback"):
                    traceback_buffer.append(line)
                    in_traceback = True
                continue

            # Filter by date
            if since_dt:
                try:
                    line_dt = datetime.strptime(
                        parsed["timestamp"], "%Y-%m-%d %H:%M:%S"
                    )
                    if line_dt < since_dt:
                        continue
                except ValueError:
                    continue

            total_lines += 1
            level = parsed["level"]
            level_counts[level] += 1
            message = parsed["message"]

            # Track daily error/warning counts
            date_str = parsed["timestamp"][:10]

            if level in ("ERROR", "WARNING", "CRITICAL"):
                if level == "ERROR":
                    error_lines += 1
                elif level == "WARNING":
                    warning_lines += 1

                daily_errors[date_str] += 1

                # Classify root cause
                cause = classify_error(message)
                if cause and cause != "telegram_sync_update":
                    root_cause_counts[cause] += 1
                    root_cause_signatures[cause][extract_error_signature(line)] += 1
                    if len(error_examples[cause]) < 3:
                        error_examples[cause].append(
                            parsed["timestamp"] + " " + message[:200]
                        )

                # Track error signatures
                sig = extract_error_signature(line)
                if level == "ERROR":
                    error_signature_counts[sig] += 1

    # === Print Report ===
    print("=" * 80)
    print("BRIDGE ERROR LOG ANALYSIS")
    print(f"File: {log_path}")
    print(f"Size: {log_path.stat().st_size / 1024 / 1024:.1f} MB")
    if since:
        print(f"Filtered: since {since}")
    print("=" * 80)

    print(f"\n### Log Level Distribution ({total_lines:,} total lines)")
    for level, count in level_counts.most_common():
        pct = count / total_lines * 100 if total_lines else 0
        bar = "█" * int(pct / 2)
        print(f"  {level:10s} {count:>8,}  ({pct:5.1f}%)  {bar}")

    print("\n### Error/Warning Rate")
    print(f"  Total errors:   {error_lines:,}")
    print(f"  Total warnings: {warning_lines:,}")
    if total_lines:
        print(
            f"  Error rate:     {error_lines / total_lines * 100:.2f}% of all log lines"
        )

    print("\n### Daily Error+Warning Volume (last 14 days)")
    sorted_days = sorted(daily_errors.keys())[-14:]
    max_daily = max(daily_errors[d] for d in sorted_days) if sorted_days else 1
    for day in sorted_days:
        count = daily_errors[day]
        bar_len = int(count / max_daily * 40) if max_daily else 0
        print(f"  {day}  {count:>5}  {'█' * bar_len}")

    print("\n### Root Causes (by frequency)")
    print(f"  {'Cause':<30s} {'Count':>7s}  {'%':>6s}  Description")
    print(f"  {'-' * 30} {'-' * 7}  {'-' * 6}  {'-' * 30}")
    total_classified = sum(root_cause_counts.values())
    for cause, count in root_cause_counts.most_common(top_n):
        pct = count / total_classified * 100 if total_classified else 0
        desc = {
            "telegram_connection_drop": "Telegram server closed connection",
            "telegram_connection_reset": "TCP connection reset by peer",
            "telegram_connection_error": "General connection failure",
            "telegram_server_disconnect": "Server-initiated disconnect",
            "telegram_api_error": "Telegram API errors (RPC/flood)",
            "telegram_sync_update": "Account sync updates (normal)",
            "session_lock_conflict": "Concurrent session file locks",
            "redis_model_error": "Popoto/Redis model exceptions",
            "redis_duplicate_key": "Duplicate key violations",
            "sdk_error": "Claude Agent SDK errors",
            "claude_code_error": "Claude Code process errors",
            "clawdbot_error": "Legacy clawdbot errors",
            "process_killed": "Process killed (OOM/signal)",
            "memory_error": "Out of memory",
            "http_error": "HTTP 4xx/5xx responses",
            "rate_limit": "API rate limiting (429)",
            "api_overloaded": "API overloaded (529)",
            "auth_error": "Authentication failures",
            "file_not_found": "Missing files",
            "permission_error": "File permission denied",
            "io_error": "Disk I/O errors",
            "python_error": "Python runtime errors",
            "import_error": "Module import failures",
            "traceback": "Unclassified tracebacks",
            "timeout": "Operation timeouts",
        }.get(cause, cause)
        print(f"  {cause:<30s} {count:>7,}  {pct:5.1f}%  {desc}")

    print(f"\n### Top {top_n} Most Frequent ERROR Signatures")
    for i, (sig, count) in enumerate(error_signature_counts.most_common(top_n), 1):
        print(f"\n  #{i} ({count:,} occurrences)")
        print(f"     {sig[:120]}")

    print("\n### Root Cause Details (with examples)")
    for cause, count in root_cause_counts.most_common(10):
        print(f"\n  ── {cause} ({count:,}) ──")
        # Show top signatures for this cause
        top_sigs = root_cause_signatures[cause].most_common(3)
        for sig, sig_count in top_sigs:
            print(f"     [{sig_count:>4}x] {sig[:100]}")
        # Show example timestamps
        for ex in error_examples[cause][:2]:
            print(f"     e.g.: {ex[:120]}")

    print("\n### Recommendations")
    print()
    if root_cause_counts.get("telegram_connection_drop", 0) > 50:
        print("  1. TELEGRAM CONNECTION DROPS are the #1 noise source.")
        print("     These are normal — Telegram periodically resets idle connections.")
        print("     FIX: Downgrade these from WARNING to DEBUG to reduce log noise.\n")
    if root_cause_counts.get("session_lock_conflict", 0) > 10:
        print(
            "  2. SESSION LOCK CONFLICTS indicate concurrent access to session files."
        )
        print("     FIX: The self-healing system should be cleaning these up.\n")
    if root_cause_counts.get("python_error", 0) > 20:
        print("  3. PYTHON RUNTIME ERRORS need individual investigation.")
        print("     These are real bugs that should be fixed.\n")
    if root_cause_counts.get("timeout", 0) > 20:
        print("  4. TIMEOUTS may indicate resource contention or slow APIs.")
        print("     Check if they correlate with high memory/CPU periods.\n")
    if root_cause_counts.get("redis_model_error", 0) > 5:
        print("  5. REDIS MODEL ERRORS indicate Popoto ORM issues.")
        print("     May need schema evolution or error handling improvements.\n")

    # File size recommendation
    file_mb = log_path.stat().st_size / 1024 / 1024
    if file_mb > 10:
        print(f"  ⚠️  LOG FILE IS {file_mb:.0f}MB. Consider rotation:")
        print("     - Add logrotate config for bridge.error.log")
        print(
            "     - Or truncate old entries: tail -n 50000 bridge.error.log"
            " > bridge.error.log.tmp && mv bridge.error.log.tmp bridge.error.log"
        )

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze bridge.error.log for common errors and root causes"
    )
    parser.add_argument(
        "--since",
        help="Only analyze entries since this date (YYYY-MM-DD)",
        default=None,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="Number of top entries to show (default: 15)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=LOG_FILE,
        help="Path to log file",
    )
    args = parser.parse_args()

    analyze_log(args.file, since=args.since, top_n=args.top)


if __name__ == "__main__":
    main()
