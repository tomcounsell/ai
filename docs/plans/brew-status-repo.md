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
   its vault self-copy and Valor parts. Brew update, formulae, casks with
   `--greedy-auto-updates`, `mas` if present, the Chrome tab-restore
   setting, yudame plugins only where that marketplace is already added,
   cleanup. Frozen casks from `~/.config/brew-status/frozen.txt`. An open
   app is quit and reopened only when HID idle >= 600 s (`ioreg`); else
   `WAITING: <cask> (<App> is open)` is logged and the cask left. `nightly.sh
   --cask X` upgrades one cask, quitting it (the person asked). An optional
   `~/.config/brew-status/local.sh` is sourced: it may add to `FROZEN` and
   define `before_upgrades` / `after_formulae`. Nothing names Valor.
3. **Setup, one mechanism.** `brew-status setup [--launcher PATH]` (the app's
   button calls the same code) writes `~/Library/LaunchAgents/me.yuda.brew-status.plist`
   (04:00, `[launcher] <bundle>/Contents/MacOS/brew-status run`, log
   `~/Library/Logs/brew-nightly.log`, kept so history carries over) and
   loads it; unchanged plist, no reload. The app re-runs it at launch when
   the job exists, so a moved app keeps working. `run` execs the script.
4. **App.** Deployment target lowered to the lowest macOS the code builds
   for (aim 14). First launch with no job: a window in plain words,
   "Homebrew isn't on this Mac" (Copy the install command, brew.sh link)
   or "Set up nightly updates", then "Run the first update now" so macOS's
   app-update permission is asked while they are there. Popover: a
   sentence per amber/red cause saying what happened and what to do;
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
   vault Brewfile floor, vault frozen list, Python restart; rollout links
   it to `~/.config/brew-status/local.sh`. `README.md`, `docs/plans/mac-app.md`,
   `docs/rollout.md` steps 8 to 10 name the repo, the new label, setup
   with `--launcher ~/Applications/Valor.app/Contents/MacOS/valor-run`.
9. **Four Macs.** Install, `setup --launcher`, link `local.sh`, carry over
   `skip-casks.txt` into `frozen.txt`, boot out and delete
   `com.valor.brew-nightly` and `~/.local/bin/brew-nightly-update.sh`; then
   the vault's script and plist go, `SETUP.md` points at the repo.

## Out of scope
Signing, self-update, other Macs' runs, installing Homebrew, org invites.

## Tests
- Parser: `WAITING` lines, the waiting-only run green; plain-words text for
  each amber and red cause.
- Frozen file: add, remove, comments and blank lines kept.
- Setup: plist for an app path, with and without launcher; same input
  writes nothing; a moved app rewrites it.
- `nightly.sh` under fake `brew`, `osascript`, `ioreg`, `open` on `PATH`
  (a Swift test running bash): idle quits, upgrades, reopens; not idle
  logs WAITING and quits nothing; frozen skipped; `--cask` quits when not
  idle; `local.sh` hooks run; without it no `~/src/ai` or vault path read.
- Valor suite passes; `grep -rn apps/BrewStatus` finds only plans/records.
- Live: on each Mac the job is `me.yuda.brew-status`, the old one gone,
  and `brew-status` prints its own last run; one kickstarted run on the
  Cowboy ends and shows. First-run screens snapshot (no Homebrew, set up,
  done) and popover amber/red/waiting, looked at; the release zip
  downloaded fresh, quarantined, opened as the note says.
