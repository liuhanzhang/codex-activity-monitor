import SwiftUI
import MonitorModel

struct ChatRowView: View {
    let chat: Chat
    let depth: Int
    @Bindable var store: DashboardStore
    var horizontalPadding: CGFloat = 14
    @State private var hovered = false
    private var expanded: Bool { store.expanded.contains(chat.id) }
    private var live: Bool { store.fresh && chat.connected != false }
    private var working: Bool { store.snapshot?.hasActiveWork(in: chat) == true }
    private var actionLabel: String {
        if !live { return L("连接中断") }
        if working {
            if chat.has_active_work != true { return L("子代理工作中") }
            return chat.display_label ?? L("状态暂不可用")
        }
        switch chat.status {
        case "completed": return L("已完成")
        case "failed": return L("失败")
        case "interrupted": return L("已停止")
        default: return chat.display_label ?? L("暂无活动")
        }
    }

    var body: some View {
        PanelDisclosureGroup(isExpanded: Binding(get: { expanded }, set: { store.setExpanded($0, for: chat) })) {
            ChatDetailsView(chat: chat, timeline: store.timeline(for: chat),
                            pollingEnabled: store.panelVisible, presentationID: store.presentationID)
                .padding(.top, 10).padding(.bottom, 14)
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(chat.title).font(.system(size: 13, weight: .semibold))
                        .lineLimit(2).truncationMode(.tail).help(chat.title)
                    Spacer(minLength: 0)
                    if live, working, let rate = chat.generation_rate, rate.isFinite, rate >= 0 {
                        Text(MetricFormat.compactSpeed(rate)).font(.system(size: 11))
                            .monospacedDigit().foregroundStyle(.secondary).fixedSize()
                            .help(MetricFormat.speed(rate) + " · " + L("生成是实时窗口估算，统计已接收的独立生成内容。"))
                    }
                }
                if !chat.project.isEmpty {
                    Text(chat.project).font(.system(size: 10)).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle).help(chat.cwd ?? chat.project)
                }
                HStack(spacing: 5) {
                    if working || chat.status == "failed" {
                        Circle().fill(chat.status == "failed" ? Color.red : Color.primary.opacity(0.65))
                            .frame(width: 5, height: 5).accessibilityHidden(true)
                    }
                    Text(actionLabel).lineLimit(1).help(actionLabel)
                    if live, working, let seconds = chat.display_seconds, seconds.isFinite, seconds >= 1 {
                        Text("·").accessibilityHidden(true)
                        Text(MetricFormat.duration(seconds)).monospacedDigit()
                    }
                }.font(.system(size: 11)).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 10)
            .alignmentGuide(VerticalAlignment.center) { $0[.top] + 18 }
            .background(hovered ? Color(nsColor: .controlColor).opacity(0.45) : .clear,
                        in: RoundedRectangle(cornerRadius: 4))
            .onHover { hovered = $0 }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("monitor.chat.\(chat.threadID)")
        }
        .font(.system(size: 12))
        .padding(.leading, CGFloat(min(depth, 2) * 12)).padding(.horizontal, horizontalPadding)
    }
}
