import Darwin
import Foundation
import Testing
@testable import MonitorModel

/// Includes the work moved out of getters; report separately from the unchanged
/// P0 getter workload so a cache hit cannot hide graph construction costs.
@Test(.enabled(if: ProcessInfo.processInfo.environment["CAM_PROJECTION_BENCHMARK"] == "1"))
func syntheticProjectionConstructionAndMemoryBenchmark() throws {
    var samples: [[String: Any]] = []
    for count in [10, 100, 300, 3000] {
        let threads: [[String: Any]] = (0..<count).map { n in
            var value: [String: Any] = ["id": "thread-\(n)", "home": "/synthetic", "visible": true,
                "has_active_work": true, "thread_source": n % 3 == 0 ? "user" : "subagent"]
            if n % 3 != 0 { value["parent_thread_id"] = "thread-\(n-n%3)" }
            return value
        }
        let data = try JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": threads])
        var times: [Double] = []
        var cost: SnapshotProjection.Cost?
        for _ in 0..<5 {
            let started = ContinuousClock.now
            let snapshot = try Snapshot.decode(data)
            let duration = started.duration(to: .now).components
            times.append(Double(duration.seconds)*1000+Double(duration.attoseconds)/1e15)
            cost = snapshot.projection.cost
            #expect(snapshot.displayedChats.count == count)
        }
        let measured = try #require(cost)
        var usage = rusage()
        #expect(getrusage(RUSAGE_SELF, &usage) == 0)
        samples.append(["chats": count, "decode_and_projection_ms": times,
            "process_peak_rss_bytes": Int(usage.ru_maxrss), "snapshot_bytes": data.count,
            "nodes": measured.nodes, "edges": measured.edges, "parent_candidates": measured.parentCandidates,
            "cycle_steps": measured.cycleSteps, "ancestor_steps": measured.ancestorSteps])
    }
    let data = try JSONSerialization.data(withJSONObject: ["build": "swift-test-debug", "samples": samples], options: [.sortedKeys])
    print("CAM_PROJECTION_CONSTRUCTION " + String(decoding: data, as: UTF8.self))
}
