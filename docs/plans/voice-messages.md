# Voice messages, both ways

**Ask.** Tom, valor team chat, 2026-10-17 10:10-10:16 UTC: "How about voice
messages I send you or you send back?" Shape agreed in that chat: every
voice note or audio message is transcribed as it arrives, audio kept; a
voice note is answered with a short voice note plus the same text; one
standard voice, no cloning. Engine: local Parakeet, as Handy runs it.
Prior art: tomcounsell/ai#3626.

**Premise, checked.** Telegram records a voice note as an attachment
`{name: "voice", mime: "audio/ogg", bytes, path}` (`bridges/telegram/inbound.py`
`attachment`); Slack (`bridges/slack/inbound.py`) and email
(`bridges/email/parse.py`, path set at line 255) record theirs the same way.
Nothing reads the audio. `core/intake.py` `_files` renders attachments for
both `chat_line` and the task prompt. Sends go out as documents
(`TelethonWire.send_file`, `force_file=True`). Prototype on this Mac:
`onnx-asr[cpu,hub]` 0.12 installs on Python 3.14; `nemo-parakeet-tdt-0.6b-v3`
int8 (the model Handy runs) downloads 642 MB from Hugging Face, loads in
0.9 s on CPU, transcribes a 4 s OGG/Opus clip (via ffmpeg) in 0.32 s,
correctly. OpenAI `/v1/audio/speech` with `response_format: opus` returns a
playable OGG/Opus; the turn's environment has `OPENAI_API_KEY`.

## What is built

1. **`core/transcribe.py`**: `transcribe(path) -> str`. ffmpeg decodes any
   audio to 16 kHz mono float PCM on a pipe; onnx-asr's Parakeet TDT 0.6B v3
   int8, CPU provider, with its Silero VAD so any length is cut into
   segments; segment texts joined. The model loads on first use and stays
   loaded in that process. Bridges may import only `core/`, so it lives
   here. `onnx-asr[cpu,hub]` is added to `pyproject.toml`. ffmpeg is found
   as git is: `core/binaries.py` `ffmpeg()`, and each bridge's plist carries
   the path found when the plist is written (launchd's PATH has no
   Homebrew). `python -m core transcribe FILE` prints a transcript; the
   first run fetches the model, which is the rollout step.
2. **Transcribed where the attachment is recorded.** Telegram and Slack
   `inbound.attachment`, and email's attachment write: an attachment whose
   mime is `audio/*` gains `transcript`, run through `asyncio.to_thread`
   (email is already off the loop), so the bridge's loop and pings never
   wait on inference. A transcription that raises records the attachment
   with `transcript_failed: <exception type>` and the message is recorded
   as before. `_files` prints, under the attachment line,
   `  transcript: <words>` or `  not transcribed: <reason>`. No Telegram
   Premium transcription path (one mechanism per concern).
3. **`valor-tts`**, a workspace tool (`tools/valor_tts.py`, standard library,
   Python 3.9, added to `WORKSPACE_TOOLS`): `valor-tts TEXT -o FILE` posts
   to OpenAI speech, model `tts-1`, voice `echo` (the old system's cloud
   voice; its Kokoro path needs a model and a non-stdlib env in every turn,
   so it is not carried over). `.ogg` writes Opus, `.mp3` writes MP3. Exits
   non-zero with OpenAI's error sentence on failure. Kernel never calls it.
4. **Sent as a voice note.** `TelethonWire.send_file`: a file named `*.ogg`
   is sent with mime `audio/ogg` and attributes
   `DocumentAttributeAudio(duration, voice=True)` plus its filename, without
   `force_file`, so it arrives as a playable voice bubble; duration from
   `ffprobe`. Everything else is unchanged. Sent-message matching (by media
   name) keeps working because the filename attribute is kept. Slack
   uploads the `.mp3` as a file, which Slack plays inline; no Slack send
   change. No payload field is added: the extension decides.
5. **Docs.** `skills/sdlc/converse.md`: a message whose attachment has a
   transcript is a voice note; answer it with `valor-tts` (`.ogg` on
   Telegram, `.mp3` on Slack) in the same send's `files`, with the same
   words as `text`, kept short; typed messages get text only.
   `docs/bridges/telegram.md`, `slack.md`, `email.md` (attachments
   paragraph), `tools/README.md`, `docs/rollout.md` (`uv sync`; `python -m
   core transcribe` on any audio file to fetch the model on each Mac;
   rewrite bridge plists; restart bridges).

**Out of scope.** Voice cloning; choosing a voice per person; Kokoro or any
second TTS backend; Telegram's own transcription; video notes; unloading
the model between messages; Slack huddles.

**Governance.** None added: no check, gate or limit. The failed-transcript
branch records the message, it refuses nothing.

## Tests

- `core/transcribe`: ffmpeg decode of a generated OGG/Opus clip to 16 kHz
  PCM; engine faked for the join. One real-model test (`say` → ffmpeg →
  Parakeet) runs when the model is cached, skipped otherwise.
- Telegram inbound on the emulator: a voice note and an audio file each get
  a transcript; a photo and a PDF get none; a transcriber that raises
  leaves the message recorded with `transcript_failed`.
- Slack inbound: an `audio/*` file transcribed, an image not. Email: an
  audio attachment transcribed.
- `_files`/`chat_line`: transcript line and not-transcribed line.
- Telegram media builder: `reply.ogg` → voice attribute with duration, no
  `force_file`; `report.pdf` → document as today. `_matches` recognises a
  sent voice note by name.
- `valor-tts` against a local HTTP stub (`OPENAI_BASE_URL`): `.ogg` asks
  `opus`, `.mp3` asks `mp3`, writes the bytes; missing key and a 4xx each
  exit non-zero with a sentence. `install_tools` installs it.
- Full suite, lint, bloat test.

**Done** when Tom sends a voice note in a real chat, Valor answers what he
said, and he plays the voice note reply (recorded with the date).
