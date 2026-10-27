import AppKit
import BrewStatusKit
import Observation

/// The summary on show, refreshed when the log or the job's state changes.
@Observable
final class StatusModel {
    static let shared = StatusModel()

    private(set) var summary = Summary(runs: [], running: false, nextRun: nil, now: .now)
    /// Set by Run Now until launchd reports the job running, so the button cannot be pressed twice.
    private(set) var starting = false
    private var seen: Seen?
    private var timer: Timer?

    /// What a tick compares: the log's size and date, and whether the job runs.
    private struct Seen: Equatable, Sendable {
        var size: Int?
        var modified: Date?
        var running: Bool
        var tenMinutes: Int   // the next-run text and the 26-hour rule move with the clock
    }

    func start() {
        load()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in
            MainActor.assumeIsolated { StatusModel.shared.tick() }
        }
        timer?.tolerance = 1
    }

    /// Reads everything now, on the calling thread (snapshot mode).
    func load() {
        summary = Job.summary()
    }

    func tick() {
        Task {
            let now = await Task.detached { Self.look() }.value
            guard now != seen else { return }
            seen = now
            if now.running { starting = false }
            summary = await Task.detached { Job.summary() }.value
        }
    }

    func runNow() {
        starting = true
        Task {
            let started = await Task.detached { Job.kickstart() }.value
            if !started { starting = false }
            try? await Task.sleep(for: .seconds(1))
            tick()
            // launchd says "running" within a second; never leave the button stuck.
            try? await Task.sleep(for: .seconds(10))
            starting = false
        }
    }

    func openLog() {
        NSWorkspace.shared.open(Job.logURL)
    }

    nonisolated private static func look() -> Seen {
        let attributes = try? FileManager.default.attributesOfItem(atPath: Job.logURL.path)
        return Seen(size: (attributes?[.size] as? NSNumber)?.intValue,
                    modified: attributes?[.modificationDate] as? Date,
                    running: Job.isRunning(),
                    tenMinutes: Int(Date.now.timeIntervalSince1970 / 600))
    }
}
