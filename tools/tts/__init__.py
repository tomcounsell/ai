"""
Text-to-Speech Tool

Dual-backend audio synthesis:
1. Kokoro ONNX (primary) - local inference, free, ~330MB models
2. OpenAI tts-1 (fallback) - cloud API, paid per character

Returns OGG/Opus audio bytes suitable for delivery as Telegram voice messages.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

SUPPORTED_FORMATS = {"opus"}
MAX_TEXT_LENGTH = 4096  # OpenAI tts-1 hard limit; we apply uniformly to keep symmetry.

# Kokoro voice catalog (subset; full list lives in kokoro-onnx voices file).
KOKORO_VOICES: set[str] = {
    "af_bella",
    "af_nicole",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
}

# OpenAI tts-1 voice catalog (fixed: 6 voices as of 2026-04).
OPENAI_VOICES: set[str] = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

# Voice fallback remap table -- when the caller asks for a voice from one
# backend but dispatch picks the other, remap to a roughly equivalent voice.
# Keys are voices NOT in the destination backend; values are the destination
# voice the caller will get instead.
_VOICE_FALLBACK_MAP: dict[str, str] = {
    # Kokoro -> OpenAI
    "af_bella": "nova",
    "af_nicole": "nova",
    "af_sarah": "nova",
    "af_sky": "shimmer",
    "am_adam": "onyx",
    "am_michael": "echo",
    "bf_emma": "fable",
    "bf_isabella": "shimmer",
    "bm_george": "onyx",
    "bm_lewis": "echo",
    # OpenAI -> Kokoro (rough equivalents)
    "alloy": "af_bella",
    "echo": "am_michael",
    "fable": "bf_emma",
    "onyx": "am_adam",
    "nova": "af_bella",
    "shimmer": "af_sky",
}

KOKORO_DEFAULT_VOICE = "af_bella"
OPENAI_DEFAULT_VOICE = "nova"

# Default location for downloaded Kokoro ONNX models.
KOKORO_MODELS_DIR = Path(
    os.environ.get(
        "KOKORO_MODELS_DIR",
        os.path.expanduser("~/.cache/kokoro-onnx"),
    )
)
KOKORO_MODEL_FILENAME = "kokoro-v1.0.onnx"
KOKORO_VOICES_FILENAME = "voices-v1.0.bin"

# Cache for Kokoro availability check (mirrors transcribe's 60s window).
_kokoro_available_cache: dict[str, float | bool | str | None] = {
    "timestamp": 0.0,
    "available": False,
    "reason": None,
}
_KOKORO_CACHE_TTL = 60  # seconds

# Process-local flag so we only emit the WARN-level kokoro-unavailable line
# the first time we fall back to cloud in this process.
_kokoro_warn_emitted = False


# --- Availability check ------------------------------------------------------


def _is_kokoro_available() -> bool:
    """
    Check if the Kokoro backend can synthesize audio, with 60s caching.

    Two stages, both cached together:
      1. Static stage (always runs first): model files exist, kokoro_onnx
         importable, ffmpeg on PATH.
      2. Dynamic probe (only on first call within each 60s window): a
         one-character synthesis must return WAV bytes without raising.
         Catches ABI / accelerator regressions that the static stage misses.

    Returns True if both stages pass; False otherwise. Never raises.
    """
    now = time.time()
    if now - _kokoro_available_cache["timestamp"] < _KOKORO_CACHE_TTL:
        return bool(_kokoro_available_cache["available"])

    available, reason = _kokoro_static_check()
    if available:
        # Stage 2: dynamic probe (one-character synth).
        try:
            wav = _synthesize_kokoro_raw("a", KOKORO_DEFAULT_VOICE)
            if not wav or not isinstance(wav, bytes | bytearray):
                available = False
                reason = "dynamic probe returned empty bytes"
        except Exception as e:  # noqa: BLE001 -- intentional: never raise
            available = False
            reason = f"dynamic probe raised: {e}"

    _kokoro_available_cache["timestamp"] = now
    _kokoro_available_cache["available"] = available
    _kokoro_available_cache["reason"] = reason if not available else None

    if available:
        logger.debug("Kokoro is available")
    else:
        logger.debug(f"Kokoro is not available: {reason}")

    return available


def _kokoro_static_check() -> tuple[bool, str | None]:
    """Stage 1 of availability check. Returns (ok, reason_if_not_ok)."""
    model_path = KOKORO_MODELS_DIR / KOKORO_MODEL_FILENAME
    voices_path = KOKORO_MODELS_DIR / KOKORO_VOICES_FILENAME
    if not model_path.exists():
        return False, f"missing model file: {model_path}"
    if not voices_path.exists():
        return False, f"missing voices file: {voices_path}"

    try:
        import kokoro_onnx  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"kokoro_onnx import failed: {e}"

    if shutil.which("ffmpeg") is None:
        return False, "ffmpeg not on PATH"

    return True, None


# --- Voice resolution --------------------------------------------------------


def _resolve_voice(voice: str, backend: str) -> tuple[str | None, dict | None]:
    """Resolve a voice name against the selected backend.

    Returns (resolved_voice, error_dict). Exactly one will be non-None.

    - "default" maps to backend canonical (af_bella for kokoro, nova for cloud).
    - Caller's voice valid on selected backend -> use as-is.
    - Caller's voice valid on the OTHER backend -> remap via _VOICE_FALLBACK_MAP
      and emit an INFO log line.
    - Unknown to both backends -> error dict.
    """
    target_set = KOKORO_VOICES if backend == "kokoro" else OPENAI_VOICES
    other_set = OPENAI_VOICES if backend == "kokoro" else KOKORO_VOICES
    canonical = KOKORO_DEFAULT_VOICE if backend == "kokoro" else OPENAI_DEFAULT_VOICE

    if voice == "default":
        return canonical, None

    if voice in target_set:
        return voice, None

    if voice in other_set:
        remapped = _VOICE_FALLBACK_MAP.get(voice, canonical)
        logger.info(
            "tts.voice_remapped from=%s to=%s reason=backend_fallback",
            voice,
            remapped,
        )
        return remapped, None

    return None, {
        "error": (
            f"unknown voice: {voice!r}. "
            f"Available kokoro: {sorted(KOKORO_VOICES)}. "
            f"Available openai: {sorted(OPENAI_VOICES)}."
        )
    }


# --- Backend implementations ------------------------------------------------


def _synthesize_kokoro_raw(text: str, voice: str) -> bytes:
    """Run Kokoro ONNX inference and return WAV PCM bytes.

    Internal helper used both by the dynamic availability probe and by the
    public dispatch path. Raises on any failure -- callers are responsible
    for catching.
    """
    from io import BytesIO

    import soundfile as sf  # type: ignore[import-not-found]
    from kokoro_onnx import Kokoro  # type: ignore[import-not-found]

    model_path = KOKORO_MODELS_DIR / KOKORO_MODEL_FILENAME
    voices_path = KOKORO_MODELS_DIR / KOKORO_VOICES_FILENAME

    kokoro = Kokoro(str(model_path), str(voices_path))
    samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0, lang="en-us")

    buf = BytesIO()
    sf.write(buf, samples, sample_rate, format="WAV")
    return buf.getvalue()


def _synthesize_kokoro(text: str, voice: str, output_path: str) -> dict:
    """Synthesize via Kokoro -> WAV -> ffmpeg -> OGG/Opus on disk.

    Returns the standard result dict on success, or {"error": ...} on failure.
    """
    try:
        wav_bytes = _synthesize_kokoro_raw(text, voice)
    except Exception as e:  # noqa: BLE001
        return {"error": f"kokoro synthesis failed: {e}"}

    # Transcode WAV -> OGG/Opus via ffmpeg subprocess.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_tmp:
        wav_tmp.write(wav_bytes)
        wav_tmp_path = wav_tmp.name

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",  # overwrite output
                "-loglevel",
                "error",
                "-i",
                wav_tmp_path,
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                output_path,
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {"error": f"ffmpeg failed: {result.stderr.decode('utf-8', errors='ignore')}"}
    except subprocess.TimeoutExpired:
        return {"error": "ffmpeg transcode timed out"}
    except FileNotFoundError:
        return {"error": "ffmpeg not found on PATH"}
    finally:
        try:
            os.unlink(wav_tmp_path)
        except OSError:
            pass

    duration = _compute_duration_opus(output_path)
    return {
        "path": output_path,
        "duration": duration,
        "backend": "kokoro",
        "voice": voice,
        "format": "opus",
        "error": None,
    }


def _synthesize_openai(text: str, voice: str, output_path: str) -> dict:
    """Synthesize via OpenAI tts-1 with response_format='opus' (native OGG/Opus)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY environment variable not set"}

    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except Exception as e:  # noqa: BLE001
        return {"error": f"openai import failed: {e}"}

    try:
        client = OpenAI(api_key=api_key)
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="opus",
        )
        # New SDK exposes .iter_bytes(); older returns .content.
        if hasattr(response, "stream_to_file"):
            response.stream_to_file(output_path)
        else:
            data = getattr(response, "content", None)
            if data is None:
                data = b"".join(response.iter_bytes())
            with open(output_path, "wb") as f:
                f.write(data)
    except Exception as e:  # noqa: BLE001
        return {"error": f"openai tts request failed: {e}"}

    duration = _compute_duration_opus(output_path)
    return {
        "path": output_path,
        "duration": duration,
        "backend": "cloud",
        "voice": voice,
        "format": "opus",
        "error": None,
    }


# --- Duration helper ---------------------------------------------------------


def _compute_duration_opus(path: str) -> float:
    """Probe an OGG/Opus file's duration via ffprobe.

    Returns 0.0 if ffprobe is missing or the probe fails -- duration is a
    best-effort metadata field, not a correctness invariant.
    """
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            return 0.0
        return float(result.stdout.decode("utf-8", errors="ignore").strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        return 0.0


# --- Public API --------------------------------------------------------------


def synthesize(
    text: str,
    output_path: str,
    voice: str = "default",
    format: str = "opus",
    force_cloud: bool = False,
) -> dict:
    """Synthesize text to an OGG/Opus audio file.

    Args:
        text: Input text. Empty / whitespace-only / over MAX_TEXT_LENGTH yields error dict.
        output_path: Destination file path (will be overwritten).
        voice: Voice name. "default" picks the backend's canonical voice.
            Names from either backend are accepted; cross-backend fallbacks
            are remapped via _VOICE_FALLBACK_MAP.
        format: Output format. v1 only supports "opus".
        force_cloud: If True, skip Kokoro entirely and use OpenAI tts-1.

    Returns:
        Dict with keys:
          - path: output file path on success
          - duration: float seconds (0.0 if ffprobe missing)
          - backend: "kokoro" or "cloud"
          - voice: the voice actually used (post-remap)
          - format: "opus"
          - error: None on success, str on failure
    """
    # Input validation.
    if not text or not text.strip():
        return {"error": "text cannot be empty"}
    if len(text) > MAX_TEXT_LENGTH:
        return {"error": f"text too long: {len(text)} chars (max {MAX_TEXT_LENGTH})"}
    if format not in SUPPORTED_FORMATS:
        return {"error": (f"unsupported format: {format!r}. Only 'opus' supported in v1.")}

    # Backend selection.
    global _kokoro_warn_emitted
    use_kokoro = (not force_cloud) and _is_kokoro_available()
    if force_cloud:
        backend = "cloud"
        reason = "force_cloud"
    elif use_kokoro:
        backend = "kokoro"
        reason = "primary"
    else:
        backend = "cloud"
        reason = "kokoro_unavailable"

    # Voice resolution against selected backend.
    resolved_voice, voice_err = _resolve_voice(voice, backend)
    if voice_err is not None:
        return voice_err

    # Observability: structured INFO line per dispatch.
    logger.info(
        "tts.backend_selected backend=%s reason=%s voice=%s",
        backend,
        reason,
        resolved_voice,
    )
    if backend == "cloud" and reason == "kokoro_unavailable" and not _kokoro_warn_emitted:
        cause = _kokoro_available_cache.get("reason") or "unknown"
        logger.warning("tts.kokoro_unavailable falling back to cloud; cause=%s", cause)
        _kokoro_warn_emitted = True

    # Dispatch.
    if backend == "kokoro":
        result = _synthesize_kokoro(text, resolved_voice or "", output_path)
        if result.get("error"):
            # Synthesis-time failure: try cloud fallback if available.
            logger.info(
                "tts.backend_selected backend=cloud reason=kokoro_synth_error voice=%s",
                resolved_voice,
            )
            cloud_voice, cloud_err = _resolve_voice(voice, "cloud")
            if cloud_err is not None:
                return cloud_err
            return _synthesize_openai(text, cloud_voice or "", output_path)
        return result

    return _synthesize_openai(text, resolved_voice or "", output_path)


def get_supported_formats() -> set:
    """Return the set of supported output formats."""
    return SUPPORTED_FORMATS.copy()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m tools.tts 'text to speak' /tmp/out.ogg [voice]")
        sys.exit(1)

    in_text = sys.argv[1]
    out_path = sys.argv[2]
    in_voice = sys.argv[3] if len(sys.argv) > 3 else "default"

    out = synthesize(in_text, out_path, voice=in_voice)
    if out.get("error"):
        print(f"Error: {out['error']}")
        sys.exit(1)
    print(
        f"OK backend={out['backend']} voice={out['voice']} "
        f"duration={out['duration']:.2f}s -> {out['path']}"
    )
