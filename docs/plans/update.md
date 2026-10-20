---
status: Planning
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3647
revision_applied: true
revision_applied_at: 2026-10-20T06:19:34Z
---
# Update: every Mac rolls to `main`, on request and overnight

Tom, 2026-10-20: "in the old system, there was an /update command that valor could run or the
user at the machine could run in claude code. we could do something like that, but an overnight
auto rollout would also be nice." Rule 2: the old system had both (`~/src/_archive_/ai-dinosaur`:
`.claude/commands/update.md`; `com.valor.update` every 12 hours, `docs/features/remote-update.md`).

## What the code does today

- A rollout starts only from a merge outcome in this Mac's ledger naming its checkout's push URL
  and branch (`MERGE_OUTCOMES`, `core/serve.py:456`); a merge made elsewhere or a developer's push
  rolls nowhere else. The Pirate was moved by hand on 2026-10-20.
- The rest is what an update needs, keyed on a `Merge` (`effect_id`, `task_id`, `remote`,
  `branch`, `sha`): `prepare`, backup, fast-forward, sync, migrate, `rollout.restarting`; the new
  kernel's `_rolled` (`core/serve.py:416`) ends each open restarting/failed `effect_id` whose sha it
  contains, then kicks stale bridges. All but its hold.

## What will be built

1. **An update is a rollout to the tip, keyed by its request.** `python -m core update` appends
   `update.requested` `{request_id, by, via, reply?}` on a fixed stream `updates`, as `routines`
   has one: no task, no judge step, nothing added to `active()` or `notices.owe`. Each wake `roll`
   reads requests with no `rollout.ended` by that id, fetches the checkout's push URL and branch
   (`git.push_url`, `git.branch`, `rollout.fetch`) and builds `Merge(request_id, "updates", row,
   remote, branch, REF's tip)` beside the merge outcomes. One `judge` returns as contained in
   `started` gets `rollout.ended` `up_to_date` at once; the rest takes the one path (rule 4). A
   merge due with it is on the tip, so `prepare` rolls the tip and `covers` the merge. Every
   `rollout.*` row goes on `updates` with the request id as `effect_id`, so `_rolled`, the bridge
   kick, `waiting`, `failures` and the notice after `NOTICE_AFTER` work unchanged.
2. **`/update` in Claude Code:** `.claude/commands/update.md` runs `python -m core update --wait`,
   which polls `updates` for its id's `rollout.ended` or first `rollout.failed` and prints the old
   and new commits, the bridges restarted, or the failed step.
3. **`/update` in a chat:** `intake._bind`'s first check, before `team_spec`, takes a message of
   exactly `/update`: `_bound(p, None, "update")` and a request with `via` the channel and `reply`
   `{channel, chat_id, message_id}`. No sender check (a limit is governance). Each wake `roll`
   first writes the replies owed: for a request with `reply` and a `rollout.ended` or `failed`,
   `notices.request(conn, "updates", kind="update", about_key=f"update:{id}", reply_to, channel,
   chat_id)`, so after a restart the new kernel replies. A turn cannot request one (the turn
   profile denies the machine cluster); it says so in its delivery.
4. **Overnight:** `routines/update/routine.toml`, runner `update`, 03:00, `model = "frontier"`
   (`load` requires one; no model is called). `update_runner`, in `_routine_runners`, writes one
   request (`by: routine`) and returns `Ran(None, "started", ...)`. A failure reaches Tom by the
   existing notice; a quiet night is silent.
5. **A restart interrupts, never waits** (Pi's durable package, `pi-durable.pdf`, 2026-10-17; Tom,
   2026-10-20: "a stuck process that needs the update gets fixed" and "we gain telemetry on how
   good our durability is"). `_apply` backs up, fast-forwards, syncs and migrates with every job
   running, and cancels every job, as `close` does, only just before `rollout.restarting`, which
   names them (`interrupted`: task id, kind). A failed step steps back and cancels nothing.
   Deleted: `_holding`, `restart_due` (in `roll`, `schedule`, and `tick`'s handler,
   `core/serve.py:536`) and the "holding new jobs" path. Holds measured on the Cowboy (21
   rollouts): median 0.1 min, longest 8.7. Each kind comes back in the new kernel:
   - turn (foreground, background, judge, clarify): `recover` ends it `interrupted`; the next
     step resumes with "Continue." (`core/session.py:1206`).
   - merge or push: the dangling intent is reconciled (`broker.reconcile`).
   - check: no outcome row, and the new process's empty `seen` steps the task again.
   - provisioning: no `workspace.provisioned`, so `_ready` provisions again.
   - recollect: `recover` recollects the uncollected turn.
   One that does not come back is a defect fixed in this build; no other kind is in scope. In the
   seconds between fast-forward and cancel a job may start a process from the moved checkout; it
   is cancelled and resumed with the rest.

Docs: the `core/rollout.py`, `core/serve.py` (`:60-75`) and `_apply` docstrings,
`core/README.md:68`, `docs/architecture.md:451`, `docs/routines.md`; `docs/rollout.md` step 3
says the checkout is clean (`git status --short` empty) before step 9 bootstraps the update
routine. An update runs uncommitted edits in paths `prepare` does not check (it stops only on
`uv.lock`, `pyproject.toml`, `core/schema.sql`): accepted on the record, no guard added.

## Tests

- A restart with a foreground turn, a check and a provisioning running cancels all three only at
  `rollout.restarting`, names them in `interrupted`, and after `recover` each task steps again.
- A migrate failure leaves a running foreground turn running and writes `rollout.failed`.
- An update to a tip ahead of `started` restarts; the new process's `recover` writes its
  `rollout.ended` on `updates` and kicks a stale bridge. A contained tip ends `up_to_date`.
- A request and a merge due together roll once, to the tip; the merge ends `rolled_by` the request.
- A failed fetch writes `rollout.failed` on `updates`, is retried, and notices after `NOTICE_AFTER`.
- Exactly `/update` in a team chat becomes a request and, once ended, a reply in that chat;
  `/update please` is a message.
- The update routine's plist prints 03:00; its run writes one request and starts no task.

## Done

Tom has used an update (any trigger) on a real Mac, recorded here with the date (rule 5).

## Open

The Cowboy's checkout holds another session's uncommitted edits, against step 3's "never commit
in it". Tom: do its interactive sessions move to a worktree before its update routine starts?

No governance added.
