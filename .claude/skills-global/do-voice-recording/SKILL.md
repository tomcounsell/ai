---
name: do-voice-recording
description: "Turn text into spoken audio (OGG/Opus). Use when asked to 'record a voiceover', 'narrate this', 'speak this', 'read this aloud', 'say this', 'make an audio/voice clip', or 'text to speech'."
argument-hint: "<text> [--output <path.ogg>] [--voice <name>] [--force-cloud]"
allowed-tools: Bash, Read
user-invocable: true
---

# /do-voice-recording — Text → Spoken Audio

The single, simple text-to-speech surface for the whole system. Hand it text, get back a spoken-audio file (OGG/Opus). Every other skill that needs synthesis (`/do-presentation` voiceovers, `/do-debrief` voice notes) defers to this rather than reimplementing it.

## Repo Context Probe

If `.claude/skill-context/do-voice-recording.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares the actual TTS CLI this skill drives: how to resolve the binary portably regardless of cwd, the synthesize command and its flags, the voice catalog, and how to deliver the result (e.g. as a chat voice note). When the file is absent (the common case in a foreign repo), follow the generic baseline below.

## Generic baseline — TTS requires a repo-provided CLI

Text-to-speech is not a capability the bare environment provides — it needs a synthesis engine. This skill does not bundle one; it drives whatever TTS CLI the repo supplies and documents in its context file.

- **Context file present** → resolve and invoke the declared TTS command exactly as it specifies, passing the user's text and any `--voice` / output options. It returns (or names) the produced audio file path.
- **Context file absent** → the synthesis dependency is unavailable in this repo. Tell the user that text-to-speech requires a repo-provided CLI which this repo does not declare, and stop gracefully. Do **not** attempt to install, download, or hand-roll a TTS engine — that is out of scope for a portable skill.

## Prosody (when the audio is for a listener, not a test)

If the text will actually be heard, apply these read-aloud rules regardless of which backend synthesizes it:

- **Never recite multi-digit identifiers** (issue/PR/port numbers). TTS reads "1195" as "one thousand one hundred ninety-five" — wasted attention. Refer to things by substance.
- **Contractions** read more naturally than expanded forms.
- **Proper-noun respelling** for prosody (e.g. spell "Yudame" as `You-duh-may`). Dictionary-style hints only — never IPA in slashes; phonemizers tend to read `/.../` literally and double the clip length.

## Delivering the result

This skill only produces a file. Delivering it (e.g. sending it as a chat voice note) is a separate step. If the context file declares a delivery command, use it; otherwise report the file path and let the caller decide how to deliver it.

For a *constructed* executive brief (categorize → draft → review-gate → speak → send), use `/do-debrief`, which calls this skill for its synthesis step.
