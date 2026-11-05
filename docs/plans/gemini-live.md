---
status: Planning
type: feature
owner: Valor
created: 2026-11-05
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
otherwise leave; all calls, not one-to-one only.

**Premise (checked 2026-11-05).** 1Password `op://m-valor/GOOGLE_API_KEY/credential`
is the paid key the research skill uses (`GEMINI_API_KEY` is free tier).
gcloud here is valor@yuda.me on `gen-lang-client-0998888049`. The key lists
`gemini-3.8-live`, `gemini-3.1-flash-live-preview` and
`gemini-2.5-flash-native-audio-*` (bidiGenerateContent), and
`gemini-3.1-pro-preview`. Today a reply comes 4 to 10 s after the speaker
stops (task c9612ed428a2).

## What is built

1. **Key.** `core/call.py` reads the key with `op read` (the vault `.env`'s
   service-account token) when a call starts, and holds it in memory only.
   It is never in the repo, an environment file, a log or the ledger. A
   failed read ends the call through `ended`, naming the item. The `op://`
   reference is in `settings`.
2. **One Live session per call** (google-genai SDK, `settings.call_model`
   defaulting to `gemini-3.8-live`, voice Charon, input and output
   transcription on, session resumption and context-window compression on).
   - The bridge's per-speaker audio is mixed to 16 kHz mono and streamed in.
     A text note "‹name› is speaking" is sent when the active speaker changes.
   - Typed Meet chat lines are sent as text.
   - The system instruction opens with `prep`, then meet.md item 5's rules:
     answer only when addressed or when asked something only Valor holds,
     otherwise stay silent (proactive audio on).
   - Output audio plays through the existing virtual mic. An interruption
     from Live stops playback; Live itself handles barge-in.
3. **Tools Live calls**, replacing the front model's marker lines:
   - `ask(question)`: the worker, as today (`runs.run_turn`, `fresh="call"`), non-blocking. Its answer returns as the tool response; an answer after leaving goes in the report.
   - `post(text)`: `meet.send_message` plus the thread copy, unchanged.
   - `open_decisions()`: `board.snapshot`.
   - `settle(ids, readback)`: the tagged read-back that `ended` answers, unchanged.
   - `leave()`.
4. **The record stays per utterance.**
   - Each input transcription span is a `call.heard` row. Its `who` is the speaker whose track was loudest over that span.
   - Each model turn is one `meet.say`, `request_id` `meet/<turn>/say`. It settles `done` with `spoken_text` from the output transcription, cut where Live reported the interruption.
   - `valor-record call` reads them as before.
5. **Metering.** Each Live turn's `usage_metadata`, and the summary call, open and charge through `spending.open_call`/`charge` on the call session (the gateway's rows). The Gemini prices (audio and text, input and output) go in `settings.PRICES` with the date they were checked. If a stopped task refuses an open, the call leaves.
6. **When Live fails.**
   - On GoAway or a dropped socket, the call reconnects with the resumption handle, carrying the conversation over.
   - If a reconnect fails twice, the call posts once in the Meet chat ("I've lost my voice connection; leaving, the summary will follow in the thread") and leaves through `ended`.
7. **Recording, Yudame Tools' way.**
   - **Why not drive the app.** Its recorder captures a call app's sound and the microphone. It skips Meet by rule (`OfferRule.offers`), and Valor's Chrome plays no call sound. The call process already holds every track and Valor's own audio.
   - **What is reused.** Its store (`~/Library/Application Support/YudameTools/Meetings/<yyyy-MM-dd HHmm title>/` with `meta.json`, `audio.m4a`, `transcript.md`, `notes.md`) and its worker. The worker backs a folder up to Valor's private Drive and deletes the local audio once Drive confirms it.
   - **What the call process writes.** The mixed call, Valor included, goes in as `audio.m4a` through `afconvert`. The folder is written with `recording: true` until the summary is in, then flipped to `false` with every `*Pending` false. One store and one backup; no second recorder. `meetings list` shows Valor's calls.
   - **The join line** becomes "Valor here, recording for notes."
8. **The summary by Gemini.** At `ended` the call process sends the audio and the ledger transcript to `settings.summary_model` (`gemini-3.1-pro-preview`), with item 7's summary instructions from meet.md. It writes the result to the folder's `notes.md` and to `call-<code>.md`. The `call` report names that file, and the asking turn sends it in the thread with at most two lines of text (`skills/sdlc/converse.md`). If the summary fails, the report says so, and no summary is written by any other model.
9. **Removed** from `core/call.py` and its tests:
   - `Ears`, `Speaker`, VAD and Smart Turn, `words()`/`/hear` for calls, and `Front`;
   - the sentence splitter, Kokoro in the say performer, `_decide` and `_prefetch`;
   - the `meet.speak` judgement site.

   Parakeet, Silero and Kokoro stay in `tools/voice.py` for voice notes.
10. **Docs.** meet.md items 4, 5 and 7 point here; `docs/machine.md` names the key; `docs/rollout.md` adds `google-genai` and checks that Yudame Tools is installed and signed in on each Mac.

**Out of scope.** A designer voice. Vertex AI. A gateway route for Gemini,
so a turn can analyse any recording on request (the next plan; this one
bakes in the call summary). Video. Zoom and Teams.

**Risks.**
- The chosen live model may lack proactive audio: then use one that has it, and the live test decides between `gemini-3.8-live` and `gemini-2.5-flash-native-audio`.
- The speaker is attributed by loudness, so crosstalk can be misattributed.
- Live has preview-grade limits.

**Stakes.** Valor speaks unscripted in live calls, records them to Drive and
spends on a new metered API, so a mistake is said aloud or bills unseen.
Critique may return the plan twice, review once.

**Governance.** None added.

## Tests and verification

A fake Live session (an async stand-in at the SDK boundary) drives
`core/call.py`. The tests cover:
- heard and said rows from the transcripts; an interruption cuts `spoken_text`;
- a speaker note only on a change; Valor's own audio never fed back in;
- `ask` reaching the worker, and a late answer landing in the report;
- `post` with its copy, and `settle` answered at `ended`;
- each usage charged at the Gemini price, and a stopped task making the call leave;
- a GoAway resuming with the handle and the context kept, and two failed reconnects giving one chat line and leaving;
- a missing key ending the call, naming the item, with the key absent from the logs;
- the folder matching a `meta.json` fixture written by Yudame Tools' own encoder, flipping `recording` only after the summary;
- the summary request carrying both audio and transcript, and a failed summary named in the report.

The bloat test passes with the removed code gone.

**Live** (`VALOR_LIVE=1`, Valor's second device, never Tom):
- a direct question answered aloud;
- the reply gap measured per exchange, from the speaker's last voiced frame to Valor's first audio frame. Target a median of 2 s or less, against 4 to 10 s;
- side talk not naming Valor gets silence;
- barge-in stops Valor;
- a typed question answered in chat;
- a worker lookup;
- a call over 12 minutes, to cross a reconnect;
- the summary file made by Gemini, and the folder backed up to Drive.

**Done** when Tom has a call with Valor on Gemini Live and says the
turn-taking works, and a call's summary is made by a Google model.
