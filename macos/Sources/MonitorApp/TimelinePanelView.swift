import SwiftUI
import MonitorModel

struct TimelinePanelView: LocalizedView {
    @Environment(\.monitorLanguage) var language
    @Bindable var store: TimelineStore
    var compact = true
    var presentation: TimelinePresentation = .activityViewer
    var pollingEnabled = true
    var presentationID = 0
    var dismissPresentation = 0
    var maxTimelineHeight: CGFloat = 250
    var onSelect: (([TimelinePhase]) -> Void)?
    @State private var inputRequest: InputPopoverRequest?
    @State private var viewportWidth = TimelineLayout.menuViewportWidth

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let bundle = store.bundle, let layout = store.layout(for: presentation) {
                if presentation == .panelCompact {
                    ScrollView(.vertical) { plot(bundle, layout: layout) }
                        .frame(height: min(layout.height + 2, maxTimelineHeight))
                        .scrollBounceBehavior(.basedOnSize)
                } else { plot(bundle, layout: layout) }
                footer(bundle).opacity(store.switchingTurn ? 0 : 1).allowsHitTesting(!store.switchingTurn)
                if let error = store.error {
                    HStack { Text(L(error)); Spacer(); Button(L("重试")) { Task { await store.refresh() } }.buttonStyle(.plain) }
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
                if !bundle.earlier_active_turns.isEmpty {
                    Menu(L("前轮仍有活动")) {
                        ForEach(bundle.earlier_active_turns, id: \.self) { turn in
                            Button(turnLabel(turn, in: bundle)) { changeTurn(turn) }
                        }
                    }.font(.system(size: 10)).menuStyle(.borderlessButton).fixedSize()
                }
                if bundle.phases.isEmpty && bundle.markers.isEmpty {
                    Text(L(bundle.quality.contains("turn_identity_unavailable") ? "历史缺少轮次字段，部分轨迹不可恢复。" : "这轮暂无可用轨迹"))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
            } else if let bundle = store.bundle, bundle.quality.contains("clock_unreliable") {
                Text(L("系统时间发生变化，这轮活动无法可靠对齐。"))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
                Menu(L("查看可用活动")) {
                    ForEach(bundle.allActivities) { phase in
                        Button(phase.presentationLabel(language: language)) { (onSelect ?? { store.open($0) })([phase]) }
                    }
                }.font(.system(size: 11))
            } else if let bundle = store.bundle, bundle.quality.contains("turn_identity_unavailable") {
                Text(L("历史缺少轮次字段，部分轨迹不可恢复。"))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            } else if let bundle = store.bundle, !bundle.quality.isEmpty {
                HStack {
                    Text(L("部分历史记录暂不可用"))
                    Spacer()
                    Button(L("重试")) { Task { await store.refresh() } }.buttonStyle(.plain)
                }.font(.system(size: 11)).foregroundStyle(.secondary)
            } else if store.loading { ProgressView().controlSize(.small).frame(maxWidth: .infinity) }
            else {
                HStack {
                    Text(store.error.map { L($0) } ?? L("这轮暂无可用轨迹")).foregroundStyle(.secondary)
                    Spacer()
                    Button(L("重试")) { Task { await store.refresh() } }.buttonStyle(.plain)
                }.font(.system(size: 11))
            }
        }
        .onChange(of: store.selectedTurn) { inputRequest = nil }
        .onChange(of: dismissPresentation) { inputRequest = nil }
        .onChange(of: pollingEnabled) { if !pollingEnabled { inputRequest = nil } }
        .onDisappear { inputRequest = nil }
        .task(id: pollingKey) {
            guard pollingEnabled else { return }
            if presentation == .panelCompact { store.prepareForPresentation(presentationID) }
            while !Task.isCancelled {
                await store.refresh()
                do { try await Task.sleep(for: .seconds(1)) } catch { break }
            }
        }
    }

    private var pollingKey: String {
        "\(pollingEnabled):\(presentationID):\(store.followsCurrentTurn ? "current" : store.selectedTurn ?? "")"
    }

    private var visibleRange: String {
        "\(Int(TimelineLayout.visibleSeconds(in: viewportWidth, zoom: store.zoom).rounded()))s"
    }

    @ViewBuilder private var header: some View {
        if presentation == .panelCompact { compactHeader } else { legacyHeader }
    }

    private var compactHeader: some View {
        HStack(spacing: 5) {
            if let bundle = store.bundle, !bundle.turns.isEmpty {
                Picker(L("轮次"), selection: Binding(get: { store.selectedTurn ?? "" }, set: { changeTurn($0) })) {
                    ForEach(bundle.turns) { turn in Text(turnLabel(turn.id, in: bundle)).tag(turn.id) }
                }.labelsHidden().pickerStyle(.menu).controlSize(.small).fixedSize()
                    .disabled(bundle.turns.count < 2)
                    .accessibilityIdentifier("monitor.timeline.turn")
            } else { Text(L("本轮活动")).foregroundStyle(.secondary) }
            Button {
                guard !store.switchingTurn, let input = store.bundle?.initialInput else { return }
                inputRequest = InputPopoverRequest(phase: input)
            } label: { Image(systemName: "text.bubble") }
                .buttonStyle(.borderless).controlSize(.small)
                .disabled(store.switchingTurn || store.bundle?.initialInput == nil)
                .help(L(store.bundle?.initialInput == nil ? "所选轮次没有可读取的初始输入" : "查看本轮用户输入"))
                .accessibilityLabel(L("查看本轮用户输入")).accessibilityIdentifier("monitor.timeline.input")
                .popover(item: $inputRequest, arrowEdge: .bottom) { request in
                    PanelAuxiliaryContent(id: request.id, close: { inputRequest = nil }) {
                        InputPopoverView(phase: request.phase, client: store.client, close: { inputRequest = nil })
                            .id(request.id)
                    }
                }
            Spacer(minLength: 0)
            HStack(spacing: 5) {
                Button { store.zoom = store.zoom.zoomedOut } label: { Image(systemName: "minus").frame(width: 14, height: 14) }
                    .disabled(store.zoom == .wide)
                    .help(L("缩小时间线")).accessibilityLabel(L("缩小时间线"))
                    .accessibilityIdentifier("monitor.timeline.zoom.out")
                Text(visibleRange)
                    .monospacedDigit().foregroundStyle(.secondary)
                    .accessibilityLabel(L("可见时间范围")).accessibilityValue(visibleRange)
                Button { store.zoom = store.zoom.zoomedIn } label: { Image(systemName: "plus").frame(width: 14, height: 14) }
                    .disabled(store.zoom == .detail)
                    .help(L("放大时间线")).accessibilityLabel(L("放大时间线"))
                    .accessibilityIdentifier("monitor.timeline.zoom.in")
            }.buttonStyle(.bordered).controlSize(.mini).fixedSize()
        }.font(.system(size: 11))
    }

    private func compactLabels(_ layout: TimelineLayout, bundle: TimelineBundle) -> some View {
        let members = layout.lanes.filter { $0.track == "model" }
        return VStack(alignment: .leading, spacing: 0) {
            Color.clear.frame(height: TimelineLayout.axisHeight)
            ForEach(members) { lane in
                let agent = bundle.agents.first { $0.thread_id == lane.threadID }
                let name = lane.isChild ? agent?.displayName ?? String(lane.threadID.suffix(8)) : L("主智能体")
                if members.count > 1 {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 3) {
                            Text(name).fontWeight(.medium).lineLimit(1).truncationMode(.middle)
                            if agent?.hasPartialInformation == true {
                                Image(systemName: "questionmark.circle").font(.system(size: 8))
                            }
                        }
                        Text(agent?.roundStatusLabel(in: language) ?? L("状态未知")).font(.system(size: 9))
                            .foregroundStyle(.secondary).lineLimit(1)
                    }
                    .padding(.leading, CGFloat(lane.depth * 5))
                    .frame(maxWidth: .infinity, minHeight: lane.height * 2, maxHeight: lane.height * 2, alignment: .leading)
                    .accessibilityElement(children: .ignore).accessibilityLabel(name)
                    .accessibilityValue((agent?.roundStatusLabel(in: language) ?? L("状态未知")) + (agent?.hasPartialInformation == true ? " · " + L("部分观测") : ""))
                    .accessibilityIdentifier("monitor.agent.\(lane.threadID)")
                    .help([name, agent?.roundStatusLabel(in: language) ?? L("状态未知"), agent?.agent_path, lane.threadID,
                           L("模型在上，工具在下")].compactMap { $0 }.joined(separator: "\n"))
                } else {
                    VStack(alignment: .leading, spacing: 0) {
                        Text(L("模型")).frame(height: lane.height)
                        Text(L("工具")).frame(height: lane.height)
                    }.foregroundStyle(.secondary)
                }
            }
        }.font(.system(size: 10))
    }

    private func plot(_ bundle: TimelineBundle, layout: TimelineLayout) -> some View {
        HStack(alignment: .top, spacing: 5) {
            Group {
                if presentation == .panelCompact { compactLabels(layout, bundle: bundle) }
                else { labels(lanes: layout.lanes) }
            }.frame(width: presentation == .panelCompact ? (layout.lanes.contains(where: \.isChild) ? 104 : 32) : compact ? 78 : 116)
            TimelineSurface(store: store, layout: layout, activities: bundle.allActivities, agents: bundle.agents,
                            pagedFollowing: presentation == .panelCompact, animationsEnabled: pollingEnabled,
                            onSelect: onSelect ?? { store.open($0) })
                .frame(height: layout.height + 2)
                .onGeometryChange(for: Double.self) { Double($0.size.width) } action: { viewportWidth = $0 }
                .accessibilityIdentifier("monitor.timeline.surface")
        }
        .overlay(alignment: .topLeading) {
            if presentation == .panelGrouped {
                VStack(spacing: 0) {
                    Color.clear.frame(height: TimelineLayout.axisHeight).allowsHitTesting(false)
                    ForEach(layout.lanes) { lane in
                        if lane.track == "header" {
                            agentHeader(lane, in: bundle).frame(height: lane.height)
                        } else {
                            Color.clear.frame(height: lane.height).allowsHitTesting(false)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .opacity(store.switchingTurn ? 0 : 1)
        .allowsHitTesting(!store.switchingTurn)
        .overlay { if store.switchingTurn { ProgressView(L("读取所选轮次…")).controlSize(.small) } }
    }

    private var legacyHeader: some View {
        HStack(spacing: 6) {
            if let bundle = store.bundle, !bundle.turns.isEmpty {
                Picker(L("轮次"), selection: Binding(get: { store.selectedTurn ?? "" }, set: { changeTurn($0) })) {
                    ForEach(bundle.turns) { turn in Text(turnLabel(turn.id, in: bundle)).tag(turn.id) }
                }.labelsHidden().pickerStyle(.menu).fixedSize()
                    .accessibilityIdentifier("monitor.timeline.turn")
                if let input = bundle.initialInput {
                    Button { (onSelect ?? { store.open($0) })([input]) } label: { Image(systemName: "text.bubble") }
                        .buttonStyle(.plain).help(L("查看本轮用户输入")).accessibilityLabel(L("查看本轮用户输入"))
                }
            } else { Text(L("活动轨迹")).foregroundStyle(.secondary) }
            Spacer(minLength: 0)
            HStack(spacing: 5) {
                Button { store.zoom = store.zoom.zoomedOut } label: {
                    Image(systemName: "minus").frame(width: 18, height: 18).contentShape(Rectangle())
                }
                    .disabled(store.zoom == .wide)
                    .help(L("缩小时间线"))
                    .accessibilityLabel(L("缩小时间线"))
                    .accessibilityIdentifier("monitor.timeline.zoom.out")
                Text(String(format: L("每屏 %d 秒"), Int(TimelineLayout.visibleSeconds(in: viewportWidth, zoom: store.zoom).rounded())))
                    .monospacedDigit().foregroundStyle(.secondary)
                Button { store.zoom = store.zoom.zoomedIn } label: {
                    Image(systemName: "plus").frame(width: 18, height: 18).contentShape(Rectangle())
                }
                    .disabled(store.zoom == .detail)
                    .help(L("放大时间线"))
                    .accessibilityLabel(L("放大时间线"))
                    .accessibilityIdentifier("monitor.timeline.zoom.in")
            }.font(.system(size: 10)).controlSize(.mini)
        }.font(.system(size: 11)).buttonStyle(.plain)
    }

    private func labels(lanes: [TimelineLane]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear.frame(height: TimelineLayout.axisHeight)
            ForEach(lanes) { lane in
                if presentation == .panelGrouped && lane.track == "header" {
                    Color.clear.frame(height: lane.height)
                } else if presentation == .activityViewer && (lane.track == "summary" || lane.track == "header") {
                    Button {
                        if !store.expandedAgents.insert(lane.threadID).inserted { store.expandedAgents.remove(lane.threadID) }
                    } label: {
                        HStack(spacing: 3) {
                            Image(systemName: store.expandedAgents.contains(lane.threadID) ? "chevron.down" : "chevron.right")
                                .font(.system(size: 8, weight: .medium))
                            Text(store.bundle?.agents.first(where: { $0.thread_id == lane.threadID })?.displayName ?? lane.label).lineLimit(1)
                            if let agent = store.bundle?.agents.first(where: { $0.thread_id == lane.threadID }), !(agent.quality ?? []).isEmpty {
                                Image(systemName: "questionmark.circle").font(.system(size: 9))
                                    .help(L("子智能体轨迹尚未完整关联"))
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                    }.buttonStyle(.plain).help(agentLaneLabel(lane)).accessibilityLabel(agentLaneLabel(lane))
                        .accessibilityValue(store.expandedAgents.contains(lane.threadID) ? L("已展开") : L("已折叠"))
                        .frame(height: lane.height)
                } else {
                    Text(lane.track == "model" ? L("模型") : lane.track == "tool" ? L("工具") : lane.label)
                        .help(lane.track == "tool" ? L("短工具以粗线标记；重叠处增强颜色，点击直接查看此处的工具详情。") : L("短活动以粗线标记，符号宽度不代表耗时；点击查看活动详情。"))
                        .accessibilityLabel(lane.track == "model" ? L("模型") : lane.track == "tool" ? L("工具") : lane.label)
                        .foregroundStyle(.secondary).padding(.leading, lane.isChild ? 10 : 0)
                        .frame(height: lane.height, alignment: .leading)
                }
            }
        }.font(.system(size: 10))
    }

    private func agentLaneLabel(_ lane: TimelineLane) -> String {
        let name = store.bundle?.agents.first { $0.thread_id == lane.threadID }?.displayName ?? String(lane.threadID.suffix(8))
        return L("子智能体") + " · " + name
    }

    @ViewBuilder private func agentHeader(_ lane: TimelineLane, in bundle: TimelineBundle) -> some View {
        let agent = bundle.agents.first { $0.thread_id == lane.threadID }
        let expanded = store.expandedAgents.contains(lane.threadID)
        let label = lane.isChild ? L("子智能体") + " · " + (agent?.displayName ?? lane.label) : L("主智能体")
        let state = agent?.roundStatusLabel(in: language) ?? L("状态未知")
        let accessibleState = L(state) + (agent?.hasPartialInformation == true ? " · " + L("部分活动记录不完整") : "")
        let description = [label, agent?.agent_path, lane.threadID].compactMap { $0 }.joined(separator: "\n")
        if lane.isChild {
            Button {
                if !store.expandedAgents.insert(lane.threadID).inserted { store.expandedAgents.remove(lane.threadID) }
            } label: {
                agentHeaderContent(lane, name: agent?.displayName ?? lane.label, state: state, expanded: expanded,
                                   partial: agent?.hasPartialInformation == true)
            }.buttonStyle(.plain).help(description)
                .accessibilityLabel(label).accessibilityValue(accessibleState + " · " + L(expanded ? "已展开" : "已折叠"))
                .accessibilityIdentifier("monitor.agent.\(lane.threadID)")
        } else {
            agentHeaderContent(lane, name: "主智能体", state: state, expanded: false, partial: agent?.hasPartialInformation == true)
                .accessibilityElement(children: .ignore).accessibilityLabel(L("主智能体")).accessibilityValue(accessibleState)
                .accessibilityIdentifier("monitor.agent.root")
        }
    }

    private func agentHeaderContent(_ lane: TimelineLane, name: String, state: String, expanded: Bool, partial: Bool) -> some View {
        HStack(spacing: 5) {
            if lane.isChild {
                Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.system(size: 8, weight: .medium)).frame(width: 9)
                Text(L("子智能体")).foregroundStyle(.secondary).font(.system(size: 10))
                    .fixedSize(horizontal: true, vertical: false)
            }
            Text(lane.isChild ? name : L(name)).font(.system(size: 11, weight: .medium)).lineLimit(1).truncationMode(.middle)
                .layoutPriority(1)
            if partial {
                Image(systemName: "questionmark.circle").font(.system(size: 9)).foregroundStyle(.secondary)
                    .help(L("部分身份或活动记录不完整"))
            }
            Spacer(minLength: 4)
            Text(L(state)).font(.system(size: 9)).foregroundStyle(.secondary).fixedSize()
        }
        .padding(.leading, CGFloat(max(0, lane.depth - 1) * 8))
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading).contentShape(Rectangle())
        .overlay(alignment: .top) { if lane.isChild { Divider().opacity(0.5) } }
    }

    private func footer(_ bundle: TimelineBundle) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 5) { legend; footerActions(bundle) }
                .fixedSize(horizontal: true, vertical: false)
            VStack(alignment: .leading, spacing: 5) {
                legend
                HStack(spacing: 5) { footerActions(bundle) }
            }
        }.font(.system(size: 9)).foregroundStyle(.secondary)
    }

    private var legend: some View {
        HStack(spacing: 5) {
            ForEach([("reasoning", "思考"), ("text", "回复"), ("tool_input", "参数"), ("file_edit_input", "改动"),
                     ("contextCompaction", "压缩")], id: \.0) { kind, title in
                HStack(spacing: 3) {
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(legendColor(kind)).frame(width: 12, height: 5)
                    Text(L(title)).lineLimit(1).fixedSize()
                }
            }
        }
    }

    @ViewBuilder private func footerActions(_ bundle: TimelineBundle) -> some View {
        if bundle.phases.contains(where: \.isCoordination) {
            HStack(spacing: 3) { Image(systemName: "person.2"); Text(L("协作")).lineLimit(1).fixedSize() }
        }
        Spacer(minLength: 1)
        if !bundle.quality.isEmpty || bundle.phases.contains(where: { !($0.quality ?? []).isEmpty }) {
            Image(systemName: "info.circle").help(L("部分耗时或内容未被完整观测，点击活动查看可用记录。"))
                .accessibilityLabel(L("部分观测"))
        }
        if presentation != .panelCompact || !store.followsLatest || !store.followsCurrentTurn {
            Button(L("回到最新")) { store.latest() }
                .buttonStyle(.plain).disabled(store.followsLatest && store.followsCurrentTurn)
                .lineLimit(1).fixedSize()
        }
    }
    private func legendColor(_ kind: String) -> Color {
        Color(nsColor: TimelinePalette.color(for: kind))
    }
    private func changeTurn(_ turn: String) { store.selectTurn(turn) }
    private func turnLabel(_ turn: String, in bundle: TimelineBundle) -> String {
        guard let index = bundle.turns.firstIndex(where: { $0.id == turn }) else { return L("前轮活动") }
        if presentation == .panelCompact {
            if turn == bundle.current_turn_id {
                return L(bundle.turns[index].status == "active" ? "本轮活动" : "最近一轮活动")
            }
            if index == 0 { return L("最近一轮活动") }
        } else {
            if turn == bundle.current_turn_id { return L("当前轮") }
            if index == 0 { return L("最近一轮") }
        }
        let date = bundle.turns[index].start_ms.map { Date(timeIntervalSince1970: $0 / 1000).formatted(.dateTime.hour().minute().locale(language.locale)) } ?? "—"
        return String(format: L("前 %d 轮 · %@"), index, date)
    }
}
