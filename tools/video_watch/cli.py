"""CLI entry point for the frames-capable video "watch" tier.

Usage:
    valor-video-watch https://youtu.be/abc123
    valor-video-watch "https://x.com/user/status/123" "what is on the slide?"
    valor-video-watch --json https://youtu.be/abc123

Pull-based visual grounding: downloads a video (YouTube or X/Twitter), extracts
deduped scene-change frames + a transcript, and prints frame JPEG paths (with
``t=MM:SS`` markers) that the agent should ``Read`` image-by-image. For X links
it also emits Grok X-native context. Prefer this over ``WebFetch`` when the
question is visual or a YouTube transcript came back thin.
"""

import argparse
import asyncio
import json
import sys

from tools.video_watch import reap_stale_frame_dirs, watch_video
from tools.video_watch.constants import WATCH_CLI_NAME


def _format_human(result: dict) -> str:
    lines: list[str] = []
    lines.append(f"Source: {result.get('source')}")
    lines.append(f"URL: {result.get('url')}")

    frames = result.get("frames") or []
    if frames:
        lines.append("")
        lines.append(f"Frames ({len(frames)}) — Read each JPEG:")
        for fr in frames:
            lines.append(f"  t={fr['timestamp']}  {fr['path']}")
    else:
        lines.append("")
        lines.append("Frames: none")

    transcript = result.get("transcript")
    lines.append("")
    if transcript:
        lines.append("Transcript:")
        lines.append(transcript)
    else:
        lines.append("Transcript: none")

    grok = result.get("grok_context")
    if grok:
        lines.append("")
        lines.append("X-native context (Grok):")
        lines.append(grok)

    notes = result.get("notes") or []
    if notes:
        lines.append("")
        lines.append("Notes:")
        for n in notes:
            lines.append(f"  - {n}")

    return "\n".join(lines)


def main():
    """Main CLI entry point for valor-video-watch."""
    try:
        reap_stale_frame_dirs()
    except Exception as e:  # noqa: BLE001 -- reaper failure must never block a watch call
        print(f"Warning: stale frame-dir reap failed: {e}", file=sys.stderr)

    parser = argparse.ArgumentParser(
        prog=WATCH_CLI_NAME,
        description=(
            "Watch a video (YouTube or X/Twitter): extract deduped scene frames + "
            "transcript for visual grounding. Prefer over WebFetch for visual questions."
        ),
        usage=f"{WATCH_CLI_NAME} [--json] URL [QUESTION]",
    )
    parser.add_argument("url", help="Video URL (YouTube, x.com/twitter.com, or yt-dlp host)")
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Optional question to frame the watch (passed to Grok for X links)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the raw watch_video() dict as JSON",
    )

    args = parser.parse_args()

    url = (args.url or "").strip()
    if not url:
        print("Error: URL is required.", file=sys.stderr)
        sys.exit(1)

    try:
        result = asyncio.run(watch_video(url, args.question))
    except Exception as e:  # noqa: BLE001 -- surface unexpected failures cleanly
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
    else:
        print(_format_human(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
