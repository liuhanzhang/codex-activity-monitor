import SwiftUI
import MonitorModel

struct ChatRowView: View {
    let chat: Chat
    let depth: Int
    @Bindable var store: DashboardStore
    private var expanded: Bool { store.expanded.contains(chat.id) }
    private var live: Bool { store.fresh && chat.connected != false }
    private var actionLabel: String {
        if !live { return L("连接中断") }
        if chat.has_active_work != true, store.snapshot?.hasActiveWork(in: chat) == true { return L("子代理工作中") }
        return chat.display_label ?? L("状态暂不可用")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                if !store.expanded.insert(chat.id).inserted { store.expanded.remove(chat.id) }
            } label: {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
                        .frame(width: 10).padding(.top, 3)
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text(chat.title).font(.system(size: 13, weight: .medium))
                                .lineLimit(1).truncationMode(.tail).help(chat.title)
                            Spacer(minLength: 0)
                            Text(MetricFormat.compactSpeed(live ? chat.generation_rate : nil))
                                .font(.system(size: 12, weight: .medium)).monospacedDigit().fixedSize()
                                .help(MetricFormat.speed(live ? chat.generation_rate : nil) + " · " + L("生成是实时窗口估算，统计已接收的独立生成内容。"))
                        }
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            if !chat.project.isEmpty {
                                Text(chat.project).lineLimit(1).truncationMode(.middle)
                                    .layoutPriority(-1).help(chat.cwd ?? chat.project)
                                Text("·")
                            }
                            Text(actionLabel).lineLimit(1).help(actionLabel)
                            Spacer(minLength: 2)
                            Text(live ? MetricFormat.duration(chat.display_seconds) : "—")
                                .monospacedDigit().fixedSize()
                        }.font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                }.contentShape(Rectangle())
            }
            .buttonStyle(.plain).padding(.vertical, 12)
            .accessibilityIdentifier("monitor.chat.\(chat.threadID)")
            .accessibilityValue(expanded ? L("已展开") : L("已折叠"))
            if expanded { details.padding(.leading, 18).padding(.bottom, 12) }
        }
        .padding(.leading, CGFloat(min(depth, 2) * 12)).padding(.horizontal, 14)
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Text("Output  \(MetricFormat.count(chat.usage?.output_tokens))")
                Spacer()
                Text("Reasoning  \(MetricFormat.count(chat.usage?.reasoning_tokens))")
            }.monospacedDigit().help(L("当前轮次的官方用量；Output 已包含 Reasoning。"))
            HStack {
                Text(L("推理均速"))
                Text(MetricFormat.compactSpeed(chat.usage?.latest_reasoning?.tokens_per_second)).monospacedDigit()
                    .help(MetricFormat.speed(chat.usage?.latest_reasoning?.tokens_per_second))
            }.help(L("推理均速是最近一次接收窗口估算，并非服务端纯推理速度。"))
            if let model = chat.model, model != "unknown", !model.isEmpty {
                Text(model).font(.system(size: 11)).foregroundStyle(.secondary).textSelection(.enabled)
            }
            let activities = (chat.activities ?? []).filter { $0.display_visible != false }
            if !activities.isEmpty {
                Divider()
                ForEach(activities) { activity in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: activity.active == true && live ? "circle.dotted" : "minus")
                            .frame(width: 12).foregroundStyle(.secondary).accessibilityHidden(true)
                        Text(activity.display_label ?? L("活动")).lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading).help(activity.display_label ?? L("活动"))
                        Text(activity.active == true ? (live ? L("进行中") : L("未知")) : result(activity.status))
                            .foregroundStyle(.secondary).fixedSize()
                        Text(activity.active == true && !live ? "—" : MetricFormat.duration(activity.start_known == false ? nil : activity.duration_seconds))
                            .monospacedDigit().foregroundStyle(.secondary).fixedSize()
                    }
                }
            }
        }.font(.system(size: 11))
    }

    private func result(_ status: String?) -> String {
        switch status {
        case "completed": L("完成")
        case "failed": L("失败")
        case "interrupted": L("已停止")
        case "declined": L("未执行")
        default: L("未知")
        }
    }
}
