"""CLI entry point for YouTube search tool.

Usage:
    valor-youtube-search "query string"
    valor-youtube-search --limit 3 "query string"
"""

import argparse
import sys

from tools.youtube_search import format_results, youtube_search_sync


def main():
    """Main CLI entry point for valor-youtube-search."""
    parser = argparse.ArgumentParser(
        prog="valor-youtube-search",
        description="Search YouTube for videos by query.",
        usage="valor-youtube-search [--limit N] QUERY",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Search query string",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of results (default: 5)",
    )

    args = parser.parse_args()

    # Join query parts (handles both quoted and unquoted args)
    query = " ".join(args.query).strip()

    if not query:
        print("Usage: valor-youtube-search [--limit N] QUERY", file=sys.stderr)
        print("\nError: search query is required.", file=sys.stderr)
        sys.exit(1)

    try:
        results = youtube_search_sync(query, limit=args.limit)
        print(format_results(results))
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: unexpected failure: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
