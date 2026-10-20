---
status: Planning
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3647
revision_applied: true
revision_applied_at: 2026-10-20T08:35:20Z
---
# Update: every Mac rolls to `main`, on request and overnight

Tom, 2026-10-20: "in the old system, there was an /update command that valor could run or the
user at the machine could run in claude code. we could do something like that, but an overnight
auto rollout would also be nice." Rule 2: the old system had both (`~/src/_archive_/ai-dinosaur`:
`.claude/commands/update.md`; `com.valor.update` every 12 hours, `docs/features/remote-update.md`).
Interrupting jobs on restart is split out to `docs/plans/rollout-interrupt.md` (the user,
2026-10-20); an update keeps today's hold.

## What the code does today

- A rollout starts only from a merge outcome in this Mac's ledger naming its checkout's push URL
  and branch (`MERGE_OUTCOMES`, `core/serve.py:456`); a merge made elsewhere or a developer's push
  rolls nowhere else. The Pirate was moved by hand on 2026-10-20.
- The rest is what an update needs, keyed on a `Merge` (`effect_id`, `task_id`, `remote`,
  `branch`, `sha`): `judge`, `prepare`, the hold (`_holding`, `restart_due`), `_apply` (cancel the
  background turns, backup, fast-forward, sync, migrate, `rollout.restarting`); the new kernel's
  `_rolled` (`core/serve.py:416`) ends each open restarting/failed `effect_id` whose sha it
  contains, then kicks stale bridges. None of it changes.

## What will be built

1. **An update is a rollout to the tip, keyed by its request.** `python -m core update` appends
   `update.requested` `{request_id, by, via, reply?}` on a fixed stream `updates`, as `routines`
   has one: no task, no judge step, nothing added to `active()` or `notices.owe`. Each wake `roll`
   reads requests with no `rollout.ended` by that id; all but the newest end `superseded` (an
   outcome `_ended` already writes, `core/serve.py:601-604`) naming it. Inside `roll`'s existing
   `try` around `judge` (`core/serve.py:581-596`), it reads the checkout's push URL and branch
   (`git.push_url`, `git.branch`), fetches (`rollout.fetch`) and builds `Merge(request_id,
   "updates", row, remote, branch, REF's tip)` beside the merge outcomes, so a `GitError` there
   goes through `_failed(conn, request, ["fetch"], "fetch", ...)` (`sha` empty when unread). One
   `judge` returns as contained in `started` ends `up_to_date` at once; the rest takes the one path
   (rule 4), hold included. A merge due with it is on the tip, so `prepare` rolls the tip and
   `covers` the merge. Every `rollout.*` row goes on `updates` with the request id as
   `effect_id`, so `_rolled`, the bridge kick, `waiting`, `failures` and the notice after
   `NOTICE_AFTER` work unchanged; for `m.task_id == "updates"` that notice (and the mixed one)
   says "I could not update my own system: …", not "it is merged, but…" (`core/serve.py:740,999`).
2. **`/update` in Claude Code:** `.claude/commands/update.md` runs `python -m core update --wait`,
   which polls `updates` for its id's `rollout.ended` or first `rollout.failed` and prints the old
   and new commits, the bridges restarted, or the failed step.
3. **`/update` in Valor's engineering room only** (the user's direction, 2026-10-20). In `_bind`
   (`core/intake.py:599-605`), inside `if team is not None:` before `_chat`, so after the existing
   team-chat / `_from_operator` filter: a message in `UPDATE_CHAT = "telegram:-1003449100931"`
   (`projects/valor.toml`: `rooms."telegram:-1003449100931" = "Engineering on Valor itself."`)
   whose text is exactly `/update` is `_bound(p, None, "update")` and a request with `via` the
   channel and `reply` `{channel, chat_id, message_id}`. `/update` anywhere else is an ordinary
   message. Hard-coded, not read from the spec: `Spec` has no field naming a room's job, and adding
   one is a new spec key read by one check for one chat (rules 4, 8). That chat is received on the
   Cowboy (`machine = "Valor the Cowboy"`), so it updates the Cowboy. Each wake `roll` first writes
   the replies owed: a request with `reply` and a `rollout.ended` gets `notices.request(conn,
   "updates", kind="update", about_key=f"update:{id}", text=old→new commit / up to date /
   superseded by, reply_to, channel, chat_id)`; a second notice with one `about_key` is dropped
   (`core/notices.py:85-93`), so it replies once, and after a restart the new kernel replies. A
   failure replies only from `_failed` once `tries` reaches `NOTICE_AFTER`, to the reply chat with
   `about_key=f"update:{id}:failed"`. A turn cannot request one (the turn profile denies the
   machine cluster); it says so in its delivery.
4. **Overnight:** `routines/update/routine.toml`, runner `update`, 03:00, `model = "frontier"`
   (`load` requires one; no model is called). `update_runner`, in `_routine_runners`, writes one
   request (`by: routine`) unless one is open, and returns `Ran(None, "started", ...)`. A failure
   reaches Tom by the existing notice; a quiet night is silent.

**Grant** (one instance, item 3's room restriction). Direction: the user, 2026-10-20.
Incident/risk: a stranger restarting the kernel by DM or email (found by round-2 critique).
Mission item served: keeping the kernel running (Constraint: Reliable stop, recovery, and
correction). Ledgered as a `core/guards.py` `SEEDED` entry `update.room` (`guard.granted` on the
`guards` stream, seeded by `migrate`), `granted_at` 2026-10-20, `expires` 2027-01-18,
`firing_record: False` (intake writes no verdict row); the expiry routine lists it for Tom.

Docs: the `core/rollout.py` and `core/serve.py` (`:60-75`) docstrings, `core/README.md:68`,
`docs/architecture.md:451`, `docs/routines.md`, `core/intake.py`'s binding table (a row for
`/update` in the engineering room), `core/__main__.py`'s command list (`update [--wait]`),
`core/guards.py`'s docstring (the seeded list); `docs/rollout.md` step 3 says the checkout is clean (`git status
--short` empty) before step 9 bootstraps the update routine. An update runs uncommitted edits in
paths `prepare` does not check (it stops only on `uv.lock`, `pyproject.toml`, `core/schema.sql`):
accepted on the record, no guard added.

## Tests

- An update to a tip ahead of `started` restarts; the new process's `recover` writes its
  `rollout.ended` on `updates` and kicks a stale bridge. A contained tip ends `up_to_date`.
- A request and a merge due together roll once, to the tip; the merge ends `rolled_by` the request.
- Two open requests: the older ends `superseded`; the routine writes none while one is open.
- An unreadable push URL or a failed fetch writes `rollout.failed` at `fetch` on `updates`, is
  retried, and after `NOTICE_AFTER` notices in update wording.
- Exactly `/update` in the engineering room becomes a request and, once ended, one reply there
  (none twice across a restart); `/update please` there, and `/update` in another team chat or
  Tom's DM, bind as before.
- The update routine's plist prints 03:00; its run writes one request and starts no task.

## Done

Tom has used an update (any trigger) on a real Mac, recorded here with the date (rule 5).

## Open

The Cowboy's checkout holds another session's uncommitted edits, against step 3's "never commit
in it". Tom: do its interactive sessions move to a worktree before its update routine starts?
Does not block the build.
