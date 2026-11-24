# Every Mac current, its dropped messages handed on, and a failing update self-healed

Task b64e3b80c04f, the follow-up to 20b3b69a569c. Operations on the four
Macs plus three changes in this repo: the kept sheet fix, the Repos
route, and a failing update self-healed by the watch. The repository is public: no
client, person or chat id goes in a commit; ops scripts stay in the task's
`ops/`, outside the repo.

## Found (observed 2026-11-24, UTC)

- Fix `9a2a41096` (app-posted Slack messages) merged 06:59. The Cowboy
  runs it. The Captain is at `336a5660b`, the Pirate and the Bald at
  `37d54a137`: none has it yet (merges roll only on the merging Mac; the
  rest wait for an update). Their last rollouts succeeded; "ahead N" in
  `git status` is a stale `origin/main`, not a local commit.
- Who wrote to the Captain's `~/src/ai`: two tasks of a client project, both told
  to. Task 85346cd18c7a (2026-11-13 17:47) made branch
  `valor/85346cd1-sheet-order` and a worktree of `~/src/ai` in its own
  workspace and committed the `order` fix there. On 2026-11-21 the
  client project's chat session started 49df20ab251e with "rebase commit
  ea83aeadb onto the current main of /Users/valorengels/src/ai ... get it
  onto main by that repo's normal route"; its build rebased (16:37) and
  fast-forwarded the live `main` (reflog: `merge
  valor/85346cd1-sheet-order`, 16:39). Turns' sandbox allows writes by
  default (developer parity), so nothing stopped it, and nothing should.
  The cause: a room on another project has no route to a change in
  Valor's own code. Its Repos line lists only the projects its spec names,
  and no spec names `valor`, so the session sent the work to the only
  copy of Valor's code it could see. `--project valor` is not limited to
  listed projects (`tasks.start_child` checks no list), and a valor task
  can run on any Mac.
- `7fb45956e` reviewed: it cherry-picks cleanly on main (nothing has
  touched product-sheet since its base `fcb4edd48`), its 28 tests pass,
  and it names no client. `order` now rewrites the Queue with
  `updateCells` over A:Z (value, note, rich text, chips, link, number
  format), so formulas, notes and links move with their row. One known
  debt, accepted: the rank cell keeps only its note, so a number format on
  rank is cleared. Sound: land it.

## What will be built

1. **Land `7fb45956e`** on this branch (`cherry-pick -x`), with its tests
   (`tests/test_product_sheet.py`, 28 cases: link formula and note move,
   rich text and whole-cell links, a hyperlink-only cell, a typed date, a
   note-only row, short-over-long clearing, rank cleared when not ready).
2. **The route to Valor's code.** `tasks.repos()` always adds the `valor`
   project to a chat session's Repos line when the session's own project is
   another one, also for a project that lists none. The line says that a change to Valor's
   own code (its skills, tools or kernel) is a task under `--project
   valor`. That task's merge reaches every Mac through the update. A
   commit in a Mac's live `~/src/ai` stops that Mac's updates. This adds
   a route, not a limit.
   Tests (`tests/test_act.py` beside the existing Repos check): a project
   listing none gets a line naming its own project and `valor`; a project
   listing others gets them and `valor`; the valor project's own sessions
   get no added line; a spec that does not load still gives None.
3. **A failing update, repaired by the watch** (Brief item 4, revised by
   Tom, 2026-11-24 09:20 UTC: "don't report to me. self-heal"; the
   incident: 2,775 failed rollouts on the Captain, 2026-11-21 to 11-24,
   that no session heard). `health.report` gains `update` (the open
   `rollout.failed` rows, those with no `rollout.ended`: the oldest one's
   `since`, the newest one's step and reason) and `kept` (the
   `rollout.realigned` rows). `rollout.realign` / `python -m core
   realign` handles a diverged checkout: it keeps the head on
   `<machine>-local-<sha>`, pushes that branch to origin, runs `reset
   --keep` onto the fetched tip, and requests an update. The watch reports
   no failing update. For each read target (only a peer repairs: every Mac is another's
   target, so a failure is seen once), it starts
   a `valor` task while it still fails, one task at a time, at most one a day per Mac (a repair that ended with the failure still open is tried again the next day), as the routine's
   own work. That task finds the cause, runs realign where the checkout
   has diverged, and confirms the next update. Its end is a quiet result,
   and only a question for what only Tom holds reaches the operator chat.
   Each kept branch gets one task to land it or drop it. The low-disk
   report is unchanged. Tests:
   - one repair, not a report, for a target, none for this Mac's own, none
     more while it is open;
   - a repair that ended with the failure still open is followed by another
     the next day, at most one a day, and none once it clears;
   - one land-or-drop task per kept branch;
   - realign leaves a diverged checkout on the tip, with its commit on a
     branch both locally and on origin and untracked files kept; it does
     nothing to a checkout that is behind or current;
   - a repair's end is quiet, and only its question reaches the operator
     session;
   - `update` and `kept` are empty while the database probe fails.

## Operations (through Valor.app's `valor-run`, a one-off gui job, as 20b3b69a569c did)

4. **Every Mac current.** After merge: `python -m core update` on the
   Captain, the Pirate and the Bald. Each one's `rollout.ended` must show
   `done` at or past the merge, `kernel.kicked` or the row's bridges must
   show the Slack bridge restarted, and its connection stamp must be the
   new head. On the Cowboy, check the Slack bridge's stamp too. A Mac that
   fails or has diverged: find why, keep any local commit on a
   `<mac>-local-<sha>` branch (and push it), then `reset --keep` onto
   origin/main. Untracked files are left alone: the Captain has two stray
   files, `5433` and `Valor`, named in the report and not deleted.
5. **Dropped messages.** For each Mac's Slack token, check whose user it
   is. Then scan the chats each Mac's specs list (the Bald's, the
   Pirate's, the Cowboy's), back to 2026-11-01, for a person's message
   that carries a `bot_id` and has no `message.received` row on the Mac
   that owns it. Reuse 20b3b69a569c's `scan.py`, extended with Slack's
   cursor paging. For each one nobody answered, run the bridge's own
   `_take` → `inbound.build` → `kernel.receive` on that Mac
   (`handon.py`), so that chat's session answers. Confirm each from the
   ledger. Post nothing.
6. Report: per Mac, its head before and after and its bridge restart;
   messages found and handed on; who wrote `~/src/ai` and the route now
   given; the kept branch landed.

## Out of scope

No sandbox rule on `~/src/ai`, no new alert channel, no report of a
failing update. The slow Yudame gap-fill budget (20b3b69a569c's
product note) is named, not fixed.

## Stakes

Kernel-adjacent and reversible: one Brief line, two health fields, a
repair task the watch starts, `python -m core realign` (it moves a live
checkout, its commits kept on a pushed branch), and a skill fix whose error would scramble a product
Sheet's Queue. The Sheet can be restored from its version history.
