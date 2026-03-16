#!/usr/bin/env python3
"""
Perplexity Deep Research API - Automated Research Script

This script uses Perplexity's Deep Research API (sonar-deep-research model) to conduct
comprehensive research with citations from authoritative sources.

Supports both synchronous (blocking) and asynchronous (fire-and-poll) API modes.

Usage:
    # Synchronous (default - blocking, waits for result)
    python perplexity_deep_research.py "Your research prompt here"
    python perplexity_deep_research.py --file prompt.txt --output results.md

    # Asynchronous (fire-and-poll)
    python perplexity_deep_research.py --async "Your research prompt here"
    python perplexity_deep_research.py --no-wait "prompt"  # returns job ID immediately
    python perplexity_deep_research.py --job-id abc123     # poll existing job
    python perplexity_deep_research.py --list-jobs         # list all async jobs

Requirements:
    - PERPLEXITY_API_KEY in .env file (get at https://www.perplexity.ai/settings/api)
    - pip install requests python-dotenv

API Documentation:
    Sync:  https://docs.perplexity.ai/api-reference/chat-completions-post
    Async: https://docs.perplexity.ai/api-reference/async-chat-completions-post
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


# API endpoints
SYNC_URL = "https://api.perplexity.ai/chat/completions"
ASYNC_URL = "https://api.perplexity.ai/async/chat/completions"


def get_api_key() -> str | None:
    """Get API key from environment or .env file."""
    api_key = os.getenv("PERPLEXITY_API_KEY")

    if not api_key:
        # Try loading from .env in parent directories
        for parent in [Path.cwd()] + list(Path.cwd().parents)[:3]:
            env_file = parent / ".env"
            if env_file.exists():
                with open(env_file) as f:
                    for line in f:
                        if line.startswith("PERPLEXITY_API_KEY="):
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

# Perplexity pricing per token (as of 2025)
PRICING = {
    "input_tokens": 2.0 / 1_000_000,  # $2/M
    "output_tokens": 8.0 / 1_000_000,  # $8/M
    "citation_tokens": 2.0 / 1_000_000,  # $2/M
    "reasoning_tokens": 3.0 / 1_000_000,  # $3/M
    "search_queries": 5.0 / 1_000,  # $5/1K
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
        ("citation_tokens", "Citation tokens"),
        ("reasoning_tokens", "Reasoning tokens"),
        ("search_queries", "Search queries"),
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
        "model": result.get("model", "sonar-deep-research"),
    }

    # Usage / tokens
    usage = result.get("usage", {})
    if usage:
        meta["usage"] = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "citation_tokens": usage.get("citation_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0),
            "search_queries": usage.get("num_search_queries", 0),
        }
        meta["cost"] = calculate_cost(meta["usage"])

    # Citations
    citations = result.get("citations", [])
    if citations:
        meta["citations"] = citations

    # Search results
    search_results = result.get("search_results", [])
    if search_results:
        meta["search_results"] = search_results

    return meta


def save_metadata(meta: dict, output_path: str):
    """Save metadata to JSON sidecar file next to the output."""
    meta_path = Path(output_path).with_suffix(".meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return str(meta_path)


# ---------------------------------------------------------------------------
# Async API functions
# ---------------------------------------------------------------------------


def submit_async_research(
    prompt: str,
    api_key: str,
    reasoning_effort: str = "high",
) -> dict:
    """
    Submit an async research job. Returns immediately with job info.

    Returns:
        dict with 'id', 'status', etc. from the API response
    """
    payload = {
        "request": {
            "model": "sonar-deep-research",
            "messages": [{"role": "user", "content": prompt}],
            "reasoning_effort": reasoning_effort,
        }
    }

    response = requests.post(
        ASYNC_URL,
        json=payload,
        headers=get_headers(api_key),
        timeout=30,
    )

    if response.status_code in (200, 201):
        return response.json()

    _handle_error_response(response)
    return {}


def poll_async_result(
    job_id: str,
    api_key: str,
    timeout: int = 600,
    poll_interval: int = 10,
    log=print,
) -> dict | None:
    """
    Poll for async job completion.

    Returns:
        Full API response dict when complete, or None on failure/timeout.
    """
    url = f"{ASYNC_URL}/{job_id}"
    headers = get_headers(api_key)
    start = time.time()

    while time.time() - start < timeout:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            log(f"ERROR: Poll request failed with status {response.status_code}")
            try:
                log(json.dumps(response.json(), indent=2))
            except Exception:
                log(response.text[:500])
            return None

        data = response.json()
        status = data.get("status", "UNKNOWN")
        elapsed = int(time.time() - start)

        if status == "COMPLETED":
            log(f"Job {job_id} completed after {elapsed}s")
            return data
        elif status == "FAILED":
            error_msg = data.get("error_message", "Unknown error")
            log(f"ERROR: Job {job_id} failed: {error_msg}")
            return None
        else:
            log(f"  [{elapsed}s] Status: {status}...")
            time.sleep(poll_interval)

    log(f"ERROR: Job {job_id} not complete after {timeout}s")
    return None


def list_async_jobs(api_key: str) -> list:
    """List all async jobs for the current user."""
    response = requests.get(
        ASYNC_URL,
        headers=get_headers(api_key),
        timeout=30,
    )
    if response.status_code == 200:
        data = response.json()
        return data.get("requests", [])
    else:
        print(f"ERROR: Failed to list jobs (status {response.status_code})")
        return []


def extract_async_content(data: dict) -> tuple[str | None, dict]:
    """
    Extract content and metadata from an async API response.

    Returns:
        (content_text, full_response_for_metadata)
    """
    response_obj = data.get("response", {})
    if not response_obj:
        return None, data

    choices = response_obj.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content")
        return content, response_obj

    return None, data


# ---------------------------------------------------------------------------
# Sync API function (existing, preserved)
# ---------------------------------------------------------------------------


def run_perplexity_research(
    prompt: str,
    reasoning_effort: str = "high",
    verbose: bool = True,
    log_file: str | None = None,
    timeout: int = 600,
    max_retries: int = 3,
) -> tuple[str | None, dict]:
    """
    Submit a research request to Perplexity Deep Research API (synchronous) with retry logic.

    Returns:
        (content, response_dict) tuple. content is None on failure.
    """
    api_key = get_api_key()

    if not api_key:
        print("ERROR: PERPLEXITY_API_KEY not found")
        print("Set it in your environment or .env file")
        print("Get your API key at: https://www.perplexity.ai/settings/api")
        return None, {}

    log = make_logger(verbose, log_file)

    log("=" * 60)
    log("PERPLEXITY DEEP RESEARCH API (sync)")
    log("=" * 60)
    log(f"\nPrompt: {prompt[:200]}..." if len(prompt) > 200 else f"\nPrompt: {prompt}")
    log("\nConfiguration:")
    log("  Model: sonar-deep-research")
    log(f"  Reasoning Effort: {reasoning_effort}")
    log(f"  Timeout: {timeout} seconds ({timeout // 60} minutes)")
    log("\nSubmitting research request...")
    log(f"Expected time: 30-120 seconds (but can take up to {timeout // 60} minutes)")
    log("-" * 60)

    payload = {
        "model": "sonar-deep-research",
        "messages": [{"role": "user", "content": prompt}],
        "reasoning_effort": reasoning_effort,
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
                url=SYNC_URL, json=payload, headers=headers, timeout=timeout
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
                log("  - Using --reasoning-effort medium instead of high")
                log("  - Increasing timeout with --timeout option")
                log("  - Using --async mode (no client-side timeout)")
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
                    "Perplexity API unexpected response format: %s",
                    json.dumps(result, indent=2)[:1000],
                )
                return None, result

        except json.JSONDecodeError:
            logger.error(
                "Failed to parse Perplexity API response as JSON: %s",
                response.text[:500],
            )
            return None, {}

    else:
        _handle_error_response(response)
        return None, {}


def _handle_error_response(response):
    """Handle non-200 API responses with helpful messages."""
    try:
        error_body = response.json()
    except Exception:
        error_body = response.text[:500]

    if response.status_code == 401:
        logger.error(
            "Perplexity API authentication failed (401). "
            "API key may be invalid or expired. Response: %s",
            error_body,
        )
    elif response.status_code == 429:
        logger.error(
            "Perplexity API rate limit exceeded (429). Response: %s", error_body
        )
    elif response.status_code == 500:
        logger.error("Perplexity API server error (500). Response: %s", error_body)
    else:
        logger.error(
            "Perplexity API returned status %d. Response: %s",
            response.status_code,
            error_body,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Run Perplexity Deep Research on a topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Synchronous (default)
    %(prog)s "Research the history of quantum computing"
    %(prog)s --file prompt.txt --output results.md

    # Async: submit and wait for result
    %(prog)s --async "Research prompt here"

    # Async: submit and return job ID immediately
    %(prog)s --no-wait "Research prompt here"

    # Async: poll an existing job
    %(prog)s --job-id abc123 --output results.md

    # List all async jobs
    %(prog)s --list-jobs

Environment:
    PERPLEXITY_API_KEY - Your Perplexity API key (required)
                        Get one at: https://www.perplexity.ai/settings/api
        """,
    )

    parser.add_argument("prompt", nargs="*", help="Research prompt (or use --file)")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    parser.add_argument("--output", "-o", help="Write output to file")

    parser.add_argument(
        "--reasoning-effort",
        "-r",
        choices=["low", "medium", "high"],
        default="high",
        help="Computational effort level (default: high)",
    )

    # Async vs sync mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--async",
        dest="use_async",
        action="store_true",
        help="Use async API (submit, poll, return result)",
    )
    mode_group.add_argument(
        "--sync",
        dest="use_sync",
        action="store_true",
        help="Force synchronous API (default behavior)",
    )

    # Async-specific options
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Submit async job and return job ID immediately (implies --async)",
    )
    parser.add_argument(
        "--job-id", help="Poll an existing async job by ID (implies --async)"
    )
    parser.add_argument(
        "--list-jobs", action="store_true", help="List all async jobs for this API key"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Seconds between async poll attempts (default: 10)",
    )

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
        default=600,
        help="Timeout in seconds (default: 600 = 10 minutes)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retry attempts for sync mode (default: 3)",
    )

    args = parser.parse_args()

    # Determine mode
    is_async = (
        args.use_async or args.no_wait or args.job_id is not None or args.list_jobs
    )

    # -----------------------------------------------------------------------
    # List jobs (no prompt needed)
    # -----------------------------------------------------------------------
    if args.list_jobs:
        api_key = get_api_key()
        if not api_key:
            print("ERROR: PERPLEXITY_API_KEY not found")
            sys.exit(1)
        jobs = list_async_jobs(api_key)
        if not jobs:
            print("No async jobs found.")
            sys.exit(0)
        print(f"{'ID':<40} {'Status':<14} {'Model':<25} {'Created'}")
        print("-" * 100)
        for job in jobs:
            created = job.get("created_at", "")
            if isinstance(created, (int, float)):
                created = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"{job.get('id', 'N/A'):<40} {job.get('status', 'N/A'):<14} {job.get('model', 'N/A'):<25} {created}"
            )
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Poll existing job (no prompt needed)
    # -----------------------------------------------------------------------
    if args.job_id:
        api_key = get_api_key()
        if not api_key:
            print("ERROR: PERPLEXITY_API_KEY not found")
            sys.exit(1)

        log = make_logger(not args.quiet)
        log(f"Polling async job: {args.job_id}")

        data = poll_async_result(
            args.job_id,
            api_key,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            log=log,
        )
        if data is None:
            print("Failed to retrieve job result.")
            sys.exit(1)

        content, response_obj = extract_async_content(data)
        if not content:
            print("ERROR: Job completed but no content found in response")
            print(json.dumps(data, indent=2)[:2000])
            sys.exit(1)

        meta = extract_metadata(response_obj)
        _output_result(content, meta, args)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Need a prompt for submission (sync or async)
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # Async mode
    # -----------------------------------------------------------------------
    if is_async:
        api_key = get_api_key()
        if not api_key:
            print("ERROR: PERPLEXITY_API_KEY not found")
            sys.exit(1)

        log = make_logger(not args.quiet)
        log("=" * 60)
        log("PERPLEXITY DEEP RESEARCH API (async)")
        log("=" * 60)
        log(
            f"\nPrompt: {prompt[:200]}..."
            if len(prompt) > 200
            else f"\nPrompt: {prompt}"
        )
        log("\nConfiguration:")
        log("  Model: sonar-deep-research")
        log(f"  Reasoning Effort: {args.reasoning_effort}")
        log(f"  Mode: {'fire-and-forget' if args.no_wait else 'submit-and-poll'}")
        log("\nSubmitting async research request...")
        log("-" * 60)

        job_data = submit_async_research(prompt, api_key, args.reasoning_effort)
        if not job_data:
            print("ERROR: Failed to submit async job")
            sys.exit(1)

        job_id = job_data.get("id")
        status = job_data.get("status", "UNKNOWN")
        log("\nJob submitted successfully!")
        log(f"  Job ID: {job_id}")
        log(f"  Status: {status}")

        # --no-wait: just print job ID and exit
        if args.no_wait:
            print(f"\nJob ID: {job_id}")
            print(
                f"Poll later with: python {sys.argv[0]} --job-id {job_id} --output results.md"
            )
            sys.exit(0)

        # Otherwise, poll for completion
        log(
            f"\nPolling for results (timeout: {args.timeout}s, interval: {args.poll_interval}s)..."
        )
        data = poll_async_result(
            job_id,
            api_key,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            log=log,
        )
        if data is None:
            print("\nJob did not complete. Poll again later with:")
            print(f"  python {sys.argv[0]} --job-id {job_id} --output results.md")
            sys.exit(1)

        content, response_obj = extract_async_content(data)
        if not content:
            print("ERROR: Job completed but no content found in response")
            sys.exit(1)

        meta = extract_metadata(response_obj)

        word_count = len(content.split())
        log(f"\n{'=' * 60}")
        log("RESEARCH COMPLETE (async)")
        log(f"Length: ~{word_count} words")
        log(f"{'=' * 60}\n")

        _output_result(content, meta, args, prompt=prompt)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Sync mode (default)
    # -----------------------------------------------------------------------
    auto_save = not args.no_auto_save and (args.auto_save or not args.output)
    log_dir = args.log_dir or "."
    if log_dir != "." and not Path(log_dir).exists():
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    output_file = args.output
    log_file = None

    if auto_save and not args.output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(Path(log_dir) / f"perplexity_output_{timestamp}.md")
        log_file = str(Path(log_dir) / f"perplexity_log_{timestamp}.txt")
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

    content, result = run_perplexity_research(
        prompt,
        reasoning_effort=args.reasoning_effort,
        verbose=not args.quiet,
        log_file=log_file,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    if content:
        meta = extract_metadata(result)

        if args.show_cost and "cost" in meta:
            print(f"\n{format_cost(meta['cost'])}")

        if output_file:
            with open(output_file, "w") as f:
                f.write("# Perplexity Deep Research Results\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                f.write("**Model:** sonar-deep-research\n\n")
                f.write(f"**Reasoning Effort:** {args.reasoning_effort}\n\n")
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
        print("\nFallback: Use browser at https://www.perplexity.ai/")
        sys.exit(1)


def _output_result(content: str, meta: dict, args, prompt: str | None = None):
    """Write result content and metadata to file or stdout."""
    if args.show_cost and "cost" in meta:
        print(f"\n{format_cost(meta['cost'])}")

    # Determine output file
    auto_save = not args.no_auto_save and (args.auto_save or not args.output)
    log_dir = args.log_dir or "."
    output_file = args.output

    if auto_save and not args.output:
        if log_dir != "." and not Path(log_dir).exists():
            Path(log_dir).mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(Path(log_dir) / f"perplexity_output_{timestamp}.md")

    if output_file:
        with open(output_file, "w") as f:
            f.write("# Perplexity Deep Research Results\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write("**Model:** sonar-deep-research\n\n")
            f.write("**Mode:** async\n\n")
            if prompt:
                f.write(f"**Prompt:** {prompt}\n\n")
            f.write("---\n\n")
            f.write(content)
        print(f"\nResults saved to: {output_file}")

        meta_path = save_metadata(meta, output_file)
        print(f"Metadata saved to: {meta_path}")
    else:
        if not args.quiet:
            print("\n" + "=" * 60)
            print("RESEARCH OUTPUT")
            print("=" * 60 + "\n")
        print(content)


if __name__ == "__main__":
    main()
