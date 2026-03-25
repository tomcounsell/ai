# SuperWhisper Transcription

Dual-backend audio transcription with SuperWhisper (local macOS app) as the primary backend and OpenAI Whisper API as automatic fallback.

## Problem

Voice messages from Telegram were transcribed exclusively via the OpenAI Whisper API, incurring per-minute costs. SuperWhisper, a local macOS transcription app already installed on the dev machine, can handle the same work for free with lower latency.

## Architecture

```
Voice message arrives (Telegram)
        |
  bridge/media.py
  transcribe_voice()
        |
  tools/transcribe/__init__.py
  transcribe()
        |
   +----+----+
   |         |
SuperWhisper  OpenAI Whisper API
 (primary)     (fallback)
```

### Backend Selection

The `transcribe()` function implements a priority chain:

1. **Check SuperWhisper availability** -- cached `pgrep -x superwhisper` check with 60-second TTL
2. **If available** -- send audio via `open -g -a superwhisper <file>`, poll `~/Documents/superwhisper/recordings/` for a new folder containing `meta.json`
3. **If unavailable, times out (30s), or returns empty** -- fall back to OpenAI Whisper API
4. **Unified return format** -- both backends return `{"text": ..., "language": ..., "duration": ...}`

SuperWhisper is only used for basic JSON-format transcription. Requests for timestamps, verbose output, or non-JSON formats always route directly to OpenAI.

### Availability Caching

The `_is_superwhisper_available()` function caches the `pgrep` result for 60 seconds to avoid subprocess overhead on every transcription request. The cache is a module-level dict with `timestamp` and `available` fields.

## Key Files

| File | Role |
|------|------|
| `tools/transcribe/__init__.py` | Dual-backend transcription logic |
| `tools/transcribe/README.md` | Tool-level documentation |
| `tools/transcribe/manifest.json` | Tool metadata and capability declaration |
| `tools/transcribe/tests/test_transcribe.py` | 22 unit tests covering both backends |
| `bridge/media.py` | Bridge integration -- calls `tools.transcribe.transcribe()` |

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `SUPERWHISPER_RECORDINGS_DIR` | `~/Documents/superwhisper/recordings/` | Where SuperWhisper writes transcription results |
| `OPENAI_API_KEY` | (required for fallback) | OpenAI API key for the Whisper API backend |

## Bridge Integration

`bridge/media.py:transcribe_voice()` delegates to `tools.transcribe.transcribe()`. The bridge no longer makes direct OpenAI API calls for voice transcription -- all transcription logic is centralized in the tool module.

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| SuperWhisper not running | Falls back to OpenAI silently |
| SuperWhisper times out (30s) | Falls back to OpenAI with log message |
| SuperWhisper returns empty result | Falls back to OpenAI |
| OpenAI API key missing | Returns error dict with message |
| Unsupported audio format | Returns error before attempting either backend |
| File too large (>25MB) | Returns error before attempting either backend |

## Related

- [YouTube Transcription](youtube-transcription.md) -- video transcription via YouTube's own transcript API
- `tools/transcribe/README.md` -- detailed API reference and installation guide
