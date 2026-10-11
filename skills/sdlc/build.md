# Stage: build

**Goal.** A candidate that does what the plan says, with the tests the plan
named, committed in the workspace. Your prompt holds the critique's
verdict and any findings it left for the build.

**Exit evidence.** Every change committed (nothing left uncommitted or
untracked), then `.valor/done.md`. It opens with a note to Tom, then a
line that is exactly `---`, then the report: what you delivered, how you
verified it, and the decisions Tom might want to change, the one that most
changes the outcome first. The note is what Tom reads in his chat: a few
plain sentences on what changed for him or the business, with no task ids,
commit hashes, file paths, test counts, or findings; the report is on the
task's status page. When the work commits a business decision (a price, an
offer, positioning, how the business markets itself), the report carries
its decision record, a fenced block labelled `decision-record` holding one
JSON object: `recommended`, the option you favoured before the argument;
`chosen`, the option the candidate commits to; and `objections`, each with
the `perspective` that raised it, the `objection`, and `changed_choice`,
true when it changed the option chosen. The kernel records the head commit as the candidate,
and test, review, and docs check it before Tom sees it. A question only Tom
can answer goes in `.valor/question.md` instead; for a second opinion,
`.valor/advice.md`.

**Seeing the result.** When the work changes what a page shows, start the dev server on a port from 8000 to 8009, run `look URL` (it writes a screenshot and the page's HTML under `.valor/screens/`), open the screenshot, and name it in `done.md` with what you saw. The kernel files recorded screens away after the turn.
