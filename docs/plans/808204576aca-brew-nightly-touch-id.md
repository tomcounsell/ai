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

Reuse (hard rule 10): Yudame Tools' `Resources/nightly.sh` met the same hang
(Ollama's launchctl probe on a Touch ID sheet, 2026-10-28, 04:02-09:28) and
already has the three pieces: `HOMEBREW_NO_SUDO` with `SUDO_ASKPASS` unset
(lines 37-42), a sudo-failure grep (221) and a reopen-on-TERM trap (285).
The vault script takes the same wording and patterns.

1. Near the top, beside the other `HOMEBREW_*` settings: `export
   HOMEBREW_NO_SUDO=1; unset SUDO_ASKPASS`, with nightly.sh's comment adapted
   to cite both cases (10-28 and 11-25). Homebrew then skips its sudo probes
   (`next if sudo && !SystemCommand.sudo_available?`) and fails a step that
   really needs root at once ("sudo is disabled by HOMEBREW_NO_SUDO"). Nothing
   is lost: a 04:00 run has no person, no terminal and no cached credential, so
   sudo never succeeded unattended on any Mac.
2. **A cask that needs a person.** The upgrade's output goes to a temp file as
   well as the log. When it fails and the file matches nightly.sh's grep
   (`no password was provided|a terminal is required|a password is required|sudo is
   disabled`), the line is `SKIP (<cask>): needs a person at the Mac (sudo) —
   leaving it outdated` instead of `FAILED: <cask>`. Its apps are relaunched as on
   any failure, and the token and installed version go into `NEEDS_PERSON`. It stays
   at its version because the step fails before changing anything, and it is
   retried each night. Yudame Tools' parser (Run.swift:212) reads it as a skip.
3. **Quit apps come back however the run ends.** The upgrade runs in the
   background, `brew upgrade --cask --greedy-auto-updates "$cask" > >(tee "$out")
   2>&1 & pid=$!; wait $pid`, because bash runs a trap only after a foreground
   command ends, and `wait` is interrupted at once. A `TERM`/`INT`/`HUP` trap
   kills `$pid`, logs and relaunches each app in `running_apps` that is not
   running (`RELAUNCH: <app> (run ended)`), and exits 143. That is well inside
   launchd's 20-second ExitTimeOut before its SIGKILL. `running_apps` is cleared
   once a cask's own relaunch has run, so the trap acts only on a cask that was
   cut short.
4. **Status file.** `yudame_tools()` rewrites `yudame-tools/status/<Mac>.txt`
   early in each run, in a subshell, without the new line. After the cask loop
   the script adds `needs a person: <cask> <version>, ...` or `needs a person:
   none` (also `none` on a watch-held night, which upgrades nothing). It writes
   `<file>.new` and renames it into place, as the existing code does, so iCloud
   never syncs a partial file. A run cut short before the loop ends leaves the
   line absent, which is how the file says the run did not finish.
5. Sync: the vault copy is the source; each Mac's `~/.local/bin` copy follows at
   its next run (setup step 1). The build copies it now through the same
   `valor-run` one-shot route task ecde7682ce91 used, after waiting for iCloud to
   deliver the vault file (that task's Captain slip), and checks sha256 on all
   four.
6. `SETUP.md`: one line saying unattended runs never use sudo, and what a
   `needs a person` status line means.

## Out of scope

- `/etc/pam.d/sudo_local`: it stays. It is for a person at the Mac, and
  removing it would need root.
- The orphaned root `sudo` from 11-25 (pid 62719 on the Cowboy) cannot be
  signalled from this user. It is harmless and goes at the next reboot.
- 1Password and iTerm refusing to quit; Yudame Tools' own `nightly.sh` (its
  Homebrew Updates is off on all four Macs).
- No timeout, no new gate or check (the brief forbids them, and none is needed
  once the wait is gone). This adds no governance: item 1 sets Homebrew's own
  switch, as Yudame Tools' nightly already does, and items 2-4 fix the restore
  and report paths.

## Tests (test-step.sh, fake brew)

- `HOMEBREW_NO_SUDO=1` is in brew's environment and `SUDO_ASKPASS` is not.
- A fake cask upgrade prints `sudo is disabled by HOMEBREW_NO_SUDO` and fails:
  `SKIP (<cask>): needs a person` is logged and `FAILED: <cask>` is not. The app
  is relaunched (`open -a`), and the status file has `needs a person: <cask> ...`
  with no `.new` left.
- A plain failure (other output): `FAILED: <cask>` as before, and the line
  reads `none`.
- A clean night and a watch-held night: `needs a person: none`.
- TERM mid-upgrade: the fake brew sleeps 30 s and the test sends TERM to the
  script. Within 5 s the script has exited and `open -a <app>` was called. The
  test fails under a deferred trap.
- A frozen cask is still skipped before any quit.
- The existing 66 cases still pass; `bash -n`.
- Live, on the Cowboy, through a `valor-run` one-shot job and not the turn's
  sandbox (which refuses sudo itself): the new script's upgrade path for
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
