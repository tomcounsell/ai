---
name: do-voice-recording
description: "Synthesize supplied text into an audio file for narration, voiceovers, or reading aloud."
---

# Do Voice Recording

Use a callable speech-generation tool if present, or the project's documented TTS CLI. In Valor resolve `valor-tts` on PATH, then `~/src/ai/.venv/bin/valor-tts`; read its `--help` and `tools/tts/README.md` for current voices and limits. Do not claim that native speech generation exists when it is not exposed.
The Valor CLI accepts `--text`, `--output`, optional `--voice`, and `--force-cloud`; its output is OGG/Opus. Use an explicit unique output path. Pass text safely as an argument, never interpolated shell code. Honor requested wording and voice. For an authored narration, use natural contractions and pronounceable proper nouns; do not remove identifiers from text the user asked to read verbatim.
Verify that synthesis succeeded and the file is nonempty and playable; remove only your own failed partial output. Return an inline audio preview or absolute file link. Sending the audio to another person is a separate authorized action; $do-debrief handles constructing an executive brief.
