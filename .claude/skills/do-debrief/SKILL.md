---
name: do-debrief
description: "Use when sending a spoken debrief to a Telegram chat. Synthesizes text to OGG/Opus and delivers as a native voice message. Triggered by 'send a voice debrief', 'speak this update', 'do-debrief', or any request to deliver an audio summary."
argument-hint: "<text> --chat <chat>"
allowed-tools: Bash
user-invocable: true
---

# /do-debrief — Spoken Debrief to Telegram

Compose a debrief, speak it, deliver it as a Telegram voice message. Uses `valor-tts` to synthesize OGG/Opus audio, then `valor-telegram send` to push the file through the bridge relay with `voice_note=True` so it arrives as a waveform bubble (not an audio document).

## When to use

- Status updates that are easier to listen to than read.
- Hands-free or accessibility scenarios.
- A spoken recap at the end of a long session.
- Any time you want the recipient's notification to play audio rather than show a wall of text.

## Inputs

- **text** (required) — The debrief content. Plain text, ≤4096 chars.
- **--chat** (required) — Target chat name (e.g. `"Dev: Valor"`) or numeric chat ID.
- **--voice** (optional) — Voice name from either backend. Defaults to the backend's canonical voice. See `tools/tts/README.md` for the catalog.
- **--reply-to** (optional) — Telegram message ID to reply to (required for forum-group topics).

## Three-step flow

1. **Synthesize.** Call `valor-tts` with the debrief text. Output goes to a fresh tempfile under `$TMPDIR` named `debrief_<uuid>.ogg`.
2. **Push to outbox.** Call `valor-telegram send --voice-note --cleanup-after-send --audio <path>`. The CLI sets `voice_note: True`, `duration: <float>`, and `cleanup_file: True` in the Redis outbox payload.
3. **Exit.** Do not delete the temp file. The relay owns it from the moment the payload is pushed -- it deletes after the successful send OR after the payload is moved to the dead-letter queue on retry exhaustion. Synchronous deletion would race the relay's retry loop.

If `valor-tts` itself fails before the payload is pushed, delete the partial tempfile in a `finally` block. That is the only pre-push cleanup responsibility on this skill.

## Example invocation

```bash
DEBRIEF_TEXT="Two-minute deploy debrief. Bridge restart succeeded; new memory \
hooks live; nightly tests green. Open question: do we want to flip the kokoro \
default voice to af_sky based on the latest user feedback?"

OUT=$(mktemp -t debrief).ogg

valor-tts --text "$DEBRIEF_TEXT" --output "$OUT" || {
    echo "Synthesis failed"
    rm -f "$OUT"
    exit 1
}

valor-telegram send \
    --chat "Dev: Valor" \
    --voice-note \
    --cleanup-after-send \
    --audio "$OUT"
```

A voice bubble appears in the chat; the relay deletes `$OUT` after delivery. No further action needed.

## Error handling

- **`valor-tts` exits non-zero** → STDERR carries `Error: <message>`. Surface it verbatim. Delete the partial file. Do not push to the outbox.
- **`valor-telegram send` exits non-zero** → the payload was not enqueued. The temp file is still on disk; remove it manually so it doesn't leak.
- **Bridge relay not running** → the payload sits in Redis until the relay starts. The voice message will eventually arrive. If you need synchronous confirmation, run `./scripts/valor-service.sh status` first.

## Why no `/tts` skill

`tools/transcribe/` has no `/transcribe` skill — its CLI + README is the stable agent-facing surface. `tools/tts/` mirrors that pattern. Agents invoke `valor-tts` via the Bash tool directly when they need raw synthesis. `/do-debrief` is the one user-invocable composite that wraps the synth + voice-message-send flow end-to-end.

## Related references

- `tools/tts/README.md` — full TTS API, voice catalog, troubleshooting
- `bridge/telegram_relay.py` — `_send_queued_message` voice-note branch + `cleanup_file` honoring
- `docs/features/tts.md` — feature design + dual-backend rationale
