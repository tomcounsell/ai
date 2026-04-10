#!/usr/bin/env python3
"""
GPT-Researcher - Multi-Agent Deep Research Framework

This script uses GPT-Researcher's multi-agent architecture to conduct
comprehensive research with parallel information gathering and synthesis.

Usage:
    uv run python gpt_researcher_run.py "Your research prompt here"
    uv run python gpt_researcher_run.py --file prompt.txt
    uv run python gpt_researcher_run.py --file prompt.txt --output results.md
    uv run python gpt_researcher_run.py "prompt" --model openai:gpt-5-mini
    uv run python gpt_researcher_run.py "prompt" --model anthropic:claude-opus-4-6

Requirements:
    - API keys in .env file (OPENAI_API_KEY, OPENROUTER_API_KEY, etc.)
    - uv (package manager): curl -LsSf https://astral.sh/uv/install.sh | sh
    - Dependencies auto-installed via: uv pip install gpt-researcher langchain-openai

Configuration Options:
    - OpenAI (default): FAST_LLM=openai:gpt-5.2, SMART_LLM=openai:gpt-5.2
    - OpenAI Alternatives: gpt-5.2-pro (harder thinking), gpt-5-mini (cost-optimized), o3 (reasoning), o4-mini (fast reasoning)
    - Anthropic: FAST_LLM=anthropic:claude-opus-4-6, SMART_LLM=anthropic:claude-opus-4-6
    - OpenRouter: Use any model via openrouter/ prefix

Documentation:
    https://docs.gptr.dev/
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Try to load dotenv from current and parent directories
try:
    from dotenv import find_dotenv, load_dotenv

    # Load from current directory first
    load_dotenv()
    # Then try parent directories (up to 3 levels)
    for _i in range(1, 4):
        parent_env = find_dotenv(usecwd=True, raise_error_if_not_found=False)
        if parent_env:
            load_dotenv(parent_env, override=False)
    # Also try common parent locations
    for parent in list(Path.cwd().parents)[:3]:
        env_file = parent / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)
except ImportError:
    pass


def get_api_keys() -> dict:
    """Get API keys from environment or .env files."""
    keys = {
        "openai": os.getenv("OPENAI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "openrouter": os.getenv("OPENROUTER_API_KEY"),
        "xai": os.getenv("XAI_API_KEY"),
        "tavily": os.getenv("TAVILY_API_KEY"),
    }

    # Try loading from .env in current and parent directories
    if not any(keys.values()):
        for parent in [Path.cwd()] + list(Path.cwd().parents)[:3]:
            env_file = parent / ".env"
            if env_file.exists():
                with open(env_file) as f:
                    for line in f:
                        if "=" in line and not line.startswith("#"):
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip().strip("\"'")

                            if key == "OPENAI_API_KEY":
                                keys["openai"] = value
                            elif key == "ANTHROPIC_API_KEY":
                                keys["anthropic"] = value
                            elif key == "OPENROUTER_API_KEY":
                                keys["openrouter"] = value
                            elif key == "XAI_API_KEY":
                                keys["xai"] = value
                            elif key == "TAVILY_API_KEY":
                                keys["tavily"] = value

    return keys


def configure_model(model_spec: str) -> tuple[str, str]:
    """
    Configure LLM model from specification.

    Args:
        model_spec: Model specification like "openai:gpt-5.2" or "openrouter/anthropic/claude-opus-4-6"

    Returns:
        Tuple of (FAST_LLM, SMART_LLM) environment variables
    """
    # Handle openrouter/ prefix (e.g., "openrouter/anthropic/claude-opus-4-6")
    if model_spec.startswith("openrouter/"):
        # Extract model path after "openrouter/"
        model_path = model_spec.replace("openrouter/", "", 1)
        # GPT-Researcher expects format: "openrouter:provider/model"
        return f"openrouter:{model_path}", f"openrouter:{model_path}"

    # Handle provider:model format (e.g., "openai:gpt-5.2")
    if ":" in model_spec:
        provider, model = model_spec.split(":", 1)
        provider_map = {
            "openai": f"openai:{model}",
            "anthropic": f"anthropic:{model}",
            "xai": f"xai:{model}",
        }
        result = provider_map.get(provider)
        if result:
            return result, result

    # Default: use as-is
    return model_spec, model_spec


async def run_research(
    prompt: str,
    model_spec: str = "openai:gpt-5.2",
    report_type: str = "research_report",
    verbose: bool = True,
    use_detailed_report: bool = False,
    config_path: str | None = None,
    log_file: str | None = None,
) -> tuple[str | None, dict]:
    """
    Run GPT-Researcher with specified configuration.

    Args:
        prompt: Research query
        model_spec: Model specification (provider:model)
        report_type: Type of report (research_report, detailed_report, etc.)
        verbose: Print progress messages

    Returns:
        Tuple of (report_text, error_dict). report_text is None on failure.
        On exception, error_dict contains ``_error_message`` and ``_error_type``.
        On success, error_dict is empty ``{}``.
    """
    try:
        from gpt_researcher import GPTResearcher

        # DetailedReport is only available in newer versions
        try:
            from gpt_researcher import DetailedReport
        except ImportError:
            DetailedReport = None
    except ImportError:
        print("ERROR: gpt-researcher not installed")
        print("Install with: uv pip install gpt-researcher langchain-openai")
        print("Or ensure you're running with: uv run python gpt_researcher_run.py")
        return None, {}

    # Check for API keys - STRICT: require both OpenAI and Tavily
    keys = get_api_keys()

    # Determine required LLM key based on model
    if model_spec.startswith("openai:") or model_spec == "openai":
        if not keys.get("openai"):
            print("=" * 60)
            print("ERROR: OPENAI_API_KEY not found")
            print("=" * 60)
            print(f"\nGPT-Researcher requires OpenAI API key for model: {model_spec}")
            print("\nFix: Add to ~/.env:")
            print("  OPENAI_API_KEY=sk-...")
            print("\nGet key at: https://platform.openai.com/api-keys")
            print("=" * 60)
            return None, {}
    elif model_spec.startswith("anthropic:"):
        if not keys.get("anthropic"):
            print("=" * 60)
            print("ERROR: ANTHROPIC_API_KEY not found")
            print("=" * 60)
            print(
                f"\nGPT-Researcher requires Anthropic API key for model: {model_spec}"
            )
            print("\nFix: Add to ~/.env:")
            print("  ANTHROPIC_API_KEY=sk-ant-...")
            print("=" * 60)
            return None, {}
    elif model_spec.startswith("openrouter"):
        if not keys.get("openrouter"):
            print("=" * 60)
            print("ERROR: OPENROUTER_API_KEY not found")
            print("=" * 60)
            print(
                f"\nGPT-Researcher requires OpenRouter API key for model: {model_spec}"
            )
            print("\nFix: Add to ~/.env:")
            print("  OPENROUTER_API_KEY=sk-or-...")
            print("\nGet key at: https://openrouter.ai/keys")
            print("=" * 60)
            return None, {}
    elif not any(keys.values()):
        print("=" * 60)
        print("ERROR: No API keys found")
        print("=" * 60)
        print("\nSet at least one in ~/.env:")
        print("  OPENAI_API_KEY=sk-...")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        print("  OPENROUTER_API_KEY=sk-or-...")
        print("=" * 60)
        return None, {}

    # STRICT: Require Tavily API key - no silent fallback to DuckDuckGo
    if not keys.get("tavily") and not os.getenv("TAVILY_API_KEY"):
        print("=" * 60)
        print("ERROR: TAVILY_API_KEY not found")
        print("=" * 60)
        print("\nGPT-Researcher requires Tavily for quality web research.")
        print("DuckDuckGo fallback is DISABLED to ensure research quality.")
        print("\nFix: Add to ~/.env:")
        print("  TAVILY_API_KEY=tvly-...")
        print("\nGet FREE key at: https://tavily.com/")
        print("=" * 60)
        return None, {}

    # Configure model
    fast_llm, smart_llm = configure_model(model_spec)

    # Set environment variables for GPT-Researcher
    os.environ["FAST_LLM"] = fast_llm
    os.environ["SMART_LLM"] = smart_llm
    os.environ["STRATEGIC_LLM"] = smart_llm

    # Use Tavily (already validated above)
    os.environ["RETRIEVER"] = "tavily"

    # Helper to log to both stdout and file
    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    if verbose or log_file:
        log("=" * 60)
        log("GPT-RESEARCHER - MULTI-AGENT DEEP RESEARCH")
        log("=" * 60)
        log(
            f"\nPrompt: {prompt[:200]}..."
            if len(prompt) > 200
            else f"\nPrompt: {prompt}"
        )
        log("\nConfiguration:")
        log(f"  Fast LLM: {fast_llm}")
        log(f"  Smart LLM: {smart_llm}")
        log(f"  Report Type: {report_type}")
        log(f"  Search Provider: {os.environ.get('RETRIEVER', 'duckduckgo')}")
        log("\nStarting multi-agent research...")
        log("Expected time: 6-20 minutes")
        log("-" * 60)

    try:
        # Use DetailedReport for STORM-based hierarchical research if requested
        if use_detailed_report:
            if DetailedReport is None:
                if verbose or log_file:
                    log(
                        "\nWARNING: DetailedReport not available in gpt-researcher 0.14.5"
                    )
                    log("Falling back to standard GPTResearcher with enhanced config\n")
                use_detailed_report = False
            else:
                if verbose or log_file:
                    log("\n[Using DetailedReport - STORM Methodology]")

                researcher = DetailedReport(
                    query=prompt,
                    report_type=report_type,
                    report_source="web_search",
                    config_path=config_path,
                    tone="scholarly and evidence-based",
                    max_subtopics=7,
                    verbose=verbose,
                )

                if verbose or log_file:
                    log("\n[Phase 1] Breaking down topic into subtopics...")
                    log("[Phase 2] Researching each subtopic in depth...")

                report_text = await researcher.run()

        if not use_detailed_report:
            # Standard GPTResearcher
            researcher = GPTResearcher(
                query=prompt,
                report_type=report_type,
                config_path=config_path,
                verbose=verbose,
            )

            # Conduct research
            if verbose or log_file:
                log("\n[Phase 1] Planning research strategy...")

            await researcher.conduct_research()

            if verbose or log_file:
                log("\n[Phase 2] Synthesizing findings...")

            report_text = await researcher.write_report()

        if verbose or log_file:
            log(f"\n{'=' * 60}")
            log("RESEARCH COMPLETE")
            log(f"{'=' * 60}\n")

        return report_text, {}

    except Exception as e:
        print(f"ERROR: Research failed: {e}")
        import traceback

        if verbose:
            traceback.print_exc()
        return None, {
            "_error_message": str(e),
            "_error_type": type(e).__name__,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Run GPT-Researcher multi-agent deep research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s "Research early childhood educator burnout interventions"
    %(prog)s --file research_prompt.txt
    %(prog)s --file prompt.txt --output results.md
    %(prog)s "prompt" --model openai:gpt-5-mini
    %(prog)s "prompt" --model anthropic:claude-opus-4-6
    %(prog)s "prompt" --model openrouter/anthropic/claude-opus-4-6

Environment:
    OPENAI_API_KEY      - OpenAI API key (for GPT-4, etc.)
    ANTHROPIC_API_KEY   - Anthropic API key (for Claude)
    OPENROUTER_API_KEY  - OpenRouter unified API (400+ models)
    XAI_API_KEY         - xAI API key (for Grok)
        """,
    )

    parser.add_argument("prompt", nargs="*", help="Research prompt (or use --file)")

    parser.add_argument("--file", "-f", help="Read prompt from file")

    parser.add_argument("--output", "-o", help="Write output to file")

    parser.add_argument(
        "--model",
        "-m",
        default="openai:gpt-5.2",
        help="Model specification (default: openai:gpt-5.2). Examples: openai:gpt-5.2-pro, openai:gpt-5-mini, openai:o3, openai:o4-mini, anthropic:claude-opus-4-6",
    )

    parser.add_argument(
        "--report-type",
        "-t",
        default="research_report",
        choices=["research_report", "detailed_report", "quick_report", "deep"],
        help="Type of report to generate (default: research_report, deep=recursive exploration)",
    )

    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Use DetailedReport class with STORM methodology for hierarchical research",
    )

    parser.add_argument(
        "--config",
        help="Path to YAML config file (default: gpt_researcher_config.yaml if exists)",
    )

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Minimal output (just the result)"
    )

    parser.add_argument(
        "--auto-save",
        action="store_true",
        help="Automatically save output and logs with timestamp (default: True unless --output specified)",
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
        output_file = str(Path(log_dir) / f"gpt_researcher_output_{timestamp}.md")
        log_file = str(Path(log_dir) / f"gpt_researcher_log_{timestamp}.txt")
        print("Auto-save enabled:")
        print(f"  Output: {output_file}")
        print(f"  Log: {log_file}")
        print()
    elif auto_save and args.output:
        # If user specified output file, also create log file
        output_path = Path(args.output)
        output_file = str(output_path)
        log_file = str(output_path.parent / (output_path.stem + "_log.txt"))
        print(f"Saving output to: {output_file}")
        print(f"Saving log to: {log_file}")
        print()

    # Determine config file path
    config_path = args.config
    if not config_path:
        # Check if default config exists (JSON format for 0.14.5)
        default_config = Path(__file__).parent / "gpt_researcher_config.json"
        if default_config.exists():
            config_path = str(default_config)

    # Run the research
    result, _err = asyncio.run(
        run_research(
            prompt,
            model_spec=args.model,
            report_type=args.report_type,
            verbose=not args.quiet,
            use_detailed_report=args.detailed,
            config_path=config_path,
            log_file=log_file,
        )
    )

    if result:
        # Output to file or stdout
        if output_file:
            with open(output_file, "w") as f:
                f.write("# GPT-Researcher Results\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write(f"**Model:** {args.model}\n\n")
                f.write(f"**Prompt:** {prompt}\n\n")
                f.write("---\n\n")
                f.write(result)
            print(f"\nResults saved to: {output_file}")
            if log_file:
                print(f"Log saved to: {log_file}")
        else:
            if not args.quiet:
                print("\n" + "=" * 60)
                print("RESEARCH OUTPUT")
                print("=" * 60 + "\n")
            print(result)

        sys.exit(0)
    else:
        print("\nResearch failed. See error messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
