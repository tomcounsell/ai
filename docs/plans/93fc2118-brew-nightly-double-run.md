# One nightly Homebrew job per Mac

Operations task 93fc2118c8cd, the follow-up to 434db948662c (gh unlinked on
the Captain by two runs at once, 2026-11-18 21:00 UTC).

## Known (observed 2026-11-23 ~00:20 UTC, over ssh and locally, read only)

- **Every Mac still runs twice, every night, since 2026-11-03.** All four
  `~/Library/Logs/brew-nightly.log` show two `=== brew nightly update
  start ===` lines at the same second each night and
  `Error: Another 'brew update' process is already running`. The Captain:
  `[2026-11-21 04:00:05] start` x2, `[2026-11-21 04:08:14] done` x2;
  `[2026-11-22 04:00:04] start` x2, `done` at 04:06:55 and 04:08:13. The
  Pirate, the Bald and the Cowboy show the same on 11-20 to 11-23. Single
  runs on the Captain end 2026-11-02; the double starts 11-03.
- **gh survived these two nights** by luck: no gh error in the Captain's
  11-21/11-22 runs, and `/opt/homebrew/bin/gh -> ../Cellar/gh/2.105.0`
  on all four. The Pirate and the Bald logged `gh: Bottle destination
  already exists` earlier (the same race as the Captain's 11-18).
- **Two launchd jobs, both 04:00, one log.** Each Mac has the vault job
  `com.valor.brew-nightly` (through Valor.app's `valor-run`) and Yudame
  Tools' `me.yuda.brew-status` (`YudameTools.app/.../brew-status run`,
  bare, its own `nightly.sh`). On the Captain `launchctl print` gives runs
  11 and 12, last exit 0, for the two. The Yudame plists date from
  2026-11-02 (Pirate) to 11-04 (Cowboy): set up from the app's own
  Homebrew setup ("Set Up Nightly Updates…"), not by any recorded task.
- **The second job is not the one the Macs should run yet.** yudame-tools
  #8 (open) says the vault job runs Homebrew until the switch, and the
  switch sets Yudame's job up through `valor-run` with `local.sh`. Today it
  runs bare and without the vault's frozen list: on 11-23 it tried to
  upgrade `tailscale-app` (frozen because quitting Tailscale took the
  Captain off the tailnet, 2026-10-28) and failed only on
  `HOMEBREW_NO_SUDO`.
- **Why it stayed.** The vault script removes a leftover
  `me.yuda.brew-status` only before Yudame Tools' first open (`yt_install`,
  `if $first`), by design: "a job someone sets up from the app later
  stays". The app re-sets its job up at launch only while the plist exists
  (`HomebrewTool.start`, `needsSetup`), so removing the plist holds.

## What will be built

1. **The vault script** (`~/Desktop/Valor/brew/brew-nightly-update.sh`,
   reaches every Mac at its next 04:00). The leftover-job removal leaves
   `yt_install`'s first-install branch and becomes its own step in
   `yudame_tools()`, run every time, placed before the `launchctl print
   $DOMAIN/me.yuda.brew-status` line that writes the status file's
   `homebrew jobs:`. It no longer skips a running job, since at 04:00 the
   Yudame job is always running when the vault job gets there (both fire
   in the same second):
   - the plist is removed at once (launchd keeps a loaded job's config, so
     the running update is undisturbed, and the app will not set the job
     up again at launch without it);
   - not running: `launchctl bootout` now, noted `removed
     me.yuda.brew-status`;
   - running: the bootout waits for it to end, through a one-shot job of
     the same shape as the script's own deferred reload (line ~616:
     `while launchctl print … | grep -q 'state = running'; do sleep 2;
     done; launchctl bootout …; launchctl bootout <itself>`), labelled
     `com.valor.brew-nightly.yudame-off`, noted `me.yuda.brew-status
     removed once its run ends`. The vault run itself never waits on it,
     so a stuck Yudame run cannot hold the vault job past the next 04:00.
   So a job set up again from the app runs at most one more night beside
   the vault job, and is gone before the next. The comment says the vault
   job is the one Homebrew runner until yudame-tools#8, and #8 deletes this
   step. `SETUP.md`'s top paragraph says the same.
2. **Each Mac now**, so tonight is single: copy the new script to
   `~/.local/bin` (remote Macs cannot read the vault over ssh,
   docs/rollout.md step 9) and run `brew-nightly-update.sh --yudame-tools`,
   which does setup plus the Yudame step and logs to
   `yudame-tools-install.log`. The Cowboy locally, the same way.
3. **docs/rollout.md step 9**: one clause, that the vault job removes Yudame
   Tools' own job until yudame-tools#8.

Governance: none added. This widens an existing reconciliation (the script
already removes this job and keeps its own plist current) from first
install to every run; it adds no check, gate or denial. `launchctl disable`
was weighed and not used: it would also make the app's later set-up fail
at #8 until someone ran `launchctl enable`.

## Out of scope (named in the delivery)

The switch itself (#8); Yudame Tools' settings (`disabledTools`, the
Pirate's keep-settings) untouched; the Captain's 11-21 "Not enough disk
space to extract"; iTerm's refused quit; `brew-status` panel showing "Set
Up Nightly Updates…" again.

## Stakes

Low and reversible: a launchd job removed that the app can set up again in
one click; a wrong edit to the vault script would break every Mac's 04:00
run, which the build checks with `bash -n` and a live `--yudame-tools` run.

## Tests (live; the vault script has no suite)

- `bash -n` on the edited script.
- **Not running** (each Mac after step 2): `launchctl print
  gui/501/me.yuda.brew-status` → not found, plist gone;
  `com.valor.brew-nightly` still loaded with `valor-run` first;
  `yudame-tools-install.log` has `removed me.yuda.brew-status`;
  `~/Desktop/Valor/yudame-tools/status/<mac>.txt` says `homebrew jobs:
  vault` (written after the removal).
- **Running** (the Cowboy, before step 2 there): `launchctl kickstart
  gui/501/me.yuda.brew-status`, then while it runs
  `brew-nightly-update.sh --yudame-tools`. By the vault run's end: plist
  gone, the note says removed once its run ends, the
  `.yudame-off` helper is loaded. Once Yudame's run ends: no
  `me.yuda.brew-status`, no helper, and the Yudame run's log ends `done`
  (it was not killed).
- Yudame Tools' install result unchanged (`current`) and its settings
  unchanged on each Mac (the Pirate's unset `disabledTools` stays unset).
- Tonight (2026-11-23 21:00 UTC): each log has exactly one `start` and one
  `done` for 2026-11-24 and no `Another 'brew update'`; checked if the
  build turn runs past it, else named open.
