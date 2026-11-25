# A decision settled in a Meet call counts, with no 👍 in chat (P-040)

Tom, #valor-product, 2026-11-25 12:00 UTC: "In a single 1-1 call there's no
question who the person is, so no reason to go for extra confirmation";
12:01 UTC: "Also Meets display name is trustworthy enough for our purposes".
This replaces Tom's 2026-10-27 rule (by account, else by 👍 in his DM) for
every call, 1:1 or group.

Case (observed, task 46b4e09c0f89 section 4): after a 1:1 client call on
2026-11-25 the chat session posted a six-point read-back ending "I act on it
once you :+1: this message"; the build sat idle 28 minutes for the 👍.

This removes an approval step and adds none: no grant needed.

## What changes

1. **`core/call.py` `_settle`.** It still folds the `done` read-backs into
   one per question, latest wins. `_confirm` (the post to Tom's Slack DM's
   session, and its "no Slack DM" refusal) is deleted. `_settle` returns the
   report's lines and the settled list; `end` passes the list on the asking
   chat's `ended` report as `settles` (`[{task_id, question_id, text}]`).
   Its line tells that session: these were settled in the call by Tom;
   answer each now with `valor-record act answer <task> --text "<read-back>"
   --by tom --via "meet call" --question-id <q>`, carry it out, and post the
   read-backs in the thread the call was asked in as one notice of what is
   now being done: no question, no 👍 asked. `by` is `tom` because the
   call's `settle` records only Tom's answers (INSTRUCTIONS, unchanged).
2. **`call.confirms` becomes `call.settles(conn, session, task_id)`**: true
   when a `call` report to that session carries the task in `settles`.
   `tools/chat_tasks.py` `TaskAnswer._child` calls it in place of
   `confirms`. A question no longer open is still refused by
   `session.answer` (unchanged).
3. **Texts that quote the rule**, each reworded to the new rule:
   `core/call.py` module docstring (lines 28-32) and `_decisions`' head ("an
   open question is still answered only on Tom's 👍" becomes "an open
   question it settled is answered as the read-back lines below say");
   `skills/sdlc/converse.md` Calls (the "only on Tom's 👍, below" clause and
   the closing "Meet shows display names only ..." sentences);
   `docs/bridges/meet.md` What-it-serves row; `docs/data.md` `call.ended`
   row; `tools/chat_tasks.py` line 36. `core/README.md` and the decision
   board text already say the questions are answered by call, unchanged.

Out of scope: the in-call wording (task 4364e60f4dab edits INSTRUCTIONS,
the `settle` tool, prep and the persona); none of my hunks touch those
lines. Whichever lands second rebases on the other. Who may settle in a
call (Tom only) is unchanged. Answering inside `end` itself, without the
chat session, was considered; the brief names the report-and-session path,
which also lets the session act on it in the same turn.

## Tests (`tests/test_meet.py`, rewriting the four 👍 tests)

- 1:1 (one `call.participant`): three questions, one read-back settling all
  three and a later correction of one: the asking session's `ended` report
  carries `settles` with the latest read-back per question, its text names
  `act answer`, `--by tom`, `meet call`, and contains no 👍 and no
  "confirm"; nothing goes to Tom's DM; the asking session's `task.answer`
  with each question id is performed, by tom via meet call.
- Group (two participants): the same, report asks no confirmation, answer
  accepted from the asking session.
- A read-back whose post failed (`done=False`) settles nothing.
- Another session's `task.answer` is refused; a stale question id raises
  `LookupError`; a question replaced after the call stays open.
- Tom joined as "Tom C": his read-back still answers.
- Wording: `_decisions`' head and converse.md hold no "only on Tom's 👍".

Full suite under 5 minutes, as usual.
