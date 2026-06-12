---
name: do-voice-recording
description: "Turn text into a spoken-audio file (OGG/Opus) from any project or directory. Kokoro local voice with OpenAI tts-1 cloud fallback. The canonical text-to-speech step — other skills (/do-presentation, /do-debrief) defer to this for synthesis. Use when asked to 'record a voiceover', 'narrate this', 'speak this', 'read this aloud', 'say this', 'make an audio/voice clip', or 'text to speech', e.g. recording narration after building a presentation."
argument-hint: "<text> [--output <path.ogg>] [--voice <name>] [--force-cloud]"
allowed-tools: Bash, Read
user-invocable: true
---

# /do-voice-recording — Text → Spoken Audio

The single, simple text-to-speech surface for the whole system. Hand it text, get back an OGG/Opus file. Works from **any** directory on **any** machine, and every other skill that needs synthesis (`/do-presentation` voiceovers, `/do-debrief` voice notes) defers to this rather than reimplementing it.

## Why this skill exists

`valor-tts` is registered in `~/src/ai/pyproject.toml [project.scripts]` and is only on `PATH` when that venv is active. From any other project the capability is invisible — which is the whole reason this skill exists: it makes TTS **discoverable and invocable regardless of cwd**, and it is the one place the synthesis mechanics (binary resolution, flags, prosody) are documented. It is listed in every machine's skill registry once `/update` hardlinks `.claude/skills-global/` into `~/.claude/skills/`.

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

## Prosody (when the audio is for a listener, not a test)

If the text will actually be heard, apply these read-aloud rules:

- **Never recite multi-digit identifiers** (issue/PR/port numbers). TTS reads "1195" as "one thousand one hundred ninety-five" — wasted attention. Refer to things by substance.
- **Contractions** read more naturally than expanded forms.
- **Proper-noun respelling** for prosody (e.g. spell "Yudame" as `You-duh-may`). Dictionary-style hints only — never IPA in slashes; the phonemizer reads `/.../` literally and doubles the clip length.

## Delivering the result

This skill only produces a file. To send it as a Telegram voice note:

```bash
valor-telegram send --chat "<chat>" --voice-note --cleanup-after-send --audio "$OUT"
```

For a *constructed* executive brief (categorize → draft → review-gate → speak → send), use `/do-debrief`, which calls this skill for its synthesis step.

## Related references

- `~/src/ai/tools/tts/README.md` — full API, voice catalog, troubleshooting
- `~/src/ai/docs/features/tts.md` — dual-backend (Kokoro/OpenAI) design rationale
- `/do-debrief` — Telegram executive-voice-brief composite that defers here for synthesis
