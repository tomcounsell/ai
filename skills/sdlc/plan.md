# Stage: plan

**Goal.** A short plan the builder, the critic, and the reviewer can all
hold the work against: what will be built and how, what is out of scope,
the stakes in one sentence, how many times critique may send the plan back
and review may send the work back (0, 1, or 2 each), any tech-debt
additions with the debt each pays, and the tests that will show it works,
including the cases beyond the obvious one. Its size follows the work.

Set the counts from the stakes: the more a mistake would cost and the
harder it is to undo, the more loops. A small reversible change in
well-tested code is a 0; a change to stored data, money, authentication,
migrations, or the kernel itself is a 2. Your prompt holds the request,
Tom's answer, or a critique's findings on your last plan.

**Exit evidence.** The plan as a file in the workspace, committed, and
`.valor/plan.json`:

    {"path": "<the plan file, relative to the workspace>",
     "stakes": "<one sentence>",
     "critique_rounds": 0, "review_rounds": 0,
     "scope": [{"item": "<added work>", "debt": "<the debt it pays>"}]}

The kernel records it from the committed file. A question only Tom can
answer goes in `.valor/question.md` instead; for a second opinion,
`.valor/advice.md`.
