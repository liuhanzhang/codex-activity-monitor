import Foundation
import Testing
@testable import MonitorApp

@Test func bundledConfigurationResolvesAfterRelocation() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let app = directory.appendingPathComponent("中文 App with spaces.app")
    let resources = app.appendingPathComponent("Contents/Resources")
    let backend = app.appendingPathComponent("Contents/Helpers/Backend/monitor-backend")
    try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: backend.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data("#!/bin/sh\nexit 0\n".utf8).write(to: backend)
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: backend.path)
    try Data("{\"mode\":\"bundled\",\"backend\":\"Contents/Helpers/Backend/monitor-backend\",\"build_id\":\"fixture\"}".utf8)
        .write(to: resources.appendingPathComponent("backend.json"))
    let home = directory.appendingPathComponent("another-user")
    let first = try BackendConfiguration.load(bundleURL: app, userHome: home)
    #expect(first.python == backend.path)
    #expect(first.data_root == home.path + "/Library/Application Support/CodexProgressMonitor")
    #expect(first.runtime == first.data_root + "/run")
    let moved = directory.appendingPathComponent("Moved.app")
    try FileManager.default.moveItem(at: app, to: moved)
    let second = try BackendConfiguration.load(bundleURL: moved, userHome: home)
    #expect(second.python == moved.path + "/Contents/Helpers/Backend/monitor-backend")
    let request = second.request(arguments: ["app", "status", "--data-root", first.data_root])
    #expect(request.arguments == ["app", "status", "--data-root", first.data_root])
    #expect(request.directory == moved.path)
    #expect(request.environment["PYTHONPATH"] == nil)
    #expect(request.environment["PYTHONHOME"] == nil)
}

@Test func bundledConfigurationRejectsEscapingExecutable() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let app = directory.appendingPathComponent("App.app")
    let resources = app.appendingPathComponent("Contents/Resources")
    try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
    for path in ["../../../bin/sh", "/bin/sh", "absent"] {
        let manifest = ["mode": "bundled", "backend": path, "build_id": "test"]
        try JSONSerialization.data(withJSONObject: manifest).write(to: resources.appendingPathComponent("backend.json"))
        #expect(throws: (any Error).self) { try BackendConfiguration.load(bundleURL: app) }
    }
}

@Test func sourceConfigurationRetainsDeveloperEntry() throws {
    let data = Data("{\"python\":\"/python\",\"source_root\":\"/source\",\"runtime\":\"/run\",\"data_root\":\"/data\",\"build_id\":\"test\"}".utf8)
    let config = try JSONDecoder().decode(BackendConfiguration.self, from: data)
    let request = config.request(arguments: ["app", "status"])
    #expect(request.arguments == ["-m", "codex_progress_monitor.cli", "app", "status"])
    #expect(request.environment["PYTHONPATH"] == "/source/src")
}
