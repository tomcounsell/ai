# Groq Whisper Backend

**Status**: Implemented
**Implemented**: 2026-07-13

## Overview

`tools/link_analysis/__init__.py`'s `transcribe_audio_file()` now prefers Groq's hosted `whisper-large-v3` (OpenAI-compatible endpoint) for audio transcription, falling back to OpenAI's `whisper-1` on Groq failure or when no Groq key is configured. This is a drop-in backend swap — no caller changes were needed, since both callers only invoke `transcribe_audio_file()` by symbol.

## Backend Selection Order

1. **Groq (`whisper-large-v3`)** — used when `GROQ_API_KEY` is set in the environment. Endpoint: `https://api.groq.com/openai/v1/audio/transcriptions`.
2. **OpenAI (`whisper-1`)** — used when `GROQ_API_KEY` is absent, or as an automatic fallback if the Groq request fails (non-200 response or exception). Endpoint: `https://api.openai.com/v1/audio/transcriptions`.
3. **`None`** — returned unchanged from before when neither `GROQ_API_KEY` nor `OPENAI_API_KEY` is set.

The fallback is per-call: a single Groq failure does not disable Groq for subsequent calls, and a `GROQ_API_KEY` that transiently fails doesn't require any config change to keep working via OpenAI.

## The `GROQ_API_KEY` Secret

`GROQ_API_KEY` is **optional**. Its absence is safe — `transcribe_audio_file()` reads it via `os.getenv("GROQ_API_KEY", "")` and silently falls through to the OpenAI path when it's empty.

To add it, follow this repo's standard secrets convention (see the top-level `CLAUDE.md` "Secrets" section):

1. Add `GROQ_API_KEY=...` to `~/Desktop/Valor/.env` (never to `repo/.env` directly — it's a symlink into the vault).
2. A placeholder already exists in `.env.example`:
   ```
   # Groq API key for whisper-large-v3 transcription (optional; falls back to OpenAI whisper-1 if unset)
   GROQ_API_KEY=gsk_****
   ```

No sync step is required beyond editing the vault `.env` — the symlink and env-completeness check pick it up automatically.

### `APISettings.groq_api_key` — validation only, not runtime-read

`config/settings.py` also has a nullable `groq_api_key` field on `APISettings`, included in the `validate_api_keys` validator list and the dict-export block. **This field exists purely for `.env` format validation and completeness checking** — it is not read by `transcribe_audio_file()`, which reads `os.getenv("GROQ_API_KEY", "")` directly (the same pattern `OPENAI_API_KEY` and `PERPLEXITY_API_KEY` use elsewhere in `tools/link_analysis`). Do not confuse the two: changing `APISettings.groq_api_key` has no effect on which transcription backend is used at runtime; only the environment variable does.

## Callers That Benefit

- **`tools/link_analysis`** — the YouTube push-tier transcription path (`process_youtube_url()` → `transcribe_audio_file()`), used when captions are unavailable. See [`youtube-transcription.md`](youtube-transcription.md).
- **`tools/video_watch`** (transitively) — the agent-invoked pull tier calls the same `tools.link_analysis.transcribe_audio_file` helper for its audio track. See [`video-watch-visual-grounding.md`](video-watch-visual-grounding.md).

Both callers inherit the new backend automatically with zero code changes, since they only reference `transcribe_audio_file` by symbol.

## Cost / Latency Rationale

Groq's hosted `whisper-large-v3` is materially cheaper and faster than OpenAI's `whisper-1` for the same task, at comparable transcription quality — the reason it's now the preferred backend rather than a peer option. The two backends share the same ~25 MB direct-upload file-size ceiling, so there is no size-regime regression: existing callers that were already sized for OpenAI's Whisper limit (e.g. `tools/video_watch`'s 64 kbps mono audio extraction) need no changes.

## Implementation Files

- `tools/link_analysis/__init__.py`:
  - `GROQ_TRANSCRIBE_URL`, `GROQ_WHISPER_MODEL`, `OPENAI_TRANSCRIBE_URL`, `OPENAI_WHISPER_MODEL` — provisional/tunable constants near the top of the module
  - `_post_transcription()` — shared multipart POST helper used by both backends
  - `transcribe_audio_file()` — backend-selection and fallback logic
- `config/settings.py` — `APISettings.groq_api_key` (validation/completeness only, see above)
- `.env.example` — `GROQ_API_KEY` placeholder

## Related

- [YouTube Link Transcription](youtube-transcription.md) — the push-tier caller
- [Video Watch Visual Grounding](video-watch-visual-grounding.md) — the pull-tier caller
