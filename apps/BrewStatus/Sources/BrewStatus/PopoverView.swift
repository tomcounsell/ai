import AppKit
import BrewStatusKit
import SwiftUI

/// The one screen: last night's run, the next run, and the job's actions.
struct PopoverView: View {
    @Environment(StatusModel.self) private var model

    var body: some View {
        let summary = model.summary
        VStack(alignment: .leading, spacing: 0) {
            header(summary)
                .padding(Space.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
                .gridArea("header", inset: Space.lg)
            Divider()
            if let run = summary.run {
                ScrollView {
                    RunDetails(run: run)
                        .padding(Space.lg)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .gridArea("details", inset: Space.lg)
                }
                .fitsContent(max: 520)
            } else {
                ContentUnavailableView("No Runs in the Log Yet", systemImage: "mug",
                                       description: Text(Job.logURL.path))
                    .padding(Space.lg)
            }
            Divider()
            Text(Summary.rule)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .gridProbe("rule", area: "rule")
                .padding(.horizontal, Space.lg)
                .padding(.vertical, Space.md)
                .frame(maxWidth: .infinity, alignment: .leading)
                .gridArea("rule", inset: Space.md)
            Divider()
            actions(summary)
                .padding(Space.md)
        }
        .frame(width: 440)
        .noInitialFocus()
        .overlay { if GridOverlay.enabled { GridOverlay() } }
    }

    private func header(_ summary: Summary) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.md) {
            StatusDot(status: summary.status)
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("Brew Nightly: \(summary.status.title)")
                    .font(.headline)
                if let run = summary.run {
                    Text(Format.span(run))
                        .foregroundStyle(.secondary)
                    Text(Format.counts(run))
                }
                // Amber's reasons are the counts above; red's say what went wrong.
                if summary.status == .red {
                    ForEach(summary.reasons, id: \.self) { reason in
                        Text(reason)
                    }
                }
                Text("Next run: \(summary.nextRun.map { Format.day($0, now: .now) } ?? "not scheduled")")
                    .foregroundStyle(.secondary)
            }
            .gridProbe("header", area: "header", edge: nil)
        }
    }

    private func actions(_ summary: Summary) -> some View {
        HStack(spacing: Space.sm) {
            if summary.running || model.starting {
                Button {} label: {
                    HStack(spacing: Space.sm) {
                        ProgressView().controlSize(.small)
                        Text("Running…")
                    }
                }
                .disabled(true)
            } else {
                Button("Run Now") { model.runNow() }
                    .help("Start the nightly job now, as at its scheduled time")
            }
            Spacer()
            Button("Open Log") { model.openLog() }
                .help("Open \(Job.logURL.lastPathComponent) in Console")
            Button("Quit") { NSApp.terminate(nil) }
                .keyboardShortcut("q")
        }
    }
}

/// The run's sections; each shows only when it has rows.
private struct RunDetails: View {
    let run: Run

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if !run.upgrades.isEmpty {
                section("Upgraded", run.upgrades.count) {
                    ForEach(Array(run.upgrades.enumerated()), id: \.offset) { _, upgrade in
                        UpgradeRow(upgrade: upgrade)
                    }
                }
            }
            lines("Failed", run.failures, symbol: "xmark.circle.fill", color: StatusDot.color(.red), lineLimit: 2)
            lines("Skipped", run.issueSkips.map { "\($0.name): \($0.reason)" },
                  symbol: "forward.fill", color: StatusDot.color(.amber), lineLimit: 2)
            if !run.frozen.isEmpty {
                section("Frozen", run.frozen.count) {
                    Text(run.frozen.joined(separator: ", "))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .help(run.frozen.joined(separator: ", "))
                }
            }
            lines("Timeouts", run.timeouts, symbol: "clock.badge.exclamationmark", color: StatusDot.color(.amber))
            lines("Errors", Format.grouped(run.errors), symbol: "exclamationmark.octagon", color: StatusDot.color(.amber))
            lines("Warnings", Format.grouped(run.warnings), symbol: "exclamationmark.triangle", color: StatusDot.color(.amber))
        }
    }

    @ViewBuilder
    private func lines(_ title: String, _ items: [String], symbol: String, color: Color, lineLimit: Int = 1) -> some View {
        if !items.isEmpty {
            section(title, items.count) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    Label {
                        Text(item.replacingOccurrences(of: "Warning: ", with: "").replacingOccurrences(of: "Error: ", with: ""))
                            .lineLimit(lineLimit)
                            .help(item)
                    } icon: {
                        Image(systemName: symbol).foregroundStyle(color)
                    }
                }
            }
        }
    }

    private func section(_ title: String, _ count: Int, @ViewBuilder rows: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text("\(title) (\(count))")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
            rows()
        }
        .gridProbe(title, area: "details")
    }
}

private struct UpgradeRow: View {
    let upgrade: Upgrade

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
            Text(upgrade.name)
            Text(upgrade.kind.rawValue)
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer(minLength: Space.sm)
            if let from = upgrade.from {
                // Casks carry a build hash after a comma; the full text is the help.
                Text("\(short(from)) → \(short(upgrade.to ?? "?"))")
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .help("\(from) -> \(upgrade.to ?? "?")")
            }
        }
    }

    private func short(_ version: String) -> String {
        String(version.split(separator: ",").first ?? Substring(version))
    }
}

/// The status colour, in one place: the menu bar image, the header dot, the row symbols.
struct StatusDot: View {
    let status: Status

    var body: some View {
        Circle()
            .fill(Self.color(status))
            .frame(width: Space.md, height: Space.md)
            .accessibilityHidden(true)
    }

    static func nsColor(_ status: Status) -> NSColor {
        switch status {
        case .green: .systemGreen
        case .amber: .systemOrange
        case .red: .systemRed
        }
    }

    static func color(_ status: Status) -> Color { Color(nsColor: nsColor(status)) }

    /// A coloured, non-template image, so the menu bar keeps its colour.
    static func menuBarImage(_ status: Status) -> NSImage {
        let image = NSImage(size: NSSize(width: 16, height: 16), flipped: false) { rect in
            nsColor(status).setFill()
            NSBezierPath(ovalIn: rect.insetBy(dx: 3, dy: 3)).fill()
            return true
        }
        image.isTemplate = false
        return image
    }
}

extension Status {
    var title: String { rawValue.capitalized }
}
