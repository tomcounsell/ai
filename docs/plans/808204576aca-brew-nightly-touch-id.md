# Plan: the vault's nightly Homebrew job never waits on Touch ID

Task 808204576aca. The script is outside this repo:
`~/Desktop/Valor/brew/brew-nightly-update.sh` (vault, iCloud) and its tests
`~/Desktop/Valor/yudame-tools/test-step.sh`. This file is the record.

## What happened (observed, 2026-11-25, the Cowboy's brew-nightly.log)

- 05:03:50 `UPGRADE: ollama-app`, after `QUIT: Ollama.app`. Homebrew printed
  `Removing launchctl service com.ollama.ollama` and then nothing until the
  hand-kill at 08:53. The backtrace shows where it stopped:
  `abstract_uninstall.rb:295 uninstall_launchctl` → `launchctl_find_service`
  → `launchctl_run(sudo: true)` → `SystemCommand`, i.e.
  `/usr/bin/sudo -u root -E -- launchctl print system/com.ollama.ollama`.
- Why it waits: Homebrew 7.0.16 calls sudo without `-n`, and its own
  `utils/sudo.sh` keeps sudo on when the check only says "a password is
  required" (`HOMEBREW_NO_SUDO`: "A password requirement ... preserves normal
  sudo behaviour"). The Cowboy's `/etc/pam.d/sudo_local` has
  `auth sufficient pam_tid.so`, so sudo puts up a Touch ID sheet in the GUI
  session and blocks on it with no timeout. Ollama stayed quit the whole time.
- Exposure on the others (checked over ssh today): the Bald has the same
  `pam_tid.so` line, so it hangs the same way on any cask step that asks for
  sudo. The Pirate and the Captain have no `sudo_local`: there, sudo with no
  terminal fails at once, the sudo probe returns "not found", and their
  `ollama-app` upgrades finished in seconds on 11-25.

## What will be built (in the vault script)

1. `export HOMEBREW_NO_SUDO=1` next to the other `HOMEBREW_*` settings at the
   top, with a comment that cites this case. Homebrew then skips its sudo probes
   (`next if sudo && !SystemCommand.sudo_available?`) and fails any step that
   really needs root at once ("sudo is disabled by HOMEBREW_NO_SUDO") instead of
   prompting. No capability is lost: a 04:00 run has no person, no terminal and
   no cached credential, so sudo never succeeded on any Mac unattended. On the
   Pirate and the Captain it already failed, just later. This is Homebrew's
   documented switch, not a new check.
2. **A cask that can only be upgraded with a person present** (brew's output
   contains `sudo is disabled by HOMEBREW_NO_SUDO`) is logged with the existing
   skip words, `SKIP (<cask>): needs a person at the Mac (sudo) — leaving it
   outdated`, in place of `FAILED: <cask>`. Its apps are relaunched as on any
   failure, and it is added to one line in the existing status file
   (`yudame-tools/status/<Mac>.txt`): `needs a person: <cask> <installed>`, or
   `none`. The file is rewritten every run, so the line holds only tonight's
   casks. Nothing persistent and no new list: the cask stays at its version
   because the step fails without changing anything, and it is retried each
   night. The output is captured with `tee` into a temp file; the exit status
   stays brew's under `pipefail`.
3. **Apps the run quit come back however it ends.** Today they are relaunched
   after success, failure or a quit timeout, but not when the run itself is
   ended mid-upgrade (as on 11-25, had the script been killed rather than brew).
   An `EXIT` trap, plus `TERM`/`INT`/`HUP` traps that exit, relaunches any app
   in the current cask's `running_apps` that is not running. `running_apps` is
   cleared after each cask's relaunch, so the trap only acts on a cask that was
   cut short.
4. Sync: the vault copy is the source; each Mac's `~/.local/bin` copy follows at
   its next run (setup step 1), and the build copies it now through the same
   `valor-run` one-shot route task ecde7682ce91 used. It waits for iCloud to
   deliver the vault file first (that task's Captain slip) and checks
   sha256 on all four.
5. `SETUP.md`: one line saying unattended runs never use sudo, and what a
   `needs a person` status line means.

## Out of scope

- `/etc/pam.d/sudo_local`: it stays. It is for a person at the Mac, and
  removing it would need root.
- The orphaned root `sudo` from 11-25 (pid 62719 on the Cowboy) cannot be
  signalled from this user. It is harmless and goes at the next reboot.
- 1Password and iTerm refusing to quit; Yudame Tools' own `nightly.sh` (its
  Homebrew Updates is off on all four Macs).
- No timeout, no new gate or check (the brief forbids them, and none is needed
  once the wait is gone). This adds no governance: item 1 sets a Homebrew
  environment switch, items 2 and 3 fix the restore and report paths.

## Tests (test-step.sh, fake brew)

- `HOMEBREW_NO_SUDO=1` is in brew's environment for every call.
- A fake cask upgrade that prints `sudo is disabled by HOMEBREW_NO_SUDO` and
  fails: `SKIP (<cask>): needs a person` logged, no `FAILED: <cask>`, its app
  relaunched (`open -a`), the status file has `needs a person: <cask> ...`.
- A plain failure (other output): `FAILED: <cask>` as before, not listed.
- A clean night: the status line reads `needs a person: none`.
- A run killed with TERM while the fake brew sleeps mid-upgrade: the quit app
  is reopened and the script exits.
- A frozen cask is still skipped before any quit; a watch hold still upgrades
  nothing and quits nothing.
- The existing 66 cases still pass; `bash -n`.
- Live, on the Cowboy, through a `valor-run` one-shot job and not the turn's
  sandbox (which refuses sudo itself): run the new script's path for
  `ollama-app`. It upgrades 0.40.17 → 0.40.18 with no Touch ID sheet and no
  wait, and Ollama is running afterwards. On the Bald, `brew` with the new
  environment reports sudo unavailable in seconds.

## Tonight (2026-11-26 04:00 +07 = 2026-11-25 21:00 UTC)

Each Mac's `brew-nightly.log` should show one `=== brew nightly update start
===`, one `done`, no gap longer than a few minutes inside a cask upgrade, Ollama
running afterwards, and the app job's "Homebrew Updates is switched off" line
with no brew process beside it. That run is about 19 hours after this build. If
it has not happened when the build ends, the delivery names it unverified with
these exact checks; the chat session's wake for this task reads it.
