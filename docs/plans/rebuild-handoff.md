---
tracking: none
slug: rebuild-handoff
type: chore
status: active
---

# Rebuild handoff: setting up Valor's machine

How to continue the rebuild in Claude Code on one of Valor's Macs. After
setup, `/build` (`.claude/skills/build/SKILL.md`) reads the plans and picks
up where the build left off.

## Where the build stands

The rebuild branch is `valor-cori-rebuild`. Milestones 1.1, 1.2, 1.3, and
task 1.4a are merged and rolled out on both Macs. Valor's Mac has the
checkout at `~/src/valor-rebuild`, a fresh ledger with one passing
calibration, the judgement keys, the long-lived Claude token, the demo
items and results at `~/src/valor-demo`, `GITHUB_PUSH_TOKEN` in the
vault, and the nightly backup job loaded against `/Volumes/PINK/valor_temp`
(a USB disk; the job carries `VALOR_PG_BIN` because launchd's PATH has no
Postgres). The popoto #191 trial run (task `75c0902b6e25`) is done and held at
its merge, not released (m1-4-checks.md). Next is 1.4b, then 1.4d. The review's
rerun (1.4c) needs Apple's `container`, which Valor's Mac and Tom's Mac
both have. On this Mac a non-interactive shell finds
Postgres 15 first on `PATH` and has neither `VALOR_BACKUP_DIR` nor
`PGPASSFILE`, so commands run with `postgresql@18/bin` first,
`VALOR_BACKUP_DIR=/Volumes/PINK/valor_temp`, and
`PGPASSFILE=~/.config/valor-kernel/pgpass` set. Each milestone's plan file in
`docs/plans/m*.md` carries its status and its records.

## Setup

The live old system runs from `~/src/ai` on `main` on every Mac. Never
switch it off `main`; the rebuild is a separate checkout.

1. **Prerequisites.** Apple's Command Line Tools (the kernel runs only the
   root-owned git at `/Library/Developer/CommandLineTools/usr/bin/git`),
   Homebrew `postgresql@18` running as a service on port 5432 (if an older
   Homebrew Postgres holds the port, dump its databases with `pg_dumpall`,
   stop it, start 18, restore, and put `postgresql@18/bin` first on PATH),
   `uv`, the `claude` CLI logged in, and Homebrew `dovecot` and `openssl`
   for the email tests (Dovecot is only run by the tests, as the user, never
   as a service). Apple's `container` 1.5.0 from
   the signed package on its GitHub releases (`apple/container`; the
   installer needs the Mac's admin password, and the Homebrew build fails
   the kernel's root-owned check), then `container system start` once,
   accepting its default Linux kernel, and `container system stop`.
   Rosetta, which its image builder needs, where `arch -x86_64
   /usr/bin/true` fails: `softwareupdate --install-rosetta --agree-to-license`.
   On a Mac with Little Snitch, the runtime's launchd services reach the
   network only with an allow rule for `/usr/local/bin/container-apiserver`
   and `/usr/local/libexec/container/`; without one, the kernel download
   and every image pull time out (`HTTPClientError.connectTimeout`). The
   kernel archive can instead be fetched with `curl` from the `url` that
   `container system property list` shows, its sha256 checked against
   `digest`, and installed with `container system kernel set --tar FILE
   --binary PATH`, `PATH` being the `binaryPath` there.
2. **Checkout.**
   `git clone -b valor-cori-rebuild https://github.com/tomcounsell/ai.git ~/src/valor-rebuild`
   (Valor's Macs reach GitHub over HTTPS through `gh`, not SSH),
   then `cd ~/src/valor-rebuild && uv sync`. Python is pinned by
   `.python-version`.
3. **The vault.** `~/Desktop/Valor/.env` holds `TYPESAFE_API_KEY` and
   `OPENROUTER_API_KEY`. If one is missing, it is in the `m-valor`
   1Password vault ("TypeSafe API"); copy it in by fingerprint, never by
   printing it.
4. **The kernel database.** `.venv/bin/python -m core migrate` creates the
   `valor_kernel` role, the `valor_rebuild` database, the schema, correction
   1, and the four seeded guards, then runs `secure-login`: it writes
   `~/.config/valor-kernel/pgpass` and puts scram rules for the two kernel
   databases at the top of that Mac's `pg_hba.conf` (every other database
   keeps its rules). Then add `export PGPASSFILE=~/.config/valor-kernel/pgpass`
   to the shell profile.
5. **The ledger.** Decided by default: the new machine starts a fresh
   ledger. Its history so far is the demonstration and replays, which the
   docs already record. The built Mac's ledger is dumped at
   `valor_rebuild-20261002T062159Z.dump` on the `valor_temp` disk if
   continuity is wanted later.
6. **Judgement keys and calibration.** Copy `~/src/valor-demo/items/` and
   `~/src/valor-demo/results/` from the built Mac (about 2 MB; they hold
   client request text, so they stay outside the repo). Then
   `.venv/bin/python -m core judgement-keys`, and
   `.venv/bin/python -m core calibrate ~/src/valor-demo/items/judgement/intake.underspecified.json `.
   Its `task_sha256` must equal `JUDGE.calibrated` in
   `core/judgement_tasks.py` and its `entry_check` must be true.
7. **The model credential.** The gateway reads the `claude` login from the
   Keychain, which expires after about eight hours unless the owner's own
   sessions refresh it. Better: a long-lived token saved as
   `~/.config/valor-kernel/claude-token` (mode 600). The vault's 1Password
   item `CLAUDE_CODE_OAUTH_TOKEN` holds one; `claude setup-token` makes a
   fresh one. Check it with a one-word `claude -p` call under
   `CLAUDE_CODE_OAUTH_TOKEN` before installing it.
8. **Backups.** Set `VALOR_BACKUP_DIR` to a folder on that Mac's external
   disk (the disk must be on a different device from the Postgres data),
   run `.venv/bin/python -m core backup` once, then
   `.venv/bin/python -m core backup --plist`, load it with `launchctl`, and
   allow disk access when macOS asks.
9. **Check.** `VALOR_TEST_DB=valor_rebuild_test_setup .venv/bin/python -m pytest -q tests`
   passes with only the live tests skipped, plus two machine-dependent
   skips on a fresh Mac (the ledger-copy test needs task history, and the
   planted commit-graph test needs a git that trusts the planted file,
   which Apple's does not), and `.venv/bin/python -m core settings`
   shows the right owner role, Postgres paths, and backup folder. Drop the
   scratch database afterwards.

## Running Valor on a Mac with only the local bridge

A Mac with no Telegram session for Valor and no mailbox login talks to
Valor through the local chat bridge ([docs/bridges/local.md](../bridges/local.md)).
Finish Setup steps 1 to 9 first; the bridge needs the kernel database, the
judgement keys, and the model credential, and nothing from Telegram or email.

1. **Make the local chat the operator channel.** Export
   `VALOR_OPERATOR_CHANNEL=local` in the shell profile. The operator chat is
   then `local` and `VALOR_OPERATOR_CHAT` is not read. The page uses port
   8711; `VALOR_LOCAL_PORT` changes it.
2. **Run the kernel.** `.venv/bin/python -m core serve` (or its LaunchAgent,
   `serve --plist`; [machine.md](../machine.md)). The kernel binds what the
   bridge records.
3. **Run the bridge.** `.venv/bin/python -m bridges.local run`. It makes
   `local-token` in `~/.config/valor-kernel/` (mode 600) when it is missing.
   To keep it running, print its launchd job with
   `.venv/bin/python -m bridges.local --plist`, save it to
   `~/Library/LaunchAgents/com.valor.kernel.local.plist`, and load it with
   `launchctl`. Set `VALOR_OPERATOR_CHANNEL` in the environment that prints
   the job, so the job carries it.
4. **Open the page.** `.venv/bin/python -m bridges.local open` opens
   `http://127.0.0.1:8711/` with the token in the URL fragment. Messages
   sent there start work; Valor's notices and approvals come back on the
   same page, and replying `approve` or `stop` to a notice binds as it does
   in Telegram. Reopen the page with `open` whenever it asks.

The local and Telegram channels cannot both be the operator channel, and an
approved `email.send` waits for an email bridge that this Mac does not run.

## Waiting on Tom

- 1.4d's ruleset is created (2026-10-02, by Tom through his `gh` login):
  ruleset 24370170 on `main` of `tomcounsell/ai`, restrict updates, bypass
  for the repository admin role only, so `valorengels` (push, not admin) is
  off it. Enforcement is disabled until takeover: today every commit and PR
  merge on `main` lands under `valorengels`, so an active rule would stop
  the running pipeline. At takeover, set it to active. The push token is
  settled: Valor's classic `repo`-scope token, in the vault as
  `GITHUB_PUSH_TOKEN` (m1-4-checks.md, Questions, 3).
- 1.5: confirm the drafted answer keys for cuttlefish #646, popoto #191,
  and popoto #188.
- Milestone 2: which Mac hosts the new bridges, and the test windows in
  which its old bridge, email bridge, and worker are disabled.
