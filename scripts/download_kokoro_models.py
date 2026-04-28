#!/usr/bin/env python3
"""Download Kokoro ONNX model + voice embeddings.

Idempotent fetch from the public Hugging Face mirror. Files land in
``$KOKORO_MODELS_DIR`` (default: ``~/.cache/kokoro-onnx/``) so they are
shared across all worktrees on the machine.

Usage:
    python scripts/download_kokoro_models.py            # download
    python scripts/download_kokoro_models.py --dry-run  # show plan
    python scripts/download_kokoro_models.py --force    # re-download
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.request import urlopen

# These mirror the constants in tools/tts/__init__.py.
MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"

# Canonical release assets published by the kokoro-onnx maintainer.
# The Hugging Face repo restructured into per-voice files in early 2026 and
# the consolidated `voices.bin` no longer resolves there (HTTP 404). The
# GitHub release tag below is the upstream's documented "model files" drop.
KOKORO_RELEASE_TAG = "model-files-v1.0"
GH_BASE = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/{KOKORO_RELEASE_TAG}"
DOWNLOAD_URLS = {
    MODEL_FILENAME: f"{GH_BASE}/kokoro-v1.0.onnx",
    VOICES_FILENAME: f"{GH_BASE}/voices-v1.0.bin",
}


def get_models_dir() -> Path:
    return Path(
        os.environ.get(
            "KOKORO_MODELS_DIR",
            os.path.expanduser("~/.cache/kokoro-onnx"),
        )
    )


def _download(url: str, dest: Path) -> None:
    """Stream-download ``url`` to ``dest`` with crude progress output."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    print(f"  -> {url}")
    print(f"     {dest}")

    with urlopen(url, timeout=120) as resp:  # noqa: S310 -- public mirror
        total = int(resp.headers.get("Content-Length", "0") or 0)
        chunk_size = 1024 * 256
        downloaded = 0
        last_pct = -1
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    if pct != last_pct and pct % 5 == 0:
                        print(
                            f"     {pct:3d}% ({downloaded / 1_048_576:.1f} MB / "
                            f"{total / 1_048_576:.1f} MB)"
                        )
                        last_pct = pct
    tmp.rename(dest)
    print(f"     done ({dest.stat().st_size / 1_048_576:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="download_kokoro_models.py",
        description="Idempotently fetch Kokoro ONNX model + voice files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned destinations without downloading.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files already exist.",
    )
    args = parser.parse_args(argv)

    models_dir = get_models_dir()
    print(f"Kokoro models directory: {models_dir}")

    if args.dry_run:
        for fname, url in DOWNLOAD_URLS.items():
            dest = models_dir / fname
            state = "(present, would skip)" if dest.exists() else "(would download)"
            print(f"  {fname} {state}")
            print(f"    src: {url}")
            print(f"    dst: {dest}")
        return 0

    for fname, url in DOWNLOAD_URLS.items():
        dest = models_dir / fname
        if dest.exists() and not args.force:
            size_mb = dest.stat().st_size / 1_048_576
            print(f"[skip] {fname} already present ({size_mb:.1f} MB)")
            continue
        try:
            _download(url, dest)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {fname}: {e}", file=sys.stderr)
            print(
                "If the network is unavailable, retry once connectivity is restored. "
                "Until then, tools/tts will fall back to OpenAI tts-1.",
                file=sys.stderr,
            )
            return 1

    print(
        "\nKokoro models ready. tools/tts will now use the local backend "
        "when ffmpeg is also installed (`brew install ffmpeg`)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
