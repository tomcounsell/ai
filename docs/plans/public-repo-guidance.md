# Client details stay out of a public repository

**Ask.** Tom, valor team chat, 2026-10-29 04:59 UTC: "identify a targeted
adjustment in the sdlc skills to prevent future internal company config
from going in the pubic repo". Proposed: a short paragraph in the plan and
build stages. Tom, 05:08 UTC: "that's perfect. guidance only."

**Cause, checked.** `gh repo view tomcounsell/ai --json visibility` says
`PUBLIC`. Each time Tom added a chat or a person, a task wrote it into a
project spec and committed it. No stage file under `skills/sdlc/` says a
repository may be public. The stages that commit are plan (the plan file),
build and patch (the candidate), and docs (doc commits on top of it).
Patch runs in the builder's own session, which already holds `build.md`;
chat sessions (`converse.md`) commit nothing.

## What is built

1. **`skills/sdlc/plan.md` and `skills/sdlc/build.md`:** one paragraph,
   the same in each, headed **A public repository.**:

   > Check once whether the repository is public (`gh repo view --json
   > visibility`). If it is, no commit carries client or business
   > details: nothing identifying a client or a person other than Tom,
   > Valor and Yudame themselves (no name, chat or user id, email address
   > or domain), and no project spec or other internal config. Those go in the private Valor folder,
   > `~/Desktop/Valor`, project specs in `~/Desktop/Valor/projects/`. Tests
   > use made-up names; commit messages, plans and docs describe the change
   > without them. A private repository carries on as before.

   In `plan.md` it sits after "Ask before the first plan"; in `build.md`
   after the exit evidence.
2. **`skills/sdlc/docs.md`:** one sentence after the first paragraph,
   since the docs session never sees `build.md`: in a public repository a
   doc identifies no client or person other than Tom, Valor and Yudame
   themselves: no name, chat or user id, email address or domain.

Guidance only. No review step, reviewer question, commit hook, scanner or
test that checks for client details; none of those is granted, and Tom
said guidance only. No governance is added.

**Timing.** The paragraph points at `~/Desktop/Valor/projects/`, which task
8e9231734fd4's first stage creates. On 2026-10-29 it does not exist yet and
that stage has not merged. The build checks again; if it still has not,
the report says so and the paragraph points at the folder anyway.

## Out of scope

Moving or scrubbing existing specs, docs and history (task 8e9231734fd4).
`patch.md`, `review.md`, `converse.md`, the persona.

## Tests

Wording only, so no new test: a test asserting the paragraph is there
would buy nothing. The build renders each changed stage through
`core.tasks.stage_text` to read it as a turn gets it, and runs the full
suite so any test reading stage files still passes.

**Done** when the next task on this repository that touches a project's
chats or people puts them in the private folder, not the repository.
