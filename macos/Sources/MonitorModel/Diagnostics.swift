import Foundation

public struct IntegrationStatus: Decodable, Sendable {
    public let version: Int
    public let configuration: String
    public let runtime: String
    public let restart: String
    public let operation: String
    public let checked_at: Double?
    public let configuration_revision: String?
    public let matching_bridges: Int?
    public let old_bridges: Int?
    public let unknown_bridges: Int?
    public let reasons: [String]?
    public var confirmedEnabled: Bool? {
        guard version == 1 else { return nil }
        switch configuration { case "enabled": return true; case "disabled": return false; default: return nil }
    }
}

public struct ComponentHealth: Codable, Sendable, Identifiable {
    public let component: String
    public let agent: String?
    public let instance_id: String?
    public let state: String
    public let code: String
    public let count: Int?
    public var id: String { component }
    public init(component: String, state: String, code: String, count: Int? = nil) {
        self.component = component; self.state = state; self.code = code; self.count = count
        agent = "codex"; instance_id = nil
    }
    public func summary(_ language: MonitorLanguage) -> String { DiagnosticEvent.summary(code, language) }
}

public struct DiagnosticSnapshot: Decodable, Sendable {
    public let version: Int
    public let instance_id: String
    public let components: [ComponentHealth]
    public var supportedComponents: [ComponentHealth] { version == 1 ? Array(components.prefix(32)) : [] }
}

public struct DiagnosticEvent: Codable, Sendable, Identifiable {
    public var timestamp: Date? = nil
    public var version = 1
    public let event_id: String
    public let sequence: Int?
    public let at: String
    public let first_at: String?
    public let level: String
    public let component: String
    public let agent: String?
    public let instance_id: String
    public let code: String
    public let message_key: String?
    public let params: [String: MessageValue]?
    public let transition: String?
    public let count: Int?
    public let operation_id: String?
    private enum CodingKeys: String, CodingKey {
        case version, event_id, sequence, at, first_at, level, component, agent, instance_id, code, message_key, params, transition, count, operation_id
    }
    public var id: String { event_id }
    public init(instance: String, sequence: Int, code: String, level: String, component: String,
                params: [String: MessageValue] = [:], operationID: String? = nil, count: Int = 1,
                firstAt: String? = nil, at: String? = nil) {
        instance_id = instance; self.sequence = sequence; event_id = instance + "-" + String(sequence)
        self.at = at ?? ISO8601DateFormatter().string(from: Date()); first_at = firstAt; self.level = level; self.component = component
        agent = "codex"; self.code = code; self.params = params; self.count = count
        message_key = "diagnostics." + code; transition = nil; operation_id = operationID
        timestamp = Date()
    }
    public static func summary(_ code: String, _ language: MonitorLanguage) -> String {
        let key = "diagnostics." + code
        return CoreLocalizer.contains(key, language: language) ? DisplayMessage(key).rendered(in: language) : code
    }
    public func summary(_ language: MonitorLanguage) -> String { Self.summary(code, language) }
    public var raw: String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        return (try? encoder.encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? code
    }
}
