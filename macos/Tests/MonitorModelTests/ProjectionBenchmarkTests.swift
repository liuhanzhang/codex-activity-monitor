import Foundation
import Testing
@testable import MonitorModel

/// Opt-in synthetic baseline, kept unchanged when comparing EH06 projection.
@Test(.enabled(if: ProcessInfo.processInfo.environment["CAM_PROJECTION_BENCHMARK"] == "1"))
func syntheticSnapshotProjectionBenchmark() throws {
    var samples: [[String: Any]] = []
    var checksum = 0
    for count in [10, 100, 300] {
        let chats: [[String: Any]] = (0..<count).map { index in
            var value: [String: Any] = ["id": "thread-\(index)", "home": "/synthetic", "visible": true,
                "status": "inProgress", "has_active_work": true,
                "thread_source": index % 3 == 0 ? "user" : "subagent"]
            if index % 3 != 0 { value["parent_thread_id"] = "thread-\(index-index%3)" }
            return value
        }
        let data = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": chats])
        let snapshot = try Snapshot.decode(data)
        let repeats = count == 10 ? 10 : 3
        var milliseconds: [Double] = []
        for _ in 0..<repeats {
            let started = ContinuousClock.now
            let roots = snapshot.rootChats
            checksum += roots.count + snapshot.displayedChats.count + snapshot.unassignedChats.count
            for root in roots.prefix(3) {
                checksum += snapshot.children(of: root).count
                checksum += snapshot.hasActiveWork(in: root) ? 1 : 0
            }
            let elapsed = started.duration(to: .now).components
            milliseconds.append(Double(elapsed.seconds)*1000 + Double(elapsed.attoseconds)/1e15)
        }
        samples.append(["chats": count, "repetitions": repeats,
                        "getter_batch_ms": milliseconds, "snapshot_bytes": data.count])
    }
    #expect(checksum > 0)
    let encoded = try JSONSerialization.data(withJSONObject: ["fixture": "three-member-groups",
        "build": "swift-test-debug", "samples": samples, "checksum": checksum], options: [.sortedKeys])
    print("CAM_PROJECTION_BENCHMARK " + String(decoding: encoded, as: UTF8.self))
}
