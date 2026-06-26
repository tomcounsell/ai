# do-debrief context — this repo (ai)

This repo supplies the **context sources** the debrief collects from and the **chat-delivery**
command it ends with. The global skill body owns the brief-construction phases (collect →
categorize → gaps → draft → cut → preface → review gate) generically; this file declares the
repo-specific data pulls and the Telegram delivery surface.

## Collect phase — repo context sources (Step 1)

Pull raw material in parallel — single message with multiple Bash calls. The first two are
generic; the rest are this repo's sources:

- `git log --oneline -20 origin/main` — recent commits (generic)
- `gh pr list --state all --limit 10` — open + recently merged PRs (generic)
- `python -m tools.valor_session list` — session activity
- `valor-telegram read --chat "<scope-relevant>" --since "24 hours ago"` — outstanding chat threads (only if scope names a chat)
- Calendar anomalies **only** for daily/morning briefs: `gws calendar events list --params '{...}'` — surface only items that **moved**, **conflict**, or are **net-new since yesterday**. Never read the agenda back.

## Voice default

`--voice` defaults to `am_michael` (Kokoro); `bf_alice` is the female alternative. See
`~/src/ai/tools/tts/README.md` for the catalog.

## Delivery (Telegram, only after confirmation)

Synthesize via `/do-voice-recording` (it owns the portable `valor-tts` resolution), then deliver
to Telegram:

```bash
# OUT = the file path /do-voice-recording prints (text → OGG/Opus).

# Preface (skippable). Send before the voice note so it lands first in the chat.
if [ -z "$NO_PREFACE" ]; then
    valor-telegram send --chat "$CHAT" "$PREFACE"
fi

valor-telegram send \
    --chat "$CHAT" \
    --voice-note \
    --cleanup-after-send \
    --audio "$OUT"
```

The relay owns the audio file from the moment the payload is pushed — it deletes on successful
send OR after dead-letter placement on retry exhaustion. Synchronous deletion races the relay's
retry loop, so let the relay handle cleanup (`--cleanup-after-send`).

## Error handling (delivery)

- **`valor-telegram send` exits non-zero** → the payload was not enqueued. The temp file is still on disk; remove it manually so it doesn't leak.
- **Bridge relay not running** → the payload sits in Redis until the relay starts. For synchronous confirmation, run `./scripts/valor-service.sh status` first.

## Related references

- `~/src/ai/tools/tts/README.md` — full TTS API, voice catalog, troubleshooting
- `~/src/ai/bridge/telegram_relay.py` — `_send_queued_message` voice-note branch + `cleanup_file` honoring
- `~/src/ai/docs/features/tts.md` — feature design + dual-backend rationale
