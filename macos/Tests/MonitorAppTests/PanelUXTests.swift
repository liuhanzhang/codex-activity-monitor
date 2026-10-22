import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

@MainActor private func panelStore(_ rows: [[String: Any]]) throws -> DashboardStore {
    let store = DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused",
        runtime: "/unused", data_root: "/unused", build_id: "test"), arguments: ["--read-only"])
    try updatePanel(store, rows)
    return store
}

@MainActor private func updatePanel(_ store: DashboardStore, _ rows: [[String: Any]]) throws {
    store.snapshot = try Snapshot.decode(JSONSerialization.data(withJSONObject: [
        "schema_version": 1, "updated_at": Date().timeIntervalSince1970, "running": true, "threads": rows]))
    store.updatePresentation()
}

@Test @MainActor func panelSelectsOneWorkingRootAcrossScopesAndChildWork() throws {
    let store = try panelStore([
        ["id": "same", "home": "a", "has_active_work": false],
        ["id": "child", "home": "a", "parent_thread_id": "same", "has_active_work": true],
        ["id": "same", "home": "b", "has_active_work": true],
        ["id": "done", "home": "a", "status": "completed", "last_progress_seconds": 1]
    ])
    store.panelVisible = true
    #expect(store.activeRows.count == 2)
    #expect(store.selectedChatKey == store.activeRows[0].chat.id)
    #expect(store.visibleChats.map(\.id) == store.activeRows.map { $0.chat.id })
    #expect(!store.showRecent && store.recentRows.isEmpty)
    #expect(store.activeRows.map { $0.chat.scope } == ["a", "b"])
}

@Test @MainActor func selectionAndStableOrderSurviveRefreshAndNewWork() throws {
    let a: [String: Any] = ["id": "a", "has_active_work": true]
    let b: [String: Any] = ["id": "b", "has_active_work": true]
    let done: [String: Any] = ["id": "done", "status": "failed", "last_progress_seconds": 1]
    let store = try panelStore([a, b, done])
    store.panelVisible = true
    store.selectChat(store.snapshot!.threads[1])
    let key = store.selectedChatKey
    store.showRecent = true
    try updatePanel(store, [b, ["id": "c", "has_active_work": true], a, done])
    #expect(store.activeRows.map { $0.chat.threadID } == ["a", "b", "c"])
    #expect(store.selectedChatKey == key)
    #expect(store.visibleChats.map(\.threadID) == ["a", "b", "c", "done"])
    #expect(store.visibleChats.last?.status == "failed")
}

@Test @MainActor func reopeningRestoresLastActiveSelectionAndPerChatReadingState() throws {
    let store = try panelStore([["id": "a", "has_active_work": true], ["id": "b", "has_active_work": true],
        ["id": "done", "status": "completed", "last_progress_seconds": 1]])
    store.panelVisible = true
    let chat = store.snapshot!.threads[1], timeline = store.timeline(for: chat)
    timeline.zoom = .overview; timeline.scrollOffset = 120; timeline.followsLatest = false
    store.selectChat(chat); store.showRecent = true
    store.selectChat(store.recentRoots[0])
    let presentation = store.presentationID
    store.panelVisible = true
    #expect(store.presentationID == presentation && store.selectedChat?.threadID == "done")
    store.panelVisible = false; store.panelVisible = true
    #expect(store.presentationID == presentation + 1)
    #expect(store.selectedChatKey == chat.id && !store.showRecent)
    #expect(store.timeline(for: chat) === timeline && timeline.zoom == .overview && timeline.scrollOffset == 120)
}

@Test @MainActor func finishingOrLosingSourceRetainsReadingUntilExplicitSelection() throws {
    let store = try panelStore([["id": "a", "has_active_work": true], ["id": "b", "has_active_work": true]])
    store.panelVisible = true
    let key = store.selectedChatKey
    try updatePanel(store, [["id": "b", "has_active_work": true],
        ["id": "a", "status": "completed", "last_progress_seconds": 1]])
    #expect(store.selectedChatKey == key && store.selectedChat?.status == "completed")
    #expect(store.visibleChats.map(\.threadID) == ["b", "a"])
    store.showRecent = true
    #expect(store.visibleChats.map(\.threadID) == ["b", "a"]) // Retained + recent is deduplicated.
    try updatePanel(store, [["id": "b", "has_active_work": true]])
    #expect(store.selectedChatKey == key && !store.sourceAvailable(for: store.selectedChat!))
    store.showRecent = false
    #expect(store.selectedChat?.threadID == "b" && store.visibleChats.count == 1)
}

@Test @MainActor func emptyPanelSelectsArrivingWorkAndRecentCollapseReturnsToEmpty() throws {
    let store = try panelStore([])
    store.panelVisible = true
    #expect(store.selectedChat == nil && store.visibleChats.isEmpty)
    try updatePanel(store, [["id": "done", "status": "completed", "last_progress_seconds": 1]])
    #expect(store.selectedChat == nil)
    store.showRecent = true
    #expect(store.selectedChat?.threadID == "done")
    store.showRecent = false
    #expect(store.selectedChat == nil && store.visibleChats.isEmpty)
    try updatePanel(store, [["id": "new", "has_active_work": true]])
    #expect(store.selectedChat?.threadID == "new")
}

@Test @MainActor func lateEphemeralMetadataClearsRetainedSelection() throws {
    let helper: [String: Any] = ["id": "helper", "has_active_work": true]
    let user: [String: Any] = ["id": "user", "ephemeral": false, "has_active_work": true]
    let store = try panelStore([helper, user])
    store.panelVisible = true
    #expect(store.selectedChat?.threadID == "helper")
    try updatePanel(store, [
        ["id": "helper", "ephemeral": true, "thread_source": "composer_predictions",
         "status": "completed", "last_progress_seconds": 1], user
    ])
    store.showRecent = true
    #expect(store.selectedChat?.threadID == "user")
    #expect(store.visibleChats.map(\.threadID) == ["user"])
    #expect(store.recentRoots.isEmpty)
    try updatePanel(store, [user])
    #expect(store.selectedChat?.threadID == "user")
}

@Test func listFitsContentThroughFiveRowsAndSharesSmallScreenBudget() {
    for count in [1, 3, 5, 6, 12] {
        let budget = PanelLayoutBudget(maximum: 720, chrome: 90, chatCount: count, hasSelection: true)
        #expect(budget.listHeight == CGFloat(min(count, 5)) * PanelLayoutBudget.rowHeight)
        #expect(budget.listHeight + budget.detailHeight <= 630)
    }
    let small = PanelLayoutBudget(maximum: 340, chrome: 90, chatCount: 12, hasSelection: true)
    #expect(small.listHeight < 5 * PanelLayoutBudget.rowHeight)
    #expect(small.listHeight + small.detailHeight == 250)
    let empty = PanelLayoutBudget(maximum: 720, chrome: 44, chatCount: 0, hasSelection: false)
    #expect(empty.listHeight == 48 && empty.detailHeight == 0)
}

@Test @MainActor func previewHeightOverrideIsReadOnlyAndRejectsInvalidSizes() {
    let configuration = BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused", data_root: "/unused", build_id: "test")
    #expect(DashboardStore(configuration: configuration, arguments: ["--read-only", "--preview-max-height", "420"]).previewMaxHeight == 420)
    #expect(DashboardStore(configuration: configuration, arguments: ["--preview-max-height", "420"]).previewMaxHeight == nil)
    for value in ["nan", "inf", "-1", "80", "bad"] {
        #expect(DashboardStore(configuration: configuration, arguments: ["--read-only", "--preview-max-height", value]).previewMaxHeight == nil)
    }
}

private func panelUsage(_ fields: [String: Any]) throws -> Usage {
    try JSONDecoder().decode(Usage.self, from: JSONSerialization.data(withJSONObject: fields))
}

@Test @MainActor func orphanDisclosurePreservesHistoryWithoutAddingARecentRoot() throws {
    let rows: [[String: Any]] = [
        ["id":"root", "home":"scope", "status":"completed", "last_progress_seconds":2],
        ["id":"child", "home":"scope", "thread_source":"subagent", "status":"unknown", "last_progress_seconds":1]
    ]
    let store = try panelStore(rows)
    store.showRecent = true; store.showUnassigned = true
    #expect(store.recentRows.map { $0.chat.threadID } == ["root"])
    #expect(store.unassignedRows.map { $0.chat.threadID } == ["child"])
    let timeline = store.timeline(for: store.snapshot!.threads[1])
    timeline.expandedAgents = ["grandchild"]
    var associated = rows
    associated[1]["parent_thread_id"] = "root"
    try updatePanel(store, associated)
    #expect(store.unassignedRows.isEmpty && store.recentRows.count == 1)
    #expect(store.timeline(for: store.snapshot!.threads[1]) === timeline)
    #expect(timeline.expandedAgents == ["grandchild"])
}

@Test @MainActor func hidingRecentDoesNotHideASelectedUnassignedTask() throws {
    let store = try panelStore([
        ["id": "root", "has_active_work": true],
        ["id": "child", "thread_source": "subagent", "has_active_work": true],
        ["id": "done", "status": "completed", "last_progress_seconds": 1]
    ])
    store.panelVisible = true; store.showRecent = true; store.showUnassigned = true
    store.selectChat(store.unassignedRows[0].chat)
    store.showRecent = false
    #expect(store.selectedChat?.threadID == "child")
    #expect(store.visibleChats.map(\.threadID) == ["root", "child"])
    #expect(store.activeRows.count == 1)
    store.showUnassigned = false
    #expect(store.selectedChat?.threadID == "root")
}

@Test @MainActor func confirmedParentKeepsSelectedChildAsARetainedReadingEntryUntilSwitching() throws {
    let rows: [[String: Any]] = [
        ["id": "root", "home": "scope", "has_active_work": true],
        ["id": "child", "home": "scope", "thread_source": "subagent", "has_active_work": true]
    ]
    let store = try panelStore(rows)
    store.panelVisible = true; store.showUnassigned = true
    store.selectChat(store.unassignedRows[0].chat)
    let childKey = store.selectedChatKey
    var associated = rows
    associated[1]["parent_thread_id"] = "root"
    try updatePanel(store, associated)
    #expect(store.selectedChatKey == childKey && store.unassignedRows.isEmpty)
    #expect(store.visibleChats.map(\.threadID) == ["root", "child"])
    #expect(store.visibleChats[1].isKnownSubagent && store.activeRows.count == 1)
    store.selectChat(store.activeRows[0].chat)
    #expect(store.visibleChats.map(\.threadID) == ["root"])
}

@Test @MainActor func selectingRecentOrUnassignedDoesNotMoveItAheadOfItsPeers() throws {
    let store = try panelStore([
        ["id": "root", "has_active_work": true],
        ["id": "done-a", "status": "completed", "last_progress_seconds": 1],
        ["id": "done-b", "status": "failed", "last_progress_seconds": 2],
        ["id": "orphan-a", "thread_source": "subagent", "has_active_work": true],
        ["id": "orphan-b", "thread_source": "subagent", "has_active_work": true]
    ])
    store.panelVisible = true; store.showRecent = true; store.showUnassigned = true
    let order = store.visibleChats.map(\.id)
    store.selectChat(store.recentRoots[1])
    #expect(store.visibleChats.map(\.id) == order)
    store.selectChat(store.unassignedRows[1].chat)
    #expect(store.visibleChats.map(\.id) == order)
}

@Test func panelStatisticsRequireExplicitTurnOwnershipForEachMetric() throws {
    let usage = try panelUsage(["turn_id": "new", "output_tokens": 5214, "reasoning_tokens": 1382,
        "latest_reasoning": ["turn_id": "old", "tokens_per_second": 50]])
    let current = PanelStatistics(usage: usage, selectedTurn: "new")
    #expect(current.outputTokens == 5214 && current.reasoningTokens == 1382)
    #expect(current.reasoningRate == nil)
    let old = PanelStatistics(usage: usage, selectedTurn: "old")
    #expect(old.outputTokens == nil && old.reasoningTokens == nil && old.reasoningRate == 50)
    #expect(!old.isUnavailable)
    #expect(PanelStatistics(usage: usage, selectedTurn: nil).isUnavailable)
    #expect(PanelStatistics(usage: usage, selectedTurn: "new", switchingTurn: true).isUnavailable)
}

@Test func legacyUsageDoesNotBorrowChatIdentityAndKnownZeroIsPreserved() throws {
    let old = try panelUsage(["output_tokens": 5214, "reasoning_tokens": 1382,
        "latest_reasoning": ["tokens_per_second": 50]])
    #expect(PanelStatistics(usage: old, selectedTurn: "new").isUnavailable)
    let zero = try panelUsage(["turn_id": "new", "output_tokens": 0,
        "latest_reasoning": ["turn_id": "new", "tokens_per_second": 0]])
    let result = PanelStatistics(usage: zero, selectedTurn: "new")
    #expect(result.outputTokens == 0 && result.reasoningTokens == nil && result.reasoningRate == 0)
    #expect(!result.isUnavailable)
    let malformed = try panelUsage(["turn_id": "new", "output_tokens": -1, "reasoning_tokens": -4])
    #expect(PanelStatistics(usage: malformed, selectedTurn: "new").isUnavailable)
}

@Test func previewTimelinesKeepDifferentWorkingThreadsSeparate() async throws {
    func bundle(_ thread: String) -> [String: Any] {
        ["version": 1, "scope_id": "fixture", "thread_id": thread, "selected_turn_id": "turn",
         "current_turn_id": "turn", "turns": [], "agents": [], "phases": [], "markers": [],
         "now_ms": 1000, "revision": "test", "quality": [], "earlier_active_turns": []]
    }
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
    try JSONSerialization.data(withJSONObject: ["bundles": ["current": bundle("root")],
        "threads": ["other": ["bundles": ["current": bundle("other")]]]]).write(to: url)
    defer { try? FileManager.default.removeItem(at: url) }
    let client = FixtureTimelineClient(path: url.path)
    let root = try await client.timeline(scope: "fixture", thread: "root", turn: nil)
    let other = try await client.timeline(scope: "fixture", thread: "other", turn: nil)
    #expect(root.thread_id == "root" && other.thread_id == "other")
}
