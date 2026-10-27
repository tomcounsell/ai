import AppKit
import SwiftUI

/// Screenshot mode for agents, on the real log (read only):
///   APP_SNAPSHOT=/tmp/BrewStatus-shots ~/Applications/BrewStatus.app/Contents/MacOS/BrewStatus
/// writes popover-light.png and popover-dark.png, each with a -layout.json of
/// the grid probes (tools/check-layout.swift), then quits. It draws the view
/// itself, so it needs no Screen Recording permission and works where
/// screencapture returns black.
enum Snapshot {
    static var requested: Bool { !(ProcessInfo.processInfo.environment["APP_SNAPSHOT"] ?? "").isEmpty }

    static func run(model: StatusModel) {
        let dir = ProcessInfo.processInfo.environment["APP_SNAPSHOT"]!
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        model.load()
        Task {
            for mode in ["light", "dark"] {
                let window = NSWindow(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
                window.appearance = NSAppearance(named: mode == "light" ? .aqua : .darkAqua)
                let host = NSHostingView(rootView: PopoverView()
                    .environment(model)
                    .background(.background))
                window.contentView = host
                window.setContentSize(host.fittingSize)
                window.setFrameOrigin(NSPoint(x: -10_000, y: -10_000))
                window.orderFrontRegardless()
                try? await Task.sleep(for: .seconds(1))
                window.setContentSize(host.fittingSize)
                try? await Task.sleep(for: .seconds(0.5))
                let path = "\(dir)/popover-\(mode).png"
                GridProbes.shared.write(to: path.replacingOccurrences(of: ".png", with: "-layout.json"),
                                        window: host.bounds.size)
                if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                    host.cacheDisplay(in: host.bounds, to: rep)
                    try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
                    print("SNAPSHOT \(path)")
                }
                window.orderOut(nil)
            }
            print("SNAPSHOT done \(dir)")
            NSApp.terminate(nil)
        }
    }
}
