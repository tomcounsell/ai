---
name: do-voice-recording
description: "Turn text into spoken audio (OGG/Opus). Triggers: 'record a voiceover', 'narrate this', 'speak this', 'read this aloud', 'say this', 'make an audio clip', 'text to speech'."
argument-hint: "<text> [--output <path.ogg>] [--voice <name>] [--force-cloud]"
allowed-tools: Bash, Read
user-invocable: true
---

# /do-voice-recording — Text → Spoken Audio

Turn text into an OGG/Opus audio file and return its path. This is the one synthesis surface; `/do-presentation` voiceovers and `/do-debrief` voice notes call it rather than reimplementing TTS.

## Repo context

TTS needs a synthesis engine the bare environment doesn't have. If `.claude/skill-context/do-voice-recording.md` exists, it declares the TTS CLI (binary resolution, synthesize command, flags, voices, delivery); follow it exactly. If it is absent, tell the user this repo declares no TTS CLI and stop. Do not install, download, or hand-roll a TTS engine.

## Prosody (when a person will hear it)

- Never recite multi-digit identifiers (issue, PR, port numbers); TTS reads "1195" as "one thousand one hundred ninety-five". Refer to things by substance.
- Contractions read more naturally than expanded forms.
- Respell proper nouns dictionary-style for prosody (e.g. "Yudame" as `You-duh-may`). Never IPA in slashes: phonemizers read `/.../` literally and double the clip length.

## Output

This skill only produces the file. Deliver it with the context file's delivery command if it declares one; otherwise report the path and let the caller deliver. For a constructed executive brief sent to a chat, use `/do-debrief`.
