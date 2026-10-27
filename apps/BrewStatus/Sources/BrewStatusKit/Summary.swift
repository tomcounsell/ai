import Foundation

public enum Status: String, Codable, Sendable {
    case green, amber, red

    /// The CLI's exit code.
    public var exitCode: Int32 {
        switch self { case .green: 0; case .amber: 1; case .red: 2 }
    }
}

/// What the dot, the popover and the CLI all show.
public struct Summary: Codable, Sendable {
    public static let rule = """
        Red: the newest run never finished, no run finished in the last 26 hours, or an upgrade failed. \
        Amber: the run logged an Error: or Warning: line, a TIMEOUT, or a skip that is not frozen. \
        Green: otherwise. A run in progress is not counted.
        """

    public var status: Status
    public var reasons: [String]
    /// The run on show: the newest one, or the one before it while the job is running.
    public var run: Run?
    public var running: Bool
    public var nextRun: Date?

    public init(runs: [Run], running: Bool, nextRun: Date?, now: Date) {
        var runs = runs
        if running, runs.last?.finished == false { runs.removeLast() }
        self.run = runs.last
        self.running = running
        self.nextRun = nextRun

        guard let run = runs.last else {
            status = .red
            reasons = ["No runs in the log."]
            return
        }
        var red: [String] = []
        if !run.finished { red.append("The newest run never finished.") }
        let lastEnd = runs.last(where: \.finished)?.end ?? .distantPast
        if now.timeIntervalSince(lastEnd) > 26 * 3600 { red.append("No run finished in the last 26 hours.") }
        if !run.failures.isEmpty { red.append("\(count(run.failures.count, "upgrade")) failed.") }
        if !red.isEmpty {
            status = .red
            reasons = red
            return
        }
        var amber: [String] = []
        if !run.errors.isEmpty { amber.append(count(run.errors.count, "error")) }
        if !run.warnings.isEmpty { amber.append(count(run.warnings.count, "warning")) }
        if !run.timeouts.isEmpty { amber.append(count(run.timeouts.count, "timeout")) }
        if !run.issueSkips.isEmpty { amber.append(count(run.issueSkips.count, "skip")) }
        status = amber.isEmpty ? .green : .amber
        reasons = amber.isEmpty ? [] : [amber.joined(separator: ", ") + "."]
    }

    /// Plain text for the CLI.
    public func text(now: Date = .now) -> String {
        var out = ["Brew nightly: \(status.rawValue.uppercased())\(running ? " (running now)" : "")"]
        out += reasons.map { "  \($0)" }
        if let run {
            out.append("Last run: \(Format.span(run))")
            out.append("Counts: \(Format.counts(run))")
        }
        out.append("Next run: \(nextRun.map { Format.day($0, now: now) } ?? "not scheduled")")
        guard let run else { return out.joined(separator: "\n") }
        func section(_ title: String, _ lines: [String]) {
            guard !lines.isEmpty else { return }
            out.append("\(title) (\(lines.count)):")
            out += lines.map { "  \($0)" }
        }
        section("Upgraded", run.upgrades.map(Format.upgrade))
        section("Failed", run.failures)
        section("Skipped", run.issueSkips.map { "\($0.name): \($0.reason)" })
        if !run.frozen.isEmpty { out.append("Frozen: \(run.frozen.joined(separator: ", "))") }
        section("Timeouts", run.timeouts)
        section("Errors", Format.grouped(run.errors))
        section("Warnings", Format.grouped(run.warnings))
        return out.joined(separator: "\n")
    }

    public func json() -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .iso8601
        return String(decoding: (try? encoder.encode(self)) ?? Data(), as: UTF8.self)
    }
}

private func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }

/// Words shared by the popover and the CLI.
public enum Format {
    public static func upgrade(_ u: Upgrade) -> String {
        let versions = u.from.map { " \($0) -> \(u.to ?? "?")" } ?? ""
        return "\(u.name) (\(u.kind.rawValue))\(versions)"
    }

    public static func counts(_ run: Run) -> String {
        ["\(run.upgrades.count) upgraded",
         "\(run.failures.count) failed",
         count(run.issueSkips.count, "skip"),
         count(run.timeouts.count, "timeout"),
         count(run.errors.count, "error"),
         count(run.warnings.count, "warning")].joined(separator: ", ")
    }

    /// "Tue 27 Oct, 04:00:05 to 04:02:38 (2 min 33 s)", or "... did not finish".
    public static func span(_ run: Run) -> String {
        let start = run.start.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).hour(.twoDigits(amPM: .omitted)).minute().second())
        guard let end = run.end else { return "\(start), did not finish" }
        let seconds = Int(end.timeIntervalSince(run.start))
        return "\(start) to \(end.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute().second())) (\(seconds / 60) min \(seconds % 60) s)"
    }

    /// "today 04:00", "tomorrow 04:00", or "Thu 29 Oct 04:00".
    public static func day(_ date: Date, now: Date, calendar: Calendar = .current) -> String {
        let time = date.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute())
        if calendar.isDate(date, inSameDayAs: now) { return "today \(time)" }
        if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now), calendar.isDate(date, inSameDayAs: tomorrow) {
            return "tomorrow \(time)"
        }
        return "\(date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))) \(time)"
    }

    /// Repeated lines once, with "(x3)".
    public static func grouped(_ lines: [String]) -> [String] {
        var order: [String] = []
        var counts: [String: Int] = [:]
        for line in lines {
            if counts[line] == nil { order.append(line) }
            counts[line, default: 0] += 1
        }
        return order.map { counts[$0]! > 1 ? "\($0) (x\(counts[$0]!))" : $0 }
    }
}
