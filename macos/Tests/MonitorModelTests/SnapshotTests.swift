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

@Test func exhaustedObservationKeepsAnActionableNoticeWithoutALiveBackend() throws {
    let value = try Snapshot.decode(Data("""
        {"schema_version":1,"threads":[],"overview":{"version":1,"coverage":"unavailable","quality":["identity_budget"]}}
        """.utf8))
    #expect(value.observationNotice == .resourceLimit)
    #expect(value.menuMode(connectionFresh: true) == .unavailable)
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

@Test func ephemeralThreadsStayOutOfActiveAndRecentChats() throws {
    let value = try hierarchy("""
    {"id":"active-helper","ephemeral":true,"thread_source":"composer_predictions","has_active_work":true},
    {"id":"01a1623b","ephemeral":true,"thread_source":"composer_predictions","status":"completed","last_progress_seconds":1},
    {"id":"01a16238","ephemeral":true,"thread_source":"composer_predictions","status":"completed","last_progress_seconds":2},
    {"id":"user","ephemeral":false,"thread_source":"user","has_active_work":true},
    {"id":"legacy","status":"completed","last_progress_seconds":3}
    """)
    #expect(value.threads.count == 5)
    #expect(value.rootChats.map(\.threadID) == ["user", "legacy"])
    #expect(value.displayedChats.map(\.threadID) == ["user", "legacy"])
    #expect(value.unassignedChats.isEmpty)
    #expect(value.activeConversationCount == 1)
}

@Test func ephemeralThreadsNeverReenterThroughParentGrouping() throws {
    let value = try hierarchy("""
    {"id":"root","ephemeral":false,"thread_source":"user","status":"completed","last_progress_seconds":700},
    {"id":"child","thread_source":"subagent","parent_thread_id":"root","has_active_work":true},
    {"id":"helper-child","ephemeral":true,"parent_thread_id":"root","has_active_work":true},
    {"id":"helper-parent","ephemeral":true,"has_active_work":true},
    {"id":"orphan","thread_source":"subagent","parent_thread_id":"helper-parent","has_active_work":true},
    {"id":"expired","status":"completed","last_progress_seconds":700},
    {"id":"expired-helper","ephemeral":true,"parent_thread_id":"expired","has_active_work":true}
    """)
    #expect(value.rootChats.map(\.threadID) == ["root"])
    #expect(value.children(of: value.threads[0]).map(\.threadID) == ["child"])
    #expect(value.unassignedChats.map(\.threadID) == ["orphan"])
    #expect(value.rows(expanded: [value.threads[0].id]).map { $0.chat.threadID } == ["root", "child"])
    #expect(Set(value.displayedChats.map(\.threadID)) == ["root", "child", "orphan"])
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
    let current = try JSONDecoder().decode(Backend.self, from: Data("{\"connected\":true,\"implementation\":{\"modules\":{\"observer_permit\":\"hash\"}}}".utf8))
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
        (",\"overview\":{\"version\":1,\"coverage\":\"unavailable\",\"quality\":[\"identity_budget\"]}", .resourceLimit),
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
