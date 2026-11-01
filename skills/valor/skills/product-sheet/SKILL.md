---
name: product-sheet
description: Keep a product room's backlog Sheet (Google Sheets, valor@yuda.me) to Yudame's product standard - create one from the template for a new product room, add, rate and rank items, run the readiness review that approves an item for build, chase blockers, and review what shipped. Use in a product room, or when setting one up.
---

# The product Sheet

One Google Sheet per product room, owned by valor@yuda.me, copied from the
template "Product sheet template" (`TEMPLATE` in `sheet.py`). The product
role (`skills/sdlc/roles/product.md`) says how to act; this says how the
Sheet is kept. The standard comes from Tom's review of the first room
(2026-11-01): no scores or decimals, lanes first, rank only what is ready,
blockers listed apart with owners, a goal on top, a review after every
ship, and Valor as approver once a full readiness review passes.

`sheet.py` beside this file does the mechanical parts through `gws`; for
anything else use `gws sheets spreadsheets values get|update|append` on the
ranges below. Read the Sheet before changing it; write whole rows.

## Tabs

- **Guide**: the rules in brief, for people.
- **Goal** (field, value): Product goal, Outcome metric, Baseline,
  Target, Measured by, Review on, Set by, Now, Next, Later, Strategy,
  WIP limit (default 2). The people who own the product set the goal and
  metric; Valor proposes and asks, never fills them in alone. Strategy
  holds the roadmap-level lenses: what an incumbent proves users want
  (second-mover) and what to eliminate, reduce, raise, create (Blue Ocean).
- **Queue**, one row per item: `id` (P-001, never reused), `lane`
  (`mandatory` or `bet`), `rank` (ready rows only), `item`, `goal` (the
  goal or Now theme it serves; blank only in the mandatory lane), `value`
  (`high`, `medium`, `low`), `appetite` (`small` up to 1 week, `medium` up
  to 3, `large` up to 6: a cap, not an estimate), `due` (a real date
  someone committed to, else blank), `status`, `blocked by` (the open
  blockers in short, else blank), `issue`, `dev thread`, `review on`.
- **Pitches**, one row per shaped or shaping item, by `id`: `item`, `job`
  (who, what progress, what happens if we do nothing), `problem`,
  `evidence` (each line tagged observed, reported or assumed), `solution`
  (elements and flow, with the code it touches), `rabbit holes` (each with
  its patch), `no-gos`, `acceptance` (testable statements), `builder
  questions` (each question, its answer, who answered and when), `hook`
  (optional: trigger, action, reward, investment, and the dark-pattern
  screen), `readiness` (the last review: date, result, what failed).
- **Blocked**, one row per blocker: `item` (id), `blocker`, `kind`
  (`human`, `product`, `eng`), `owner` (a person, or the item or issue it
  waits on), `asked on`, `chase on`, `chased` (dates), `status` (`open`,
  `cleared`).
- **Archive**: dropped items, with `why`.

## Statuses, and what each needs

| Status | Needs |
|---|---|
| `raw` | an item name and the problem as heard |
| `shaping` | a Pitches row being written |
| `shaped` | job, problem with evidence, solution, rabbit holes, no-gos, acceptance written |
| `ready` | the readiness review passed (below); no open blocker |
| `approved` | posted in the dev chat and started there; `dev thread` set |
| `building` | a plan, branch or pull request exists |
| `shipped` | merged and live; `review on` set, 14 days on by default |
| `reviewed` | the review's decision (keep, iterate, revert) written in its Pitches row |

## Ranking

`python3 sheet.py order SHEET` sorts the Queue and numbers the ready rows,
printing every row that moved. The order: `approved` and `building` first;
then `ready`, the mandatory lane before bets, mandatory by `due` (none
last), bets by value then the smaller appetite; then `shaped`, `shaping`,
`raw`; `shipped` and `reviewed` last. Ties keep their place, so a
deliberate reorder by hand stands until the bands change. When you move a
row by hand, say in the room which rows it now sits above and why.

## Procedures

**A new product room.** When Tom opens a product channel for a project:
1. `python3 sheet.py new "<Product> product" --share tom@yuda.me`, then
   set the Goal tab's `Set by` to who owns the product.
2. Make sure Valor is in the channel (Tom invites it, or Valor creates a
   private `<project>-product` channel as itself and invites Tom, as the
   dev channels were made; docs/bridges/slack.md).
3. In the project's private spec (`~/Desktop/Valor/projects/<name>.toml`,
   docs/plans/private-projects.md): add the channel to `chats` (not
   `mentions`), `rooms` naming the Sheet's URL and the dev chat's id, and
   `chat_roles` `product`. Load it with `Spec.load` and render the role.
4. In the room, ask for the product goal and outcome metric first: every
   bet's readiness depends on it.

**Adding an item.** Read the code and the issues first; if it exists or
shipped, say so instead. Otherwise append a Queue row (next free id,
status `raw`, lane, value and appetite as your first read with the
evidence for them) and, once there is more than a sentence, a Pitches row.
Merge duplicates into the older id.

**Blockers.** Each dependency is a Blocked row. Ask the owner in the room
(or where they are), set `asked on` to today and `chase on` three days on.
`python3 sheet.py due SHEET` lists what to chase today: chase once, then at
the second chase take it to Tom in the room. Clear the row when it is done
and remove it from the item's `blocked by`.

**The readiness review.** Run it in full for the top candidate, writing
each check's result in the item's `readiness` cell. It passes only when
every check does:
1. **Goal.** A bet names the product goal or Now theme it serves, and
   the Goal tab has a goal set by its owner.
2. **Job.** The job line names who, what progress, and the cost of doing
   nothing; the item stays inside it.
3. **Evidence.** The problem has at least one `observed` or `reported`
   line; value does not rest on an assumption alone.
4. **Appetite.** Fixed, and the solution fits inside it.
5. **Solution.** Read against the current code: the files, models and
   views it touches are named and exist; nothing it needs is missing.
6. **Rabbit holes and no-gos.** Each rabbit hole has its patch; the
   no-gos close off the scope creep.
7. **Acceptance.** Testable statements a reviewer can check, and the
   question the post-ship review will ask.
8. **Builder questions.** Write every question an engineer would ask
   before starting (data, edge cases, permissions, copy and translation,
   migrations, rollout). Answer each from the code and docs; a question
   only a person can answer (policy, wording, priority, a client's
   preference) is asked of that person, and the item waits for the answer.
   Never answer one yourself on their behalf.
9. **Blockers.** None open for it on the Blocked tab.
10. **Engagement screen.** If it adds a habit loop or a prompt, it has no
    dark pattern (false urgency, nagging, hidden costs, hard exits).
11. **Capacity.** Fewer `approved` and `building` items than the Goal
    tab's WIP limit.
12. **First.** It is the top `ready` row after `order`.

A pass makes the item `ready`; then approve it as the role says (post in
the dev chat, `task.start` with `"in"`, status `approved`).

**After shipping.** Set `review on` 14 days after the merge. When `due`
lists it, read the outcome (the metric, usage, support, the client's word),
write keep, iterate or revert with the reason in its Pitches row, and mark
it `reviewed`; an iterate becomes a new row.

**Sharing.** When Tom asks to give someone the Sheet:
`gws drive permissions create --params '{"fileId": "<id>", "sendNotificationEmail": false}' --json '{"role": "writer", "type": "user", "emailAddress": "<email>"}'`.
