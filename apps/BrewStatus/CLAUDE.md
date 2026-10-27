# BrewStatus

A menu bar extra (no Dock icon) showing the nightly Homebrew job's last run
on this Mac, plus the `brew-status` CLI. It reads the job's log; it stores
nothing. What it is and the status rule: `README.md`.

## Layout

- `Sources/BrewStatusKit/` parser, status rule, schedule and `launchctl`, no SwiftUI. Tests in `Tests/BrewStatusKitTests/` on real-log fixtures.
- `Sources/BrewStatus/` the app: `App.swift` (scene, login item), `StatusModel.swift` (refresh every 3 s when the log or the job's state changes), `PopoverView.swift` (the one screen), `Snapshot.swift`.
- `Sources/brew-status/` the CLI.
- `build.sh` builds, signs, installs `~/Applications/BrewStatus.app`, links `~/.local/bin/brew-status`.
- `Icon/AppIcon.icon` the icon; `Resources/` is generated from it.

## Build and check

- Every `swift build`/`swift test` takes `--disable-sandbox` (Valor's turns run under `sandbox-exec`); `build.sh` already passes it.
- Unit tests: `swift test --disable-sandbox --scratch-path ~/Library/Developer/BrewStatus/test-build`.
- After a UI change: `./build.sh`, `tools/snap.sh`, look at `/tmp/BrewStatus-shots/popover-light.png` and `-dark.png`, then `tools/grid-lint.sh` and `swift tools/check-layout.swift /tmp/BrewStatus-shots/*-layout.json`.
- Snapshot mode draws the view itself (screencapture returns black in Valor's sessions) from the real log, read only.
- Never touch the brew script or its plist, and never install anything as `~/Applications/BrewNightly.app` (the script deletes that path).

## Design rules

- System controls, system text styles, `Space` from `UI/Grid.swift` on the 4 pt grid.
- The only colours are the status colours, in one place: `StatusDot`.
- Words are in `docs/GLOSSARY.md`.
