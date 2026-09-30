"""CLI wrapper for the narrated deck-video compositor (``valor-deck-video``)."""

from __future__ import annotations

import argparse
import sys

from tools.deck_video import (
    DeckVideoError,
    build_deck_video,
    check_prerequisites,
    parse_narration_blocks,
)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``valor-deck-video``.

    Guards prerequisites (ffmpeg, ffprobe, Marp CLI, valor-tts) BEFORE any
    work, emitting an actionable message + non-zero exit if any is missing.
    For an all-silent deck it still produces a video-only MP4 but warns to
    stderr that no narration blocks were found (keep-and-warn, not drop-and-
    fail). Reports total runtime and the output path on success.
    """
    parser = argparse.ArgumentParser(
        prog="valor-deck-video",
        description=(
            "Render a narrated MP4 from a Marp deck. Each slide is held on "
            "screen for the duration of its <!-- narration: ... --> clip "
            "(synthesized via valor-tts); silent slides hold for a default "
            "duration. Slides + voiceover are muxed into one deck.mp4."
        ),
    )
    parser.add_argument(
        "deck",
        help="Path to the Marp markdown deck (with per-slide narration blocks).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Destination MP4 path. Defaults to <deck>.mp4 next to the deck.",
    )

    args = parser.parse_args(argv)

    # Prerequisite guard FIRST -- fail fast before export/synthesis.
    missing = check_prerequisites()
    if missing:
        print(
            "Error: missing required tool(s):\n  - " + "\n  - ".join(missing),
            file=sys.stderr,
        )
        return 1

    # Surface the all-silent case as a warning (keep-and-warn).
    try:
        with open(args.deck, encoding="utf-8") as f:
            blocks = parse_narration_blocks(f.read())
        if not any(b for b in blocks):
            print(
                "Warning: No narration blocks found; producing a video-only "
                "slideshow -- add <!-- narration: ... --> comments to narrate.",
                file=sys.stderr,
            )
    except OSError as e:
        print(f"Error: cannot read deck: {e}", file=sys.stderr)
        return 1

    try:
        output = build_deck_video(args.deck, output_path=args.output)
    except DeckVideoError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"OK -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
