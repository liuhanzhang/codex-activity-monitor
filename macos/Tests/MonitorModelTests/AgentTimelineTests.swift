import Foundation
import CoreGraphics
import Testing
@testable import MonitorModel

private func agentBundle(children: Bool = true) throws -> TimelineBundle {
    let agents: [[String: Any]] = [["id": "root", "thread_id": "root", "name": "主智能体", "status": "active",
        "status_scope": "selected_parent_turn"]] + (children ? [
        ["id": "child", "thread_id": "child", "name": "子智能体 · Lorentz", "display_name": "Lorentz", "parent_thread_id": "root",
         "status": "completed", "status_scope": "selected_parent_turn"],
        ["id": "nested", "thread_id": "nested", "name": "子智能体 · Parfit", "parent_thread_id": "child"]] : [])
    return try TimelineBundle.decode(JSONSerialization.data(withJSONObject: ["version": 1, "scope_id": "scope", "thread_id": "root",
        "agents": agents, "turns": [], "phases": [], "markers": [], "origin_ms": 1000, "end_ms": 5000,
        "now_ms": 5000, "quality": [], "revision": "one", "earlier_active_turns": []]))
}

private func agentPhase(_ owner: String, type: String = "reasoning", extra: [String: Any] = [:]) throws -> TimelinePhase {
    var value: [String: Any] = ["id": owner+"-activity", "scope_id": "scope", "thread_id": owner, "turn_id": owner+"-turn",
        "item_id": "call", "item_type": type, "namespace": type == "reasoning" ? "model" : "canonical",
        "track": type == "reasoning" ? "model" : "tool", "kind": type == "reasoning" ? "reasoning" : "tool_execution",
        "label": "original", "status": "completed", "start_ms": 2000, "end_ms": 3000]
    value.merge(extra) { _, new in new }
    return try JSONDecoder().decode(TimelinePhase.self, from: JSONSerialization.data(withJSONObject: value))
}

@Test func groupedAgentHeadersDoNotChangeActivityTimeOrViewerLanes() throws {
    let bundle = try agentBundle(), phases = try [agentPhase("root"), agentPhase("child"), agentPhase("nested")]
    let viewer = TimelineLane.lanes(in: bundle, expanded: ["child"])
    let grouped = TimelineLane.lanes(in: bundle, expanded: ["child"], presentation: .panelGrouped)
    #expect(viewer.map(\.track) == ["model", "tool", "header", "model", "tool", "summary"])
    #expect(grouped.map(\.track) == ["header", "model", "tool", "header", "model", "tool", "header", "summary"])
    #expect(grouped.first?.label == "主智能体")
    #expect(grouped.first { $0.threadID == "nested" }?.depth == 2)
    let before = TimelineLayout(origin: 1000, end: 5000, now: 5000, lanes: viewer, activities: phases)
    let after = TimelineLayout(origin: 1000, end: 5000, now: 5000, lanes: grouped, activities: phases)
    for phase in phases {
        let a = try #require(before.rect(for: phase)), b = try #require(after.rect(for: phase))
        #expect(a.minX == b.minX && a.width == b.width)
        #expect(after.hitTest(CGPoint(x: b.midX, y: b.midY), phases: phases).map(\.id) == [phase.id])
        for lane in grouped where lane.track == "header" {
            #expect(b.maxY <= after.laneY(lane.id) || b.minY >= after.laneY(lane.id) + lane.height)
        }
    }
    #expect(TimelineLane.lanes(in: bundle, expanded: ["child"]) == viewer)
    let rootOnly = try agentBundle(children: false)
    #expect(TimelineLane.lanes(in: rootOnly, expanded: [], presentation: .panelGrouped) == TimelineLane.lanes(in: rootOnly, expanded: []))
}

@Test func oldAgentPayloadAndCoordinationKeepConservativeFallbacks() throws {
    let bundle = try agentBundle()
    #expect(bundle.agents[1].displayName == "Lorentz")
    #expect(bundle.agents[1].roundStatusLabel == "本轮已结束")
    #expect(bundle.agents[2].displayName == "Parfit")
    #expect(bundle.agents[2].roundStatusLabel == "状态未知")
    let legacy = try JSONDecoder().decode(TimelineAgent.self, from: JSONSerialization.data(withJSONObject: [
        "id": "01a15451-full-abc123", "thread_id": "01a15451-full-abc123", "name": "子智能体 · 01a15451"]))
    #expect(legacy.displayName == "l-abc123")
    let call = try agentPhase("root", type: "collabAgentToolCall", extra: ["coordination_action": "wait", "related_agent_ids": ["child"]])
    #expect(call.kind == "tool_execution" && call.displayTrack == "tool")
    #expect(call.presentationLabel(in: bundle.agents) == "协作 · 等待 · Lorentz")
    #expect(call.accessibleLabel(at: 5000, agents: bundle.agents).contains("Lorentz"))
    let oldCall = try agentPhase("root", type: "collabAgentToolCall")
    #expect(oldCall.presentationLabel() == "协作调用 · 对象未知")
    let ordinary = try agentPhase("root", type: "dynamicToolCall", extra: ["coordination_action": "wait", "related_agent_ids": ["child"]])
    #expect(!ordinary.isCoordination && ordinary.kindLabel == "工具执行")
}
