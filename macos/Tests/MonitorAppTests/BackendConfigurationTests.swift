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
    #expect(first.data_root == home.path + "/Library/Application Support/CodexActivityMonitor")
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
    #expect(request.arguments == ["-m", "codex_activity_monitor.cli", "app", "status"])
    #expect(request.environment["PYTHONPATH"] == "/source/src")
    #expect(request.environment["TIKTOKEN_CACHE_DIR"] == "/source/.local/tokenizer-cache")
}

// Build acceptance: run bin/build-gui first, then MONITOR_TOKENIZER_CACHE_TESTS=1.
@Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_TOKENIZER_CACHE_TESTS"] == "1"))
func sourceTokenizerWorksWithEmptyTemporaryCacheAndNoNetwork() async throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporary) }
    let config = BackendConfiguration(python: root.appendingPathComponent(".local/venv/bin/python").path,
        source_root: root.path, runtime: temporary.path, data_root: temporary.path, build_id: "fixture")
    var request = config.request(arguments: [])
    request.arguments = ["-c", """
        from codex_activity_monitor.tokenizer_process import TokenizerProcess
        worker = TokenizerProcess(timeout=3)
        try:
            for encoding in ('o200k_base', 'cl100k_base'):
                assert worker.count('hello world', encoding)[0] == 2
            assert worker.diagnostics()['timeouts'] == 0
        finally:
            worker.close()
        """]
    request.environment["TMPDIR"] = temporary.path
    request.environment["DATA_GYM_CACHE_DIR"] = temporary.path
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"] {
        request.environment[key] = "http://127.0.0.1:1"
    }
    request.environment["NO_PROXY"] = ""
    request.environment["no_proxy"] = ""
    let result = try await ProcessExecutor().execute(request)
    #expect(result.exitCode == 0, "\(String(decoding: result.stderr, as: UTF8.self))")
    #expect(try FileManager.default.contentsOfDirectory(atPath: temporary.path).isEmpty)
}
