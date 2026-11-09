import Foundation
import Testing
import MonitorModel
@testable import MonitorApp

@MainActor private func samplingDashboard() -> DashboardStore {
    DashboardStore(configuration: BackendConfiguration(python: "/unused", source_root: "/unused", runtime: "/unused",
        data_root: "/unused", build_id: "sampling-test"), arguments: ["--snapshot", "/unused"])
}

private func speedSnapshot(_ rate: Double? = nil, status: String = "sampling",
                           quality: [String] = ["stream_pending"], connected: Bool = true,
                           age: Double = 0) throws -> Snapshot {
    var overview: [String: Any] = ["version": 1, "coverage": rate == nil ? "unavailable" : "complete",
                                  "quality": rate == nil ? quality : [], "rate_status": rate == nil ? status : "ready"]
    if let rate { overview["generation_rate"] = rate }
    return try Snapshot.decode(JSONSerialization.data(withJSONObject: [
        "schema_version": 1, "running": true, "updated_at": Date().timeIntervalSince1970 - age, "threads": [],
        "overview": overview,
        "menu_presentation": ["version": 1, "mode": "speed", "reason": rate == nil ? status : "ready",
                              "active_conversation_count": 1],
        "backends": ["b": ["connected": connected, "implementation": ["modules": ["observer_permit": "test"]]]]]))
}

@Test @MainActor func briefInitialCountKeepsTheMenuStableWithoutChangingMetrics() throws {
    let store = samplingDashboard()
    let start = ContinuousClock.now
    store.snapshot = try speedSnapshot(48)
    store.updatePresentation(menuTime: start)
    #expect(store.menuTitle == "48 T/s" && store.menuMode == .speed)

    store.snapshot = try speedSnapshot()
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(10)))
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(260)))
    #expect(store.menuTitle == "48 T/s" && store.menuMode == .speed)
    #expect(store.snapshot?.overview?.generation_rate == nil)
    #expect(store.snapshot?.menuTitle() == "—")

    store.snapshot = try speedSnapshot(62)
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(300)))
    #expect(store.menuTitle == "62 T/s")
}

@Test @MainActor func repeatedSamplingCannotExtendTheHalfSecondDeadline() throws {
    let store = samplingDashboard()
    let start = ContinuousClock.now
    store.snapshot = try speedSnapshot(48)
    store.updatePresentation(menuTime: start)
    store.snapshot = try speedSnapshot()
    store.updatePresentation(menuTime: start)
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(499)))
    #expect(store.menuTitle == "48 T/s")
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(500)))
    #expect(store.menuTitle == "—" && store.menuMode == .unavailable)
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(600)))
    #expect(store.menuTitle == "—")

    store.snapshot = try speedSnapshot(12)
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(700)))
    store.snapshot = try speedSnapshot()
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(800)))
    #expect(store.menuTitle == "12 T/s")
    store.updatePresentation(menuTime: start.advanced(by: .milliseconds(1300)))
    #expect(store.menuTitle == "—")
}

@Test @MainActor func realFailuresBypassSamplingGraceAndDoNotResurrectAnOldSpeed() throws {
    let failures = [try speedSnapshot(connected: false),
                    try speedSnapshot(status: "unavailable", quality: ["stream_pending"]),
                    try speedSnapshot(quality: ["observation_gap"]),
                    try speedSnapshot(quality: ["stream_pending", "tokenizer_error"]),
                    try speedSnapshot(age: 10)]
    for (index, failure) in (failures + [try speedSnapshot()]).enumerated() {
        let store = samplingDashboard()
        let start = ContinuousClock.now
        store.snapshot = try speedSnapshot(48)
        store.updatePresentation(menuTime: start)
        store.snapshot = try speedSnapshot()
        store.updatePresentation(menuTime: start)
        #expect(store.menuTitle == "48 T/s")
        store.snapshot = failure
        if index == failures.count { store.readError = "read failed" }
        store.updatePresentation(menuTime: start.advanced(by: .milliseconds(100)))
        #expect(store.menuTitle == "—" && store.menuMode == .unavailable)
        store.readError = nil
        store.snapshot = try speedSnapshot()
        store.updatePresentation(menuTime: start.advanced(by: .milliseconds(200)))
        #expect(store.menuTitle == "—")
    }
}

@Test @MainActor func samplingGraceExpiresEvenWithoutAnotherStateFrame() async throws {
    let store = samplingDashboard()
    store.snapshot = try speedSnapshot(48)
    store.updatePresentation()
    store.snapshot = try speedSnapshot()
    store.updatePresentation()
    #expect(store.menuTitle == "48 T/s")
    var expired = false
    store.onUpdate = { expired = store.menuTitle == "—" }
    try await Task.sleep(for: .milliseconds(750))
    #expect(expired)
    #expect(store.menuTitle == "—" && store.menuMode == .unavailable)
}
