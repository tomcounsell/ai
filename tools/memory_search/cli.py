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

from tools.memory_search import forget, inspect, save, search


def cmd_search(args: argparse.Namespace) -> int:
    """Search memories."""
    result = search(
        query=args.query,
        project_key=args.project,
        limit=args.limit,
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

        print(f"  [{source}] {content}")
        print(f"    id={memory_id}  confidence={confidence:.2f}")
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

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if result is None:
        print("Memory was not saved (filtered or failed).", file=sys.stderr)
        return 1

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

    # forget command
    forget_parser = subparsers.add_parser("forget", help="Delete a memory")
    forget_parser.add_argument("--id", required=True, help="Memory ID to delete")
    forget_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to confirm deletion",
    )
    forget_parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "search": cmd_search,
        "save": cmd_save,
        "inspect": cmd_inspect,
        "forget": cmd_forget,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
