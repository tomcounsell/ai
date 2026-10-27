# BrewStatus

A menu bar dot on each of Valor's Macs for the nightly Homebrew job
(`com.valor.brew-nightly`, 04:00 local). Green, amber or red; click it for
last night's run: start and finish, what was upgraded (formulae and casks,
with versions), what failed, skips, timeouts, errors and warnings, and the
next run. Buttons: Run Now (disabled and reading "Running…" while the job
runs), Open Log, Quit. No Dock icon, no notifications. Each Mac reads only
its own log, `~/Library/Logs/brew-nightly.log`; the schedule comes from
`~/Library/LaunchAgents/com.valor.brew-nightly.plist`.

## Install on a Mac

macOS 26 and Xcode 26 (Swift 6.2). From this folder:

```bash
./build.sh --open
```

That builds, signs (an Apple Development identity if the keychain has one,
else ad hoc), installs `~/Applications/BrewStatus.app`, links
`~/.local/bin/brew-status` into the bundle, and starts the app. On its first
launch the app adds itself to Login Items (`SMAppService.mainApp`); turn it
off in System Settings > General > Login Items and it stays off.

A Mac without Xcode 26 (the Pirate and the Bald have only Swift 6.0
Command Line Tools) gets the build of one that has it, over ssh: zip the
bundle with `ditto -c -k --keepParent`, unpack it into `~/Applications`
with `ditto -x -k`, link `~/.local/bin/brew-status` to
`BrewStatus.app/Contents/MacOS/brew-status`, and `open` the app. The
first launch adds the login item there too.

## Status rule

- **Red**: the newest run never finished, no run finished in the last 26
  hours, or an upgrade failed (`FAILED: <cask>`, or `... returned nonzero`).
- **Amber**: the run logged any `Error:` or `Warning:` line, a `TIMEOUT`,
  or a skip that is not frozen (`SKIP (<cask>): reason`).
- **Green**: otherwise.

A run in progress is not counted: while the job runs, the run before it is
on show. A run the script restarts on itself (`SETUP: ... restarting on
it`) is one run. `SKIP (frozen)` is listed, never a warning.

## CLI

```bash
brew-status          # the same summary as the popover, as text
brew-status --json   # as JSON
```

Exit code 0 green, 1 amber, 2 red. `APP_LOG=<file>` reads another log.

## Develop

- `Sources/BrewStatusKit/` the log parser (`Run.swift`), the status rule
  and the text (`Summary.swift`), the plist schedule and `launchctl`
  (`Job.swift`). Shared by the app and the CLI.
- `Sources/BrewStatus/` the menu bar app; `Sources/brew-status/` the CLI.
- Tests: `swift test --disable-sandbox --scratch-path ~/Library/Developer/BrewStatus/test-build`,
  on real runs copied into `Tests/BrewStatusKitTests/Fixtures/`.
- Snapshots of the popover from the real log, light and dark:
  `tools/snap.sh` writes `/tmp/BrewStatus-shots/popover-{light,dark}.png`.
- `docs/` the brief, the stories and the glossary.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.
