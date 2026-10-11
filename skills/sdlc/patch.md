# Stage: patch

**Goal.** Every finding the checks sent back, or Tom's feedback, resolved
or answered with a reason. Your prompt holds all of them at once; you are
the session that built this, so you know why each line is there.

**Exit evidence.** As for build: every change committed, then
`.valor/done.md`, a note to Tom, a line that is exactly `---`, then what
changed for each finding and why for any you answered without a change,
and, when the work commits a business decision, its `decision-record`
block as build describes, updated if the decision changed.
That is a new candidate, and test, review, and docs check it again. A
question only Tom can answer goes in `.valor/question.md` instead; for a
second opinion, `.valor/advice.md`.

**When the plan's rounds are spent.** The prompt says so. This is the last
round, and you decide, as general manager: patch once more when the
findings are within reach, or stop when they are not. To stop, write
`.valor/stop.md` instead of `done.md`: a note to Tom saying what was built,
what did not pass, and why you stopped, in a few plain sentences. The task
ends there and Tom gets that note; nothing waits on him. For a second
opinion before you decide, `.valor/advice.md`.

**When the target branch moved.** The prompt says the merge was refused
because the target moved on. Rebase the work onto it, resolve conflicts,
run the tests, and write `done.md` as for any patch; this spends no round.

**Seeing the result.** When the work changes what a page shows, start the dev server on a port from 8000 to 8009, run `look URL` (it writes a screenshot and the page's HTML under `.valor/screens/`), open the screenshot, and name it in `done.md` with what you saw. The kernel files recorded screens away after the turn.
