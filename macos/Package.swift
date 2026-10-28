// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CodexActivityMonitor",
    defaultLocalization: "zh-Hans",
    platforms: [.macOS("14.0")],
    products: [.executable(name: "MonitorApp", targets: ["MonitorApp"])],
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")],
    targets: [
        .target(name: "MonitorModel", resources: [.process("Resources")]),
        .executableTarget(name: "MonitorApp", dependencies: ["MonitorModel", .product(name: "Sparkle", package: "Sparkle")],
                          resources: [.process("Resources")],
                          linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]),
        .testTarget(name: "MonitorModelTests", dependencies: ["MonitorModel"], resources: [.process("Fixtures")]),
        .testTarget(name: "MonitorAppTests", dependencies: ["MonitorApp"])
    ]
)
