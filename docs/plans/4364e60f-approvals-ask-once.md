# Approvals: explain first, ask once, only what is really theirs (P-040)

Tom, #valor-product, 2026-11-25 11:34 UTC: "overly pushy asking for approval
before making sure the stakeholder ... understands what is being approved";
"repeated approvals on trivial steps that simply implements the
requirements". Lane mandatory, value high, appetite small. Findings: task
46b4e09c0f89 (sections a, b, d). The repo is public: tests and docs use
made-up projects and names; no client, people or schema details.

## Cases (observed, 2026-11-25 client call and its chat; ledger on the Captain)

- Asked for a yes at 2:04 and 2:18, before explaining anything. The prep it
  had held only the ask ("yes or no to each step's model ... worker: bring
  each step's before and after").
- Asked about steps that only carried out the plan Tom approved on 11-17.
  The task instruction had widened a project rule that covers only new
  names into "every migration is a question" (row 122105). Nine PRs waited
  a week.
- A decided item was asked again three times (12:42, 13:46, 14:02). The
  prep notes listed it as open under a second label. `PREPARE` sees only the
  last 40 lines of the call (`call.py:2039`), so a no said earlier has
  scrolled out by the next prep, which relists the item. (Every `settle`
  queues a prep whatever its ids, `call.py:1806`: the parse failure below
  did not stop preps; it only kept the decision off the open question.)

## Change

1. **Persona, `persona/conduct.md`**. The call reads only "In a call" and
   "How product decisions are made" (`call.py:815-816`), so the test goes in
   the second, which every turn and the call both read:
   - "How product decisions are made" gains a bullet: work that only carries
     out what was already decided (a stated requirement, a name or plan
     already approved) is status, never a question. A question is only a
     departure from it, a real choice, or a risk. A project rule is carried
     at the reach it was given, never widened.
   - "In a call", **Context first**: never ask for a yes on something you
     have not yet explained in the person's terms. When the detail is not in
     yet, say what the item is and ask nothing.
   - **Only what they hold**: what carries out a decision is said as
     following it, with no ask (the test above, "How product decisions are
     made").
   - **Short and plain**: an explanation may run longer when it catches
     someone up on an item they have not seen.
2. **`core/call.py` `INSTRUCTIONS`**:
   - "let them decide each one" becomes: walk through the items, say which
     only follow what was decided, and ask once for each one that needs
     their answer, after explaining it. A no changes course and is not
     asked again.
   - The 1:1 read-back still opens the next item (P-038). Opening an item
     means explaining it; a yes is asked only when the item needs one.
3. **The prep carries the explanation**:
   - `READ_UP` and `PREPARE` ask, for each item, for what it is and what it
     changes, in the person's terms, and whether it only follows a decision
     (status) or needs their answer.
   - Each decision is named once, under the label the call uses.
   - `skills/sdlc/converse.md`: the call-prep paragraph gets the same test,
     and each item's explanation goes with it, never a bare yes-or-no.
     `act start`: an instruction carries a project rule at its own reach.
4. **Bug: prep lists a settled item as open** (`Call`):
   - The call keeps its read-backs (`self.settled`). `PREPARE` gets them as
     "Settled in this call (leave each out, under any name)", so a decision
     stays out of the notes after it scrolls out of the 40-line window.
   - Only Tom settles, so only his decisions are kept. A no from someone
     else in the call stays as today, in the window only. Out of scope: no
     recorded case yet.
   - A prep that started before the newest `settle` is dropped when it
     returns. That settle has already queued a fresh prep.
5. **Bug: settle ids that did not parse** (`_tools`, `settle`):
   - Cause: only `task/question` is accepted (`call.py:1794`). The voice
     knows those ids only after calling `open_decisions`, which in this case
     it did only when asked "what is open". Any other form was dropped, and
     the tool still answered "recorded".
   - Fix: a bare id is resolved against `board.snapshot(conn, spec)["open"]`
     (not `decisions`' display text). A question id is unique
     (`ledger.new_id`) and resolves to its question. A task id resolves only
     when that task has exactly one open question; with several it is
     unmatched. `ids` becomes optional, so a read-back with no
     id still posts, is kept in `self.settled` and queues a prep.
   - The tool's answer names any id that matched no open decision, so Live
     is never told "recorded" for nothing. That answer is not in
     `ACKNOWLEDGED`, so it is scheduled `WHEN_IDLE` and Live says aloud that
     nothing was recorded for it (intended). The raw ids go to `meet.log`.
6. `docs/bridges/meet.md` and `docs/persona.md` say the same.

## Out of scope (no-gos)

The after-call 👍 confirm in chat (`_confirm`, `converse.md`'s confirm
lines): that waits on Tom. No new check, gate or guard: wording, two bug
fixes and their tests. No change to turn detection, the model or memory
trimming.

## Tests

- `tests/test_call.py`, wording: the persona and `INSTRUCTIONS` carry the new
  lines, and the status-or-question test and the explain-first line reach
  `call.live_config(...).system_instruction`; "let them decide each one" is gone (the test at :3242 is updated);
  the 1:1 read-back line stays; `PREPARE`/`READ_UP` ask for the explanation
  and the status/answer split; converse.md carries the prep test and the
  rule-reach line.
- `tests/test_call.py`, prep: after `settle` "Keep the old name? No, rename
  it", the next `PREPARE` prompt lists that read-back as settled. A prep held
  at a gate while a settle comes is not noted, and the next prep's notes
  are (`test_prep_runs_on_the_queue...` is updated to match).
- `tests/test_call.py`, settle: ids `t1/q1`, a bare `q2` and a bare `t3`
  (one open question) resolve against the board's open list. A bare `t4`
  whose task holds two open questions is unmatched and records neither. An
  unknown id is reported in the tool result, which is scheduled `WHEN_IDLE`
  (not silent), and is not recorded. A settle with no ids posts the read-back, carries
  no `settles` and queues a prep.
- `tests/test_live_call_voice.py` (VALOR_LIVE=1), made-up project: prep
  holding three items that implement a stated requirement and one
  departure. Asked what is open, Valor reports the three as following the
  decision with no ask, and explains the departure before asking about it.
  Told no by Tom (who settles it), it says the new course, and over the next two replies never asks
  about it again. Each run three times, as the existing live tests do.
- Full suite under 5 minutes; the live file run once on this Mac.
