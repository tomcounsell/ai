# Roles, and the product role's first room

**Ask.** Tom, valor team chat, #need-additional-hat, 2026-10-31/11-01: "role"
and "room" as defined terms, each naming "hat"; a "product" role (his points go
verbatim into its role file); the queue a Google Sheet from Shape Up; the room
approves the next ready item "like a PR review" with a thread in the sibling
`<project>-dev`; Tom adds members. First room: one client project's private
product channel (named in the private spec).

**Premise, checked.** A chat's purpose is its spec's `rooms` line
(`intake.room`, rendered as `Room:` by `tasks.dispatch`). Eng vs team chat is
today prose repeated in each team room's line plus `mentions`. The spec key
`roles` already means Postgres roles. A chat session's `task.start`
(`tools/chat_tasks.py`) starts a child of *itself*, so its reports come back to
it; the Slack bridge drops Valor's own posts, so a post in a dev channel wakes
nothing there. `Spec.from_dict` refuses unknown keys, and every Mac reads every
spec from the shared folder.

## What is built

1. **Ontology.** New `ONTOLOGIES.md` (yudame-dev:ontologies): *room* (a chat and
   what it is for), *role* (how Valor acts there: what it takes up, what starts
   from it), each naming *hat* = role-in-room; *chat role* vs Postgres *roles*.
2. **A chat's role.** Spec key `chat_roles`, a table chat → role name, keyed and
   normalised as `rooms` (and `chat_roles.email`, as `rooms.email`). No new
   refusal. `intake.role(channel, chat_id, project)` resolves it as `room`
   does; Tom's own chats have none. `tasks.dispatch` resolves it from the same
   `chat` as the room (a call's asking chat, else `b.chat`) and renders
   `skills/sdlc/roles/<role>.md` as a section after `converse.md`; a missing
   file raises, as an unreadable persona does.
3. **Four roles** in `skills/sdlc/roles/`, without client names: `engineer`
   (engineering starts here, per converse.md), `teammate` (the sentence now in
   every team room line: no engineering or SDLC work starts here; web search and
   filing issues are fine; engineering goes to the project's Eng chat; ported
   from old-system `config/personas/teammate.md`), `customer-service` (ported
   from its old file), `product` (below). `mentions` stays separate.
4. **The product role** (`roles/product.md`): Tom's points; the Sheet standard
   (one Sheet per product room owned by valor@yuda.me, row order = rank, columns
   from Shape Up plus job, hook, value, effort, status, issue, dev thread); the
   readiness review (guidance, not a gate or state); sharing the Sheet with a
   member Tom adds; build state read from each approved row's issue, PR and dev
   thread on every queue review, since the task reports to the dev chat.
5. **Handoff: `task.start` takes `"in": "<chat>"`**, a chat of the session's own
   spec, so one this Mac owns (one spec, one machine). `perform` resolves it;
   a chat the spec does not list raises `ValueError` with the reason (no new
   `refuse` branch). Under `ledger.lock` on that chat it takes the chat's open
   session (`intake.chat_session`), or opens one with `intake.open_session`
   and `tasks.start`s its Brief in the same transaction, as
   `operator_session` does; the child starts under it, with `project.chat` the
   `in` chat, which the child reads. So it reports to the dev session; it sits
   on the board only when the `in` chat is the project's `board` (for the first
   project it is: its dev channel is its board). The product room first posts
   the approval as a new top-level message in the dev channel, then starts the
   task with `in` the dev chat and the instruction holding the row, the issue
   and that thread's `ts`; `converse.md` says a task started from another room
   is reported in the thread its instruction names, and that a session whose
   opening carries no new message posts nothing. The dev chat's id sits in the
   product room's `rooms` line beside the Sheet URL. One path, extended.
6. **Build stage, reversible live objects only** (as Valor): create the private
   channel, invite Tom only, set its description; create the Sheet, share with
   Tom. No spec is edited before merge.
7. **After merge** (a feedback round on this task, as private-projects stage
   2a/2b): once each of the four Macs is confirmed on the merged commit (older
   code refuses `chat_roles`, and every Mac reads every spec), edit
   `~/Desktop/Valor/projects/*.toml`: the product channel in its project's spec
   with `rooms` (the Sheet's URL in it), `chat_roles` product, not in
   `mentions`; every spec chat with a room gets its role, and the moved sentence
   leaves team room lines. Load each with `Spec.load`, and render a dispatch
   section per listed chat so a misspelt role fails there, not in the chat.
   Confirm the Captain's Slack bridge log receives the channel. Docs:
   `docs/bridges/slack.md` (product channels), `projects/README.md`,
   `skills/README.md`, `core/README.md` (`chat_roles`).

**Out of scope.** Product rooms for other projects; any check on readiness;
bulk rating of existing issues; a Slack canvas of the queue.

**Tests.** Spec: `chat_roles` parsed and normalised like `rooms`, `email` key.
Dispatch: a role section for a session with a role, none without, none in Tom's
own chat, an email thread's from `chat_roles.email`, a call's from its asking
chat's role; each role file renders. `task.start` with `in`: the child's parent
is the `in` chat's open session, a new one opened when none, the child's
`project.chat` is the `in` chat, `notices.reports_to` points to that session,
`board.here` true when the `in` chat is the board and false otherwise; without
`in` unchanged; `in` a chat not in the spec fails with the reason.
`projects/example.toml` carries `chat_roles`.

**Done** when Tom posts in the product channel and Valor answers as a product
owner, e.g. rating and ranking a backlog item in the Sheet.
