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

**When the work is done with nothing to merge.** When the whole job was an
effect (a message, an email) and its outcome says it was performed, write
`.valor/stop.md` opening `Done:`, then one line on what was sent. The task
ends there and Tom gets that note. A turn with no signal is resumed with
`Continue.`, so end the work this way rather than waiting.
