# Call voice from one mind (P-042)

Tom, #valor-product, 2026-11-25 12:15 UTC: "Ideally the voice always comes from a single speaker";
12:04: "I'd rather the interaction be slow than have to repeat myself". Case (observed, client call
2026-11-25, findings task 46b4e09c0f89): the Live voice, writing its own words beside a worker's notes,
re-asked a decided item three times. Research: task 562c1741852b's report, shape (b), its harness
(`lt.py`, `mind.py`): 2.9-4.3 s to first sound on clean clips, tails of 8.2 and 13.3 s.

## Base

P-041 (task ba80dbaa6235, branch commit ffa06866a, unmerged) owns the live meeting state: `Call.state()`,
`decided`, `topics`, `settle` writing a decision into it and the record at once, the worker's UPDATE.
This item reads that state, builds no second one, and changes none of its recording. Step 1 needs
neither. Steps 2-5 branch from `origin/main` once P-041 has merged; if it has not by then, the step-1
report says so and the default is to wait for it. The steer the request asks for (P-041 keeps its state
and recording; its pushing state notes into Live and its L1/L2 live tests are on hold) is not this
task's to send: it has no channel. The plan delivery hands it to the reporting session, and the step-1
report repeats it if P-041's ledger shows it was not received.

## Step 1: ears and turn end, measured and reported first

`tests/test_live_ears.py` (`VALOR_LIVE=1`). Candidates, each fed real-time 40 ms chunks of 16 kHz mix:
**A** `gemini-3.5-transcribe-live`, end-of-speech sensitivity high; **B** the same, default; **C** the
transcriber for words with turn end from Silero + Smart Turn v3 (the old `Ears`/`Speaker` logic and
constants from `ef5172ac1^:core/call.py`, taken, not rewritten); **L** today's `gemini-3.8-live` with
`live_config`, its first output as its turn end (the baseline). Two corpora, 3 runs per candidate:
- **Scripted through Meet.** A Meet made with `gws`; Valor's call page and the second device (as
  `tests/test_live_meet.py` sets them up) join; the device speaks about 25 made-up utterances in two
  voices with mid-thought pauses of 0.6, 1.0, 1.5 and 2.0 s (the fragment before a pause ends without
  final punctuation), plus 4 with a third device's "mm-hm" in a pause. The call's mix is recorded and the
  candidates are run over that recording, so all see the same Meet audio. Truth: the script's pause
  and end positions aligned to the recording by energy (-45 dBFS, as `lt.py`).
- **Tom's real calls.** The right channel (others; left is Valor, checked) of the 2026-11-13 and
  2026-11-16 recordings, fetched from Drive (`meta.json` links, `gws drive files get alt=media`) to the
  task folder, outside the repo: 40 or more pauses of 0.5 s or more
  after which the same speaker resumes before Valor speaks (mid-thought), and the ends followed by
  Valor's channel or 3 s of quiet (turn ends), labelled by a script the test holds and spot-checked
  against the stored transcript. Audio and labels stay out of the repo (path from an env var).

Measured per candidate and corpus, by pause length: **(a) false turn ends**: a turn end inside a
mid-thought pause. Under this design that costs a rerun of the mind (time, tokens), not talk-over,
since speech before first sound cancels the turn (below). **(b) effective cut-offs**: false turn ends
where the speaker does not resume within turn end + the first-sound time, the mind plus TTS measured
by the same test on each utterance's words (the report's 2.9-4.3 s is the check). Also: **delay** from
end of speech to turn end, median, p90, max; missed ends (none within 5 s).
**Go** (my default, stated in the report) if one of A, B, C has (b) at most 1 in 10 mid-thought pauses
of 1.5 s or less on both corpora, no more often than L's cut-offs, (a) at most 1 in 3 such pauses
(a rerun at most every third pause), and median delay to turn end 1.5 s or less. The builder
writes both rates, the delays, the choice and go/no-go to `.valor/question.md` for thread
1795612622.181159 and ends the turn; default: go on the chosen candidate. No-go stops the task and
names the fallback (ElevenLabs for turn-taking, Valor's mind as its custom LLM) as the product room's
decision, unbuilt.

## Steps 2-5: one change that switches over (`core/call.py`, `core/settings.py`)

- **Ears.** One transcriber session on the chosen setting, fed by `_tick`; its text feeds the existing
  stretches (`_speakers`, `_close_stretch`, `call.heard`, who is answered, barge-in cut). Reopened at a
  quiet moment before its 10-minute limit and on a drop, with no audio lost across the switch.
- **Mind.** On each turn end, one streamed call to `settings.call_mind_model` (default
  `gemini-3.5-flash-lite`; `gemini-3.8-flash` as the value. Claude is cut from this change: it needs a
  per-call gateway token, the Anthropic SDK and a second tool format; built if Gemini fails acceptance):
  system prompt from today's `INSTRUCTIONS` and "In a call" rules, P-041's `state()` read fresh
  each turn, the transcript so far; tools the existing `ask`, `post`, `settle`, `open_decisions`,
  `backlog`, `person`, `leave` on today's handlers. Told to call `settle` once, before replying, when
  the person decides. One call twice in a turn is done once; the last round writes words only. An
  empty reply is silence (side talk in a group). Metered at `gemini_price`.
- **A turn commits at first sound, or at the mind's end if it has no words.** Until then only the
  lookups (`backlog`, `open_decisions`, `person`) run. `settle`, `post`, `ask` and `leave` are held: each
  returns its acknowledgement at once as `_tools` does today (asked, posting, recorded, leaving), so the
  mind writes its reply, and runs in order just before the first sentence plays (`settle` first, so
  the state holds the decision before Valor speaks of it). A turn that ends with no reply (group side
  talk, a silent `settle` or `leave`) and was not cancelled runs its held effects when the mind ends.
  `ask`'s dedupe (`self.asked`) applies at commit, so a dropped ask marks nothing asked. Speech from the person being answered before commit cancels the turn,
  drops what it held, and reruns it from fresh state with the new words, so a premature turn end costs
  time, never a duplicate effect or talk-over. Others' speech in a group does not cancel (the
  barge-in rule): it is in the next turn's transcript. A group turn dropped because its person went on
  is answered when they finish, named or not.
- **What is not speech.** Every input Live got by `_tell` goes to the mind:
  - Kept notes (the worker's `ANSWERED`, `STEERED`, `READ_IN`) sit in the mind's prompt beside
    `state()` every turn (newest first, within today's `so_far` budget, the state whole).
  - A worker's answer, a steer or the reading starts no turn of its own (Tom, 2026-11-19: said at a
    pause it spoke over him; hold it for the next opportunity to talk): an answer asked aloud opens
    the next reply, once; the rest are in every turn's prompt.
  - A typed line (`_typed`) is a turn end whose reply is posted, not spoken (`self.typed`,
    `typed_posted` as today).
  - `_answer_alone` and `NAMED_NOTE`'s ask (no reply to the only person, or to one naming Valor in a
    group) start a turn with that note as input, as they ask Live today; `UNPOSTED` starts a turn
    whose reply is spoken.
- **Voice.** Replies split by the old `SENTENCE` regex; each sentence to `gemini-3.8-flash-tts` in
  `settings.call_voice` (Alnilam), the next synthesized while one plays, into today's `_play`, `meet.say`
  and `_cut` (the said text is now exact). No holding line (Tom's answer to step 1, 2026-11-25): the slow
  tail is measured in step 4 and reported, and one is added only if he asks.
- **Removed** (step 5, same change): the Live session (`Gemini.connect`, `live_config`, `note`,
  `send_client_content`, resumption, fresh-session rebuild, the `kept` resend, the stall watch),
  `settings.call_model`, and P-041's Live-only half: `_show`, `state_told`, the state held while Live's
  turn runs, the state in `told`/`so_far` and in a fresh session, and their tests
  (`test_a_fresh_session_is_told_the_workers_answers_the_steers_and_the_newest_state`,
  `test_a_state_waits_out_valors_turn_and_is_not_told_twice`, `test_a_long_state_reaches_a_fresh_session_whole`,
  `test_a_state_done_while_no_session_is_open_is_noted_once_to_the_next`, live L1/L2). Kept from
  P-041: `state()`, `decided`, `topics`, settle recording, UPDATE and their tests. `Gemini.summarise`
  stays. No flag keeps both. `spending.GEMINI_PRICES` gains `gemini-3.5-transcribe-live`,
  `gemini-3.5-flash-lite`, `gemini-3.8-flash`, `gemini-3.8-flash-tts` (else charged unpriced at the top rate).
  `docs/bridges/meet.md`, `core/README.md` updated.

## Tests

Fake ears, mind and voice in `tests/test_call.py`: one test per rule in the bullets above (each
held effect, commit, cancel and rerun, each non-speech input, the reopen, the repeated call, sentence
order); every test that faked Live is moved, not dropped. **Step 4 live** (`test_live_call_voice.py` rewritten,
then `test_live_meet.py` through Meet, 3 runs): P-041's acceptance: three topics out of order, a
decision that changes another, a stale note; no decided item asked again, the decision stated when
asked, `settle` called mid-call. Delay per reply (median, p90, max) and reruns per call reported.
Full suite under 5 min.

## Out of scope

ElevenLabs or any non-Google ears or voice (unless step 1 fails, and then not here); changes to P-041's
state and recording; acting on decisions mid-call. No new check, gate or guard. Client calls cited by
code and date only; tests use made-up names. Done is Tom using it on a real call.
