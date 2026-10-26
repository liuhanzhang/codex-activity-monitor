import CoreGraphics
import Foundation
import Testing
@testable import MonitorModel

func timelinePhase(_ fields: [String: Any] = [:]) throws -> TimelinePhase {
    let base: [String: Any] = ["id": "p", "scope_id": "home", "thread_id": "root", "turn_id": "turn",
                             "item_id": "item", "namespace": "model", "track": "model", "kind": "text",
                             "label": "正文", "status": "completed", "start_ms": 1000.0, "end_ms": 3000.0]
    return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: base.merging(fields) { _, n in n }))
}

@Test func sharedFixedAxisPreservesDurationAndCoordinates() throws {
    let p = try timelinePhase()
    let lane = TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)
    let a = TimelineLayout(origin: 0, end: 300000, now: 3000, lanes: [lane])
    let b = TimelineLayout(origin: 0, end: 600000, now: 3000, lanes: [lane])
    #expect(a.rect(for: p).map { Double($0.width) } == 2 * TimelineLayout.defaultScale)
    #expect(b.rect(for: p) == a.rect(for: p))
    #expect(a.width(viewport: 300) > 1200)
    #expect(abs(a.time(at: a.x(at: 12458)) - 12458) < 0.000001)
}

@Test func shortHitTargetDoesNotStretchOrMoveTimeline() throws {
    let p = try timelinePhase(["end_ms": 1001])
    let lane = TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)
    let layout = TimelineLayout(origin: 0, end: 3000, now: 3000, lanes: [lane])
    let rect = try #require(layout.rect(for: p)), hit = try #require(layout.hitRect(for: p))
    #expect(rect.width == 3)
    #expect(hit == rect)
    #expect(rect.midX == hit.midX)
    #expect(abs(Double(rect.minX) - layout.x(at: 1000)) < 0.000001)
}

@Test func overlapReturnsAllOriginalActivitiesWithoutAddingLanes() throws {
    let a = try timelinePhase(), b = try timelinePhase(["id": "b", "start_ms": 2000, "end_ms": 4000])
    let lane = TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)
    let layout = TimelineLayout(origin: 0, end: 5000, now: 5000, lanes: [lane])
    let y = try #require(layout.rect(for: a)).midY
    #expect(layout.hitTest(CGPoint(x: layout.x(at: 2500), y: y), phases: [a, b]).map(\.id) == ["p", "b"])
    #expect(layout.lanes.count == 1)
}

@Test(arguments: ["model", "summary"])
func modelStagesStaySeparateWhenMinimumPixelsOrClockBoundariesOverlap(track: String) throws {
    let stages = try [("reasoning", 1000, 1046), ("text", 1049, 2041),
                      ("tool_input", 2019, 3000), ("file_edit_input", 2978, 4000),
                      ("contextCompaction", 3978, 5000)].map { kind, start, end in
        try timelinePhase(["id": kind, "kind": kind, "start_ms": start, "end_ms": end])
    }
    let lane = TimelineLane(id: "model", threadID: "root", track: track, label: "模型", height: 21, isChild: false)
    let layout = TimelineLayout(origin: 0, end: 5000, now: 5000, lanes: [lane], activities: stages)
    for (a, b) in zip(stages, stages.dropFirst()) {
        #expect(try #require(layout.rect(for: a)).intersects(try #require(layout.rect(for: b))))
    }
    #expect(stages[0].end_ms! < stages[1].start_ms!)
    #expect(stages[0].duration(at: 5000) == 0.046)
    #expect(layout.rect(for: stages[1]).map { Double($0.minX) } == layout.x(at: 1049))
}

@Test func missingStartHasNoFakeDurationAndUnknownKindStillDecodes() throws {
    let p = try timelinePhase(["start_ms": NSNull(), "kind": "future-kind", "quality": ["start_unknown"]])
    #expect(p.duration(at: 5000) == nil)
    #expect(p.anchor == 3000)
    #expect(p.kindLabel == "正文")
    let active = try timelinePhase(["status": "active", "end_ms": NSNull()])
    #expect(active.duration(at: 5000) == 4)
}

@Test(arguments: ["unknown", "completed", "interrupted"])
func missingModelEndDoesNotTurnObservationWindowIntoGeneration(status: String) throws {
    let phase = try timelinePhase(["status": status, "kind": "reasoning", "end_ms": NSNull(),
                                   "observed_until_ms": 76000, "quality": ["missing_item_end"]])
    let lane = TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)
    for now in [76000.0, 100000.0] {
        let layout = TimelineLayout(origin: 0, end: now, now: now, lanes: [lane], activities: [phase])
        #expect(phase.duration(at: now) == nil)
        #expect(layout.rect(for: phase).map { Double($0.width) } == TimelineLayout.shortActivityWidth)
        #expect(layout.rect(for: phase).map { Double($0.minX) } == layout.x(at: 1000))
    }
}

@Test func oldSnapshotAcceptsMissingTimelineContract() throws {
    let snapshot = try Snapshot.decode(Data("{\"schema_version\":1,\"threads\":[{\"id\":\"root\"}]}".utf8))
    #expect(snapshot.timeline_summary == nil)
    #expect(snapshot.threads[0].turn_id == nil)
}

private func toolsLayout(_ phases: [TimelinePhase], now: Double = 5000,
                         end: Double = 5000, lanes: [TimelineLane]? = nil) -> TimelineLayout {
    TimelineLayout(origin: 0, end: end, now: now, lanes: lanes ?? [
        TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false),
        TimelineLane(id: "tool", threadID: "root", track: "tool", label: "工具", height: 21, isChild: false)], activities: phases)
}

@Test func shortToolsUseVisibleLinesAtSourcePositionsWithoutTransparentHitPadding() throws {
    let tools = try (0..<100).map { n in
        try timelinePhase(["id": String(format: "%03d", n), "track": "tool", "kind": "tool_execution",
                           "start_ms": 1000 + n * 1000, "end_ms": 1000 + n * 1000 + 1])
    }
    let layout = toolsLayout(tools), reversed = toolsLayout(Array(tools.reversed()))
    for tool in tools {
        let rect = try #require(layout.rect(for: tool))
        #expect(Double(rect.width) == TimelineLayout.shortActivityWidth)
        #expect(layout.hitRect(for: tool) == rect)
        #expect(reversed.rect(for: tool) == rect)
        #expect(Double(rect.minX) == layout.x(at: tool.start_ms!))
        #expect(layout.hitTest(CGPoint(x: rect.midX, y: rect.midY), phases: tools).map(\.id) == [tool.id])
        #expect(layout.hitTest(CGPoint(x: rect.maxX + 0.1, y: rect.midY), phases: tools).isEmpty)
    }
    #expect(tools[0].duration(at: 5000) == 0.001)
}

@Test func longToolDoesNotConnectFortySequentialToolsIntoOneSelection() throws {
    let long = try timelinePhase(["id": "long", "track": "tool", "kind": "tool_execution", "start_ms": 0, "end_ms": 300000])
    let short = try (0..<40).map { n in
        try timelinePhase(["id": "short-\(n)", "track": "tool", "kind": "tool_execution",
                           "start_ms": 5000 + n * 5000, "end_ms": 5050 + n * 5000])
    }
    let layout = toolsLayout([long] + short, end: 300000)
    for tool in short {
        let rect = try #require(layout.rect(for: tool))
        #expect(layout.hitTest(CGPoint(x: rect.midX, y: rect.midY), phases: [long] + short).map(\.id) == ["long", tool.id])
    }
    let row = try #require(layout.rect(for: long))
    #expect(layout.hitTest(CGPoint(x: layout.x(at: 250000), y: row.midY), phases: [long] + short).map(\.id) == ["long"])
}

@Test func longParallelToolsKeepDurationWidthWithoutMovingModel() throws {
    let a = try timelinePhase(["id": "a", "track": "tool", "kind": "tool_execution", "end_ms": 301000])
    let b = try timelinePhase(["id": "b", "track": "tool", "kind": "tool_execution", "end_ms": 201000])
    let model = try timelinePhase(["id": "model"])
    let layout = toolsLayout([b, model, a], end: 301000)
    let ra = try #require(layout.rect(for: a)), rb = try #require(layout.rect(for: b))
    #expect(abs(Double(ra.width) - 300 * TimelineLayout.defaultScale) < 0.000001)
    #expect(abs(Double(rb.width) - 200 * TimelineLayout.defaultScale) < 0.000001)
    #expect(rb.minX == ra.minX)
    #expect(layout.rect(for: model).map { Double($0.minX) } == layout.x(at: 1000))
    #expect(layout.rect(for: model).map { Double($0.width) } == 2 * TimelineLayout.defaultScale)
    #expect(abs(layout.width(viewport: 300) - (301 * TimelineLayout.defaultScale + 8)) < 0.000001)
    #expect(a.duration(at: 5000) == 300 && b.duration(at: 5000) == 200)
    #expect(layout.end == 301000)
}

@Test func childToolsRemainIndependentlySelectableWithoutSelectingParent() throws {
    let root = try timelinePhase(["id": "root-tool", "track": "tool", "kind": "tool_execution", "end_ms": 11000])
    let a = try timelinePhase(["id": "child-a", "thread_id": "child", "track": "tool", "kind": "tool_execution"])
    let b = try timelinePhase(["id": "child-b", "thread_id": "child", "track": "tool", "kind": "tool_execution"])
    let model = try timelinePhase(["id": "child-model", "thread_id": "child"])
    let rootLane = TimelineLane(id: "root", threadID: "root", track: "tool", label: "工具", height: 21, isChild: false)
    for track in ["summary", "tool"] {
        let lane = TimelineLane(id: "child", threadID: "child", track: track, label: "子智能体", height: 21, isChild: true)
        let layout = toolsLayout([root, b, a, model], lanes: [rootLane, lane])
        #expect(layout.rect(for: root)?.minX == layout.rect(for: a)?.minX)
        #expect(layout.rect(for: b)?.minX == layout.rect(for: a)?.minX)
        let rect = try #require(layout.rect(for: a))
        #expect(layout.hitTest(CGPoint(x: rect.midX, y: rect.midY), phases: [root, a, b, model])
            .filter { $0.displayTrack == "tool" }.map(\.id) == ["child-a", "child-b"])
    }
}

@Test func unknownToolDurationUsesSelectableWidthWithoutInventingTime() throws {
    let tool = try timelinePhase(["track": "tool", "kind": "tool_execution", "start_ms": NSNull(), "quality": ["start_unknown"]])
    let layout = toolsLayout([tool])
    #expect(layout.rect(for: tool).map { Double($0.width) } == TimelineLayout.shortActivityWidth)
    #expect(tool.duration(at: 5000) == nil)
    #expect(layout.rect(for: tool).map { Double($0.minX) } == layout.x(at: 3000))
}

@Test func fixedScaleIgnoresTurnLengthAndOnlyViewportChangesVisibleTime() throws {
    let lane = TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)
    let short = TimelineLayout(origin: 1000, end: 31000, now: 31000, lanes: [lane], activities: [])
    let longer = TimelineLayout(origin: 1000, end: 31001, now: 31001, lanes: [lane], activities: [])
    let hour = TimelineLayout(origin: 1000, end: 3601000, now: 3601000, lanes: [lane], activities: [])
    #expect(short.width(viewport: 291) == 291)
    #expect(abs(short.pointsPerSecond - 347.0 / 60) < 0.00001)
    #expect(short.pointsPerSecond == longer.pointsPerSecond && longer.pointsPerSecond == hour.pointsPerSecond)
    #expect(TimelineLayout.visibleSeconds(in: 355) == 60)
    #expect(TimelineLayout.visibleSeconds(in: 702) == 120)
    #expect(hour.width(viewport: 291) > 16000)
    let p = try timelinePhase()
    #expect(short.rect(for: p) == hour.rect(for: p))
    #expect(short.width(viewport: 600) == 600)
}

@Test func selectedToolReadingAnchorSurvivesGrowthAndEarlierHistory() throws {
    let active = try timelinePhase(["id": "a", "track": "tool", "kind": "tool_execution",
                                   "status": "active", "end_ms": NSNull()])
    let selected = try timelinePhase(["id": "b", "track": "tool", "kind": "tool_execution", "start_ms": 1100, "end_ms": 1200])
    let before = toolsLayout([active, selected], now: 5000)
    let grown = toolsLayout([active, selected], now: 20000)
    let restored = TimelineLayout(origin: -1000, end: 5000, now: 20000, lanes: grown.lanes, activities: [active, selected])
    let offset = 10.0
    let shifted = grown.readingOffset(from: before, offset: offset, selectedID: "b", viewport: 300)
    #expect(abs((Double(grown.rect(for: selected)!.minX) - shifted) - (Double(before.rect(for: selected)!.minX) - offset)) < 0.000001)
    let restoredOffset = restored.readingOffset(from: grown, offset: shifted, selectedID: "b", viewport: 300)
    #expect(abs((Double(restored.rect(for: selected)!.minX) - restoredOffset) - (Double(grown.rect(for: selected)!.minX) - shifted)) < 0.000001)
    #expect(grown.readingOffset(from: before, offset: 10, selectedID: nil, viewport: 300) == 10)
}

@Test func oldStatusMarkersAreHiddenAndCompactionKeepsItsCanonicalIdentity() throws {
    let hook = try timelinePhase(["track": "marker", "kind": "other", "item_type": "sleep"])
    let compaction = try timelinePhase(["id": "compaction", "track": "marker", "kind": "contextCompaction",
                                       "item_type": "contextCompaction", "namespace": "canonical"])
    let layout = toolsLayout([hook, compaction])
    #expect(layout.rect(for: hook) == nil)
    #expect(layout.hitRect(for: hook) == nil)
    #expect(layout.rect(for: compaction).map { Double($0.width) } == 2 * TimelineLayout.defaultScale)
    #expect(compaction.kindLabel == "压缩")
    #expect(compaction.namespace == "canonical")
}

@Test func zoomLevelsPreserveSourceTimeAndReadingFocus() throws {
    let phase = try timelinePhase(["start_ms": 40000, "end_ms": 42000])
    let lanes = [TimelineLane(id: "model", threadID: "root", track: "model", label: "模型", height: 21, isChild: false)]
    let overview = TimelineLayout(origin: 0, end: 90000, now: 90000, lanes: lanes, activities: [phase], zoom: .overview)
    let detail = TimelineLayout(origin: 0, end: 90000, now: 90000, lanes: lanes, activities: [phase], zoom: .detail)
    #expect(TimelineLayout.visibleSeconds(in: 355, zoom: .overview) == 60)
    #expect(detail.rect(for: phase)!.width == 2 * overview.rect(for: phase)!.width)
    #expect(phase.duration(at: 90000) == 2)
    let offset = 90.0, viewport = 291.0
    let centered = detail.readingOffset(from: overview, offset: offset, selectedID: nil, viewport: viewport)
    #expect(abs(detail.time(at: centered + viewport / 2) - overview.time(at: offset + viewport / 2)) < 0.001)
    let selected = detail.readingOffset(from: overview, offset: offset, selectedID: phase.id, viewport: viewport)
    #expect(abs((detail.rect(for: phase)!.minX - selected) - (overview.rect(for: phase)!.minX - offset)) < 0.001)
}

@Test func rawDetailNoticeIsOptionalAndEmptyValuesAreNotMissing() throws {
    let old = try JSONDecoder().decode(ActivityDetail.self, from: Data("""
    {"version":1,"state":"available","sections":[{"id":"input","title":"参数","format":"code","text":""}],"attachments":[]}
    """.utf8))
    let new = try JSONDecoder().decode(ActivityDetail.self, from: Data("""
    {"version":1,"state":"available","sections":[{"id":"input","title":"参数","format":"text","text":"","notice":"源记录未提供独立参数。"},{"id":"output","title":"结果","format":"json","text":"false"}],"attachments":[]}
    """.utf8))
    #expect(old.sections[0].notice == nil)
    #expect(new.sections[0].notice != nil)
    #expect(new.sections[1].text == "false" && new.sections[1].notice == nil)
}
