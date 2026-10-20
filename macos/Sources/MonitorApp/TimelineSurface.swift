import AppKit
import SwiftUI
import MonitorModel

@MainActor enum TimelinePalette {
    private static func shade(_ light: UInt32, _ dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let value = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: Double((value >> 16) & 255) / 255,
                           green: Double((value >> 8) & 255) / 255, blue: Double(value & 255) / 255, alpha: 1)
        }
    }
    private static let shades: [String: NSColor] = [
        "reasoning": shade(0x8968B3, 0x9A80C0), "text": shade(0x7957AC, 0xB097D1),
        "tool_input": shade(0x6A429E, 0xC3A2E1), "file_edit_input": shade(0x4F2B77, 0xDCBAF1),
        "tool_execution": shade(0x965916, 0xD49B57), "contextCompaction": shade(0x567D85, 0x82ACB4)]
    static let toolOpacity: Double = 0.72
    static var selectionColor: NSColor { NSColor.systemBlue.withAlphaComponent(0.16) }
    static func color(for kind: String) -> NSColor { shades[kind] ?? .secondaryLabelColor }
    static func color(_ phase: TimelinePhase) -> NSColor {
        color(for: phase.kind)
    }
}

@MainActor enum TimelineDecoration {
    static func selectionRect(for rect: CGRect) -> CGRect {
        let width = max(11, rect.width + 8)
        return CGRect(x: rect.midX - width / 2, y: rect.minY - 3, width: width, height: rect.height + 6)
    }

    static func statusSymbol(for phase: TimelinePhase) -> String? {
        switch phase.status {
        case "failed": "xmark"
        case "declined": "hand.raised.fill"
        default: nil
        }
    }

    static func statusColor(for phase: TimelinePhase) -> NSColor {
        phase.status == "failed" ? .systemRed : .secondaryLabelColor
    }

    static func statusRect(for phase: TimelinePhase, layout: TimelineLayout, viewport: CGRect) -> CGRect? {
        guard statusSymbol(for: phase) != nil, let end = phase.end_ms, end.isFinite,
              phase.duration(at: layout.now) != nil, let rect = layout.rect(for: phase), rect.width >= 12 else { return nil }
        let endX = layout.x(at: end)
        let badge = CGRect(x: endX - 10, y: rect.midY - 4, width: 8, height: 8)
        // Never pin a result to the viewport edge or stretch a short source interval.
        guard endX >= viewport.minX, endX <= viewport.maxX, rect.contains(badge), viewport.contains(badge) else { return nil }
        return badge
    }
}

@MainActor final class TimelineScrollView: NSScrollView {
    var onViewportChange: ((Double) -> Void)?
    override func layout() {
        super.layout()
        if contentSize.width > 1 { onViewportChange?(contentSize.width) }
    }
    override func scrollWheel(with event: NSEvent) {
        if abs(event.scrollingDeltaY) > abs(event.scrollingDeltaX), !event.modifierFlags.contains(.shift) {
            var ancestor = superview
            while let view = ancestor {
                if let scroll = view as? NSScrollView, scroll.hasVerticalScroller {
                    scroll.scrollWheel(with: event)
                    return
                }
                ancestor = view.superview
            }
            nextResponder?.scrollWheel(with: event)
            return
        }
        super.scrollWheel(with: event)
    }
}

@MainActor
struct TimelineSurface: NSViewRepresentable {
    let store: TimelineStore
    let layout: TimelineLayout
    let activities: [TimelinePhase]
    var agents: [TimelineAgent] = []
    var onSelect: ([TimelinePhase]) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(store: store) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = TimelineScrollView()
        scroll.drawsBackground = false
        scroll.hasHorizontalScroller = true; scroll.hasVerticalScroller = false
        scroll.scrollerStyle = .overlay
        scroll.horizontalScroller?.controlSize = .small
        scroll.autohidesScrollers = true; scroll.borderType = .noBorder
        scroll.horizontalScrollElasticity = .none; scroll.verticalScrollElasticity = .none
        let document = TimelineDocumentView()
        document.setAccessibilityRole(.group)
        document.setAccessibilityLabel(L("活动时间线，左右方向键选择活动，回车打开"))
        scroll.documentView = document
        scroll.contentView.postsBoundsChangedNotifications = true
        context.coordinator.observe(scroll)
        scroll.onViewportChange = { [weak scroll, weak coordinator = context.coordinator] width in
            if let scroll { coordinator?.resize(scroll, viewport: width) }
        }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let document = scroll.documentView as? TimelineDocumentView else { return }
        let coordinator = context.coordinator
        coordinator.updating = true
        defer { coordinator.updating = false }
        let old = document.layout
        let changedTurn = document.turn != store.selectedTurn
        let viewport = scroll.contentSize.width > 1 ? scroll.contentSize.width : document.viewportWidth
        let resized = abs(document.viewportWidth - viewport) > 0.5
        let oldOffset = resized ? store.scrollOffset : scroll.contentView.bounds.minX
        document.layout = layout; document.activities = activities; document.viewportWidth = viewport
        document.agents = agents
        document.selectedIDs = store.selectedPhaseIDs; document.turn = store.selectedTurn
        document.onSelect = { [weak scroll] phase in
            if let scroll { store.scrollOffset = scroll.contentView.bounds.minX }
            onSelect(phase)
        }
        document.onInteraction = { [weak scroll] in
            if let scroll { store.scrollOffset = scroll.contentView.bounds.minX }
            store.followsLatest = false
        }
        var offset = changedTurn ? store.scrollOffset : oldOffset
        if !store.followsLatest, !changedTurn, let old {
            offset = layout.readingOffset(from: old, offset: oldOffset, selectedID: store.selectedPhaseID, viewport: viewport)
        }
        document.resizeDocument(preservingOffset: store.followsLatest ? 0 : offset)
        let maximum = max(0, document.frame.width - viewport)
        if store.followsLatest { offset = maximum }
        scroll.contentView.scroll(to: NSPoint(x: min(maximum, max(0, offset)), y: 0))
        scroll.reflectScrolledClipView(scroll.contentView)
        document.needsDisplay = true; document.refreshAccessibility()
    }

    static func dismantleNSView(_ nsView: NSScrollView, coordinator: Coordinator) {
        coordinator.stop()
        if let document = nsView.documentView as? TimelineDocumentView {
            document.onSelect = nil; document.onInteraction = nil
        }
    }

    @MainActor final class Coordinator {
        let store: TimelineStore
        var updating = false
        var observation: NSObjectProtocol?
        init(store: TimelineStore) { self.store = store }
        func resize(_ scroll: NSScrollView, viewport: Double) {
            guard !updating, let document = scroll.documentView as? TimelineDocumentView, document.layout != nil else { return }
            guard abs(document.viewportWidth - viewport) > 0.5 else { document.refreshAccessibility(); return }
            updating = true
            defer { updating = false }
            let readingOffset = store.followsLatest ? 0 : store.scrollOffset
            document.viewportWidth = viewport; document.resizeDocument(preservingOffset: readingOffset)
            let maximum = max(0, document.frame.width - viewport)
            let offset = store.followsLatest ? maximum : readingOffset
            scroll.contentView.scroll(to: CGPoint(x: min(maximum, max(0, offset)), y: 0))
            scroll.reflectScrolledClipView(scroll.contentView)
            document.needsDisplay = true; document.refreshAccessibility()
        }
        func observe(_ scroll: NSScrollView) {
            observation = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
                object: scroll.contentView, queue: .main) { [weak self, weak scroll] _ in
                    MainActor.assumeIsolated {
                        guard let self, let scroll else { return }
                        let document = scroll.documentView as? TimelineDocumentView
                        document?.needsDisplay = true; document?.refreshAccessibility()
                        guard !self.updating, let document,
                              abs(document.viewportWidth - scroll.contentSize.width) <= 0.5 else { return }
                        self.store.scrollOffset = scroll.contentView.bounds.minX
                        let maximum = max(0, document.frame.width - scroll.contentSize.width)
                        // Our layout changes are guarded by `updating`; external scrolling,
                        // including accessibility actions, preserves the reading position.
                        if self.store.scrollOffset < maximum - 3 { self.store.followsLatest = false }
                    }
                }
        }
        func stop() { if let observation { NotificationCenter.default.removeObserver(observation) }; observation = nil }
    }
}

// AppKit can call accessibility actions off-main. Transfer only a locked Sendable callback.
final class TimelineAccessibleElement: NSAccessibilityElement, @unchecked Sendable {
    private let actionLock = NSLock()
    private var action: (@MainActor @Sendable () -> Void)?

    func setAction(_ callback: @escaping @MainActor @Sendable () -> Void) {
        actionLock.withLock { action = callback }
    }

    override func accessibilityPerformPress() -> Bool {
        guard let callback = actionLock.withLock({ action }) else { return false }
        if Thread.isMainThread {
            MainActor.assumeIsolated { callback() }
        } else {
            Task { @MainActor in callback() }
        }
        return true
    }
}

@MainActor final class TimelineDocumentView: NSView {
    var layout: TimelineLayout?
    var activities: [TimelinePhase] = []
    var agents: [TimelineAgent] = []
    var selectedIDs: Set<String> = [] {
        didSet { if oldValue != selectedIDs { keyboardID = nil } }
    }
    var turn: String? {
        didSet { if oldValue != turn { keyboardID = nil } }
    }
    var onSelect: (([TimelinePhase]) -> Void)?
    var onInteraction: (() -> Void)?
    var viewportWidth = TimelineLayout.menuViewportWidth
    private var readingOffset: Double = 0
    private var keyboardID: String?
    private var tracking: NSTrackingArea?
    private var accessibleElements: [String: TimelineAccessibleElement] = [:]
    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }
    var highlightedIDs: Set<String> { keyboardID.map { Set([$0]) } ?? selectedIDs }

    private func activityLabel(_ phase: TimelinePhase) -> String {
        let agent = layout?.lanes.first {
            $0.threadID == phase.thread_id && ($0.track == "header" || $0.track == "summary")
        }?.label ?? L("主智能体")
        let partial = phase.start_ms == nil || !(phase.quality ?? []).isEmpty
        let targets = phase.isCoordination ? phase.coordinationTargetDetails(in: agents) : ""
        return agent + " · " + phase.accessibleLabel(at: layout?.now ?? 0, agents: agents)
            + (partial ? " · " + L("部分观测") : "") + (targets.isEmpty ? "" : "\n" + targets)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let layout else { return }
        let visible = visibleRect.intersection(dirtyRect).insetBy(dx: -5, dy: 0)
        let tick = layout.tickSeconds
        let lower = floor((layout.time(at: visible.minX) - layout.origin) / 1000 / tick) * tick
        let upper = (layout.time(at: visible.maxX) - layout.origin) / 1000
        for second in stride(from: max(0, lower), through: max(0, upper), by: tick) {
            let x = layout.x(at: layout.origin + second * 1000)
            NSColor.separatorColor.withAlphaComponent(0.25).setStroke()
            let line = NSBezierPath()
            line.move(to: NSPoint(x: x, y: 17)); line.line(to: NSPoint(x: x, y: TimelineLayout.axisHeight))
            var y = TimelineLayout.axisHeight
            for lane in layout.lanes {
                if lane.track != "header" {
                    line.move(to: NSPoint(x: x, y: y)); line.line(to: NSPoint(x: x, y: y + lane.height))
                }
                y += lane.height
            }
            line.stroke()
            let text = second >= 60 ? "\(Int(second / 60)):\(String(format: "%02d", Int(second) % 60))" : "\(Int(second))s"
            (text as NSString).draw(at: NSPoint(x: x + 3, y: 1), withAttributes: [
                .font: NSFont.monospacedDigitSystemFont(ofSize: 9, weight: .regular), .foregroundColor: NSColor.secondaryLabelColor])
        }
        let phases = activities.filter { layout.rect(for: $0)?.intersects(visible) == true }
        let selection = NSBezierPath()
        for phase in phases where highlightedIDs.contains(phase.id) {
            guard let rect = layout.rect(for: phase) else { continue }
            selection.append(NSBezierPath(roundedRect: TimelineDecoration.selectionRect(for: rect), xRadius: 4, yRadius: 4))
        }
        TimelinePalette.selectionColor.setFill(); selection.fill()
        // Each source interval paints only itself. Alpha composition emphasizes local
        // overlap and naturally saturates; no connected-component grouping is needed.
        for phase in phases {
            guard let rect = layout.rect(for: phase) else { continue }
            let opacity = phase.status == "unknown" ? 0.25 : phase.start_ms == nil ? 0.40 : phase.displayTrack == "tool" ? TimelinePalette.toolOpacity : 0.95
            TimelinePalette.color(phase).withAlphaComponent(opacity).setFill()
            path(for: phase, in: rect).fill()
        }
        for phase in phases {
            if phase.isCoordination, let rect = layout.rect(for: phase), rect.width >= 16 {
                let configuration = NSImage.SymbolConfiguration(pointSize: 7, weight: .medium)
                    .applying(NSImage.SymbolConfiguration(paletteColors: [NSColor.labelColor]))
                NSImage(systemSymbolName: "person.2", accessibilityDescription: nil)?.withSymbolConfiguration(configuration)?
                    .draw(in: CGRect(x: rect.minX + 2, y: rect.midY - 4, width: 11, height: 8), from: .zero,
                          operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
            }
            guard let name = TimelineDecoration.statusSymbol(for: phase),
                  let rect = TimelineDecoration.statusRect(for: phase, layout: layout, viewport: visibleRect) else { continue }
            let configuration = NSImage.SymbolConfiguration(pointSize: 8, weight: .semibold)
                .applying(NSImage.SymbolConfiguration(paletteColors: [TimelineDecoration.statusColor(for: phase)]))
            NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(configuration)?
                .draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
        }
    }

    private func path(for phase: TimelinePhase, in rect: CGRect) -> NSBezierPath {
        if phase.displayTrack == "marker" {
            let path = NSBezierPath(); path.move(to: NSPoint(x: rect.midX, y: rect.minY))
            path.line(to: NSPoint(x: rect.maxX, y: rect.midY)); path.line(to: NSPoint(x: rect.midX, y: rect.maxY))
            path.line(to: NSPoint(x: rect.minX, y: rect.midY)); path.close(); return path
        }
        return NSBezierPath(roundedRect: rect, xRadius: min(2, rect.width / 2), yRadius: 2)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        tracking = NSTrackingArea(rect: .zero, options: [.activeInKeyWindow, .mouseMoved, .mouseEnteredAndExited, .inVisibleRect], owner: self)
        if let tracking { addTrackingArea(tracking) }
    }
    override func mouseMoved(with event: NSEvent) {
        let selected = selection(at: convert(event.locationInWindow, from: nil))
        toolTip = selected.isEmpty ? nil : selected.prefix(4).map(activityLabel).joined(separator: "\n")
    }
    override func mouseExited(with event: NSEvent) { toolTip = nil }
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        activate(at: convert(event.locationInWindow, from: nil))
    }
    func activate(at point: CGPoint) {
        keyboardID = nil
        onInteraction?()
        let selected = selection(at: point)
        if !selected.isEmpty { onSelect?(selected) }
    }
    private func selection(at point: CGPoint) -> [TimelinePhase] {
        guard let layout else { return [] }
        let hit = layout.hitTest(point, phases: activities)
        let tools = hit.filter { $0.displayTrack == "tool" }
        if !tools.isEmpty { return tools }
        // Keep serial model stages independent even when their minimum pixels touch.
        let nearest = hit.min { a, b in
            guard let ra = layout.rect(for: a), let rb = layout.rect(for: b) else { return false }
            return (hypot(point.x - ra.midX, point.y - ra.midY), a.anchor ?? 0, a.id)
                 < (hypot(point.x - rb.midX, point.y - rb.midY), b.anchor ?? 0, b.id)
        }
        return nearest.map { [$0] } ?? []
    }

    func resizeDocument(preservingOffset offset: Double? = nil) {
        guard let layout else { return }
        if let offset { readingOffset = max(0, offset) }
        setFrameSize(NSSize(width: max(layout.width(viewport: viewportWidth), readingOffset + viewportWidth), height: layout.height))
    }
    override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); refreshAccessibility() }
    override func keyDown(with event: NSEvent) {
        let ordered = activities.filter { layout?.rect(for: $0) != nil }.sorted { ($0.anchor ?? 0, $0.id) < ($1.anchor ?? 0, $1.id) }
        guard !ordered.isEmpty else { return }
        if [123, 124, 125, 126].contains(event.keyCode) {
            let currentID = keyboardID ?? ordered.first(where: { selectedIDs.contains($0.id) })?.id
            let index = ordered.firstIndex { $0.id == currentID } ?? (event.keyCode == 123 || event.keyCode == 126 ? ordered.count : -1)
            let delta = event.keyCode == 123 || event.keyCode == 126 ? -1 : 1
            let phase = ordered[max(0, min(ordered.count - 1, index + delta))]
            keyboardID = phase.id; onInteraction?()
            if let rect = layout?.rect(for: phase) { scrollToVisible(rect.insetBy(dx: -10, dy: 0)) }
            setAccessibilityValue(activityLabel(phase)); needsDisplay = true; refreshAccessibility()
        } else if event.keyCode == 36 || event.keyCode == 49 {
            if let phase = ordered.first(where: { $0.id == keyboardID }) ?? ordered.first { onSelect?([phase]) }
        } else { super.keyDown(with: event) }
    }

    func refreshAccessibility() {
        guard let layout, let window else { return }
        var children: [TimelineAccessibleElement] = []
        var visibleIDs: Set<String> = []
        for phase in activities {
            guard let rect = layout.hitRect(for: phase), rect.intersects(visibleRect) else { continue }
            visibleIDs.insert(phase.id)
            let element = accessibleElements[phase.id] ?? TimelineAccessibleElement()
            accessibleElements[phase.id] = element
            element.setAccessibilityRole(.button); element.setAccessibilityLabel(activityLabel(phase))
            element.setAccessibilityEnabled(true); element.setAccessibilityIdentifier("monitor.activity." + phase.id)
            element.setAccessibilityParent(self); element.setAccessibilityFrame(window.convertToScreen(convert(rect, to: nil)))
            element.setAccessibilitySelected(highlightedIDs.contains(phase.id))
            element.setAction { [weak self] in
                self?.keyboardID = nil; self?.onInteraction?(); self?.onSelect?([phase])
            }; children.append(element)
        }
        accessibleElements = accessibleElements.filter { visibleIDs.contains($0.key) }
        setAccessibilityChildren(children)
    }
}
