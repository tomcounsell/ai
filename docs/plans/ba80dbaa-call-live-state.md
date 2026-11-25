# Call: one live picture of the meeting (P-041)

Tom, #valor-product, 2026-11-25 12:03/12:04 UTC: real-time PM work on a call, topics in any order;
"I'd rather the interaction be slow than have to repeat myself". Case (observed, client call
2026-11-25, findings task 46b4e09c0f89 §2-3): a decided item asked again three times, after two prep
notes written before the decision still listed it as open; one decision under two labels; the one
settle recorded nothing.

## Base

Build on P-040 (tasks 4364e60f4dab, 1675bea864e4: `Call.settled`, `_settles`, the stale-prep drop,
1:1 decisions counting without the 👍). Branch from `origin/main` once they land; if they have not when
the build starts, do step 1 first (it needs neither) and rebase before step 2.

## Step 1: the unknown, tested and reported first

Live tests (`tests/test_live_call_voice.py`, `VALOR_LIVE=1`, `live_config` as the call uses it), 3 runs
each, made-up project:
- **L1 replaced state.** A state note (`note()`, `turn_complete=False`), then a second that contradicts
  it ("replaces every earlier state"), sent in silence and during a long reply: no reply to either
  note, the reply plays whole, no restart; asked, Valor answers from the newer.
- **L2 read before speaking.** The same with a blocking `meeting` tool returning the state, which the
  instruction says to call before speaking on a topic: does Live call it every time, and the time to
  first voice against L1 (the added delay).

The builder writes the counts and delays to `.valor/question.md` for the dev thread 1795608680.417339
and ends the turn, with the default stated: **note only (L1) if it is right 3 of 3; else note plus
`meeting` (L2); if neither, stop and say so** (a fresh Live session per state is the fallback named,
not built). The rest waits for that turn's answer.

## Step 2: the build (core/call.py)

- **One state.** `Call.state`: the code-owned decisions (P-040's `settled`, each with its read-back,
  time and the open-question ids it answered) and the worker's topics text. Rendered as one note:
  "The meeting now (replaces every earlier state): Decided in this call (stands over anything below) …
  Topics …". `PREPARED` and `READ_IN` go: the prep and the reading become the topics text. Only the
  newest state is in `told`; `so_far` keeps it whole and cuts the transcript tail first (not the
  quarter that drops a long state). `_read_up` no longer appends the reading to `info["prep"]`: once a
  state exists, a session opened mid-call gets the state in place of the setup prep, so no stale list
  sits beside it. Noted only when its text changed (context growth).
- **Updated after every exchange.** `PREPARE` becomes `UPDATE`: given the current state, the decisions
  and the call since the last update, the worker rewrites the topics: each topic once under the label
  the call uses, its state (open, decided and what, parked), what it touches; a topic a decision
  touches marked changed; then depth on the next two open ones (P-038's follow-ups). Queued, coalesced,
  after each of Valor's turns that spoke and on `settle`, on the worker's one lane, questions first.
- **A decision at once.** `settle` writes the decision into the state and notes it before any worker
  turn; writes it as a dated decision fact of the project citing the call and the question ids
  `_settles` matched (`_decisions`, now mid-call); queues an update. `ids` is optional (P-040's diff
  makes it so; kept or done here): every decision goes through `settle`, one on no open question
  included, so none lives only in the topics text. A worker update begun before a settle is dropped and
  queued again (P-040's rule, kept). `settle` gains an optional `by` (who decided, as the call names
  them); left out with one other person in the call, it is that person; the fact is about them
  (`memory.known`), as `_decisions` does today. A question asked while an update runs cancels it and the update is
  queued again after the answer, so a lookup never waits on an update (the builder checks a cancelled
  worker turn ends cleanly; the update's own time is logged and stated). The end-of-call summary is told the
  decisions already recorded and lists only others, so none is written twice. Answering the open
  question itself stays at the end (no acting mid-call).
- **The end keeps them.** `end`'s summary step merges the state's recorded decisions (text, `by`,
  fact name) into `decided`, so notes.md, summary.json and the report's decided lines list them;
  `_decisions` writes facts only for the summary's others.
- `INSTRUCTIONS`: the `[note]` line names the state note as the call's one record of what is open and
  decided; topics come in any order; a decided topic is never asked again; call `settle` on every
  decision, read back, with the open-decision ids it answers or none; L2's line only if chosen.
- `docs/bridges/meet.md`, `live_config`'s docstring: the state, the measured delay.

## Tests

Fake Live (`tests/test_call.py`): a settle puts the decision in the next state note before any worker
turn; a state note replaces the last in `told` and `so_far` holds one; an unchanged state is not noted
again; a reading or update that lands after a settle reaches Live with the decision on top; an update
begun before a settle is dropped and requeued; a settle writes its decision fact and ids before `end`,
and the summary prompt carries it; a mid-call settle is in notes.md, summary.json and the report's decided lines with its fact
written once; a settle with `by` (and one without, in a 1:1) writes its fact about that person;
a settle with no `ids` is in the state as decided and written as a
fact; a question asked during an update cancels it and the update runs after the answer; a state over
a quarter of `CALL_SO_FAR` reaches a fresh session whole, the transcript cut instead; a session opened
after the reading gets the state, not the reading, in its instruction.
Moved, not dropped: the tests built on `PREPARED`/`READ_IN`/`READ_NONE` (tests/test_call.py ~3074,
3143, 3244; tests/test_live_call_voice.py ~40, 206, 221, 231, 264, 474: 1:1 next item, group wait,
asked-for-more, mid-reply note, settled item not reopened) take the rendered state note, and the live
ones are rerun (3 runs) as the evidence P-038/P-040's voice behaviour still holds.
Live, the acceptance (**L3**, 3 runs): three topics raised out of order; a decision on one that changes
another; then the stale reading arrives through the Call's own render. Valor does not ask the decided
topic again, states the decision when asked, says the affected topic changed, and called `settle`
with the read-back during the run (so the decision is recorded mid-call). Delay per reply printed
and written into `live_config`'s docstring.

## Out of scope

Acting on decisions in the call (answering questions, starting work, the Sheet); a second worker;
persona and approval wording, the after-call 👍 (P-040); a fresh session per state. No new check or
gate. No client details: tests and docs use made-up projects.
