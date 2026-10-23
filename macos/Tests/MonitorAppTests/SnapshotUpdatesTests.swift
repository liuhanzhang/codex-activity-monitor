import Foundation
import Testing
@testable import MonitorApp
@testable import MonitorModel

private func snapshotData(active: Bool) throws -> Data {
    try JSONSerialization.data(withJSONObject: [
        "schema_version": 1, "running": true, "updated_at": Date().timeIntervalSince1970,
        "overview": ["version": 1, "coverage": "complete", "generation_rate": 0],
        "menu_presentation": ["version": 1, "mode": active ? "thinking" : "speed",
                              "reason": active ? "reasoning" : "idle",
                              "active_conversation_count": active ? 1 : 0],
        "backends": ["b": ["connected": true]],
        "threads": [["id": "t", "status": active ? "inProgress" : "completed", "has_active_work": active]]
    ])
}

@MainActor private func waitForMode(_ mode: MenuMode, in store: DashboardStore, seconds: Double) async -> Bool {
    let deadline = ContinuousClock.now.advanced(by: .seconds(seconds))
    while ContinuousClock.now < deadline {
        if store.menuMode == mode { return true }
        try? await Task.sleep(for: .milliseconds(10))
    }
    return store.menuMode == mode
}

@Test @MainActor func menuFollowsAtomicSnapshotReplacementWithoutWaitingForPolling() async throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let file = directory.appendingPathComponent("snapshot.json")
    try snapshotData(active: true).write(to: file, options: .atomic)
    let store = DashboardStore(configuration: BackendConfiguration(
        python: "/unused", source_root: "/unused", runtime: directory.path,
        data_root: "/unused", build_id: "test"), arguments: ["--read-only"])
    store.start()
    let started = await waitForMode(.thinking, in: store, seconds: 2)
    #expect(started)
    try snapshotData(active: false).write(to: file, options: .atomic)
    let stopped = await waitForMode(.idle, in: store, seconds: 0.7)
    #expect(stopped)
    // A watcher attached to the old file inode would miss this replacement.
    try snapshotData(active: true).write(to: file, options: .atomic)
    let resumed = await waitForMode(.thinking, in: store, seconds: 0.7)
    #expect(resumed)
    #expect(await store.prepareQuit())
}
