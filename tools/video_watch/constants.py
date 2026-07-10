"""Dependency-free constants for the video "watch" pipeline.

This module MUST remain importable without pulling in yt-dlp, ffmpeg,
httpx, or PIL — ``bridge/enrichment.py`` imports from here so the cheap
push-path enrichment never drags in the heavy pull-path pipeline
(``tools/video_watch/__init__.py``). Only ``os`` may be imported.

Both ``tools/video_watch/cli.py`` and ``bridge/enrichment.py`` import from
this module only; ``tools/video_watch/__init__.py`` re-exports these names
for backward-compatible internal use (single source of truth).
"""

from __future__ import annotations

import os

# The agent-facing command name, defined once so a naming reversal touches one
# place (reused by the enrichment signpost string).
WATCH_CLI_NAME = "valor-video-watch"

# --- Provisional/tunable constants -------------------------------------------
# All grain-of-salt: adopted from claude-video's balanced defaults, tuned against
# real usage. Each is env-overridable, mirroring MAX_VIDEO_DURATION in
# tools/link_analysis.

# Cap on frames emitted per video — bounds agent context/token cost.
VIDEO_WATCH_MAX_FRAMES = int(os.getenv("VIDEO_WATCH_MAX_FRAMES", "60"))
# Output frame width in px (height auto-scaled); 512 balances legibility vs tokens.
VIDEO_WATCH_FRAME_WIDTH = int(os.getenv("VIDEO_WATCH_FRAME_WIDTH", "512"))
# Only the first N seconds are processed; guards latency/token blowup on long clips.
VIDEO_WATCH_MAX_DURATION = int(os.getenv("VIDEO_WATCH_MAX_DURATION", "1800"))
# ffmpeg scene-change score threshold (0..1); higher = fewer, more distinct frames.
VIDEO_WATCH_SCENE_THRESHOLD = float(os.getenv("VIDEO_WATCH_SCENE_THRESHOLD", "0.3"))
# Near-duplicate dedup: mean-abs-diff over a 16x16 grayscale thumbnail, 0..255.
# Frames closer than this to the previous kept frame are dropped.
VIDEO_WATCH_DEDUP_THRESHOLD = float(os.getenv("VIDEO_WATCH_DEDUP_THRESHOLD", "6.0"))

# Shared subprocess timeout (seconds) for yt-dlp downloads and ffmpeg frame/audio
# extraction — the plan's documented name for all three. Grain of salt.
VIDEO_WATCH_SUBPROCESS_TIMEOUT = int(os.getenv("VIDEO_WATCH_SUBPROCESS_TIMEOUT", "600"))
# ffprobe's duration probe gets its own, deliberately much shorter timeout: it
# only reads a container header (no transcode/decode work), so 600s would mask
# a genuinely hung/corrupt probe far longer than necessary. Grain of salt.
VIDEO_WATCH_PROBE_TIMEOUT = int(os.getenv("VIDEO_WATCH_PROBE_TIMEOUT", "30"))

# HTTP timeout (seconds) for the single Grok X-context call. Grain of salt.
VIDEO_WATCH_GROK_TIMEOUT = float(os.getenv("VIDEO_WATCH_GROK_TIMEOUT", "60"))

# Provisional/tunable: transcripts shorter than this (chars, stripped) are treated
# as "thin" — likely music-only/silent/on-screen-only — and get a watch signpost
# in bridge/enrichment.py. Grain of salt; tune against real usage.
VIDEO_WATCH_THIN_TRANSCRIPT_CHARS = int(os.getenv("VIDEO_WATCH_THIN_TRANSCRIPT_CHARS", "80"))

# Age (seconds) after which a stale `video_watch_frames_*` temp dir is reaped by
# tools.video_watch.reap_stale_frame_dirs(). Default 24h. Grain of salt.
VIDEO_WATCH_FRAME_DIR_MAX_AGE = int(os.getenv("VIDEO_WATCH_FRAME_DIR_MAX_AGE", str(24 * 3600)))

# Upload ceiling (bytes) for the transcription request — Whisper rejects bodies
# over ~25 MB, a limit sized for AUDIO tracks (the plan's "~25 MB / ~30 min mono
# 16 kHz"), never the muxed video. Over-ceiling audio skips transcription with an
# explicit "[audio too long to transcribe — frames only]" note. Grain of salt.
VIDEO_WATCH_TRANSCRIBE_MAX_BYTES = int(
    os.getenv("VIDEO_WATCH_TRANSCRIBE_MAX_BYTES", str(25 * 1024 * 1024))
)
