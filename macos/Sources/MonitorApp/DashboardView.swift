import SwiftUI
import MonitorModel
import ServiceManagement

private struct RestoreRequest: Identifiable { let id = UUID() }

struct DashboardView: View {
    @Bindable var store: DashboardStore
    var quit: () -> Void
    var onSizeChange: (CGSize) -> Void = { _ in }
    var onModalChange: (Bool) -> Void = { _ in }
    @State private var restoreRequest: RestoreRequest?
    @State private var listHeight: CGFloat = 88
    @State private var topHeight: CGFloat = 44
    @State private var bottomHeight: CGFloat = 30
    @State private var noticeExpanded = false

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
            ScrollView {
                VStack(spacing: 0) {
                    if store.activeRows.isEmpty {
                        Text(L("当前没有活跃聊天"))
                            .font(.system(size: 12)).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity).padding(.vertical, 24)
                    }
                    ForEach(store.activeRows) { row in
                        ChatRowView(chat: row.chat, depth: row.depth, store: store)
                        Divider().padding(.horizontal, 14)
                    }
                    if !store.recentRoots.isEmpty {
                        Button { store.showRecent.toggle() } label: {
                            HStack(spacing: 8) {
                                Image(systemName: store.showRecent ? "chevron.down" : "chevron.right")
                                    .font(.system(size: 9, weight: .medium)).frame(width: 10)
                                Text(L("最近完成"))
                                Text("\(store.recentRoots.count)").foregroundStyle(.secondary)
                                Spacer()
                            }
                            .font(.system(size: 12)).padding(.horizontal, 14).padding(.vertical, 12)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain).accessibilityIdentifier("monitor.recent")
                        .accessibilityValue(store.showRecent ? L("已展开") : L("已折叠"))
                        ForEach(store.recentRows) { row in
                            ChatRowView(chat: row.chat, depth: row.depth, store: store)
                            Divider().padding(.horizontal, 14)
                        }
                    }
                }
                .frame(maxWidth: .infinity).fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { listHeight = $0 }
            }
            .frame(height: min(listHeight, max(64, store.panelMaxHeight - topHeight - bottomHeight - 2)))
            .scrollBounceBehavior(.basedOnSize)
            Divider()
            footer.padding(.horizontal, 14).padding(.vertical, 8)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { bottomHeight = $0 }
        }
        .frame(width: 420)
        .background(.background)
        .onChange(of: store.dismissPresentation) { restoreRequest = nil }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { onSizeChange($0) }
        .sheet(item: $restoreRequest) { _ in
            RestoreSheet(store: store)
                .onAppear { onModalChange(true) }
                .onDisappear { onModalChange(false) }
        }
    }

    private var header: some View {
        HStack(spacing: 8) {
            Circle().fill(store.fresh && store.snapshot?.backends?.values.contains(where: { $0.connected == true }) == true ? .green : .secondary)
                .frame(width: 6, height: 6).accessibilityHidden(true)
            Text(store.compactConnectionLabel).lineLimit(1).help(store.connectionLabel)
            Spacer(minLength: 6)
            Text(store.menuTitle == "—" ? "—" : MetricFormat.compactSpeed(store.snapshot?.overview?.generation_rate)).fontWeight(.medium).monospacedDigit().fixedSize()
                .help(L("已接入聊天的生成总速度") + " " + store.menuTitle)
                .accessibilityLabel(L("已接入聊天的生成总速度") + " " + store.menuTitle)
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
        Menu {
            Button(String(localized: "Configure", bundle: .module)) { Task { await store.configure() } }
                .disabled(store.readOnly || store.busy || store.management?.waitingForCleanup == true)
            Button(String(localized: "Restore", bundle: .module)) { restoreRequest = RestoreRequest() }
                .disabled(store.readOnly || store.busy || store.management == nil)
            Divider()
            Toggle(L("登录时启动"), isOn: Binding(get: { store.loginEnabled }, set: { enabled in
                Task { await store.setLogin(enabled) }
            })).disabled(store.readOnly || store.busy || store.testProfile != nil)
            if store.loginNeedsApproval {
                Button(L("在系统设置中允许登录启动")) { SMAppService.openSystemSettingsLoginItems() }
            }
            Divider()
            Button(String(localized: "Quit", bundle: .module), action: quit).keyboardShortcut("q")
        } label: { Image(systemName: "gearshape").font(.system(size: 13)) }
        .menuStyle(.borderlessButton).fixedSize().accessibilityLabel(L("设置"))
        .menuIndicator(.hidden)
        .accessibilityIdentifier("monitor.settings")
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Text(store.fresh ? String(format: L("%d 个活动聊天"), store.snapshot?.activeConversationCount ?? 0) : L("活动聊天 —"))
            Spacer()
            if store.readOnly { Text(String(localized: "ReadOnly", bundle: .module)) }
            if store.testProfile != nil { Text(L("隔离验证")) }
            if store.busy { ProgressView().controlSize(.mini).accessibilityLabel(L("正在处理")) }
        }.font(.system(size: 11)).foregroundStyle(.secondary)
    }
}
