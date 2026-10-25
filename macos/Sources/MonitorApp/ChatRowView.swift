import SwiftUI
import MonitorModel

struct ChatRowView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    let chat: Chat
    @Bindable var store: DashboardStore
    @State private var hovered = false
    private var selected: Bool { store.selectedChatKey == chat.id }
    private var live: Bool { store.sourceAvailable(for: chat) }
    private var working: Bool { store.snapshot?.hasActiveWork(in: chat) == true }
    private var unassigned: Bool { store.isUnassigned(chat) }
    private var actionLabel: String {
        if !live { return L("状态暂不可用") }
        if working {
            if chat.has_active_work != true { return L("子代理工作中") }
            return chat.activityLabel(language)
        }
        return switch chat.status {
        case "completed": L("已完成")
        case "failed": L("失败")
        case "interrupted": L("已停止")
        default: L("状态未知")
        }
    }

    var body: some View {
        Button { store.selectChat(chat) } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(chat.localizedTitle(language)).font(.system(size: 12, weight: selected ? .medium : .regular))
                        .lineLimit(1).truncationMode(.tail)
                    Spacer(minLength: 0)
                    if live, working, let rate = chat.generation_rate, rate.isFinite, rate >= 0 {
                        Text(MetricFormat.compactSpeed(rate)).font(.system(size: 11)).monospacedDigit().fixedSize()
                            .help(MetricFormat.speed(rate) + " · " + L("生成是实时窗口估算，统计已接收的独立生成内容。"))
                    }
                }
                HStack(spacing: 5) {
                    if chat.isKnownSubagent {
                        Text(L(unassigned ? "归属待确认" : "子任务")).foregroundStyle(.secondary)
                    }
                    if !chat.project.isEmpty {
                        Text(chat.project).lineLimit(1).truncationMode(.middle)
                        Text("·").accessibilityHidden(true)
                    }
                    Text(actionLabel).lineLimit(1).layoutPriority(1)
                    if live, working, let seconds = chat.display_seconds, seconds.isFinite, seconds >= 1 {
                        Text("·").accessibilityHidden(true)
                        Text(MetricFormat.duration(seconds)).monospacedDigit().fixedSize()
                    }
                }.font(.system(size: 10)).foregroundStyle(.secondary)
            }
            .foregroundStyle(working || selected ? .primary : .secondary)
            .padding(.horizontal, 10).frame(maxWidth: .infinity, minHeight: PanelLayoutBudget.rowHeight,
                                            maxHeight: PanelLayoutBudget.rowHeight, alignment: .leading)
            .background(selected ? Color.accentColor.opacity(0.09) : hovered ? Color.primary.opacity(0.035) : .clear,
                        in: RoundedRectangle(cornerRadius: 5))
            .overlay(alignment: .leading) {
                if selected { RoundedRectangle(cornerRadius: 1).fill(Color.accentColor).frame(width: 2, height: 26) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).onHover { hovered = $0 }
        .help([chat.localizedTitle(language), chat.cwd, actionLabel].compactMap { $0 }.joined(separator: "\n"))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("monitor.chat.\(chat.scope).\(chat.threadID)")
    }
}
