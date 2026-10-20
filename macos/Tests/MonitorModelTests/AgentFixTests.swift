import Foundation
import Testing
@testable import MonitorModel

private func fixedSnapshot(_ threads: [[String: Any]]) throws -> Snapshot {
    try Snapshot.decode(JSONSerialization.data(withJSONObject: ["schema_version": 1, "threads": threads]))
}

@Test func replyingChildRemainsInsideCompletedConversation() throws {
    let value = try fixedSnapshot([
        ["id": "root", "home": "scope", "thread_source": "user", "status": "completed", "last_progress_seconds": 1,
         "subagents": ["child": "completed"]],
        ["id": "child", "home": "scope", "thread_source": "subagent", "status": "completed", "last_progress_seconds": 2,
         "parent_thread_id": "root", "parent_source": "session_header", "subagents": ["root": "interacted"]]
    ])
    #expect(value.rootChats.map(\.threadID) == ["root"])
    #expect(value.children(of: value.rootChats[0]).map(\.threadID) == ["child"])
}

@Test func completedLimitCountsGroupsAndRetainsParentContext() throws {
    var rows: [[String: Any]] = [["id": "root", "home": "scope", "status": "completed", "last_progress_seconds": 100]]
    for n in 0..<12 {
        rows.append(["id": "child-\(n)", "home": "scope", "thread_source": "subagent", "status": "completed",
                     "last_progress_seconds": n + 1, "parent_thread_id": "root"])
    }
    let value = try fixedSnapshot(rows)
    #expect(value.rootChats.map(\.threadID) == ["root"])
    #expect(value.children(of: value.rootChats[0]).count == 12)
}

@Test func globalWaitAndRawCoordinationKeepTheirModelTrack() throws {
    let raw: [String: Any] = ["id":"raw", "scope_id":"s", "thread_id":"root", "turn_id":"turn", "item_id":"raw",
        "namespace":"input", "item_type":"function_call", "track":"model", "kind":"tool_input", "label":"collaboration.wait_agent",
        "status":"completed", "coordination_action":"wait", "coordination_target_scope":"global", "related_agent_ids":[]]
    let phase = try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: raw))
    #expect(phase.isCoordination)
    #expect(phase.displayTrack == "model")
    #expect(phase.presentationLabel().contains("等待子智能体消息"))
    #expect(!phase.presentationLabel().contains("对象未知"))
}

@Test func knownOrphanHasAnEntryAndNeverCountsAsAMainConversation() throws {
    let value = try fixedSnapshot([
        ["id":"child", "home":"scope", "thread_source":"subagent", "parent_thread_id":"missing",
         "status":"completed", "last_progress_seconds":1],
        ["id":"root", "home":"other", "status":"completed", "last_progress_seconds":1]
    ])
    #expect(value.rootChats.map(\.threadID) == ["root"])
    #expect(value.unassignedChats.map(\.threadID) == ["child"])
    #expect(value.displayedChats.count == 2)
}

@Test func actualTenConversationGroupsRetainAllTheirChildren() throws {
    var rows: [[String: Any]] = []
    for n in 0..<12 {
        rows.append(["id":"root-\(n)", "home":"scope", "status":"completed", "last_progress_seconds":n + 1])
        rows.append(["id":"child-\(n)", "home":"scope", "status":"completed", "last_progress_seconds":n + 1,
                     "thread_source":"subagent", "parent_thread_id":"root-\(n)"])
    }
    let value = try fixedSnapshot(rows)
    #expect(value.rootChats.count == 10)
    #expect(value.displayedChats.count == 20)
    #expect(value.unassignedChats.isEmpty)
}
