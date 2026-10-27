import BrewStatusKit
import Foundation

// brew-status          the nightly Homebrew job's last run on this Mac, as text
// brew-status --json   the same as JSON
// Exit code: 0 green, 1 amber, 2 red.
let arguments = CommandLine.arguments.dropFirst()
if arguments.contains("--help") || arguments.contains("-h") {
    print("usage: brew-status [--json]\nReads \(Job.logURL.path).\nExit 0 green, 1 amber, 2 red.\n\n\(Summary.rule)")
    exit(0)
}
let summary = Job.summary()
print(arguments.contains("--json") ? summary.json() : summary.text())
exit(summary.status.exitCode)
