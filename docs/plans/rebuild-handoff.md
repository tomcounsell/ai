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
task 1.4a are merged and rolled out on the Mac where they were built. Next,
per `docs/plans/valor-rebuild.md` (1.4): the popoto #191 trial run, then
1.4b, then 1.4d; 1.4c after takeover. Each milestone's plan file in
`docs/plans/m*.md` carries its status and its records.

## Setup

The live old system runs from `~/src/ai` on `main` on every Mac. Never
switch it off `main`; the rebuild is a separate checkout.

1. **Prerequisites.** Apple's Command Line Tools (the kernel runs only the
   root-owned git at `/Library/Developer/CommandLineTools/usr/bin/git`),
   Homebrew `postgresql@18` running as a service, `uv`, and the `claude`
   CLI logged in.
2. **Checkout.**
   `git clone -b valor-cori-rebuild git@github.com:tomcounsell/ai.git ~/src/valor-rebuild`,
   then `cd ~/src/valor-rebuild && uv sync`. Python is pinned by
   `.python-version`.
3. **The vault.** `~/Desktop/Valor/.env` holds `TYPESAFE_API_KEY` and
   `OPENROUTER_API_KEY` (already present on Valor's machines).
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
   `.venv/bin/python -m core calibrate ~/src/valor-demo/items/judgement/intake.underspecified.json --budget-usd 0.05`.
   Its `task_sha256` must equal `JUDGE.calibrated` in
   `core/judgement_tasks.py` and its `entry_check` must be true.
7. **The model credential.** The gateway reads the `claude` login from the
   Keychain, which expires after about eight hours unless the owner's own
   sessions refresh it. Better: `claude setup-token`, saved as
   `~/.config/valor-kernel/claude-token` (mode 600).
8. **Backups.** Set `VALOR_BACKUP_DIR` to a folder on that Mac's external
   disk (the disk must be on a different device from the Postgres data),
   run `.venv/bin/python -m core backup` once, then
   `.venv/bin/python -m core backup --plist`, load it with `launchctl`, and
   allow disk access when macOS asks.
9. **Check.** `VALOR_TEST_DB=valor_rebuild_test_setup .venv/bin/python -m pytest -q tests`
   passes with only the live tests skipped, and `.venv/bin/python -m core settings`
   shows the right owner role, Postgres paths, and backup folder.

## Waiting on Tom

- 1.4d: a Valor GitHub account with a ruleset on `main` (recommended) or
  Tom's own fine-grained token, saved as `GITHUB_PUSH_TOKEN` in the vault.
- 1.5: confirm the drafted answer keys for cuttlefish #646, popoto #191,
  and popoto #188.
- 1.4c, after takeover: install Apple's `container` from Apple's signed
  package (the Homebrew build fails the kernel's root-owned binary check).
- Milestone 2: which Mac hosts the new bridges, and the test windows in
  which its old bridge, email bridge, and worker are disabled.
