"""Heavy pull-pipeline for the frames-capable video "watch" tier.

Everything that drags in real dependencies lives here — yt-dlp/ffmpeg
subprocess orchestration, Pillow frame dedup, the Whisper transcription path
(``tools.link_analysis``), and the httpx-backed Grok client. The package
``__init__`` stays light (stdlib + ``constants`` only) and loads this module
lazily via PEP 562 ``__getattr__``, so ``bridge/enrichment.py`` can import
``tools.video_watch.constants`` without ever loading this module at bridge
import time.

Source-agnostic core: ``youtube`` and ``x``/``twitter`` differ only in URL
detection and whether the Grok X-context step runs. One pipeline, not two.

Frames go to the agent (Claude) via ``Read`` — the model-agnostic technique
vendored from the ``claude-video`` ``/watch`` skill. Grok is used only where it
is genuinely differentiated (first-party X post/thread context + X media
fallback), never for frame vision.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from tools.link_analysis import transcribe_audio_file
from tools.video_watch.constants import (
    VIDEO_WATCH_DEDUP_THRESHOLD,
    VIDEO_WATCH_FRAME_WIDTH,
    VIDEO_WATCH_MAX_DURATION,
    VIDEO_WATCH_MAX_FRAMES,
    VIDEO_WATCH_PROBE_TIMEOUT,
    VIDEO_WATCH_SCENE_THRESHOLD,
    VIDEO_WATCH_SUBPROCESS_TIMEOUT,
    VIDEO_WATCH_TRANSCRIBE_MAX_BYTES,
)
from tools.video_watch.grok import fetch_x_context

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_X_HOSTS = ("twitter.com", "x.com", "mobile.twitter.com", "mobile.x.com")


class VideoWatchError(Exception):
    """A watch operation failed in a way the CLI should surface (non-zero exit)."""


def detect_source(url: str) -> str:
    """Classify a URL as ``youtube`` | ``x`` | ``other``.

    ``other`` still runs the generic yt-dlp path (best-effort), but YouTube and
    X are the committed, tested surfaces.
    """
    lowered = (url or "").lower()
    host_match = re.search(r"https?://([^/]+)/?", lowered)
    host = host_match.group(1) if host_match else lowered
    host = host.split("@")[-1]  # strip any userinfo
    if any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS):
        return "youtube"
    if any(host == h or host.endswith("." + h) for h in _X_HOSTS):
        return "x"
    return "other"


def _probe_duration(video_path: Path) -> float | None:
    """Return media duration in seconds via ffprobe, or None if unknown."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=VIDEO_WATCH_PROBE_TIMEOUT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as e:
        logger.warning("ffprobe duration probe failed for %s: %s", video_path, e)
    return None


def _download_video(url: str, tmpdir: Path) -> Path:
    """Download the video into ``tmpdir`` with yt-dlp. Raises VideoWatchError on failure."""
    output_template = str(tmpdir / "source.%(ext)s")
    cmd = [
        "yt-dlp",
        "--format",
        "bestvideo*+bestaudio/best",
        "--merge-output-format",
        "mp4",
        "--output",
        output_template,
        "--no-warnings",
        "--quiet",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=VIDEO_WATCH_SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise VideoWatchError(
            f"yt-dlp download timed out after {VIDEO_WATCH_SUBPROCESS_TIMEOUT}s"
        ) from e
    except FileNotFoundError as e:
        raise VideoWatchError(
            "yt-dlp not installed. Install with: pip install yt-dlp (and ensure ffmpeg is on PATH)."
        ) from e

    if result.returncode != 0:
        raise VideoWatchError(f"yt-dlp download failed: {result.stderr.strip()[:400]}")

    candidates = sorted(tmpdir.glob("source.*"))
    videos = [p for p in candidates if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not videos:
        raise VideoWatchError("yt-dlp reported success but no video file was produced.")
    return videos[0]


def _extract_audio(video_path: Path, tmpdir: Path) -> Path:
    """Extract a mono 16 kHz MP3 audio track from ``video_path`` via ffmpeg.

    Whisper only needs the audio; handing it the full merged .mp4 wastes
    upload bandwidth and time. ``.mp3`` is in
    ``tools.link_analysis.transcribe_audio_file``'s known MIME map (avoids a
    silent MIME mis-type when uploaded to the Whisper API) and — unlike
    uncompressed WAV, whose 32 KB/s mono-16kHz stream crosses Whisper's ~25 MB
    request ceiling at ~13 minutes — 64 kbps MP3 keeps a full
    ``VIDEO_WATCH_MAX_DURATION`` (30 min) clip at ~14 MB, comfortably under it.
    """
    audio_path = tmpdir / "audio.mp3"
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=VIDEO_WATCH_SUBPROCESS_TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise VideoWatchError("ffmpeg audio extraction timed out") from e
    except FileNotFoundError as e:
        raise VideoWatchError(
            "ffmpeg not installed. Install ffmpeg and ensure it is on PATH."
        ) from e

    if result.returncode != 0:
        raise VideoWatchError(f"ffmpeg audio extraction failed: {result.stderr.strip()[:400]}")
    if not audio_path.exists():
        raise VideoWatchError("ffmpeg reported success but no audio file was produced.")
    return audio_path


def _extract_scene_frames(video_path: Path, tmpdir: Path) -> list[tuple[Path, float]]:
    """Extract scene-change frames with ffmpeg. Returns [(frame_path, timestamp_seconds)].

    Uses ``select='gt(scene,THRESH)'`` with a ``metadata=print`` sidecar to recover
    each kept frame's presentation timestamp. Processing is bounded to the first
    ``VIDEO_WATCH_MAX_DURATION`` seconds.
    """
    frames_dir = tmpdir / "frames"
    frames_dir.mkdir(exist_ok=True)
    meta_path = tmpdir / "frames_meta.txt"

    # `eq(n,0)+gt(scene,THRESH)`: `+` is OR in ffmpeg expressions, so the opening
    # frame is ALWAYS kept (scene detection never scores frame 0) plus every
    # scene-change frame. Guarantees >=1 frame even for a static/very-short clip.
    vf = (
        f"select='eq(n\\,0)+gt(scene,{VIDEO_WATCH_SCENE_THRESHOLD})',"
        f"metadata=print:file={meta_path},"
        f"scale={VIDEO_WATCH_FRAME_WIDTH}:-2"
    )
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-t",
        str(VIDEO_WATCH_MAX_DURATION),
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-vsync",
        "vfr",
        "-frame_pts",
        "true",
        "-qscale:v",
        "3",
        str(frames_dir / "frame_%05d.jpg"),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=VIDEO_WATCH_SUBPROCESS_TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise VideoWatchError("ffmpeg frame extraction timed out") from e
    except FileNotFoundError as e:
        raise VideoWatchError(
            "ffmpeg not installed. Install ffmpeg and ensure it is on PATH."
        ) from e

    if result.returncode != 0:
        raise VideoWatchError(f"ffmpeg frame extraction failed: {result.stderr.strip()[:400]}")

    frame_files = sorted(frames_dir.glob("frame_*.jpg"))
    timestamps = _parse_frame_timestamps(meta_path)

    paired: list[tuple[Path, float]] = []
    for idx, fp in enumerate(frame_files):
        ts = timestamps[idx] if idx < len(timestamps) else float(idx)
        paired.append((fp, ts))
    return paired


def _parse_frame_timestamps(meta_path: Path) -> list[float]:
    """Parse ordered ``pts_time`` values from an ffmpeg metadata=print sidecar."""
    timestamps: list[float] = []
    if not meta_path.exists():
        return timestamps
    try:
        text = meta_path.read_text(errors="ignore")
    except OSError:
        return timestamps
    for m in re.finditer(r"pts_time:([0-9.]+)", text):
        try:
            timestamps.append(float(m.group(1)))
        except ValueError:
            continue
    return timestamps


def _subsample(frames: list[tuple[Path, float]], cap: int) -> list[tuple[Path, float]]:
    """Evenly subsample frames down to ``cap`` (keeps temporal coverage)."""
    if cap <= 0 or len(frames) <= cap:
        return frames
    step = len(frames) / cap
    return [frames[int(i * step)] for i in range(cap)]


def _dedup_frames(frames: list[tuple[Path, float]]) -> list[tuple[Path, float]]:
    """Drop near-duplicate consecutive frames via 16x16 grayscale mean-abs-diff.

    Best-effort: if Pillow is unavailable, returns the frames unchanged.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow unavailable — skipping frame dedup")
        return frames

    def thumb(path: Path) -> list[int] | None:
        try:
            with Image.open(path) as im:
                small = im.convert("L").resize((16, 16))
                # "L" mode => one byte per pixel; tobytes() avoids the deprecated getdata().
                return list(small.tobytes())
        except Exception as e:  # noqa: BLE001 -- a bad frame shouldn't kill dedup
            logger.warning("Could not read frame for dedup %s: %s", path, e)
            return None

    kept: list[tuple[Path, float]] = []
    prev_sig: list[int] | None = None
    for path, ts in frames:
        sig = thumb(path)
        if sig is None:
            kept.append((path, ts))
            continue
        if prev_sig is not None:
            mad = sum(abs(a - b) for a, b in zip(prev_sig, sig, strict=False)) / len(sig)
            if mad < VIDEO_WATCH_DEDUP_THRESHOLD:
                continue  # near-duplicate of the last kept frame
        kept.append((path, ts))
        prev_sig = sig
    return kept


def _fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS (or HH:MM:SS beyond an hour)."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


async def watch_video(
    url: str,
    question: str | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Download a video, extract scene frames + transcript, and (for X) Grok context.

    Args:
        url: A video URL (YouTube, X/Twitter, or another yt-dlp-supported host).
        question: Optional human framing (does not change extraction; passed to Grok).
        output_dir: Where to persist emitted frame JPEGs. Defaults to a temp dir
            under the system temp; frames must OUTLIVE this call so the agent can
            ``Read`` them, so they are copied out of the working temp dir.

    Returns:
        Dict with keys:
            - success: bool
            - source: "youtube" | "x" | "other"
            - url: str
            - frames: list[{"path": str, "timestamp": str, "seconds": float}]
            - transcript: str | None (timestamp-prefixed when available)
            - grok_context: str | None (X links only)
            - notes: list[str] (degraded-mode / informational messages)
            - error: str | None
    """
    url = (url or "").strip()
    result: dict = {
        "success": False,
        "source": "other",
        "url": url,
        "frames": [],
        "transcript": None,
        "grok_context": None,
        "notes": [],
        "error": None,
    }
    if not url:
        result["error"] = "URL is required."
        return result

    source = detect_source(url)
    result["source"] = source

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="video_watch_frames_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    download_failed = False
    # The whole acquire→extract→transcribe sequence runs inside a TemporaryDirectory
    # so a mid-run crash (OOM/SIGKILL of a child) cannot leak a multi-hundred-MB dir.
    with tempfile.TemporaryDirectory(prefix="video_watch_work_") as work:
        workdir = Path(work)
        video_path: Path | None = None
        try:
            video_path = _download_video(url, workdir)
        except VideoWatchError as e:
            download_failed = True
            result["notes"].append(f"media acquisition failed: {e}")
            logger.warning("watch_video download failed for %s: %s", url, e)

        if video_path is not None:
            duration = _probe_duration(video_path)
            if duration and duration > VIDEO_WATCH_MAX_DURATION:
                result["notes"].append(
                    f"video is {_fmt_ts(duration)} long — only the first "
                    f"{_fmt_ts(VIDEO_WATCH_MAX_DURATION)} were scanned (sparse coverage)."
                )

            try:
                frames = _extract_scene_frames(video_path, workdir)
                frames = _dedup_frames(frames)
                frames = _subsample(frames, VIDEO_WATCH_MAX_FRAMES)
                for i, (fp, ts) in enumerate(frames):
                    dest = output_dir / f"frame_{i:03d}_{_fmt_ts(ts).replace(':', '-')}.jpg"
                    dest.write_bytes(fp.read_bytes())
                    result["frames"].append(
                        {"path": str(dest), "timestamp": _fmt_ts(ts), "seconds": ts}
                    )
                if not result["frames"]:
                    result["notes"].append(
                        "no scene frames extracted (very short or static video)."
                    )
            except VideoWatchError as e:
                result["notes"].append(f"frame extraction failed: {e}")
                logger.warning("watch_video frame extraction failed for %s: %s", url, e)

            # Transcript from an extracted audio track (never the raw merged
            # video) via link_analysis' Whisper path. Two independent ceilings
            # guard Whisper's hard ~25 MB request limit: duration beyond
            # VIDEO_WATCH_MAX_DURATION (mirrors the ~30min frames ceiling), and
            # a post-extraction byte check against VIDEO_WATCH_TRANSCRIBE_MAX_BYTES
            # (catches high-bitrate outliers the duration gate alone would miss).
            if duration and duration > VIDEO_WATCH_MAX_DURATION:
                result["notes"].append("[audio too long to transcribe — frames only]")
            else:
                try:
                    audio_path = _extract_audio(video_path, workdir)
                    if audio_path.stat().st_size > VIDEO_WATCH_TRANSCRIBE_MAX_BYTES:
                        result["notes"].append("[audio too long to transcribe — frames only]")
                    else:
                        transcript = await transcribe_audio_file(audio_path)
                        if transcript:
                            result["transcript"] = transcript
                        else:
                            result["notes"].append(
                                "no transcript (silent, music-only, or no OPENAI_API_KEY)."
                            )
                except VideoWatchError as e:
                    result["notes"].append(f"audio extraction failed: {e}")
                    logger.warning("watch_video audio extraction failed for %s: %s", url, e)
                except Exception as e:  # noqa: BLE001 -- transcript is best-effort
                    result["notes"].append(f"transcription failed: {e}")
                    logger.warning("watch_video transcription failed for %s: %s", url, e)

    # X-native Grok context (X source only): post/thread context + media fallback.
    if source == "x":
        grok_ctx = fetch_x_context(url, question)
        if grok_ctx:
            result["grok_context"] = grok_ctx
        elif download_failed:
            result["notes"].append(
                "X media could not be downloaded and Grok context is unavailable "
                "(no GROK_API_KEY or Grok call failed)."
            )
        else:
            result["notes"].append("Grok X-context unavailable (no GROK_API_KEY or call failed).")

    # Success = we produced SOMETHING useful (frames, transcript, or Grok context).
    if result["frames"] or result["transcript"] or result["grok_context"]:
        result["success"] = True
    else:
        result["error"] = (
            "Could not extract frames, transcript, or context from the URL. "
            + " ".join(result["notes"])
        ).strip()
    return result
