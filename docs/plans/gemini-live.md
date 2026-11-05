---
status: Planning
type: feature
owner: Valor
created: 2026-11-05
revision: 3 (critique of revision 2)
amends: docs/plans/meet.md (items 4, 5, 7)
---

# Meet calls on Gemini Live; call summaries by Gemini

**Ask.** Tom, Slack #lets-see-fixed, 2026-11-05 07:09 and 10:15 UTC:
- Gemini Live on the existing GCP project.
- Google models for summaries and analysis of meeting recordings.
- Keys in 1Password.
- A Gemini voice, and Gemini decides when it is addressed.
- Meetings saved through Yudame Tools' recorder, not a second one.
- His standing defaults: the Gemini API key, no Vertex; reconnect or else leave; all calls.

**Premise (checked 2026-11-05).**
- `GOOGLE_API_KEY` (1Password `m-valor`) is in the vault `.env` and is paid tier, on valor@yuda.me's `gen-lang-client-0998888049`.
- It reaches `gemini-3.8-live`, `gemini-2.5-flash-native-audio-*` and `gemini-3.1-pro-preview`.
- Replies today take 4 to 10 s.

## What is built

1. **Key.** `python -m core google-key` copies the key (`credentials.copy_keys`) into `google-key` in the kernel key directory, and the call reads it with `read_key`. This is the `openai-key` path, with a row in `docs/machine.md`.
2. **One Live session per call** (google-genai, `settings.call_model` `gemini-3.8-live`).
   - Voice Charon, with input and output transcription, resumption and context compression.
   - The tracks are mixed to 16 kHz mono and sent with `send_realtime_input(audio=…)`. Valor's own audio is never fed back.
   - Activity detection: start sensitivity LOW, prefix padding 300 ms, and the interruption setting kept, so barge-in works.
   - The instructions are `prep` plus meet.md item 5's rules: answer only when addressed or asked what only Valor holds. A line tagged "typed" is answered with `post()`, never aloud. Proactive audio is on.
3. **Notes into the session.** `send_client_content(turns=…, turn_complete=False)` is context only, with no reply. It carries:
   - a change of speaker ("Tom is speaking");
   - a change of head count (with one other person present: "all Tom says is to you", today's "Hello" rule);
   - a worker answer.
   - A typed chat line goes with `turn_complete=True`, so it is answered, through `post()`.
   - "Valor, leave" is matched deterministically with `LEAVE` on the operator's transcription, as today.
4. **Playback** (`bridges/meet/page.js`). `valorStream(b64)` appends PCM chunks to one Web Audio timeline. `valorStop()` clears it at once and returns the seconds played, and Live's `interrupted` calls it. `valorPlay` goes.
5. **The say gate.**
   - `meet.say` (payload `{"turn"}`) is requested at a turn's first audio chunk. Chunks are buffered until it is released; refused, they are dropped.
   - It settles `done` with `spoken_text`, the output transcription cut at the seconds played (`_cut`).
   - In `core/bridge.py`, `meet.say` gets a turn-shape check. `meet.send_message` keeps `_refuse_meet` (empty text and `max_text`).
6. **Tools.**
   - `ask(q)` starts the worker (`runs.run_turn`, `fresh="call"`) and returns at once. The answer is posted "On "q": …" and noted (item 3), so it survives a reconnect. After leaving, it goes in the report.
   - `post(text)`: `meet.send_message` plus the thread copy.
   - `open_decisions()`: `board.snapshot`.
   - `settle(ids, readback)`.
   - `leave()`.
7. **Record.** Each input transcription span is a `call.heard` row, attributed to the loudest track over that span. `valor-record call` is unchanged.
8. **Metering.**
   - `settings.GEMINI_PRICES`: `GeminiPrice(text_in, audio_in, text_out, audio_out, checked)`.
   - `spending.gemini_cost` prices `usage_metadata`'s per-modality details (every turn re-bills its context).
   - Each Live turn and the summary go through `open_call`/`charge`. The estimate is 32k audio-in plus 8k audio-out.
   - If a stopped task refuses an open, the call leaves.
9. **Failure.** GoAway or a drop resumes with the handle. After two failed reconnects, the call posts one chat line ("I've lost my voice connection; leaving, the summary will follow in the thread") and leaves through `ended`.
10. **Recording into Yudame Tools' store.**
    - Its recorder skips Meet and hears no call sound. Its `--meeting` import re-transcribes locally and writes notes with Ollama. So the call writes the store's files itself.
    - It stages `audio.m4a` (the mix with Valor, through `afconvert`), `transcript.json` (`[Segment]`), `transcript.md`, `summary.json` (`MeetingSummary`), `notes.md` (`Notes.markdown`'s layout) and `meta.json` in its own directory.
    - In `meta.json`: `recording` false, `soundChecked` and `transcribeTried` true, every `*Pending` false, uploads pending.
    - After the summary, it moves the finished folder into `MeetingStore`'s root with one `rename`. The app never sees a half-written folder, and only backs it up to Valor's Drive and deletes the local audio.
    - The join line becomes "Valor here, recording for notes."
11. **Summary.** At `ended`, the audio goes up through the Files API.
    - `settings.summary_model` (`gemini-3.1-pro-preview`) reads it with the ledger transcript.
    - Its structured output fills `summary.json`/`notes.md`, and its `markdown` field (meet.md item 7) becomes `call-<code>.md`.
    - The file is deleted afterwards. The asking turn sends `call-<code>.md` in the thread.
    - If the summary fails, the report says so, and no other model writes one.
12. **Removed together.**
    - `core/call.py`: `Ears`, `Speaker`, `Down`, `words()`, `Front`, `_decide`, `_prefetch`, `_draft`, the sentence splitter.
    - `meet.speak` in `core/judgement_tasks.py` and `core/ledger.py` `QUIET_SITES`.
    - `core/__main__.py` `_call`'s ears, voice, judge and front-model wiring.
    - `meet.say`'s use of `_refuse_meet` in `core/bridge.py`.
    - `valorPlay`, and their tests.

    `tools/voice.py` keeps Parakeet, Silero and Kokoro for voice notes.
13. **Docs.** meet.md items 4, 5 and 7 point here. `docs/rollout.md` adds `google-genai`, `google-key`, and Yudame Tools signed in to Drive.

**Out of scope.** A designer voice; Vertex; a Gemini gateway route for analysis on request (the next plan); video; Zoom and Teams.

**Risks.**
- A model may lack proactive audio or mid-session `send_client_content`. If so, the live test picks the native-audio model.
- Loudness attribution misnames crosstalk.
- Side talk can still cut Valor off.

**Stakes.** Valor speaks unscripted in calls, records them to Drive and spends on a new metered API; a mistake is said aloud or bills unseen. Critique may return the plan twice, review once.

**Governance.** None added (`meet.say`'s check is narrowed).

## Tests and verification

**Unit**, with a fake Live session at the SDK boundary, recording each call and flag:
- notes are sent with `turn_complete=False`, and a typed line with `True` and is answered by `post()`;
- the say is requested at the first chunk, held until released, dropped when refused, and `spoken_text` is cut at an interruption;
- `meet.send_message` still refuses empty text and text over `max_text`, while `meet.say` takes `{"turn"}`;
- `LEAVE` works from the operator only;
- the one-other-person note;
- an `ask` answer after a resumption is posted and noted;
- `settle` is answered at `ended`;
- a turn is charged per modality, the estimate applies when no usage comes back, and a stopped task makes the call leave;
- GoAway resumes, and two failed reconnects give one line and leave;
- a missing `google-key` is named, and its value is never in the logs;
- the store folder appears only complete, and its JSON decodes with fixtures made by Yudame Tools' encoders;
- the summary request carries the uploaded file and the transcript, and a failure is named in the report.

**Page harness.** Three chunks play gaplessly, and stop clears the queue and returns the seconds played.

**Live** (`VALOR_LIVE=1`, Valor's second device, never Tom). The same clips run on `main` and on this branch. The gap is timed from the end of the clip (the track below -45 dBFS for 300 ms) to Valor's first played frame, with a median target of 2 s or less against `main`'s. The steps:
- a direct question;
- "Hello" with one other person present;
- side talk, which gets silence;
- side talk *during* Valor's reply, which doesn't cut it;
- barge-in, which does;
- a typed question, answered in chat;
- a lookup;
- "Valor, leave";
- a call over 12 minutes;
- the Gemini summary, and the folder in Drive.

**Done** when Tom has a call with Valor on Gemini Live and says the turn-taking works, and a call's summary is made by a Google model.
