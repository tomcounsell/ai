#!/usr/bin/env python3
"""
Gemini Deep Research API - Automated Research Script

This script uses Google's Gemini Deep Research API (Interactions API) to conduct
comprehensive multi-step research on any topic.

Usage:
    python gemini_deep_research.py "Your research prompt here"
    python gemini_deep_research.py --file prompt.txt
    python gemini_deep_research.py --file prompt.txt --output results.md

Requirements:
    - GEMINI_API_KEY in .env file (get at https://aistudio.google.com/apikey)
    - pip install requests python-dotenv

API Documentation:
    https://ai.google.dev/gemini-api/docs/deep-research
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Try to load dotenv, but don't fail if not installed
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class GeminiQuotaError(Exception):
    """Raised when the Gemini API returns HTTP 429 (quota exceeded).

    This typically means the API key is on a free tier or has exhausted its
    quota.  Callers should catch this to provide actionable error messages
    directing the user to upgrade billing.
    """


def get_api_key() -> str | None:
    """Get API key from environment or .env file."""
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        # Try loading from .env in parent directories
        for parent in [Path.cwd()] + list(Path.cwd().parents)[:3]:
            env_file = parent / ".env"
            if env_file.exists():
                with open(env_file) as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip("\"'")
                            break
                if api_key:
                    break

    return api_key


def submit_research(api_key: str, prompt: str, stream: bool = False) -> dict | None:
    """
    Submit a research request to Gemini Deep Research API.

    Args:
        api_key: Google AI API key
        prompt: Research prompt/query
        stream: Whether to use streaming mode

    Returns:
        Response JSON or None if failed
    """
    base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    payload = {
        "input": prompt,
        "agent": "deep-research-pro-preview-12-2025",
        "background": True,
        "store": True,
    }

    if stream:
        headers["Accept"] = "text/event-stream"
        payload["stream"] = True
        del payload["background"]
        del payload["store"]

    try:
        response = requests.post(base_url, headers=headers, json=payload, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return None

    if response.status_code == 429:
        try:
            error_data = response.json()
            error_msg = error_data.get("error", {}).get("message", "Quota exceeded")
        except Exception:
            error_msg = response.text or "Quota exceeded"
        raise GeminiQuotaError(f"Gemini API quota exceeded (HTTP 429): {error_msg}")

    if response.status_code != 200:
        print(f"ERROR: API returned status {response.status_code}")
        try:
            error_data = response.json()
            print(f"Error details: {json.dumps(error_data, indent=2)}")
        except Exception:
            print(f"Response: {response.text}")
        return None

    return response.json()


def check_status(api_key: str, interaction_id: str) -> dict | None:
    """
    Check the status of a research interaction.

    Args:
        api_key: Google AI API key
        interaction_id: ID of the interaction to check

    Returns:
        Status response JSON or None if failed
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/interactions/{interaction_id}"

    try:
        response = requests.get(url, headers={"x-goog-api-key": api_key}, timeout=30)
    except requests.exceptions.RequestException as e:
        print(f"WARNING: Status check failed: {e}")
        return None

    if response.status_code != 200:
        print(f"WARNING: Status check returned {response.status_code}")
        return None

    return response.json()


def extract_output(result: dict) -> str:
    """Extract text content from research result."""
    outputs = result.get("outputs", [])
    text_parts = []

    for output in outputs:
        if output.get("type") == "text":
            text_parts.append(output.get("text", ""))

    return "\n".join(text_parts)


def run_gemini_research(
    prompt: str,
    poll_interval: int = 120,
    max_attempts: int = 30,
    verbose: bool = True,
    log_file: str | None = None,
) -> str | None:
    """
    Submit a research request and wait for completion.

    Args:
        prompt: Research prompt/query
        poll_interval: Seconds between status checks (default 120 = 2 min)
        max_attempts: Maximum polling attempts (default 30 = 60 min max)
        verbose: Whether to print progress messages

    Returns:
        Research report text or None if failed
    """
    api_key = get_api_key()

    if not api_key:
        print("ERROR: GEMINI_API_KEY not found")
        print("Set it in your environment or .env file")
        print("Get your API key at: https://aistudio.google.com/apikey")
        return None

    # Helper to log to both stdout and file
    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    if verbose or log_file:
        log("=" * 60)
        log("GEMINI DEEP RESEARCH API")
        log("=" * 60)
        log(
            f"\nPrompt: {prompt[:200]}..."
            if len(prompt) > 200
            else f"\nPrompt: {prompt}"
        )
        log("\nSubmitting research request...")

    # Submit the research request
    result = submit_research(api_key, prompt)

    if not result:
        return None

    interaction_id = result.get("id")

    if not interaction_id:
        print("ERROR: No interaction ID returned")
        if verbose:
            print(json.dumps(result, indent=2))
        return None

    if verbose or log_file:
        log("\nResearch started successfully!")
        log(f"Interaction ID: {interaction_id}")
        log(f"Status: {result.get('status', 'unknown')}")
        log("\nEstimated time: 3-10 minutes (max 60 minutes)")
        log(f"Polling every {poll_interval} seconds...")
        log("-" * 60)

    # Poll for completion
    start_time = time.time()

    for attempt in range(max_attempts):
        if verbose or log_file:
            elapsed = int(time.time() - start_time)
            log(
                f"\n[{time.strftime('%H:%M:%S')}] Status check #{attempt + 1} (elapsed: {elapsed}s)"
            )

        status_result = check_status(api_key, interaction_id)

        if not status_result:
            if verbose:
                print("Could not get status, retrying...")
            time.sleep(poll_interval)
            continue

        status = status_result.get("status")

        if verbose or log_file:
            log(f"Status: {status}")

        if status == "completed":
            research_text = extract_output(status_result)

            if research_text:
                elapsed = int(time.time() - start_time)
                if verbose or log_file:
                    log(f"\n{'=' * 60}")
                    log(f"RESEARCH COMPLETE (took {elapsed}s)")
                    log(f"{'=' * 60}\n")
                return research_text
            else:
                print("WARNING: Research completed but no text output found")
                if verbose:
                    print(json.dumps(status_result, indent=2))
                return None

        elif status == "failed":
            error = status_result.get("error", "Unknown error")
            print(f"ERROR: Research failed: {error}")
            return None

        else:
            # in_progress - wait and retry
            if attempt < max_attempts - 1:
                if verbose or log_file:
                    log(f"Research in progress. Waiting {poll_interval}s...")
                time.sleep(poll_interval)

    elapsed = int(time.time() - start_time)
    print(f"ERROR: Research timed out after {elapsed}s ({max_attempts} attempts)")
    return None


def run_streaming_research(prompt: str) -> str | None:
    """
    Run research with streaming output (real-time progress).

    Args:
        prompt: Research prompt/query

    Returns:
        Research report text or None if failed
    """
    api_key = get_api_key()

    if not api_key:
        print("ERROR: GEMINI_API_KEY not found")
        return None

    print("=" * 60)
    print("GEMINI DEEP RESEARCH API (Streaming)")
    print("=" * 60)
    print(
        f"\nPrompt: {prompt[:200]}..." if len(prompt) > 200 else f"\nPrompt: {prompt}"
    )
    print("\nStarting research with streaming output...\n")
    print("-" * 60)

    base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"

    try:
        response = requests.post(
            base_url,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json={
                "input": prompt,
                "agent": "deep-research-pro-preview-12-2025",
                "stream": True,
            },
            stream=True,
            timeout=3600,  # 60 minute timeout for streaming
        )
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return None

    if response.status_code != 200:
        print(f"ERROR: API returned status {response.status_code}")
        print(response.text)
        return None

    full_text = []

    try:
        for line in response.iter_lines():
            if not line:
                continue

            line = line.decode("utf-8")

            if not line.startswith("data: "):
                continue

            try:
                event_data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            event_type = event_data.get("type")

            if event_type == "content.delta":
                delta = event_data.get("delta", {})
                delta_type = delta.get("type")

                if delta_type == "text":
                    text = delta.get("text", "")
                    print(text, end="", flush=True)
                    full_text.append(text)
                elif delta_type == "thought_summary":
                    thought = delta.get("text", "")
                    print(f"\n[Thinking: {thought}]\n", flush=True)

            elif event_type == "interaction.complete":
                print(f"\n\n{'=' * 60}")
                print("RESEARCH COMPLETE")
                print("=" * 60)
                break

            elif event_type == "interaction.error":
                error = event_data.get("error", "Unknown error")
                print(f"\nERROR: {error}")
                return None

    except KeyboardInterrupt:
        print("\n\nResearch interrupted by user")
        return None

    return "".join(full_text) if full_text else None


def main():
    parser = argparse.ArgumentParser(
        description="Run Gemini Deep Research on a topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s "Research the history of quantum computing"
    %(prog)s --file research_prompt.txt
    %(prog)s --file prompt.txt --output results.md
    %(prog)s --stream "Analyze market trends in AI"

Environment:
    GEMINI_API_KEY - Your Google AI API key (required)
                       Get one at: https://aistudio.google.com/apikey
        """,
    )

    parser.add_argument("prompt", nargs="*", help="Research prompt (or use --file)")

    parser.add_argument("--file", "-f", help="Read prompt from file")

    parser.add_argument("--output", "-o", help="Write output to file")

    parser.add_argument(
        "--stream",
        "-s",
        action="store_true",
        help="Use streaming mode for real-time output",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=120,
        help="Seconds between status checks (default: 120)",
    )

    parser.add_argument(
        "--max-wait",
        type=int,
        default=60,
        help="Maximum wait time in minutes (default: 60)",
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
        output_file = str(Path(log_dir) / f"gemini_output_{timestamp}.md")
        log_file = str(Path(log_dir) / f"gemini_log_{timestamp}.txt")
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

    # Run the research
    if args.stream:
        result = run_streaming_research(prompt)
    else:
        max_attempts = (args.max_wait * 60) // args.poll_interval
        result = run_gemini_research(
            prompt,
            poll_interval=args.poll_interval,
            max_attempts=max_attempts,
            verbose=not args.quiet,
            log_file=log_file,
        )

    if result:
        # Output to file or stdout
        if output_file:
            with open(output_file, "w") as f:
                f.write("# Gemini Deep Research Results\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
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
        print("\nFallback: Use browser-based Gemini at https://gemini.google.com/")
        sys.exit(1)


if __name__ == "__main__":
    main()
