# OpenRouter Whisper Backend

**Status**: Implemented
**Implemented**: 2026-07-13

## Overview

`tools/link_analysis/__init__.py`'s `transcribe_audio_file()` prefers OpenRouter's hosted `openai/whisper-large-v3` (OpenAI-compatible endpoint) for audio transcription, falling back to OpenAI's `whisper-1` on OpenRouter failure or when no OpenRouter key is configured. This is a drop-in backend — no caller changes are needed, since both callers only invoke `transcribe_audio_file()` by symbol.

## Backend Selection Order

1. **OpenRouter (`openai/whisper-large-v3`)** — used when `OPENROUTER_API_KEY` is set in the environment. Endpoint: `https://openrouter.ai/api/v1/audio/transcriptions`. OpenRouter routes Whisper Large V3 to fast hosted inference.
2. **OpenAI (`whisper-1`)** — used when `OPENROUTER_API_KEY` is absent, or as an automatic fallback if the OpenRouter request fails (non-200 response or exception). Endpoint: `https://api.openai.com/v1/audio/transcriptions`.
3. **`None`** — returned when neither `OPENROUTER_API_KEY` nor `OPENAI_API_KEY` is set.

The fallback is per-call: a single OpenRouter failure does not disable OpenRouter for subsequent calls, and an `OPENROUTER_API_KEY` that transiently fails doesn't require any config change to keep working via OpenAI.

## The `OPENROUTER_API_KEY` Secret

`transcribe_audio_file()` reads `OPENROUTER_API_KEY` via `os.getenv("OPENROUTER_API_KEY", "")` and silently falls through to the OpenAI path when it's empty.

To add it, follow this repo's standard secrets convention (see the top-level `CLAUDE.md` "Secrets" section):

1. Add `OPENROUTER_API_KEY=...` to `~/Desktop/Valor/.env` (never to `repo/.env` directly — it's a symlink into the vault).
2. A placeholder already exists in `.env.example`:
   ```
   # OpenRouter API Key (for model routing and whisper-large-v3 transcription; falls back to OpenAI whisper-1 if unset)
   OPENROUTER_API_KEY=sk-or-****
   ```

No sync step is required beyond editing the vault `.env` — the symlink and env-completeness check pick it up automatically.

### `APISettings.openrouter_api_key` — validation only, not runtime-read

`config/settings.py` also has a nullable `openrouter_api_key` field on `APISettings`, included in the `validate_api_keys` validator list and the dict-export block. **This field exists purely for `.env` format validation and completeness checking** — it is not read by `transcribe_audio_file()`, which reads `os.getenv("OPENROUTER_API_KEY", "")` directly (the same pattern `OPENAI_API_KEY` and `PERPLEXITY_API_KEY` use elsewhere in `tools/link_analysis`). Do not confuse the two: changing `APISettings.openrouter_api_key` has no effect on which transcription backend is used at runtime; only the environment variable does.

## Callers That Benefit

- **`tools/link_analysis`** — the YouTube push-tier transcription path (`process_youtube_url()` → `transcribe_audio_file()`), used when captions are unavailable. See [`youtube-transcription.md`](youtube-transcription.md).
- **`tools/video_watch`** (transitively) — the agent-invoked pull tier calls the same `tools.link_analysis.transcribe_audio_file` helper for its audio track. See [`video-watch-visual-grounding.md`](video-watch-visual-grounding.md).

Both callers inherit the backend automatically with zero code changes, since they only reference `transcribe_audio_file` by symbol.

## Cost / Latency Rationale

OpenRouter's hosted `openai/whisper-large-v3` is materially cheaper and faster than OpenAI's `whisper-1` for the same task, at comparable transcription quality — the reason it's the preferred backend rather than a peer option. Both backends share the same ~25 MB direct-upload file-size ceiling, so there is no size-regime regression: existing callers already sized for OpenAI's Whisper limit (e.g. `tools/video_watch`'s 64 kbps mono audio extraction) need no changes.

## Implementation Files

- `tools/link_analysis/__init__.py`:
  - `OPENROUTER_TRANSCRIBE_URL`, `OPENROUTER_WHISPER_MODEL`, `OPENAI_TRANSCRIBE_URL`, `OPENAI_WHISPER_MODEL` — provisional/tunable constants near the top of the module
  - `_post_transcription()` — shared multipart POST helper used by both backends
  - `transcribe_audio_file()` — backend-selection and fallback logic
- `config/settings.py` — `APISettings.openrouter_api_key` (validation/completeness only, see above)
- `.env.example` — `OPENROUTER_API_KEY` placeholder

## Related

- [YouTube Link Transcription](youtube-transcription.md) — the push-tier caller
- [Video Watch Visual Grounding](video-watch-visual-grounding.md) — the pull-tier caller
