#!/usr/bin/env python3
"""Memory Search CLI.

Search, save, inspect, and forget memories from the command line.

Usage:
    python -m tools.memory_search search "deploy patterns"
    python -m tools.memory_search search "deploy patterns" --project dm --limit 5
    python -m tools.memory_search save "API X requires auth header Y"
    python -m tools.memory_search save "important note" --importance 6.0 --source human
    python -m tools.memory_search inspect --id abc123
    python -m tools.memory_search inspect --stats --project dm
    python -m tools.memory_search forget --id abc123 --confirm
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC

from tools.memory_search import forget, inspect, outcome_stats, save, search, status, timeline


def cmd_search(args: argparse.Namespace) -> int:
    """Search memories."""
    result = search(
        query=args.query,
        project_key=args.project,
        limit=args.limit,
        category=getattr(args, "category", None),
        tag=getattr(args, "tag", None),
        min_act_rate=getattr(args, "act_rate", None),
    )

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    matches = result.get("results", [])
    if not matches:
        print(f"No memories found matching '{args.query}'")
        return 0

    print(f"Found {len(matches)} memories matching '{args.query}':")
    print()

    for mem in matches:
        content = mem.get("content", "")
        if len(content) > 200:
            content = content[:197] + "..."
        source = mem.get("source", "unknown")
        confidence = mem.get("confidence", 0.0)
        memory_id = mem.get("memory_id", "")

        meta = mem.get("metadata", {})
        category = meta.get("category", "")
        tags = meta.get("tags", [])

        cat_label = f"[{category}] " if category else ""
        print(f"  {cat_label}[{source}] {content}")
        tag_str = f"  tags={','.join(tags)}" if tags else ""
        print(f"    id={memory_id}  confidence={confidence:.2f}{tag_str}")
        print()

    return 0


def cmd_save(args: argparse.Namespace) -> int:
    """Save a new memory."""
    result = save(
        content=args.content,
        importance=args.importance,
        project_key=args.project,
        source=args.source,
    )

    if result is None:
        if args.json:
            print(json.dumps({"error": "Memory was not saved (filtered or failed)"}, indent=2))
            return 1
        print("Memory was not saved (filtered or failed).", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(f"Memory saved: {result.get('memory_id', 'unknown')}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Inspect a memory or show stats."""
    result = inspect(
        memory_id=args.id,
        project_key=args.project,
        stats=args.stats,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.id:
        # Single memory details
        print(f"Memory: {result.get('memory_id', '')}")
        print(f"  Content: {result.get('content', '')}")
        print(f"  Source: {result.get('source', '')}")
        print(f"  Importance: {result.get('importance', 0.0)}")
        print(f"  Confidence: {result.get('confidence', 0.0):.2f}")
        print(f"  Access count: {result.get('access_count', 0)}")
        print(f"  Project: {result.get('project_key', '')}")
        meta = result.get("metadata", {})
        if meta.get("category"):
            print(f"  Category: {meta['category']}")
        if meta.get("tags"):
            print(f"  Tags: {', '.join(meta['tags'])}")
        if meta.get("file_paths"):
            print(f"  File paths: {', '.join(meta['file_paths'])}")
        if meta.get("dismissal_count"):
            print(f"  Dismissal count: {meta['dismissal_count']}")
        if meta.get("last_outcome"):
            print(f"  Last outcome: {meta['last_outcome']}")
        outcome_history = meta.get("outcome_history", [])
        if outcome_history:
            from datetime import datetime

            from agent.memory_extraction import compute_act_rate

            act_rate = compute_act_rate(outcome_history)
            rate_str = f"{act_rate:.0%}" if act_rate is not None else "N/A"
            print(f"  Act rate: {rate_str} ({len(outcome_history)} outcomes)")
            print("  Outcome history:")
            print(f"    {'Date':<20} {'Outcome':<12} {'Reasoning'}")
            print(f"    {'-' * 20} {'-' * 12} {'-' * 40}")
            for entry in outcome_history:
                ts = entry.get("ts", 0)
                dt_str = (
                    datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M")
                    if ts
                    else "unknown"
                )
                outcome_val = entry.get("outcome", "?")
                reasoning_val = entry.get("reasoning", "")[:60]
                print(f"    {dt_str:<20} {outcome_val:<12} {reasoning_val}")
    elif args.stats:
        # Aggregate stats
        print(f"Memory stats for project '{result.get('project_key', '')}':")
        print(f"  Total memories: {result.get('total', 0)}")
        by_source = result.get("by_source", {})
        if by_source:
            print("  By source:")
            for src, count in sorted(by_source.items()):
                print(f"    {src}: {count}")
        avg_conf = result.get("avg_confidence", 0.0)
        print(f"  Avg confidence: {avg_conf:.2f}")

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show outcome statistics."""
    result = outcome_stats(project_key=args.project)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    project = result.get("project_key", "")
    total = result.get("total_with_history", 0)
    avg_rate = result.get("avg_act_rate", 0.0)

    print(f"Outcome statistics for project '{project}':")
    print(f"  Memories with outcome history: {total}")
    print(f"  Average act rate: {avg_rate:.1%}")

    top_acted = result.get("top_acted", [])
    if top_acted:
        print()
        print("  Top acted-on memories:")
        for mem in top_acted:
            content = mem.get("content", "")
            rate = mem.get("act_rate", 0.0)
            total_outcomes = mem.get("total_outcomes", 0)
            mid = mem.get("memory_id", "")
            print(f"    [{rate:.0%} over {total_outcomes}] {content}")
            print(f"      id={mid}")

    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """Delete a memory by ID."""
    if not args.confirm:
        print(
            "Error: --confirm flag is required to delete a memory.",
            file=sys.stderr,
        )
        print(
            f"Run: python -m tools.memory_search forget --id {args.id} --confirm",
            file=sys.stderr,
        )
        return 1

    result = forget(memory_id=args.id)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if result.get("deleted"):
        print(f"Memory deleted: {result.get('memory_id', '')}")
        return 0
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show memory system health status."""
    result = status(
        project_key=args.project,
        deep=getattr(args, "deep", False),
    )

    # Redis-down: always exit 1 with human-readable error on stderr
    if not result.get("healthy", True) and "error" in result:
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Human-readable output
    project = result.get("project_key", "")
    total = result.get("total", 0)
    superseded = result.get("superseded", 0)
    avg_conf = result.get("avg_confidence", 0.0)
    last_write = result.get("last_write") or "unknown"
    embedding = result.get("embedding_field", "unknown")
    by_category = result.get("by_category", {})

    print(f"Memory System Status — project '{project}'")
    print("  Redis:           ok")
    print(f"  Total records:   {total}")
    print(f"  Superseded:      {superseded}")
    print(f"  Avg confidence:  {avg_conf:.4f}")
    print(f"  Last write:      {last_write}")
    print(f"  Embedding field: {embedding}")

    if by_category:
        print("  By category:")
        for cat, count in sorted(by_category.items()):
            print(f"    {cat}: {count}")

    if "orphan_index_count" in result:
        print(f"  Orphan index keys: {result['orphan_index_count']}")

    if "by_category_confidence" in result:
        print("  Per-category confidence:")
        for cat, info in sorted(result["by_category_confidence"].items()):
            print(f"    {cat}: avg={info['avg_confidence']:.4f} (n={info['count']})")

    return 0


def cmd_timeline(args: argparse.Namespace) -> int:
    """Show memory timeline for a time range."""
    from datetime import datetime, timedelta

    # Parse time bounds
    since = None
    until = None

    if hasattr(args, "since") and args.since:
        since = _parse_time_arg(args.since)
        if since is None:
            print(f"Error: could not parse --since value: {args.since}", file=sys.stderr)
            return 1

    if hasattr(args, "until") and args.until:
        until = _parse_time_arg(args.until)
        if until is None:
            print(f"Error: could not parse --until value: {args.until}", file=sys.stderr)
            return 1

    # Default: last 7 days if no time bounds specified
    if since is None and until is None:
        since = datetime.now(UTC) - timedelta(days=7)

    result = timeline(
        project_key=args.project,
        since=since,
        until=until,
        category=getattr(args, "category", None),
        group_by=getattr(args, "group_by", None),
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    results = result.get("results", [])
    summary = result.get("summary", {})
    groups = result.get("groups")

    if not results:
        time_desc = ""
        if since:
            time_desc = f" since {since.strftime('%Y-%m-%d')}"
        print(f"No memories found{time_desc}")
        return 0

    print(f"Memory Timeline ({summary.get('total', 0)} records)")
    print()

    if groups:
        for group_key in sorted(groups.keys(), reverse=True):
            group_items = groups[group_key]
            print(f"  [{group_key}] ({len(group_items)} records)")
            for mem in group_items:
                _print_timeline_entry(mem)
            print()
    else:
        for mem in results:
            _print_timeline_entry(mem)

    # Summary footer
    by_cat = summary.get("by_category", {})
    if by_cat:
        cat_parts = [f"{cat}: {count}" for cat, count in sorted(by_cat.items())]
        print(f"  Categories: {', '.join(cat_parts)}")

    return 0


def _print_timeline_entry(mem: dict) -> None:
    """Print a single timeline entry."""
    content = mem.get("content", "")
    if len(content) > 150:
        content = content[:147] + "..."
    source = mem.get("source", "unknown")
    la = mem.get("last_accessed", "")
    date_str = la[:16] if la else "unknown"
    meta = mem.get("metadata", {})
    category = meta.get("category", "")
    cat_label = f"[{category}] " if category else ""
    importance = mem.get("importance", 0.0)

    print(f"    {date_str}  {cat_label}({source}, imp={importance:.1f}) {content}")


def _parse_time_arg(value: str):
    """Parse a time argument: ISO date, ISO datetime, or relative like '7 days ago'.

    Returns a datetime with UTC timezone, or None if unparseable.
    """
    from datetime import datetime, timedelta

    value = value.strip()

    # Try ISO date (YYYY-MM-DD)
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(tzinfo=UTC)
    except ValueError:
        pass

    # Try ISO datetime (YYYY-MM-DDTHH:MM:SS)
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=UTC)
    except ValueError:
        pass

    # Try relative: "N days ago", "N hours ago", "N weeks ago"
    import re

    match = re.match(r"(\d+)\s+(day|days|hour|hours|week|weeks)\s+ago", value, re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower().rstrip("s")
        now = datetime.now(UTC)
        if unit == "day":
            return now - timedelta(days=amount)
        elif unit == "hour":
            return now - timedelta(hours=amount)
        elif unit == "week":
            return now - timedelta(weeks=amount)

    return None


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="memory-search",
        description="Search, save, inspect, and forget memories",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # search command
    search_parser = subparsers.add_parser("search", help="Search memories")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    search_parser.add_argument(
        "--limit", "-n", type=int, default=10, help="Max results (default: 10)"
    )
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")
    search_parser.add_argument(
        "--category",
        "-c",
        choices=["correction", "decision", "pattern", "surprise"],
        help="Filter by metadata category",
    )
    search_parser.add_argument("--tag", "-t", help="Filter by metadata tag")
    search_parser.add_argument(
        "--act-rate",
        type=float,
        default=None,
        help="Filter to memories with act_rate >= threshold (0.0-1.0)",
    )

    # save command
    save_parser = subparsers.add_parser("save", help="Save a new memory")
    save_parser.add_argument("content", help="Memory content text")
    save_parser.add_argument(
        "--importance",
        "-i",
        type=float,
        default=None,
        help="Importance score (default: 6.0)",
    )
    save_parser.add_argument(
        "--source",
        "-s",
        default="human",
        choices=["human", "agent", "system"],
        help="Source type (default: human)",
    )
    save_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    save_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a memory or show stats")
    inspect_parser.add_argument("--id", help="Memory ID to inspect")
    inspect_parser.add_argument("--stats", action="store_true", help="Show aggregate statistics")
    inspect_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    inspect_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show outcome statistics")
    stats_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # forget command
    forget_parser = subparsers.add_parser("forget", help="Delete a memory")
    forget_parser.add_argument("--id", required=True, help="Memory ID to delete")
    forget_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm deletion",
    )
    forget_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # status command
    status_parser = subparsers.add_parser("status", help="Check memory system health")
    status_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")
    status_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run slow checks: orphan index count and per-category confidence",
    )

    # timeline command
    timeline_parser = subparsers.add_parser(
        "timeline", help="Show memory timeline for a time range"
    )
    timeline_parser.add_argument(
        "--since",
        help="Start of time range (ISO date, or '7 days ago')",
    )
    timeline_parser.add_argument(
        "--until",
        help="End of time range (ISO date, or '1 day ago')",
    )
    timeline_parser.add_argument("--project", "-p", help="Project key (default: from env)")
    timeline_parser.add_argument(
        "--limit", "-n", type=int, default=50, help="Max results (default: 50)"
    )
    timeline_parser.add_argument("--json", action="store_true", help="Output as JSON")
    timeline_parser.add_argument(
        "--category",
        "-c",
        choices=["correction", "decision", "pattern", "surprise"],
        help="Filter by metadata category",
    )
    timeline_parser.add_argument(
        "--group-by",
        choices=["day", "category"],
        help="Group results by day or category",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "search": cmd_search,
        "save": cmd_save,
        "inspect": cmd_inspect,
        "stats": cmd_stats,
        "forget": cmd_forget,
        "status": cmd_status,
        "timeline": cmd_timeline,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
