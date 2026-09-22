"""Local encoder weights step for ``/update`` (Backend.LOCAL_ENCODER, #3420).

Ensures the pinned encoder files (``config.models.LOCAL_ENCODER_FILES``)
are present under ``local_encoder_models_dir()`` with matching sha256, so a
landed ``LOCAL_ENCODER`` site serves locally instead of falling back to
Anthropic on every call. The fetch itself lives in
``scripts/download_local_encoder_models.py``; this module runs it as a
subprocess and returns a structured result. Every failure is non-fatal:
the router's Anthropic fallback answers until the next update, and
``python -m tools.doctor`` reports the row.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from config.models import LOCAL_ENCODER_FILES, local_encoder_models_dir, local_encoder_weights_state


@dataclass
class DownloadResult:
    """Result of the local encoder weights download/check."""

    success: bool
    action: str  # "downloaded", "skipped", "failed"
    models_dir: str | None = None
    error: str | None = None


def weights_verified(models_dir: Path | None = None) -> bool:
    """True when every pinned file is present with its pinned sha256."""
    state = local_encoder_weights_state(models_dir, LOCAL_ENCODER_FILES)
    return all(value == "ok" for value in state.values())


def ensure_models(project_dir: Path) -> DownloadResult:
    """Run ``scripts/download_local_encoder_models.py`` unless the weights verify.

    The script is idempotent (skips verified files, re-fetches on a
    mismatch, deletes a ``.part`` whose digest is wrong and exits 1 naming
    both digests). Its non-zero exit is surfaced as ``failed`` with the
    script's stderr so the orchestrator can warn.
    """
    models_dir = local_encoder_models_dir()
    if weights_verified(models_dir):
        return DownloadResult(success=True, action="skipped", models_dir=str(models_dir))

    script = project_dir / "scripts" / "download_local_encoder_models.py"
    if not script.exists():
        return DownloadResult(
            success=False,
            action="failed",
            models_dir=str(models_dir),
            error=f"Download script not found: {script}",
        )

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=project_dir,
        )
    except subprocess.TimeoutExpired:
        return DownloadResult(
            success=False,
            action="failed",
            models_dir=str(models_dir),
            error="Download timed out after 600s",
        )
    except OSError as e:
        return DownloadResult(
            success=False,
            action="failed",
            models_dir=str(models_dir),
            error=f"Could not run download script: {e}",
        )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return DownloadResult(
            success=False,
            action="failed",
            models_dir=str(models_dir),
            error=f"Download failed: {error_msg}",
        )

    if weights_verified(models_dir):
        return DownloadResult(success=True, action="downloaded", models_dir=str(models_dir))

    return DownloadResult(
        success=False,
        action="failed",
        models_dir=str(models_dir),
        error="Download completed but the weights still fail sha256 verification",
    )
