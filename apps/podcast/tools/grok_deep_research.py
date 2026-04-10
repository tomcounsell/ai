#!/usr/bin/env python3
"""
Grok Deep Research API - Automated Research Script

This script uses xAI's Grok API (grok-3 model) to conduct comprehensive research
via the OpenAI-compatible chat completions endpoint.

Usage:
    python grok_deep_research.py "Your research prompt here"
    python grok_deep_research.py --file prompt.txt --output results.md

Requirements:
    - GROK_API_KEY in .env file (get at https://console.x.ai/)
    - pip install requests python-dotenv

API Documentation:
    https://docs.x.ai/docs
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Try to load dotenv, but don't fail if not installed
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# API endpoint
API_URL = "https://api.x.ai/v1/chat/completions"


def get_api_key() -> str | None:
    """Get API key from environment or .env file."""
    api_key = os.getenv("GROK_API_KEY")

    if not api_key:
        # Try loading from .env in parent directories
        for parent in [Path.cwd()] + list(Path.cwd().parents)[:3]:
            env_file = parent / ".env"
            if env_file.exists():
                with open(env_file) as f:
                    for line in f:
                        if line.startswith("GROK_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip("\"'")
                            break
                if api_key:
                    break

    return api_key


def get_headers(api_key: str) -> dict:
    """Build API request headers."""
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def make_logger(verbose: bool = True, log_file: str | None = None):
    """Create a logging function that writes to stdout and/or file."""

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    return log


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

# xAI pricing per token (placeholder rates - update when official pricing published)
# TODO: Update with actual xAI/Grok pricing when publicly available
PRICING = {
    "input_tokens": 3.0 / 1_000_000,  # $3/M (placeholder)
    "output_tokens": 15.0 / 1_000_000,  # $15/M (placeholder)
}


def calculate_cost(usage: dict) -> dict:
    """Calculate cost breakdown from usage data."""
    costs = {}
    total = 0.0
    for key, rate in PRICING.items():
        count = usage.get(key, 0) or 0
        cost = count * rate
        costs[key] = {"count": count, "cost": cost}
        total += cost
    costs["total"] = total
    return costs


def format_cost(costs: dict) -> str:
    """Format cost breakdown for display."""
    lines = ["Cost breakdown:"]
    for key, rate_label in [
        ("input_tokens", "Input tokens"),
        ("output_tokens", "Output tokens"),
    ]:
        entry = costs.get(key, {})
        count = entry.get("count", 0)
        cost = entry.get("cost", 0)
        if count > 0:
            lines.append(f"  {rate_label}: {count:,} = ${cost:.4f}")
    lines.append(f"  Total: ${costs.get('total', 0):.4f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def extract_metadata(result: dict) -> dict:
    """Extract structured metadata from API response."""
    meta = {
        "timestamp": datetime.now().isoformat(),
        "model": result.get("model", "grok-3"),
    }

    # Usage / tokens
    usage = result.get("usage", {})
    if usage:
        meta["usage"] = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
        meta["cost"] = calculate_cost(meta["usage"])

    return meta


def save_metadata(meta: dict, output_path: str):
    """Save metadata to JSON sidecar file next to the output."""
    meta_path = Path(output_path).with_suffix(".meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return str(meta_path)


# ---------------------------------------------------------------------------
# Core API function
# ---------------------------------------------------------------------------


def run_grok_research(
    prompt: str,
    timeout: int = 300,
    verbose: bool = True,
    log_file: str | None = None,
    max_retries: int = 3,
) -> tuple[str | None, dict]:
    """
    Submit a research request to xAI Grok API with retry logic.

    Returns:
        (content, response_dict) tuple. content is None on failure.
    """
    api_key = get_api_key()

    if not api_key:
        print("ERROR: GROK_API_KEY not found")
        print("Set it in your environment or .env file")
        print("Get your API key at: https://console.x.ai/")
        return None, {}

    log = make_logger(verbose, log_file)

    log("=" * 60)
    log("GROK DEEP RESEARCH API")
    log("=" * 60)
    log(f"\nPrompt: {prompt[:200]}..." if len(prompt) > 200 else f"\nPrompt: {prompt}")
    log("\nConfiguration:")
    log("  Model: grok-3")
    log(f"  Timeout: {timeout} seconds ({timeout // 60} minutes)")
    log("\nSubmitting research request...")
    log(f"Expected time: 15-60 seconds (but can take up to {timeout // 60} minutes)")
    log("-" * 60)

    payload = {
        "model": "grok-3",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    headers = get_headers(api_key)
    retry_delay = 5
    response = None

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                log(f"\nRetry attempt {attempt + 1}/{max_retries}...")
                log(f"Waiting {retry_delay} seconds before retry...")
                time.sleep(retry_delay)
                retry_delay *= 2

            response = requests.post(
                url=API_URL, json=payload, headers=headers, timeout=timeout
            )
            break

        except requests.exceptions.Timeout:
            log(
                f"WARNING: Request timed out after {timeout} seconds (attempt {attempt + 1}/{max_retries})"
            )
            if attempt < max_retries - 1:
                log("Retrying with longer wait time...")
                continue
            else:
                log("\nERROR: All retry attempts exhausted")
                log("The research query may be too complex. Try:")
                log("  - Simplifying the prompt")
                log("  - Increasing timeout with --timeout option")
                return None, {}

        except requests.exceptions.RequestException as e:
            log(f"ERROR: Request failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                continue
            else:
                log("\nERROR: All retry attempts failed")
                return None, {}
    else:
        log("ERROR: Request failed after all retry attempts")
        return None, {}

    if response.status_code == 200:
        try:
            result = response.json()

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]

                if "usage" in result:
                    usage = result["usage"]
                    log("\nAPI Usage:")
                    log(f"  Input tokens: {usage.get('prompt_tokens', 'N/A')}")
                    log(f"  Output tokens: {usage.get('completion_tokens', 'N/A')}")
                    log(f"  Total tokens: {usage.get('total_tokens', 'N/A')}")

                word_count = len(content.split())
                log(f"\n{'=' * 60}")
                log("RESEARCH COMPLETE")
                log(f"Length: ~{word_count} words")
                log(f"{'=' * 60}\n")

                return content, result
            else:
                logger.error(
                    "Grok API unexpected response format: %s",
                    json.dumps(result, indent=2)[:1000],
                )
                return None, result

        except json.JSONDecodeError:
            logger.error(
                "Failed to parse Grok API response as JSON: %s",
                response.text[:500],
            )
            return None, {}

    else:
        return None, _handle_error_response(response)


def _handle_error_response(response) -> dict:
    """Handle non-200 API responses with helpful messages.

    Returns a dict with ``_error_status``, ``_error_message``, and
    ``_error_body`` keys so callers can surface the failure details.
    """
    try:
        error_body = response.json()
    except Exception:
        error_body = response.text[:500]

    # Extract a human-readable message from the response body
    if isinstance(error_body, dict):
        raw_error = error_body.get("error")
        if isinstance(raw_error, dict):
            error_message = raw_error.get("message") or str(response.status_code)
        elif isinstance(raw_error, str):
            error_message = raw_error
        else:
            error_message = str(response.status_code)
    else:
        error_message = (
            str(error_body)[:200] if error_body else str(response.status_code)
        )

    if response.status_code == 401:
        logger.error(
            "Grok API authentication failed (401). "
            "API key may be invalid or expired. Response: %s",
            error_body,
        )
    elif response.status_code == 429:
        logger.error("Grok API rate limit exceeded (429). Response: %s", error_body)
    elif response.status_code == 500:
        logger.error("Grok API server error (500). Response: %s", error_body)
    else:
        logger.error(
            "Grok API returned status %d. Response: %s",
            response.status_code,
            error_body,
        )

    return {
        "_error_status": response.status_code,
        "_error_message": error_message,
        "_error_body": error_body,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Run Grok Deep Research on a topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s "Research the history of quantum computing"
    %(prog)s --file prompt.txt --output results.md
    %(prog)s --quiet "Quick research query"

Environment:
    GROK_API_KEY - Your xAI API key (required)
                   Get one at: https://console.x.ai/
        """,
    )

    parser.add_argument("prompt", nargs="*", help="Research prompt (or use --file)")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--output", "-o", help="Write output to file")

    # Output options
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    parser.add_argument(
        "--show-cost", action="store_true", help="Display cost breakdown"
    )
    parser.add_argument(
        "--auto-save",
        action="store_true",
        help="Automatically save output with timestamp (default when no --output)",
    )
    parser.add_argument(
        "--no-auto-save", action="store_true", help="Disable automatic file saving"
    )
    parser.add_argument(
        "--log-dir",
        help="Directory for output and log files (default: current directory)",
    )
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=300,
        help="Timeout in seconds (default: 300 = 5 minutes)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts (default: 3)",
    )

    args = parser.parse_args()

    # Get prompt
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

    # Determine auto-save behavior
    auto_save = not args.no_auto_save and (args.auto_save or not args.output)
    log_dir = args.log_dir or "."
    if log_dir != "." and not Path(log_dir).exists():
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    output_file = args.output
    log_file = None

    if auto_save and not args.output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(Path(log_dir) / f"grok_output_{timestamp}.md")
        log_file = str(Path(log_dir) / f"grok_log_{timestamp}.txt")
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

    content, result = run_grok_research(
        prompt,
        timeout=args.timeout,
        verbose=not args.quiet,
        log_file=log_file,
        max_retries=args.max_retries,
    )

    if content:
        meta = extract_metadata(result)

        if args.show_cost and "cost" in meta:
            print(f"\n{format_cost(meta['cost'])}")

        if output_file:
            with open(output_file, "w") as f:
                f.write("# Grok Deep Research Results\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write("**Model:** grok-3\n\n")
                f.write(f"**Prompt:** {prompt}\n\n")
                f.write("---\n\n")
                f.write(content)
            print(f"\nResults saved to: {output_file}")

            # Save metadata sidecar
            meta_path = save_metadata(meta, output_file)
            print(f"Metadata saved to: {meta_path}")

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
        print("\nFallback: Use browser at https://x.com/i/grok")
        sys.exit(1)


if __name__ == "__main__":
    main()
