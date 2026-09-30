# present context — this repo (ai)

The global skill body owns everything generic (postures → content → shape → HTML → show). This
file declares the two repo-specific bits: **how to detect bridge mode** and **how to deliver the
PDF** back over the Telegram bridge.

## Bridge detection (Step 4)

This session is running through the Telegram bridge iff **`$TELEGRAM_CHAT_ID` is set and
non-empty**. The worker injects it into the harness env for bridge-originated Eng/Teammate
sessions (`agent/session_executor.py`). A local `claude` session in a terminal has no
`TELEGRAM_CHAT_ID` → local mode (open in Chrome).

```bash
if [ -n "$TELEGRAM_CHAT_ID" ]; then
    MODE=bridge
else
    MODE=local
fi
```

## Bridge delivery (Step 4, bridge mode)

Print the page to PDF with headless Chrome (the generic Step-4 command), then send the PDF as a
Telegram document to the originating chat:

```bash
python -m tools.valor_telegram send \
    --chat "$TELEGRAM_CHAT_ID" \
    --file "$PDF" \
    --cleanup-after-send \
    "$CAPTION"
```

- `--chat "$TELEGRAM_CHAT_ID"` targets the chat the request came from.
- `--reply-to` defaults to `$TELEGRAM_REPLY_TO` when set — required for forum-group topics, so
  leave it to the default rather than passing it explicitly.
- `--cleanup-after-send` lets the relay own the PDF's lifecycle (deletes after successful send or
  dead-letter placement). Don't `rm` it yourself — synchronous deletion races the retry loop.
- `$CAPTION` is a one-line framing of what the PDF explains (the Step-1 takeaway sentence), so the
  chat shows something searchable above the document bubble.

## Error handling (delivery)

- **`valor-telegram send` exits non-zero** → the payload was not enqueued; the PDF is still on
  disk. Remove `$SCRATCH` manually so it doesn't leak, and report the failure to the user.
- **Bridge relay not running** → the payload sits in Redis until the relay starts. For synchronous
  confirmation, `./scripts/valor-service.sh status` first.

## Related references

- `~/src/ai/tools/valor_telegram.py` — the `send` subcommand (`--file`, `--cleanup-after-send`)
- `~/src/ai/agent/session_executor.py` — where `TELEGRAM_CHAT_ID` enters the env
- `~/src/ai/agent/sdk_client.py` — where `TELEGRAM_REPLY_TO` enters the env
- `~/src/ai/.claude/skill-context/do-debrief.md` — the sibling pattern (voice note over the same bridge)
