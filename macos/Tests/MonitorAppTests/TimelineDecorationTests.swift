import AppKit
import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

private func decorationPhase(_ fields: [String: Any] = [:]) throws -> TimelinePhase {
    let base: [String: Any] = ["id": "tool", "scope_id": "scope", "thread_id": "root", "turn_id": "turn",
        "item_id": "tool", "namespace": "canonical", "track": "tool", "kind": "tool_execution",
        "label": "运行命令", "status": "failed", "start_ms": 1000, "end_ms": 21000]
    return try JSONDecoder().decode(TimelinePhase.self,
        from: JSONSerialization.data(withJSONObject: base.merging(fields) { _, next in next }))
}

private func decorationLayout(_ phases: [TimelinePhase]) -> TimelineLayout {
    TimelineLayout(origin: 0, end: 90000, now: 90000, lanes: [
        TimelineLane(id: "tool", threadID: "root", track: "tool", label: "工具", height: 21, isChild: false)
    ], activities: phases)
}

@Test @MainActor func statusIconsRespectRecordedStateAndKeepIncompleteResultsUnknown() throws {
    for (state, symbol) in [("failed", "xmark"), ("declined", "hand.raised.fill")] {
        let phase = try decorationPhase(["status": state])
        #expect(TimelineDecoration.statusSymbol(for: phase) == symbol)
        #expect(NSImage(systemSymbolName: symbol, accessibilityDescription: nil) != nil)
    }
    for state in ["completed", "interrupted", "unknown", "active", "waiting", "future-status"] {
        #expect(TimelineDecoration.statusSymbol(for: try decorationPhase(["status": state])) == nil)
    }
    let blocked = try decorationPhase(["status": "declined", "start_ms": NSNull()])
    #expect(blocked.statusLabel == "已阻止，未执行")
    #expect(blocked.accessibleLabel(at: 90000).contains("已阻止，未执行"))
    #expect(blocked.duration(at: 90000) == nil)
}

@Test @MainActor func resultBadgeUsesRealEndAndDisappearsWhenEndIsOffscreenOrUnreliable() throws {
    let failed = try decorationPhase()
    let layout = decorationLayout([failed])
    let viewport = CGRect(x: 0, y: 0, width: 291, height: layout.height)
    let source = try #require(layout.rect(for: failed))
    let badge = try #require(TimelineDecoration.statusRect(for: failed, layout: layout, viewport: viewport))
    #expect(source.contains(badge))
    #expect(abs(badge.maxX - (layout.x(at: failed.end_ms!) - 2)) < 0.001)
    let clipped = CGRect(x: 0, y: 0, width: layout.x(at: failed.end_ms!) - 1, height: layout.height)
    #expect(TimelineDecoration.statusRect(for: failed, layout: layout, viewport: clipped) == nil)
    for fields: [String: Any] in [["end_ms": NSNull()], ["quality": ["clock_conflict"]], ["end_ms": 1001]] {
        let phase = try decorationPhase(fields)
        #expect(TimelineDecoration.statusRect(for: phase, layout: decorationLayout([phase]), viewport: viewport) == nil)
    }
    #expect(layout.rect(for: failed) == source)
}

@Test @MainActor func selectionBackingDoesNotEnlargeSourceGeometryOrMouseTargets() throws {
    let phase = try decorationPhase(["end_ms": 1001])
    let layout = decorationLayout([phase])
    let source = try #require(layout.rect(for: phase))
    let backing = TimelineDecoration.selectionRect(for: source)
    #expect(source.width == 3 && backing.width == 11)
    #expect(backing.midX == source.midX && backing.midY == source.midY)
    #expect(layout.hitRect(for: phase) == source)
    let view = TimelineDocumentView()
    view.layout = layout; view.activities = [phase]; view.selectedIDs = [phase.id]
    view.resizeDocument()
    var opens = 0
    view.onSelect = { _ in opens += 1 }
    view.activate(at: CGPoint(x: backing.minX + 0.5, y: source.midY))
    #expect(opens == 0)
    view.activate(at: CGPoint(x: source.midX, y: source.midY))
    #expect(opens == 1)
    #expect(layout.rect(for: phase) == source)
}

@Test @MainActor func keyboardAndMouseShareOneSelectionBacking() throws {
    let first = try decorationPhase(["id": "a", "end_ms": 4000])
    let next = try decorationPhase(["id": "b", "start_ms": 12000])
    let view = TimelineDocumentView()
    view.activities = [first, next]; view.layout = decorationLayout(view.activities)
    view.selectedIDs = [first.id]; view.resizeDocument()
    let right = try #require(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
        windowNumber: 0, context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: 124))
    view.keyDown(with: right)
    #expect(view.highlightedIDs == [next.id])
    let rect = try #require(view.layout?.rect(for: first))
    view.activate(at: CGPoint(x: rect.midX, y: rect.midY))
    #expect(view.highlightedIDs == [first.id])
    view.keyDown(with: right)
    view.selectedIDs = []
    #expect(view.highlightedIDs.isEmpty)
}
