---
status: Planning
type: feature
tracking: https://github.com/tomcounsell/ai/issues/3647
revision_applied: true
revision_applied_at: 2026-10-20T09:48:39Z
---
# Update: every Mac rolls to `main`, on request and overnight

Tom, 2026-10-20: "an /update command that valor could run or the user at the machine could run in
claude code ... an overnight auto rollout would also be nice." The old system had both (rule 2;
`ai-dinosaur`: `.claude/commands/update.md`, `com.valor.update`). Interrupting jobs moved to
`docs/plans/rollout-interrupt.md` (today's hold stays); "the update should also kill and restart
the web dashboard."

## What the code does today

A rollout starts only from a merge outcome in this Mac's ledger (`MERGE_OUTCOMES`, `core/serve.py:456`);
the rest (`judge`, `prepare`, the hold, `_apply`, `_rolled` `:402`, `restart_bridges` `:368`) is keyed
on a `Merge` and does not change. The dashboard (`python -m ui`, port 8790) has no launchd job.

## What will be built

1. **An update is a rollout to the tip, keyed by its request.** `python -m core update`, the room and
   the routine call one `request_update`: under `ledger.lock(conn, "updates")` it returns the open
   `update.requested` (no `rollout.ended` by its id) on the fixed stream `updates`, else appends
   `{request_id, by, via, reply?}`; no superseded path, no task. In `roll`, before the `if not
   merges` return (`:579`), unless it is in `self.waiting` (cleared each tick, `:523`):
   `rollout.fetch` the push URL and branch. A tip `git.is_ancestor` of `self.started` is
   `_ended(conn, req, outcome="up_to_date", sha=tip)`, not appended (`judge` would only mark it
   judged, `core/rollout.py:258`, leaving it open); any other is `Merge(request_id, "updates", row,
   remote, branch, tip)` in `merges`. A `GitError` or `CredentialError` is `_failed(conn, req,
   ["fetch"], "fetch", ...)`. The `judge` `GitError` handler (`:583`) blames only `m.task_id !=
   "updates"`. `prepare` fetches again (accepted). The rest is the one path (rule 4), hold
   included; a merge due with it is `covered`. Every `rollout.*` row goes on `updates`, `effect_id`
   the request id, so `_rolled`, the kicks, `waiting`, `failures` and the `NOTICE_AFTER` notice work
   unchanged, worded "I could not update my own system: …" (`:740,999`). `from` is on
   `rollout.restarting` (`:672`); the fast-forward path adds to its `rollout.ended` the `before`
   `rollout.fast_forward` returns (`:620` drops it).
2. **The dashboard restarts with every restarting rollout.** `python -m ui --plist` prints
   `com.valor.ui` from `plistlib.loads(serve.plist())` (`core/serve.py:1158`), replacing `Label`,
   `ProgramArguments` (`-m ui`) and the log path, dropping `ProcessType`. `restart_bridges` kicks
   `rollout.UI = "com.valor.ui"` after the bridges, with no lock check (the dashboard holds no
   connection, `ui/app.py:245`), into `restarted` / `restart_failed`. It runs on any start that ends
   a rollout (`:355-357`), `by: started` included, so `done["bridges"]` lists it then too. The first
   rollout shipping this precedes step 9's job: one `restart_failed`, accepted.
3. **`/update` in Claude Code:** `.claude/commands/update.md` runs `python -m core update --wait`,
   polling `updates` for its id's `rollout.ended` or first `rollout.failed`: `from` and `head`,
   services restarted, or the failed step. A hold is silent (`:612-615`) and the kernel may be down,
   so after 900 s `--wait` exits 1 ("still open: held, or no kernel"), writing nothing.
4. **`/update` in Valor's engineering room.** In `_bind` (`core/intake.py:599-603`), in `if team is
   not None:` before `_chat`, a message in `UPDATE_CHAT = "telegram:-1009417593398"`
   (`projects/valor.toml:36`) fully matching `/update(@\w+)?` is `_bound(p, None, "update")` and a
   `request_update` with `via` and `reply` `{channel, chat_id, message_id}`; one sent while a request
   is open joins it, unreplied (accepted). Any verified member may (Tom, 2026-10-20): `team_spec`
   (`:409-418`) checks verified, chat kind, not Tom's own chat. Hard-coded: `Spec` has no field
   naming a room's job (rules 4, 8). Each Mac has its own kernel database, so only the Mac whose
   bridge owns that chat gets it; the others update by routine or Claude Code. Each wake `roll`
   first selects in SQL the ended requests with `reply` and no `notice.requested` with `about_key =
   'update:' || id`, and sends each `notices.request(conn, "updates", kind="update",
   about_key=f"update:{id}", text=from→head / up to date, reply_to, channel, chat_id)`. A failure
   gets no second reply: `_failed`'s notice goes to the operator chat (`rollout` is in `TOMS`) and
   the sender may not be Tom, so for a request with `reply` that one notice goes to the room.
5. **Overnight:** `routines/update/routine.toml` (`core/routines.py:101-114`): `name`/`runner = "update"`,
   `ceiling = "act"`, `model = "frontier"` (required, unused), `mission_item = "1; Tom's ask (rule 2)"`,
   `instruction`, `created = 2026-10-20`, `[schedule] hour = 3, minute = 0`. `update_runner`
   (`core/__main__.py:505`) calls `request_update` (`by: routine`), returns `Ran(None, "started",
   ...)`; `_used` skips taskless runs (`:664`), so expiry lists it for Tom every 90 days: accepted.
6. **The guard ledger entry:** `core/guards.py` `SEEDED` gains `update.room`: `name` ("/update binds
   only in Valor's engineering room"), `incident` ("none; accepted by Tom 2026-10-20"),
   `mission_items: [1]`, `source` and `via` ("docs/plans/update.md, Grant"), `granted_at`
   2026-10-20, `expires` 2027-01-18, `note` (Tom's grant), `firing_record: False`, commented: the
   firing fold reads only pipeline verdict rows (`machine.VERDICT_ROWS`, `core/routines.py:575`) and
   a refused `/update` is an ordinary message; a row of its own would be code for no use.

**Grant** (one instance: item 4's room restriction). Tom, 2026-10-20. Incident: none, accepted by Tom;
the risk, a stranger restarting the kernel by DM or email and cancelling background turns, came from
the round-2 critique. Mission item 1 (work in flight is not cut by someone else).

Docs: docstrings of `core/rollout.py`, `core/serve.py` (`:60-75`), `ui/__main__.py`, `core/guards.py`;
`core/README.md:68`, `ui/README.md`, `docs/architecture.md:451`, `docs/routines.md`, `core/intake.py`'s
binding table, `core/__main__.py`'s commands. `docs/rollout.md`: step 3 a clean checkout; step 9
stops a hand-started dashboard on 8790 (else the job crash-loops), then adds `com.valor.ui` and the
routine; one line for a kernel crash-looping after a rollout (today's risk for any restarting merge):
`git -C ~/src/ai reset --keep <from>`, `launchctl kickstart -k $D/com.valor.kernel`. Uncommitted edits
outside `uv.lock`, `pyproject.toml`, `core/schema.sql` run unchecked: accepted.

## Tests

- A tip ahead of `started` restarts; `recover` ends it and kicks a stale bridge and `com.valor.ui`.
  A tip equal to `started` ends `up_to_date` that wake. A fast-forward-only one ends with `from`.
- A request and a merge due together roll once; the merge ends `rolled_by` the request. A `judge`
  `GitError` fails the merge only. A second `request_update` while one is open returns its id.
- A failed fetch or `CredentialError` writes `rollout.failed` at `fetch` on `updates`, is not
  fetched again before the next tick, and after `NOTICE_AFTER` notices once in update wording, to
  the room when the request has a `reply`. `--wait` past its deadline exits 1, writing nothing.
- `/update` and `/update@x` in the engineering room become a request and one reply (none twice
  across a restart); `/update please` there, and `/update` elsewhere, bind as before.
- The routine's run writes one request, no task. `python -m ui --plist` prints `com.valor.ui`.

## Done

Tom has used an update (any trigger) on a real Mac, recorded here with the date (rule 5).

## Open

- The Cowboy's checkout holds another session's uncommitted edits: do its interactive sessions move
  to a worktree before its routine starts? Does not block the build.
