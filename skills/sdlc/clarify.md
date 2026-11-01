# Stage: clarify

**Goal.** Every material question reaches the session this task reports
to in one message: the request's intent where it is not on the page, what
its users see or hear, who does what, what happens when it goes wrong, and
what only Tom holds (vision, priorities, the cost and benefit of a
tradeoff in how the company is organised). That session answers what it
can and raises the rest to Tom. Nothing the code can settle is asked. For
a second opinion on how to build it, ask the advisor (`.valor/advice.md`,
in the channel above), then decide.

This turn inspects and changes no file. Your prompt holds the request, or
Tom's answers to what you asked before.

**Exit evidence.** One of:

- `.valor/question.md`: the questions, numbered, each with the answer you
  will assume if Tom leaves it open, then the approach you intend in a few
  lines. The task waits for Tom.
- `.valor/no_question.md`: why no question would materially change the
  result, then the approach you intend in a few lines. The task goes on to
  the plan without Tom.

**When the work is done with nothing to merge.** When the whole job was
something found, filed or drafted (an issue, a review, a message or email
for someone), write `.valor/stop.md` opening `Done:`, then what it came to.
You send no message: only a chat session speaks as Valor. A message or
email for someone goes in the note as its effect, unchanged, in a fenced
`json` block, with any file it names left in your workspace, and the
session you report to sends it. The task ends there and that session gets
the note. A turn with no signal is resumed with `Continue.`, so end the
work this way rather than waiting.
