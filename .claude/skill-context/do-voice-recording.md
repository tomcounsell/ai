# do-voice-recording context — this repo (ai)

The TTS CLI is **`valor-tts`**, registered in `~/src/ai/pyproject.toml` and on `PATH` only when that venv is active, so resolve it explicitly.

## Resolve the binary

```bash
TTS="$(command -v valor-tts || true)"
[ -z "$TTS" ] && [ -x "$HOME/src/ai/.venv/bin/valor-tts" ] && TTS="$HOME/src/ai/.venv/bin/valor-tts"
[ -z "$TTS" ] && { echo "valor-tts not found — is the ~/src/ai venv installed? Run /update there." >&2; exit 1; }
```

The repo lives at `~/src/ai` on every machine, and the binary's shebang points at its own venv, so the absolute path works from any cwd.

## Synthesize

```bash
OUT="${OUTPUT:-$(mktemp -t voice).ogg}"
"$TTS" --text "$TEXT" --output "$OUT" ${VOICE:+--voice "$VOICE"} ${FORCE_CLOUD:+--force-cloud} || {
    echo "Synthesis failed" >&2
    rm -f "$OUT"
    exit 1
}
echo "$OUT"
```

- `--text`: empty or over 4096 characters is rejected; split longer text.
- `--output`: OGG/Opus, overwritten if it exists.
- `--voice`: e.g. `af_bella`, `am_michael`, `nova`; `default` uses the backend's canonical voice, and names remap across backends. Catalog: `~/src/ai/tools/tts/README.md`.
- `--force-cloud`: use OpenAI tts-1 even when Kokoro is available.

## Delivery

To send as a Telegram voice note: `valor-telegram send --chat "<chat>" --voice-note --cleanup-after-send --audio "$OUT"` (the sanctioned `valor-telegram` exception; see the `telegram` skill).
