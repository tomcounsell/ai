# TTS (Text-to-Speech)

Tracking issue: [#1136](https://github.com/tomcounsell/ai/issues/1136)

## What it is

A two-layer feature plus one composite skill, each siloed so any layer can be upgraded without changing the others:

1. `tools/tts/` — Python module with a stable `synthesize(text, output_path, voice="default")` API and a pluggable dual backend.
2. `valor-tts` — thin CLI wrapper exposed via `pyproject.toml [project.scripts]`.
3. `/do-debrief` — composite skill that synthesizes a text debrief and delivers it as a native Telegram voice message.

Output format is **OGG/Opus** — the format Telegram expects for native voice messages.

## Dual backend

| Priority | Backend       | When it is used                                                  |
|----------|---------------|------------------------------------------------------------------|
| 1        | Kokoro ONNX   | Local inference, free, ~330MB models on disk; needs `ffmpeg`.    |
| 2        | OpenAI tts-1  | Cloud fallback when Kokoro is unavailable; native OGG/Opus output. |

`tools/tts/__init__.py` mirrors `tools/transcribe/__init__.py` one-for-one: 60-second availability cache, error-as-dict convention, dispatch with synthesis-failure fallback. Future readers should see these as mirror images.

### Availability check (canonical definition)

`_is_kokoro_available()` runs two stages, both cached together for 60 seconds:

1. **Static stage (always first, cheap):** model files exist at `KOKORO_MODELS_DIR`, `kokoro_onnx` importable, `ffmpeg` on PATH.
2. **Dynamic probe (runs only if static passes AND the cache is cold):** a one-character `_synthesize_kokoro_raw("a", "af_bella")` must return WAV bytes without raising. This catches ABI/accelerator regressions that the static stage misses (e.g., onnxruntime breaks on a CPU it doesn't recognize).

Either stage failing marks Kokoro unavailable for the next 60 seconds and stores the root cause in `_kokoro_available_cache["reason"]`.

### Voice handling

The full Kokoro voice catalog (~40 voices) is in the upstream `voices.bin` file; `tools/tts/__init__.py:KOKORO_VOICES` ships a 10-voice subset known to work. OpenAI tts-1 has six fixed voices (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`).

`_resolve_voice(voice, backend)` runs a five-step algorithm:

1. `"default"` → backend canonical (`af_bella` for Kokoro, `nova` for cloud).
2. Voice valid on the selected backend → use as-is.
3. Voice valid on the *other* backend only → remap via `_VOICE_FALLBACK_MAP` and emit `tts.voice_remapped` at INFO.
4. Unknown to both → return `{"error": ...}` *without* calling either backend.

This closes the silent-mismatch gap where a Kokoro-only name would hit the cloud path and either fail at the API boundary or (worse) succeed with an unintended voice.

### Observability

Every dispatch emits one structured INFO line:

```
tts.backend_selected backend=<kokoro|cloud> reason=<primary|kokoro_unavailable|kokoro_synth_error|force_cloud> voice=<name>
```

The first cloud fallback in a process additionally emits a WARN-level line naming the root cause, so silent OpenAI spend is traceable:

```
tts.kokoro_unavailable falling back to cloud; cause=<reason>
```

## Voice-message delivery (bridge integration)

Telethon's default `send_file()` delivers OGG/Opus as a generic audio document, not a voice bubble. The relay's voice-note branch fixes this:

- `tools/valor_telegram.py` `send` subcommand now accepts `--voice-note` and `--cleanup-after-send`. With `--voice-note`, the CLI sets `voice_note: True` and `duration: <float>` on the Redis outbox payload (duration is computed once via `tools.tts._compute_duration_opus`, which probes via `ffprobe`).
- `bridge/telegram_relay.py:_send_queued_message` honors `voice_note` by calling `client.send_file(..., voice_note=True, attributes=[DocumentAttributeAudio(duration=N, voice=True, waveform=b"")])`. On any voice-send exception it falls back to the document-send path and logs a warning — the relay never crashes.
- `cleanup_file: True` tells the relay to `os.unlink(path)` after a successful send **or** after dead-letter placement on retry exhaustion. Wrapped in try/except so missing-file is harmless.

## Temp-file ownership (the load-bearing detail)

The relay is asynchronous with up to three retries over minutes. A naive synchronous cleanup by `/do-debrief` after pushing the payload would race the retry loop and hit the "file not found at send time" branch in `bridge/telegram_relay.py`.

So the contract is:

- `/do-debrief` creates a temp file via `mktemp -t debrief`, calls `valor-tts`, then calls `valor-telegram send --voice-note --cleanup-after-send --audio <path>` and exits.
- The CLI sets `cleanup_file: True` in the Redis payload.
- The relay is the **sole deleter** — on a successful send, or after the payload is moved to the dead-letter queue on retry exhaustion. `_safe_unlink()` swallows errors so cleanup never raises into the send path.
- If `valor-tts` itself raises before the payload is pushed, `/do-debrief` cleans up the partial file in a `finally` block. That is the only pre-push cleanup responsibility on the caller.

## Setup

### Cloud-only (default — works everywhere)

`OPENAI_API_KEY` set in `~/Desktop/Valor/.env`. No further setup. Synthesis costs are tiny (less than $0.02 per minute of audio).

### Local Kokoro (optional)

```bash
pip install -e '.[tts]'                        # kokoro-onnx + soundfile + onnxruntime
brew install ffmpeg                            # transcoder + ffprobe
python scripts/download_kokoro_models.py       # ~330MB into ~/.cache/kokoro-onnx/
```

Override the models directory by setting `KOKORO_MODELS_DIR` in the environment. The cache path is shared across all worktrees on a machine — not committed and not per-worktree.

## Usage

### Python

```python
from tools.tts import synthesize

result = synthesize("Hello world.", "/tmp/out.ogg")
if result["error"]:
    raise RuntimeError(result["error"])
print(result["backend"], result["duration"])
```

### CLI

```bash
valor-tts --text "Hello." --output /tmp/out.ogg
valor-tts --text "Hello." --output /tmp/out.ogg --voice af_bella
valor-tts --text "Hello." --output /tmp/out.ogg --force-cloud
```

### `/do-debrief`

```bash
DEBRIEF="Two-minute deploy debrief..."
OUT=$(mktemp -t debrief).ogg
valor-tts --text "$DEBRIEF" --output "$OUT" || { rm -f "$OUT"; exit 1; }
valor-telegram send --chat "Dev: Valor" --voice-note --cleanup-after-send --audio "$OUT"
```

The agent invokes this skill when the user asks for a spoken update.

## Why no `/tts` skill

`tools/transcribe/` ships no `/transcribe` skill — its CLI + README is the agent-facing surface. `tools/tts/` mirrors that pattern. Adding a `/tts` skill would be pure indirection that duplicates `tools/tts/README.md` with no behavior. If a need for one emerges later (multi-step TTS workflows that aren't debriefs), it can be added as a follow-up.

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| Kokoro never selected, only cloud calls | `_is_kokoro_available()` failing static or dynamic probe | Run `python scripts/download_kokoro_models.py`; `brew install ffmpeg`; `pip install -e '.[tts]'`. Check the WARN log line for the precise cause. |
| Voice message arrives as a generic audio document | `voice_note: True` not set in the Redis payload | Confirm `--voice-note` flag was passed to `valor-telegram send`. |
| `duration` always 0.0 | `ffprobe` missing | `brew install ffmpeg` (ffprobe ships with ffmpeg). |
| Unknown voice error | Voice name not in either catalog | Use `default` or pick from `tools/tts/README.md`. |
| Cloud spend higher than expected | Silent Kokoro fallback | Search logs for `tts.kokoro_unavailable` — the WARN line names the root cause. |
| Temp `.ogg` files accumulating in `$TMPDIR` | `cleanup_file` not set on payload OR relay crashed mid-send | Pass `--cleanup-after-send` from the CLI; OS reaps `$TMPDIR` periodically as a backstop. |

## Future work

- Enroll the optional `tests/integration/test_tts_debrief.py` (gated on `LIVE_TELEGRAM=1`) into `scripts/nightly_regression_tests.py` once the feature has been stable in production for one week — catches Telethon upgrade drift on the voice-note API without blocking feature merge.
- Optional `--voice-preview` flag on `valor-tts` to play back locally before sending (deferred from v1).

## Related files

- `tools/tts/__init__.py` — synthesize, dual-backend dispatch, availability cache, voice resolution
- `tools/tts/cli.py` — `valor-tts` entry point
- `tools/tts/README.md` — agent-facing reference
- `tools/tts/manifest.json` — backend declarations + capabilities
- `scripts/download_kokoro_models.py` — idempotent model fetch
- `bridge/telegram_relay.py` — `_send_queued_message` voice-note branch + `_safe_unlink` + DLQ cleanup
- `tools/valor_telegram.py` — `--voice-note` and `--cleanup-after-send` flags on `send`
- `.claude/skills/do-debrief/SKILL.md` — the only user-invocable composite
- Tests: `tools/tts/tests/`, `tests/unit/test_telegram_relay_voice_note.py`, `tests/unit/test_valor_telegram_voice_flag.py`
