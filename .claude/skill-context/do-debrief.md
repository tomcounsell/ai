# do-debrief context — this repo (ai)

## Extra collect sources

Add to the generic `git`/`gh` batch:

- `python -m tools.valor_session list` — session activity
- `valor-telegram read --chat "<scope-relevant>" --since "24 hours ago"` — only if the scope names a chat
- Daily/morning briefs only: `gws calendar events list --params '{...}'`, surfacing only items that **moved**, **conflict**, or are **net-new since yesterday**. Never read the agenda back.

## Voice default

`am_michael` (Kokoro); `bf_alice` is the female alternative. Catalog: `~/src/ai/tools/tts/README.md`.

## Delivery (Telegram)

`OUT` is the path `/do-voice-recording` prints. `tools/send_message.py` has no voice-note support, so this is the sanctioned `valor-telegram send` exception (see the `telegram` skill for its side effect):

```bash
if [ -z "$NO_PREFACE" ]; then
    valor-telegram send --chat "$CHAT" "$PREFACE"
fi
valor-telegram send --chat "$CHAT" --voice-note --cleanup-after-send --audio "$OUT"
```

Send the preface first so it lands above the voice note. Let the relay delete the audio (`--cleanup-after-send`): it owns the file once the payload is pushed and deletes it on send or after dead-lettering; deleting it yourself races its retry loop.

## Delivery errors

- `valor-telegram send` exits non-zero → nothing was enqueued; remove the temp file yourself.
- Relay not running → the payload waits in Redis until it starts; check `./scripts/valor-service.sh status` if you need confirmation.
