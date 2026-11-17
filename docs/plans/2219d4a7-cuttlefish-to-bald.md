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
4. **Kernel state.** Read `core/board.py`, the chat-session store and the
   Telegram seen marks first:
   - **Board canvas:** the Bald's ledger has no Cuttlefish questions, so
     the board must not be rewritten empty. Carry open questions over,
     re-recorded on the Bald with their source, or establish that the
     board keeps them.
   - **Telegram seen mark:** carry the chat's mark into the Bald's
     `telegram-seen.json`, so no message is dropped or answered twice.
   - **Chat sessions:** if a chat session cannot move, say what its loss
     costs.
   - **Memory:** the shared store already crosses machines.
5. **Switch.**
   - **Wait for in-flight work:** list open Cuttlefish tasks and waits on
     the Captain, and wait for each to finish, or name what was carried
     over.
   - **Edit the spec:** set `machine = "Valor the Bald"` and fix its
     header comment, then `Spec.load` it.
   - **Confirm the sync:** check, through Valor.app on each Mac, that the
     edit has synced.
   - **Restart:** run the normal update (`core update --wait` in each
     kernel job's launchctl env) on the Bald, then the Captain.
   - **Confirm ownership:** the Bald's bridges own the three chats and
     the Captain's do not. Evidence is bridge logs and settings, and
     nothing is posted in the rooms.
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
- **Chat ownership:** the Bald's Telegram and Slack bridges list the
  Telegram group and both Slack channels, and the Captain's list none.
- **Board:** the board canvas still shows its open items.
- **Suite:** the Bald's Cuttlefish suite results, with pre-existing
  failures separated out.
- **Spec folder:** the Cuttlefish spec loads with the Bald as its
  machine, no spec names the client, and the backup folder holds the
  deleted files.
- **Repo suite:** Valor's suite still passes after the doc edit.
- **Undo:** the switch is undone by setting the machine back to the
  Captain and rerunning the update.
