"""CLI entry point for Open Deep Research.

Usage:
    python -m apps.podcast.tools.together_deep_research "Your research prompt"
    python -m apps.podcast.tools.together_deep_research --file prompt.txt
    python -m apps.podcast.tools.together_deep_research --file prompt.txt --output results.md
    python -m apps.podcast.tools.together_deep_research "prompt" --max-search-depth 3
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .runner import run_together_research


def main():
    parser = argparse.ArgumentParser(
        description="Run Open Deep Research on a topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s "Research the history of quantum computing"
    %(prog)s --file research_prompt.txt
    %(prog)s --file prompt.txt --output results.md
    %(prog)s "prompt" --max-search-depth 3

Environment (need at least one LLM key + Tavily):
    ANTHROPIC_API_KEY  - Anthropic API key (default provider)
    OPENAI_API_KEY     - OpenAI API key (alternative)
    OPENROUTER_API_KEY - OpenRouter API key (fallback)
    TAVILY_API_KEY     - Tavily API key (required for web search)
                         Get one at: https://tavily.com/
        """,
    )

    parser.add_argument("prompt", nargs="*", help="Research prompt (or use --file)")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--output", "-o", help="Write output to file")

    parser.add_argument(
        "--max-search-depth",
        type=int,
        default=2,
        help="Max search iterations per section (default: 2)",
    )

    parser.add_argument(
        "--number-of-queries",
        type=int,
        default=2,
        help="Search queries per iteration (default: 2)",
    )

    parser.add_argument(
        "--planner-provider",
        help="LLM provider for planning (default: auto-detect from keys)",
    )

    parser.add_argument(
        "--planner-model",
        help="LLM model for planning (default: auto-detect)",
    )

    parser.add_argument(
        "--writer-provider",
        help="LLM provider for writing (default: same as planner)",
    )

    parser.add_argument(
        "--writer-model",
        help="LLM model for writing (default: same as planner)",
    )

    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=900,
        help="Timeout in seconds (default: 900 = 15 minutes)",
    )

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Minimal output (just the result)"
    )

    parser.add_argument(
        "--auto-save",
        action="store_true",
        help=(
            "Automatically save output and logs with timestamp"
            " (default: True unless --output specified)"
        ),
    )

    parser.add_argument(
        "--no-auto-save", action="store_true", help="Disable automatic file saving"
    )

    parser.add_argument(
        "--log-dir",
        help="Directory for output and log files (default: current directory)",
    )

    args = parser.parse_args()

    # Get prompt from arguments or file
    if args.file:
        try:
            with open(args.file) as f:
                prompt = f.read().strip()
        except FileNotFoundError:
            print(f"ERROR: File not found: {args.file}")
            sys.exit(1)
    elif args.prompt:
        prompt = " ".join(args.prompt)
    else:
        parser.print_help()
        sys.exit(1)

    if not prompt:
        print("ERROR: Empty prompt")
        sys.exit(1)

    # Determine if auto-save should be enabled
    auto_save = not args.no_auto_save and (args.auto_save or not args.output)

    # Set up log directory
    log_dir = args.log_dir or "."
    if log_dir != "." and not Path(log_dir).exists():
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Set up auto-save file paths
    output_file = args.output
    log_file = None

    if auto_save and not args.output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(Path(log_dir) / f"together_output_{timestamp}.md")
        log_file = str(Path(log_dir) / f"together_log_{timestamp}.txt")
        print("Auto-save enabled:")
        print(f"  Output: {output_file}")
        print(f"  Log: {log_file}")
        print()
    elif auto_save and args.output:
        output_path = Path(args.output)
        output_file = str(output_path)
        log_file = str(output_path.parent / (output_path.stem + "_log.txt"))
        print(f"Saving output to: {output_file}")
        print(f"Saving log to: {log_file}")
        print()

    # Run the research
    content, metadata = run_together_research(
        prompt,
        max_search_depth=args.max_search_depth,
        number_of_queries=args.number_of_queries,
        planner_provider=args.planner_provider,
        planner_model=args.planner_model,
        writer_provider=args.writer_provider,
        writer_model=args.writer_model,
        timeout=args.timeout,
        verbose=not args.quiet,
        log_file=log_file,
    )

    if content:
        if output_file:
            provider = metadata.get("planner_provider", "unknown")
            model = metadata.get("planner_model", "unknown")
            with open(output_file, "w") as f:
                f.write("# Open Deep Research Results\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write(f"**Provider:** {provider}\n\n")
                f.write(f"**Planner Model:** {model}\n\n")
                f.write(f"**Search Depth:** {args.max_search_depth} iterations\n\n")
                f.write(f"**Prompt:** {prompt}\n\n")
                f.write("---\n\n")
                f.write(content)
            print(f"\nResults saved to: {output_file}")
            if log_file:
                print(f"Log saved to: {log_file}")
        else:
            if not args.quiet:
                print("\n" + "=" * 60)
                print("RESEARCH OUTPUT")
                print("=" * 60 + "\n")
            print(content)

        sys.exit(0)
    else:
        print("\nResearch failed. See error messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
