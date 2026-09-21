#!/usr/bin/env python3
"""Download the pinned local encoder weights (Backend.LOCAL_ENCODER, #3420).

Idempotent, checksum-verified fetch of ``config.models.LOCAL_ENCODER_FILES``
from the Hugging Face repo ``LOCAL_ENCODER_MODEL`` at revision
``LOCAL_ENCODER_REVISION``. Files land under ``local_encoder_models_dir()``
(``$LOCAL_ENCODER_MODELS_DIR``, default ``~/.cache/valor-encoder/``) with the
same relative path they have in the repo, so the int8 model is
``<dir>/onnx/model_int8.onnx`` and the tokenizer is ``<dir>/tokenizer.json``.
The cache is shared across every worktree on the machine.

Each file streams to a ``.part`` sibling and is renamed into place only
when its sha256 matches the pin. A present file whose digest matches is
skipped; one whose digest differs is re-downloaded; a download whose
digest still differs is deleted and the script exits 1 naming the expected
and actual digests. The leg never downloads (``agent/llm/backends/
local_encoder.py`` verifies and refuses); ``/update`` runs this script
through ``scripts/update/local_encoder.py::ensure_models``.

Usage:
    python scripts/download_local_encoder_models.py            # download
    python scripts/download_local_encoder_models.py --dry-run  # show plan
    python scripts/download_local_encoder_models.py --force    # re-download
"""

from __future__ import annotations

import argparse
import hashlib
import ssl
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.models import (  # noqa: E402
    LOCAL_ENCODER_FILES,
    LOCAL_ENCODER_MODEL,
    LOCAL_ENCODER_REVISION,
    local_encoder_models_dir,
)

HF_BASE = f"https://huggingface.co/{LOCAL_ENCODER_MODEL}/resolve/{LOCAL_ENCODER_REVISION}"


def source_url(filename: str) -> str:
    """The pinned-revision download URL for one file in ``LOCAL_ENCODER_FILES``."""
    return f"{HF_BASE}/{filename}"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, tmp: Path) -> None:
    """Stream-download ``url`` to ``tmp`` with coarse progress output."""
    tmp.parent.mkdir(parents=True, exist_ok=True)
    print(f"  -> {url}")
    print(f"     {tmp}")

    try:
        import certifi  # noqa: PLC0415

        _ssl_ctx: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        _ssl_ctx = None

    with urlopen(url, timeout=120, context=_ssl_ctx) as resp:  # noqa: S310 -- pinned public repo
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
                    if pct != last_pct and pct % 10 == 0:
                        print(
                            f"     {pct:3d}% ({downloaded / 1_048_576:.1f} MB / "
                            f"{total / 1_048_576:.1f} MB)"
                        )
                        last_pct = pct


def fetch(filename: str, expected: str, models_dir: Path, *, force: bool) -> bool:
    """Ensure ``models_dir / filename`` exists with sha256 ``expected``.

    Returns ``True`` on success. On a post-download mismatch the ``.part``
    file is deleted and both digests are printed to stderr.
    """
    dest = models_dir / filename
    if dest.exists() and not force:
        actual = sha256_of(dest)
        if actual == expected:
            size_mb = dest.stat().st_size / 1_048_576
            print(f"[skip] {filename} present, sha256 verified ({size_mb:.1f} MB)")
            return True
        print(f"[stale] {filename} sha256 {actual[:12]} != pinned {expected[:12]}; re-downloading")

    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        _download(source_url(filename), tmp)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        print(f"[fail] {filename}: {e}", file=sys.stderr)
        return False

    actual = sha256_of(tmp)
    if actual != expected:
        tmp.unlink(missing_ok=True)
        print(
            f"[fail] {filename}: sha256 mismatch after download\n"
            f"       expected {expected}\n"
            f"       actual   {actual}",
            file=sys.stderr,
        )
        return False

    tmp.replace(dest)
    print(f"[ok] {filename} downloaded, sha256 verified ({dest.stat().st_size / 1_048_576:.1f} MB)")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="download_local_encoder_models.py",
        description=(
            f"Idempotently fetch and checksum-verify the {LOCAL_ENCODER_MODEL} encoder files."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the planned destinations without downloading."
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if a verified file is present."
    )
    args = parser.parse_args(argv)

    models_dir = local_encoder_models_dir()
    print(f"Local encoder models directory: {models_dir}")
    print(f"Model: {LOCAL_ENCODER_MODEL} @ {LOCAL_ENCODER_REVISION}")

    if args.dry_run:
        for filename, expected in LOCAL_ENCODER_FILES.items():
            dest = models_dir / filename
            if dest.exists():
                state = (
                    "(present, verified, would skip)"
                    if sha256_of(dest) == expected
                    else "(present, sha256 mismatch, would re-download)"
                )
            else:
                state = "(would download)"
            print(f"  {filename} {state}")
            print(f"    src: {source_url(filename)}")
            print(f"    dst: {dest}")
        return 0

    ok = True
    for filename, expected in LOCAL_ENCODER_FILES.items():
        ok = fetch(filename, expected, models_dir, force=args.force) and ok
    if not ok:
        print(
            "If the network is unavailable, retry once connectivity is restored. Until then "
            "every LOCAL_ENCODER site falls back to Anthropic and doctor reports the row.",
            file=sys.stderr,
        )
        return 1

    print("\nLocal encoder weights ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
