"""CLI tool for analytics export and management.

Usage:
    python -m tools.analytics export --days 30
    python -m tools.analytics summary
    python -m tools.analytics rollup
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Ensure project root on sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logger = logging.getLogger(__name__)


def cmd_export(args: argparse.Namespace) -> None:
    """Export analytics data as JSON."""
    from analytics.query import (
        list_metric_names,
        query_daily_summary,
        query_metric_count,
        query_metric_total,
    )

    days = args.days
    metrics = list_metric_names()

    export_data = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "days": days,
        "metrics": {},
    }

    for name in metrics:
        total = query_metric_total(name, days=days)
        count = query_metric_count(name, days=days)
        daily = query_daily_summary(name, days=days)
        export_data["metrics"][name] = {
            "total": total,
            "count": count,
            "daily": daily,
        }

    json.dump(export_data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def cmd_summary(args: argparse.Namespace) -> None:
    """Print a human-readable summary of analytics data."""
    from analytics.query import list_metric_names, query_metric_count, query_metric_total

    metrics = list_metric_names()
    if not metrics:
        print("No analytics data recorded yet.")
        return

    print(f"Analytics Summary ({len(metrics)} metrics)")
    print("=" * 50)

    for name in metrics:
        count_1d = query_metric_count(name, days=1)
        count_7d = query_metric_count(name, days=7)
        total_1d = query_metric_total(name, days=1)
        total_7d = query_metric_total(name, days=7)
        print(f"\n  {name}:")
        print(f"    Today:  {count_1d} events, total={total_1d}")
        print(f"    7-day:  {count_7d} events, total={total_7d}")


def cmd_rollup(args: argparse.Namespace) -> None:
    """Run the daily rollup manually."""
    from analytics.rollup import rollup_daily

    result = rollup_daily()
    agg = result["aggregated_days"]
    purged = result["purged_rows"]
    print(f"Rollup complete: {agg} days aggregated, {purged} rows purged")
    if result["errors"]:
        for err in result["errors"]:
            print(f"  Error: {err}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="analytics",
        description="Unified analytics CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export
    export_parser = subparsers.add_parser("export", help="Export analytics as JSON")
    export_parser.add_argument("--days", type=int, default=30, help="Days to export (default: 30)")

    # summary
    subparsers.add_parser("summary", help="Print human-readable summary")

    # rollup
    subparsers.add_parser("rollup", help="Run daily rollup")

    args = parser.parse_args()

    if args.command == "export":
        cmd_export(args)
    elif args.command == "summary":
        cmd_summary(args)
    elif args.command == "rollup":
        cmd_rollup(args)


if __name__ == "__main__":
    main()
