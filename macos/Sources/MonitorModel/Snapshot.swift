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

public struct Usage: Decodable, Sendable {
    public let turn_id: String?
    public let output_tokens: Int?
    public let reasoning_tokens: Int?
}

public struct Activity: Decodable, Identifiable, Sendable {
    public let id: String
    public let display_label: String?
    public let display_message: DisplayMessage?
    public let display_visible: Bool?
    public let status: String?
    public let active: Bool?
    public let duration_seconds: Double?
    public let start_known: Bool?
}

public enum MenuMode: String, Sendable { case speed, idle, thinking, executing, unavailable }

public struct MenuPresentation: Decodable, Sendable {
    public let version: Int
    public let mode: String
    public let active_conversation_count: Int
    public let reason: String?
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
    public let ephemeral: Bool?
    public let has_active_work: Bool?
    public let display_label: String?
    public let display_message: DisplayMessage?
    public let display_seconds: Double?
    public let generation_rate: Double?
    public let generation_estimated: Bool?
    public let last_progress_seconds: Double?
    public let usage: Usage?
    public let activities: [Activity]?
    public let subagents: [String: String]?
    public let parent_thread_id: String?
    public let parent_source: String?
    public let parent_quality: [String]?
    public let thread_source: String?
    public let context_only: Bool?
    public let turn_id: String?

    public var id: String { (home ?? backend ?? "") + "\u{0}" + threadID }
    public var scope: String { home ?? backend ?? "" }
    public var title: String { localizedTitle(.chinese) }
    public func localizedTitle(_ language: MonitorLanguage) -> String { name.flatMap { $0.isEmpty ? nil : $0 } ?? ModelLocalizer.text("未命名聊天", language) }
    public func activityLabel(_ language: MonitorLanguage) -> String {
        if let display_message { return display_message.rendered(in: language) }
        if language == .chinese, let display_label { return display_label }
        let key = ["completed": "activity.completed", "failed": "activity.failed", "interrupted": "activity.stopped", "idle": "activity.idle"][status ?? ""] ?? "activity.unknown"
        return DisplayMessage(key).rendered(in: language)
    }
    public var project: String { cwd.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "" }
    public var isKnownSubagent: Bool { thread_source != "user" && (thread_source == "subagent" || parent_thread_id != nil) }
    public var isTerminal: Bool { ["completed", "failed", "interrupted"].contains(status ?? "") }

    enum CodingKeys: String, CodingKey {
        case threadID = "id"
        case home, backend, name, cwd, model, status, connected, visible, ephemeral, has_active_work
        case display_label, display_message, display_seconds, generation_rate, generation_estimated
        case last_progress_seconds, usage, activities, subagents, parent_thread_id, parent_source, parent_quality, thread_source, context_only, turn_id
    }
}

public struct Snapshot: Decodable, Sendable {
    public let schema_version: Int
    public let updated_at: Double?
    public let running: Bool?
    public let overview: Overview?
    public let menu_presentation: MenuPresentation?
    public let timeline_summary: TimelineSummary?
    public let threads: [Chat]
    public let backends: [String: Backend]?

    public let projection: SnapshotProjection

    private enum CodingKeys: String, CodingKey {
        case schema_version, updated_at, running, overview, menu_presentation, timeline_summary, threads, backends
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schema_version = try values.decode(Int.self, forKey: .schema_version)
        guard schema_version == 1 else { throw SnapshotError.unsupportedVersion }
        updated_at = try values.decodeIfPresent(Double.self, forKey: .updated_at)
        running = try values.decodeIfPresent(Bool.self, forKey: .running)
        overview = try values.decodeIfPresent(Overview.self, forKey: .overview)
        menu_presentation = try values.decodeIfPresent(MenuPresentation.self, forKey: .menu_presentation)
        timeline_summary = try values.decodeIfPresent(TimelineSummary.self, forKey: .timeline_summary)
        threads = try values.decode([Chat].self, forKey: .threads)
        backends = try values.decodeIfPresent([String: Backend].self, forKey: .backends)
        guard Set(threads.map(\.id)).count == threads.count else { throw SnapshotError.duplicateIdentity }
        projection = SnapshotProjection(threads: threads)
    }

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

    public func menuTitle(at date: Date = Date(), connectionFresh: Bool? = nil) -> String {
        guard connectionFresh ?? isFresh(at: date), let overview, overview.version == 1,
              overview.coverage == "complete", let rate = overview.generation_rate,
              rate.isFinite, rate >= 0 else { return "—" }
        return MetricFormat.speed(rate)
    }

    public func menuMode(at date: Date = Date(), connectionFresh: Bool? = nil) -> MenuMode {
        guard menuTitle(at: date, connectionFresh: connectionFresh) != "—" else { return .unavailable }
        guard let presentation = menu_presentation else { return .speed }
        guard presentation.version == 1, presentation.active_conversation_count >= 0,
              let mode = MenuMode(rawValue: presentation.mode), mode != .idle else { return .unavailable }
        if mode == .speed && presentation.reason == "idle" && presentation.active_conversation_count == 0 {
            guard backends?.values.contains(where: { $0.connected == true }) == true else { return .unavailable }
            return .idle
        }
        if mode == .thinking || mode == .executing {
            return presentation.active_conversation_count == 1 ? mode : .speed
        }
        return mode
    }

    public var activeConversationCount: Int {
        if let value = menu_presentation, value.version == 1, value.active_conversation_count >= 0 {
            return value.active_conversation_count
        }
        return rootChats.filter { hasActiveWork(in: $0) }.count
    }

    public var observationNotice: ObservationNotice? {
        if overview?.version == 1, overview?.quality?.contains("identity_budget") == true { return .resourceLimit }
        guard backends?.values.contains(where: { $0.connected == true }) == true else { return .waitingForConnection }
        guard let overview, overview.version == 1 else { return .totalUnavailable }
        guard overview.coverage != "complete" else { return nil }
        let quality = Set(overview.quality ?? [])
        if quality.contains("observation_gap") { return .observationGap }
        if !quality.isDisjoint(with: ["identity_conflict", "multiple_active_backends"]) { return .identityConflict }
        if quality.contains("unassociated_events") { return .associatingEvents }
        return .streamUnavailable
    }

    public var displayedChats: [Chat] { projection.initial.members }
    public var rootChats: [Chat] { projection.initial.roots }
    public var unassignedChats: [Chat] { projection.initial.unassigned }
    public func children(of parent: Chat) -> [Chat] { projection.children(of: parent) }
    public func hasActiveWork(in root: Chat) -> Bool { projection.hasActiveWork(in: root) }
    public func rows(expanded: Set<String>) -> [ChatRow] { projection.rows(roots: rootChats, expanded: expanded) }
    public func rows(roots: [Chat], expanded: Set<String>, order: [String: Int] = [:]) -> [ChatRow] {
        projection.rows(roots: roots, expanded: expanded, order: order)
    }

}

public enum ObservationNotice: String, Sendable {
    case waitingForConnection = "NoticeWaitingForConnection"
    case totalUnavailable = "NoticeTotalUnavailable"
    case observationGap = "NoticeObservationGap"
    case identityConflict = "NoticeIdentityConflict"
    case associatingEvents = "NoticeAssociatingEvents"
    case streamUnavailable = "NoticeStreamUnavailable"
    case resourceLimit = "NoticeObservationResourceLimit"
}

public struct ChatRow: Identifiable, Sendable {
    public let chat: Chat
    public let depth: Int
    public var id: String { chat.id }
}

public enum SnapshotError: Error { case unsupportedVersion, duplicateIdentity }

public struct Backend: Decodable, Sendable {
    public let connected: Bool?
    public let implementation: Implementation?
    public var supportsObservationLease: Bool { implementation?.modules?["observer_permit"] != nil }
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
        return String(format: "%.0f", locale: Locale(identifier: "en_US_POSIX"), value.rounded(.toNearestOrAwayFromZero))
    }

    public static func speed(_ value: Double?) -> String {
        let number = rate(value)
        return number == "—" ? number : number + " T/s"
    }

    public static func compactSpeed(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0 else { return "—" }
        return value.rounded(.toNearestOrAwayFromZero) > 99999 ? ">99999 T/s" : speed(value)
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
