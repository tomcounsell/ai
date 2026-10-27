import Foundation
import Testing
@testable import BrewStatusKit

/// Real runs copied from ~/Library/Logs/brew-nightly.log on the Cowboy.
private let zone = TimeZone(identifier: "Asia/Bangkok")!

private func fixture(_ name: String) throws -> String {
    let url = try #require(Bundle.module.url(forResource: name, withExtension: "log", subdirectory: "Fixtures"))
    return try String(contentsOf: url, encoding: .utf8)
}

private func runs(_ names: String...) throws -> [Run] {
    LogParser.parse(try names.map(fixture).joined(separator: "\n"), timeZone: zone)
}

private func date(_ text: String) -> Date {
    let format = DateFormatter()
    format.locale = Locale(identifier: "en_US_POSIX")
    format.timeZone = zone
    format.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return format.date(from: text)!
}

@Suite struct RunBoundaries {
    @Test func finishedRun() throws {
        let all = try runs("amber-2026-10-27")
        #expect(all.count == 1)
        #expect(all[0].start == date("2026-10-27 04:00:05"))
        #expect(all[0].end == date("2026-10-27 04:02:38"))
    }

    @Test func runsInOrder() throws {
        let all = try runs("failed-2026-10-09", "restart-2026-10-24", "amber-2026-10-27")
        #expect(all.map(\.start) == [date("2026-10-09 04:00:03"), date("2026-10-24 04:00:05"), date("2026-10-27 04:00:05")])
        #expect(all.allSatisfy { $0.finished })
    }

    @Test("A start line with no done line before the next start is an unfinished run")
    func unfinished() throws {
        let all = try runs("unfinished-2026-09-08")
        #expect(all.count == 2)
        #expect(all.allSatisfy { !$0.finished })
        #expect(all[0].errors == ["Error: SIGTERM"])
    }

    @Test("The script restarting on itself after SETUP is one run")
    func restart() throws {
        let all = try runs("restart-2026-10-24")
        #expect(all.count == 1)
        #expect(all[0].start == date("2026-10-24 04:00:05"))
        #expect(all[0].end == date("2026-10-24 04:06:53"))
    }
}

@Suite struct RunContents {
    @Test func upgradesWithVersions() throws {
        let run = try #require(try runs("amber-2026-10-27").first)
        #expect(run.upgrades == [
            Upgrade(name: "redis", kind: .formula, from: "8.10.2_1", to: "8.10.3"),
            Upgrade(name: "python@3.13", kind: .formula, from: "3.13.17", to: "3.13.18"),
            Upgrade(name: "python@3.12", kind: .formula, from: "3.12.15_1", to: "3.12.16"),
            Upgrade(name: "cursor", kind: .cask,
                    from: "3.27.12,df132ffba90dfc15a12e6b8f020ba4e5d026c4be",
                    to: "3.27.14,69b724ebc67ec522c1b5d287a5025f7a7e81be97"),
            Upgrade(name: "github", kind: .cask, from: "3.6.9-e0ba6bb8", to: "3.6.10-ffeefe5d"),
        ])
    }

    @Test("A dependency upgrade is named by the dependency")
    func dependencyUpgrade() {
        let log = """
            [2026-09-09 04:00:02] === brew nightly update start ===
            [2026-09-09 04:00:10] --- brew bundle (vault manifest) ---
            ==> Upgrading cocoapods dependency: openssl@3
            ==> Pouring openssl@3--3.6.4.arm64_tahoe.bottle.tar.gz
            [2026-09-09 04:06:00] === brew nightly update done ===
            """
        let run = LogParser.parse(log, timeZone: zone)[0]
        #expect(run.upgrades == [Upgrade(name: "openssl@3", kind: .formula, from: nil, to: nil)])
    }

    @Test func skips() throws {
        let run = try #require(try runs("amber-2026-10-27").first)
        #expect(run.frozen == ["anythingllm", "claude", "figma", "kitty", "notion-calendar", "superwhisper"])
        #expect(run.issueSkips == [Skip(name: "ollama-app",
                                        reason: "could not safely quit and restart Ollama.app — leaving it outdated",
                                        frozen: false)])
        #expect(run.timeouts == ["TIMEOUT quitting Ollama.app after 30s"])
    }

    @Test func errorsAndWarnings() throws {
        let run = try #require(try runs("amber-2026-10-27").first)
        #expect(run.errors == ["Error: python@3.14 is pinned. You must unpin it to uninstall.",
                               "Error: Could not cleanup old kegs! Fix your permissions on:"])
        #expect(run.warnings.count == 8)
        #expect(run.warnings.first == "Warning: Formulae dependency graph sorting found a circular dependency:")
    }

    @Test func failures() throws {
        let run = try #require(try runs("failed-2026-10-09").first)
        #expect(run.failures == ["github", "ollama-app"])
    }
}

@Suite struct StatusColour {
    let morning = date("2026-10-27 09:00:00")

    @Test("Errors, warnings, a timeout and a skip make amber")
    func amber() throws {
        let summary = Summary(runs: try runs("amber-2026-10-27"), running: false, nextRun: nil, now: morning)
        #expect(summary.status == .amber)
        #expect(summary.reasons == ["2 errors, 8 warnings, 1 timeout, 1 skip."])
    }

    @Test func green() {
        let log = """
            [2026-10-27 04:00:05] === brew nightly update start ===
            [2026-10-27 04:00:28] SKIP (frozen): claude
            [2026-10-27 04:02:38] === brew nightly update done ===
            """
        let summary = Summary(runs: LogParser.parse(log, timeZone: zone), running: false, nextRun: nil, now: morning)
        #expect(summary.status == .green)
        #expect(summary.reasons.isEmpty)
    }

    @Test("A failed upgrade makes red")
    func failed() throws {
        let summary = Summary(runs: try runs("failed-2026-10-09"), running: false, nextRun: nil,
                              now: date("2026-10-09 12:00:00"))
        #expect(summary.status == .red)
        #expect(summary.reasons == ["2 upgrades failed."])
    }

    @Test("No finished run in the last 26 hours makes red")
    func stale() throws {
        let summary = Summary(runs: try runs("amber-2026-10-27"), running: false, nextRun: nil,
                              now: date("2026-10-28 06:03:00"))
        #expect(summary.status == .red)
        #expect(summary.reasons == ["No run finished in the last 26 hours."])
    }

    @Test("The newest run never finishing makes red")
    func neverFinished() throws {
        var all = try runs("amber-2026-10-27")
        all.append(Run(start: date("2026-10-27 08:00:00")))
        let summary = Summary(runs: all, running: false, nextRun: nil, now: morning)
        #expect(summary.status == .red)
        #expect(summary.reasons == ["The newest run never finished."])
    }

    @Test("While the job runs, the run before it is on show")
    func running() throws {
        var all = try runs("amber-2026-10-27")
        all.append(Run(start: date("2026-10-27 09:00:00")))
        let summary = Summary(runs: all, running: true, nextRun: nil, now: morning)
        #expect(summary.status == .amber)
        #expect(summary.run?.start == date("2026-10-27 04:00:05"))
    }

    @Test func emptyLog() {
        let summary = Summary(runs: [], running: false, nextRun: nil, now: morning)
        #expect(summary.status == .red)
        #expect(summary.status.exitCode == 2)
    }

    @Test func textAndJSON() throws {
        let summary = Summary(runs: try runs("amber-2026-10-27"), running: false,
                              nextRun: date("2026-10-28 04:00:00"), now: morning)
        let text = summary.text(now: morning)
        #expect(text.hasPrefix("Brew nightly: AMBER"))
        #expect(text.contains("Counts: 5 upgraded, 0 failed, 1 skip, 1 timeout, 2 errors, 8 warnings"))
        #expect(text.contains("redis (formula) 8.10.2_1 -> 8.10.3"))
        #expect(text.contains("ollama-app: could not safely quit"))
        let json = try JSONSerialization.jsonObject(with: Data(summary.json().utf8)) as? [String: Any]
        #expect(json?["status"] as? String == "amber")
        #expect(json?["nextRun"] != nil)
    }
}

@Suite struct NextRun {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = zone
        return c
    }

    @Test("Read from the job's real plist shape")
    func fromPlist() throws {
        let plist = """
            <?xml version="1.0" encoding="UTF-8"?>
            <plist version="1.0"><dict>
            <key>Label</key><string>com.valor.brew-nightly</string>
            <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
            </dict></plist>
            """
        let intervals = Schedule.intervals(plist: Data(plist.utf8))
        #expect(Schedule.next(after: date("2026-10-27 14:00:00"), intervals, calendar: calendar) == date("2026-10-28 04:00:00"))
        #expect(Schedule.next(after: date("2026-10-27 03:59:00"), intervals, calendar: calendar) == date("2026-10-27 04:00:00"))
    }

    @Test("An array of intervals gives the earliest; launchd's Weekday 0 is Sunday")
    func array() {
        let plist = """
            <plist version="1.0"><dict><key>StartCalendarInterval</key><array>
            <dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>30</integer></dict>
            <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
            </array></dict></plist>
            """
        let intervals = Schedule.intervals(plist: Data(plist.utf8))
        // Tuesday 27 Oct: Friday 30 Oct comes before Sunday 1 Nov.
        #expect(Schedule.next(after: date("2026-10-27 14:00:00"), intervals, calendar: calendar) == date("2026-10-30 04:00:00"))
        // Saturday 31 Oct: Sunday 1 Nov 09:30.
        #expect(Schedule.next(after: date("2026-10-31 14:00:00"), intervals, calendar: calendar) == date("2026-11-01 09:30:00"))
    }

    @Test func noSchedule() {
        #expect(Schedule.intervals(plist: Data("<plist><dict/></plist>".utf8)).isEmpty)
    }

    @Test func runningState() {
        #expect(Schedule.isRunning(launchctlPrint: "gui/501/com.valor.brew-nightly = {\n\tactive count = 1\n\tstate = running\n"))
        #expect(!Schedule.isRunning(launchctlPrint: "\tstate = not running\n"))
        #expect(!Schedule.isRunning(launchctlPrint: nil))
    }
}
