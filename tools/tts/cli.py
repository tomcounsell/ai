"""CLI wrapper for tools.tts.synthesize."""

from __future__ import annotations

import argparse
import sys

from tools.tts import synthesize


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``valor-tts``."""
    parser = argparse.ArgumentParser(
        prog="valor-tts",
        description=(
            "Synthesize text to OGG/Opus audio. "
            "Uses Kokoro ONNX locally when available, OpenAI tts-1 otherwise."
        ),
    )
    parser.add_argument(
        "--text",
        "-t",
        required=True,
        help="Text to synthesize. Empty / >4096 chars rejected.",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Destination OGG/Opus file path. Will be overwritten.",
    )
    parser.add_argument(
        "--voice",
        "-v",
        default="default",
        help=(
            "Voice name (e.g. af_bella, nova). "
            "Pass 'default' to use the selected backend's canonical voice. "
            "Cross-backend voices remap automatically."
        ),
    )
    parser.add_argument(
        "--force-cloud",
        action="store_true",
        help="Skip Kokoro and use OpenAI tts-1 even if Kokoro is available.",
    )

    args = parser.parse_args(argv)

    result = synthesize(
        text=args.text,
        output_path=args.output,
        voice=args.voice,
        force_cloud=args.force_cloud,
    )

    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    print(
        f"OK backend={result['backend']} voice={result['voice']} "
        f"duration={result['duration']:.2f}s -> {result['path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
