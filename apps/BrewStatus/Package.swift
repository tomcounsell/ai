// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "BrewStatus",
    platforms: [.macOS(.v26)],
    targets: [
        // The log parser, the status rule and the job's launchd calls, with no SwiftUI.
        .target(name: "BrewStatusKit"),
        .executableTarget(
            name: "BrewStatus",
            dependencies: ["BrewStatusKit"],
            // App code runs on the main thread unless you say otherwise.
            swiftSettings: [.defaultIsolation(MainActor.self)]
        ),
        // The CLI, installed as ~/.local/bin/brew-status.
        .executableTarget(name: "brew-status", dependencies: ["BrewStatusKit"]),
        .testTarget(
            name: "BrewStatusKitTests",
            dependencies: ["BrewStatusKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
