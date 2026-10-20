import Foundation
import Testing
@testable import MonitorModel

private func sample(_ extra: String = "", coverage: String = "complete", rate: String = "48.5", threads: String = "") throws -> Snapshot {
    try Snapshot.decode(Data("""
    {"schema_version":1,"updated_at":100,"running":true,"threads":[\(threads)],
     "overview":{"version":1,"coverage":"\(coverage)","generation_rate":\(rate)}\(extra)}
    """.utf8))
}

@Test func integerSpeedsUseHalfUpRoundingAndKeepUnknown() {
    #expect(MetricFormat.speed(48.5) == "49 T/s")
    #expect(MetricFormat.speed(0.49) == "0 T/s")
    #expect(MetricFormat.speed(0.5) == "1 T/s")
    #expect(MetricFormat.speed(nil) == "—")
    #expect(MetricFormat.speed(.nan) == "—")
    #expect(MetricFormat.speed(1e100).hasSuffix(" T/s"))
    #expect(MetricFormat.rate(43.7, estimated: true) == "44")
    #expect(MetricFormat.compactSpeed(1e100) == ">99999 T/s")
    #expect(MetricFormat.compactSpeed(99999.49) == "99999 T/s")
    #expect(MetricFormat.compactSpeed(99999.5) == ">99999 T/s")
}

@Test func onlyFreshSingleConversationCanAnimate() throws {
    let now = Date(timeIntervalSince1970: 101)
    for mode in ["thinking", "executing"] {
        for count in [0, 1, 2] {
            let extra = ",\"menu_presentation\":{\"version\":1,\"mode\":\"\(mode)\",\"active_conversation_count\":\(count)}"
            let value = try sample(extra)
            #expect(value.menuMode(at: now) == (count == 1 ? MenuMode(rawValue: mode)! : .speed))
            #expect(value.menuMode(at: Date(timeIntervalSince1970: 104)) == .unavailable)
            #expect(try sample(extra, coverage: "partial").menuMode(at: now) == .unavailable)
        }
    }
}

@Test func missingProjectionFallsBackButUnknownProjectionDoesNotGuess() throws {
    let now = Date(timeIntervalSince1970: 101)
    #expect(try sample().menuMode(at: now) == .speed)
    for (version, mode, count) in [(2,"thinking",1), (1,"future",1), (1,"idle",0), (1,"thinking",-1)] {
        let extra = ",\"menu_presentation\":{\"version\":\(version),\"mode\":\"\(mode)\",\"active_conversation_count\":\(count)}"
        #expect(try sample(extra).menuMode(at: now) == .unavailable)
    }
}

@Test func onlyReliableIdleUsesBeachIcon() throws {
    let now = Date(timeIntervalSince1970: 101)
    for (reason, count) in [("idle", 0), ("waiting", 0), ("idle", 1), ("waiting", 1), ("paused", 1), ("generating", 1)] {
        let extra = ",\"menu_presentation\":{\"version\":1,\"mode\":\"speed\",\"reason\":\"\(reason)\",\"active_conversation_count\":\(count)},\"backends\":{\"test\":{\"connected\":true}}"
        let value = try sample(extra, rate: "0")
        #expect(value.menuMode(at: now) == (reason == "idle" && count == 0 ? .idle : .speed))
        #expect(value.menuTitle(at: now) == "0 T/s")
        #expect(value.menuMode(at: Date(timeIntervalSince1970: 104)) == .unavailable)
        #expect(try sample(extra, coverage: "partial", rate: "0").menuMode(at: now) == .unavailable)
    }
    for connected in ["false", "null"] {
        let extra = ",\"menu_presentation\":{\"version\":1,\"mode\":\"speed\",\"reason\":\"idle\",\"active_conversation_count\":0},\"backends\":{\"test\":{\"connected\":\(connected)}}"
        #expect(try sample(extra, rate: "0").menuMode(at: now) == .unavailable)
    }
    #expect(try sample(rate: "0").menuMode(at: now) == .speed)
}

@Test func activeDescendantRetainsOldAncestorAndIdleIsNotRecentCompletion() throws {
    let value = try sample(threads: """
    {"id":"parent","status":"completed","last_progress_seconds":700,"subagents":{"child":"spawnAgent"}},
    {"id":"child","has_active_work":true,"parent_thread_id":"parent"},
    {"id":"idle","status":"idle","last_progress_seconds":1},
    {"id":"recent","status":"completed","last_progress_seconds":1}
    """)
    #expect(value.activeConversationCount == 1)
    #expect(value.displayedChats.map(\.threadID) == ["parent", "child", "recent"])
    #expect(value.hasActiveWork(in: value.rootChats.first!))
    #expect(value.rows(expanded: [value.threads[0].id]).map(\.depth) == [0, 1, 0])
}
