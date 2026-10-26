import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

@Test(.enabled(if: ProcessInfo.processInfo.environment["CAM_PROJECTION_BENCHMARK"] == "1"))
@MainActor func syntheticDashboardProjectionOnMainActorBenchmark() async throws {
    let store = DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused",
        runtime: "/unused", data_root: "/unused", build_id: "benchmark"), arguments: ["--read-only"])
    var samples: [[String: Any]] = []
    var checksum = 0
    for count in [10, 100, 300, 3000] {
        let threads: [[String: Any]] = (0..<count).map { n in
            var value: [String: Any] = ["id": "thread-\(n)", "home": "/synthetic", "connected": true,
                "has_active_work": true, "thread_source": n % 3 == 0 ? "user" : "subagent"]
            if n % 3 != 0 { value["parent_thread_id"] = "thread-\(n-n%3)" }
            return value
        }
        let data = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": threads,
            "running": true, "updated_at": Date().timeIntervalSince1970])
        var times: [Double] = []
        for _ in 0..<5 {
            // Production decoding/projection is also on the read worker.
            let snapshot = try await Task.detached { try Snapshot.decode(data) }.value
            let started = ContinuousClock.now
            store.snapshot = snapshot; store.updatePresentation()
            store.panelVisible = true
            let rows = store.activeRows
            checksum += rows.count + store.visibleChats.count + store.recentRows.count + store.unassignedRows.count
            for row in rows {
                checksum += store.sourceAvailable(for: row.chat) ? 1 : 0
                checksum += snapshot.hasActiveWork(in: row.chat) ? 1 : 0
                checksum += store.isUnassigned(row.chat) ? 1 : 0
            }
            let duration = started.duration(to: .now).components
            times.append(Double(duration.seconds)*1000+Double(duration.attoseconds)/1e15)
        }
        samples.append(["chats": count, "main_actor_presentation_ms": times])
    }
    #expect(checksum > 0)
    let data = try JSONSerialization.data(withJSONObject: ["samples": samples, "checksum": checksum,
        "scope": "Dashboard presentation model and row flags; excludes SwiftUI/AppKit drawing"], options: [.sortedKeys])
    print("CAM_PROJECTION_MAIN_ACTOR " + String(decoding: data, as: UTF8.self))
}
