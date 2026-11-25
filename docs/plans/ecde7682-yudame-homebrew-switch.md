# Valor's Macs: Yudame Tools' Homebrew Updates switched off, the stopgap removed

Operations task ecde7682ce91 (commitment in Tom's DM, 12026/12027), the
follow-up to 93fc2118c8cd, which added `yt_job_off` until yudame-tools#8.

## Known (observed 2026-11-25, read only)

- **No Mac has 0.2.0.** The vault's published build
  (`~/Desktop/Valor/yudame-tools/YudameTools.json`) is ebbe154, 2026-11-13.
  Status files (04:00 today): Captain, Bald and Pirate run that build; the
  Cowboy runs 168bad9 (11-17, hand-installed). Update checks are off on
  three (`SUEnableAutomaticChecks 0`), so the existing path is the vault
  step `yt_install`, fed by `publish.sh`. The Cowboy holds the Developer ID.
- **`disabledTools`** is `()` on the Captain, the Bald and the Cowboy, and
  unset on the Pirate, which keeps its own settings (`keep-settings` file).
- **Disk Cleanup runs only from the app's 04:00 job `me.yuda.brew-status`**
  (0.2.0 README, Disk Cleanup). `yt_job_off` removed that job from all four
  on 11-23, so no Mac has cleaned nightly since. With Homebrew Updates off,
  that job runs no `nightly.sh`, only Disk Cleanup; it is set up with
  `brew-status setup --launcher <valor-run>` and logs to the same
  `brew-nightly.log` as the vault job.
- **The step's tests are stale**: `test-step.sh` cases 14/14b/14c still
  expect the first-install-only removal 93fc2118 replaced.

## What will be done

1. **Publish 0.2.0 to the vault**: `publish.sh ~/src/yudame-tools 0f2c7a9`
   (the release's source commit; `git archive`, so the checkout's local
   edits are not built). Check `CFBundleShortVersionString` 0.2.0.
2. **The vault script** (`~/Desktop/Valor/brew/brew-nightly-update.sh`):
   - delete `yt_job_off` and its call and comment; `YT_JOB_NOTE` goes too;
   - `yt_settings`' unset default becomes `window-tiling homebrew`, so a new
     Mac gets the switch off (still only while unset);
   - the status line `homebrew jobs:` becomes `04:00 jobs:` (the app's job
     is now a cleanup job);
   - the restart-only-after-a-successful-copy fix stays as it is.
   `test-step.sh`: cases 14x rewritten to "a loaded app job is left in
   place", settings cases expect `window-tiling homebrew`; run it on the
   edited copy, then on the saved one.
3. **Each Mac** (the Cowboy locally; the others over `ssh -o BatchMode=yes
   valorengels@valor-the-{captain,pirate,bald}`, copying the script to
   `~/.local/bin` since ssh cannot read the vault):
   - `defaults write me.yuda.tools disabledTools -array <existing entries>
     homebrew` (all four today: `homebrew` alone), before the app reopens;
   - install 0.2.0 by running `brew-nightly-update.sh --yudame-tools` with
     vault access: locally on the Cowboy; on the others through a one-shot
     launchd job run by Valor.app's `valor-run`, as the 04:00 job is. Where
     that fails, the 04:00 run installs it and the version is named open;
   - `~/.local/bin/brew-status setup --launcher
     ~/Applications/Valor.app/Contents/MacOS/valor-run`: the app's job, now
     cleanup only.
4. **Docs**: `SETUP.md`'s top paragraph and the vault `yudame-tools/README.md`
   say the app's Homebrew Updates is off on Valor's Macs and its 04:00 job
   only cleans; this repo's `docs/rollout.md` step 9 the same, dropping the
   removal clause; yudame-tools' README "Valor's Macs" section rewritten to
   match (a docs commit on its `main`, as that repo's docs land).
5. **yudame-tools#8**: a comment with each Mac's version, switch and jobs;
   closed if only tonight's log check is left, which the comment names.

Fallback (the brief's): if any Mac's app runs `brew update`/`upgrade` with
`homebrew` in `disabledTools`, step 2's removal is not saved, `yt_job_off`
stays, the app job is not set up, and a defect is filed on
yudame/yudame-tools with the log lines.

## Out of scope

Removing any Yudame Tools feature; the Pirate's other settings; iTerm's
refused quit; the Captain's 11-21 disk-space error.

## Stakes

A broken vault script stops every Mac's 04:00 update, and an app that still
runs Homebrew brings back the double run that unlinked gh; both reversible
within a night.

## Tests (live; the vault script has only `test-step.sh`)

- `bash -n` and `test-step.sh` pass; `grep -c yt_job_off` is 0.
- Per Mac: `defaults read me.yuda.tools disabledTools` has `homebrew` (and
  any prior entries); the running app's bundle says 0.2.0
  (`ps` path + `CFBundleShortVersionString`); General shows Homebrew
  Updates off (screenshot on the Cowboy; the setting read on the others).
- Per Mac: `launchctl print` shows `com.valor.brew-nightly` (valor-run) and
  `me.yuda.brew-status` (valor-run, `brew-status run`), both 04:00.
- **The switch holds**: `launchctl kickstart` the app's job on each Mac;
  its run logs Disk Cleanup and no `brew update`/`upgrade`, and no `brew`
  process is its child while it runs. Run Now on the Cowboy does nothing
  with Homebrew.
- Per Mac: `~/.local/bin` copy equals the vault's (shasum); the status file
  reads `04:00 jobs: vault, yudame-tools`.
- Tonight (2026-11-25 21:00 UTC): each `brew-nightly.log` has one
  `=== brew nightly update start ===` for 11-26, no `Another 'brew update'`,
  and cleanup lines from the app's job. Named open if not yet run.

Governance: none added; this deletes a stopgap and changes a default.
