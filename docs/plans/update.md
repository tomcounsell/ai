---
status: Planning
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3647
revision_applied: true
revision_applied_at: 2026-10-20T08:44:05Z
---
# Update: every Mac rolls to `main`, on request and overnight

Tom, 2026-10-20: "in the old system, there was an /update command that valor could run or the
user at the machine could run in claude code. we could do something like that, but an overnight
auto rollout would also be nice." Rule 2: the old system had both (`~/src/_archive_/ai-dinosaur`:
`.claude/commands/update.md`; `com.valor.update` every 12 hours). The user, 2026-10-20: interrupting
jobs moves to `docs/plans/rollout-interrupt.md` (an update keeps today's hold), and "the update
should also kill and restart the web dashboard."

## What the code does today

- A rollout starts only from a merge outcome in this Mac's ledger naming its checkout's push URL
  and branch (`MERGE_OUTCOMES`, `core/serve.py:456`); a merge made elsewhere rolls nowhere else.
- The rest is keyed on a `Merge` (`effect_id`, `task_id`, `remote`, `branch`, `sha`): `judge`,
  `prepare`, the hold, `_apply`; the new kernel's `_rolled` (`core/serve.py:402`) ends each open
  restarting/failed `effect_id` its commit contains, and `restart_bridges` (`:368`) kicks stale
  bridges through `rollout.kick` (`launchctl kickstart -k`). None of it changes.
- The dashboard (`python -m ui`, 127.0.0.1:8790) has no launchd job; the Cowboy's is hand-started.

## What will be built

1. **An update is a rollout to the tip, keyed by its request.** `python -m core update` appends
   `update.requested` `{request_id, by, via, reply?}` on a fixed stream `updates`: no task, nothing
   added to `active()` or `notices.owe`. In `roll`, before the `if not merges` return
   (`core/serve.py:579`): read requests with no `rollout.ended` by that id; all but the newest end
   `superseded` naming it. Unless the newest is in `self.waiting` (cleared each `serve_tick_s`, `:523`,
   so origin is not hit every wake), read `git.push_url`/`git.branch`, `rollout.fetch`, and append
   `Merge(request_id, "updates", row, remote, branch, REF's tip)` to `merges`; a `GitError` or
   `credentials.CredentialError` (`rollout.fetch` → `header_file`, `core/rollout.py:281`) goes to
   `_failed(conn, request, ["fetch"], "fetch", ...)` (`sha` empty), so it waits for the next
   tick. The `GitError` handler around `judge` blames only merges with `m.task_id != "updates"`
   (`:590`). `prepare` fetches again (one extra fetch per update, accepted). A tip `judge` finds
   contained ends `up_to_date`; the rest takes the one path (rule 4), hold included; a merge due
   with it is `covered`. Every `rollout.*` row goes on `updates` with the request id as `effect_id`, so `_rolled`, the kicks,
   `waiting`, `failures` and the notice after `NOTICE_AFTER` work unchanged; for
   `m.task_id == "updates"` that notice and the mixed one (`:740,999`) say "I could not update my
   own system: …", not "it is merged, but…".
2. **The dashboard restarts with every restarting rollout.** `python -m ui --plist` prints
   `com.valor.ui` from the kernel's printer (`plistlib.loads(serve.plist())`, `core/serve.py:1158`),
   replacing `Label`, `ProgramArguments` (`-m ui`) and the log path, dropping `ProcessType`.
   `restart_bridges` kicks `rollout.UI = "com.valor.ui"` after the bridges and reports it in
   `restarted` / `restart_failed`, so `rollout.ended` and `--wait` list it. Not by closure: a stamp
   needs a held connection the dashboard lacks (it connects per request, `ui/app.py:245`), and a
   `ui/`-only diff already restarts (not in `QUIET_DIRS`); three lines against a stamp and a second root.
3. **`/update` in Claude Code:** `.claude/commands/update.md` runs `python -m core update --wait`,
   polling `updates` for its id's `rollout.ended` or first `rollout.failed`: old and new commits,
   services restarted, or the failed step. A hold is silent (not a failure, `:612-615`) and the kernel
   may be down, so after 900 s `--wait` exits 1 ("still open: held, or no kernel"), writing no row and
   leaving the request open. A long hold is `rollout-interrupt.md`'s fix.
4. **`/update` in Valor's engineering room** (the user's direction, 2026-10-20). In `_bind`
   (`core/intake.py:599-603`), inside `if team is not None:` before `_chat`. `team_spec`
   (`:409-418`) checks only verified, chat kind and not Tom's own chat; `_from_operator` comes later
   (`:604`). So any verified member of that room may update the Cowboy; the plan adds no operator
   check (Open). A message in `UPDATE_CHAT = "telegram:-1003449100931"` (`projects/valor.toml:36`)
   whose text fully matches `/update(@\w+)?` is `_bound(p, None, "update")` and a request with `via`
   the channel and `reply` `{channel, chat_id, message_id}`; anything else is an ordinary message.
   Hard-coded: `Spec` has no field naming a room's job (rules 4, 8). Each wake `roll` first writes the
   replies owed: an ended request with `reply` gets `notices.request(conn, "updates", kind="update",
   about_key=f"update:{id}", text=old→new / up to date / superseded by, reply_to, channel, chat_id)`;
   a repeat `about_key` is dropped (`core/notices.py:85-93`), so it replies once, across a restart. A
   failure replies from `_failed` at `NOTICE_AFTER`, `about_key=f"update:{id}:failed"`. A turn cannot
   request one (the turn profile denies the machine cluster).
5. **Overnight:** `routines/update/routine.toml`: `name = "update"`, `runner = "update"`,
   `ceiling = "act"`, `model = "frontier"` (required; no model is called), `mission_item = "1; Tom's
   ask (rule 2)"`, `instruction`, `created = 2026-10-20`, `[schedule] hour = 3, minute = 0`
   (`core/routines.py:101-114`). `update_runner`, in `_routine_runners`, writes one request
   (`by: routine`; superseding covers a duplicate) and returns `Ran(None, "started", ...)`. `_used`
   skips runs with no task (`core/routines.py:664`), so expiry lists the routine for Tom every ninety
   days: accepted on record (rule 2 wants Tom to see it; zero code).

**Grant** (one instance: item 4's room restriction). Direction relayed as the user's, 2026-10-20;
awaiting Tom's own grant (CLAUDE.md requires Tom's grant). Incident: none has happened; the risk, a
stranger restarting the kernel by DM or email and cancelling its background turns, was found by the
round-2 critique. Mission item 1 (own outcomes: work in flight is not cut by someone else). Once Tom
grants, ledgered as a `core/guards.py` `SEEDED` entry `update.room`: `mission_items: [1]`,
`granted_at` the grant's date, `expires` ninety days on, its own `note` (Tom's words) and `via`
("docs/plans/update.md, Grant, seeded by migrate"), `firing_record: False`.

Docs: docstrings of `core/rollout.py`, `core/serve.py` (`:60-75`), `ui/__main__.py`, `core/guards.py`;
`core/README.md:68`, `ui/README.md`, `docs/architecture.md:451`, `docs/routines.md`, `core/intake.py`'s
binding table, `core/__main__.py`'s commands (`update [--wait]`); `docs/rollout.md` step 9 adds `ui`
(`com.valor.ui`) and the routine, step 3 a clean checkout. Uncommitted edits outside `uv.lock`,
`pyproject.toml`, `core/schema.sql` run unchecked: accepted on the record.

## Tests

- An update to a tip ahead of `started` restarts; the new `recover` ends it on `updates`, kicks a
  stale bridge and `com.valor.ui` (diff touching `ui/`). A contained tip ends `up_to_date`.
- A lone request, with no merge outcomes, is read and rolled.
- A request and a merge due together roll once, to the tip; the merge ends `rolled_by` the request.
  A `judge` `GitError` fails the merge, not the request. Two open requests: the older is `superseded`.
- A failed fetch or a `CredentialError` writes `rollout.failed` at `fetch` on `updates`, is not
  fetched again before the next tick, and after `NOTICE_AFTER` notices in update wording.
- `--wait` past its deadline exits 1 and writes nothing.
- `/update` and `/update@x` in the engineering room become a request and one reply (none twice
  across a restart); `/update please` there, and `/update` elsewhere, bind as before.
- The routine's plist prints 03:00; its run writes one request and starts no task. `python -m ui
  --plist` prints `com.valor.ui` running `-m ui`.

## Done

Tom has used an update (any trigger) on a real Mac, recorded here with the date (rule 5).

## Open

- Tom: grant item 4's room restriction yourself, accepting it without a past incident, or drop the
  room restriction (and say where chat `/update` binds instead). Until then the guard entry is not seeded.
- Tom: in that room, may any verified member send `/update`, or the operator only?
- The Cowboy's checkout holds another session's uncommitted edits. Do its interactive sessions move
  to a worktree before its update routine starts? Does not block the build.
