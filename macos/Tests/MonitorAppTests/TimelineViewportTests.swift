import AppKit
import SwiftUI
import Testing
import MonitorModel
@testable import MonitorApp

private func viewportBundle(seconds: Double, status: String = "active", end: Double? = nil) throws -> TimelineBundle {
    var turn: [String: Any] = ["thread_id": "root", "turn_id": "turn", "status": status, "start_ms": 0]
    if let end { turn["end_ms"] = end * 1000 }
    return try TimelineBundle.decode(JSONSerialization.data(withJSONObject: [
        "version": 1, "scope_id": "scope", "thread_id": "root", "selected_turn_id": "turn", "current_turn_id": "turn",
        "turns": [turn], "agents": [], "phases": [], "markers": [], "origin_ms": 0,
        "end_ms": (end ?? seconds) * 1000, "now_ms": seconds * 1000,
        "revision": "fixture", "quality": [], "earlier_active_turns": []]))
}

@MainActor private func viewportStore(seconds: Double = 59) throws -> TimelineStore {
    let store = TimelineStore(scope: "scope", thread: "root", title: "synthetic", client: FixtureTimelineClient(path: nil))
    store.bundle = try viewportBundle(seconds: seconds); store.selectedTurn = "turn"
    return store
}

@MainActor private final class ViewportHarness {
    let store: TimelineStore
    let scroll: TimelineScrollView
    let document: TimelineDocumentView
    let coordinator: TimelineSurface.Coordinator
    init(paged: Bool = true) throws {
        store = try viewportStore()
        scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 355, height: 80))
        scroll.borderType = .noBorder
        document = TimelineDocumentView(); scroll.documentView = document
        scroll.contentView.postsBoundsChangedNotifications = true
        coordinator = TimelineSurface.Coordinator(store: store, pagedFollowing: paged)
        coordinator.observe(scroll)
    }
    func render() throws {
        coordinator.update(scroll, layout: try #require(store.layout(for: .panelCompact)),
                           activities: store.bundle?.allActivities ?? [], agents: [], onSelect: { _ in })
    }
    func advance(_ seconds: Double) throws {
        store.bundle = try viewportBundle(seconds: seconds)
        try render()
    }
}

@Test @MainActor func compactPagingKeepsOffsetStableAndReservesTheFuture() throws {
    let h = try ViewportHarness()
    defer { h.coordinator.stop() }
    #expect(h.store.zoom == .overview)
    for time in [1.0, 59, 60] {
        try h.advance(time)
        #expect(h.scroll.contentView.bounds.minX == 0)
    }
    try h.advance(60.001)
    let offset = 347 * 0.75
    #expect(abs(h.scroll.contentView.bounds.minX - offset) < 0.001)
    #expect(h.document.frame.width >= offset + 355)
    for time in [61.0, 90, 105, 104.99] {
        try h.advance(time)
        #expect(abs(h.scroll.contentView.bounds.minX - offset) < 0.001)
        #expect(h.store.followsLatest)
    }
    try h.advance(105.001)
    #expect(abs(h.scroll.contentView.bounds.minX - offset * 2) < 0.001)
    try h.advance(600)
    #expect(h.coordinator.page?.index == 12)
    #expect(h.coordinator.animationTask == nil)
}

@Test @MainActor func compactReadingPausesAndLatestRestoresTheCurrentPage() throws {
    let h = try ViewportHarness()
    defer { h.coordinator.stop() }
    try h.advance(90)
    h.scroll.contentView.scroll(to: NSPoint(x: 40, y: 0))
    NotificationCenter.default.post(name: NSView.boundsDidChangeNotification, object: h.scroll.contentView)
    #expect(!h.store.followsLatest)
    try h.advance(140)
    #expect(abs(h.scroll.contentView.bounds.minX - 40) < 0.001)
    h.store.latest(); try h.render()
    #expect(h.store.followsLatest && h.coordinator.page?.index == 2)
    #expect(abs(h.scroll.contentView.bounds.minX - 347 * 1.5) < 0.001)
    h.document.onInteraction?()
    #expect(!h.store.followsLatest)
}

@Test @MainActor func unchangedAppKitBoundsNotificationDoesNotPauseFollowing() throws {
    let h = try ViewportHarness()
    defer { h.coordinator.stop() }
    try h.advance(61)
    NotificationCenter.default.post(name: NSView.boundsDidChangeNotification, object: h.scroll.contentView)
    #expect(h.store.followsLatest)
    h.scroll.contentView.scroll(to: NSPoint(x: 40, y: 0))
    NotificationCenter.default.post(name: NSView.boundsDidChangeNotification, object: h.scroll.contentView)
    #expect(!h.store.followsLatest)
}

@Test @MainActor func compactResizeAndZoomRecomputePagesAndKeepReadingAnchors() throws {
    let h = try ViewportHarness()
    defer { h.coordinator.stop() }
    try h.advance(90)
    h.scroll.setFrameSize(NSSize(width: 702, height: 80))
    h.coordinator.resize(h.scroll, viewport: 702)
    #expect(h.coordinator.page?.seconds == 120 && h.coordinator.page?.index == 0)
    #expect(h.scroll.contentView.bounds.minX == 0)
    h.scroll.setFrameSize(NSSize(width: 355, height: 80))
    h.coordinator.resize(h.scroll, viewport: 355)
    h.store.zoom = .wide; try h.render()
    #expect(h.coordinator.page?.seconds == 120 && h.scroll.contentView.bounds.minX == 0)
    h.store.zoom = .detail; try h.render()
    #expect(h.coordinator.page?.seconds == 30 && h.coordinator.page?.index == 3)
    h.coordinator.pause(h.scroll)
    let layout = try #require(h.document.layout)
    let centerTime = layout.time(at: h.scroll.contentView.bounds.midX)
    h.store.zoom = .overview; try h.render()
    #expect(abs(h.document.layout!.time(at: h.scroll.contentView.bounds.midX) - centerTime) < 0.001)
    let leftTime = h.document.layout!.time(at: h.scroll.contentView.bounds.minX)
    h.scroll.setFrameSize(NSSize(width: 702, height: 80))
    h.coordinator.resize(h.scroll, viewport: 702)
    #expect(abs(h.document.layout!.time(at: h.scroll.contentView.bounds.minX) - leftTime) < 0.001)
    #expect(!h.store.followsLatest)
}

@Test @MainActor func completedPagingFreezesAtFinalTimeAndViewerKeepsContinuousFollowing() throws {
    let h = try ViewportHarness()
    defer { h.coordinator.stop() }
    try h.advance(61)
    h.store.bundle = try viewportBundle(seconds: 999, status: "completed", end: 61)
    try h.render()
    #expect(h.coordinator.page?.index == 1)
    #expect(h.store.bundle?.viewportFollowTime == 61000)
    let viewer = TimelineSurface.Coordinator(store: h.store)
    let scroll = TimelineScrollView(frame: NSRect(x: 0, y: 0, width: 600, height: 80))
    let document = TimelineDocumentView(); scroll.documentView = document
    defer { viewer.stop() }
    viewer.update(scroll, layout: try #require(h.store.layout), activities: [], agents: [], onSelect: { _ in })
    #expect(viewer.page == nil)
    #expect(abs(scroll.contentView.bounds.maxX - document.frame.width) < 0.001)
    #expect(h.coordinator.page?.seconds == 60)
    #expect(h.document.viewportWidth == 355)
}

@Suite(.serialized)
struct TimelineNativeViewportTests {
    @Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
    @MainActor func timelineReferenceWidthMatchesDefaultCompactPanel() async throws {
        _ = NSApplication.shared
        let store = try viewportStore()
        let host = NSHostingView(rootView: TimelinePanelView(store: store, presentation: .panelCompact, pollingEnabled: false)
            .padding(14).frame(width: 420).environment(PanelAuxiliaryState()))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 180),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        window.orderFront(nil)
        defer { window.orderOut(nil) }
        for _ in 0..<10 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(20)) }
        func surface(_ view: NSView) -> TimelineScrollView? {
            if let scroll = view as? TimelineScrollView { return scroll }
            return view.subviews.lazy.compactMap { surface($0) }.first
        }
        let scroll = try #require(surface(host))
        print("TIMELINE_REFERENCE_WIDTH=\(scroll.contentSize.width)")
        #expect(abs(scroll.contentSize.width - 355) < 0.5)
        #expect(abs(scroll.contentSize.width - TimelineLayout.menuViewportWidth) < 0.5)
    }

    @Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
    @MainActor func timelinePagingAnimationMovesRealBoundsAndUserCanInterrupt() async throws {
        _ = NSApplication.shared
        let h = try ViewportHarness()
        h.coordinator.reduceMotion = { false }
        let window = NSWindow(contentRect: h.scroll.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = h.scroll; window.orderFront(nil)
        defer { h.coordinator.stop(); window.orderOut(nil) }
        try h.advance(59)
        try h.advance(61)
        #expect(h.coordinator.animationTask != nil)
        try await Task.sleep(for: .milliseconds(70))
        let middle = h.scroll.contentView.bounds.minX
        #expect(middle > 0 && middle < 347 * 0.75)
        #expect(h.store.followsLatest)
        try h.advance(62)
        #expect(h.coordinator.animationTask != nil)
        h.coordinator.pause(h.scroll)
        let interrupted = h.scroll.contentView.bounds.minX
        #expect(h.coordinator.animationTask == nil && !h.store.followsLatest)
        try await Task.sleep(for: .milliseconds(160))
        #expect(abs(h.scroll.contentView.bounds.minX - interrupted) < 0.001)
        try h.advance(110)
        #expect(abs(h.scroll.contentView.bounds.minX - interrupted) < 0.001)
        h.store.latest(); try h.render()
        #expect(h.coordinator.animationTask == nil && h.coordinator.page?.index == 2)
        try h.advance(151)
        #expect(h.coordinator.animationTask != nil)
        try await Task.sleep(for: .milliseconds(230))
        #expect(h.coordinator.animationTask == nil && h.store.followsLatest)
        #expect(abs(h.scroll.contentView.bounds.minX - 347 * 2.25) < 0.001)
        h.coordinator.reduceMotion = { true }
        try h.advance(196)
        #expect(h.coordinator.animationTask == nil)
        #expect(abs(h.scroll.contentView.bounds.minX - 347 * 3) < 0.001)
        print("TIMELINE_ANIMATION middle=\(middle) interrupted=\(interrupted) final=\(h.scroll.contentView.bounds.minX)")
    }

    @Test(.enabled(if: ProcessInfo.processInfo.environment["MONITOR_WINDOW_TESTS"] == "1"))
    @MainActor func timelinePagingAnimationCancelsOnGeometryPresentationAndStop() async throws {
        _ = NSApplication.shared
        let h = try ViewportHarness()
        h.coordinator.reduceMotion = { false }
        let window = NSWindow(contentRect: h.scroll.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = h.scroll; window.orderFront(nil)
        defer { h.coordinator.stop(); window.orderOut(nil) }
        try h.advance(59); try h.advance(61)
        #expect(h.coordinator.animationTask != nil)
        h.store.zoom = .wide; try h.render()
        #expect(h.coordinator.animationTask == nil && h.coordinator.page?.index == 0)
        try h.advance(121)
        #expect(h.coordinator.animationTask != nil)
        h.scroll.setFrameSize(NSSize(width: 702, height: 80))
        h.coordinator.resize(h.scroll, viewport: 702)
        #expect(h.coordinator.animationTask == nil && h.coordinator.page?.seconds == 240)
        h.scroll.setFrameSize(NSSize(width: 355, height: 80))
        h.coordinator.resize(h.scroll, viewport: 355)
        try h.advance(211)
        #expect(h.coordinator.animationTask != nil)
        h.coordinator.animationsEnabled = false
        #expect(h.coordinator.animationTask == nil)
        h.coordinator.animationsEnabled = true
        h.store.latest(); try h.render()
        try h.advance(301)
        #expect(h.coordinator.animationTask != nil)
        h.coordinator.stop()
        let stopped = h.scroll.contentView.bounds.minX
        try await Task.sleep(for: .milliseconds(230))
        // AppKit may align the clip to a device pixel on the next layout pass.
        #expect(abs(h.scroll.contentView.bounds.minX - stopped) <= 0.500001)
    }
}
