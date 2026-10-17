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
   (plist written like the bridges', with the ffmpeg path found at write
   time), aiohttp on `127.0.0.1:settings.voice_port` (8714).
   `POST /transcribe {path}` → `{text}`: ffmpeg decodes to 16 kHz mono PCM,
   Parakeet with onnx-asr's Silero VAD so any length is cut into segments.
   `POST /speech {text, format: ogg|mp3}` → audio bytes: Kokoro, then
   ffmpeg to Opus or MP3. Each model loads on first use and stays loaded.
   Engines sit behind two functions, `listen(path)` and `speak(text)`; the
   models and voice are settings (`asr_model`, `tts_model_dir`
   default `~/.cache/kokoro-onnx` as before, `tts_voice` `am_michael`), so a
   new voice is a setting and a new engine is one function. `python -m
   tools.voice fetch` downloads both models (the rollout step).
   `onnx-asr[cpu,hub]` and `kokoro-onnx` join `pyproject.toml`.
2. **Transcribed where the attachment is recorded.** `core/voice.py`
   `transcribe(path)`: a stdlib POST to the voice process (bridges import
   only `core/`). Telegram and Slack `inbound.attachment`, and email's
   attachment write, add `transcript` to an `audio/*` attachment, through
   `asyncio.to_thread` (email is already off the loop). Any failure,
   including the process being down, records `transcript_failed: <reason>`
   and the message is recorded as before. Telegram: the stall check
   (`_cancel_stalled`) covers the download only; the message's entry leaves
   `_received` once the file is in, so a long transcription is not
   cancelled. Telegram files are already received off the stream and Slack
   messages each run as their own task, so a slow transcription holds up
   no other message; Slack redelivery while it runs is the existing wait.
   `_files` prints `  transcript: <words>` or `  not transcribed: <reason>`
   under the attachment line. No Telegram Premium path.
3. **`valor-tts TEXT -o FILE`**, workspace tool (`tools/valor_tts.py`,
   stdlib, Python 3.9, in `WORKSPACE_TOOLS`): POSTs to `/speech`, writes
   the bytes; `.ogg` or `.mp3` picks the format. No key. Exits non-zero
   with a sentence when the process is unreachable or answers an error.
4. **Sent as a voice note.** `TelethonWire.send_file`: a `*.ogg` goes with
   mime `audio/ogg` and `DocumentAttributeAudio(duration, voice=True)`
   beside its filename attribute, without `force_file`; duration from the
   Ogg's last granule position (48 kHz). Other files unchanged; sent-message
   matching by name still holds. Slack: the `.mp3` uploads as today and
   plays inline. No payload field: the extension decides.
5. **Docs.** `skills/sdlc/converse.md`: an attachment with a transcript is
   a voice note; answer with `valor-tts` (`.ogg` Telegram, `.mp3` Slack) in
   the same send's `files`, the same words as `text`, kept short; typed
   messages get text only. `docs/bridges/telegram.md`, `slack.md`,
   `email.md`, `tools/README.md`, `docs/rollout.md` (on each Mac: `uv
   sync`, `python -m tools.voice fetch`, install `com.valor.voice`, rewrite
   bridge plists, restart; ~2.3 GB resident once both models are used).

**Out of scope.** OpenAI or any second speech backend (the old cloud
fallback is not carried: one mechanism); voice cloning or per-person
voices; Telegram's transcription; video notes; unloading models; a length
cap.

**Governance.** None added. The failed-transcript branch records the
message; it refuses nothing.

## Tests

- Voice process, engines faked: `/transcribe` and `/speech` (ogg and mp3
  through real ffmpeg), the configured voice passed to `speak`. One test
  with real models (`say` → Parakeet; Kokoro → Opus → Parakeet round trip)
  runs when the models are cached, skipped otherwise.
- `valor-tts` run with the env `workspace.environment()` builds (no
  `OPENAI_API_KEY`) against the faked process writes playable `.ogg`/`.mp3`;
  process down and a 500 each exit non-zero with a sentence.
- Telegram on the emulator: voice and audio transcribed, photo and PDF not;
  transcriber down → `transcript_failed`, message recorded; a slow
  transcriber across two ticks is not cancelled, a text sent after it is
  recorded first, the voice note once it finishes, and gap fill does not
  pass it. Slack: `audio/*` transcribed, image not, slow transcriber acked
  once committed. Email: audio attachment transcribed.
- `_files`/`chat_line`: transcript and not-transcribed lines.
- Telegram send: `reply.ogg` → voice attribute with its duration, no
  `force_file`; `report.pdf` as today; a sent voice note matched by name.
- Full suite, lint, bloat test.

**Done** when Tom sends a voice note in a real chat, Valor answers what he
said, and he plays the voice reply (recorded with the date).
