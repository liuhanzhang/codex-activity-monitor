import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

/// Run by the disposable collector harness; never reads a daily runtime.
@Test(.enabled(if: ProcessInfo.processInfo.environment["CAM_ROLLOUT_CLIENT_CONFIG"] != nil))
@MainActor func isolatedRolloutHistoryBackendClient() async throws {
    struct Fixture: Decodable {
        let configuration: BackendConfiguration
        let scope: String
    }
    let path = try #require(ProcessInfo.processInfo.environment["CAM_ROLLOUT_CLIENT_CONFIG"])
    let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    let client = SystemTimelineClient(configuration: fixture.configuration, runtime: fixture.configuration.runtime)
    let timeline = TimelineStore(scope: fixture.scope, thread: "packaging-rollout", title: "Synthetic", client: client)
    timeline.selectTurn("current")
    await timeline.refresh()
    #expect(timeline.error == nil)
    #expect(timeline.bundle?.average_speed?.tokens_per_second == 200)
    let current = try #require(timeline.bundle?.phases.first { $0.item_id == "current-item" })
    let viewer = ActivityViewerStore(timeline: timeline, phase: current)
    await viewer.refresh()
    #expect(viewer.error == nil)
    #expect(viewer.detail?.state == "available")
    #expect(viewer.detail?.sections.first?.text == "Synthetic current suffix")

    timeline.selectTurn("kept")
    await timeline.refresh()
    let kept = try #require(timeline.bundle?.phases.first { $0.item_id == "kept-item" })
    viewer.select(kept)
    await viewer.refresh()
    #expect(viewer.error == nil)
    #expect(viewer.detail?.sections.first?.text == "Synthetic retained prefix")
    #expect(timeline.bundle?.average_speed?.tokens_per_second == 100)

    timeline.selectTurn("discarded")
    await timeline.refresh()
    #expect(timeline.error == nil)
    #expect(timeline.bundle?.phases.isEmpty == true)

    let fork = try await client.timeline(scope: fixture.scope, thread: "packaging-user-fork", turn: "kept")
    let inherited = try #require(fork.phases.first { $0.item_id == "kept-item" })
    let body = try await client.detail(phase: inherited, page: nil)
    #expect(body.sections.first?.text == "Synthetic retained prefix")
    try Data("verified\n".utf8).write(to: URL(fileURLWithPath: path+".ok"))
}
