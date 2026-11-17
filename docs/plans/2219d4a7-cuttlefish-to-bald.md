# Cuttlefish to the Bald; the paused client project out of config

Tom, Slack DM 2026-11-17 06:28 UTC: move Cuttlefish to the Bald as a real
move (dependencies, database copy, system config), and permanently remove
the paused client project (named in the Brief, not here: this repo is
public) from Valor's config. Machine operations plus doc edits; no code
change is expected. A defect found on the way gets a fix and a test.

## Found at plan time (observed 2026-11-17, ssh from the Cowboy)

- **Bald:** kernel at `90e77a9ac` (current main). Kernel, telegram, slack,
  email, voice and ui jobs are running. Installed: Postgres 18.7 and 15,
  pgvector 0.8.7, redis, uv, node, gh, ffmpeg and the CLT SDK. 54 GB free.
  **Missing:** Python 3.12, which the spec's `UV_PYTHON` needs. Still to
  check: sentry-cli, gws, claude, byob, and gh push. `~/src/cuttlefish`
  is stale (May, `b7af012e`). There is a `cuttlefish` database (23 MB) on
  the Postgres 15 cluster.
- **Captain:** `~/src/cuttlefish` is at `db06996a` (Sep) with one
  uncommitted doc edit. It has `.env.local` and `.env.prod`, and
  `.env.local`'s `DATABASE_URL` points at a **hosted** database. There
  are `test_cuttlefish*` databases only (leftover test DBs). There is
  `~/work-vault/Cuttlefish` and no crontab. The non-Apple launchd jobs
  are vault-sync, brew-status, redis and pg15.
- **Paused client on the Bald:** three checkouts under `~/src` and two
  local databases. Its spec is `.paused`.

## Build, in order

1. **Bald stock and fixes.** Check every item in the Brief's list and
   install or repair what is missing. Already known: `uv python install
   3.12`. Then confirm gh push to the repo, `sentry-cli info`, the gws
   profile, `claude` with the yudame plugins, and byob in `~/.claude.json`.
2. **Inventory on the Captain.** For each item, record what it is, its
   size, and whether it moves:
   - the checkouts and every `CUTTLEFISH_DIR` user in the deep-research
     skill and the podcast episode path;
   - the `.env*` files and keys;
   - every database, on both clusters, that is not a test DB;
   - the spec dir's `cuttlefish/` folder;
   - Chrome and NotebookLM sign-ins;
   - launchd jobs;
   - the work-vault folder;
   - the kernel's per-project state: chat sessions, ledger rows, open
     tasks, questions and waits.
3. **Copy to the Bald.**
   - **Checkout:** bring the Bald's checkout to the remote's main. The
     Captain's uncommitted edit is reported, not pushed.
   - **Env files:** copied over ssh, mode 600, never printed.
   - **Databases:** each local dev database goes across with
     `pg_dump -Fc | pg_restore`. The hosted database is never touched,
     and nothing runs a migration with the copied env.
   - **Work vault:** pulled to match.
   - **Chrome sign-ins:** not copied. Google signs a copied profile out
     (rollout.md, step 9). The Bald's byob Chrome gets its own sign-in,
     or this is named as what cannot move and what it stops.
   - **Captain checkout:** kept on the Captain, since research may run
     there.
4. **Kernel state, read before the switch.** Ownership is read live
   from the synced spec on every message (`core/intake.py` `_taking`,
   `owned`), so a Mac switches when its iCloud copy syncs, not when it
   restarts.
   - **Stale marks on the Bald:** read the Bald's `telegram-seen.json`,
     `slack-seen.json` and `intake.lowest`/`lowest_ts` for the three
     chats. An old entry would make gap fill answer months of history.
     The overwrite in step 5 replaces any entry it finds.
   - **Board:** the Bald finds the existing `BOARD` canvas by its tab
     (`bridges/slack/send.py` `write_canvas`), so no second canvas is
     made. But its first write replaces the canvas whole from the Bald's
     ledger, which holds no Cuttlefish questions. So the open questions
     and recent answers are re-recorded on the Bald, each naming its
     Captain source, through the path chat sessions record them, before
     the Bald's bridges start. If no such path exists, the build names
     what the first board write will drop.
   - **Chat sessions:** they do not move. Say what their loss costs.
     Memory is already shared.
5. **Switch, ordered around the sync.**
   1. List open Cuttlefish tasks and waits on the Captain, then let them
      finish or name what was carried over.
   2. On the Bald, `launchctl bootout` the telegram and slack bridge jobs,
      after recording their plists.
   3. Set `machine = "Valor the Bald"` and fix the header comment, then
      `Spec.load` it.
   4. Wait for the sync. Run `intake.owned('telegram')` and
      `intake.owned('slack')` through Valor.app on each Mac. The Captain
      must list none of the three chats and the Bald must list all
      three.
   5. Copy the Captain's final entries for the three chats from
      `telegram-seen.json` and `slack-seen.json` into the Bald's files
      (mode 600), overwriting what is there.
   6. Bootstrap the Bald's bridges. Their gap fill picks up messages
      posted during the pause, for the Bald's other projects too.
   7. Run `intake.owned` again on both Macs, and check each bridge's log
      for a clean start. That check is the evidence. Do not count
      `core update --wait` on a Mac already at tip as a restart.
6. **Prove it on the Bald.** Provision a Cuttlefish workspace through
   the kernel's provisioning path, run its setup, and run the spec's
   suite. Report the result. For any failure, rerun it on the Captain at
   the same commit to separate pre-existing failures from new ones.
7. **Docs.** Fix every place, in the private folder (specs, rooms,
   machine lists) and in the repo, that says which Mac serves Cuttlefish.
8. **Remove the paused client from config.**
   - **List the leftovers first.** List everything of the client's that
     is not config, on all four Macs: checkouts, databases, keys
     (`claude-token.<client>`, `op-token.<client>`, vault `.env` lines),
     the work-vault folder, record and memory rows, Slack and Telegram
     memberships, and Valor's mailbox on its domain. For each, give
     keep or delete as a recommendation for Tom. None of it is touched.
   - **Then delete:** the `.paused` spec, any spec or `valor.toml`
     routing naming its people or domain, the old `projects.json` entry,
     and the `.bak-*` file. The JSON entry and the backup file are
     deleted only after a grep shows nothing reads them. One dated copy
     of everything deleted goes in
     `~/Desktop/Valor/backups/2026-11-17-<client>-config/`.
   - **Repo:** a grep of tests and docs shows the client is already
     absent (checked).

## Out of scope

- Posting in any chat.
- Cuttlefish's production systems and the hosted database.
- Deleting non-config client data or any credential.
- Tom's ssh key on the Captain.
- Any new check or gate (no `governance_grant`).

## Evidence that it works

- **The Bald's jobs:** every job's `launchctl list` exit is 0 after the
  update, and `core settings` names the Bald.
- **Chat ownership:** `intake.owned` on the Bald lists all three chats,
  and on the Captain it lists none.
- **Read marks:** the Bald's seen files hold the Captain's final marks
  for the three chats.
- **Board:** the board canvas still shows its open items.
- **Suite:** the Bald's Cuttlefish suite results, with pre-existing
  failures separated out.
- **Spec folder:** the Cuttlefish spec loads with the Bald as its
  machine, no spec names the client, and the backup folder holds the
  deleted files.
- **Repo suite:** Valor's suite still passes after the doc edit.
- **Undo:** reverse step 5: stop the Captain's bridges, set the machine
  back, wait for the sync, copy the marks back, and start the Captain's
  bridges.
