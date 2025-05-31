"""Command-line interface for token usage reports.

This module provides a simple CLI for querying and displaying token usage statistics.
"""

import argparse
import sys
from datetime import datetime, timedelta
from typing import Optional
from .token_tracker import get_tracker


def format_tokens(count: int) -> str:
    """Format token count with thousands separator."""
    return f"{count:,}"


def format_cost(cost: float) -> str:
    """Format cost in USD."""
    return f"${cost:.4f}"


def print_summary_report(
    project: Optional[str] = None,
    host: Optional[str] = None,
    model: Optional[str] = None,
    days: Optional[int] = None
) -> None:
    """Print usage summary report."""
    tracker = get_tracker()
    
    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days) if days else None
    
    summary = tracker.get_usage_summary(
        project=project,
        host=host,
        model=model,
        start_date=start_date,
        end_date=end_date
    )
    
    print("=" * 60)
    print("TOKEN USAGE SUMMARY")
    print("=" * 60)
    
    if project:
        print(f"Project: {project}")
    if host:
        print(f"Host: {host}")
    if model:
        print(f"Model: {model}")
    if days:
        print(f"Period: Last {days} days")
    
    print()
    print(f"Total Requests:      {summary.get('request_count', 0):,}")
    print(f"Input Tokens:        {format_tokens(summary.get('total_input_tokens', 0))}")
    print(f"Output Tokens:       {format_tokens(summary.get('total_output_tokens', 0))}")
    print(f"Total Tokens:        {format_tokens(summary.get('total_tokens', 0))}")
    print(f"Total Cost:          {format_cost(summary.get('total_cost_usd', 0))}")
    print(f"Avg Tokens/Request:  {summary.get('avg_tokens_per_request', 0):.1f}")


def print_project_breakdown(days: Optional[int] = None) -> None:
    """Print usage breakdown by project."""
    tracker = get_tracker()
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days) if days else None
    
    projects = tracker.get_usage_by_project(start_date=start_date, end_date=end_date)
    
    print("=" * 80)
    print("USAGE BY PROJECT")
    print("=" * 80)
    
    if days:
        print(f"Period: Last {days} days")
        print()
    
    if not projects:
        print("No usage data found.")
        return
    
    print(f"{'Project':<20} {'Requests':<10} {'Tokens':<15} {'Cost':<12}")
    print("-" * 80)
    
    for project in projects:
        print(f"{project['project']:<20} "
              f"{project['request_count']:<10} "
              f"{format_tokens(project['total_tokens']):<15} "
              f"{format_cost(project['total_cost_usd']):<12}")


def print_model_breakdown(days: Optional[int] = None) -> None:
    """Print usage breakdown by model."""
    tracker = get_tracker()
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days) if days else None
    
    models = tracker.get_usage_by_model(start_date=start_date, end_date=end_date)
    
    print("=" * 90)
    print("USAGE BY MODEL")
    print("=" * 90)
    
    if days:
        print(f"Period: Last {days} days")
        print()
    
    if not models:
        print("No usage data found.")
        return
    
    print(f"{'Host':<12} {'Model':<25} {'Requests':<10} {'Tokens':<15} {'Cost':<12}")
    print("-" * 90)
    
    for model in models:
        print(f"{model['host']:<12} "
              f"{model['model']:<25} "
              f"{model['request_count']:<10} "
              f"{format_tokens(model['total_tokens']):<15} "
              f"{format_cost(model['total_cost_usd']):<12}")


def print_daily_usage(days: int = 7, project: Optional[str] = None) -> None:
    """Print daily usage for the last N days."""
    tracker = get_tracker()
    
    daily_data = tracker.get_daily_usage(days=days, project=project)
    
    print("=" * 70)
    print("DAILY USAGE")
    print("=" * 70)
    
    if project:
        print(f"Project: {project}")
    print(f"Period: Last {days} days")
    print()
    
    if not daily_data:
        print("No usage data found.")
        return
    
    print(f"{'Date':<12} {'Requests':<10} {'Tokens':<15} {'Cost':<12}")
    print("-" * 70)
    
    for day in daily_data:
        print(f"{day['date']:<12} "
              f"{day['request_count']:<10} "
              f"{format_tokens(day['total_tokens']):<15} "
              f"{format_cost(day['total_cost_usd']):<12}")


def export_data(
    output_file: str,
    days: Optional[int] = None,
    format: str = "csv"
) -> None:
    """Export usage data to file."""
    tracker = get_tracker()
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days) if days else None
    
    data = tracker.export_usage_data(
        start_date=start_date,
        end_date=end_date,
        format=format
    )
    
    with open(output_file, 'w') as f:
        f.write(data)
    
    print(f"Usage data exported to: {output_file}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Token usage reporting tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m utilities.token_reports summary
  python -m utilities.token_reports summary --project myproject --days 7
  python -m utilities.token_reports projects --days 30
  python -m utilities.token_reports models
  python -m utilities.token_reports daily --days 14
  python -m utilities.token_reports export --output usage.csv --days 30
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Summary command
    summary_parser = subparsers.add_parser('summary', help='Show usage summary')
    summary_parser.add_argument('--project', help='Filter by project')
    summary_parser.add_argument('--host', help='Filter by host')
    summary_parser.add_argument('--model', help='Filter by model')
    summary_parser.add_argument('--days', type=int, help='Number of days to include')
    
    # Projects command
    projects_parser = subparsers.add_parser('projects', help='Show usage by project')
    projects_parser.add_argument('--days', type=int, help='Number of days to include')
    
    # Models command
    models_parser = subparsers.add_parser('models', help='Show usage by model')
    models_parser.add_argument('--days', type=int, help='Number of days to include')
    
    # Daily command
    daily_parser = subparsers.add_parser('daily', help='Show daily usage')
    daily_parser.add_argument('--days', type=int, default=7, help='Number of days to show')
    daily_parser.add_argument('--project', help='Filter by project')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export usage data')
    export_parser.add_argument('--output', required=True, help='Output file path')
    export_parser.add_argument('--days', type=int, help='Number of days to include')
    export_parser.add_argument('--format', default='csv', choices=['csv'], help='Export format')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'summary':
            print_summary_report(
                project=args.project,
                host=args.host,
                model=args.model,
                days=args.days
            )
        elif args.command == 'projects':
            print_project_breakdown(days=args.days)
        elif args.command == 'models':
            print_model_breakdown(days=args.days)
        elif args.command == 'daily':
            print_daily_usage(days=args.days, project=args.project)
        elif args.command == 'export':
            export_data(
                output_file=args.output,
                days=args.days,
                format=args.format
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()