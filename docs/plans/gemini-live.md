---
status: Planning
type: feature
owner: Valor
created: 2026-11-05
revision: 3 (critique of revision 2)
amends: docs/plans/meet.md (items 4, 5, 7)
---

# Meet calls on Gemini Live; call summaries by Gemini

**Ask.** Tom (Slack #lets-see-fixed, 2026-11-05):
- Gemini Live on the existing GCP project.
- Google models summarise and analyse the recordings.
- Keys stay in 1Password.
- A Gemini voice, and Gemini decides when it is addressed.
- Meetings are saved the way Yudame Tools does, borrowing it.
- The Gemini API key, not Vertex.
- Reconnect, otherwise leave; this covers all calls.

**Premise.** The vault `.env`'s `GOOGLE_API_KEY` (1Password `m-valor`) is paid tier on `gen-lang-client-0998888049`, and it reaches `gemini-3.8-live`, `gemini-2.5-flash-native-audio-*` and `gemini-3.1-pro-preview`. Today a reply takes 4 to 10 s.

## Built

1. **Key.** `python -m core google-key` copies it with `credentials.copy_keys` into the kernel key directory, read with `read_key`. This is the `openai-key` path, recorded in `docs/machine.md`.
2. **Session** (google-genai, `settings.call_model` = `gemini-3.8-live`).
   - The voice is Charon. Transcription in and out, resumption and compression are on.
   - The tracks are mixed to 16 kHz mono and sent with `send_realtime_input`. Valor's own audio is not sent.
   - Activity detection: start sensitivity LOW, prefix padding 300 ms, interruptions kept.
   - Instructions: `prep`, then meet.md item 5. Answer only when addressed, or when asked what only Valor holds. A line tagged "typed" is answered with `post()`. Proactive audio is on.
3. **Notes.** These are context only, sent as `send_client_content(turn_complete=False)`:
   - who is speaking;
   - the head count, and with one other person, "all Tom says is to you";
   - a worker's answer.

   A typed chat line is sent with `turn_complete=True`. "Valor, leave" is still matched by `LEAVE` on the operator's transcription.
4. **Playback** (`page.js`). `valorStream` appends PCM chunks to one Web Audio timeline. `valorStop` clears it and returns the seconds played, called on Live's `interrupted`. `valorPlay` goes.
5. **Say gate.**
   - `meet.say` `{"turn"}` is requested at a turn's first chunk. Audio is buffered until it is released and dropped if it is refused.
   - It settles `done` with `spoken_text`: the output transcription cut at the seconds played.
   - In `core/bridge.py`, `meet.say` gets a turn-shape check; `meet.send_message` keeps `_refuse_meet`.
6. **Tools.**
   - `ask(q)` starts the worker (`runs.run_turn`, `fresh="call"`) and returns at once. Its answer is posted ("On "q": …") and noted, so it survives a reconnect. After leaving, it goes in the report.
   - `post` (with the thread copy), `open_decisions` (`board.snapshot`), `settle`, `leave`.
7. **Record.** Each input transcription span is a `call.heard` row, credited to the loudest track. `valor-record call` is unchanged.
8. **Metering.**
   - `settings.GEMINI_PRICES` holds `GeminiPrice(text_in, audio_in, text_out, audio_out, checked)`.
   - `spending.gemini_cost` prices `usage_metadata`'s per-modality details.
   - Each turn and the summary go through `open_call`/`charge`, with a worst case of 32k audio in plus 8k out.
   - A stopped task makes the call leave.
9. **Failure.** A GoAway or a dropped connection resumes with the handle. After two failed reconnects, Valor posts one chat line and leaves through `ended`.
10. **Store.** Yudame Tools' recorder skips Meet and hears no call sound. Its `--meeting` import re-transcribes and summarises locally. So the call does the following:
    - It stages the store's files in its own directory: `audio.m4a` (the mix with Valor, via `afconvert`), `transcript.json`, `transcript.md`, `summary.json`, `notes.md` and `meta.json`. In `meta.json`, `recording` is false, `soundChecked` and `transcribeTried` are true, every `*Pending` is false, and uploads are pending.
    - After the summary, it `rename`s the finished folder into `MeetingStore`'s root.
    - The app sees only complete folders, and it only backs them up to Drive and deletes the audio.
    - The join line becomes "Valor here, recording for notes."
11. **Summary.**
    - At `ended`, the audio goes up through the Files API.
    - `gemini-3.1-pro-preview` reads it with the transcript and fills `summary.json`/`notes.md`, plus `call-<code>.md` (meet.md item 7), which the asking turn sends.
    - On failure, the report says so, and no other model writes the summary.
12. **Removed together.**
    - In `core/call.py`: `Ears`, `Speaker`, `Down`, `words`, `Front`, `_decide`, `_prefetch`, `_draft`, the sentence splitter.
    - `meet.speak` in `judgement_tasks.py` and `ledger.QUIET_SITES`.
    - The ears, voice, judge and front-model wiring in `__main__._call`.
    - `meet.say`'s `_refuse_meet`; `valorPlay`.
    - Their tests.
    - `tools/voice.py` keeps Parakeet, Silero and Kokoro for voice notes.
13. **Docs.** meet.md items 4, 5 and 7 point here. `rollout.md` adds `google-genai`, `google-key`, and Yudame Tools signed in.

**Out of scope.** A designer voice, Vertex, a Gemini gateway route for on-request analysis (the next plan), video, Zoom and Teams.

**Risks.**
- The model may lack proactive audio or mid-session notes; the live test then picks native audio.
- Crediting turns to the loudest track misnames crosstalk.

**Stakes.** Valor speaks unscripted in calls, records them to Drive and spends on a new metered API; a mistake is said aloud or bills unseen. Critique may return the plan twice, review once. **Governance:** none added.

## Tests

**Unit** (a fake Live session that records each call and flag):
- notes go with `turn_complete=False`; a typed line with `True`, answered by `post()`;
- the say is held until released, dropped when refused, and `spoken_text` is cut;
- `meet.send_message` still refuses empty text and text over `max_text`, while `meet.say` takes `{"turn"}`;
- `LEAVE` acts only for the operator;
- the one-other-person note;
- an `ask` answer after a resumption;
- `settle` at `ended`;
- per-modality charges, the worst-case estimate, and leaving when the task is stopped;
- a GoAway resumes, and two failures post one line and leave;
- a missing `google-key` is named, and its value is never logged;
- the folder appears only complete, and decodes with fixtures from Yudame Tools' encoders;
- the summary carries the file and the transcript, and a failure is reported.

**Page harness.** Chunks play gaplessly; stop clears and returns the seconds played.

**Live** (`VALOR_LIVE=1`, Valor's second device, never Tom). The same clips run on `main` and on this branch. The gap is timed from the clip's end (its track below -45 dBFS for 300 ms) to Valor's first frame, with a median target of 2 s or less. The steps:
- a direct question;
- "Hello" one to one;
- side talk, which gets silence;
- side talk during Valor's reply, which doesn't cut it off;
- barge-in;
- a typed question;
- a lookup;
- "Valor, leave";
- more than 12 minutes;
- the Gemini summary, and the folder in Drive.

**Done** when Tom has a call with Valor on Gemini Live and says the turn-taking works, and a call's summary is made by a Google model.
