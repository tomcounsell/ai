# Valor in Google Meet

**Ask.** Tom, 2026-10-26: Valor joins Google Meet calls. First he connects,
then he answers when he is addressed in a group; video comes last. Real-time
speech to text that knows who is speaking, and speech back. "full duplex is
more important than voice quality". Tool use and subagents still work,
faster: "named worker agents that are mostly read-only and high token
rates". Whether he is addressed: "a decision (Jev-like) model". "Valor
should join just like any other user would join. he has a full google
workspace pro seat", and "as human-like as possible in his turn taking and
usefulness (not visually)".

**Premise, checked 2026-10-26.**
- Google's Meet Media API is a developer preview, needs every participant
  enrolled, and documents no way to send audio. **Rejected.**
- Attendee (attendee-labs/attendee, Elastic License 2.0, active) joins Meet
  in a full Chrome in Docker. Over a websocket it sends 16 kHz PCM per
  participant, each stream tagged with a participant whose join event
  carries the display name, and it speaks PCM sent back
  (`docs/realtime_audio.md`). The bot's own voice is not in what it hears,
  so no echo cancellation is needed. **Reused** (rule 10).
- Attendee signs in to Meet only through SAML SSO: the Workspace sends the
  bot user's sign-in to Attendee as identity provider. For valor@yuda.me
  that would route all of Valor's Google sign-ins, Chrome's and gws's
  included, through Attendee, and it is an admin change on yuda.me.
  **Rejected.** Instead, Valor signs in once in the bot Chrome's own
  profile, as on a new laptop, and the profile is kept (a patch to
  Attendee's Chrome launch, `bots/web_bot_adapter`). Unverified: does
  Google keep that session alive in a container?
- Speech stays on `tools/voice.py`'s models: Parakeet in, Kokoro out (one
  mechanism). Turn timing comes from Pipecat (Silero VAD, Smart Turn v3,
  stopping when interrupted), reused, not written.
- When to speak is a judgement task through `core/judgement.py` with Jev,
  not a second classifier.
- The Cowboy: Apple M4, 16 GB, no Docker. Attendee's container, Parakeet
  (1.7 GB) and Kokoro (0.6 GB) next to the kernel and its turns is tight.
  Step 1 measures it.

## What is built

1. **Join and hear.** OrbStack on the Cowboy, Attendee self-hosted, its
   Chrome profile signed in as valor@yuda.me. Started with a Meet URL, it
   joins as Valor and writes a live transcript, each line with the
   speaker's name: per-participant audio, cut by VAD, through Parakeet.
2. **Speak when asked, write when useful.** A judgement task `meet.speak`
   asks Jev, at each turn's end, about the last few lines (with speakers),
   whether Valor spoke last, and what he has already posted. Labels: asked
   Valor (he speaks), something he can usefully add (he posts it in the
   Meet chat through Attendee's `send_chat_message`, never interrupting),
   someone else's turn, nothing for him (both silent). Abstain is silence.
   A fallback answer that arrives after the turn has moved on is dropped.
   He reads the chat too (`chat_messages.update`), and a message to him
   there is answered there. Human-like turn taking:
   - he speaks after a natural gap, never over someone;
   - he stops within a word when the person he answers talks, and does not
     stop for other people's side talk;
   - his answers are short, addressed to the person by name, and offer
     more;
   - he makes no "mm-hm" sounds.
3. **Answer with tools.** A front model (Haiku 5.5, streamed through the
   gateway) drafts while Jev decides, and the draft is dropped on a no. It
   has one tool: ask a named worker. The workers are mostly read-only
   (repo, calendar and mail, web, memory), kept warm on a fast model.
   A spoken question whose answer needs a worker gets a short spoken
   line ("I'll put it in the chat"), and the full answer is posted there
   when it is ready, however far the talk has moved on.
4. **Useful after.** At the end, the transcript and what he promised in
   the call become a task, the way a chat message does.
5. **Video**, last: Attendee shows a page as his camera or screen; a
   caption card on his tile is decided then, not as a second channel now.

**Out of scope.** Telling apart people who share one laptop's microphone;
Zoom and Teams; joining from the calendar by himself (next, after this
works); a speech engine of our own.

**Governance.** None added.

## Tests

Attendee's websocket mocked: per-speaker transcript lines; `meet.speak`
inputs and routes (speak, post, silent); a chat question answered in
chat; a worker's late answer posted; stopping when the addressed speaker talks but not for
side talk; a dropped late answer; a dropped draft. Live on the Cowboy:
Valor joins a test Meet signed in, plus memory and latency measured at
each step.

**Done** when Tom has a real Meet call with Valor in it, Valor joins as
himself, answers when it is his turn with a tool where needed, and Tom
records the date.
