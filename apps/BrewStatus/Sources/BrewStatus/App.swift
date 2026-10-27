import AppKit
import BrewStatusKit
import ServiceManagement
import SwiftUI

// BrewStatus
//   BrewStatus                      the menu bar dot for the nightly Homebrew job
//   APP_LOG=<file> BrewStatus       read another log (experiments)
//   APP_SNAPSHOT=<dir> BrewStatus   save popover-light.png and popover-dark.png, then quit (tools/snap.sh)
//   APP_GRID=1 BrewStatus           draw the 8 pt grid and the layout probes over the popover

@main
struct BrewStatusApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = StatusModel.shared

    var body: some Scene {
        // Snapshot runs beside the installed copy; they add no second dot.
        MenuBarExtra(isInserted: .constant(!Snapshot.requested)) {
            PopoverView()
                .environment(model)
        } label: {
            Image(nsImage: StatusDot.menuBarImage(model.summary.status))
                .accessibilityLabel("Brew Nightly: \(model.summary.status.title)")
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        if Snapshot.requested {
            Snapshot.run(model: StatusModel.shared)
            return
        }
        StatusModel.shared.start()
        LoginItem.registerOnce()
    }
}

/// Opens at login, registered on the first launch only, so turning it off in
/// System Settings > General > Login Items stays off.
enum LoginItem {
    static func registerOnce() {
        let key = "registeredLoginItem"
        guard !UserDefaults.standard.bool(forKey: key) else { return }
        do {
            try SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: key)
        } catch {
            NSLog("BrewStatus: could not add the login item: \(error)")
        }
        NSLog("BrewStatus: login item status \(SMAppService.mainApp.status.rawValue)")
    }
}
