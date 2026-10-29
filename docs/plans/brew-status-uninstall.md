# Brew Status off the four Macs; the vault job back until Studio Tools

Task 9c9294936bdb. Tom, Telegram 11762 (2026-10-28): "uninstall the old brew
status from the machines. they can wait until the new yudame tool is ready".
The Macs wait for Studio Tools' app; their nightly Homebrew run does not wait.
It goes back to the vault job until yudame/studio-tools#2 deploys.

## Premise, checked

- `permissions.ensure` calls `brew_status` and `brew_status_local` on every
  update. The update is `com.valor.routine.update`, at 03:00. Without the code
  change, tonight's update would reinstall the app and relink `local.sh`.
- `scripts/brew-status-local.sh` is used only by Brew Status's `nightly.sh`
  (through `local.sh`). Every part of it is also in the old vault script:
  the Brewfile floor, the frozen list, the Python restart and the yudame
  plugins.
- The old job is preserved in `~/valor-retired/brew-nightly-2026-10-28/` on
  all four Macs. On the Cowboy, `vault/` holds the vault copies of
  `brew-nightly-update.sh`, `com.valor.brew-nightly.plist` and `SETUP.md`,
  and `brew-nightly/skip-casks.txt` holds the Cowboy's skip list (`claude`).
  That script has both fixes from task 2bb12eb511b2:
  - **The hang fix.** The plist runs the script through Valor.app's
    `valor-run`, which holds App Management and Desktop access, so no
    privacy prompt waits for a click. Its setup step keeps each Mac's copy
    current with the vault.
  - **The Ollama fix.** Ollama is quit with SIGTERM (`TERM_QUIT_APPS`).
- Live state: each Mac runs `BrewStatus.app` and has `~/.local/bin/brew-status`,
  `me.yuda.brew-status.plist` and `~/.config/brew-status/`. On the Cowboy,
  `local.sh` is a link and `frozen.txt` holds `claude`; on the other three,
  `local.sh` is a plain copy. Only the Cowboy and the Captain have
  `~/src/brew-status`. The login item is `SMAppService.mainApp` (App.swift).
- Over ssh, `~/Desktop/Valor` is not readable (TCC, "operation not
  permitted"). The remote Macs therefore get the script and the plist by
  scp from the Cowboy, not through `--setup` from the vault. From then on,
  the 04:00 job (through Valor.app) syncs itself from the vault.

## Build

1. **Code** (`core/permissions.py`): remove `BREW_STATUS`, `brew_status`,
   `brew_status_local`, the helpers only they call (`_commit`, `_swift`),
   their calls in `ensure`, the docstring paragraph, and the imports left
   unused (`re`, `shutil`; `tempfile` and `plistlib` stay). Delete
   `scripts/brew-status-local.sh`. In `tests/test_permissions.py`, drop the
   Brew Status tests and the two monkeypatches. The `ensure` test keeps
   `com.valor.brew-nightly` (a bash job left alone), which is now the live
   case.
2. **Docs**:
   - `docs/rollout.md` step 9: no Brew Status install. Homebrew runs from
     the vault job `com.valor.brew-nightly` (`~/Desktop/Valor/brew/SETUP.md`)
     until Studio Tools replaces it per yudame/studio-tools#2, and Brew
     Status was removed on 2026-10-28.
   - `docs/rollout.md` lines 143 and 229: `me.yuda.brew-status` becomes
     `com.valor.brew-nightly` (it is a `com.valor.*` job, so the
     `launchctl list | grep me.yuda` check goes).
   - `docs/machine.md` (~l.251) and `core/README.md` (permissions entry):
     drop the Brew Status sentence and say Studio Tools will replace it per
     #2.
3. **Vault** (Cowboy, `~/Desktop/Valor/brew`): restore
   `brew-nightly-update.sh` and `com.valor.brew-nightly.plist` from the
   retired `vault/` copy. Restore `SETUP.md` from that copy, plus one
   paragraph: Brew Status was removed 2026-10-28, and this job runs until
   Studio Tools deploys (yudame/studio-tools#2).
4. **Each Mac** (the Cowboy locally; the Pirate, the Captain and the Bald
   over `ssh -o BatchMode=yes`). First look at what each Mac has, then:
   - Remove Brew Status:
     - quit BrewStatus (`osascript quit`, then `pkill`);
     - `launchctl bootout gui/$UID/me.yuda.brew-status` and remove its
       plist;
     - remove `~/Applications/BrewStatus.app`, `~/.local/bin/brew-status`,
       `~/src/brew-status` and `~/.config/brew-status/local.sh`;
     - unregister the login item. Removing the app ends `SMAppService`
       registration; the build confirms with `sfltool dumpbtm` where it
       answers, and otherwise checks that BrewStatus is gone from the
       running processes and Login Items.
   - Move `frozen.txt` to `~/.config/brew-nightly/skip-casks.txt` where it
     is non-empty, then remove `~/.config/brew-status`.
   - Install the old job:
     - put the script in `~/.local/bin/brew-nightly-update.sh`;
     - write the plist to `~/Library/LaunchAgents` with `$HOME`
       substituted, and with `valor-run` first (Valor.app exists on all
       four);
     - `launchctl bootstrap`.
   - Start one run with `launchctl kickstart` (the Pirate, the Captain and
     the Bald first; the Cowboy last, after the commit and the #2 comment,
     since a Python upgrade in the run restarts every running `com.valor.*`
     job, this build's kernel among them) and watch
     `~/Library/Logs/brew-nightly.log`. It must reach `=== brew nightly
     update done ===` within about 20 minutes, through `valor-run`. A run
     that hangs is sampled, and a grant only a person at the Mac can give
     is named in the delivery as open for that Mac, not left silent.
5. **studio-tools#2**: one comment saying Valor's four Macs run the vault
   job `com.valor.brew-nightly` until Studio Tools is deployed, then switch
   over (`gh issue comment`, as Valor).

**Found in the build.** A turn's sandbox cannot bootstrap a launchd job
(`Bootstrap failed: 5`, even `/usr/bin/true`), so the Cowboy's job is put
in place but loaded by Valor's kernel: `permissions.ensure` now loads any
`com.valor.*` plist that is not loaded (only the Cowboy's
`com.valor.brew-nightly` on the three Macs checked), at this merge's
rollout or the 03:00 update, before 04:00. Tested.

## Out of scope

Packaging or deploying Studio Tools (#2). `yudame/brew-status` stays
archived as it is. The shared `~/Library/Logs/brew-nightly.log` is kept.

## Risk: reinstall before the merge reaches each Mac

`permissions.ensure` runs at the end of every rollout and on every
up-to-date update check (`core/serve.py` `_setup`), so another task's
merge or the 03:00 update before this one lands reinstalls the app,
re-clones `~/src/brew-status` (Cowboy, Captain) and relinks `local.sh`.
So the build removes Brew Status now, and once this merge's
`rollout.ended` row is on each Mac, the post-merge check (step 6) runs
again and removes anything that came back.

6. **After the merge rolls out** (each Mac's `rollout.ended` for this
   head): recheck `pgrep BrewStatus`, the six paths and `me.yuda.brew-status`
   on all four, remove what came back, and record that check as the
   delivery's live result. The build's own checks are provisional.

## Tests

- `uv run pytest tests/test_permissions.py`: the `ensure` test still passes
  without the monkeypatches. Its fixture's `com.valor.brew-nightly` bash job
  is left unwrapped and not reloaded, and `ensure` with no Brew Status calls
  returns no `BrewStatus` entry.
- `tests/test_bloat.py` and the full suite in a separate worktree, under 5
  minutes.
- Live, per Mac, taken after this merge's rollout (step 6):
  - `pgrep BrewStatus` is empty;
  - none of the six paths exist;
  - `launchctl print gui/$UID/me.yuda.brew-status` fails;
  - `launchctl print .../com.valor.brew-nightly` shows `program =
    …/valor-run`, the 04:00 calendar interval and the last exit 0;
  - the kickstarted run's log ends with `done`, and Ollama, where it is
    outdated and running, is upgraded and reopened.
- `grep -rn "brew.status\|brew_status" core scripts tests docs/rollout.md
  docs/machine.md` is empty.
