"""Kokoro ONNX model + voice download module.

Ensures the Kokoro TTS model files are present in ``$KOKORO_MODELS_DIR``
(default: ``~/.cache/kokoro-onnx/``) so the local TTS backend can serve
voice messages without falling back to OpenAI tts-1.

The actual download logic lives in ``scripts/download_kokoro_models.py``;
this module just invokes it as a subprocess so the orchestrator gets a
structured result. The download script is idempotent — it skips files
that already exist.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DownloadResult:
    """Result of Kokoro model download/check."""

    success: bool
    action: str  # "downloaded", "skipped", "failed"
    models_dir: str | None = None
    error: str | None = None


# Mirrors constants in scripts/download_kokoro_models.py + tools/tts/__init__.py.
MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"


def _models_dir() -> Path:
    return Path(
        os.environ.get(
            "KOKORO_MODELS_DIR",
            os.path.expanduser("~/.cache/kokoro-onnx"),
        )
    )


def ensure_models(project_dir: Path) -> DownloadResult:
    """Run ``scripts/download_kokoro_models.py`` to fetch missing files.

    The download script handles idempotence (skips present files) and
    cross-machine cache sharing (resolves ``$KOKORO_MODELS_DIR``). All
    failures are non-fatal — the TTS layer falls back to OpenAI tts-1
    when Kokoro is unavailable.
    """
    models_dir = _models_dir()
    model_path = models_dir / MODEL_FILENAME
    voices_path = models_dir / VOICES_FILENAME

    if model_path.exists() and voices_path.exists():
        return DownloadResult(
            success=True,
            action="skipped",
            models_dir=str(models_dir),
        )

    script = project_dir / "scripts" / "download_kokoro_models.py"
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
        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return DownloadResult(
                success=False,
                action="failed",
                models_dir=str(models_dir),
                error=f"Download failed: {error_msg}",
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

    # Verify both files exist after the run.
    if model_path.exists() and voices_path.exists():
        return DownloadResult(
            success=True,
            action="downloaded",
            models_dir=str(models_dir),
        )

    return DownloadResult(
        success=False,
        action="failed",
        models_dir=str(models_dir),
        error="Download completed but model/voice files still missing",
    )
