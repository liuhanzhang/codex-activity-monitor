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

@Test @MainActor func panelDefaultsExpandWorkingRootsAcrossScopesAndChildWork() throws {
    let store = try panelStore([
        ["id": "same", "home": "a", "has_active_work": false],
        ["id": "child", "home": "a", "parent_thread_id": "same", "has_active_work": true],
        ["id": "same", "home": "b", "has_active_work": true],
        ["id": "done", "home": "a", "status": "completed", "has_active_work": false]
    ])
    store.panelVisible = true
    #expect(store.activeRows.count == 2)
    #expect(store.expanded == Set(store.activeRows.map { $0.chat.id }))
    #expect(!store.showRecent && store.recentRows.isEmpty)
    #expect(!store.expanded.contains(store.snapshot!.threads[1].id))
}

@Test @MainActor func manualPanelChoicesSurviveRefreshWhileNewWorkExpands() throws {
    let a: [String: Any] = ["id": "a", "has_active_work": true]
    let b: [String: Any] = ["id": "b", "has_active_work": true]
    let done: [String: Any] = ["id": "done", "status": "completed", "has_active_work": false]
    let store = try panelStore([a, b, done])
    store.panelVisible = true
    let first = store.snapshot!.threads[0], recent = store.snapshot!.threads[2]
    store.setExpanded(false, for: first)
    store.setExpanded(true, for: recent)
    store.showRecent = true
    try updatePanel(store, [b, ["id": "c", "has_active_work": true], a, done])
    #expect(store.activeRows.map { $0.chat.threadID } == ["a", "b", "c"])
    #expect(!store.expanded.contains(first.id))
    #expect(store.expanded.contains(recent.id) && store.showRecent)
    #expect(store.expanded.contains(store.snapshot!.threads[1].id))
    try updatePanel(store, [b, done])
    try updatePanel(store, [a, b, done])
    #expect(store.expanded.contains(first.id)) // Removed identities discard obsolete overrides.
}

@Test @MainActor func reopeningPanelRestoresDefaultsWithoutReplacingTimelineState() throws {
    let store = try panelStore([["id": "a", "has_active_work": true], ["id": "done", "status": "completed"]])
    store.panelVisible = true
    let chat = store.snapshot!.threads[0], timeline = store.timeline(for: chat)
    timeline.zoom = .overview
    store.setExpanded(false, for: chat); store.showRecent = true
    let presentation = store.presentationID
    store.panelVisible = true
    #expect(store.presentationID == presentation && !store.expanded.contains(chat.id))
    store.panelVisible = false; store.panelVisible = true
    #expect(store.presentationID == presentation + 1)
    #expect(store.expanded.contains(chat.id) && !store.showRecent)
    #expect(store.timeline(for: chat) === timeline && timeline.zoom == .overview)
}

@Test @MainActor func startingAndFinishingWorkDoNotOverrideExplicitPanelChoices() throws {
    let store = try panelStore([["id": "a", "has_active_work": false]])
    let chat = store.snapshot!.threads[0]
    store.setExpanded(false, for: chat)
    try updatePanel(store, [["id": "a", "has_active_work": true]])
    #expect(!store.expanded.contains(chat.id))
    store.setExpanded(true, for: store.snapshot!.threads[0])
    try updatePanel(store, [["id": "a", "status": "completed", "has_active_work": false]])
    #expect(store.expanded.contains(chat.id))
    #expect(store.activeRows.isEmpty)
}

private func panelUsage(_ fields: [String: Any]) throws -> Usage {
    try JSONDecoder().decode(Usage.self, from: JSONSerialization.data(withJSONObject: fields))
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
