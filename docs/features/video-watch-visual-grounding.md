# Video Watch Visual Grounding

**Status**: Implemented
**Implemented**: 2026-07-08

## Overview

Valor "reads" video links via transcript-only enrichment, but never "sees" them by default. This feature adds a second, agent-invoked tier: `valor-video-watch`, a CLI that downloads a video (YouTube or X/Twitter), extracts deduped scene-change frames, and transcribes the audio, then hands the frame JPEG paths to the agent to `Read` directly. This gives real visual grounding for content where the meaning is on screen and not in the audio: slide decks, product demos, UI walkthroughs, charts, silent or music-only clips, "as you can see here" narration.

## Two-Tier Model

There are two distinct reactions to a video link, and they must not be confused:

1. **Push tier (default, unchanged)** — `bridge/enrichment.py` step 2 auto-calls `tools.link_analysis.process_youtube_urls_in_text` for any bare YouTube URL in a message: captions first, Whisper fallback, GPT-4o-mini summary for long transcripts. This is cheap, fast, and runs on every message with a YouTube link. See [`docs/features/youtube-transcription.md`](youtube-transcription.md). **Frames are never attached on this path** — no token or latency regression on the common case.
2. **Pull tier (new, agent-invoked)** — the agent runs `valor-video-watch <url>` via Bash when it judges the question is visual, or when the push-tier transcript comes back thin (see the thin-transcript signpost below). This tier is strictly opt-in; nothing auto-escalates into it.

The push path only gains a cheap per-URL text signpost pointing at the pull path. It never gains frames.

## Source Coverage

`detect_source(url)` in `tools/video_watch/__init__.py` classifies a URL as one of:

- `youtube` — `youtube.com`, `youtu.be`, `youtube-nocookie.com`
- `x` — `twitter.com`, `x.com`, `mobile.twitter.com`, `mobile.x.com`
- `other` — any other yt-dlp-supported host, best-effort

YouTube and X share the exact same pipeline (yt-dlp download, ffmpeg frame extraction, Whisper transcript). They differ only in URL detection and in whether the Grok X-context step runs. This is deliberate: one pipeline, not two, so X support required no new download library.

## Pipeline

`watch_video(url, question=None, output_dir=None)` runs the following steps, all inside a `tempfile.TemporaryDirectory()` (see Temp-Dir Discipline below):

1. **Acquire** — `yt-dlp` downloads best video+audio into the temp work dir, merged to mp4. Bounded by `VIDEO_WATCH_SUBPROCESS_TIMEOUT` (ffmpeg extraction shares the same named timeout; the cheap `ffprobe` header read gets the much shorter `VIDEO_WATCH_PROBE_TIMEOUT`).
2. **Duration probe** — `ffprobe` checks total duration; if it exceeds `VIDEO_WATCH_MAX_DURATION`, a note is added that only the first N seconds were scanned.
3. **Frame extraction** — `ffmpeg` samples scene-change frames (`select='gt(scene,VIDEO_WATCH_SCENE_THRESHOLD)'`), scaled to `VIDEO_WATCH_FRAME_WIDTH` px wide, with a `metadata=print` sidecar recovering each frame's presentation timestamp.
4. **Dedup** — near-duplicate consecutive frames are dropped via a 16x16 grayscale thumbnail mean-absolute-difference comparison against the previously kept frame; frames closer than `VIDEO_WATCH_DEDUP_THRESHOLD` are discarded. Falls back to no-op if Pillow is unavailable.
5. **Subsample** — the deduped frame list is evenly subsampled down to `VIDEO_WATCH_MAX_FRAMES`, preserving temporal coverage across the clip rather than just taking the first N.
6. **Audio extraction** — `ffmpeg -vn -ac 1 -ar 16000 -b:a 64k` extracts a mono 16 kHz MP3 track from the merged video. The muxed mp4 is never uploaded: OpenAI Whisper's hard ~25 MB request ceiling is sized for audio, and 64 kbps MP3 keeps a full 30-minute clip at ~14 MB. Sources over `VIDEO_WATCH_MAX_DURATION`, or extracted audio over `VIDEO_WATCH_TRANSCRIBE_MAX_BYTES`, skip transcription with the explicit note `[audio too long to transcribe — frames only]`.
7. **Transcript** — the extracted audio track is transcribed via `tools.link_analysis.transcribe_audio_file` (OpenAI `whisper-1`). This is the same source-agnostic helper the push tier uses; it does not go through `process_youtube_url`, which is YouTube-only and rejects X URLs.
8. **Persist frames** — kept frames are copied out of the temp work dir into `output_dir` (default: a fresh `tempfile.mkdtemp(prefix="video_watch_frames_")`), named `frame_{i:03d}_{MM-SS}.jpg`. This copy step is what lets the frames survive past the `TemporaryDirectory` teardown.
9. **X-native context (X source only)** — after the temp-dir block closes, `fetch_x_context` is called for `x`-source URLs (see Grok's Role below).

The result dict has: `success`, `source`, `url`, `frames` (list of `{path, timestamp, seconds}`), `transcript`, `grok_context`, `notes` (degraded-mode messages), `error`.

### Temp-Dir Discipline

Two directories with different lifetimes are used on purpose:

- The **download/audio scratch dir** is a `with tempfile.TemporaryDirectory() as work:` block wrapping the whole acquire, extract, and transcribe sequence. A crash partway through (OOM, subprocess timeout, unexpected exception) still reclaims this dir on the way out — it cannot leak a multi-hundred-MB working directory.
- The **frames output dir** is created separately and deliberately outlives the `watch_video` call, because the agent `Read`s the JPEGs in a later, separate tool invocation. It is not context-managed. Stale `video_watch_frames_*` dirs older than `VIDEO_WATCH_FRAME_DIR_MAX_AGE` (default 24h) are swept by `reap_stale_frame_dirs()`, which runs non-fatally at every CLI start and is also registered with the hourly `agent-session-cleanup` reflection (`agent/session_health.py::cleanup_corrupted_agent_sessions`), bounding the disk leak on machines where the CLI is invoked rarely.

## Grok's Role (X links only)

`tools/video_watch/grok.py` is a thin xAI (Grok) client used only for X/Twitter URLs, via `fetch_x_context(url, question=None)`. Grok has first-party access to the X corpus, so for an X post it can report:

- The author (handle + display name)
- The full post text
- Thread/reply context
- A description of an attached video's on-screen content, when there is one

This serves two purposes: X-native context that a raw HTML fetch can't get (anti-bot markup), and a **fallback** for visual understanding when `yt-dlp` fails to download the clip (protected/age-gated/quote-tweet media).

**Grok is never used for frame vision.** Extracted frames always go to the agent (Claude) via `Read` — this is the model-agnostic technique this feature adopts from the `claude-video` `/watch` skill. Routing frames through Grok vision would be redundant and would vendor-couple visual grounding to xAI.

`GROK_API_KEY` is read directly via `os.getenv("GROK_API_KEY")` in `grok.py` — there is intentionally no `config.settings` / `APISettings` field for it. The settings sub-model's `env_nested_delimiter="__"` would bind a field to `API__GROK_API_KEY` rather than the plain `GROK_API_KEY` that is actually provisioned in the vault `.env`. This mirrors how `tools/link_analysis` reads `OPENAI_API_KEY` directly. A missing key logs a warning and returns `None` — `fetch_x_context` never raises; `watch_video` treats a `None` as "Grok context unavailable, degrade gracefully" and adds a note instead of failing the whole watch.

## Env-Tunable Constants

All provisional/tunable, each read via `os.getenv(NAME, default)` with a grain-of-salt comment in source, mirroring the `MAX_VIDEO_DURATION` convention in `tools/link_analysis`:

All live in the dependency-free `tools/video_watch/constants.py` (single source of truth — importable by `bridge/enrichment.py` without dragging in the heavy pipeline module):

| Constant | Default | Meaning |
|----------|---------|---------|
| `VIDEO_WATCH_MAX_FRAMES` | `60` | Cap on frames emitted per video (bounds agent context/token cost) |
| `VIDEO_WATCH_FRAME_WIDTH` | `512` | Output frame width in px (height auto-scaled) |
| `VIDEO_WATCH_MAX_DURATION` | `1800` (seconds) | Only the first N seconds of a video are processed; also the transcription duration ceiling |
| `VIDEO_WATCH_SCENE_THRESHOLD` | `0.3` | ffmpeg scene-change score threshold (0..1); higher = fewer, more distinct frames |
| `VIDEO_WATCH_DEDUP_THRESHOLD` | `6.0` | Mean-abs-diff (0..255) below which a frame is dropped as a near-duplicate |
| `VIDEO_WATCH_SUBPROCESS_TIMEOUT` | `600` (seconds) | Shared subprocess timeout for yt-dlp download and ffmpeg frame/audio extraction |
| `VIDEO_WATCH_PROBE_TIMEOUT` | `30` (seconds) | ffprobe duration-probe timeout (header read only, deliberately short) |
| `VIDEO_WATCH_GROK_TIMEOUT` | `60` (seconds) | HTTP timeout for the single Grok X-context call |
| `VIDEO_WATCH_TRANSCRIBE_MAX_BYTES` | `26214400` (25 MiB) | Extracted-audio upload ceiling (Whisper's hard request limit); over it, transcription is skipped with the "audio too long" note |
| `VIDEO_WATCH_FRAME_DIR_MAX_AGE` | `86400` (seconds) | Age after which a stale `video_watch_frames_*` dir is reaped |
| `VIDEO_WATCH_THIN_TRANSCRIPT_CHARS` | `80` | Transcript length below which the push tier appends the watch-tier signpost |

## Thin-Transcript Signpost

Inside `bridge/enrichment.py`'s existing `for r in youtube_results:` loop (push tier, step 2), each YouTube result's transcript is checked: if `len((r.get("transcript") or "").strip()) < VIDEO_WATCH_THIN_TRANSCRIPT_CHARS`, a per-URL hint is appended to the enriched text:

```
[transcript thin for <url> — run valor-video-watch <url> for visual grounding]
```

This gates on the `transcript` field **only**, never on `context`. `process_youtube_url` fills `context` with a non-empty string (e.g. `"[YouTube video: … transcript unavailable …]"`) even when transcription fails, while leaving `transcript` as `None`. Gating on `context` would either never fire or always fire depending on which failure string is checked, so the transcript-length check is the only reliable trigger. This signpost is additive text only — it never triggers frame extraction itself; the agent decides whether to act on it.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| `yt-dlp` on PATH | Video/audio download for both YouTube and X |
| `ffmpeg` on PATH | Scene-change frame extraction (`ffmpeg`) and duration probing (`ffprobe`) |
| `OPENAI_API_KEY` | Whisper (`whisper-1`) transcription |
| `GROK_API_KEY` (optional) | xAI X-native context + X media-description fallback; missing key degrades gracefully, X links still get frames + transcript when yt-dlp succeeds |

## Usage

```bash
# YouTube
valor-video-watch https://youtu.be/abc123

# X/Twitter, with a framing question passed through to Grok
valor-video-watch "https://x.com/user/status/123" "what is on the slide?"

# Machine-readable output
valor-video-watch --json https://youtu.be/abc123
```

Human-readable output lists each frame's path with a `t=MM:SS` marker, followed by the transcript, then the Grok X-context block (X only), then any degraded-mode notes. The agent is expected to `Read` each listed JPEG path image-by-image after invoking the CLI.

## Implementation Files

- `tools/video_watch/__init__.py` — `watch_video()` pipeline, `detect_source()`, provisional constants, `VideoWatchError`
- `tools/video_watch/grok.py` — `fetch_x_context()`, xAI client (OpenAI-compatible endpoint at `https://api.x.ai/v1`)
- `tools/video_watch/cli.py` — `valor-video-watch` CLI entry point (`main`), human/JSON output formatting
- `bridge/enrichment.py` — thin-transcript signpost, inside the YouTube URL transcription step
- Console script: `valor-video-watch = "tools.video_watch.cli:main"` in `pyproject.toml [project.scripts]`

## Related

- [YouTube Link Transcription](youtube-transcription.md) — the push-tier transcript-only path this feature escalates from
