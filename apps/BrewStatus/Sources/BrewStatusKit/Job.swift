import Foundation

/// The nightly job on this Mac: its log, its plist schedule, and launchd.
public enum Job {
    public static let label = "com.valor.brew-nightly"
    static let home = FileManager.default.homeDirectoryForCurrentUser

    /// APP_LOG points the app or the CLI at another log (tests, experiments).
    public static var logURL: URL {
        if let path = ProcessInfo.processInfo.environment["APP_LOG"], !path.isEmpty { return URL(fileURLWithPath: path) }
        return home.appending(path: "Library/Logs/brew-nightly.log")
    }

    public static var plistURL: URL { home.appending(path: "Library/LaunchAgents/\(label).plist") }
    static var target: String { "gui/\(getuid())/\(label)" }

    public static func readRuns() -> [Run] {
        guard let data = try? Data(contentsOf: logURL) else { return [] }
        return LogParser.parse(String(decoding: data, as: UTF8.self))
    }

    /// The summary as of now, read from the log, the plist and launchd.
    public static func summary(now: Date = .now) -> Summary {
        let intervals = (try? Data(contentsOf: plistURL)).map(Schedule.intervals(plist:)) ?? []
        return Summary(runs: readRuns(), running: isRunning(), nextRun: Schedule.next(after: now, intervals), now: now)
    }

    public static func isRunning() -> Bool {
        Schedule.isRunning(launchctlPrint: launchctl("print", target))
    }

    /// Starts the job now, like its 04:00 start.
    @discardableResult
    public static func kickstart() -> Bool {
        launchctl("kickstart", target) != nil
    }

    /// The command's output, or nil when it failed.
    private static func launchctl(_ arguments: String...) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return process.terminationStatus == 0 ? String(decoding: data, as: UTF8.self) : nil
    }
}

/// The plist's StartCalendarInterval and launchd's state, as plain values.
public enum Schedule {
    /// StartCalendarInterval is one dict or an array of dicts. A missing key matches any value.
    public static func intervals(plist data: Data) -> [DateComponents] {
        guard let root = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else { return [] }
        let raw = root["StartCalendarInterval"]
        let dicts = (raw as? [[String: Int]]) ?? ((raw as? [String: Int]).map { [$0] } ?? [])
        return dicts.map { dict in
            var c = DateComponents()
            c.minute = dict["Minute"]
            c.hour = dict["Hour"]
            c.day = dict["Day"]
            c.month = dict["Month"]
            // launchd: 0 and 7 are Sunday. Calendar: 1 is Sunday.
            c.weekday = dict["Weekday"].map { $0 % 7 + 1 }
            return c
        }
    }

    public static func next(after now: Date, _ intervals: [DateComponents], calendar: Calendar = .current) -> Date? {
        intervals.compactMap { calendar.nextDate(after: now, matching: $0, matchingPolicy: .nextTime) }.min()
    }

    public static func isRunning(launchctlPrint output: String?) -> Bool {
        output?.split(separator: "\n").contains { $0.trimmingCharacters(in: .whitespaces) == "state = running" } ?? false
    }
}
