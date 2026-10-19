import Foundation
import CoreGraphics

public struct TimelineSummary: Decodable, Sendable {
    public let version: Int
    public let revision: Int?
    public let available: Bool?
    public let quality: [String]?
}

public struct TimelineTurn: Decodable, Identifiable, Sendable {
    public let turn_id: String
    public let thread_id: String
    public let status: String
    public let start_ms: Double?
    public let end_ms: Double?
    public var id: String { turn_id }
}

public struct TimelineAgent: Decodable, Identifiable, Sendable {
    public let id: String
    public let thread_id: String
    public let name: String
    public let parent_thread_id: String?
    public let quality: [String]?
    public let status: String?
}

public struct TimelinePhase: Decodable, Identifiable, Sendable {
    public let id: String
    public let scope_id: String
    public let thread_id: String
    public let turn_id: String
    public let item_id: String
    public let item_type: String?
    public let namespace: String
    public let track: String
    public let kind: String
    public let label: String
    public let status: String
    public let start_ms: Double?
    public let end_ms: Double?
    public let observed_until_ms: Double?
    public let start_source: String?
    public let end_source: String?
    public let quality: [String]?
    public let is_initial: Bool?
    public let related_phase_ids: [String]?

    public var anchor: Double? { start_ms ?? end_ms ?? observed_until_ms }
    public var isActive: Bool { status == "active" }
    // Also handle a first-release collector/fixture while the GUI is upgraded.
    public var displayTrack: String { item_type == "contextCompaction" ? "model" : track }
    public var isTimelineVisible: Bool { displayTrack != "marker" || kind == "input" }
    public func duration(at now: Double) -> Double? {
        guard let start_ms, Set(quality ?? []).isDisjoint(with: ["clock_conflict", "wall_clock_changed"]),
              let end = end_ms ?? (isActive ? now : nil), end >= start_ms else { return nil }
        return (end - start_ms) / 1000
    }
    public var kindLabel: String {
        switch kind {
        case "reasoning": "思考"
        case "text": "正文输出"
        case "tool_input": "工具参数生成"
        case "file_edit_input": "编辑内容生成"
        case "input": "用户输入"
        case "tool_execution": "工具执行"
        case "contextCompaction": "整理上下文"
        default: label
        }
    }
    public var statusLabel: String {
        switch status {
        case "active": "进行中"
        case "completed": "已完成"
        case "failed": "失败"
        case "interrupted": "已停止"
        case "declined": "已阻止，未执行"
        case "waiting": "等待执行"
        default: "部分观测"
        }
    }
    public func accessibleLabel(at now: Double) -> String {
        let length = duration(at: now).map { $0 < 1 ? String(format: "%.0f 毫秒", $0 * 1000) : String(format: "%.1f 秒", $0) } ?? "耗时未知"
        return "\(kindLabel) · \(label) · \(statusLabel) · \(length)"
    }
}

public struct TimelineBundle: Decodable, Sendable {
    public let version: Int
    public let scope_id: String
    public let thread_id: String
    public let selected_turn_id: String?
    public let current_turn_id: String?
    public let turns: [TimelineTurn]
    public let agents: [TimelineAgent]
    public let phases: [TimelinePhase]
    public let markers: [TimelinePhase]
    public let origin_ms: Double?
    public let end_ms: Double?
    public let now_ms: Double
    public let revision: String
    public let quality: [String]
    public let earlier_active_turns: [String]

    public var initialInput: TimelinePhase? { markers.first { $0.kind == "input" && $0.is_initial == true } }
    public var allActivities: [TimelinePhase] { (phases + markers).filter(\.isTimelineVisible) }
    public static func decode(_ data: Data) throws -> Self {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.version == 1 else { throw SnapshotError.unsupportedVersion }
        return value
    }
}

public struct ActivitySection: Decodable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let format: String
    public var text: String
    public let offset: Int?
    public let total_chars: Int?
    public var next_offset: Int?
    public let notice: String?
}

public struct ActivityDetail: Decodable, Sendable {
    public let version: Int
    public let state: String
    public let phase: TimelinePhase?
    public var sections: [ActivitySection]
    public let attachments: [String]
    public let source: String?
    public func retainingContent(from prior: Self) -> Self {
        Self(version: version, state: state, phase: phase, sections: prior.sections,
             attachments: prior.attachments, source: prior.source)
    }
}

public struct TimelineLane: Identifiable, Sendable, Equatable {
    public let id: String
    public let threadID: String
    public let track: String
    public let label: String
    public let height: Double
    public let isChild: Bool

    public init(id: String, threadID: String, track: String, label: String, height: Double, isChild: Bool) {
        self.id = id; self.threadID = threadID; self.track = track; self.label = label
        self.height = height; self.isChild = isChild
    }

    public static func lanes(in bundle: TimelineBundle, expanded: Set<String>) -> [Self] {
        var lanes = [Self(id: "root-model", threadID: bundle.thread_id, track: "model", label: "模型", height: 21, isChild: false),
                     Self(id: "root-tool", threadID: bundle.thread_id, track: "tool", label: "工具", height: 21, isChild: false)]
        for agent in bundle.agents where agent.thread_id != bundle.thread_id {
            lanes.append(Self(id: agent.id, threadID: agent.thread_id, track: expanded.contains(agent.id) ? "header" : "summary",
                              label: agent.name, height: 25, isChild: true))
            if expanded.contains(agent.id) {
                lanes.append(Self(id: agent.id + "-model", threadID: agent.thread_id, track: "model", label: "模型", height: 20, isChild: true))
                lanes.append(Self(id: agent.id + "-tool", threadID: agent.thread_id, track: "tool", label: "工具", height: 20, isChild: true))
            }
        }
        return lanes
    }
}

public enum TimelineZoom: Double, CaseIterable, Sendable {
    case detail = 30
    case overview = 60

    public var pointsPerSecond: Double { (TimelineLayout.menuViewportWidth - 8) / rawValue }
}

public struct TimelineLayout: Sendable {
    public let origin: Double
    public let end: Double
    public let now: Double
    public let pointsPerSecond: Double
    public let lanes: [TimelineLane]
    private let rectangles: [String: CGRect]
    public static let axisHeight: Double = 25
    public static let shortActivityWidth: Double = 3

    public static let menuViewportWidth: Double = 291
    public static let defaultScale: Double = TimelineZoom.detail.pointsPerSecond

    public static func visibleSeconds(in viewport: Double, zoom: TimelineZoom = .detail) -> Double {
        max(0, viewport - 8) / zoom.pointsPerSecond
    }

    public init(origin: Double, end: Double, now: Double, lanes: [TimelineLane],
                activities: [TimelinePhase] = [], zoom: TimelineZoom = .detail) {
        self.origin = origin; self.end = max(origin, end); self.now = now
        let points = zoom.pointsPerSecond
        self.pointsPerSecond = points; self.lanes = lanes
        var frames: [String: CGRect] = [:]
        for phase in activities {
            if let rect = Self.sourceRect(for: phase, origin: origin, now: now, points: points, lanes: lanes) {
                frames[phase.id] = rect
            }
        }
        rectangles = frames
    }

    public var height: Double { Self.axisHeight + lanes.reduce(0) { $0 + $1.height } + 8 }
    public func width(viewport: Double) -> Double {
        max(viewport, (end - origin) / 1000 * pointsPerSecond + 8, (rectangles.values.map(\.maxX).max() ?? 0) + 4)
    }
    public func x(at milliseconds: Double) -> Double { (milliseconds - origin) / 1000 * pointsPerSecond + 4 }
    public func time(at x: Double) -> Double { origin + (x - 4) / pointsPerSecond * 1000 }

    public func rect(for phase: TimelinePhase) -> CGRect? {
        rectangles[phase.id] ?? Self.sourceRect(for: phase, origin: origin, now: now, points: pointsPerSecond, lanes: lanes)
    }
    private static func sourceRect(for phase: TimelinePhase, origin: Double, now: Double,
                                   points: Double, lanes: [TimelineLane]) -> CGRect? {
        guard phase.isTimelineVisible, let anchor = phase.anchor, anchor.isFinite else { return nil }
        let x = (anchor - origin) / 1000 * points + 4
        if phase.displayTrack == "marker" {
            if phase.is_initial == true { return nil }
            return CGRect(x: x - 3, y: Self.axisHeight - 8, width: 6, height: 6)
        }
        var y = Self.axisHeight
        for lane in lanes {
            defer { y += lane.height }
            guard lane.threadID == phase.thread_id, lane.track == phase.displayTrack || lane.track == "summary" else { continue }
            let end = phase.start_ms == nil ? anchor : (phase.end_ms ?? (phase.isActive ? now : phase.observed_until_ms) ?? anchor)
            let width = phase.displayTrack == "tool"
                ? max(shortActivityWidth, (phase.duration(at: now) ?? 0) * points)
                : max(shortActivityWidth, max(0, end - anchor) / 1000 * points)
            return CGRect(x: x, y: y + (lane.height - 11) / 2, width: width, height: 11)
        }
        return nil
    }
    public func hitRect(for phase: TimelinePhase) -> CGRect? {
        rect(for: phase)
    }
    public func hitTest(_ point: CGPoint, phases: [TimelinePhase]) -> [TimelinePhase] {
        let actual = phases.filter { rect(for: $0)?.contains(point) == true }
        let candidates = actual.isEmpty ? phases.filter { hitRect(for: $0)?.contains(point) == true } : actual
        return candidates.sorted { ($0.anchor ?? 0, $0.id) < ($1.anchor ?? 0, $1.id) }
    }
    public func readingOffset(from old: Self, offset: Double, selectedID: String?, viewport: Double) -> Double {
        if let selectedID, let before = old.rectangles[selectedID], let after = rectangles[selectedID],
           before.maxX >= offset, before.minX <= offset + viewport {
            return max(0, offset + after.minX - before.minX)
        }
        if old.pointsPerSecond != pointsPerSecond {
            return max(0, x(at: old.time(at: offset + viewport / 2)) - viewport / 2)
        }
        if old.origin != origin {
            return max(0, x(at: old.time(at: offset)))
        }
        return offset
    }
    public var tickSeconds: Double {
        [1.0, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600].first { $0 * pointsPerSecond >= 45 } ?? 3600
    }
}
