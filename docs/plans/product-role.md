# Roles, and the product role's first room

**Ask.** Tom, valor team chat, 2026-10-31/11-01 (#need-additional-hat): lock in
"role" and "room" as defined terms, each naming "hat" as his word for the
role-in-room. Add a "product" role: a professional product owner (JTBD, Hooked,
second-mover + blue ocean, value/effort ratings, one ordered queue for bugs and
features where raising one lowers another, nothing ready until scoped and its
details decided, not a yes-man). The queue is a Google Sheet, standard drawn
from Shape Up. The room approves the next ready item for build "like a PR
review", by starting a thread in the sibling `<project>-dev` channel. Members:
Tom adds them. First room: a private `#<project>-product` channel for one client
project (named in the private spec, not here).

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
   does; Tom's own chats have none. `tasks.dispatch` renders, for a chat
   session with a role, `skills/sdlc/roles/<role>.md` as a section after
   `converse.md`; a missing file raises, as an unreadable persona does.
3. **Four roles** in `skills/sdlc/roles/`, without client names: `engineer`
   (engineering starts here, per converse.md), `teammate` (the sentence now in
   every team room line: no engineering or SDLC work starts here; web search and
   filing issues are fine; engineering goes to the project's Eng chat; ported
   from old-system `config/personas/teammate.md`), `customer-service` (ported
   from its old file), `product` (below). `mentions` stays separate.
4. **The product role** guidance: Tom's points; the Sheet standard; the
   readiness review; how to share the Sheet with a member Tom adds (gws
   `drive permissions create`, writer). **Sheet standard**, one Sheet per
   product room owned by valor@yuda.me, tab `Queue`, one row per item, row
   order = rank: `rank`, `item`, `kind` (bug/feature), `job` (JTBD: when…,
   I want…, so I can…), `hook` (trigger/action/reward/investment/none),
   `problem`, `appetite` (small batch ≤2 wk / big batch ≤6 wk), `value` 1-5,
   `effort` 1-5, `solution sketch`, `rabbit holes`, `no-gos`, `dependencies`
   (each typed human / product item / engineering task, with its state),
   `status` (raw → shaping → shaped → ready → approved → building → shipped,
   or dropped), `issue`, `dev thread`, `updated`. Raising a row's rank moves
   rows down and the room says which. **Readiness review** (guidance, not a
   gate or state): `ready` only when problem, appetite, sketch, rabbit holes and
   no-gos are written and every blocking dependency is cleared; then the room
   approves the top ready row.
5. **Handoff: `task.start` takes `"in": "<chat>"`**, a chat of the session's own
   spec. The child is started under that chat's open session (opened as
   `intake.open_session` does when none is), so it reports to the dev session
   and sits on its board. The product room first posts the approval as a new
   top-level message in the dev channel (`slack.send_message`), then starts the
   task with `in` the dev chat and the instruction holding the row, the issue
   and that thread's `ts`; `converse.md` says a task started from another room
   is reported in the thread its instruction names. One path, extended.
6. **Live** (build stage, as Valor): create the private channel, invite Tom
   only, set its description; create the Sheet, share with Tom; its URL goes in
   the room line. **After all four Macs run the merge** (older code refuses
   `chat_roles` and every Mac reads every spec): add the channel to the
   project's spec with `rooms`, `chat_roles` (product), not in `mentions`; give
   every spec chat with a room its role and drop the moved sentence from team
   room lines; load each with `Spec.load`. The project's Mac (the Captain)
   receives it as it does that project's dev channel; confirm in its bridge log.
   Docs: `docs/bridges/slack.md` (product channels), `projects/README.md`,
   `skills/README.md`, `core/README.md` (`chat_roles`).

**Out of scope.** Product rooms for other projects; any check on readiness;
bulk rating of existing issues; a Slack canvas of the queue.

**Tests.** Spec: `chat_roles` parsed and normalised like `rooms`, `email` key.
Dispatch: a role section for a session with a role, none without, none in Tom's
own chat, email thread's from `chat_roles.email`; each role file renders.
`task.start` with `in`: child's parent is the dev chat's open session, a new one
opened when none, `notices.reports_to` and `board.here` point there; without
`in` unchanged; `in` a chat not in the spec is refused (the outcome says why).
Example spec in `projects/example.toml` carries `chat_roles`.

**Done** when Tom posts in the product channel and Valor answers as a product
owner, e.g. rating and ranking a backlog item in the Sheet.
