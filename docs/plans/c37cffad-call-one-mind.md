# Call voice from one mind (P-042)

Tom, #valor-product, 2026-11-25 12:15 UTC: "Ideally the voice always comes from a single speaker";
12:04: "I'd rather the interaction be slow than have to repeat myself". Case (observed, client call
2026-11-25, findings task 46b4e09c0f89): the Live voice, writing its own words beside a worker's notes,
re-asked a decided item three times. Research: task 562c1741852b's report, shape (b), its harness
(`lt.py`, `mind.py`): 2.9-4.3 s to first sound on clean clips, tails of 8.2 and 13.3 s.

## Base

P-041 (task ba80dbaa6235) owns the live meeting state (`Call.state`, `settle` writing a decision into it
and the record at once, the worker's UPDATE). This item reads that state; it builds no second one and
changes none of its recording. Step 1 needs neither. Steps 2-5 branch from `origin/main` once P-041's
state has merged; if it has not by then, the step-1 question says so and the default is to wait for it.

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
- **Tom's real calls.** The right channel of recordings in the meetings store on this Mac
  (`settings.meetings_store`, 2026-11-13 and 2026-11-16 calls): 40 or more pauses of 0.5 s or more
  after which the same speaker resumes before Valor speaks (mid-thought), and the ends followed by
  Valor's channel or 3 s of quiet (turn ends), labelled by a script the test holds and spot-checked
  against the stored transcript. Audio and labels stay out of the repo (path from an env var).

Measured per candidate and corpus: **cut-off rate** (a turn end inside a mid-thought pause), by pause
length; **delay** from end of speech to turn end, median, p90, max; missed ends (none within 5 s).
**Go** (my default, stated in the report) if one of A, B, C cuts off at most 1 in 10 mid-thought pauses
of 1.5 s or less on both corpora, no more often than L, with median delay 1.5 s or less. The builder
writes the table, the choice and go/no-go to `.valor/question.md` for thread 1795612622.181159 and ends
the turn; default: go on the chosen candidate. No-go stops the task and names the fallback (ElevenLabs
for turn-taking, Valor's mind as its custom LLM) as the product room's decision, unbuilt.

## Steps 2-5: one change that switches over (`core/call.py`, `core/settings.py`)

- **Ears.** One transcriber session on the chosen setting, fed by `_tick`; its text feeds the existing
  stretches (`_speakers`, `_close_stretch`, `call.heard`, who is answered, barge-in cut). Reopened at a
  quiet moment before its 10-minute limit and on a drop, with no audio lost across the switch.
- **Mind.** On each turn end, one streamed call to `settings.call_mind_model` (default
  `gemini-3.5-flash-lite`; `gemini-3.8-flash` or a Claude model through the call's gateway as the
  value): system prompt from today's `INSTRUCTIONS` and "In a call" rules, P-041's rendered state read
  fresh each turn, the transcript so far; tools the existing `ask`, `post`, `settle`, `open_decisions`,
  `backlog`, `person`, `leave` on today's handlers. Told to call `settle` before replying to a decision.
  An empty reply is silence (side talk in a group). Speech arriving before Valor's first sound cancels
  the turn and reruns it with the new words, so a premature turn end costs time, not a cut-off.
- **Voice.** Replies split by the old `SENTENCE` regex; each sentence to `gemini-3.8-flash-tts` in
  `settings.call_voice` (Alnilam), the next synthesized while one plays, into today's `_play`, `meet.say`
  and `_cut` (the said text is now exact). With no sentence 5 s after a turn end, Valor says one short
  holding line (the observed 8-13 s tails).
- **Removed** (step 5, same change): the Live session (`Gemini.connect`, `live_config`, `note`,
  `send_client_content`, resumption, fresh-session rebuild, the `kept` resend, the stall watch),
  `settings.call_model`. `Gemini.summarise` stays. No flag keeps both. Metering charges ears, mind and
  voice at their `gemini_price`. `docs/bridges/meet.md`, `core/README.md` updated.

## Tests

Fake ears, mind and voice in `tests/test_call.py`; every test that faked Live is moved, not dropped:
a turn end runs the mind once with the current state; a `settle` before the reply is in the state
before the voice plays; speech before first sound cancels and reruns; barge-in by the person answered
cuts and records the said part; the other person talking does not; group side talk gets silence; a
tool error is said; the 10-minute reopen drops no audio and keeps the stretch; the holding line at
5 s and only once; sentences play in order. **Step 4 live** (`test_live_call_voice.py` rewritten,
then `test_live_meet.py` through Meet, 3 runs): P-041's acceptance: three topics out of order, a
decision that changes another, a stale note; no decided item asked again, the decision stated when
asked, `settle` called mid-call. Delay per reply (median, p90, max) reported. Full suite under 5 min.

## Out of scope

ElevenLabs or any non-Google ears or voice (unless step 1 fails, and then not here); changes to P-041's
state and recording; acting on decisions mid-call. No new check, gate or guard. Client calls cited by
code and date only; tests use made-up names. Done is Tom using it on a real call.
