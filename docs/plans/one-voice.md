# One voice for Valor (P-044)

Tom, #valor-product, 2026-11-27: "I'd like to consider unifying Valor's voice
across the present skill and the calls on Google meet. Let's use what we are
doing with Google for the voice presentations." Job: anyone who hears Valor (a
call, a narrated presentation, a voice note) hears the same person.

## Premises (observed 2026-11-28)

- Calls speak Gemini's `Alnilam` (`settings.call_voice`, `VALOR_CALL_VOICE`).
- Voice notes and narrated videos go through the yudame plugin's
  `skills/present/tts.py` (plugin commit 2517a77): Kokoro, then macOS `say`.
  `skills/sdlc/converse.md:345` pins `--engine kokoro --voice "$VALOR_TTS_VOICE"`
  (`am_michael`, `settings.tts_voice`, set per turn in `core/__main__.py`
  `_reading_env`). Videos pass no `--voice`, so they get Kokoro's default.
- A turn's env is the operator's less the kernel's secrets
  (`core/workspace.py` `environment`); `GEMINI_API_KEY` is in it (seen in this turn).
- The plugin's source is `yudame/skills` (public, Valor has maintain; Valor
  opens and merges its own PRs there, #6-#10). The kernel cannot merge there.
- P-042's `core/call.py` `speak` calls `gemini-3.8-flash-tts` with a prebuilt
  voice; the response is 24 kHz 16-bit mono PCM.

## What is built

**yudame/skills, one PR (`present: a Google voice`):**

1. `tts.py` gets a `Gemini` engine: one `generateContent` POST to
   `gemini-3.8-flash-tts` over stdlib `urllib` (no new dependency), the voice as
   `prebuiltVoiceConfig`, the base64 PCM written as a 24 kHz WAV with `wave`; the
   existing `to_format` makes ogg/mp3/m4a as now. Key: `GEMINI_API_KEY`, else
   `GOOGLE_API_KEY`.
2. Engine choice: `--engine gemini` added. Under `auto`, a voice that is one of
   Gemini's prebuilt voice names (the published list, a constant) tries Gemini
   first; without a key it falls to Kokoro with Kokoro's default voice, then
   `say`, saying so on stderr as today. Kokoro voices (`_` in the name) and every
   other name behave exactly as now, so a plugin user who sets nothing hears no
   change.
3. `--voice` defaults to `$YUDAME_TTS_VOICE` when set, so an environment picks a
   voice for every clip, voice notes and videos alike, without each command
   passing it.
4. Rate limits on a long video: a 429 or 5xx, or a reply with no audio, is
   retried up to 3 times, waiting the server's `retryDelay` when it gives one.
   No switch to another engine mid-run: one video, one voice.
5. Names: LEXICON is Kokoro phonemes and does not carry. The builder speaks
   "Valor Engels", "Yudame" and "yuda.me" in Alnilam, transcribes each clip back
   (`tools/voice.py`'s transcriber), and where Gemini says one wrong adds a plain
   respelling to a second, Gemini-only table applied to the text it is sent
   (captions and manifest keep the real spelling), retested the same way.
   Engels as in Friedrich Engels, Yudame stressed first (YOO-dah-meh), as the
   Kokoro lexicon says them (product room steer, 2026-11-28; Tom raised both).
6. The JSON printed and the scenes manifest name the engine and voice used, the
   Gemini voice included (as Kokoro's do today).
7. SKILL.md and VIDEO.md say the Google engine, its key, its cost (paid per
   clip) and `YUDAME_TTS_VOICE`; the README's test line names the new checks.

**tomcounsell/ai, this branch (safe to land before the PR merges):**

8. One setting: `settings.tts_voice` and `VALOR_TTS_VOICE` are removed; the turn
   env sets `YUDAME_TTS_VOICE = settings.call_voice` beside `KOKORO_MODELS_DIR`.
9. `converse.md`'s voice-note command drops `--engine kokoro --voice ...`. With
   today's plugin it still speaks Kokoro `am_michael` (auto, no voice: unchanged
   behaviour); with the new plugin it speaks Alnilam.
10. Docs naming `VALOR_TTS_VOICE`/`tts_voice` follow (`tools/README.md`,
   `core/README.md`, `docs/harnesses.md`, `docs/local-models.md`, settings comment).

## Out of scope

The call voice and P-042; choosing another voice; removing Kokoro or `say`;
audio formats (ogg for Telegram, mp3 for Slack, as now); the call's own
pronunciation (P-042's). No new check, gate or guard.

## Tests

- yudame/skills `tests/check_tts.py` (its style, no network, `urllib` and
  `time.sleep` stubbed): Gemini voice + key gives Gemini; Gemini voice, no key
  gives Kokoro with `am_michael` (Kokoro stubbed), Kokoro missing gives `say`
  with `Daniel`; a Kokoro voice never reaches Gemini; a `say` voice name
  (`Daniel`) never reaches Gemini; `YUDAME_TTS_VOICE` is the default and
  `--voice` overrides it; `GOOGLE_API_KEY` alone works; the request carries the
  model and voice and the WAV is 24 kHz mono of the returned PCM; a 429 with
  `retryDelay` waits it and succeeds; three failures exit non-zero without
  another engine.
- ai `tests/test_chat_history.py:388`: a turn's env has `YUDAME_TTS_VOICE` equal
  to `settings.call_voice` (and `VALOR_CALL_VOICE` moves both), and no
  `VALOR_TTS_VOICE`. The full suite.
- `check_tts.py`: the Gemini respelling reaches the request and not the
  manifest's `text`.
- Live, on this Mac with the real key: Alnilam is accepted by
  `gemini-3.8-flash-tts` (the 2026-11-25 test and P-042 used it; confirmed again
  here); each name clip transcribed back before and after any respelling, the
  transcripts reported; a Telegram voice note (`reply.ogg`), a
  Slack one (`reply.mp3`) and a three-scene `--scenes` run each report
  `engine: gemini, voice: Alnilam`; the same with both keys unset reports
  `kokoro`. After the PR merges, the plugin cache on this Mac is updated and the
  converse command, run as written, speaks Alnilam. The sample `.ogg` and a short
  narrated video are left in `.valor/` for the chat session to send Tom: done is
  Tom hearing it.
