# Voice messages, both ways

**Ask.** Tom, valor team chat, 2026-10-17 10:10-10:16 UTC: "How about voice
messages I send you or you send back?" Agreed there: every voice note or
audio message is transcribed as it arrives, audio kept; a voice note is
answered with a short voice note plus the same text. Engine: local
Parakeet, as Handy runs it. Then, 10:19: "You can use the same model and
voice as the previous system had. But keep it modular so we can easily
replace TTS model and voice later". Prior art: tomcounsell/ai#3626.

**Premise, checked.** Telegram, Slack and email record audio as an
attachment `{name, mime: "audio/…", bytes, path}`; nothing reads it.
`core/intake.py` `_files` renders attachments for `chat_line` and task
prompts. The old system's speech (`tools/tts`, archived checkout
`~/src/_archive_/ai-dinosaur`; `docs/features/tts.md`) was Kokoro ONNX
v1.0, voice `am_michael`, OGG/Opus, with OpenAI `tts-1` as a fallback. A
turn has no `OPENAI_API_KEY` (`core/workspace.py` `DROP_NAMES`), so a cloud
call from a turn fails; Kokoro is local and needs no key or metering.
Prototype on this Mac (16 GB), Python 3.14: `onnx-asr[cpu,hub]` 0.12 with
`nemo-parakeet-tdt-0.6b-v3` int8 (642 MB, Hugging Face) loads in 0.9 s,
transcribes 4 s of OGG/Opus in 0.3 s, peak RSS 1.7 GB. `kokoro-onnx` with
the v1.0 release files (354 MB, GitHub) loads in 0.5 s, speaks 4.5 s of
`am_michael` in 1.7 s, RSS 0.6 GB; Parakeet transcribed Kokoro's output
correctly. Three bridges each holding Parakeet would be ~5 GB, so the
models live in one process.

## What is built

1. **One voice process, `tools/voice.py`**, launchd agent `com.valor.voice`
   (plist with ffmpeg's directory on PATH), aiohttp on
   `127.0.0.1:settings.voice_port` (8714). `POST /transcribe {path}` →
   `{text}`: ffmpeg decodes to 16 kHz mono, Parakeet with onnx-asr's Silero
   VAD. The model loads on first use and stays loaded; the engine is
   `listen`, the model `settings.asr_model`. `python -m tools.voice fetch`
   downloads Parakeet and Kokoro's files (the rollout step).
2. **Transcribed before the message is recorded.** `core/bridge.py`
   `transcribe(attachment)`, a stdlib POST with a 600 s timeout, called off
   the loop from Telegram's and Slack's `_receive` and email's `poll`: an
   `audio/*` attachment gains `transcript`, or `transcript_failed` with the
   reason, and the message is recorded either way. Telegram's stall check
   stops watching once the file is in. `_files` prints the transcript line.
3. **Speech out reuses the present skill's `tts.py`** (yudame plugin:
   Kokoro v1.0, `am_michael`, the old system's engine, model files and
   voice). The kernel puts `KOKORO_MODELS_DIR` and `VALOR_TTS_VOICE`
   (`settings.tts_model_dir`, `settings.tts_voice`) in every turn's env, so
   the voice is one setting; `converse.md` gives the command.
4. **Sent as a voice note.** `TelethonWire.send_file`: an `*.ogg` goes as
   `audio/ogg` with `DocumentAttributeAudio(duration, voice=True)` and its
   filename; lookup matches a voice note by kind and size. Slack uploads
   the `.mp3` as today.
5. **Docs**: `converse.md`, the three bridge docs, `tools/README.md`,
   `docs/harnesses.md`, `core/README.md`, `docs/rollout.md`.

**Out of scope.** A speech engine of our own, OpenAI or any second speech
backend (one mechanism); voice cloning or per-person
voices; Telegram's transcription; video notes; unloading models; a length
cap.

**Governance.** None added. The failed-transcript branch records the
message; it refuses nothing.

## Tests

`tests/test_voice.py`: decode; `listen` joins segments; the route; the
plist; real Parakeet on `say` speech when cached; only downloaded audio
transcribed; process down, and hung past the timeout, each recorded with
the reason; chat-line rendering; Telegram on the emulator (voice and audio
transcribed, photo and PDF not, a slow transcription not cancelled, run
once, holding nothing up); Telegram sends (voice attribute and length, a
nameless voice note found, a lost reply reconciled not resent); Slack and
email transcription; the turn env names the voice. Full suite, lint, bloat.

**Done** when Tom sends a voice note in a real chat, Valor answers what he
said, and he plays the voice reply (recorded with the date).
