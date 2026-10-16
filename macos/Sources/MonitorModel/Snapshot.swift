import Foundation

public struct Overview: Decodable, Sendable {
    public let version: Int
    public let coverage: String
    public let generation_rate: Double?
    public let observed_subtotal: Double?
    public let estimated: Bool?
    public let active_thread_count: Int?
    public let quality: [String]?
}

public struct ReasoningRate: Decodable, Sendable {
    public let tokens_per_second: Double?
    public let model: String?
    public let turn_id: String?
}

public struct Usage: Decodable, Sendable {
    public let output_tokens: Int?
    public let reasoning_tokens: Int?
    public let latest_reasoning: ReasoningRate?
}

public struct Activity: Decodable, Identifiable, Sendable {
    public let id: String
    public let display_label: String?
    public let display_visible: Bool?
    public let status: String?
    public let active: Bool?
    public let duration_seconds: Double?
    public let start_known: Bool?
}

public struct Chat: Decodable, Identifiable, Sendable {
    public let threadID: String
    public let home: String?
    public let backend: String?
    public let name: String?
    public let cwd: String?
    public let model: String?
    public let status: String?
    public let connected: Bool?
    public let visible: Bool?
    public let has_active_work: Bool?
    public let display_label: String?
    public let display_seconds: Double?
    public let generation_rate: Double?
    public let generation_estimated: Bool?
    public let last_progress_seconds: Double?
    public let usage: Usage?
    public let activities: [Activity]?
    public let subagents: [String: String]?
    public let parent_thread_id: String?

    public var id: String { (home ?? backend ?? "") + "\u{0}" + threadID }
    public var scope: String { home ?? backend ?? "" }
    public var title: String { name.flatMap { $0.isEmpty ? nil : $0 } ?? "未命名聊天" }
    public var project: String { cwd.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "" }

    enum CodingKeys: String, CodingKey {
        case threadID = "id"
        case home, backend, name, cwd, model, status, connected, visible, has_active_work
        case display_label, display_seconds, generation_rate, generation_estimated
        case last_progress_seconds, usage, activities, subagents, parent_thread_id
    }
}

public struct Snapshot: Decodable, Sendable {
    public let schema_version: Int
    public let updated_at: Double?
    public let running: Bool?
    public let overview: Overview?
    public let threads: [Chat]
    public let backends: [String: Backend]?

    public static func decode(_ data: Data) throws -> Self {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.schema_version == 1 else { throw SnapshotError.unsupportedVersion }
        return value
    }

    public func isFresh(at date: Date = Date()) -> Bool {
        guard running == true, let updated_at else { return false }
        let age = date.timeIntervalSince1970 - updated_at
        return age >= -5 && age < 3
    }

    public func menuTitle(at date: Date = Date()) -> String {
        guard isFresh(at: date), let overview, overview.version == 1,
              overview.coverage == "complete", let rate = overview.generation_rate,
              rate.isFinite, rate >= 0 else { return "—" }
        if rate == 0 { return "0 t/s" }
        return (overview.estimated == true ? "≈" : "") + String(format: "%.1f t/s", rate)
    }

    public var observationNotice: ObservationNotice? {
        guard backends?.values.contains(where: { $0.connected == true }) == true else { return .waitingForConnection }
        guard let overview, overview.version == 1 else { return .totalUnavailable }
        guard overview.coverage != "complete" else { return nil }
        let quality = Set(overview.quality ?? [])
        if quality.contains("observation_gap") { return .observationGap }
        if !quality.isDisjoint(with: ["identity_conflict", "multiple_active_backends"]) { return .identityConflict }
        if quality.contains("unassociated_events") { return .associatingEvents }
        return .streamUnavailable
    }

    public var displayedChats: [Chat] {
        let eligible = threads.filter { $0.visible != false }
        let active = eligible.filter { $0.has_active_work == true }
        let recent = eligible.filter { $0.has_active_work != true && ($0.last_progress_seconds ?? .infinity) < 600 }
            .sorted { ($0.last_progress_seconds ?? .infinity) < ($1.last_progress_seconds ?? .infinity) }
        return active + recent.prefix(10)
    }

    public func children(of parent: Chat) -> [Chat] {
        displayedChats.filter { parentMap[$0.id] == parent.id }
    }

    public var rootChats: [Chat] {
        displayedChats.filter { parentMap[$0.id] == nil }
    }

    private var parentMap: [String: String] {
        var edges: [String: String] = [:]
        let chats = displayedChats
        for child in chats {
            let parents = chats.filter { parent in
                parent.scope == child.scope && parent.id != child.id &&
                    (child.parent_thread_id == parent.threadID ||
                     (child.parent_thread_id == nil && parent.subagents?[child.threadID] != nil))
            }
            if parents.count == 1 { edges[child.id] = parents[0].id }
        }
        let original = edges
        for (child, parent) in original {
            var seen: Set<String> = [child]
            var cursor: String? = parent
            while let current = cursor, !seen.contains(current) {
                seen.insert(current)
                cursor = original[current]
            }
            if cursor != nil { edges.removeValue(forKey: child) }
        }
        return edges
    }

    public func rows(expanded: Set<String>) -> [ChatRow] {
        let edges = parentMap
        let chats = displayedChats
        var result: [ChatRow] = []
        func append(_ chat: Chat, depth: Int) {
            result.append(ChatRow(chat: chat, depth: depth))
            if expanded.contains(chat.id) {
                for child in chats where edges[child.id] == chat.id { append(child, depth: depth + 1) }
            }
        }
        for chat in chats where edges[chat.id] == nil { append(chat, depth: 0) }
        return result
    }
}

public enum ObservationNotice: String, Sendable {
    case waitingForConnection = "NoticeWaitingForConnection"
    case totalUnavailable = "NoticeTotalUnavailable"
    case observationGap = "NoticeObservationGap"
    case identityConflict = "NoticeIdentityConflict"
    case associatingEvents = "NoticeAssociatingEvents"
    case streamUnavailable = "NoticeStreamUnavailable"
}

public struct ChatRow: Identifiable, Sendable {
    public let chat: Chat
    public let depth: Int
    public var id: String { chat.id }
}

public enum SnapshotError: Error { case unsupportedVersion }

public struct Backend: Decodable, Sendable {
    public let connected: Bool?
    public let implementation: Implementation?
    public var supportsObservationLease: Bool { implementation?.modules?["app_session"] != nil }
}

public struct Implementation: Decodable, Sendable {
    public let modules: [String: String]?
}

public enum MetricFormat {
    public static func count(_ value: Int?) -> String {
        guard let value, value >= 0 else { return "—" }
        return value.formatted(.number.locale(Locale(identifier: "en_US")))
    }

    public static func rate(_ value: Double?, estimated: Bool = false) -> String {
        guard let value, value.isFinite, value >= 0 else { return "—" }
        return (estimated ? "≈" : "") + String(format: "%.1f", value)
    }

    public static func duration(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0 else { return "—" }
        guard value >= 1 else { return "" }
        let seconds = Int(min(value, Double(Int.max / 2)))
        if seconds >= 3600 { return String(format: "%dh%02dm%02ds", seconds / 3600, seconds / 60 % 60, seconds % 60) }
        if seconds >= 60 { return String(format: "%dm%02ds", seconds / 60, seconds % 60) }
        return "\(seconds)s"
    }
}
