import Foundation
import AppKit
import Testing
import MonitorModel
@testable import MonitorApp

private actor ControlledTimelineClient: TimelineReading {
    var timelines: [String: CheckedContinuation<TimelineBundle, any Error>] = [:]
    var details: [String: CheckedContinuation<ActivityDetail, any Error>] = [:]
    var observers: [String: [CheckedContinuation<Void, Never>]] = [:]

    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle {
        let key = turn ?? "current"
        return try await withCheckedThrowingContinuation { continuation in
            timelines[key] = continuation
            for observer in observers.removeValue(forKey: key) ?? [] { observer.resume() }
        }
    }
    func detail(phase: TimelinePhase, section: String?, offset: Int) async throws -> ActivityDetail {
        try await withCheckedThrowingContinuation { continuation in
            details[phase.id] = continuation
            for observer in observers.removeValue(forKey: phase.id) ?? [] { observer.resume() }
        }
    }
    func waitFor(_ key: String) async {
        if timelines[key] != nil || details[key] != nil { return }
        await withCheckedContinuation { observers[key, default: []].append($0) }
    }
    func resolve(_ key: String, bundle: TimelineBundle) { timelines.removeValue(forKey: key)?.resume(returning: bundle) }
    func resolve(_ key: String, detail: ActivityDetail) { details.removeValue(forKey: key)?.resume(returning: detail) }
}

private func bundle(_ turn: String) throws -> TimelineBundle {
    let value: [String: Any] = ["version": 1, "scope_id": "scope", "thread_id": "root", "selected_turn_id": turn,
        "current_turn_id": "new", "turns": [["thread_id": "root", "turn_id": turn, "status": "active"]],
        "agents": [], "phases": [], "markers": [], "origin_ms": 1000, "end_ms": 5000,
        "now_ms": 5000, "revision": turn, "quality": [], "earlier_active_turns": []]
    return try TimelineBundle.decode(JSONSerialization.data(withJSONObject: value))
}

private func phase(_ id: String) throws -> TimelinePhase {
    let value: [String: Any] = ["id": id, "scope_id": "scope", "thread_id": "root", "turn_id": "turn",
        "item_id": id, "namespace": "model", "track": "model", "kind": "text", "label": "正文", "status": "completed"]
    return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: value))
}

@Test @MainActor func agentNameRefreshAndPresentationModesKeepSelectionAndExpansion() async throws {
    func namedBundle(_ name: String) throws -> TimelineBundle {
        try TimelineBundle.decode(JSONSerialization.data(withJSONObject: ["version": 1, "scope_id": "scope", "thread_id": "root",
            "selected_turn_id": "new", "current_turn_id": "new", "turns": [],
            "agents": [["id": "root", "thread_id": "root", "name": "主智能体"],
                       ["id": "child", "thread_id": "child", "name": "子智能体 · " + name, "parent_thread_id": "root"]],
            "phases": [], "markers": [], "origin_ms": 1000, "end_ms": 5000, "now_ms": 5000,
            "revision": name, "quality": [], "earlier_active_turns": []]))
    }
    let client = ControlledTimelineClient()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    store.bundle = try namedBundle("first"); store.selectedTurn = "new"
    store.expandedAgents = ["child"]; store.selectedPhaseID = "selected"; store.selectedPhaseIDs = ["selected"]
    store.followsLatest = false; store.scrollOffset = 40
    let viewer = try #require(store.layout(for: .activityViewer))
    let panel = try #require(store.layout(for: .panelGrouped))
    #expect(viewer.lanes.first?.track == "model" && panel.lanes.first?.track == "header")
    let pending = Task { await store.refresh() }
    await client.waitFor("current")
    await client.resolve("current", bundle: try namedBundle("Lorentz"))
    await pending.value
    #expect(store.expandedAgents == ["child"] && store.selectedPhaseIDs == ["selected"])
    #expect(store.scrollOffset == 40 && !store.followsLatest)
    #expect(store.layout(for: .activityViewer)?.lanes.map(\.track) == viewer.lanes.map(\.track))
    #expect(store.layout(for: .panelGrouped)?.lanes.map(\.track) == panel.lanes.map(\.track))
}

@Test @MainActor func panelSwitchBackWithinPresentationKeepsHistoricalReadingPosition() throws {
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    store.bundle = try bundle("new"); store.selectedTurn = "new"
    store.prepareForPresentation(1)
    store.selectTurn("old"); store.scrollOffset = 42; store.zoom = .overview
    store.prepareForPresentation(1)
    #expect(store.selectedTurn == "old" && store.scrollOffset == 42 && store.zoom == .overview)
    store.prepareForPresentation(2)
    #expect(store.followsCurrentTurn && store.selectedTurn == "new" && store.zoom == .overview)
}

@Test @MainActor func historicalRequestRejectsReturnedWrongTurnAndLatestInvalidatesOldRequest() async throws {
    let client = ControlledTimelineClient()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    store.bundle = try bundle("new"); store.selectedTurn = "new"
    store.selectTurn("old")
    let wrong = Task { await store.refresh() }
    await client.waitFor("old"); await client.resolve("old", bundle: try bundle("unrelated")); await wrong.value
    #expect(store.bundle?.selected_turn_id == "new" && store.error != nil)
    let old = Task { await store.refresh() }
    await client.waitFor("old")
    store.latest()
    await client.resolve("old", bundle: try bundle("old")); await old.value
    #expect(store.bundle?.selected_turn_id == "new" && store.selectedTurn == "new")
}

@Test @MainActor func incomingHistoricalTurnResetsViewportAfterLoadingOldBundle() throws {
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    store.bundle = try bundle("new"); store.selectedTurn = "new"
    let currentPhase = try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: [
        "id": "current", "scope_id": "scope", "thread_id": "root", "turn_id": "new", "item_id": "current",
        "namespace": "model", "track": "model", "kind": "text", "label": "正文", "status": "completed",
        "start_ms": 200000, "end_ms": 210000]))
    let oldPhase = try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: [
        "id": "old", "scope_id": "scope", "thread_id": "root", "turn_id": "old", "item_id": "old",
        "namespace": "model", "track": "model", "kind": "text", "label": "正文", "status": "completed",
        "start_ms": 1000, "end_ms": 5000]))
    let lanes = [TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 18, isChild: false)]
    let current = TimelineLayout(origin: 200000, end: 210000, now: 210000, lanes: lanes, activities: [currentPhase])
    let historic = TimelineLayout(origin: 1000, end: 5000, now: 210000, lanes: lanes, activities: [oldPhase])
    let scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 291, height: 80))
    let document = TimelineDocumentView(); scroll.documentView = document
    let coordinator = TimelineSurface.Coordinator(store: store)
    coordinator.update(scroll, layout: current, activities: [currentPhase], agents: [], onSelect: { _ in })
    store.selectTurn("old")
    coordinator.update(scroll, layout: current, activities: [currentPhase], agents: [], onSelect: { _ in })
    #expect(document.turn == "new") // A hidden loading frame is still the old source bundle.
    store.bundle = try bundle("old")
    coordinator.update(scroll, layout: historic, activities: [oldPhase], agents: [], onSelect: { _ in })
    #expect(document.turn == "old" && scroll.contentView.bounds.minX == 0)
    let rect = try #require(document.layout?.rect(for: oldPhase))
    #expect(rect.intersects(scroll.contentView.bounds))
}

@MainActor private func overlappingDocument() throws -> TimelineDocumentView {
    let view = TimelineDocumentView()
    view.activities = try [("a", 1000, 31000), ("b", 1000, 21000), ("later", 32000, 33000)].map { id, start, end in
        let value: [String: Any] = ["id": id, "scope_id": "scope", "thread_id": "root", "turn_id": "turn",
            "item_id": id, "namespace": "canonical", "track": "tool", "kind": "tool_execution", "label": id,
            "status": "completed", "start_ms": start, "end_ms": end]
        return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: value))
    }
    view.layout = TimelineLayout(origin: 0, end: 33000, now: 33000, lanes: [
        TimelineLane(id: "tool", threadID: "root", track: "tool", label: "工具", height: 21, isChild: false)], activities: view.activities)
    view.resizeDocument()
    return view
}

@Test @MainActor func overlappingToolClickOpensOnlyLocalActivitiesDirectly() throws {
    let view = try overlappingDocument()
    var opened: [[String]] = []
    view.onSelect = { opened.append($0.map(\.id)) }
    let a = try #require(view.layout?.rect(for: view.activities[0]))
    let later = try #require(view.layout?.rect(for: view.activities[2]))
    view.activate(at: CGPoint(x: view.layout!.x(at: 10000), y: a.midY))
    #expect(opened == [["a", "b"]])
    view.activate(at: CGPoint(x: view.layout!.x(at: 25000), y: a.midY))
    #expect(opened == [["a", "b"], ["a"]])
    view.activate(at: CGPoint(x: later.midX, y: later.midY))
    #expect(opened == [["a", "b"], ["a"], ["later"]])
    view.activate(at: CGPoint(x: later.maxX + 0.1, y: later.midY))
    #expect(opened.count == 3)
    #expect(view.layout?.rect(for: view.activities[2]) == later)
}

@Test @MainActor func serialModelStagesOpenDirectlyDespiteOverlappingPixelWidths() throws {
    let view = TimelineDocumentView()
    view.activities = try [("reasoning", 1000, 1046), ("text", 1049, 2041)].map { kind, start, end in
        let value: [String: Any] = ["id": kind, "scope_id": "scope", "thread_id": "root", "turn_id": "turn",
            "item_id": kind, "namespace": "model", "track": "model", "kind": kind, "label": kind,
            "status": "completed", "start_ms": start, "end_ms": end]
        return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: value))
    }
    view.layout = TimelineLayout(origin: 0, end: 3000, now: 3000, lanes: [
        TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)], activities: view.activities)
    view.resizeDocument()
    let reasoning = try #require(view.layout?.rect(for: view.activities[0]))
    let text = try #require(view.layout?.rect(for: view.activities[1]))
    #expect(reasoning.intersects(text))
    var opened: [String] = []
    view.onSelect = { opened.append(contentsOf: $0.map(\.id)) }
    view.activate(at: CGPoint(x: reasoning.midX, y: reasoning.midY))
    view.activate(at: CGPoint(x: text.midX, y: text.midY))
    #expect(opened == ["reasoning", "text"])
}

@Test @MainActor func timelineResizeKeepsMenuScaleAndActivityWidths() throws {
    let view = try overlappingDocument()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    store.followsLatest = false
    let coordinator = TimelineSurface.Coordinator(store: store)
    let scroll = TimelineScrollView(frame: .zero)
    scroll.documentView = view
    let before = view.activities.map { view.layout!.rect(for: $0) }
    coordinator.resize(scroll, viewport: 600)
    #expect(view.layout!.pointsPerSecond == TimelineLayout.defaultScale)
    #expect(view.activities.map { view.layout!.rect(for: $0) } == before)
    coordinator.resize(scroll, viewport: 291)
    #expect(abs(view.layout!.pointsPerSecond - 283.0 / 30) < 0.000001)
    #expect(view.viewportWidth == 291)
    #expect(view.layout?.rect(for: view.activities[2]).map { Double($0.minX) } == view.layout?.x(at: 32000))
}

@Test @MainActor func wideningWhileReadingKeepsTheLeftTimeEvenWhenAllHistoryWouldFit() throws {
    let view = try overlappingDocument()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    store.followsLatest = false; store.scrollOffset = 40
    let coordinator = TimelineSurface.Coordinator(store: store)
    let scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 291, height: 80))
    scroll.borderType = .noBorder; scroll.documentView = view
    view.resizeDocument(preservingOffset: 40)
    scroll.contentView.scroll(to: CGPoint(x: 40, y: 0))
    coordinator.observe(scroll)
    defer { coordinator.stop() }
    let sourceTime = view.layout!.time(at: 40)
    scroll.setFrameSize(NSSize(width: 600, height: 80))
    coordinator.resize(scroll, viewport: 600)
    #expect(abs(scroll.contentView.bounds.minX - 40) < 0.001)
    #expect(abs(view.layout!.time(at: scroll.contentView.bounds.minX) - sourceTime) < 0.001)
    #expect(view.frame.width >= 640)
    view.activate(at: CGPoint(x: 280, y: 90))
    view.resizeDocument()
    #expect(abs(view.frame.width - 640) < 0.001)
    #expect(abs(scroll.contentView.bounds.minX - 40) < 0.001)
}

@Test @MainActor func resizingWhileFollowingKeepsTheLatestEdgeAtTheSameScale() throws {
    let view = try overlappingDocument()
    view.layout = TimelineLayout(origin: 0, end: 300000, now: 300000, lanes: view.layout!.lanes, activities: view.activities)
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    let coordinator = TimelineSurface.Coordinator(store: store)
    let scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 291, height: 80))
    scroll.borderType = .noBorder; scroll.documentView = view
    for width in [600.0, 291.0] {
        scroll.setFrameSize(NSSize(width: width, height: 80))
        coordinator.resize(scroll, viewport: width)
        #expect(abs(scroll.contentView.bounds.maxX - view.frame.width) < 0.001)
        #expect(view.layout!.pointsPerSecond == TimelineLayout.defaultScale)
    }
}

@Test @MainActor func lateCurrentTurnResponseCannotReplaceChosenHistory() async throws {
    let client = ControlledTimelineClient()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    let first = Task { await store.refresh() }
    await client.waitFor("current")
    store.selectTurn("old")
    let second = Task { await store.refresh() }
    await client.waitFor("old")
    await client.resolve("old", bundle: try bundle("old"))
    await second.value
    await client.resolve("current", bundle: try bundle("new"))
    await first.value
    #expect(store.selectedTurn == "old")
    #expect(store.bundle?.selected_turn_id == "old")
    #expect(!store.followsCurrentTurn)
}

@Test @MainActor func reopeningRunningTimelineAndNewTurnRestoreFollowing() async throws {
    let client = ControlledTimelineClient()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    store.bundle = try bundle("new"); store.selectedTurn = "new"
    store.followsLatest = false; store.scrollOffset = 100
    store.prepareForPresentation()
    #expect(store.followsLatest && store.followsCurrentTurn)
    store.followsLatest = false
    store.selectedPhaseID = "old-phase"; store.selectedPhaseIDs = ["old-phase"]
    let refresh = Task { await store.refresh() }
    await client.waitFor("current")
    await client.resolve("current", bundle: try bundle("next"))
    await refresh.value
    #expect(store.followsLatest && store.scrollOffset == 0)
    #expect(store.selectedPhaseIDs.isEmpty)
}

@Test @MainActor func externalScrollPausesFollowingWithoutRequiringAMouseEvent() throws {
    let view = try overlappingDocument()
    let store = TimelineStore(scope: "scope", thread: "root", title: "test", client: ControlledTimelineClient())
    let coordinator = TimelineSurface.Coordinator(store: store)
    let scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 291, height: 80))
    scroll.documentView = view
    coordinator.observe(scroll)
    defer { coordinator.stop() }
    scroll.contentView.postsBoundsChangedNotifications = true
    scroll.contentView.scroll(to: CGPoint(x: 10, y: 0))
    NotificationCenter.default.post(name: NSView.boundsDidChangeNotification, object: scroll.contentView)
    #expect(!store.followsLatest)
}

@Test @MainActor func lateDetailCannotChangeNewActivitySelection() async throws {
    let client = ControlledTimelineClient()
    let timeline = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    let old = try phase("old"), new = try phase("new")
    let viewer = ActivityViewerStore(timeline: timeline, phase: old)
    let pending = Task { await viewer.refresh() }
    await client.waitFor("old")
    viewer.select(new)
    let response = try JSONDecoder().decode(ActivityDetail.self, from: Data("""
    {"version":1,"state":"available","sections":[{"id":"body","title":"正文","format":"text","text":"old content"}],"attachments":[]}
    """.utf8))
    await client.resolve("old", detail: response)
    await pending.value
    #expect(viewer.phase.id == "new")
    #expect(viewer.detail == nil)
    #expect(!viewer.followsContent)
}

private func rawDetail(_ text: String, next: Int? = nil, state: String = "available") throws -> ActivityDetail {
    let value: [String: Any] = ["version": 1, "state": state, "source": "codex_history", "attachments": [],
        "sections": state == "available" ? [["id": "input", "title": "参数", "format": "text", "text": text,
            "next_offset": next as Any? ?? NSNull()]] : []]
    return try JSONDecoder().decode(ActivityDetail.self, from: JSONSerialization.data(withJSONObject: value))
}

@Test @MainActor func multipleDetailsKeepIndependentFailurePagingAndClickedMembership() async throws {
    let client = ControlledTimelineClient()
    let timeline = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    let a = try phase("a"), b = try phase("b"), c = try phase("c")
    let group = ActivityViewerGroupStore(timeline: timeline, phases: [b, a, a])
    #expect(group.entries.map { $0.phase.id } == ["a", "b"])
    let task = Task { await group.refresh() }
    await client.waitFor("a"); await client.waitFor("b")
    await client.resolve("b", detail: try rawDetail("", state: "unavailable"))
    await client.resolve("a", detail: try rawDetail("prefix", next: 6))
    await task.value
    let entry = group.entries[0]
    #expect(entry.detail?.sections[0].text == "prefix")
    #expect(group.entries[1].detail?.state == "unavailable")
    #expect(group.entries.map { $0.phase.id } == ["a", "b"])
    let paging = Task { await entry.loadMore() }
    await client.waitFor("a")
    await client.resolve("a", detail: try rawDetail(" tail"))
    await paging.value
    #expect(entry.detail?.sections[0].text == "prefix tail")
    #expect(group.entries[1].detail?.sections.isEmpty == true)
    group.select([c, a])
    #expect(group.entries[0] === entry)
    #expect(group.entries[0].detail?.sections[0].text == "prefix tail")
    #expect(timeline.selectedPhaseIDs == ["a", "c"])
}

@Test @MainActor func lateMultiToolResponsesCannotReplaceTheNextClickedSelection() async throws {
    let client = ControlledTimelineClient()
    let timeline = TimelineStore(scope: "scope", thread: "root", title: "test", client: client)
    let group = ActivityViewerGroupStore(timeline: timeline, phases: try [phase("a"), phase("b")])
    let pending = Task { await group.refresh() }
    await client.waitFor("a"); await client.waitFor("b")
    group.select([try phase("c")])
    await client.resolve("a", detail: try rawDetail("old a"))
    await client.resolve("b", detail: try rawDetail("old b"))
    await pending.value
    #expect(group.entries.map { $0.phase.id } == ["c"])
    #expect(group.entries[0].detail == nil)
    #expect(timeline.selectedPhaseIDs == ["c"])
}

@Test @MainActor func viewerFrameReturnsToAvailableScreenWithoutGrowing() {
    let visible = NSRect(x: -1920, y: 30, width: 1920, height: 1050)
    let frame = NSRect(x: 2500, y: -1000, width: 800, height: 600)
    let result = ActivityViewerWindowController.constrained(frame, to: visible)
    #expect(visible.contains(result))
    #expect(result.size == frame.size)
}

@Test @MainActor func timelineShadesRemainVisibleInBothAppearances() throws {
    func luminance(_ color: NSColor) -> Double {
        let c = color.usingColorSpace(.deviceRGB)!
        func linear(_ n: Double) -> Double { n <= 0.04045 ? n / 12.92 : pow((n + 0.055) / 1.055, 2.4) }
        return 0.2126 * linear(c.redComponent) + 0.7152 * linear(c.greenComponent) + 0.0722 * linear(c.blueComponent)
    }
    for name in [NSAppearance.Name.aqua, .darkAqua] {
        let appearance = try #require(NSAppearance(named: name))
        appearance.performAsCurrentDrawingAppearance {
            for background in [NSColor.windowBackgroundColor, .controlBackgroundColor] {
                let bg = background.usingColorSpace(.deviceRGB)!
                for kind in ["reasoning", "text", "tool_input", "file_edit_input", "tool_execution", "contextCompaction"] {
                    let fg = TimelinePalette.color(for: kind).usingColorSpace(.deviceRGB)!
                    let alpha = kind == "tool_execution" ? TimelinePalette.toolOpacity : 0.95
                    let visible = NSColor(srgbRed: fg.redComponent * alpha + bg.redComponent * (1 - alpha),
                                          green: fg.greenComponent * alpha + bg.greenComponent * (1 - alpha),
                                          blue: fg.blueComponent * alpha + bg.blueComponent * (1 - alpha), alpha: 1)
                    let a = luminance(visible), b = luminance(bg)
                    #expect((max(a, b) + 0.05) / (min(a, b) + 0.05) >= 3)
                }
            }
        }
    }
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
@MainActor func viewerReusesWindowAndPreservesResizedFrameAcrossContentChanges() throws {
    _ = NSApplication.shared
    let controller = ActivityViewerWindowController(preview: true)
    let window = try #require(controller.window)
    defer { window.close() }
    let timeline = TimelineStore(scope: "scope", thread: "root", title: "test", client: FixtureTimelineClient(path: nil))
    let screen = try #require(NSScreen.main)
    let size = NSSize(width: min(900, screen.visibleFrame.width), height: min(650, screen.visibleFrame.height))
    let frame = NSRect(origin: screen.visibleFrame.origin, size: size)
    window.setFrame(frame, display: false)
    controller.show(timeline: timeline, phase: try phase("one"))
    #expect(window.styleMask.contains(.resizable))
    #expect(window.level == .normal)
    #expect(window.frame.size == size)
    let resized = NSSize(width: max(640, size.width - 40), height: max(452, size.height - 30))
    window.setFrame(NSRect(origin: window.frame.origin, size: resized), display: false)
    controller.show(timeline: timeline, phases: try [phase("two"), phase("z-three")])
    #expect(controller.window === window)
    #expect(controller.viewer?.entries.map { $0.phase.id } == ["two", "z-three"])
    #expect(window.frame.size == resized)
    window.close()
    #expect(controller.viewer == nil)
    controller.show(timeline: timeline, phase: try phase("three"))
    #expect(controller.window === window)
    #expect(window.frame.size == resized)
}
