# Stage: patch

**Goal.** Every finding the checks sent back, or Tom's feedback, resolved
or answered with a reason. Your prompt holds all of them at once; you are
the session that built this, so you know why each line is there.

**Exit evidence.** As for build: every change committed, then
`.valor/done.md` saying what changed for each finding and why for any you
answered without a change. That is a new candidate, and test, review, and
docs check it again. A question only Tom can answer goes in
`.valor/question.md` instead; for a second opinion, `.valor/advice.md`.

**Seeing the result.** When the work changes what a page shows, start the dev server on a port from 8000 to 8009, run `look URL` (it writes a screenshot and the page's HTML under `.valor/screens/`), open the screenshot, and name it in `done.md` with what you saw. The kernel files recorded screens away after the turn.
