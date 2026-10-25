import Foundation
import MonitorModel
import Observation

@MainActor @Observable
final class TimelineStore {
    let scope: String
    let thread: String
    let title: String
    let client: any TimelineReading
    var bundle: TimelineBundle?
    var error: String?
    var loading = false
    var selectedTurn: String?
    var followsCurrentTurn = true
    var expandedAgents: Set<String> = []
    var scrollOffset: Double = 0
    var followsLatest = true
    var zoom: TimelineZoom = .overview
    private(set) var viewportRevision = 0
    var selectedPhaseID: String?
    var selectedPhaseIDs: Set<String> = []
    var onOpen: ((TimelineStore, [TimelinePhase]) -> Void)?
    private var serial = 0
    private var panelPresentationID: Int?

    init(scope: String, thread: String, title: String, client: any TimelineReading) {
        self.scope = scope; self.thread = thread; self.title = title; self.client = client
    }

    func refresh() async {
        serial += 1
        let token = serial
        let requestedTurn = followsCurrentTurn ? nil : selectedTurn
        loading = bundle == nil
        defer { if token == serial { loading = false } }
        do {
            let next = try await client.timeline(scope: scope, thread: thread, turn: requestedTurn)
            guard !Task.isCancelled, token == serial else { return }
            guard next.scope_id == scope, next.thread_id == thread,
                  requestedTurn == nil || next.selected_turn_id == requestedTurn else {
                throw BackendFailure(message: "轨迹身份不匹配")
            }
            if followsCurrentTurn, selectedTurn != next.selected_turn_id {
                viewportRevision += 1
                followsLatest = true; scrollOffset = 0
                selectedPhaseID = nil; selectedPhaseIDs = []
            }
            bundle = next; selectedTurn = next.selected_turn_id; error = nil
        } catch is CancellationError { }
        catch {
            guard !Task.isCancelled, token == serial else { return }
            self.error = queryFailureKey(error, fallback: "暂时无法读取轨迹")
        }
    }

    func selectTurn(_ turn: String) {
        guard selectedTurn != turn else { return }
        viewportRevision += 1
        serial += 1
        selectedTurn = turn
        error = nil
        followsCurrentTurn = turn == bundle?.current_turn_id
        followsLatest = followsCurrentTurn
        scrollOffset = 0
        selectedPhaseID = nil; selectedPhaseIDs = []
    }

    func open(_ phase: TimelinePhase) { open([phase]) }

    func open(_ phases: [TimelinePhase]) {
        guard let first = phases.first else { return }
        selectedPhaseID = first.id; selectedPhaseIDs = Set(phases.map(\.id))
        followsLatest = false
        onOpen?(self, phases)
    }

    func latest() {
        viewportRevision += 1
        serial += 1
        followsCurrentTurn = true; followsLatest = true
        selectedTurn = bundle?.current_turn_id ?? bundle?.turns.first?.id
    }

    func prepareForPresentation() {
        guard let bundle,
              bundle.turns.contains(where: { $0.turn_id == bundle.current_turn_id && $0.status == "active" })
                || bundle.phases.contains(where: \.isActive) else { return }
        latest()
    }

    func prepareForPresentation(_ id: Int) {
        guard panelPresentationID != id else { return }
        panelPresentationID = id
        prepareForPresentation()
    }

    var lanes: [TimelineLane] { bundle.map { TimelineLane.lanes(in: $0, expanded: expandedAgents) } ?? [] }
    var switchingTurn: Bool { bundle != nil && selectedTurn != bundle?.selected_turn_id }
    var layout: TimelineLayout? {
        layout(for: .activityViewer)
    }
    func layout(for presentation: TimelinePresentation) -> TimelineLayout? {
        guard let bundle, let origin = bundle.origin_ms else { return nil }
        return TimelineLayout(origin: origin, end: bundle.end_ms ?? origin, now: bundle.now_ms,
                              lanes: TimelineLane.lanes(in: bundle, expanded: expandedAgents, presentation: presentation),
                              activities: bundle.allActivities, zoom: zoom)
    }
}

@MainActor @Observable
final class ActivityViewerStore {
    var phase: TimelinePhase
    let timeline: TimelineStore
    var detail: ActivityDetail?
    var selectedSection: String?
    var error: String?
    var loadingMore = false
    var followsContent = true
    private var serial = 0

    init(timeline: TimelineStore, phase: TimelinePhase) {
        self.timeline = timeline; self.phase = phase; followsContent = phase.isActive
    }

    func select(_ phase: TimelinePhase) {
        guard self.phase.id != phase.id else { return }
        serial += 1; self.phase = phase; detail = nil; selectedSection = nil; error = nil; loadingMore = false
        followsContent = phase.isActive; timeline.selectedPhaseID = phase.id; timeline.selectedPhaseIDs = [phase.id]
    }

    func refresh(reload: Bool = false) async {
        guard !loadingMore, reload || detail?.requiresReload != true else { return }
        serial += 1
        let token = serial, selected = phase
        do {
            var next = try await timeline.client.detail(phase: selected, page: nil)
            guard !Task.isCancelled, !loadingMore, token == serial, phase.id == selected.id else { return }
            next = try next.validated(for: selected)
            next = try next.reconcilingRefresh(from: detail, followsLive: followsContent && selected.isActive, reload: reload)
            detail = next; phase = next.phase ?? phase; error = nil
            if !next.sections.contains(where: { $0.id == selectedSection }) { selectedSection = next.sections.first?.id }
        } catch is CancellationError { }
        catch {
            guard !Task.isCancelled, token == serial else { return }
            self.error = queryFailureKey(error, fallback: "暂时无法读取活动内容")
        }
    }

    func loadMore() async {
        guard !loadingMore, let prior = detail,
              let cursor = prior.pageCursor(for: selectedSection) else { return }
        loadingMore = true
        serial += 1
        let token = serial, selected = phase
        defer { if token == serial { loadingMore = false } }
        do {
            let next = try await timeline.client.detail(phase: selected, page: cursor)
            guard !Task.isCancelled, token == serial, phase.id == selected.id else { return }
            detail = try prior.appendingPage(next, cursor: cursor)
            phase = next.phase ?? phase
            error = nil
        } catch is CancellationError { }
        catch {
            guard !Task.isCancelled, token == serial else { return }
            self.error = queryFailureKey(error, fallback: "暂时无法读取更多内容")
        }
    }
}

// Selection membership is fixed by the click. Each member retains its own content,
// paging, identity checks and request generation in ActivityViewerStore.
@MainActor @Observable
final class ActivityViewerGroupStore {
    let timeline: TimelineStore
    private(set) var entries: [ActivityViewerStore] = []
    var phase: TimelinePhase { entries[0].phase }
    var selectionID: String { entries.map { $0.phase.id }.joined(separator: ":") }

    init(timeline: TimelineStore, phases: [TimelinePhase]) {
        self.timeline = timeline
        precondition(!phases.isEmpty)
        select(phases)
    }

    func select(_ phases: [TimelinePhase]) {
        guard !phases.isEmpty else { return }
        var seen: Set<String> = []
        let selected = phases.filter { seen.insert($0.id).inserted }
            .sorted { ($0.anchor ?? 0, $0.id) < ($1.anchor ?? 0, $1.id) }
        let prior = Dictionary(uniqueKeysWithValues: entries.map { ($0.phase.id, $0) })
        entries = selected.map { prior[$0.id] ?? ActivityViewerStore(timeline: timeline, phase: $0) }
        timeline.selectedPhaseID = entries.first?.phase.id
        timeline.selectedPhaseIDs = Set(entries.map { $0.phase.id })
        for entry in entries where entry.phase.thread_id != timeline.thread {
            timeline.expandedAgents.insert(entry.phase.thread_id)
        }
    }

    func refresh() async {
        let pending = entries.filter {
            $0.detail == nil || $0.phase.isActive || ["pending", "partial", "content_unavailable"].contains($0.detail?.state ?? "")
        }
        await withTaskGroup(of: Void.self) { group in
            var next = 0
            func enqueue() {
                guard next < pending.count, !Task.isCancelled else { return }
                let entry = pending[next]; next += 1
                group.addTask { await entry.refresh() }
            }
            for _ in 0..<min(3, pending.count) { enqueue() }
            while await group.next() != nil {
                if Task.isCancelled { group.cancelAll(); break }
                enqueue()
            }
        }
    }
}
