import Foundation
import Testing
@testable import MonitorModel

@Test func projectionMatchesTheCapturedBaselineAcrossScopedAndInvalidGraphs() throws {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    let fixture = try #require(JSONSerialization.jsonObject(with: Data(contentsOf:
        root.appendingPathComponent("tests/fixtures/projection-golden-v1.json"))) as? [String: Any])
    #expect(fixture["baseline"] as? String == "c3996dc")
    let cases = try #require(fixture["cases"] as? [[String: Any]])
    for (index, value) in cases.enumerated() {
        let snapshot = try Snapshot.decode(JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": value["threads"]!]))
        let expected = try #require(value["expected"] as? [String: Any])
        #expect(snapshot.rootChats.map(\.id) == expected["roots"] as? [String], "case \(index) roots")
        #expect(snapshot.displayedChats.map(\.id) == expected["members"] as? [String], "case \(index) members")
        #expect(snapshot.unassignedChats.map(\.id) == expected["unassigned"] as? [String], "case \(index) unassigned")
        #expect(snapshot.threads.filter { snapshot.hasActiveWork(in: $0) }.map(\.id) == expected["active"] as? [String], "case \(index) active")
        let children = Dictionary(uniqueKeysWithValues: snapshot.threads.map { ($0.id, snapshot.children(of: $0).map(\.id)) })
        #expect(children == expected["children"] as? [String: [String]], "case \(index) children")
        let rows = snapshot.rows(expanded: Set(snapshot.threads.map(\.id)))
        #expect(rows.map(\.id) == expected["row_ids"] as? [String], "case \(index) rows")
        #expect(rows.map(\.depth) == expected["row_depths"] as? [Int], "case \(index) depth")
    }
}

@Test func projectionHandlesADeepHiddenAncestorChainInOneTraversal() throws {
    let count = 5000
    let threads: [[String: Any]] = (0..<count).map { n in
        var row: [String: Any] = ["id": String(n), "home": "scope", "visible": n == count-1, "has_active_work": n == count-1]
        if n > 0 { row["parent_thread_id"] = String(n-1) }
        return row
    }
    let snapshot = try Snapshot.decode(JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": threads]))
    #expect(snapshot.rootChats.map(\.threadID) == ["0"])
    #expect(snapshot.displayedChats.count == count)
    let rows = snapshot.rows(expanded: Set(snapshot.threads.map(\.id)))
    #expect(rows.count == count && rows.last?.depth == count-1)
    #expect(snapshot.projection.cost.cycleSteps == count)
    #expect(snapshot.projection.cost.ancestorSteps == count-1)
    #expect(snapshot.projection.cost.parentCandidates == count-1)
}

@Test func snapshotCopiesShareAProjectionAndDiscardedStatesReleaseIt() throws {
    weak var released: SnapshotProjection?
    try autoreleasepool {
        let snapshot = try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[{\"id\":\"a\",\"has_active_work\":true}]}".utf8))
        let copied = snapshot
        released = snapshot.projection
        #expect(snapshot.projection === copied.projection)
        for _ in 0..<10 {
            _ = snapshot.rootChats; _ = copied.displayedChats; _ = copied.rows(expanded: [])
            #expect(snapshot.projection === copied.projection)
        }
    }
    #expect(released == nil)
}

@Test func duplicateScopedIdentityCannotCreateAnAmbiguousProjection() {
    #expect(throws: SnapshotError.self) {
        try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[{\"id\":\"a\"},{\"id\":\"a\"}]}".utf8))
    }
}

@Test func recentExpiryUsesASeparateClockWithoutRebuildingTheGraph() throws {
    let snapshot = try Snapshot.decode(Data("""
        {"schema_version":1,"threads":[{"id":"active","has_active_work":true},
        {"id":"recent","status":"completed","last_progress_seconds":599},
        {"id":"orphan","thread_source":"subagent","status":"unknown","last_progress_seconds":598}]}
        """.utf8))
    #expect(snapshot.projection.selection(elapsed: 0).roots.map(\.threadID) == ["active", "recent"])
    #expect(snapshot.projection.selection(elapsed: 1).roots.map(\.threadID) == ["active"])
    #expect(snapshot.projection.selection(elapsed: 1).unassigned.map(\.threadID) == ["orphan"])
    #expect(snapshot.projection.selection(elapsed: 2).unassigned.isEmpty)
    #expect(snapshot.projection.selection(elapsed: 1000).roots.map(\.threadID) == ["active"])
}
