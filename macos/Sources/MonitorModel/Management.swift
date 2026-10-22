import Foundation

public struct ObservationLease: Codable, Sendable {
    public var version: Int
    public var id: String
    public var owner_pid: Int32
    public var heartbeat_at: Double
    public var active: Bool
}

public struct PendingDependency: Decodable, Sendable {
    public let pid: Int?
    public let reason: String?
}

public struct ManagementState: Decodable, Sendable {
    public let version: Int?
    public let ok: Bool?
    public let state: String?
    public let runtime: String?
    public let purge: Bool?
    public let pending: [PendingDependency]?
    public let code: String?
    public let error: String?
    public let display_message: DisplayMessage?
    public let language: String?
    public let session: ObservationLease?

    public var configured: Bool { state == "configured" }
    public var waitingForCleanup: Bool { state == "pending_cleanup" || state == "restoring" }
    public var label: String { localizedLabel(.chinese) }
    public func localizedLabel(_ language: MonitorLanguage) -> String {
        ModelLocalizer.text(labelKey, language)
    }
    private var labelKey: String {
        switch state {
        case "configured": "已配置"
        case "pending_cleanup":
            pending?.isEmpty != false ? "配置已恢复，等待完成清理" :
                (pending?.contains { $0.reason == "process_visibility_unknown" } == true ? "配置已恢复，暂时无法确认 Codex 是否已退出" : "配置已恢复，等待 Codex 退出后完成清理")
        case "restoring": "正在恢复配置"
        case "restored": "已恢复 · 点击配置可重新接入"
        case "purged": "本机数据已清理"
        case "error": "上次操作未完成"
        case "configuring": "正在配置"
        default: "尚未配置"
        }
    }
}
