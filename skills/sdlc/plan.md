# Stage: plan

**Goal.** A short plan the builder, the critic, and the reviewer can all
hold the work against: what will be built and how, what is out of scope,
the stakes in one sentence, how many times critique may send the plan back
(0, 1, or 2) and whether review may send the work back (0 or 1), any
tech-debt additions with the debt each pays, and the tests that will show it
works, including the cases beyond the obvious one. Its size follows the work.

Set the counts from the stakes: the more a mistake would cost and the
harder it is to undo, the more loops. A small reversible change in
well-tested code is a 0; a change to stored data, money, authentication,
migrations, or the kernel itself gets 2 critique rounds and 1 review round.
Your prompt holds the request, Tom's answer, or a critique's findings on
your last plan.

**Exit evidence.** The plan as a file in the workspace, committed, and
`.valor/plan.json`:

    {"path": "<the plan file, relative to the workspace>",
     "stakes": "<one sentence>",
     "critique_rounds": 0, "review_rounds": 0,
     "scope": [{"item": "<added work>", "debt": "<the debt it pays>"}]}

The kernel records it from the committed file. A question only Tom can
answer goes in `.valor/question.md` instead; for a second opinion,
`.valor/advice.md`.

**When the work is done with nothing to merge.** When the whole job was an
effect (a message, an email) and its outcome says it was performed, write
`.valor/stop.md` opening `Done:`, then one line on what was sent. The task
ends there and Tom gets that note. A turn with no signal is resumed with
`Continue.`, so end the work this way rather than waiting.
