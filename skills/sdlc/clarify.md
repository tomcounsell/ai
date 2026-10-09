# Stage: clarify

**Goal.** Every question only Tom can answer (the request's intent where
it is not on the page, vision, priorities, the cost and benefit of a
tradeoff in how the company works, or something only he holds) reaches him
in one message; nothing the code can settle is asked. For a second opinion
on anything else, ask the advisor (`.valor/advice.md`, in the channel
above), then decide.

This turn inspects and changes no file. Your prompt holds the request, or
Tom's answers to what you asked before.

**Exit evidence.** One of:

- `.valor/question.md`: the questions, numbered, each with the answer you
  will assume if Tom leaves it open, then the approach you intend in a few
  lines. The task waits for Tom.
- `.valor/no_question.md`: why no question would materially change the
  result, then the approach you intend in a few lines. The task goes on to
  the plan without Tom.
