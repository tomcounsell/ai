# do-voice-recording context — this repo (ai)

This repo provides the TTS CLI the `/do-voice-recording` skill drives: **`valor-tts`**.
The global skill body runs a generic baseline that only declares the dependency; this file
supplies the actual binary, flags, and delivery command. Read top to bottom and honor every
declaration.

## Why this skill exists here

`valor-tts` is registered in `~/src/ai/pyproject.toml [project.scripts]` and is only on `PATH`
when that venv is active. From any other project the capability is invisible — which is the
whole reason this skill exists: it makes TTS **discoverable and invocable regardless of cwd**,
and it is the one place the synthesis mechanics (binary resolution, flags, prosody) are
documented. It is listed in every machine's skill registry once `/update` hardlinks
`.claude/skills-global/` into `~/.claude/skills/`.

## Resolve the binary (portable)

Never assume `valor-tts` is on `PATH`. Resolve it in this order:

```bash
TTS="$(command -v valor-tts || true)"
[ -z "$TTS" ] && [ -x "$HOME/src/ai/.venv/bin/valor-tts" ] && TTS="$HOME/src/ai/.venv/bin/valor-tts"
[ -z "$TTS" ] && { echo "valor-tts not found — is the ~/src/ai venv installed? Run /update there." >&2; exit 1; }
```

`$HOME/src/ai/.venv/bin/valor-tts` is correct on every machine (the repo lives at `~/src/ai`
regardless of the OS username). Invoking by absolute path works from any cwd — the binary's
shebang points at its own venv interpreter.

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

## Flags

| Flag | Meaning |
|------|---------|
| `--text` / `-t` | Text to synthesize. Empty or >4096 chars is rejected. |
| `--output` / `-o` | Destination OGG/Opus path. Overwritten if it exists. Defaults to a `mktemp` file if the user didn't name one. |
| `--voice` / `-v` | Voice name (e.g. `af_bella`, `am_michael`, `nova`). `default` uses the backend's canonical voice. Cross-backend names remap automatically. Catalog: `~/src/ai/tools/tts/README.md`. |
| `--force-cloud` | Skip Kokoro and use OpenAI tts-1 even if Kokoro is available. |

## Delivering the result (Telegram voice note)

This skill only produces a file. To send it as a Telegram voice note:

```bash
valor-telegram send --chat "<chat>" --voice-note --cleanup-after-send --audio "$OUT"
```

For a *constructed* executive brief (categorize → draft → review-gate → speak → send), use
`/do-debrief`, which calls this skill for its synthesis step.

## Related references

- `~/src/ai/tools/tts/README.md` — full API, voice catalog, troubleshooting
- `~/src/ai/docs/features/tts.md` — dual-backend (Kokoro/OpenAI) design rationale
- `/do-debrief` — Telegram executive-voice-brief composite that defers here for synthesis
