import Foundation
import Testing
@testable import MonitorModel

private func speedSnapshot(status: String? = "sampling", quality: [String] = ["stream_pending"],
                           reason: String = "sampling", connected: Bool = true,
                           rate: Double? = nil, coverage: String = "unavailable") throws -> Snapshot {
    var overview: [String: Any] = ["version": 1, "coverage": coverage, "quality": quality]
    if let status { overview["rate_status"] = status }
    if let rate { overview["generation_rate"] = rate }
    return try Snapshot.decode(JSONSerialization.data(withJSONObject: [
        "schema_version": 1, "running": true, "updated_at": 100, "overview": overview,
        "menu_presentation": ["version": 1, "mode": "speed", "reason": reason, "active_conversation_count": 1],
        "backends": ["b": ["connected": connected]], "threads": []]))
}

@Test func initialCountingUsesGenerationWithoutEllipsisOrAnErrorNotice() throws {
    let value = try speedSnapshot()
    #expect(value.isMeasuringSpeed)
    #expect(value.menuTitle(at: Date(timeIntervalSince1970: 101)) == "生成")
    #expect(value.menuTitle(connectionFresh: true, language: .english) == "Generation")
    #expect(value.menuMode(connectionFresh: true) == .speed)
    #expect(value.observationNotice == nil)
    #expect(value.overview?.generation_rate == nil)
    #expect(value.menuTitle(connectionFresh: false) == "—")
    #expect(value.menuTitle(at: Date(timeIntervalSince1970: 110)) == "—")
}

@Test func incompleteOrContradictorySamplingNeverHidesErrors() throws {
    for quality in [["observation_gap"], ["tokenizer_error"], ["count_budget"],
                    ["stream_pending", "identity_conflict"], ["unassociated_events"], []] {
        let value = try speedSnapshot(quality: quality)
        #expect(!value.isMeasuringSpeed)
        #expect(value.menuTitle(connectionFresh: true) == "—")
    }
    for value in [try speedSnapshot(status: nil), try speedSnapshot(status: "future-status"),
                  try speedSnapshot(reason: "coverage_incomplete"), try speedSnapshot(connected: false),
                  try speedSnapshot(rate: 12)] {
        #expect(!value.isMeasuringSpeed)
        #expect(value.menuMode(connectionFresh: true) == .unavailable)
    }
}

@Test func knownZeroAndRealRatesRemainNumeric() throws {
    for rate in [0.0, 100.0] {
        let value = try speedSnapshot(status: "ready", quality: [], reason: "generating", rate: rate, coverage: "complete")
        #expect(!value.isMeasuringSpeed)
        #expect(value.menuTitle(connectionFresh: true) == MetricFormat.speed(rate))
        #expect(value.observationNotice == nil)
    }
}

@Test func countingFailuresHaveSpecificNotices() throws {
    #expect(try speedSnapshot(status: "unavailable", quality: ["count_budget"]).observationNotice == .speedResourceLimit)
    #expect(try speedSnapshot(status: "unavailable", quality: ["tokenizer_error"]).observationNotice == .speedCountFailed)
    #expect(try speedSnapshot(status: "unavailable", quality: ["observation_gap"]).observationNotice == .observationGap)
}
