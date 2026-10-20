import SwiftUI
import MonitorModel

/// Panel-only hierarchy; the independent activity window keeps its own layout.
struct ChatDetailsView: View {
    let chat: Chat
    @Bindable var timeline: TimelineStore
    let pollingEnabled: Bool
    let presentationID: Int
    @State private var explanationsExpanded = false

    private var statistics: PanelStatistics {
        PanelStatistics(usage: chat.usage, selectedTurn: timeline.selectedTurn,
                        switchingTurn: timeline.switchingTurn)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TimelinePanelView(store: timeline, presentation: .panelGrouped, pollingEnabled: pollingEnabled)
            Divider()
            statisticsView
            PanelDisclosureGroup(isExpanded: $explanationsExpanded) {
                explanations.padding(.top, 6)
            } label: {
                Text(L("模型与数据说明")).frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 4)
                    .accessibilityIdentifier("monitor.chat.explanations.\(chat.threadID)")
            }
            .font(.system(size: 10)).foregroundStyle(.secondary)
        }
        .onChange(of: presentationID) { explanationsExpanded = false }
    }

    private var statisticsView: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("本轮统计")).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("输出")).font(.system(size: 11)).foregroundStyle(.secondary)
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(PanelStatistics.compactCount(statistics.outputTokens))
                            .font(.system(size: 22, weight: .medium)).monospacedDigit()
                            .lineLimit(1).minimumScaleFactor(0.8)
                            .help(MetricFormat.count(statistics.outputTokens))
                        Text(String(format: L("含推理 %@"), PanelStatistics.compactCount(statistics.reasoningTokens)))
                            .font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit()
                            .lineLimit(1).minimumScaleFactor(0.8)
                            .help(MetricFormat.count(statistics.reasoningTokens))
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .combine).accessibilityIdentifier("monitor.statistics.output")
                    .help(L("所选轮次的官方用量；输出已包含推理。"))
                Divider().frame(height: 43)
                VStack(alignment: .leading, spacing: 5) {
                    Text(L("推理均速")).font(.system(size: 11)).foregroundStyle(.secondary)
                    Text(MetricFormat.compactSpeed(statistics.reasoningRate))
                        .font(.system(size: 22, weight: .medium)).monospacedDigit()
                        .lineLimit(1).minimumScaleFactor(0.7)
                        .help(MetricFormat.speed(statistics.reasoningRate))
                }.frame(width: 112, alignment: .leading)
                    .accessibilityElement(children: .combine).accessibilityIdentifier("monitor.statistics.reasoning")
                    .help(L("推理均速是最近一次接收窗口估算，并非服务端纯推理速度。"))
            }
            if statistics.isUnavailable {
                Text(L("所选轮次统计暂不可用"))
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
    }

    private var explanations: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let model = chat.model, model != "unknown", !model.isEmpty {
                Text(L("当前模型") + " · " + model).textSelection(.enabled)
            }
            Text(L("输出包含推理用量；统计只显示可确认属于所选轮次的数据。"))
            Text(L("推理均速是接收窗口估算，并非服务端纯计算速度。"))
            if let sample = chat.usage?.latest_reasoning, let rate = sample.tokens_per_second,
               rate.isFinite, rate >= 0, sample.turn_id != timeline.selectedTurn {
                Text(L("最近一次推理均速") + " · " + MetricFormat.compactSpeed(rate))
                Text(L("该样本不属于所选轮次，未计入本轮统计。"))
            }
        }.font(.system(size: 10)).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
