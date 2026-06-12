---
name: tts
description: "Synthesize text into a spoken-audio file (OGG/Opus) from any project or directory. Kokoro local voice with OpenAI tts-1 cloud fallback. Use when asked to 'speak this', 'read this aloud', 'say this', 'make an audio file', 'turn this into a voice clip', or 'text to speech'. This is the raw synthesis surface; for a constructed executive voice-brief delivered to Telegram, use /do-debrief instead."
argument-hint: "<text> [--output <path.ogg>] [--voice <name>] [--force-cloud]"
allowed-tools: Bash, Read
user-invocable: true
---

# /tts — Text → Spoken Audio

Synthesize arbitrary text to an OGG/Opus audio file. Works from **any** directory on **any** machine — the underlying `valor-tts` binary lives only inside the `~/src/ai` project venv, so this skill resolves it portably rather than assuming it's on `PATH`.

## Why this skill exists

`valor-tts` is registered in `~/src/ai/pyproject.toml [project.scripts]` and is only on `PATH` when that venv is active. From any other project, the capability is invisible. This globally-synced skill makes TTS **discoverable and invocable regardless of cwd** — it is listed in every machine's skill registry once `/update` hardlinks `.claude/skills-global/` into `~/.claude/skills/`.

This is the raw "speak this text" surface. The constructed 30-second executive voice-brief-to-Telegram flow is a separate composite — `/do-debrief`.

## Resolve the binary (portable)

Never assume `valor-tts` is on `PATH`. Resolve it in this order:

```bash
TTS="$(command -v valor-tts || true)"
[ -z "$TTS" ] && [ -x "$HOME/src/ai/.venv/bin/valor-tts" ] && TTS="$HOME/src/ai/.venv/bin/valor-tts"
[ -z "$TTS" ] && { echo "valor-tts not found — is the ~/src/ai venv installed? Run /update there." >&2; exit 1; }
```

`$HOME/src/ai/.venv/bin/valor-tts` is correct on every machine (the repo lives at `~/src/ai` regardless of the OS username). Invoking by absolute path works from any cwd — the binary's shebang points at its own venv interpreter.

## Synthesize

```bash
OUT="${OUTPUT:-$(mktemp -t tts).ogg}"
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

## TTS prosody (when the audio is for a listener, not a test)

If the text will actually be heard, apply the same read-aloud rules `/do-debrief` uses:

- **Never recite multi-digit identifiers** (issue/PR/port numbers). TTS reads "1195" as "one thousand one hundred ninety-five" — wasted attention. Refer to work by substance.
- **Contractions** read more naturally than expanded forms.
- **Proper-noun respelling** for prosody (e.g. spell "Yudame" as `You-duh-may`). Dictionary-style hints only — never IPA in slashes; the phonemizer reads `/.../` literally and doubles the clip length.

## Delivering the result

This skill only produces a file. To send it as a Telegram voice note:

```bash
valor-telegram send --chat "<chat>" --voice-note --cleanup-after-send --audio "$OUT"
```

For a *constructed* brief (categorize → draft → review-gate → speak → send), use `/do-debrief` — don't reimplement its construction phases here.

## Related references

- `~/src/ai/tools/tts/README.md` — full API, voice catalog, troubleshooting
- `~/src/ai/docs/features/tts.md` — dual-backend (Kokoro/OpenAI) design rationale
- `/do-debrief` — the Telegram executive-voice-brief composite built on this same `valor-tts`
