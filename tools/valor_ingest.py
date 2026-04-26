#!/usr/bin/env python3
"""On-demand markitdown ingestion CLI (``valor-ingest``).

Converts local files or URLs into `.md` sidecars that the knowledge
indexer can pick up. Two usage modes via a mutually exclusive argparse
group (per plan C2):

- Single source:  ``valor-ingest <path-or-url> [--vault-subdir <dir>]``
- Backfill scan: ``valor-ingest --scan <dir>`` (recursive)

Destination defaults:
    Local files — sidecar lands next to the source (``report.pdf`` →
    ``report.pdf.md`` in the same directory).
    URLs — default to CWD; the CLI prints a one-line hint encouraging
    ``--vault-subdir`` when ingesting into the vault.

YouTube URLs are deliberately delegated to the existing
``youtube-transcript-api`` path (via ``tools.valor_youtube_search``, per
plan N2) rather than markitdown's ``[youtube-transcription]`` extra,
which is excluded from our ``[knowledge]`` install.

Exit codes:
    0 — success
    1 — conversion failed
    2 — argparse error (default argparse behavior)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.knowledge.converter import (
    CONVERTIBLE_EXTENSIONS,
    ConversionError,
    convert_to_sidecar,
)

logger = logging.getLogger(__name__)


def _looks_like_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _is_youtube_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host in {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}


def _download_url_to_tempfile(url: str, *, dest_dir: Path) -> Path:
    """Fetch a URL into ``dest_dir`` and return the local path.

    Uses ``urllib.request`` — no new runtime dep. The filename comes from
    the URL path; content-type sniffing is intentionally not attempted
    because markitdown already handles extension-based dispatch.
    """
    parsed = urllib.parse.urlparse(url)
    filename = os.path.basename(parsed.path) or "downloaded.html"
    dest = dest_dir / filename
    req = urllib.request.Request(url, headers={"User-Agent": "valor-ingest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())
    return dest


def _handle_youtube_url(url: str, *, dest_dir: Path) -> Path:
    """Write a YouTube transcript markdown file into ``dest_dir``.

    Delegates to ``tools.valor_youtube_search`` / ``youtube-transcript-api``
    rather than carrying markitdown's `[youtube-transcription]` extra.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise ConversionError(
            "youtube-transcript-api not installed — cannot ingest YouTube URL"
        ) from exc

    parsed = urllib.parse.urlparse(url)
    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.lstrip("/")
    else:
        qs = urllib.parse.parse_qs(parsed.query)
        video_id = (qs.get("v") or [""])[0]
    if not video_id:
        raise ConversionError(f"could not extract video id from {url}")

    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as exc:
        raise ConversionError(f"YouTube transcript fetch failed for {url}: {exc}") from exc

    body = "\n".join(entry["text"] for entry in transcript)
    dest = dest_dir / f"youtube-{video_id}.md"
    dest.write_text(
        "---\n"
        f"source_url: {url}\n"
        f"video_id: {video_id}\n"
        "generated_by: youtube-transcript-api\n"
        "---\n\n"
        f"# YouTube Transcript: {video_id}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return dest


def _resolve_vault_dir(vault_subdir: str | None) -> Path:
    """Resolve ``--vault-subdir`` to an absolute vault path."""
    vault_root = Path(os.path.expanduser("~/work-vault"))
    if vault_subdir is None:
        return vault_root
    sub = Path(vault_subdir)
    if sub.is_absolute():
        return sub
    return vault_root / sub


def _ingest_single(
    source: str,
    *,
    vault_subdir: str | None,
    force: bool,
    output: str | None,
) -> Path | None:
    """Ingest a single source (local path or URL). Returns the sidecar path."""
    if _looks_like_url(source):
        # URL flow.
        if _is_youtube_url(source):
            dest_dir = _resolve_vault_dir(vault_subdir) if vault_subdir else Path.cwd()
            dest_dir.mkdir(parents=True, exist_ok=True)
            if vault_subdir is None:
                print(
                    f"Note: writing to {dest_dir}. Use --vault-subdir to ingest into the vault.",
                    file=sys.stderr,
                )
            return _handle_youtube_url(source, dest_dir=dest_dir)

        # Generic URL — download then hand to the converter.
        dest_dir = _resolve_vault_dir(vault_subdir) if vault_subdir else Path.cwd()
        dest_dir.mkdir(parents=True, exist_ok=True)
        if vault_subdir is None:
            print(
                f"Note: writing to {dest_dir}. Use --vault-subdir to ingest into the vault.",
                file=sys.stderr,
            )
        local_path = _download_url_to_tempfile(source, dest_dir=dest_dir)
        return convert_to_sidecar(local_path, force=force)

    # Local path flow.
    local_path = Path(source).expanduser().resolve()
    if not local_path.exists():
        raise ConversionError(f"source not found: {local_path}")

    if vault_subdir is not None:
        # Copy source into vault first, then convert in place.
        dest_dir = _resolve_vault_dir(vault_subdir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / local_path.name
        shutil.copy2(local_path, dest)
        local_path = dest

    sidecar = convert_to_sidecar(local_path, force=force)
    if sidecar is None:
        return None

    if output is not None:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sidecar), target)
        return target
    return sidecar


def _ingest_scan(root: str, *, force: bool) -> tuple[int, int, int]:
    """Recursively scan ``root`` and convert every convertible file.

    Returns (converted, skipped, failed).
    """
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ConversionError(f"--scan target is not a directory: {base}")

    converted = 0
    skipped = 0
    failed = 0

    for dirpath, dirnames, filenames in os.walk(base):
        # Skip hidden and _archive_ dirs, matching the watcher's filter.
        dirnames[:] = [
            d
            for d in dirnames
            if not (d.startswith(".") or (d.startswith("_") and d.endswith("_")))
        ]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in CONVERTIBLE_EXTENSIONS:
                continue
            source = Path(dirpath) / name
            try:
                sidecar = convert_to_sidecar(source, force=force)
            except ConversionError as exc:
                logger.warning("valor-ingest: %s: %s", source, exc)
                failed += 1
                continue
            except Exception as exc:
                logger.warning("valor-ingest: %s: unexpected error: %s", source, exc)
                failed += 1
                continue
            if sidecar is None:
                skipped += 1
            else:
                converted += 1
    return converted, skipped, failed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valor-ingest",
        description=(
            "Convert binary/non-markdown sources into `.md` sidecars for the "
            "knowledge pipeline. Supports local paths, URLs, and recursive "
            "directory scans."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "source",
        nargs="?",
        help="File path or URL to convert. Mutually exclusive with --scan.",
    )
    group.add_argument(
        "--scan",
        metavar="DIR",
        help=(
            "Recursively backfill every convertible file beneath DIR. "
            "Mutually exclusive with the `source` positional."
        ),
    )
    parser.add_argument(
        "--vault-subdir",
        metavar="PATH",
        help=(
            "Subdirectory under ~/work-vault/ where the sidecar lands. "
            "Ignored when used with --scan."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate sidecars even when the source hash is unchanged.",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Explicit sidecar output path (single source only).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Log conversion decisions at DEBUG level.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.scan is not None:
        if args.vault_subdir is not None:
            parser.error("--scan cannot be combined with --vault-subdir")
        if args.output is not None:
            parser.error("--scan cannot be combined with --output")
        try:
            converted, skipped, failed = _ingest_scan(args.scan, force=args.force)
        except ConversionError as exc:
            print(f"valor-ingest: {exc}", file=sys.stderr)
            return 1
        print(f"valor-ingest: {converted} converted, {skipped} skipped, {failed} failed")
        return 0 if failed == 0 else 1

    # Single-source path.
    try:
        sidecar = _ingest_single(
            args.source,
            vault_subdir=args.vault_subdir,
            force=args.force,
            output=args.output,
        )
    except ConversionError as exc:
        print(f"valor-ingest: {exc}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        # Specialize HTTPError (subclass of URLError) for clearer messaging
        # — surfaces the HTTP status code so users distinguish 404 from
        # network unreachable.
        print(
            f"valor-ingest: HTTP error fetching {args.source}: {exc.code} {exc.reason}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        # Offline / DNS / connection-refused / timeout. Reason can be an
        # OSError or a string; str() handles both cleanly.
        print(
            f"valor-ingest: Network error fetching {args.source}: {exc.reason}",
            file=sys.stderr,
        )
        return 1

    if sidecar is None:
        print(
            "valor-ingest: no sidecar generated "
            "(unconvertible extension, empty source, or hash unchanged)",
            file=sys.stderr,
        )
        return 0
    print(str(sidecar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
