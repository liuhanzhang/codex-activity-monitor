// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CodexActivityMonitor",
    defaultLocalization: "zh-Hans",
    platforms: [.macOS("26.0")],
    products: [.executable(name: "MonitorApp", targets: ["MonitorApp"])],
    targets: [
        .target(name: "MonitorModel", resources: [.process("Resources")]),
        .executableTarget(name: "MonitorApp", dependencies: ["MonitorModel"], resources: [.process("Resources")]),
        .testTarget(name: "MonitorModelTests", dependencies: ["MonitorModel"], resources: [.process("Fixtures")]),
        .testTarget(name: "MonitorAppTests", dependencies: ["MonitorApp"])
    ]
)
