# BrewStatus

A menu bar dot on each of Valor's Macs that tells Tom, at a glance, how last
night's Homebrew update job (`com.valor.brew-nightly`) went on that Mac.

## Replaces
Opening `~/Library/Logs/brew-nightly.log` in a terminal and scrolling.

## Main jobs
1. Glance at the dot: green, amber or red.
2. Click it: last night's run, when it started and finished, what was
   upgraded (with versions), what failed, warnings, skips, the next run.
3. Run the job now, open the log, or read the same summary from a terminal
   (`brew-status`, exit code 0/1/2).

## Data
- Run: start, end (or none), upgrades, failures, skips, timeouts, errors, warnings.
- Upgrade: name, formula or cask, from and to version (when the log has them).
- Skip: name, reason, frozen or not.
Read from the job's log each time it changes; nothing is stored.

## Shape
Menu bar extra with a window-style popover, no Dock icon, macOS 26 or
later. Local, offline, no accounts, view only.

## System hooks
Menu bar, launch at login (`SMAppService.mainApp`), `launchctl` for the
job's state and "Run now", a CLI (`brew-status`, `--json`).

## Reference
none

## Look
Native, system accent. The only colour is the status dot (green, amber, red).
Decided by the agent from Tom's answers.

## Not in version one
- Notifications.
- Other Macs' runs (each Mac shows only its own).
- Editing the job, its frozen list, or its schedule.
