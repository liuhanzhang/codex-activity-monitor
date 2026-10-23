import Foundation
import Testing
@testable import MonitorModel
@testable import MonitorApp

private actor ContentReplies: TimelineReading {
    private var replies: [String: CheckedContinuation<ActivityDetail, any Error>] = [:]
    private var started: [String: CheckedContinuation<Void, Never>] = [:]

    func timeline(scope: String, thread: String, turn: String?) async throws -> TimelineBundle {
        throw CancellationError()
    }
    func detail(phase: TimelinePhase, section: String?, offset: Int) async throws -> ActivityDetail {
        try await withCheckedThrowingContinuation {
            replies[phase.id] = $0
            started.removeValue(forKey: phase.id)?.resume()
        }
    }
    func wait(_ id: String) async {
        if replies[id] != nil { return }
        await withCheckedContinuation { started[id] = $0 }
    }
    func reply(_ id: String, _ value: ActivityDetail) { replies.removeValue(forKey: id)?.resume(returning: value) }
    func fail(_ id: String) { replies.removeValue(forKey: id)?.resume(throwing: CocoaError(.fileReadUnknown)) }
}

private func contentPhase(_ changes: [String: Any] = [:]) throws -> TimelinePhase {
    let fields: [String: Any] = ["id": "a", "scope_id": "scope", "thread_id": "thread", "turn_id": "turn",
        "item_id": "a", "namespace": "model", "track": "model", "kind": "text", "label": "Text", "status": "completed"]
    return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject:
        fields.merging(changes) { _, new in new }))
}

private func contentDetail(_ phase: TimelinePhase?, text: String? = "raw", offset: Int = 0,
                           next: Int? = nil, state: String = "available", version: Int = 1,
                           attachments: [String] = []) -> ActivityDetail {
    ActivityDetail(version: version, state: state, phase: phase, sections: text.map {
        [ActivitySection(id: "body", title: "Body", format: "text", text: $0, offset: offset,
                         total_chars: nil, next_offset: next, notice: nil)]
    } ?? [], attachments: attachments, source: "codex_history")
}

@Test @MainActor func viewerRejectsUnownedContentAndUnsupportedVersions() async throws {
    let selected = try contentPhase()
    let invalid = try [contentDetail(nil), contentDetail(nil, text: nil, state: "missing", attachments: ["/unowned.png"]),
        contentDetail(selected, version: 2)] +
        ["id", "scope_id", "thread_id", "turn_id"].map { contentDetail(try contentPhase([$0: "other"])) }
    for response in invalid {
        let client = ContentReplies()
        let timeline = TimelineStore(scope: "scope", thread: "thread", title: "", client: client)
        let store = ActivityViewerStore(timeline: timeline, phase: selected)
        let request = Task { await store.refresh() }
        await client.wait("a"); await client.reply("a", response); await request.value
        #expect(store.detail == nil && store.error != nil)
    }
}

@Test @MainActor func viewerPagesRequireMatchingIdentityAndForwardOffsets() async throws {
    let selected = try contentPhase()
    let invalid = [contentDetail(selected, text: "duplicate", offset: 0),
        contentDetail(selected, text: "repeat", offset: 3, next: 3),
        contentDetail(try contentPhase(["turn_id": "other"]), text: "unrelated", offset: 3),
        contentDetail(nil, text: "unowned", offset: 3)]
    for response in invalid {
        let client = ContentReplies()
        let timeline = TimelineStore(scope: "scope", thread: "thread", title: "", client: client)
        let store = ActivityViewerStore(timeline: timeline, phase: selected)
        let request = Task { await store.refresh() }
        await client.wait("a"); await client.reply("a", contentDetail(selected, text: "raw", next: 3)); await request.value
        let page = Task { await store.loadMore() }
        await client.wait("a"); await client.reply("a", response); await page.value
        #expect(store.detail?.sections.first?.text == "raw" && store.error != nil)
    }
}

@Test @MainActor func viewerKeepsLoadedContentWhenPageSourceDisappears() async throws {
    let selected = try contentPhase(), client = ContentReplies()
    let timeline = TimelineStore(scope: "scope", thread: "thread", title: "", client: client)
    let store = ActivityViewerStore(timeline: timeline, phase: selected)
    let request = Task { await store.refresh() }
    await client.wait("a"); await client.reply("a", contentDetail(selected, text: "raw", next: 3)); await request.value
    let page = Task { await store.loadMore() }
    await client.wait("a"); await client.reply("a", contentDetail(nil, text: nil, state: "missing")); await page.value
    #expect(store.detail?.sections.first?.text == "raw" && store.detail?.state == "missing")
    #expect(!store.loadingMore)
}

@Test @MainActor func oldPageFailureCannotMarkANewSelectionAsFailed() async throws {
    let selected = try contentPhase(), client = ContentReplies()
    let timeline = TimelineStore(scope: "scope", thread: "thread", title: "", client: client)
    let store = ActivityViewerStore(timeline: timeline, phase: selected)
    let request = Task { await store.refresh() }
    await client.wait("a"); await client.reply("a", contentDetail(selected, next: 3)); await request.value
    let page = Task { await store.loadMore() }
    await client.wait("a")
    store.select(try contentPhase(["id": "new"]))
    #expect(!store.loadingMore)
    await client.fail("a"); await page.value
    #expect(store.error == nil && store.detail == nil)
}
