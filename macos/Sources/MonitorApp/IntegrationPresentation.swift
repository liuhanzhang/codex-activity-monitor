import Foundation
import MonitorModel

extension DashboardStore {
    var integrationEnabled: Bool { management?.integration_status?.confirmedEnabled ?? management?.configured ?? false }
    var integrationCanToggle: Bool {
        !readOnly && !busy && management != nil && management?.waitingForCleanup != true &&
        !managementCheckFailed && (management?.integration_status == nil || management?.integration_status?.confirmedEnabled != nil)
    }
    var integrationStatusText: String {
        let language = language
        func text(_ key: String) -> String { localizedText(key, language: language) }
        if busy { return text("正在处理…") }
        if managementCheckFailed { return text("暂时无法确认配置状态，保留上次结果。") }
        guard let management else { return text("正在检查接入状态…") }
        guard let status = management.integration_status, status.version == 1 else {
            return management.localizedLabel(language)
        }
        if status.configuration == "partial" || status.configuration == "unknown" {
            return text("接入操作未完成或配置已改变，请重试检查或继续恢复。")
        }
        if status.confirmedEnabled == false {
            if management.waitingForCleanup { return management.localizedLabel(language) }
            return text("已关闭，原配置已恢复。")
        }
        let matching = fresh && snapshot?.backends?.values.contains {
            $0.connected == true && $0.configuration_matches == true &&
            $0.configuration_revision == status.configuration_revision && status.configuration_revision != nil
        } == true
        if status.restart == "required" {
            return text(matching ? "部分接入已生效；仍有先前启动的进程，任务结束后请重开 Codex 完成切换。" : "已配置，任务结束后请完全退出并重新打开 Codex。")
        }
        if !fresh { return text("配置已开启，采集连接暂不可用。") }
        if matching {
            return text((status.unknown_bridges ?? 0) > 0 ? "部分接入已生效，其他进程暂无法确认。" : "已生效，正在监测已接入的本机会话。")
        }
        if status.runtime == "waiting_for_codex" { return text("已配置，下次打开 Codex 后接入。") }
        return text("已配置，等待确认 Codex 接入；没有聊天不表示接入失败。")
    }

    var diagnosticComponents: [ComponentHealth] {
        _ = diagnosticPulse
        let disabled = management?.waitingForCleanup == true || management?.integration_status?.confirmedEnabled == false ||
            (management != nil && management?.configured == false && management?.state != "error")
        var values: [ComponentHealth] = [.init(component: "connection", state: disabled ? "disabled" : busy || management == nil ? "unknown" : fresh ? "healthy" : "unavailable",
                                              code: disabled ? "stop_completed" : fresh ? "state_connected" : "state_disconnected")]
        if !disabled {
            if fresh {
                values += snapshot?.diagnostics?.supportedComponents ?? []
                if snapshot?.diagnostics?.version != 1 {
                    values.append(.init(component: "diagnostics", state: "unknown", code: "diagnostics_unsupported"))
                }
            }
            else { values.append(.init(component: "collector", state: "unknown", code: "waiting_for_connection")) }
        }
        if management?.integration_status?.restart == "required" {
            values.append(.init(component: "integration", state: "pending", code: "restart_required"))
        }
        if management?.integration_status?.configuration == "partial" {
            values.append(.init(component: "integration_configuration", state: "unknown", code: "configuration_changed"))
        }
        if managementCheckFailed {
            values.append(.init(component: "management", state: "unknown", code: "management_status_failed"))
        }
        if recorder.failure != nil { values.append(.init(component: "gui_logging", state: "degraded", code: "logging_unavailable")) }
        if recorder.recentlyDropped { values.append(.init(component: "log_retention", state: "degraded", code: "log_dropped", count: recorder.dropped)) }
        if recorder.previousExitUnclean { values.append(.init(component: "previous_run", state: "unknown", code: "previous_exit_unclean")) }
        return values
    }

    var diagnosticDirectory: String? {
        if readOnly {
            return diagnosticFixtureDirectory
        }
        return dataRoot + "/diagnostics"
    }
}
