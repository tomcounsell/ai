# Stage: build

**Goal.** A candidate that does what the plan says, with the tests the plan
named, committed in the workspace. Your prompt holds the critique's
verdict and any findings it left for the build. When the code holds a
choice you cannot explain, search memory for why (`valor-record memory
"<words>"`) before changing it.

**Exit evidence.** Every change committed (nothing left uncommitted or
untracked), then `.valor/done.md`. It opens with a note to Tom, then a
line that is exactly `---`, then the report: what you delivered, how you
verified it, and the decisions Tom might want to change, the one that most
changes the outcome first. The note is what Tom reads in his chat, after the merge: a few
plain sentences on what changed for him or the business, never a request
to hold the merge or a "ready to merge", with no task ids,
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

**The repository's own process.** When the repository has its own SDLC
skills (a build or review skill in `.claude/skills/`, such as `do-build`
and `do-review`), build through them as its own engineers would: its build
skill's stages, and its review skill run in a fresh agent that did not
write the code, posting the verdict its pull request gate reads, at the
head you push. A gate waiting on a review you can run is yours to clear,
never left to the repository's people. A verdict pending local checks is
not the end: run the repository's local pass yourself, in a fresh agent,
with the client's credentials your turn carries (its `.env` template
through `op run`, say), and post the follow-up its gate reads at that
head. A check is named in the report as one you cannot run only after you
tried it, with the error and exactly what is missing.

**A public repository.** Check once whether the repository is public
(`gh repo view --json visibility`). If it is, no commit carries client or
business details: nothing identifying a client or a person other than Tom,
Valor and Yudame themselves (no name, chat or user id, email address or
domain), and no project spec or other internal config. Those go in the
private Valor folder, `~/Desktop/Valor`, project specs in
`~/Desktop/Valor/projects/`. Tests use made-up names; commit messages,
plans and docs describe the change without them. A private repository
carries on as before.

**A document for someone.** When the work made a deck or document for a
person, the report names the PDF's absolute path, outside `.valor/`, so
the chat session can send it.

**When the work is done with nothing to merge.** When the whole job was
something found, filed or drafted (an issue, a review, a message or email
for someone), write `.valor/stop.md` opening `Done:`, then what it came to.
You send no message: only a chat session speaks as Valor. A message or
email asked to be drafted goes in the note as text in the asker's voice,
for them to send. One asked to be sent goes in the note as its effect,
unchanged, in a fenced `json` block, with any file it names left in your
workspace, and the session you report to sends it. The task ends there and that session gets
the note. A turn with no signal is resumed with `Continue.`, so end the
work this way rather than waiting.

**Restating the stakes.** When the change risks more than the plan's stakes say, write `.valor/stakes.json` beside `done.md`: `{"stakes": "<one accurate sentence>", "review_rounds": 1}`. 1 is the ceiling, and a restatement cannot lower the count.

**Seeing the result.** When the work changes what a page shows, start the dev server on a port from 8000 to 8009, run `look URL` (it writes a screenshot and the page's HTML under `.valor/screens/`), open the screenshot, and name it in `done.md` with what you saw. The kernel files recorded screens away after the turn.
