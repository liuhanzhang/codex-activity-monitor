import Foundation
import AppKit
import Testing
import MonitorModel
@testable import MonitorApp

private func management(_ configuration: String, runtime: String = "awaiting_handshake", restart: String = "not_required") throws -> ManagementState {
    let object: [String: Any] = ["version": 1, "ok": true, "state": configuration == "disabled" ? "restored" : "configured",
        "integration_status": ["version": 1, "configuration": configuration, "operation": "idle", "runtime": runtime,
                               "restart": restart, "configuration_revision": "revision-example", "matching_bridges": 1]]
    return try JSONDecoder().decode(ManagementState.self, from: JSONSerialization.data(withJSONObject: object))
}

@MainActor private func dashboard() -> DashboardStore {
    DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused",
                                                       data_root: "/unused", build_id: "fixture"),
                   arguments: ["--test-profile", "/unused"])
}

@Test @MainActor func configurationReceiptAloneCannotProveRuntimeActivation() throws {
    let store = dashboard()
    store.management = try management("enabled")
    store.fresh = true
    #expect(store.integrationEnabled)
    #expect(store.integrationCanToggle)
    #expect(!store.integrationStatusText.contains("已生效"))
    let object: [String: Any] = ["schema_version": 1, "threads": [], "backends": ["b": ["connected": true,
            "configuration_revision": "revision-example", "configuration_matches": true]]]
    store.snapshot = try Snapshot.decode(JSONSerialization.data(withJSONObject: object))
    #expect(store.integrationStatusText.contains("已生效"))
    store.fresh = false
    #expect(!store.integrationStatusText.contains("已生效"))
}

@Test @MainActor func incompleteOrUnverifiedConfigurationDisablesOrdinaryToggle() throws {
    let store = dashboard()
    store.management = try management("partial")
    #expect(!store.integrationCanToggle)
    #expect(store.integrationStatusText.contains("未完成"))
    store.management = try management("enabled")
    store.managementCheckFailed = true
    #expect(!store.integrationCanToggle)
    #expect(store.integrationStatusText.contains("上次结果"))
}

@Test @MainActor func mixedConnectionsKeepActivationAndRestartVisibleTogether() throws {
    let store = dashboard()
    store.management = try management("enabled", restart: "required")
    store.fresh = true
    let object: [String: Any] = ["schema_version": 1, "threads": [], "backends": ["b": ["connected": true,
            "configuration_revision": "revision-example", "configuration_matches": true,
            "implementation": ["modules": ["observer_permit": "current"]]]]]
    store.snapshot = try Snapshot.decode(JSONSerialization.data(withJSONObject: object))
    #expect(store.integrationStatusText.contains("部分接入已生效"))
    #expect(store.integrationStatusText.contains("重开 Codex"))
    #expect(store.connectionDetail == store.integrationStatusText)
    #expect(store.diagnosticComponents.contains { $0.component == "integration" && $0.state == "pending" })
}

@Test @MainActor func pendingUpgradeRestartIsVisibleBeforeAnyBridgeConnects() throws {
    let store = dashboard()
    store.management = try management("enabled", runtime: "waiting_for_codex", restart: "required")
    #expect(!store.bridgeNeedsRestart)
    #expect(store.connectionDetail == store.integrationStatusText)
    #expect(store.connectionDetail?.contains("完全退出并重新打开 Codex") == true)
}

@Test @MainActor func restoredConfigurationAndPendingRestartRemainSeparate() throws {
    let store = dashboard()
    store.management = try management("disabled", runtime: "disabled", restart: "required")
    #expect(!store.integrationEnabled)
    #expect(store.management?.integration_status?.restart == "required")
    #expect(store.diagnosticComponents.first?.state == "disabled")
    store.managementCheckFailed = true
    #expect(store.diagnosticComponents.contains { $0.component == "management" && $0.state == "unknown" })
}

@Test func missingAndUnknownDiagnosticsDoNotInvalidateActivitySnapshots() throws {
    let original = Data("{\"schema_version\":1,\"threads\":[]}".utf8)
    #expect(try Snapshot.decode(original).diagnostics == nil)
    let future = Data("{\"schema_version\":1,\"threads\":[],\"diagnostics\":{\"version\":99,\"instance_id\":\"future\",\"components\":[]}}".utf8)
    #expect(try Snapshot.decode(future).diagnostics?.supportedComponents.isEmpty == true)
    let bad = Data("{\"schema_version\":1,\"threads\":[],\"diagnostics\":\"bad\"}".utf8)
    #expect(try Snapshot.decode(bad).threads.isEmpty)
}

@Test func diagnosticReaderHandlesPartialLinesFiltersAndAtomicReplacement() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("cam-log-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let path = directory.appendingPathComponent("collector.jsonl")
    let event = DiagnosticEvent(instance: "example", sequence: 1, code: "metadata_write_failed", level: "error", component: "metadata")
    var bytes = Data("bad line\n".utf8)
    bytes.append(try JSONEncoder().encode(event)); bytes.append(10); bytes.append(Data("{\"partial\":".utf8))
    try bytes.write(to: path)
    let client = DiagnosticLogClient()
    let first = await client.read(directory: directory.path, search: "", level: "error", source: "metadata", limit: 200)
    #expect(first.events.map(\.id) == [event.id])
    #expect(first.invalidLines == 1)
    #expect(first.limited)
    let filtered = await client.read(directory: directory.path, search: "not-found", level: "", source: "", limit: 200)
    #expect(filtered.events.isEmpty)
    let next = DiagnosticEvent(instance: "replacement", sequence: 1, code: "metadata_ready", level: "info", component: "metadata")
    var replacement = try JSONEncoder().encode(next); replacement.append(10)
    try replacement.write(to: path, options: .atomic)
    let refreshed = await client.read(directory: directory.path, search: "", level: "", source: "", limit: 200)
    #expect(refreshed.events.map(\.id) == [next.id])
}

@Test func unknownLogCodeIsVisibleWithoutPretendingToBeKnownCopy() {
    #expect(DiagnosticEvent.summary("future_code", .english) == "future_code")
    #expect(DiagnosticEvent.summary("metadata_write_failed", .english) == "Timeline metadata write failed")
}

@Test func diagnosticSearchKeepsTheByteBudgetAcrossMultipleRoleFiles() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("cam-log-budget-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let encoder = JSONEncoder()
    for role in ["gui", "management", "collector", "bridge"] {
        var frames = Data()
        for sequence in 1...150 {
            let event = DiagnosticEvent(instance: role, sequence: sequence, code: "budget_probe", level: "info",
                                        component: role, params: ["reason": .string(String(repeating: "x", count: 6000))])
            frames.append(try encoder.encode(event)); frames.append(10)
        }
        try frames.write(to: directory.appendingPathComponent(role + ".jsonl"))
    }
    let result = await DiagnosticLogClient().read(directory: directory.path, search: "budget_probe", level: "", source: "", limit: 1000)
    #expect(!result.events.isEmpty)
    #expect(result.limited)
    let bytes = try result.events.reduce(0) { try $0 + encoder.encode($1).count }
    #expect(bytes <= 2*1024*1024)
}

@Test func applicationLogIsBoundedAndNeverCreatesAnInstallation() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("cam-rec-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let foreign = DiagnosticRecorder(root: root.path)
    foreign.record("gui_started"); await foreign.finish()
    #expect(!FileManager.default.fileExists(atPath: root.path))
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try Data("codex-activity-monitor:1\n".utf8).write(to: root.appendingPathComponent(".monitor-owned"))
    let log = DiagnosticRecorder(root: root.path)
    log.record("state_disconnected", level: "warning", component: "connection")
    await log.finish()
    let result = await DiagnosticLogClient().read(directory: root.appendingPathComponent("diagnostics").path,
                                                 search: "", level: "", source: "", limit: 200)
    #expect(result.events.contains { $0.code == "state_disconnected" })
    #expect(log.failure == nil)
}

@Test func applicationRepeatsPreserveCountsAndFirstTime() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("cam-repeat-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    try Data("codex-activity-monitor:1\n".utf8).write(to: root.appendingPathComponent(".monitor-owned"))
    let log = DiagnosticRecorder(root: root.path)
    for _ in 0..<12 { log.record("management_failed", level: "error") }
    await log.finish()
    let batch = await DiagnosticLogClient().read(directory: root.appendingPathComponent("diagnostics").path,
                                                search: "", level: "error", source: "", limit: 200)
    #expect(batch.events.count == 2)
    #expect(batch.events.reduce(0) { $0 + ($1.count ?? 0) } == 12)
    #expect(batch.events.contains { $0.first_at != nil })
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
@MainActor func diagnosticCopyWritesTheSelectedJSONToAPrivatePasteboard() {
    let board = NSPasteboard(name: .init("monitor-tests-" + UUID().uuidString))
    defer { board.clearContents() }
    let event = DiagnosticEvent(instance: "synthetic", sequence: 1, code: "metadata_write_failed", level: "error", component: "metadata")
    #expect(copyDiagnosticEvent(event, to: board))
    #expect(board.string(forType: .string) == event.raw)
}

@Test @MainActor func settingsHaveStandardNativeTextEditingCommands() {
    let menu = makeEditMenu(language: .english)
    let text = NSTextView()
    for key in ["x", "c", "v", "a"] {
        let item = menu.items.first { $0.keyEquivalent == key }
        #expect(item?.keyEquivalentModifierMask == .command)
        #expect(item?.action.map { text.responds(to: $0) } == true)
    }
}

@Test func applicationLoggingRequiresSuccessfulWriteEvidenceToRecover() async throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("cam-log-recovery-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let log = DiagnosticRecorder(root: root.path)
    log.record("gui_started")
    var deadline = ContinuousClock.now.advanced(by: .seconds(1))
    while log.failure == nil, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    #expect(log.failure != nil)
    #expect(!FileManager.default.fileExists(atPath: root.path))
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try Data("codex-activity-monitor:1\n".utf8).write(to: root.appendingPathComponent(".monitor-owned"))
    deadline = ContinuousClock.now.advanced(by: .seconds(2))
    while log.failure != nil, ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(20)) }
    #expect(log.failure == nil)
    await log.finish()
    let batch = await DiagnosticLogClient().read(directory: root.appendingPathComponent("diagnostics").path,
                                                 search: "", level: "", source: "gui_logging", limit: 200)
    #expect(batch.events.contains { $0.code == "logging_ready" })
}
