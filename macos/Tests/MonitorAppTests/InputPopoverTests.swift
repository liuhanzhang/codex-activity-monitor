import AppKit
import Foundation
import Testing
@testable import MonitorModel
@testable import MonitorApp

private actor InputReplies: TimelineReading {
    var pending: CheckedContinuation<ActivityDetail, any Error>?
    var started: CheckedContinuation<Void, Never>?
    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle { throw CancellationError() }
    func detail(phase: TimelinePhase, page: ActivityPageCursor?) async throws -> ActivityDetail {
        try await withCheckedThrowingContinuation {
            pending = $0
            started?.resume(); started = nil
        }
    }
    func wait() async {
        if pending != nil { return }
        await withCheckedContinuation { started = $0 }
    }
    func reply(_ value: ActivityDetail) { pending?.resume(returning: value); pending = nil }
    func fail(_ error: any Error) { pending?.resume(throwing: error); pending = nil }
}

private func inputPhase(_ fields: [String: Any] = [:]) throws -> TimelinePhase {
    try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: [
        "id": "input", "scope_id": "scope", "thread_id": "root", "turn_id": "turn", "item_id": "input",
        "namespace": "canonical", "track": "marker", "kind": "input", "label": "输入", "status": "completed",
        "is_initial": true
    ].merging(fields) { _, new in new }))
}

private func inputDetail(_ phase: TimelinePhase?, text: String? = "raw", state: String = "available",
                         offset: Int = 0, next: Int? = nil) -> ActivityDetail {
    ActivityDetail(version: 2, state: state, phase: phase, sections: text.map { [
        ActivitySection(id: "input", title: "用户输入", format: "text", text: $0, offset: offset,
                        total_chars: next == nil ? offset + $0.unicodeScalars.count : 9, next_offset: next, notice: nil, page_token: "fixture-input")
    ] } ?? [], attachments: [], source: "synthetic")
}

@Test @MainActor func structuredQueryLimitPreservesInputReading() async throws {
    let client = InputReplies(), selected = try inputPhase()
    let store = InputContentStore(phase: selected, client: client)
    let initial = Task { await store.load() }
    await client.wait(); await client.reply(inputDetail(selected, text: "kept")); await initial.value
    let next = Task { await store.load() }
    await client.wait()
    await client.fail(BackendFailure(message: "", presentationKey: "所选内容过多，暂时无法一次载入。"))
    await next.value
    #expect(store.detail?.sections[0].text == "kept")
    #expect(store.error == "所选内容过多，暂时无法一次载入。")
}

@Test @MainActor func dismissedInputDiscardsLateContent() async throws {
    let client = InputReplies(), phase = try inputPhase()
    let store = InputContentStore(phase: phase, client: client)
    let request = Task { await store.load() }
    await client.wait()
    store.invalidate()
    await client.reply(inputDetail(phase))
    await request.value
    #expect(store.detail == nil && store.error == nil && !store.loading)
}

@Test @MainActor func inputRejectsContentFromAnyOtherScopeThreadTurnOrPhase() async throws {
    for fields: [String: Any] in [["scope_id": "other"], ["thread_id": "other"], ["turn_id": "other"], ["id": "other"]] {
        let client = InputReplies()
        let store = InputContentStore(phase: try inputPhase(), client: client)
        let request = Task { await store.load() }
        await client.wait()
        await client.reply(inputDetail(try inputPhase(fields)))
        await request.value
        #expect(store.detail == nil && store.error != nil)
    }
}

@Test @MainActor func missingInputDoesNotNeedFabricatedIdentityButCannotCarryText() async throws {
    let client = InputReplies()
    let realStore = InputContentStore(phase: try inputPhase(), client: client)
    let request = Task { await realStore.load() }
    await client.wait()
    await client.reply(inputDetail(nil, text: nil, state: "content_unavailable"))
    await request.value
    #expect(realStore.detail?.sections.isEmpty == true && realStore.notice != nil)
    let retry = Task { await realStore.load() }
    await client.wait()
    await client.reply(inputDetail(nil, text: "unowned", state: "content_unavailable"))
    await retry.value
    #expect(realStore.detail?.sections.isEmpty == true && realStore.error != nil)
}

@Test @MainActor func inputPaginationPreservesRawTextAndRejectsWrongOffsets() async throws {
    let client = InputReplies(), phase = try inputPhase()
    let store = InputContentStore(phase: phase, client: client)
    let first = Task { await store.load() }
    await client.wait(); await client.reply(inputDetail(phase, text: "a\n$", next: 3)); await first.value
    let page = Task { await store.loadMore() }
    await client.wait(); await client.reply(inputDetail(phase, text: "{b}", offset: 3, next: 6)); await page.value
    #expect(store.detail?.sections[0].text == "a\n${b}")
    let wrong = Task { await store.loadMore() }
    await client.wait(); await client.reply(inputDetail(phase, text: "duplicate", offset: 0)); await wrong.value
    #expect(store.detail?.sections[0].text == "a\n${b}" && store.error != nil)
    let more = Task { await store.loadMore() }
    await client.wait(); store.invalidate()
    await client.reply(inputDetail(phase, text: "late", offset: 6)); await more.value
    #expect(store.detail?.sections[0].text == "a\n${b}")
}

@Test @MainActor func inputSourceTruncationAndNotRetainedAreExplicit() async throws {
    let phase = try inputPhase()
    for state in ["source_truncated", "not_retained", "partial"] {
        let client = InputReplies()
        let reader = InputContentStore(phase: phase, client: client)
        let request = Task { await reader.load() }
        await client.wait(); await client.reply(inputDetail(phase, text: state == "not_retained" ? nil : "raw", state: state)); await request.value
        #expect(reader.notice != nil && reader.detail?.state == state)
    }
}

@Test @MainActor func inputPageSourceLossKeepsLoadedTextWithExplicitUnavailableState() async throws {
    let client = InputReplies(), phase = try inputPhase()
    let store = InputContentStore(phase: phase, client: client)
    let first = Task { await store.load() }
    await client.wait(); await client.reply(inputDetail(phase, text: "raw", next: 3)); await first.value
    let page = Task { await store.loadMore() }
    await client.wait(); await client.reply(inputDetail(nil, text: nil, state: "missing")); await page.value
    #expect(store.detail?.sections[0].text == "raw")
    #expect(store.detail?.state == "missing" && store.notice != nil)
}

@Test @MainActor func inputChangedPageRequiresReloadWithoutDiscardingTheReadText() async throws {
    let client = InputReplies(), phase = try inputPhase()
    let store = InputContentStore(phase: phase, client: client)
    let first = Task { await store.load() }
    await client.wait(); await client.reply(inputDetail(phase, text: "raw", next: 3)); await first.value
    let page = Task { await store.loadMore() }
    await client.wait(); await client.reply(inputDetail(nil, text: nil, state: "content_changed")); await page.value
    #expect(store.detail?.sections[0].text == "raw" && store.detail?.pageCursor() == nil)
    #expect(store.detail?.requiresReload == true && store.notice != nil)
    let reload = Task { await store.load() }
    await client.wait(); await client.reply(inputDetail(phase, text: "new")); await reload.value
    #expect(store.detail?.sections[0].text == "new" && store.detail?.state == "available")
}

@Test @MainActor func auxiliaryDismissalIsOrderedAndCannotRemoveANewerPopover() {
    let state = PanelAuxiliaryState(), first = UUID(), second = UUID()
    var closed: [UUID] = []
    state.present(first) { closed.append(first) }
    state.present(second) { closed.append(second) }
    #expect(closed == [first] && state.activeID == second)
    state.remove(first)
    #expect(state.activeID == second)
    #expect(state.dismiss() && state.activeID == nil && closed == [first, second])
    #expect(!state.dismiss() && !state.owns(nil))
}

@Test @MainActor func compactActivityAccessibilityKeepsActualChildOwnership() throws {
    let view = TimelineDocumentView(), phase = try inputPhase(["thread_id": "child", "track": "model", "kind": "text"])
    view.agents = [try JSONDecoder().decode(TimelineAgent.self, from: JSONSerialization.data(withJSONObject: [
        "id": "child", "thread_id": "child", "name": "子智能体 · Lorentz", "display_name": "Lorentz"
    ]))]
    view.layout = TimelineLayout(origin: 0, end: 1000, now: 1000, lanes: [
        TimelineLane(id: "child-model", threadID: "child", track: "model", label: "模型", height: 18, isChild: true)])
    #expect(view.activityLabel(phase).hasPrefix("Lorentz · "))
    #expect(!view.activityLabel(phase).contains("主智能体"))
}

@Test @MainActor func availablePanelHeightUsesItsAnchorScreenAndNativeChrome() {
    let visible = NSRect(x: -1200, y: 400, width: 1200, height: 700)
    let anchor = NSRect(x: -400, y: 1100, width: 50, height: 22)
    #expect(PopoverController.availableContentHeight(anchor: anchor, visibleFrame: visible) == 658)
    let budget = PanelLayoutBudget(maximum: 658, chrome: 90, chatCount: 12, hasSelection: true)
    #expect(budget.listHeight == 280 && budget.listHeight + budget.detailHeight + 90 == 658)
}
