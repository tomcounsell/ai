# A stale question is followed up where it was asked

Tom, 2026-11-06 (Slack, valor team chat): the board chat's one-line "still
worth doing?" post about a question from his DM — "you're going to make me
decide without telling me what it is?", "shouldn't that come through the DM
tho?", then "do what you need to do" on the read-back. Follows task
3a89e5dcf503's stale flow. A fix: commit with a test.

Stakes: a wrong rule asks Tom twice or nowhere about a stale question;
guidance and report text only, reverted with one commit.

## Build

1. `core/notices.py` `owe`: the task's open question, when not `board.here`,
   already gets its `question:<id>` report to the session that hears it
   (`hears`: the chat it was asked in, or the operator chat). Once it is
   open `board.STALE_DAYS` or more (its `question.asked` row's `at`), the same
   branch also requests a `question`-kind report `stale:<id>` (once, by
   `about_key`; `child_id` set, so that session's `task.stop` reaches it
   through `tasks.reporting`). Text: lead "My question has gone 7+ days
   unanswered", then: ask Tom here whether it is still worth doing, with the
   question's full context and your recommendation, keep or close; his 👍 or
   a reply to keep leaves it waiting, his 👎 or a reply to close is
   `task.stop` with `by`/`via`; then the question text. The wake already runs
   `notices.owe` over every active task, so crossing 7 days fires it; no
   routine, no state, no check.
2. `core/board.py` `text`: a stale question not asked here
   (`asked_here` false) reads "unanswered 7+ days: asked whether still worth
   doing in the chat it was asked in (Tom's DM): no post here" — the canvas
   line points there. A stale question asked here keeps today's wording
   (post here, then name the `stale:` post). Docstring updated to match.
3. `skills/sdlc/converse.md`: decision-board step 3 covers only questions
   asked in this chat; others are one canvas line pointing to the chat it
   was asked in, no post. Step 2's "until it goes stale" goes. "A task's
   report" item 3 gains one sentence for a `stale` question report: ask in
   this chat with full context (Tom's DM is read by Tom alone; in a shared
   chat the Who-reads rule holds), recommendation keep or close; 👍/keep
   leaves it, 👎/close is `task.stop`, neither is an answer.

Out of scope: the board's own `task.stop` reach (stays), any new reaction
handling (reactions already reach every chat's session), the DM-question
Who-reads rule for the board canvas (unchanged).

## Tests (tests/test_board.py)

- Extend the stale test: a DM-asked stale question's board text says it
  was asked in the other chat, no post here, and does not say "post here".
- New: with `STALE_DAYS` 0, `notices.owe` on a DM-asked task writes one
  `stale:<id>` report on the DM's session with `child_id` the task, and a
  second `owe` writes none; a board-started (`here`) stale task gets no
  `stale:` report (the board posts it); a fresh question gets none.
- The DM session's `task.stop` of that task is allowed (reporting path).

Critique rounds 0, review rounds 0: small, reversible, well-tested.
