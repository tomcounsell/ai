"""CLI entry point for YouTube video transcription.

Usage:
    valor-youtube-transcribe https://youtu.be/abc123
    valor-youtube-transcribe --json https://youtube.com/watch?v=abc123
    valor-youtube-transcribe --summary-only https://youtu.be/abc123

Wraps :func:`tools.link_analysis.process_youtube_url`. Captions-first with
Whisper fallback. Prefer this CLI over ``WebFetch`` for YouTube URLs --
YouTube serves anti-bot HTML to non-browser fetchers.
"""

import argparse
import asyncio
import json
import sys

from tools.link_analysis import process_youtube_url

NO_SUMMARY_NOTE = "# No summary available; full transcript below"


def _format_human(result: dict) -> str:
    """Format a successful result as human-readable text."""
    lines = []
    title = result.get("title")
    if title:
        lines.append(f"Title: {title}")
    video_id = result.get("video_id")
    if video_id:
        lines.append(f"Video ID: {video_id}")
    summary = result.get("summary")
    transcript = result.get("transcript") or ""
    if summary:
        lines.append("")
        lines.append("Summary:")
        lines.append(summary)
    else:
        lines.append("")
        lines.append("Transcript:")
        lines.append(transcript)
    return "\n".join(lines)


def _format_summary_only(result: dict) -> str:
    """Format for --summary-only mode.

    If summary exists, return it. Otherwise return the full transcript with
    a pinned one-line note (NO_SUMMARY_NOTE) so callers can detect the
    fallback deterministically.
    """
    summary = result.get("summary")
    if summary:
        return summary
    transcript = result.get("transcript") or ""
    return f"{NO_SUMMARY_NOTE}\n{transcript}"


def main():
    """Main CLI entry point for valor-youtube-transcribe."""
    parser = argparse.ArgumentParser(
        prog="valor-youtube-transcribe",
        description=(
            "Transcribe a YouTube video (captions-first, Whisper fallback). "
            "Prefer this over WebFetch for YouTube URLs."
        ),
        usage="valor-youtube-transcribe [--json | --summary-only] URL",
    )
    parser.add_argument(
        "url",
        help="YouTube video URL",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit raw process_youtube_url() dict as JSON",
    )
    output_group.add_argument(
        "--summary-only",
        action="store_true",
        dest="summary_only",
        help="Emit only the summary (or full transcript with a note if none)",
    )

    args = parser.parse_args()

    url = (args.url or "").strip()
    if not url:
        print("Error: URL is required.", file=sys.stderr)
        sys.exit(1)

    try:
        result = asyncio.run(process_youtube_url(url))
    except Exception as e:  # noqa: BLE001 -- surface unexpected failures
        print(f"Error: unexpected failure: {e}", file=sys.stderr)
        sys.exit(1)

    if not result.get("success"):
        error = result.get("error") or "Unknown error"
        if args.as_json:
            print(json.dumps(result, indent=2))
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    if args.as_json:
        print(json.dumps(result, indent=2))
    elif args.summary_only:
        print(_format_summary_only(result))
    else:
        print(_format_human(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
