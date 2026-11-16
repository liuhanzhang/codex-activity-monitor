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
        "reasoning": shade(0x8B6CC1, 0xB49ADC), "text": shade(0x27847B, 0x69B9AF),
        "tool_input": shade(0x427DBB, 0x82ACE2), "file_edit_input": shade(0xB65D91, 0xD796B7),
        "tool_execution": shade(0x965916, 0xD49B57), "contextCompaction": shade(0x6F7886, 0xA4ABB6)]
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
    var onInteraction: (() -> Void)?
    var onDetach: (() -> Void)?
    var viewportDiagnostics: (() -> [String: Any])?
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window == nil { onDetach?() }
    }
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
        onInteraction?()
        super.scrollWheel(with: event)
    }
}

@MainActor
struct TimelineSurface: NSViewRepresentable, LocalizedView {
    @Environment(\.monitorLanguage) var language
    let store: TimelineStore
    let layout: TimelineLayout
    let activities: [TimelinePhase]
    var agents: [TimelineAgent] = []
    var pagedFollowing = false
    var animationsEnabled = true
    var onSelect: ([TimelinePhase]) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(store: store, pagedFollowing: pagedFollowing) }
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
        scroll.onInteraction = { [weak scroll, weak coordinator = context.coordinator] in
            if let scroll { coordinator?.pause(scroll) }
        }
        scroll.onDetach = { [weak coordinator = context.coordinator] in coordinator?.cancelAnimation() }
        scroll.viewportDiagnostics = { [weak scroll, weak coordinator = context.coordinator] in
            guard let scroll, let coordinator else { return [:] }
            return ["width": scroll.contentSize.width, "offset": scroll.contentView.bounds.minX,
                    "document_width": scroll.documentView?.frame.width ?? 0,
                    "page_index": coordinator.page?.index ?? -1,
                    "seconds": coordinator.page?.seconds ?? -1,
                    "points_per_second": coordinator.store.zoom.pointsPerSecond,
                    "follows_latest": coordinator.store.followsLatest,
                    "animating": coordinator.animationTask != nil]
        }
        scroll.onViewportChange = { [weak scroll, weak coordinator = context.coordinator] width in
            if let scroll { coordinator?.resize(scroll, viewport: width) }
        }
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        if let document = scroll.documentView as? TimelineDocumentView {
            document.language = language
            document.setAccessibilityLabel(L("活动时间线，左右方向键选择活动，回车打开"))
        }
        context.coordinator.animationsEnabled = animationsEnabled
        context.coordinator.update(scroll, layout: layout, activities: activities, agents: agents, onSelect: onSelect)
    }

    static func dismantleNSView(_ nsView: NSScrollView, coordinator: Coordinator) {
        coordinator.stop()
        if let scroll = nsView as? TimelineScrollView {
            scroll.onViewportChange = nil; scroll.onInteraction = nil; scroll.onDetach = nil
            scroll.viewportDiagnostics = nil
        }
        if let document = nsView.documentView as? TimelineDocumentView {
            document.onSelect = nil; document.onInteraction = nil
        }
    }

    @MainActor final class Coordinator {
        let store: TimelineStore
        let pagedFollowing: Bool
        var updating = false
        var observation: NSObjectProtocol?
        private var mouseMonitor: Any?
        var animationsEnabled = true { didSet { if !animationsEnabled { cancelAnimation() } } }
        var reduceMotion: () -> Bool = { NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }
        private(set) var page: TimelineViewport?
        private(set) var animationTask: Task<Void, Never>?
        private var readingOffset: Double?
        private var lastRevision: Int?
        private var wasFollowing = false
        private var lastUpdate: ContinuousClock.Instant?

        init(store: TimelineStore, pagedFollowing: Bool = false) {
            self.store = store; self.pagedFollowing = pagedFollowing
        }

        func update(_ scroll: NSScrollView, layout: TimelineLayout, activities: [TimelinePhase],
                    agents: [TimelineAgent], onSelect: @escaping ([TimelinePhase]) -> Void) {
            guard let document = scroll.documentView as? TimelineDocumentView else { return }
            updating = true
            defer { updating = false }
            let old = document.layout
            let changedTurn = document.turn != store.bundle?.selected_turn_id
            let viewport = scroll.contentSize.width > 1 ? scroll.contentSize.width : document.viewportWidth
            let resized = abs(document.viewportWidth - viewport) > 0.5
            let changedGeometry = resized || old?.origin != layout.origin || old?.pointsPerSecond != layout.pointsPerSecond
            let reset = changedTurn || lastRevision != store.viewportRevision
            let clock = ContinuousClock.now
            let delayed = lastUpdate.map { $0.duration(to: clock) > .seconds(2.5) } ?? true
            lastUpdate = clock
            let oldOffset = resized ? (readingOffset ?? store.scrollOffset) : scroll.contentView.bounds.minX
            if reset || changedGeometry || !store.followsLatest { cancelAnimation() }
            if reset || changedGeometry { page = nil }
            document.layout = layout; document.activities = activities; document.viewportWidth = viewport
            document.agents = agents; document.selectedIDs = store.selectedPhaseIDs
            document.turn = store.bundle?.selected_turn_id
            document.onSelect = { [weak scroll, weak self] phases in
                if let scroll { self?.pause(scroll) }
                onSelect(phases)
            }
            document.onInteraction = { [weak scroll, weak self] in
                if let scroll { self?.pause(scroll) }
            }
            var offset = changedTurn ? store.scrollOffset : oldOffset
            var animate = false
            let priorPage = page
            if store.followsLatest, pagedFollowing {
                if let time = store.bundle?.viewportFollowTime {
                    page = TimelineViewport(origin: layout.origin, time: time, viewport: viewport,
                                            pointsPerSecond: layout.pointsPerSecond, previous: page)
                }
                if let page {
                    offset = page.offset
                    document.minimumTimelineWidth = offset + viewport
                    animate = !reset && !changedGeometry && !delayed && wasFollowing &&
                        priorPage.map { page.hasSameGeometry(as: $0) && page.index == $0.index + 1 } == true
                } else {
                    // Unreliable/missing time never falls back to the continuously moving history edge.
                    document.minimumTimelineWidth = max(viewport, offset + viewport)
                }
            } else {
                if !store.followsLatest, !changedTurn, let old {
                    offset = layout.readingOffset(from: old, offset: oldOffset,
                                                  selectedID: store.selectedPhaseID, viewport: viewport)
                }
                if reset { document.minimumTimelineWidth = 0 }
            }
            document.resizeDocument(preservingOffset: store.followsLatest ? 0 : offset)
            let maximum = max(0, document.frame.width - viewport)
            if store.followsLatest && !pagedFollowing { offset = maximum }
            offset = min(maximum, max(0, offset))
            // A same-page refresh must neither restart nor jump ahead of an in-flight animation.
            if animationTask == nil || animate || priorPage != page {
                move(scroll, to: offset, animated: animate)
            }
            lastRevision = store.viewportRevision; wasFollowing = store.followsLatest
            document.needsDisplay = true; document.refreshAccessibility()
        }

        func resize(_ scroll: NSScrollView, viewport: Double) {
            guard !updating, let document = scroll.documentView as? TimelineDocumentView,
                  let layout = document.layout, viewport > 8, viewport.isFinite else { return }
            guard abs(document.viewportWidth - viewport) > 0.5 else { return }
            updating = true
            defer { updating = false }
            cancelAnimation()
            var offset = readingOffset ?? store.scrollOffset
            document.viewportWidth = viewport
            if store.followsLatest && pagedFollowing {
                page = store.bundle?.viewportFollowTime.flatMap {
                    TimelineViewport(origin: layout.origin, time: $0, viewport: viewport,
                                     pointsPerSecond: layout.pointsPerSecond)
                }
                if let page { offset = page.offset }
                document.minimumTimelineWidth = offset + viewport
            }
            document.resizeDocument(preservingOffset: store.followsLatest ? 0 : offset)
            let maximum = max(0, document.frame.width - viewport)
            if store.followsLatest && !pagedFollowing { offset = maximum }
            setOffset(scroll, min(maximum, max(0, offset)))
        }

        func pause(_ scroll: NSScrollView) {
            cancelAnimation()
            readingOffset = scroll.contentView.bounds.minX
            store.scrollOffset = scroll.contentView.bounds.minX
            store.followsLatest = false; wasFollowing = false
        }

        private func setOffset(_ scroll: NSScrollView, _ offset: Double) {
            let prior = updating; updating = true
            defer { updating = prior }
            scroll.contentView.scroll(to: NSPoint(x: offset, y: 0))
            scroll.reflectScrolledClipView(scroll.contentView)
            readingOffset = scroll.contentView.bounds.minX
            if let document = scroll.documentView as? TimelineDocumentView {
                document.needsDisplay = true; document.refreshAccessibility()
            }
        }

        private func move(_ scroll: NSScrollView, to offset: Double, animated: Bool) {
            cancelAnimation()
            guard animated, animationsEnabled, !reduceMotion(), scroll.window?.isVisible == true,
                  abs(scroll.contentView.bounds.minX - offset) > 0.5 else {
                setOffset(scroll, offset); return
            }
            let start = ContinuousClock.now
            let from = scroll.contentView.bounds.minX
            animationTask = Task { @MainActor [weak self, weak scroll] in
                while !Task.isCancelled {
                    guard let self, let scroll else { return }
                    guard scroll.window?.isVisible == true else { self.cancelAnimation(); return }
                    let elapsed = start.duration(to: .now)
                    let seconds = Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18
                    let progress = min(1, seconds / 0.18)
                    let eased = 1 - pow(1 - progress, 3)
                    self.setOffset(scroll, self.reduceMotion() ? offset : from + (offset - from) * eased)
                    if progress >= 1 || self.reduceMotion() { self.animationTask = nil; return }
                    do { try await Task.sleep(for: .milliseconds(16)) } catch { return }
                }
            }
        }

        func cancelAnimation() { animationTask?.cancel(); animationTask = nil }

        func observe(_ scroll: NSScrollView) {
            // Observe the press before AppKit's native scroller enters its tracking loop.
            // No NSScroller subclass: native overlay metrics, hover and drawing remain intact.
            mouseMonitor = NSEvent.addLocalMonitorForEvents(matching: .leftMouseDown) { [weak self, weak scroll] event in
                MainActor.assumeIsolated {
                    if let self, let scroll, let window = scroll.window, window.isVisible, event.window === window,
                       scroll.bounds.contains(scroll.convert(event.locationInWindow, from: nil)) {
                        self.pause(scroll)
                    }
                }
                return event
            }
            observation = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
                object: scroll.contentView, queue: .main) { [weak self, weak scroll] _ in
                    MainActor.assumeIsolated {
                        guard let self, let scroll else { return }
                        let document = scroll.documentView as? TimelineDocumentView
                        document?.needsDisplay = true; document?.refreshAccessibility()
                        guard !self.updating, let document,
                              abs(document.viewportWidth - scroll.contentSize.width) <= 0.5 else { return }
                        if let offset = self.readingOffset,
                           abs(scroll.contentView.bounds.minX - offset) <= 0.5 { return }
                        // Every programmatic frame is guarded synchronously by setOffset.
                        // An external bounds change, including accessibility scrolling, is user takeover.
                        self.pause(scroll)
                    }
                }
        }
        func stop() {
            cancelAnimation()
            if let observation { NotificationCenter.default.removeObserver(observation) }
            observation = nil
            if let mouseMonitor { NSEvent.removeMonitor(mouseMonitor) }
            mouseMonitor = nil
        }
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
    var language: MonitorLanguage = .chinese
    private func L(_ key: String) -> String { localizedText(key, language: language) }
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
    var minimumTimelineWidth: Double = 0
    private var readingOffset: Double = 0
    private var keyboardID: String?
    private var tracking: NSTrackingArea?
    private var accessibleElements: [String: TimelineAccessibleElement] = [:]
    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }
    var highlightedIDs: Set<String> { keyboardID.map { Set([$0]) } ?? selectedIDs }

    func activityLabel(_ phase: TimelinePhase) -> String {
        let owner = agents.first { $0.thread_id == phase.thread_id }
        let isChild = layout?.lanes.first { $0.threadID == phase.thread_id }?.isChild == true
        let agent = isChild ? (owner?.displayName ?? L("子智能体") + " · " + String(phase.thread_id.suffix(8))) : L("主智能体")
        let partial = phase.start_ms == nil || !(phase.quality ?? []).isEmpty
        let targets = phase.isCoordination ? phase.coordinationTargetDetails(in: agents, language: language) : ""
        return agent + " · " + phase.accessibleLabel(at: layout?.now ?? 0, agents: agents, language: language)
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
        setFrameSize(NSSize(width: max(layout.width(viewport: viewportWidth), readingOffset + viewportWidth, minimumTimelineWidth), height: layout.height))
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
