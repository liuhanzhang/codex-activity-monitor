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
    public let display_name: String?
    public let name_source: String?
    public let nickname: String?
    public let agent_path: String?
    public let status_scope: String?
    public let activity_quality: [String]?

    public var hasPartialInformation: Bool { !(quality ?? []).isEmpty || !(activity_quality ?? []).isEmpty }

    public var displayName: String {
        if let display_name { return display_name }
        let prefix = "子智能体 · "
        let legacy = name.hasPrefix(prefix) ? String(name.dropFirst(prefix.count)) : name
        return legacy == String(thread_id.prefix(8)) || legacy == thread_id ? String(thread_id.suffix(8)) : legacy
    }
    public var roundStatusLabel: String { roundStatusLabel(in: .chinese) }
    public func roundStatusLabel(in language: MonitorLanguage) -> String {
        guard status_scope == "selected_parent_turn" else { return ModelLocalizer.text("状态未知", language) }
        switch status {
        case "active": return ModelLocalizer.text("本轮活动中", language)
        case "waiting": return ModelLocalizer.text("本轮等待中", language)
        case "completed": return ModelLocalizer.text("本轮已结束", language)
        case "interrupted": return ModelLocalizer.text("本轮已停止", language)
        case "failed": return ModelLocalizer.text("本轮失败", language)
        default: return ModelLocalizer.text("状态未知", language)
        }
    }
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
    public let coordination_action: String?
    public let related_agent_ids: [String]?
    public let coordination_target_scope: String?
    public var label_message: DisplayMessage? = nil

    public var isCoordination: Bool {
        (namespace == "canonical" && item_type == "collabAgentToolCall") ||
        (namespace == "input" && item_type == "function_call" && coordination_action != nil)
    }
    public func coordinationTargets(in agents: [TimelineAgent], language: MonitorLanguage = .chinese) -> String {
        let ids = related_agent_ids ?? []
        if coordination_action == "wait" && coordination_target_scope == "global" { return ModelLocalizer.text("等待子智能体消息", language) }
        if ids.count > 1 { return String(format: ModelLocalizer.text("%d 个智能体", language), ids.count) }
        guard let id = ids.first else { return ModelLocalizer.text("对象未知", language) }
        return agents.first { $0.thread_id == id }?.displayName ?? ModelLocalizer.text("智能体 · ", language) + String(id.suffix(6))
    }
    public func presentationLabel(in agents: [TimelineAgent] = [], language: MonitorLanguage = .chinese) -> String {
        if isCoordination && coordination_action == "wait" && coordination_target_scope == "global" { return kindLabel(in: language) }
        if isCoordination { return kindLabel(in: language) + " · " + coordinationTargets(in: agents, language: language) }
        let kind = kindLabel(in: language), source = sourceLabel(in: language)
        return kind == source ? kind : kind + " · " + source
    }
    public func coordinationTargetDetails(in agents: [TimelineAgent], language: MonitorLanguage = .chinese) -> String {
        (related_agent_ids ?? []).map { id in
            (agents.first { $0.thread_id == id }?.displayName ?? ModelLocalizer.text("智能体", language)) + " · " + id
        }.joined(separator: "\n")
    }

    public func sourceLabel(in language: MonitorLanguage) -> String {
        if let label_message { return label_message.rendered(in: language) }
        // Known model labels are Codex Activity Monitor copy, including labels retained by older records.
        if namespace == "model" && (kind == "text" || kind == "reasoning") { return kindLabel(in: language) }
        if kind == "contextCompaction" { return kindLabel(in: language) }
        if language == .chinese { return label }
        if namespace == "input" || track == "tool" {
            // Older tool labels have no provenance. New records carry label_message.
            return ModelLocalizer.text("工具", language)
        }
        return kindLabel(in: language)
    }

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
    public var kindLabel: String { kindLabel(in: .chinese) }
    public func kindLabel(in language: MonitorLanguage) -> String {
        if isCoordination {
            let action = coordination_action == "wait" && coordination_target_scope == "global" ? ModelLocalizer.text("协作 · 等待子智能体消息", language) :
                (["spawn": ModelLocalizer.text("协作 · 派发", language), "send": ModelLocalizer.text("协作 · 发送消息", language), "followup": ModelLocalizer.text("协作 · 追加任务", language), "wait": ModelLocalizer.text("协作 · 等待", language),
                  "close": ModelLocalizer.text("协作 · 关闭", language), "resume": ModelLocalizer.text("协作 · 恢复", language)][coordination_action ?? ""] ?? ModelLocalizer.text("协作调用", language))
            return namespace == "input" ? ModelLocalizer.text("工具参数生成 · ", language) + action : action
        }
        return switch kind {
        case "reasoning": ModelLocalizer.text("思考", language)
        case "text": ModelLocalizer.text("正文输出", language)
        case "tool_input": ModelLocalizer.text("工具参数生成", language)
        case "file_edit_input": ModelLocalizer.text("编辑内容生成", language)
        case "input": ModelLocalizer.text("用户输入", language)
        case "tool_execution": ModelLocalizer.text("工具执行", language)
        case "contextCompaction": ModelLocalizer.text("整理上下文", language)
        default: language == .chinese ? label : ModelLocalizer.text("活动", language)
        }
    }
    public var statusLabel: String { statusLabel(in: .chinese) }
    public func statusLabel(in language: MonitorLanguage) -> String {
        switch status {
        case "active": ModelLocalizer.text("进行中", language)
        case "completed": ModelLocalizer.text("已完成", language)
        case "failed": ModelLocalizer.text("失败", language)
        case "interrupted": ModelLocalizer.text("已停止", language)
        case "declined": ModelLocalizer.text("已阻止，未执行", language)
        case "waiting": ModelLocalizer.text("等待执行", language)
        default: ModelLocalizer.text("部分观测", language)
        }
    }
    public func accessibleLabel(at now: Double, agents: [TimelineAgent] = [], language: MonitorLanguage = .chinese) -> String {
        let length = duration(at: now).map { $0 < 1 ? String(format: ModelLocalizer.text("%.0f 毫秒", language), $0 * 1000) : String(format: ModelLocalizer.text("%.1f 秒", language), $0) } ?? ModelLocalizer.text("耗时未知", language)
        return "\(presentationLabel(in: agents, language: language)) · \(statusLabel(in: language)) · \(length)"
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
    /// A completed turn must never follow today's clock, including with an unfinished child phase.
    public var viewportFollowTime: Double? {
        guard !quality.contains("clock_unreliable"),
              let turn = turns.first(where: { $0.id == selected_turn_id }) else { return nil }
        let time: Double?
        switch turn.status {
        case "active": time = now_ms
        case "completed", "failed", "interrupted": time = turn.end_ms ?? end_ms
        default: time = nil
        }
        return time.flatMap { $0.isFinite ? $0 : nil }
    }
    public static func decode(_ data: Data) throws -> Self {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.version == 1 else { throw SnapshotError.unsupportedVersion }
        return value
    }
}

public enum TimelinePresentation: Sendable, Equatable {
    case panelCompact, activityViewer
}

public struct TimelineLane: Identifiable, Sendable, Equatable {
    public let id: String
    public let threadID: String
    public let track: String
    public let label: String
    public let height: Double
    public let isChild: Bool
    public let depth: Int

    public init(id: String, threadID: String, track: String, label: String, height: Double, isChild: Bool, depth: Int = 0) {
        self.id = id; self.threadID = threadID; self.track = track; self.label = label
        self.height = height; self.isChild = isChild
        self.depth = depth
    }

    public static func lanes(in bundle: TimelineBundle, expanded: Set<String>, presentation: TimelinePresentation = .activityViewer) -> [Self] {
        if presentation == .panelCompact {
            var result: [Self] = []
            var visited: Set<String> = []
            func append(thread: String, depth: Int) {
                guard visited.insert(thread).inserted else { return }
                for track in ["model", "tool"] {
                    result.append(Self(id: thread + "-" + track, threadID: thread, track: track,
                        label: track == "model" ? "模型" : "工具", height: 18,
                        isChild: thread != bundle.thread_id, depth: min(2, depth)))
                }
                for child in bundle.agents where child.parent_thread_id == thread {
                    append(thread: child.thread_id, depth: depth + 1)
                }
            }
            append(thread: bundle.thread_id, depth: 0)
            for agent in bundle.agents where !visited.contains(agent.thread_id) {
                append(thread: agent.thread_id, depth: 1)
            }
            return result
        }
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
    case wide = 120

    public var pointsPerSecond: Double { (TimelineLayout.menuViewportWidth - 8) / rawValue }
    public var zoomedOut: Self { self == .detail ? .overview : .wide }
    public var zoomedIn: Self { self == .wide ? .overview : .detail }
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

    // Measured in the 420 pt compact panel, with 28 pt padding, 32 pt labels and a 5 pt gap.
    public static let menuViewportWidth: Double = 355
    public static let defaultScale: Double = TimelineZoom.overview.pointsPerSecond

    public static func visibleSeconds(in viewport: Double, zoom: TimelineZoom = .overview) -> Double {
        max(0, viewport - 8) / zoom.pointsPerSecond
    }

    public init(origin: Double, end: Double, now: Double, lanes: [TimelineLane],
                activities: [TimelinePhase] = [], zoom: TimelineZoom = .overview) {
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
    public func laneY(_ id: String) -> Double {
        Self.axisHeight + lanes.prefix { $0.id != id }.reduce(0) { $0 + $1.height }
    }
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
            // An observation boundary cannot supply an unknown lifecycle end.
            let width = max(shortActivityWidth, (phase.duration(at: now) ?? 0) * points)
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
