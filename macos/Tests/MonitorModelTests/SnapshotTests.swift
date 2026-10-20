import Foundation
import Testing
@testable import MonitorModel

@Test func oldSnapshotNeverInventsTotal() throws {
    let json = """
    {"schema_version":1,"updated_at":100,"running":true,"threads":[]}
    """
    let value = try Snapshot.decode(Data(json.utf8))
    #expect(value.menuTitle(at: Date(timeIntervalSince1970: 101)) == "—")
}

@Test func staleAndPartialDoNotPresentSubtotalAsTotal() throws {
    for coverage in ["complete", "partial"] {
        let json = """
        {"schema_version":1,"updated_at":100,"running":true,"threads":[],
        "overview":{"version":1,"coverage":"\(coverage)","generation_rate":12,"observed_subtotal":12}}
        """
        let value = try Snapshot.decode(Data(json.utf8))
        #expect(value.menuTitle(at: Date(timeIntervalSince1970: 110)) == "—")
        #expect(value.menuTitle(at: Date(timeIntervalSince1970: 101)) == (coverage == "complete" ? "12 T/s" : "—"))
    }
}

@Test func identitiesAreScopedByHomeAndUnknownUsageStaysUnknown() throws {
    let json = """
    {"schema_version":1,"threads":[{"id":"same","home":"/a"},{"id":"same","home":"/b"}]}
    """
    let value = try Snapshot.decode(Data(json.utf8))
    #expect(value.threads[0].id != value.threads[1].id)
    #expect(value.threads[0].usage == nil)
    #expect(MetricFormat.count(nil) == "—")
}

@Test func unsupportedSchemaIsRejected() {
    #expect(throws: (any Error).self) { try Snapshot.decode(Data("{\"schema_version\":9,\"threads\":[]}".utf8)) }
}

private func hierarchy(_ rows: String) throws -> Snapshot {
    try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[\(rows)]}".utf8))
}

@Test func expansionUsesStableScopedParents() throws {
    let value = try hierarchy("""
    {"id":"parent","home":"h","has_active_work":true,"subagents":{"child":"agent"}},
    {"id":"child","home":"h","has_active_work":true,"parent_thread_id":"parent"},
    {"id":"child","home":"other","has_active_work":true}
    """)
    #expect(value.rootChats.count == 2)
    let rows = value.rows(expanded: [value.threads[0].id])
    #expect(rows.map(\.depth) == [0, 1, 0])
    #expect(Set(rows.map(\.id)).count == 3)
}

@Test func cyclicAndAmbiguousParentsRemainVisible() throws {
    let cycle = try hierarchy("""
    {"id":"a","has_active_work":true,"parent_thread_id":"b"},
    {"id":"b","has_active_work":true,"parent_thread_id":"a"}
    """)
    #expect(cycle.rootChats.isEmpty)
    #expect(cycle.unassignedChats.count == 2)
    #expect(cycle.displayedChats.count == 2)
    let ambiguous = try hierarchy("""
    {"id":"a","has_active_work":true,"subagents":{"c":"one"}},
    {"id":"b","has_active_work":true,"subagents":{"c":"two"}},
    {"id":"c","has_active_work":true}
    """)
    #expect(ambiguous.rootChats.count == 3)
}

@Test func recentLimitNeverEvictsActiveChats() throws {
    let recent = (0..<14).map { "{\"id\":\"r\($0)\",\"status\":\"completed\",\"last_progress_seconds\":\($0)}" }
    let value = try hierarchy((["{\"id\":\"active\",\"has_active_work\":true}", "{\"id\":\"old\",\"last_progress_seconds\":700}"] + recent).joined(separator: ","))
    #expect(value.displayedChats.count == 11)
    #expect(value.displayedChats.first?.threadID == "active")
    #expect(!value.displayedChats.contains { $0.threadID == "old" })
}

@Test func managementSeparatesRestoreAndCleanup() throws {
    for state in ["restoring", "pending_cleanup", "restored", "purged", "error"] {
        let result = try JSONDecoder().decode(ManagementState.self, from: Data("{\"version\":1,\"state\":\"\(state)\"}".utf8))
        #expect(!result.configured)
        #expect(result.waitingForCleanup == ["restoring", "pending_cleanup"].contains(state))
    }
}

@Test func oldBridgesDoNotClaimPauseCapability() throws {
    let old = try JSONDecoder().decode(Backend.self, from: Data("{\"connected\":true}".utf8))
    let current = try JSONDecoder().decode(Backend.self, from: Data("{\"connected\":true,\"implementation\":{\"modules\":{\"app_session\":\"hash\"}}}".utf8))
    #expect(!old.supportsObservationLease)
    #expect(current.supportsObservationLease)
}

@Test func metricsRejectInvalidAndPreserveUnknown() {
    #expect(MetricFormat.rate(.infinity) == "—")
    #expect(MetricFormat.rate(-1) == "—")
    #expect(MetricFormat.duration(.nan) == "—")
    #expect(MetricFormat.duration(0.3) == "")
    #expect(MetricFormat.count(-10) == "—")
}

@Test func unavailableTotalHasASpecificCauseAndConnectionHint() throws {
    let fixtures: [(String, ObservationNotice?)] = [
        ("", .totalUnavailable),
        (",\"overview\":{\"version\":1,\"coverage\":\"complete\"}", nil),
        (",\"overview\":{\"version\":1,\"coverage\":\"partial\",\"quality\":[\"observation_gap\"]}", .observationGap),
        (",\"overview\":{\"version\":1,\"coverage\":\"unavailable\",\"quality\":[\"multiple_active_backends\"]}", .identityConflict),
        (",\"overview\":{\"version\":1,\"coverage\":\"partial\",\"quality\":[\"unassociated_events\"]}", .associatingEvents),
        (",\"overview\":{\"version\":1,\"coverage\":\"partial\"}", .streamUnavailable)
    ]
    for (extra, expected) in fixtures {
        let data = "{\"schema_version\":1,\"threads\":[],\"backends\":{\"b\":{\"connected\":true}}\(extra)}"
        #expect(try Snapshot.decode(Data(data.utf8)).observationNotice == expected)
    }
    #expect(try hierarchy("").observationNotice == .waitingForConnection)
}
