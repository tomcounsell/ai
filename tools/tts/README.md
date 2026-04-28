# Text-to-Speech (TTS)

## Overview

Dual-backend text-to-speech tool. Uses **Kokoro ONNX** (local inference) as the primary backend for free, fast synthesis. Falls back to **OpenAI tts-1** when Kokoro is unavailable.

**Backends:**
1. **Kokoro ONNX** (primary) - Local inference, no API cost, ~330MB models on disk
2. **OpenAI tts-1** (fallback) - Cloud API, paid per character, native OGG/Opus output

**Capabilities**: `synthesize`

Output format is always **OGG/Opus** in v1 -- the format Telegram expects for native voice messages.

## How It Works

When `synthesize(text, output_path)` is called:

1. Check Kokoro availability: model files present + `kokoro_onnx` importable + `ffmpeg` on PATH + a one-character dynamic probe must succeed (cached 60s).
2. If Kokoro available: run inference -> WAV bytes -> ffmpeg -> OGG/Opus on disk.
3. If Kokoro unavailable: call OpenAI tts-1 with `response_format="opus"` -> bytes written directly.
4. Return identical dict shape regardless of backend.

Backend selection emits one structured INFO log line per call (`tts.backend_selected`); the first cloud fallback in a process additionally emits a WARN-level `tts.kokoro_unavailable` so silent cloud spend is traceable.

## Quick Start

### Python

```python
from tools.tts import synthesize

result = synthesize("Hello world.", "/tmp/out.ogg")
if result["error"]:
    print("failed:", result["error"])
else:
    print(f"backend={result['backend']} duration={result['duration']:.2f}s")
```

### CLI

```bash
valor-tts --text "Hello world." --output /tmp/out.ogg
valor-tts --text "Hello." --output /tmp/out.ogg --voice af_bella
valor-tts --text "Hello." --output /tmp/out.ogg --force-cloud
```

### Send as a Telegram voice message

```bash
valor-tts --text "Two-minute debrief on the deploy..." --output /tmp/debrief.ogg
valor-telegram send --chat "Dev: Valor" --voice-note --audio /tmp/debrief.ogg
```

The `--voice-note` flag adds `voice_note: True` and `duration` to the Redis outbox payload; the bridge relay calls Telethon with `voice_note=True` and a `DocumentAttributeAudio(voice=True)` attribute so the message arrives as a voice bubble (not an audio document).

## Installation

### Kokoro (Primary, optional)

```bash
pip install -U kokoro-onnx soundfile
brew install ffmpeg                 # required for transcoding + duration probe
python scripts/download_kokoro_models.py
```

Models are stored at `~/.cache/kokoro-onnx/` by default; override with the `KOKORO_MODELS_DIR` env var. Without these steps, the tool falls back to OpenAI tts-1 automatically.

### OpenAI tts-1 (Fallback)

Set the `OPENAI_API_KEY` environment variable. (`pip install openai` is already a dependency.)

## Voices

| Backend | Default | Catalog (subset) |
|---------|---------|------------------|
| Kokoro  | `am_michael` | `af_bella`, `af_nicole`, `af_sarah`, `af_sky`, `am_adam`, `am_michael`, `bf_emma`, `bf_isabella`, `bm_george`, `bm_lewis` |
| OpenAI  | `nova`     | `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer` |

`voice="default"` always resolves to the selected backend's canonical voice. If you pass a Kokoro-only voice and the cloud backend ends up being selected (or vice versa), the `_VOICE_FALLBACK_MAP` remaps to a roughly equivalent voice and emits an INFO log line (`tts.voice_remapped`).

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `KOKORO_MODELS_DIR` | `~/.cache/kokoro-onnx/` | Where Kokoro `.onnx` and `.bin` files live |
| `OPENAI_API_KEY`    | (required for fallback) | OpenAI API key |

## API Reference

### `synthesize(text, output_path, voice="default", format="opus", force_cloud=False)`

Returns a dict with:
- `path` -- output file path on success
- `duration` -- float seconds (0.0 if `ffprobe` is missing)
- `backend` -- `"kokoro"` or `"cloud"`
- `voice` -- the voice actually used (post-remap)
- `format` -- `"opus"`
- `error` -- `None` on success, `str` on failure

### `get_supported_formats()`

Returns `{"opus"}`.

## Bridge Integration

`bridge/telegram_relay.py:_send_queued_message` honors three optional payload fields added by this feature:

- `voice_note: True` -- send via Telethon with `voice_note=True` and a `DocumentAttributeAudio(voice=True)` attribute, so the message renders as a voice bubble
- `duration: <float seconds>` -- packed into `DocumentAttributeAudio(duration=...)`
- `cleanup_file: True` -- the relay unlinks the file after a successful send OR after dead-letter placement on retry exhaustion. The CLI sets this when invoked from the `/do-debrief` skill so the relay owns temp-file lifecycle across the async retry boundary.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Kokoro never selected | Run `python scripts/download_kokoro_models.py`; `brew install ffmpeg`; `pip install kokoro-onnx soundfile` |
| Voice arrives as audio document, not voice bubble | Confirm `voice_note: True` in the Redis payload (the `--voice-note` flag must be passed to `valor-telegram send`) |
| `duration` is always 0.0 | `ffprobe` missing -- it ships with `ffmpeg`; reinstall ffmpeg |
| `unknown voice` error | Pass a name from one of the catalogs above, or `default` |
| Cloud spend higher than expected | Search logs for `tts.kokoro_unavailable` -- the WARN line names the root cause |
