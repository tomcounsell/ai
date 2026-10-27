# Stories

## M1 Glance and read (the whole app)

### S-001 See the job's status in the menu bar
As Tom, I see a green, amber or red dot in the menu bar.
Screens: popover-light.png, popover-dark.png
Acceptance:
- [unit] Red when the newest run never finished, no run finished in the last 26 hours, or an upgrade failed.
- [unit] Amber when the run had any `Error:` or `Warning:` line, a TIMEOUT, or a non-frozen SKIP; green otherwise.
- [unit] A run the script restarted on itself (`SETUP: ... restarting on it`) counts as one run.
- [manual] The dot shows in colour in the menu bar; there is no Dock icon.
UX: menu bar extra (the app's one entry point). The dot carries an accessibility label "Brew nightly: Amber"; colour is never the only signal, the popover title says the word.
Status: done

### S-002 Read last night's run
As Tom, I click the dot and see the run's start and finish, upgrades with versions, failures, skips, timeouts, errors and warnings, with counts, and the rule in the help text.
Screens: popover-light.png, popover-dark.png
Acceptance:
- [unit] Upgrades come from `==> Upgrading <name>` (and `... dependency: <name>`) with the `a -> b` line under it, marked formula or cask by section.
- [unit] `FAILED: x` lines are failures; `SKIP (frozen)` and `SKIP (<cask>): reason` are skips.
- [snap] The popover shows the real log's newest run, readable in light and dark, with nothing clipped.
UX: window-style popover from the dot ("a few related things tied to one control"). Header: dot, "Brew Nightly: Amber", the run's date and times. Then one counts line, the reasons, and sections that appear only when they have rows. Long lines truncate to one line with the full text as help. Empty state: "No runs in the log yet". Help text at the bottom states the rule.
Status: done

### S-003 See when the next run is
As Tom, I see the next run time, read from the job's plist StartCalendarInterval.
Acceptance:
- [unit] Hour 4, minute 0 gives the next 04:00 after now; an array of intervals gives the earliest.
UX: one line in the header, "Next run: tomorrow 04:00". If the plist has no schedule: "Next run: not scheduled".
Status: done

### S-004 Run the job now
As Tom, I click Run now; it shows Running… and is disabled while `launchctl print` says `state = running`, and the popover refreshes as the log changes.
Acceptance:
- [unit] `state = running` in `launchctl print` output reads as running.
- [manual] Run now starts the job; the button reads Running… until it ends.
UX: "Run Now" button, bottom bar. While running it reads "Running…" with a small spinner and is disabled. Flow: click -> launchctl kickstart -> Running… -> log changes refresh the popover -> button returns.
Status: done

### S-005 Open the log, quit
Acceptance:
- [manual] Open log opens the log in Console; Quit quits.
UX: "Open Log" and "Quit" in the bottom bar; Quit has Command-Q.
Status: done

### S-006 Read the status from a terminal
As Tom or an agent, I run `brew-status` (or `--json`) and get the same summary; exit 0 green, 1 amber, 2 red.
Acceptance:
- [unit] The text and JSON summaries carry the status, counts and next run.
- [manual] `~/.local/bin/brew-status` prints the real summary.
Status: done

### S-007 Start at login
Acceptance:
- [manual] After the first launch the app is a login item (`SMAppService.mainApp`).
Status: done

## Dropped
- Notifications: Tom said no.
- Settings window, accent picker: one screen, nothing to set.
- Seed data: the snapshots use the real log.
