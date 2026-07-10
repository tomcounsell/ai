"""Frames-capable "watch" package for video links (YouTube + X/Twitter).

Two-tier reaction to a video link (see docs/features/video-watch-visual-grounding.md):

- **Push default (elsewhere, unchanged):** ``bridge/enrichment.py`` transcript-only
  enrichment — cheap, fast, blind to on-screen content.
- **Pull "watch" tier (this package):** agent-invoked. Download a video (yt-dlp) →
  ffmpeg scene-change frame sampling with near-duplicate dedup → timestamped
  transcript (reusing ``tools.link_analysis.transcribe_audio_file``) → return
  frame JPEG paths + transcript (+ Grok X-native context for X links) that the
  agent ``Read``s image-by-image for real visual grounding.

Import discipline (load-bearing): this ``__init__`` is deliberately LIGHT —
stdlib plus the os-only ``constants`` module. ``bridge/enrichment.py`` imports
``tools.video_watch.constants``, which executes this ``__init__`` first
(Python always initializes the parent package), so anything imported eagerly
here loads at bridge import time. The heavy pull pipeline (yt-dlp/ffmpeg
orchestration, Pillow dedup, Whisper path, httpx Grok client) therefore lives
in ``tools/video_watch/pipeline.py`` and the stdlib-only frames-dir sweep in
``tools/video_watch/reaper.py``; both are loaded lazily via PEP 562
``__getattr__`` on first attribute access. Enforced by
``tools/video_watch/tests/test_import_discipline.py``.
"""

from __future__ import annotations

from tools.video_watch.constants import (
    VIDEO_WATCH_DEDUP_THRESHOLD,
    VIDEO_WATCH_FRAME_DIR_MAX_AGE,
    VIDEO_WATCH_FRAME_WIDTH,
    VIDEO_WATCH_GROK_TIMEOUT,
    VIDEO_WATCH_MAX_DURATION,
    VIDEO_WATCH_MAX_FRAMES,
    VIDEO_WATCH_PROBE_TIMEOUT,
    VIDEO_WATCH_SCENE_THRESHOLD,
    VIDEO_WATCH_SUBPROCESS_TIMEOUT,
    VIDEO_WATCH_THIN_TRANSCRIPT_CHARS,
    VIDEO_WATCH_TRANSCRIBE_MAX_BYTES,
    WATCH_CLI_NAME,
)

__all__ = [
    "WATCH_CLI_NAME",
    "VIDEO_WATCH_MAX_FRAMES",
    "VIDEO_WATCH_FRAME_WIDTH",
    "VIDEO_WATCH_MAX_DURATION",
    "VIDEO_WATCH_SCENE_THRESHOLD",
    "VIDEO_WATCH_DEDUP_THRESHOLD",
    "VIDEO_WATCH_SUBPROCESS_TIMEOUT",
    "VIDEO_WATCH_PROBE_TIMEOUT",
    "VIDEO_WATCH_GROK_TIMEOUT",
    "VIDEO_WATCH_THIN_TRANSCRIPT_CHARS",
    "VIDEO_WATCH_TRANSCRIBE_MAX_BYTES",
    "VIDEO_WATCH_FRAME_DIR_MAX_AGE",
    "VideoWatchError",
    "detect_source",
    "watch_video",
    "reap_stale_frame_dirs",
]

# Public callables, mapped to the submodule that defines them. Loaded lazily
# (PEP 562) so `import tools.video_watch[.constants]` never pulls the heavy
# pipeline; `reaper` stays importable without the pipeline too.
_PIPELINE_ATTRS = frozenset({"VideoWatchError", "detect_source", "watch_video"})
_REAPER_ATTRS = frozenset({"reap_stale_frame_dirs"})


def __getattr__(name: str):
    if name in _PIPELINE_ATTRS:
        from tools.video_watch import pipeline

        return getattr(pipeline, name)
    if name in _REAPER_ATTRS:
        from tools.video_watch import reaper

        return getattr(reaper, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
