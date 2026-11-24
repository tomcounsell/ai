# Every Mac current, its dropped messages handed on, and a failing update reported

Task b64e3b80c04f, the follow-up to 20b3b69a569c. Operations on the four
Macs plus three small changes in this repo. The repository is public: no
client, person or chat id goes in a commit; ops scripts stay in the task's
`ops/`, outside the repo.

## Found (observed 2026-11-24, UTC)

- Fix `9a2a41096` (app-posted Slack messages) merged 06:59. The Cowboy
  runs it. The Captain is at `336a5660b`, the Pirate and the Bald at
  `37d54a137`: none has it yet (merges roll only on the merging Mac; the
  rest wait for an update). Their last rollouts succeeded; "ahead N" in
  `git status` is a stale `origin/main`, not a local commit.
- Who wrote to the Captain's `~/src/ai`: two PsyOPTIMAL tasks, both told
  to. Task 85346cd18c7a (2026-11-13 17:47) made branch
  `valor/85346cd1-sheet-order` and a worktree of `~/src/ai` in its own
  workspace and committed the `order` fix there. On 2026-11-21 the
  PsyOPTIMAL chat session started 49df20ab251e with "rebase commit
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
3. **A failing update, reported by the watch the way low disk is**
   (Brief item 4; the incident: 2,775 failed rollouts on the Captain
   2026-11-21 to 11-24, heard by no session). `health.report` gains
   `update`: None, or `{since, step, reason}`. It comes from the
   ledger's open `rollout.failed` rows, those whose `effect_id` has no
   `rollout.ended`: `since` is the oldest one's `at`, step and reason the
   newest. The `owed` ended rows close a failure once the running commit
   contains it, so a Mac that later updates reports nothing.
   `routines/watch/runner.py`: `_disk` becomes one daily report per Mac
   covering disk and update. The update report goes through
   `notices.request(..., about_key=f"update:{host}:{day}")`, for each read
   target and for this Mac (read on `ctx.conn`). It is not part of
   `healthy` and holds nothing. Tests (`tests/test_health.py`, beside the
   disk test):
   - a target whose health has `update` is reported once that day, again
     the next day, and not after it clears;
   - this Mac's own open failure is reported;
   - `report()` gives None with no rows and with a failure later ended
     (done, up_to_date or superseded); it gives the oldest open `since`
     across two failing effects, and an update request's fetch failure
     (no sha) counts.
   Docs: `docs/routines.md` watch paragraph, the watch docstring.
   A Mac still on code before this change cannot report it. That is why
   step 4 brings every Mac current first.

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

No auto-repair of a diverged checkout, no sandbox rule on `~/src/ai`, no
new alert channel. The slow Yudame gap-fill budget (20b3b69a569c's
product note) is named, not fixed.

## Stakes

Kernel-adjacent and reversible: one Brief line, one health field, one
watch report, and a skill fix whose error would scramble a product
Sheet's Queue. The Sheet can be restored from its version history.
