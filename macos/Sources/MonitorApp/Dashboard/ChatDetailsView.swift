import SwiftUI
import MonitorModel

private struct StatisticsInfo: Identifiable { let id = UUID() }

/// Panel-only hierarchy; the independent activity window keeps its own layout.
struct ChatDetailsView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    let chat: Chat
    @Bindable var timeline: TimelineStore
    let pollingEnabled: Bool
    let presentationID: Int
    let dismissPresentation: Int
    let maxTimelineHeight: CGFloat
    let sourceAvailable: Bool
    @State private var information: StatisticsInfo?

    private var statistics: PanelStatistics {
        PanelStatistics(usage: sourceAvailable ? chat.usage : nil, selectedTurn: timeline.selectedTurn,
                        switchingTurn: timeline.switchingTurn, average: timeline.bundle?.average_speed, thread: chat.threadID)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !sourceAvailable {
                Text(L("聊天实时状态暂不可用；以下仅为可读取的活动记录。"))
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            TimelinePanelView(store: timeline, presentation: .panelCompact, pollingEnabled: pollingEnabled,
                              presentationID: presentationID, dismissPresentation: dismissPresentation,
                              maxTimelineHeight: maxTimelineHeight)
            Divider()
            HStack(spacing: 5) {
                Text(L("输出") + " " + PanelStatistics.compactCount(statistics.outputTokens))
                    .help(MetricFormat.count(statistics.outputTokens)).accessibilityIdentifier("monitor.statistics.output")
                Text(String(format: L("含推理 %@"), PanelStatistics.compactCount(statistics.reasoningTokens)))
                    .help(MetricFormat.count(statistics.reasoningTokens)).foregroundStyle(.secondary)
                Spacer(minLength: 2)
                Text(L("平均速度") + " " + statistics.averageText)
                    .accessibilityIdentifier("monitor.statistics.averageSpeed")
                    .accessibilityLabel(L("平均速度") + " " + statistics.averageText)
                    .help(L("平均速度 ≈ 累计输出 ÷ 请求总耗时（含等待）。"))
                Button { information = StatisticsInfo() } label: { Image(systemName: "info.circle") }
                    .buttonStyle(.borderless).accessibilityLabel(L("统计口径说明"))
                    .accessibilityIdentifier("monitor.statistics.info")
                    .popover(item: $information, arrowEdge: .bottom) { request in
                        PanelAuxiliaryContent(id: request.id, close: { information = nil }) {
                            explanations.padding(14).frame(width: 320)
                        }
                    }
            }.font(.system(size: 10)).monospacedDigit().lineLimit(1)
        }
        .onChange(of: dismissPresentation) { information = nil }
        .onChange(of: timeline.selectedTurn) { information = nil }
        .onChange(of: pollingEnabled) { if !pollingEnabled { information = nil } }
        .onDisappear { information = nil }
    }

    private var explanations: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(L("统计口径说明")).fontWeight(.medium)
                Spacer()
                Button { information = nil } label: { Image(systemName: "xmark") }
                    .buttonStyle(.borderless).accessibilityLabel(L("关闭提示"))
            }
            if let model = chat.model, model != "unknown", !model.isEmpty {
                Text(L("当前模型") + " · " + model).textSelection(.enabled)
            }
            Text(L("输出包含推理，仅统计所选轮次。"))
            if statistics.isUnavailable { Text(L("用量暂不可用")) }
            Text(L("平均速度 ≈ 累计输出 ÷ 请求总耗时（含等待）。"))
            if statistics.averageIsPartial { Text(L("平均速度仅计入有计时的响应。")) }
            if statistics.averageSpeed == nil { Text(L("平均速度暂不可用")) }
        }.font(.system(size: 11)).foregroundStyle(.secondary)
            // The popover inherits lineLimit(1) from the statistics row.
            .lineLimit(nil).fixedSize(horizontal: false, vertical: true)
    }
}
