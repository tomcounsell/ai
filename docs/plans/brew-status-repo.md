# Brew Status: a Yudame team app in yudame/brew-status

Tom, Telegram 11748/11750/11751 (2026-10-28), answers to six questions the
same day (all defaults). For Tom, Charlie and Thabi as much as for Valor: a
nice install, a first launch that sets up the nightly update itself, plain
words for amber and red. Done when Tom opens the repo and the app on the Mac
he walks over to still shows its last run.

## Now (checked)
`apps/BrewStatus/` here (2 commits, macOS 26 only, no other `apps/`). The
job is not in this repo: `~/Desktop/Valor/brew/brew-nightly-update.sh` in
the vault, `com.valor.brew-nightly`, run through Valor.app's `valor-run`,
with Valor-only parts (vault Brewfile floor, vault frozen list, Python
restart of `com.valor.*`). Xcode 26 on the Cowboy and the Captain only.
No signing identity on the Cowboy: builds are ad hoc.

## Build
1. **Repo.** `git subtree split -P apps/BrewStatus` (history kept) pushed
   to new private `yudame/brew-status`, `main`.
2. **The job, in the app.** `Resources/nightly.sh`: the vault script minus
   its vault self-copy and deferred self-reload (no copy to reload from),
   the `BrewNightly.app` removal (gone on all four Macs, checked) and the
   Valor parts. Brew update, formulae, casks with `--greedy-auto-updates`,
   `mas` if present, the Chrome tab-restore setting, yudame plugins updated
   where that marketplace is already added, cleanup. `brew` found in
   `/opt/homebrew` or `/usr/local` (Intel), both on the job's `PATH`.
   Frozen casks: `~/.config/brew-status/frozen.txt`. An open app is quit
   and reopened only when HID idle >= 600 s (`ioreg`); else `WAITING:
   <cask> (<App> is open)` is logged and the cask left. `nightly.sh --cask
   X` upgrades one cask, quitting it (the person asked). An optional
   `~/.config/brew-status/local.sh` is sourced: it may add to `FROZEN` and
   define `before_upgrades` / `after_formulae`. Nothing names Valor.
3. **Setup, one mechanism.** `brew-status setup [--launcher PATH]` (the app's
   button calls the same code) writes `~/Library/LaunchAgents/me.yuda.brew-status.plist`
   (04:00, `[launcher] <bundle>/Contents/MacOS/brew-status run`, log
   `~/Library/Logs/brew-nightly.log`, kept so history carries over) and
   loads it; unchanged plist, no reload. Without `--launcher` the existing
   plist's launcher is kept, so the app's re-run at each launch (which
   follows a moved app) never drops Valor.app's `valor-run`. `run` starts
   `nightly.sh` as a child and waits, never `exec`: the process launchd
   started stays the app's binary, so macOS asks App Management for Brew
   Status, not bash or Homebrew's Ruby (the vault plist's reason for a
   launcher). An app run from an App Translocation path (a downloaded zip
   opened in place) does not set up: the first-run window asks to move it
   to Applications, moves it and reopens. `Job.label` becomes
   `me.yuda.brew-status`, so the schedule and Run Now follow the new job.
4. **App.** Deployment target lowered to the lowest macOS the code builds
   for (aim 14). First launch with no job: a window in plain words,
   "Homebrew isn't on this Mac" (Copy the install command, brew.sh link)
   or "Set up nightly updates", then "Run the first update now" so macOS's
   app-update permission is asked while they are there. Popover: a
   sentence per amber/red cause saying what happened and what to do (no
   run in 26 hours: "this Mac was asleep or off at 4:00; it updates when
   it's awake, or press Run Now");
   "Waiting until you close it" rows with Update now (`--cask`); a
   "Don't update this app" / "Update this app again" item per app, writing
   `frozen.txt`. Waiting alone stays green. Words in `docs/GLOSSARY.md`.
5. **Release.** `build.sh --zip` makes `out/BrewStatus.zip`; `gh release
   create v1.0` attaches it. The install note (download, drag to
   Applications, the first-open step as it really is on macOS 26) is
   written after trying it on a quarantined copy.
6. **Docs.** README for people first: what the dot means, what to do at
   amber or red, install, freezing, the permission asked again per build
   until the Developer ID certificate (Tom accepted it), troubleshooting,
   then Develop (CLI, build without Xcode GUI, how runs are read) and
   Valor's Macs (`--launcher` Valor.app, `local.sh`, `tools/ship.sh`).
   BRIEF, GLOSSARY, STORIES, CLAUDE.md brought up to date.
7. **Updates.** Valor's Macs: on the Cowboy `git pull && ./build.sh --open
   && tools/ship.sh valor-the-pirate valor-the-captain valor-the-bald`
   (ditto over ssh, relink CLI, reopen). Teammates: a new release zip,
   forwarded by Tom. No self-update.
8. **Valor (this repo).** Delete `apps/`. `scripts/brew-status-local.sh`:
   vault Brewfile floor, vault `brew-unmanaged.txt` into `FROZEN`, Python
   restart, yudame plugins added and installed when missing and the
   marketplace's `autoUpdate`; rollout links
   it to `~/.config/brew-status/local.sh`. `README.md`, `docs/plans/mac-app.md`,
   `docs/rollout.md` steps 8 to 10 name the repo, the new label, setup
   with `--launcher ~/Applications/Valor.app/Contents/MacOS/valor-run`.
9. **Four Macs.** Install, `setup --launcher`, link `local.sh`, carry over
   `~/.config/brew-nightly/skip-casks.txt` (the Cowboy's only) into `frozen.txt`, boot out and delete
   `com.valor.brew-nightly` and `~/.local/bin/brew-nightly-update.sh`; then
   the vault's script and plist go, `SETUP.md` points at the repo.

## Out of scope
Signing, self-update, other Macs' runs, installing Homebrew, org invites.

## Tests
- Parser: `WAITING` lines, the waiting-only run green; plain-words text for
  each amber and red cause, the asleep one included (last run 3 days ago).
- Frozen file: add, remove, comments and blank lines kept.
- Setup: plist for an app path, with and without launcher; same input
  writes nothing; a moved app rewrites it; an existing plist with a
  launcher re-run without one gives the same plist; a translocated path is
  detected; `Job.label` and Run Now's target are `me.yuda.brew-status`.
- `nightly.sh` under fake `brew`, `osascript`, `ioreg`, `open` on `PATH`
  (a Swift test running bash): idle quits, upgrades, reopens; not idle
  logs WAITING and quits nothing; frozen skipped; `--cask` quits when not
  idle; `local.sh` hooks run; without it no `~/src/ai` or vault path read;
  `brew` found under `/usr/local` alone. `brew-status run` leaves the
  script as its child (its parent pid is the app binary's).
- Valor suite passes; `grep -rn apps/BrewStatus` finds only plans/records.
- Live, Valor's route: on each Mac the job is `me.yuda.brew-status` with
  `valor-run`, the old one gone, `brew-status` prints its own last run and
  the next run; one kickstarted run on the Cowboy ends and shows.
- Live, the teammates' route, on the Cowboy before its launcher is set:
  the release zip downloaded fresh (quarantined), opened as the note says,
  moved, set up without `--launcher`, one run kickstarted; TCC's log names
  Brew Status as the app asking App Management. No one is at the Cowboy to
  click Allow, so a cask upgrading through that grant is first seen on
  Tom's Mac and named open in the delivery. First-run screens (no
  Homebrew, set up, translocated, done) and popover amber/red/waiting
  snapshotted and looked at.
