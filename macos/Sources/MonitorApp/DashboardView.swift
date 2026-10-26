import SwiftUI
import MonitorModel

private struct RestoreRequest: Identifiable { let id = UUID() }

struct DashboardView: LocalizedView {
    var language: MonitorLanguage { store.language }
    @Bindable var store: DashboardStore
    var quit: () -> Void
    var onSizeChange: (CGSize) -> Void = { _ in }
    var onModalChange: (Bool) -> Void = { _ in }
    @State private var restoreRequest: RestoreRequest?
    @State private var topHeight: CGFloat = 44
    @State private var bottomHeight: CGFloat = 0
    @State private var controlsHeight: CGFloat = 0
    @State private var detailHeight: CGFloat = 180
    @State private var noticeExpanded = false

    private var budget: PanelLayoutBudget {
        PanelLayoutBudget(maximum: store.panelMaxHeight,
            chrome: topHeight + bottomHeight + controlsHeight + 2,
            chatCount: store.visibleChats.count, hasSelection: store.selectedChat != nil)
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 0) {
                header.padding(.horizontal, 14).padding(.vertical, 11)
                if store.readOnly && CommandLine.arguments.contains("--preview-status") {
                    HStack {
                        Text(L("菜单栏原尺寸")).font(.system(size: 11)).foregroundStyle(.secondary)
                        Spacer()
                        NativeStatusPreview(mode: store.menuMode, title: store.menuTitle)
                            .frame(width: StatusContentView.itemWidth(for: store.menuMode, title: store.menuTitle), height: 24)
                    }.padding(.horizontal, 14).padding(.bottom, 8)
                }
                notices
            }
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { topHeight = $0 }
            Divider()
            ScrollView(.vertical) {
                LazyVStack(spacing: 0) {
                    if store.visibleChats.isEmpty {
                        Text(L("当前没有活跃聊天")).font(.system(size: 12)).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity).frame(height: 48)
                    }
                    ForEach(store.visibleChats) { chat in ChatRowView(chat: chat, store: store) }
                }.padding(.horizontal, 8)
            }
            .frame(height: budget.listHeight)
            .scrollBounceBehavior(.basedOnSize)
            .accessibilityIdentifier("monitor.chatList")
            .id(store.presentationID)
            listControls
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { controlsHeight = $0 }
            if let chat = store.selectedChat {
                Divider()
                ScrollView(.vertical) {
                    ChatDetailsView(chat: chat, timeline: store.timeline(for: chat),
                        pollingEnabled: store.panelVisible, presentationID: store.presentationID,
                        dismissPresentation: store.dismissPresentation, maxTimelineHeight: budget.timelineHeight,
                        sourceAvailable: store.sourceAvailable(for: chat))
                        .padding(14)
                        .frame(maxWidth: .infinity)
                        .fixedSize(horizontal: false, vertical: true)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { detailHeight = $0 }
                }
                .frame(height: min(detailHeight, budget.detailHeight))
                .scrollBounceBehavior(.basedOnSize)
                .id(chat.id)
            }
            VStack(spacing: 0) {
                if store.readOnly || store.testProfile != nil || store.busy {
                    Divider()
                    footer.padding(.horizontal, 14).padding(.vertical, 8)
                }
            }.onGeometryChange(for: CGFloat.self) { $0.size.height } action: { bottomHeight = $0 }
        }
        .frame(width: 420)
        .environment(store.auxiliary)
        .background(.regularMaterial)
        .onChange(of: store.dismissPresentation) { restoreRequest = nil }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { onSizeChange($0) }
        .environment(\.monitorLanguage, store.language)
        .environment(\.locale, store.language.locale)
        .onAppear { if store.previewRestore { restoreRequest = RestoreRequest() } }
        .sheet(item: $restoreRequest) { _ in
            RestoreSheet(store: store)
                .onAppear { onModalChange(true) }
                .onDisappear { onModalChange(false) }
        }
    }

    private var listControls: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !store.recentRoots.isEmpty {
                Button {
                    store.showRecent.toggle()
                } label: {
                    Text(store.showRecent ? L("收起已完成") : plural("显示最近完成（%d）", count: store.recentRoots.count))
                        .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }.accessibilityIdentifier("monitor.recent")
            }
            if let snapshot = store.snapshot, !snapshot.unassignedChats.isEmpty {
                Button {
                    store.showUnassigned.toggle()
                } label: {
                    Label(plural("归属待确认的子任务（%d）", count: snapshot.unassignedChats.count),
                          systemImage: store.showUnassigned ? "chevron.up" : "questionmark.circle")
                        .frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
                }.accessibilityIdentifier("monitor.unassignedAgents")
            }
        }
        .buttonStyle(.plain).font(.system(size: 10)).foregroundStyle(.secondary)
        .padding(.horizontal, 18)
        .padding(.vertical, (!store.recentRoots.isEmpty || store.snapshot?.unassignedChats.isEmpty == false) ? 8 : 0)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Text(L("工作概览")).fontWeight(.semibold)
            Text(store.fresh ? plural("%d 个活跃聊天", count: store.activeRows.count) : "—")
                .font(.system(size: 10)).foregroundStyle(.secondary).fixedSize()
            Spacer(minLength: 6)
            if store.menuTitle != "—", let rate = store.snapshot?.overview?.displayedRate, rate > 0 {
                Text(MetricFormat.compactSpeed(rate)).font(.system(size: 11)).monospacedDigit().fixedSize()
                    .help(L("已接入聊天的生成总速度") + " " + store.menuTitle)
            }
            Circle().fill(store.fresh && store.snapshot?.backends?.values.contains(where: { $0.connected == true }) == true ? .green : .secondary)
                .frame(width: 6, height: 6).accessibilityHidden(true)
            Text(store.compactConnectionLabel).font(.system(size: 11)).foregroundStyle(.secondary)
                .lineLimit(1).help(store.connectionLabel)
            settings
        }.font(.system(size: 12))
    }

    @ViewBuilder private var notices: some View {
        if let error = store.operationError {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "exclamationmark.circle")
                Text(error).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                Button { store.operationError = nil } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).accessibilityLabel(L("关闭提示"))
            }
            .font(.system(size: 11)).foregroundStyle(.red)
            .padding(.horizontal, 14).padding(.bottom, 10)
        }
        if let notice = store.connectionDetail {
            Button { noticeExpanded.toggle() } label: {
                HStack(alignment: .top, spacing: 7) {
                    Image(systemName: "info.circle")
                    Text(notice).lineLimit(noticeExpanded ? nil : 2)
                        .frame(maxWidth: .infinity, alignment: .leading).multilineTextAlignment(.leading)
                    Image(systemName: noticeExpanded ? "chevron.up" : "chevron.down").font(.system(size: 9))
                }.contentShape(Rectangle())
            }
            .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(.secondary)
            .help(notice).accessibilityIdentifier("monitor.notice")
            .padding(.horizontal, 14).padding(.bottom, 10)
        }
        if store.fresh, store.snapshot?.overview?.coverage == "partial",
           let subtotal = store.snapshot?.overview?.observed_subtotal {
            Text(String(format: L("已观测小计 %@"), MetricFormat.compactSpeed(subtotal)))
                .help(MetricFormat.speed(subtotal))
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14).padding(.bottom, 10)
        }
    }

    private var settings: some View {
        DashboardSettingsMenu(state: SettingsMenuState(store: store), store: store,
            restore: { restoreRequest = RestoreRequest() }, quit: quit)
            .equatable()
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if store.readOnly { Text(L("ReadOnly")) }
            if store.testProfile != nil { Text(L("隔离验证")) }
            Spacer()
            if store.busy { ProgressView().controlSize(.mini).accessibilityLabel(L("正在处理")) }
        }.font(.system(size: 11)).foregroundStyle(.secondary)
    }
}
