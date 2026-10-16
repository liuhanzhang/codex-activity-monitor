import SwiftUI
import MonitorModel
import ServiceManagement

private struct RestoreRequest: Identifiable { let id = UUID() }

struct DashboardView: View {
    @Bindable var store: DashboardStore
    var quit: () -> Void
    @State private var restoreRequest: RestoreRequest?

    var body: some View {
        VStack(spacing: 0) {
            header.padding(20)
            if let error = store.operationError {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.circle")
                    Text(error).textSelection(.enabled)
                    Spacer()
                    Button { store.operationError = nil } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).accessibilityLabel(L("关闭提示"))
                }.font(.caption).foregroundStyle(.red).padding(.horizontal, 20).padding(.bottom, 12)
            }
            Divider()
            if let snapshot = store.snapshot, !snapshot.rootChats.isEmpty {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(snapshot.rows(expanded: store.expanded)) { row in
                            ChatRowView(chat: row.chat, store: store).padding(.leading, CGFloat(min(row.depth, 4) * 16))
                            Divider().padding(.horizontal, 18)
                        }
                    }
                }
            } else {
                ContentUnavailableView {
                    Label(String(localized: "Empty", bundle: .module), systemImage: "bubble.left.and.bubble.right")
                } description: { Text(store.connectionLabel) }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            footer.padding(.horizontal, 18).padding(.vertical, 12)
        }
        .frame(width: 720, height: 520)
        .background(.background)
        .sheet(item: $restoreRequest) { _ in RestoreSheet(store: store) }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 16) {
            Image(systemName: "waveform.path")
                .font(.title2).frame(width: 42, height: 42)
                .glassEffect(.regular, in: .rect(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(String(localized: "Monitor", bundle: .module)).font(.headline)
                HStack(spacing: 6) {
                    Circle().fill(store.fresh && store.snapshot?.backends?.values.contains(where: { $0.connected == true }) == true ? .green : .secondary).frame(width: 6, height: 6)
                    Text(store.connectionLabel).font(.caption).foregroundStyle(.secondary)
                    if store.readOnly { Text(String(localized: "ReadOnly", bundle: .module)).font(.caption).foregroundStyle(.secondary) }
                    if store.testProfile != nil { Text(L("隔离验证")).font(.caption).foregroundStyle(.secondary) }
                }
                if store.bridgeNeedsRestart {
                    Text(L("旧接入仍在运行；任务结束后重开 Codex，以启用退出暂停采集。"))
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if store.fresh, store.readOnly || store.management?.configured == true,
                   let notice = store.snapshot?.observationNotice {
                    Text(L(notice.rawValue)).font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(store.menuTitle).font(.system(size: 26, weight: .medium, design: .rounded)).monospacedDigit()
                Text(L("已接入聊天的生成总速度")).font(.caption2).foregroundStyle(.secondary)
                if store.fresh, store.snapshot?.overview?.coverage == "partial",
                   let subtotal = store.snapshot?.overview?.observed_subtotal {
                    Text(String(format: L("已观测小计 %@ t/s"), MetricFormat.rate(subtotal))).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Label(String(format: L("%d 个活动聊天"), store.snapshot?.displayedChats.filter { $0.has_active_work == true }.count ?? 0), systemImage: "circle.dotted")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            if store.busy { ProgressView().controlSize(.mini) }
            Menu {
                Toggle(L("登录时启动"), isOn: Binding(get: { store.loginEnabled }, set: { enabled in
                    Task { await store.setLogin(enabled) }
                }))
                if store.loginNeedsApproval {
                    Button(L("在系统设置中允许登录启动")) { SMAppService.openSystemSettingsLoginItems() }
                }
            } label: { Image(systemName: "gearshape") }
                .menuStyle(.borderlessButton).fixedSize().disabled(store.readOnly || store.busy || store.testProfile != nil)
                .accessibilityLabel(L("设置"))
            Button(String(localized: "Configure", bundle: .module)) { Task { await store.configure() } }
                .disabled(store.readOnly || store.busy || store.management?.waitingForCleanup == true)
            Button(String(localized: "Restore", bundle: .module)) { restoreRequest = RestoreRequest() }
                .disabled(store.readOnly || store.busy || store.management == nil)
            Button(String(localized: "Quit", bundle: .module), action: quit)
        }
        .buttonStyle(.borderless)
    }
}

private struct RestoreSheet: View {
    let store: DashboardStore
    @Environment(\.dismiss) private var dismiss
    @State private var purge = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(L("恢复 Codex 配置"), systemImage: "arrow.uturn.backward").font(.headline)
            Text(L("恢复接入前的配置并停止采集。用户后来的设置会保留，Codex 任务不会被强制结束。"))
                .font(.callout).foregroundStyle(.secondary)
            Toggle(L("清理本机数据并退出，用于卸载"), isOn: $purge)
            Text(purge ? L("等待 Codex 安全退出并完成清理后，Monitor 将退出。之后可手动删除 App；再次打开会按首次启动处理。") :
                 L("保留 App 设置与已恢复状态。以后打开仍保持未接入，需要时点击配置即可重新启用。"))
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button(L("取消")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("恢复配置")) {
                    let shouldPurge = purge
                    dismiss()
                    Task { await store.restore(purge: shouldPurge) }
                }.buttonStyle(.glassProminent).keyboardShortcut(.defaultAction)
            }
        }.padding(24).frame(width: 430)
    }
}

private struct ChatRowView: View {
    let chat: Chat
    @Bindable var store: DashboardStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                if !store.expanded.insert(chat.id).inserted { store.expanded.remove(chat.id) }
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: store.expanded.contains(chat.id) ? "chevron.down" : "chevron.right")
                        .font(.caption2).foregroundStyle(.tertiary).frame(width: 10).padding(.top, 4)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(chat.name.flatMap { $0.isEmpty ? nil : $0 } ?? L("未命名聊天")).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                        HStack(spacing: 6) {
                            if !chat.project.isEmpty { Text(chat.project).foregroundStyle(.secondary) }
                            Text(store.fresh && chat.connected != false ? (chat.display_label ?? L("状态暂不可用")) : L("连接中断"))
                                .foregroundStyle(store.fresh && chat.has_active_work == true ? .primary : .secondary)
                                .lineLimit(1)
                        }.font(.caption)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    Text(store.fresh ? MetricFormat.duration(chat.display_seconds) : "—")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 64, alignment: .trailing)
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("Output \(MetricFormat.count(chat.usage?.output_tokens))")
                        Text("Reasoning \(MetricFormat.count(chat.usage?.reasoning_tokens))").foregroundStyle(.secondary)
                    }.font(.caption.monospacedDigit()).frame(width: 125, alignment: .trailing)
                        .help(L("当前轮次的官方用量；Output 已包含 Reasoning。"))
                    VStack(alignment: .trailing, spacing: 4) {
                        Text(String(format: L("生成 %@"), MetricFormat.rate(store.fresh ? chat.generation_rate : nil, estimated: chat.generation_estimated == true)))
                        Text(String(format: L("推理 %@"), MetricFormat.rate(chat.usage?.latest_reasoning?.tokens_per_second, estimated: true))).foregroundStyle(.secondary)
                    }.font(.caption.monospacedDigit()).frame(width: 100, alignment: .trailing)
                        .help(L("单位为 token/s。生成是实时窗口速度；推理是最近一次接收窗口估算，并非服务端纯推理速度。"))
                }
                .contentShape(Rectangle())
            }.buttonStyle(.plain)
            if store.expanded.contains(chat.id) {
                VStack(alignment: .leading, spacing: 7) {
                    if let model = chat.model, model != "unknown", !model.isEmpty {
                        Text(model).font(.caption2).foregroundStyle(.tertiary)
                    }
                    ForEach((chat.activities ?? []).filter { $0.display_visible != false }) { activity in
                        HStack {
                            Text(activity.display_label ?? L("活动")).lineLimit(2)
                            Spacer()
                            Text(activity.active == true ? (store.fresh ? L("进行中") : L("未知")) : result(activity.status)).foregroundStyle(.secondary)
                            Text(MetricFormat.duration(activity.start_known == false ? nil : activity.duration_seconds))
                                .monospacedDigit().foregroundStyle(.secondary).frame(width: 70, alignment: .trailing)
                        }.font(.caption)
                    }
                }.padding(.leading, 22)
            }
        }.padding(.horizontal, 18).padding(.vertical, 14)
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
