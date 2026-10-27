import Foundation

/// One run of the nightly job, from its `=== ... start ===` line to its `=== ... done ===` line.
public struct Run: Codable, Equatable, Sendable {
    public var start: Date
    /// Nil when the run never logged its done line.
    public var end: Date?
    public var upgrades: [Upgrade] = []
    public var failures: [String] = []
    public var skips: [Skip] = []
    public var timeouts: [String] = []
    public var errors: [String] = []
    public var warnings: [String] = []

    public var finished: Bool { end != nil }
    public var issueSkips: [Skip] { skips.filter { !$0.frozen } }
    public var frozen: [String] { skips.filter(\.frozen).map(\.name) }
}

public struct Upgrade: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, Sendable { case formula, cask }
    public var name: String
    public var kind: Kind
    public var from: String?
    public var to: String?
}

public struct Skip: Codable, Equatable, Sendable {
    public var name: String
    public var reason: String
    public var frozen: Bool
}

/// Reads `brew-nightly.log` into runs, oldest first.
public enum LogParser {
    public static func parse(_ text: String, timeZone: TimeZone = .current) -> [Run] {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = timeZone
        format.dateFormat = "yyyy-MM-dd HH:mm:ss"

        var runs: [Run] = []
        var current: Run?
        var restarting = false
        var inCasks = false
        var pendingUpgrade: Int?   // index of the upgrade waiting for its "a -> b" line

        func close() { if let run = current { runs.append(run) }; current = nil }

        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            // "[2026-10-27 04:00:05] message": the script's own lines.
            if line.hasPrefix("["), let bracket = line.firstIndex(of: "]"),
               let date = format.date(from: String(line[line.index(after: line.startIndex)..<bracket])) {
                let message = line[line.index(after: bracket)...].trimmingCharacters(in: .whitespaces)
                pendingUpgrade = nil
                if message == "=== brew nightly update start ===" {
                    // The script re-executes itself after updating from the vault: one run, not two.
                    if restarting, current != nil { restarting = false; continue }
                    close()
                    current = Run(start: date)
                    inCasks = false
                    continue
                }
                guard current != nil else { continue }
                if message == "=== brew nightly update done ===" {
                    current?.end = date
                    close()
                } else if message.hasPrefix("SETUP:"), message.contains("restarting") {
                    restarting = true
                } else if message.hasPrefix("--- brew upgrade") {
                    inCasks = message.contains("casks")
                } else if message.hasPrefix("--- ") {
                    inCasks = false
                } else if message.hasPrefix("FAILED:") {
                    current?.failures.append(after("FAILED:", in: message))
                } else if message.contains("returned nonzero") {
                    current?.failures.append(message)
                } else if message.hasPrefix("TIMEOUT") {
                    current?.timeouts.append(message)
                } else if message.hasPrefix("SKIP ("), let paren = message.firstIndex(of: ")") {
                    let tag = String(message[message.index(message.startIndex, offsetBy: 6)..<paren])
                    let rest = message[message.index(after: paren)...]
                        .trimmingCharacters(in: CharacterSet(charactersIn: ": ").union(.whitespaces))
                    current?.skips.append(tag == "frozen"
                        ? Skip(name: rest, reason: "frozen", frozen: true)
                        : Skip(name: tag, reason: rest, frozen: false))
                }
                continue
            }
            guard current != nil else { continue }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("Error:") {
                current?.errors.append(trimmed)
            } else if line.hasPrefix("Warning:") {
                current?.warnings.append(trimmed)
            } else if line.hasPrefix("==> Upgrading ") {
                // "==> Upgrading redis" or "==> Upgrading cocoapods dependency: openssl@3"
                let rest = after("==> Upgrading", in: line)
                let name = rest.components(separatedBy: " dependency: ").last ?? rest
                guard !name.isEmpty, !name.contains(" ") else { continue }
                current?.upgrades.append(Upgrade(name: name, kind: inCasks ? .cask : .formula))
                pendingUpgrade = (current?.upgrades.count ?? 1) - 1
            } else if let index = pendingUpgrade {
                pendingUpgrade = nil
                let parts = trimmed.components(separatedBy: " -> ")
                if parts.count == 2, !parts[0].contains(" "), !parts[1].contains(" ") {
                    current?.upgrades[index].from = parts[0]
                    current?.upgrades[index].to = parts[1]
                }
            }
        }
        close()
        return runs
    }

    private static func after(_ prefix: String, in text: some StringProtocol) -> String {
        String(text.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
    }
}
