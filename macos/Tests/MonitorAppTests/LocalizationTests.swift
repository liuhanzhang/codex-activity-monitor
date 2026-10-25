import AppKit
import Foundation
import Observation
import SwiftUI
import Testing
@testable import MonitorModel
@testable import MonitorApp

private actor LanguageBackend: BackendManaging {
    var calls: [[String]] = []
    var continuation: CheckedContinuation<Void, Never>?
    let fail: Bool
    init(fail: Bool = false) { self.fail = fail }
    func command(_ action: String, extra: [String]) async throws -> ManagementState {
        calls.append([action] + extra)
        if calls.count == 1 { await withCheckedContinuation { continuation = $0 } }
        if fail { throw BackendFailure(message: "diagnostic") }
        return try JSONDecoder().decode(ManagementState.self, from: Data("{\"version\":1,\"ok\":true}".utf8))
    }
    func heartbeat(_ session: ObservationLease) async {}
    func started() -> Bool { continuation != nil }
    func release() { continuation?.resume(); continuation = nil }
    func recorded() -> [[String]] { calls }
}

@MainActor private func languageStore(_ backend: (any BackendManaging)? = nil, readOnly: Bool = true, root: String = "/unused") -> DashboardStore {
    DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused", data_root: root, build_id: "test"),
                   arguments: readOnly ? ["--read-only"] : [], client: backend)
}

@Test @MainActor func localizationSwitchKeepsIdentityAndReadingStateWithoutCommandsInPreview() async throws {
    let backend = LanguageBackend()
    let store = languageStore(backend)
    store.snapshot = try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[{\"id\":\"root\",\"has_active_work\":true}]}".utf8))
    store.updatePresentation(); store.panelVisible = true; store.showRecent = true
    let timeline = store.timeline(for: try #require(store.selectedChat))
    timeline.zoom = .wide; timeline.followsLatest = false; timeline.scrollOffset = 123
    timeline.selectedTurn = "history"; timeline.selectedPhaseID = "phase"
    let selection = store.selectedChatKey, presentation = store.presentationID, revision = timeline.viewportRevision
    store.operationError = "管理操作仍在完成，请稍后退出。"
    store.setLanguage(.english)
    #expect(store.operationError == "A management operation is still running. Quit again shortly.")
    #expect(store.selectedChatKey == selection && store.presentationID == presentation && store.showRecent)
    #expect(store.timeline(for: try #require(store.selectedChat)) === timeline)
    #expect(timeline.zoom == .wide && !timeline.followsLatest && timeline.scrollOffset == 123)
    #expect(timeline.selectedTurn == "history" && timeline.selectedPhaseID == "phase" && timeline.viewportRevision == revision)
    #expect(await backend.recorded().isEmpty)
}

@Test @MainActor func localizationSavesSerializeAndFinalChoiceWins() async throws {
    let backend = LanguageBackend(), store = languageStore(backend, readOnly: false)
    store.setLanguage(.english)
    for _ in 0..<1000 { if await backend.started() { break }; await Task.yield() }
    #expect(await backend.started())
    store.setLanguage(.chinese); store.setLanguage(.english); store.setLanguage(.chinese)
    await backend.release(); await store.waitForLanguageSave()
    #expect(store.language == .chinese)
    #expect(await backend.recorded() == [["preferences", "--ui-language", "en"], ["preferences", "--ui-language", "zh-CN"]])
}

@Test @MainActor func localizationSavingFailureLeavesCurrentLanguageAndReportsUnsaved() async {
    let backend = LanguageBackend(fail: true), store = languageStore(backend, readOnly: false)
    store.setLanguage(.english)
    for _ in 0..<1000 { if await backend.started() { break }; await Task.yield() }
    await backend.release(); await store.waitForLanguageSave()
    #expect(store.language == .english)
    #expect(store.operationError == "Language changed, but the preference could not be saved.")
}

@Test @MainActor func localizationSavedPreferenceAndSystemLocaleAreIndependent() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let path = root.appendingPathComponent("app-state.json")
    for json in ["[]", "broken", "{\"version\":true,\"language\":\"en\"}", "{\"version\":1,\"language\":{}}", "{\"version\":2,\"language\":\"en\"}", "{\"version\":1,\"language\":\"fr\"}"] {
        try Data(json.utf8).write(to: path)
        #expect(languageStore(readOnly: false, root: root.path).language == .chinese)
    }
    try Data("{\"version\":1,\"language\":\"en\",\"launch_at_login\":false}".utf8).write(to: path)
    #expect(languageStore(readOnly: false, root: root.path).language == .english)
    #expect(languageStore(root: root.path).language == .chinese) // read-only preview never reads daily preference
    #expect(L("Configure", language: .english) == "Configure")
    #expect(L("Configure", language: .chinese) == "配置")
}

@Test @MainActor func localizationFlushesPendingSaveBeforeRemovingAppData() async {
    let backend = LanguageBackend(), store = languageStore(backend, readOnly: false)
    store.setLanguage(.english)
    for _ in 0..<1000 { if await backend.started() { break }; await Task.yield() }
    let restoration = Task { await store.restore(purge: true) }
    for _ in 0..<1000 { if store.busy { break }; await Task.yield() }
    #expect(store.busy)
    store.setLanguage(.chinese) // management operations disable the language entry
    #expect(store.language == .english)
    #expect(await backend.recorded().count == 1)
    await backend.release(); await restoration.value
    #expect(await backend.recorded() == [["preferences", "--ui-language", "en"], ["restore", "--purge"]])
}

private struct NativeLanguageFixture: TimelineReading {
    let bundle: TimelineBundle
    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle { bundle }
    func detail(phase: TimelinePhase, page: ActivityPageCursor?) async throws -> ActivityDetail {
        let text = (1...100).map { "\($0). 原始中文内容 remains unchanged" }.joined(separator: "\n")
        let section = ActivitySection(id: "input", title: "参数", format: "code",
            text: text, offset: 0, total_chars: text.unicodeScalars.count, next_offset: nil, notice: nil,
            page_token: "fixture-native", title_message: DisplayMessage("detail.input"))
        return ActivityDetail(version: 2, state: "available", phase: phase,
                              sections: [section], attachments: [], source: "synthetic")
    }
}

@Suite(.serialized) struct LocalizationNativeTests {
@Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
@MainActor func localizationNativeOpenViewerRefreshesWithoutReplacingReadingViews() async throws {
    _ = NSApplication.shared
    let root = languageStore()
    root.snapshot = try Snapshot.decode(Data("""
        {"schema_version":1,"running":true,"threads":[{"id":"root","home":"scope","name":"原始聊天标题","has_active_work":true}]}
        """.utf8))
    root.updatePresentation(); root.panelVisible = true
    let phaseFields: [String: Any] = ["id": "phase", "scope_id": "scope", "thread_id": "root", "turn_id": "turn", "item_id": "item",
        "namespace": "canonical", "track": "tool", "kind": "tool_execution", "label": "source_tool", "status": "completed",
        "start_ms": 1000, "end_ms": 2000, "label_message": ["key": "activity.named", "params": ["name": "source_tool"]]]
    let phase = try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: phaseFields))
    let bundleFields: [String: Any] = ["version": 1, "scope_id": "scope", "thread_id": "root", "selected_turn_id": "turn", "current_turn_id": "turn",
        "turns": [["thread_id": "root", "turn_id": "turn", "status": "completed", "start_ms": 0, "end_ms": 3000]],
        "agents": [], "phases": [phaseFields], "markers": [], "origin_ms": 0, "end_ms": 3000, "now_ms": 3000,
        "revision": "test", "quality": [], "earlier_active_turns": []]
    let bundle = try TimelineBundle.decode(JSONSerialization.data(withJSONObject: bundleFields))
    let timeline = TimelineStore(scope: "scope", thread: "root", title: "原始聊天标题", client: NativeLanguageFixture(bundle: bundle))
    timeline.bundle = bundle; timeline.selectedTurn = "turn"; timeline.zoom = .wide; timeline.followsLatest = false
    let viewer = ActivityViewerWindowController(preview: true, dashboard: root)
    viewer.show(timeline: timeline, phase: phase)
    defer { viewer.close() }
    let group = try #require(viewer.viewer), entry = try #require(group.entries.first)
    await group.refresh()
    entry.followsContent = false
    func descendants<T: NSView>(_ view: NSView, _ type: T.Type) -> [T] {
        (view as? T).map { [$0] } ?? view.subviews.flatMap { descendants($0, type) }
    }
    let host = try #require(viewer.window?.contentView)
    for _ in 0..<60 {
        host.layoutSubtreeIfNeeded()
        if !descendants(host, NSTextView.self).isEmpty { break }
        try await Task.sleep(for: .milliseconds(10))
    }
    let document = try #require(descendants(host, TimelineDocumentView.self).first)
    let text = try #require(descendants(host, NSTextView.self).first)
    let scroll = try #require(text.enclosingScrollView)
    text.setSelectedRange(NSRange(location: 3, length: 8))
    scroll.contentView.scroll(to: NSPoint(x: 0, y: 100))
    let offset = scroll.contentView.bounds.origin, selected = text.selectedRanges, original = text.string
    await entry.refresh()
    root.setLanguage(.english)
    for _ in 0..<60 {
        host.layoutSubtreeIfNeeded()
        if document.language == .english { break }
        try await Task.sleep(for: .milliseconds(10))
    }
    viewer.updateLanguage()
    #expect(viewer.window?.title == "Activity Viewer")
    #expect(document.language == .english)
    #expect(viewer.viewer === group && group.entries.first === entry)
    #expect(descendants(host, NSTextView.self).first === text)
    #expect(text.string == original && text.selectedRanges == selected)
    #expect(scroll.contentView.bounds.origin == offset)
    #expect(!entry.followsContent && timeline.zoom == .wide && !timeline.followsLatest)
    #expect(document.activityLabel(phase).contains("Tool execution · source_tool · Completed"))
    root.setLanguage(.chinese)
    for _ in 0..<60 {
        host.layoutSubtreeIfNeeded()
        if document.language == .chinese { break }
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(document.language == .chinese)
    #expect(text.string == original && text.selectedRanges == selected)
    print("LOCALIZATION_NATIVE: open viewer, document language, text identity, selection and scroll survived en/zh-CN")
}

@Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
@MainActor func localizationNativeDialogsAndEmptyErrorStatesRenderInBothLanguages() async throws {
    _ = NSApplication.shared
    let directory = ProcessInfo.processInfo.environment["MONITOR_LOCALIZATION_ARTIFACTS"].map { URL(fileURLWithPath: $0) }
    if let directory { try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true) }
    let root = languageStore()
    root.snapshot = try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[]}".utf8))
    root.updatePresentation()
    func render<V: View>(_ content: V, name: String) async throws {
        let host = NSHostingView(rootView: content.background(.background))
        let size = host.fittingSize
        #expect(size.width >= 400 && size.width <= 460 && size.height > 50 && size.height < 800)
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [], backing: .buffered, defer: false)
        window.contentView = host; window.orderFront(nil)
        try await Task.sleep(for: .milliseconds(80))
        host.layoutSubtreeIfNeeded()
        defer { window.orderOut(nil) }
        if let directory, let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let data = try #require(bitmap.representation(using: .png, properties: [:]))
            try data.write(to: directory.appendingPathComponent(name + ".png"))
        }
    }
    for language in MonitorLanguage.allCases {
        root.setLanguage(language)
        for dark in [false, true] {
            NSApplication.shared.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
            let suffix = language.rawValue + (dark ? "-dark" : "-light")
            try await render(RestoreSheet(store: root), name: "restore-" + suffix)
            root.operationError = nil
            try await render(DashboardView(store: root, quit: {}), name: "empty-" + suffix)
            root.operationError = "语言已切换，本次选择未能保存。"
            try await render(DashboardView(store: root, quit: {}), name: "error-" + suffix)
        }
    }
    NSApplication.shared.appearance = nil
    print("LOCALIZATION_NATIVE: restore, empty and error states rendered in zh-CN/en, light/dark")
}

}
