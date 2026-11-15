# Nightly history for the Valor folder; Tailscale reopened by the kernel

Task d998b1189f77. Tom, Telegram DM 2026-11-15, message 11867: the work vault as a backup "but I wouldn't want to give up the auto-sync"; Tailscale "we can probably add this to the existing local health check".

**Stakes.** A secret copied into the vault's git history is hard to undo, because the vault syncs to Tom's Macs. A wrong Tailscale action could cut a Mac off its only route.

**Governance: none added.** Both are capabilities Tom asked for (rule 2): a nightly copy, and a self-repair beside the database restart. Neither limits, refuses or gates anything.

## 1. Snapshot of `~/Desktop/Valor` into the work vault (the Cowboy, nightly)

- **Where it runs.** In the existing `update` routine (03:00 on every Mac), with no new launchd job. `update_runner` calls `core/snapshot.py` only when `settings.snapshot_dir` is set (`VALOR_SNAPSHOT_DIR`, added to `routines.PLIST_ENV`). It is set only in the Cowboy's update plist, the same way `VALOR_WATCH` is placed. Target: `~/work-vault/_valor-desktop_/`, following the vault's `_archive_` and `_services_` naming.
- **Copy.** `rsync -a --delete --delete-excluded --exclude-from=<folder>/snapshot-exclude.txt`. Without that file nothing is copied and the run reports `no exclude list`. rsync skips deletion on an I/O error by its own default, so an evicted iCloud file never deletes the vault's copy.
- **The exclude list** lives in the private folder, not in this public repo, so it can name folders. The builder writes it from a fresh read of the folder:
  - **In:** specs, scripts, docs and config text.
  - **Out:** `.env`, `*.env`, `.env.*`, `*token*`, `*credential*`, `*secret*`, `*oauth*`, `*key*` (also catches `old_keys.txt`), ssh files, `*.session`, `*.pem`, `*.p12`, shell env files, hook stamps, `*.bak*`, the old project registry JSONs, and memory and chat dumps and exports (the bench's corpus, dumps, exports, results and import). Media and archives are out too (`*.mp3 *.m4a *.mp4 *.mov *.wav *.zip *.bundle *.dump *.jsonl`), along with `.DS_Store` and `__pycache__`.
  - **Before the first commit,** the builder greps the copied tree for key shapes (`sk-ant`, `sk-proj`, `ghp_`, `github_pat_`, `xox?-`, `AIza`, `PRIVATE KEY`, `refresh_token`, `client_secret`). It widens the list until that grep is empty. This is a build step, not a runtime check.
- **Commit.**
  - The snapshot holds the vault sync job's own lock (`<git-dir>/vault-sync/lock`, the `mkdir` lock that `.sync/vault-sync.sh` uses), so the 5-minute sync never commits a half-copied tree. If the lock is busy, it retries for up to 60 s, then gives up with `vault busy`.
  - It runs `git add -A -- _valor-desktop_` and commits only that path.
  - Message: `Valor folder snapshot <date>: N files (a added, m modified, d deleted)`, with a body from `git diff --cached --name-status`, capped at 200 lines plus "and N more".
  - When nothing changed, there is no commit.
- **Push.** `git push origin HEAD:main`. A rejected push is left to the vault sync job, which rebases and pushes within 5 minutes.
- **Failure** (no exclude list, an rsync error, the commit, vault busy) sends one `notices.request` a day on `intake.UPDATES`, like the disk-low notice. Every outcome is in the routine's `Ran` text.
- **Rollout.** The update plist on the Cowboy is printed with `VALOR_SNAPSHOT_DIR`. The builder runs the first snapshot by hand; the next 03:00 run gives the nightly commit.

## 2. Tailscale in `core/health.py`

- **`tailscale()`** returns:
  - `None` when `/Applications/Tailscale.app` is missing;
  - `not running` when `pgrep -x Tailscale` finds nothing;
  - otherwise `BackendState` from the app's own CLI (`Tailscale.app/Contents/MacOS/Tailscale status --json`, 10 s timeout, `unreadable` on error). It does not use Homebrew's `tailscale`, which talks to a different daemon.
- **`reopen_tailscale(state)`**, spaced like the database restart: the `flock` and stamp logic moves out of `restart_postgres` into one shared helper. The stamp sits beside the kernel's logs, and nothing is done within `SPACING_S` (10 min) of the last action. The action depends on the state:
  - `not running` or `unreadable`: `open -a Tailscale`;
  - `Stopped`: the app CLI's `up`;
  - `NeedsLogin` or `NeedsMachineAuth`: nothing, because only a person can sign in. It shows in the report.
- **Kernel clock.** On every `serve_tick_s` wake (next to `mend.check()`), a down state that has lasted `DOWN_S` (60 s, so Tailscale's own self-update relaunch passes untouched) reopens it, run off the loop. It prints `tailscale <state>: <action>` to the kernel log. A quit app is back within about 2 minutes.
- **`python -m core health`** adds `"tailscale": <state>`, and `--fix` reopens with no 60 s wait, sharing the stamp. It stays out of `healthy`: a peer whose tailnet is down can't be read anyway.
- **Vault script.** After the merge, delete setup step 4 (the 04:00 reopen) from `~/Desktop/Valor/brew/brew-nightly-update.sh` and renumber step 5 and its references. Then check with `bash -n` and a `--setup` run on the Cowboy.

## Tests

- **`tests/test_snapshot.py`** uses a temporary source folder and a temporary vault with a bare origin. It covers:
  - `.env`, a token file and an excluded dump never reach the copy or the commit;
  - no exclude list: nothing copied, `no exclude list`;
  - a file added, edited or deleted appears in the commit message under the right heading;
  - no change: no commit;
  - a held vault-sync lock: waits, then `vault busy`, with nothing committed;
  - an unrelated untracked or staged vault file stays out of the commit;
  - the commit reaches the origin;
  - `update_runner` snapshots only with `snapshot_dir` set, and a failure sends one notice a day.
- **`tests/test_health.py`** fakes the subprocess calls:
  - not installed: no action;
  - `Running`: no action;
  - not running under 60 s: nothing; at 60 s: one `open`; a second within 10 min: `spaced`;
  - `Stopped`: `up`;
  - `NeedsLogin`: no action, and the report shows it;
  - a status call that times out counts as `unreadable`;
  - `report(fix=True)` reopens at once and shares the stamp with the clock.
- **Real Macs.**
  - Snapshot: run by hand on the Cowboy, then check the commit on GitHub and repeat the key-shape grep over the committed tree.
  - Tailscale, before the merge: quit Tailscale.app on the Cowboy and run `python -m core health --fix` from the workspace.
  - Tailscale, after the rollout: quit it again and watch the kernel bring it back unaided within minutes. Also `Tailscale down` and watch it come back up.

## Docs

`docs/machine.md` (what runs where), `docs/rollout.md` (the Cowboy's update plist with `VALOR_SNAPSHOT_DIR`, the exclude list file), `core/README.md`'s health line, the `routines/README.md` and `routines/update/routine.toml` entries (instruction, plus a `need` line for Tom's ask).

## Out of scope

Restoring from the vault copy (nothing reads it). Snapshots from the other Macs. Tailscale under Homebrew's `tailscaled`. Obsidian's per-user ignore setting for the new folder.
