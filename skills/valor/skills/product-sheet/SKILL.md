---
name: product-sheet
description: Keep a product room's backlog Sheet (Google Sheets, valor@yuda.me) to Yudame's product standard - create one from the template for a new product room, add, rate and rank items, run the readiness review that approves an item for build, chase blockers, and review what shipped. Use in a product room, or when setting one up.
---

# The product Sheet

One Google Sheet per client project (in its product room where it has
one), owned by valor@yuda.me, in its own Drive folder with its companion
Doc, copied from the template "Product sheet template" (`TEMPLATE` in `sheet.py`). The product
role (`skills/sdlc/roles/product.md`) and the persona's "Run the project" and
"How product decisions are made" say how to act; this says how the
Sheet is kept. The standard comes from Tom's review of the first room
(2026-11-01): no scores or decimals, lanes first, rank only what is ready,
blockers listed apart with owners, a goal on top, a review after every
ship, and Valor as approver once a full readiness review passes.

`sheet.py` beside this file does the mechanical parts through `gws`; for
anything else use `gws sheets spreadsheets values get|update|append` on the
ranges `doc.md` describes. Read the Sheet before changing it; write whole rows.

## Tabs, columns and statuses

`doc.md` beside this file is the one description of the Sheet: every tab,
every column with its values, every status and what it needs, how a raw
idea becomes ready, and the readiness review's checks. `sheet.py doc`
renders it into each Sheet's companion Google Doc and its `Columns` tab,
so people and other agents (Gemini, Figma's Drive connection) read the
same text; change `doc.md`, then run `doc` on every Sheet. In brief: tabs
Columns, Goal, Queue, Pitches, Blocked, Archive (a chart tab, where one
exists, is drawn from Queue); statuses `raw`, `shaping`, `shaped`,
`ready`, `approved`, `building`, `shipped`, `reviewed`, and no others.

Valor drafts the goal and metric from what the people who own the
product have said, sets them with `Set by` Valor and what they rest on,
and tells them; they correct or override, and their word replaces the
draft (`Set by` them).

## Ranking

`python3 sheet.py order SHEET` sorts the Queue and numbers the ready rows,
printing every row that moved. The order: `approved` and `building` first;
then `ready`, the mandatory lane before bets, mandatory by `due` (none
last), bets by value then the smaller appetite; then `shaped`, `shaping`,
`raw`; `shipped` and `reviewed` last. Ties keep their place, so a
deliberate reorder by hand stands until the bands change. When you move a
row by hand, say in the room which rows it now sits above and why.

## Procedures

**Onboarding a project.** Every client project Valor works on gets this
(Tom, 2026-11-06: "anywhere there is a PM persona active, there should be
a Google sheet"); Valor-internal, personal, paused and dormant projects
(no `machine`, no recent push) are left out, and the report says which and
why. Run it for a new project, or over every spec in
`~/Desktop/Valor/projects/` (docs/plans/private-projects.md says how a turn
edits one), and report per project what it had, what it lacked, what was
filled, and the Sheet, Doc and folder links. It is a list Valor runs and
reports on; nothing waits on it.
1. **Sheet, Doc and folder.** Look for a Sheet URL in the spec's `rooms`
   and for `<Product> product` in valor@yuda.me's Drive first: a project
   that has a Sheet gets `python3 sheet.py doc SHEET --share tom@yuda.me`
   (it moves a Sheet loose in My Drive into its own folder, replaces the
   old Guide tab with Columns, links the Doc on Goal), plus any Queue or
   Archive header column the template has and it lacks, and any status
   outside the list set to what its row supports. One without:
   `python3 sheet.py new "<Product>" --share tom@yuda.me`. Share the folder
   with Tom only; others get it when Tom asks (Sharing, below).
2. **Seed.** A new Sheet: `python3 sheet.py seed SHEET OWNER/REPO` adds the
   repo's open issues as `raw` rows, unrated. An existing Sheet is not
   seeded blind; the room adds rows as items come up.
3. **Every room names the Sheet.** Each `rooms` line of the spec carries
   the Sheet's URL: an engineering room keeps it there (or names the
   product room where there is one); a teammate room or client email says
   it is kept in the Eng or product room. Load the spec with `Spec.load`
   on its path and put the diff in the report.
4. **Product room, only where Tom made one.** Never create a channel. Find
   its id from a Mac that holds the workspace's Slack keys (`ssh
   valorengels@<mac>`, then `users.conversations` with the user token in
   `~/.config/valor-kernel/slack-keys`; a task cannot read its own Mac's
   key directory, and `valor-slack read` needs the id first). A public
   channel is received like a private one. In the spec: the channel in
   `chats` (not `mentions`), `chat_roles` `product`, and a `rooms` line
   naming the Sheet and the dev chat's id, as the first product room's is.
   On the spec's `machine` (over ssh: `stat` the spec for the iCloud sync,
   the bridge's log `~/Library/Logs/valor/slack.log` for a live
   connection): ownership is reread on every message, so no restart; a
   message posted before the channel was listed is not taken (the first
   new one is the live test).
5. **Decision board.** The spec's `board` names its dev channel; its
   "Valor Decisions" canvas is made by that chat's session at the first
   open decision, so a project with none open shows no canvas yet.
6. **Goal.** The room's session drafts the product goal and metric first,
   from the room, the code and the record, and tells the room.

**Adding an item.** Read the code and the issues first; if it exists or
shipped, say so instead. Otherwise append a Queue row (next free id,
status `raw`, lane, value and appetite as your first read with the
evidence for them) and, once there is more than a sentence, a Pitches row.
Merge duplicates into the older id.

**Blockers.** Each dependency is a Blocked row. Ask the owner in the room
(or where they are), set `asked on` to today and `chase on` three days on.
`python3 sheet.py due SHEET` lists what to chase today, and every open
blocker with its owner and the days it has waited: chase once, then at
the second chase take it to Tom in the room. Clear the row when it is done
and remove it from the item's `blocked by`.

**The readiness review.** Run all twelve checks in `doc.md` ("The
readiness review") in full for the top candidate, after `order`, writing
each check's result in the item's `readiness` cell. It passes only when
every check does. A builder question only a person can answer is asked of
that person and the item waits; never answer one on their behalf.

A pass makes the item `ready`; then approve it as the role says (post in
the dev chat, `task.start` with `"in"`, status `approved`).

**After shipping.** Set `review on` 14 days after the merge. When `due`
lists it, read the outcome (the metric, usage, support, the client's word),
write keep, iterate or revert with the reason in its Pitches row, and mark
it `reviewed`; an iterate becomes a new row.

**Sharing.** When Tom asks to give someone the Sheet, share its folder
(the Doc comes with it):
`gws drive permissions create --params '{"fileId": "<folder id>", "sendNotificationEmail": false}' --json '{"role": "writer", "type": "user", "emailAddress": "<email>"}'`.
