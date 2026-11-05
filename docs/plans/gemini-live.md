---
status: Planning
type: feature
owner: Valor
created: 2026-11-05
revision: 2 (critique of revision 1)
amends: docs/plans/meet.md (items 4, 5, 7)
---

# Meet calls on Gemini Live; call summaries by Gemini

**Ask.** Tom (Slack #lets-see-fixed, 2026-11-05 07:09 and 10:15 UTC):
target Gemini Live on the existing GCP project and accounts, and commit to
Google models for summaries and analysis of meeting recordings, with keys
kept in 1Password. His answers: a Gemini voice ("expect drift until … a
designer voice"); Gemini decides when it is addressed; save meetings the way
Yudame Tools' meeting recorder does, borrowing it rather than duplicating
it. Defaults he left standing: the Gemini API key, no Vertex; reconnect,
otherwise leave; all calls.

**Premise (checked 2026-11-05).**
- **The key.** `GOOGLE_API_KEY` (1Password `m-valor/GOOGLE_API_KEY`) is in the vault `.env` and is paid tier. It is on valor@yuda.me's `gen-lang-client-0998888049` project.
- **The models it reaches.** `gemini-3.8-live`, `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio-*` and `gemini-3.1-pro-preview`.
- **Today's delay.** A reply comes 4 to 10 s after the speaker stops (task c9612ed428a2).

## What is built

1. **Key, through the existing mechanism.** `python -m core google-key` copies the vault's `GOOGLE_API_KEY` with `credentials.copy_keys` into `google-key` in the kernel key directory. `core/call.py` reads it with `credentials.read_key`. This is the same path as `openai-key`, and `docs/machine.md`'s key table gets the row. The vault `.env` is already the 1Password-sourced copy kernel processes read, so no second path is added. The key is never in the repo, a log or the ledger.
2. **One Live session per call.** It uses the google-genai SDK, with `settings.call_model` defaulting to `gemini-3.8-live` and the voice Charon. Input and output transcription, session resumption and context compression are on.
   - **Audio in.** The bridge's per-speaker audio is mixed to 16 kHz mono and streamed in. Valor's own audio is never fed back.
   - **Text notes.** A note goes in when the active speaker changes ("Tom is speaking") and when the head count changes. With one other person present, the note says "only Tom is here: all he says is to you", which keeps today's "Hello" rule.
   - **Instructions.** They open with `prep`, then meet.md item 5's rules. Answer only when addressed, or when asked what only Valor holds. A line marked as typed in the chat is answered with `post()` and not aloud. Proactive audio is on.
   - **"Valor, leave."** It stays deterministic: the operator's input transcription span is matched with `LEAVE`, as today.
3. **Streaming playback, in `bridges/meet/page.js`.**
   - `valorStream(b64)` schedules each PCM chunk at the end of one Web Audio timeline, so there is no gap and no cut.
   - `valorStop()` stops and clears every scheduled chunk at once and returns the seconds played.
   - Live's `interrupted` calls it.
   - `valorPlay` goes, since only the join line used it, and that line is posted in chat.
4. **The say effect gates the audio.**
   - At a model turn's first audio chunk, `meet.say` is requested with payload `{"turn": <id>}`. Chunks are buffered until it is released, which is one local ledger write.
   - A refused say drops the turn's audio.
   - At turn end or interruption it settles `done` with `spoken_text`: the output transcription, cut at the seconds played (`_cut`).
   - `meet.say`'s check becomes a shape check on `turn`. `max_text` stays on `meet.send_message` only, since a spoken turn's length is bounded by audio, not by a text field.
5. **Tools Live calls**, replacing the front model's marker lines:
   - `ask(q)` starts the worker (`runs.run_turn`, `fresh="call"`) and returns "asked" at once. The answer is posted in chat ("On "q": …", as `Call.work` does now) and sent into the session as a text note. Because it is a new message, it survives any reconnect. After leaving, it goes in the report.
   - `post(text)` is `meet.send_message` plus the thread copy, unchanged.
   - `open_decisions()` reads `board.snapshot`.
   - `settle(ids, readback)` makes the tagged read-back that `ended` answers.
   - `leave()`.
6. **The heard record.** Each input transcription span is a `call.heard` row. Its `who` is the speaker whose track was loudest over that span. `valor-record call` reads the record unchanged.
7. **Metering.**
   - `settings.GEMINI_PRICES` is keyed by model. It holds `GeminiPrice(text_in, audio_in, text_out, audio_out, checked)`, beside `OPENAI_PRICES`.
   - `spending.gemini_cost(usage, price)` prices `usage_metadata`'s per-modality token details: `prompt_tokens_details` and `response_tokens_details`. Each Live turn re-bills its whole context, and the price covers that.
   - Each turn opens with `open_call`. The estimate is the compression trigger (32k tokens) at audio-in, plus 8k at audio-out, and it is charged only when no usage comes back.
   - The summary call is metered the same way.
   - If a stopped task refuses an open, the call leaves.
8. **When Live fails.** On GoAway or a dropped socket, the call reconnects with the resumption handle. If a reconnect fails twice, it posts once in the Meet chat ("I've lost my voice connection; leaving, the summary will follow in the thread") and leaves through `ended`.
9. **Recording into Yudame Tools' store.**
   - **Not its recorder.** It skips Meet (`OfferRule.offers`), and Valor's Chrome plays no call sound.
   - **Not its `--meeting` import.** That runs only with `APP_STORE` overridden, and it re-transcribes with local Whisper and writes notes with the local Ollama model, which is a second transcriber and a non-Google summary.
   - **What the call writes.** It writes the app's folder under `MeetingStore`'s root:
     - `meta.json`: `recording: true` until the summary is in; then `soundChecked` and `transcribeTried` true, every `*Pending` false, and uploads pending.
     - `audio.m4a`: the mixed call with Valor, made with `afconvert`.
     - `transcript.json`: `[Segment]` from the ledger.
     - `transcript.md`.
     - `summary.json`: a `MeetingSummary`.
     - `notes.md`, in `Notes.markdown`'s layout.
   - **What the app does.** Its worker then only backs the folder up to Valor's private Drive and deletes the local audio. That is one store and one backup.
   - **The join line** becomes "Valor here, recording for notes."
10. **The summary by Gemini.**
    - At `ended`, `audio.m4a` goes up through the Files API, since inline requests are capped at about 20 MB.
    - `settings.summary_model` (`gemini-3.1-pro-preview`) gets the file and the ledger transcript. Its structured output is `{summary, decisions, actions, markdown}`: the first three go to `summary.json` and `notes.md`, and `markdown` (meet.md item 7's contents) goes to `call-<code>.md`.
    - The uploaded file is then deleted.
    - The `call` report names the file, and the asking turn sends it in the thread, unchanged (`skills/sdlc/converse.md`).
    - If the summary fails, the report says so, and no other model writes one.
11. **Removed, together.**
    - From `core/call.py`: `Ears`, `Speaker`, `Down`, `words()`, `Front`, `_decide`, `_prefetch`, `_draft` and the sentence splitter.
    - `meet.speak` in `core/judgement_tasks.py` and in `core/ledger.py` `QUIET_SITES`.
    - In `core/__main__.py` `_call`: the ears, voice, judge and front-model gateway wiring.
    - `valorPlay`.
    - Their tests.

    Parakeet, Silero and Kokoro stay in `tools/voice.py` for voice notes.
12. **Docs.** meet.md items 4, 5 and 7 point here. `docs/rollout.md` adds `google-genai`, `python -m core google-key`, and a check that Yudame Tools is installed and signed in to Drive.

**Out of scope.** A designer voice. Vertex. A gateway route for Gemini, so
any turn can analyse a recording on request (the next plan). Video. Zoom and Teams.

**Risks.**
- If `gemini-3.8-live` lacks proactive audio, the live test picks the native-audio model.
- Attributing the speaker by loudness can misattribute crosstalk.
- A typed answer rests on instruction.

**Stakes.** Valor speaks unscripted in live calls, records them to Drive and
spends on a new metered API, so a mistake is said aloud or bills unseen.
Critique may return the plan twice, review once.

**Governance.** None added (`meet.say`'s check is narrowed, not added).

## Tests and verification

A fake Live session at the SDK boundary drives `core/call.py`. The tests cover:
- the heard rows, and a say requested at the first chunk, held until it is released, dropped when refused, and `done` with `spoken_text` cut at an interruption;
- a speaker note only on a change; the one-other-person note; and a typed line arriving tagged so that `post()` answers it;
- `LEAVE` from the operator ending the call, and not from others;
- `ask` returning at once, with an answer after a resumption posted in chat and noted to the session;
- `settle` answered at `ended`;
- a turn with audio and text tokens charged per modality, the estimate when no usage comes back, and a stopped task making the call leave;
- GoAway resuming, and two failed reconnects giving one chat line and leaving;
- a missing `google-key` named, and no key value in the logs;
- the folder's `meta.json`, `transcript.json` and `summary.json` decoding with fixtures produced by Yudame Tools' encoders;
- the summary request carrying the uploaded file and the transcript, and a failed summary named in the report.

In the page harness: three chunks play back to back with no gap, and stop clears the queue and returns the seconds played. The bloat test passes.

**Live** (`VALOR_LIVE=1`, Valor's second device, never Tom). The same
scripted clips run once on `main` and once on this branch. The reply gap is
measured the same way for both: from the end of the second device's clip
(its track's energy below -45 dBFS for 300 ms) to Valor's first played
frame. Target a median of 2 s or less, against `main`'s. The steps:
- a direct question;
- "Hello" with one other person present;
- side talk not naming Valor, which gets silence;
- barge-in;
- a typed question answered in chat;
- a worker lookup;
- "Valor, leave";
- a call over 12 minutes, to cross a reconnect;
- the Gemini summary file, and the folder in Drive.

**Done** when Tom has a call with Valor on Gemini Live and says the
turn-taking works, and a call's summary is made by a Google model.
